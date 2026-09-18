"""Check-in (ARCHITECTURE.md §5) — the security-critical path.

Order of checks, deliberately:

1. rate limit (10 attempts per 10 minutes per user) — before anything is looked up, so a guesser
   cannot use the endpoint to probe which events exist;
2. code shape;
3. the event: given explicitly, it must exist, have check-in open and be inside its time window;
   given as a bare code, only events already satisfying all three are searched;
4. the code itself, in constant time, against the accepted windows;
5. the check-in row, inserted idempotently — a repeat is ``already=True`` and a 200, not an error.

Every request that got past the rate limit costs exactly one row in ``checkin_attempts``,
whatever the outcome — a guessed code, a closed event and an event id that does not exist all
count, or the endpoint would answer "does this event exist?" an unlimited number of times.
A request rejected *by* the rate limit is not recorded, so the window can drain.

The rows are written in the caller's transaction, and the caller must commit it even when this
service raises: see :func:`campus.db.session.domain_scope`.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from campus.db.models import CHECKIN_METHODS, Checkin, CheckinAttempt, Event, User
from campus.domain import codes
from campus.domain.context import CHECKIN_RATE_LIMIT_ATTEMPTS, CHECKIN_RATE_LIMIT_WINDOW
from campus.domain.errors import (
    AmbiguousCodeError,
    CheckinClosedError,
    CheckinNotInTimeWindowError,
    DomainError,
    InvalidCodeError,
    TooManyAttemptsError,
    ValidationFailedError,
)
from campus.domain.services.base import Service
from campus.domain.services.events import EventService
from campus.domain.services.onboarding import OnboardingService
from campus.domain.services.outbox import OutboxService
from campus.domain.views import CheckinResult, StepView


@dataclass(frozen=True, slots=True)
class CheckinOutcome:
    """What the check-in did, before it is dressed up as a view."""

    event: Event
    already: bool
    points_total: int
    completed_step_key: str | None


@dataclass(frozen=True, slots=True)
class CheckinService(Service):
    @property
    def _events(self) -> EventService:
        return EventService(self.session, self.config, self.clock)

    @property
    def _onboarding(self) -> OnboardingService:
        return OnboardingService(self.session, self.config, self.clock)

    @property
    def _outbox(self) -> OutboxService:
        return OutboxService(self.session, self.config, self.clock)

    # --- rate limiting -----------------------------------------------------------------------

    async def recent_attempts(self, user_id: int) -> int:
        since = self.now() - CHECKIN_RATE_LIMIT_WINDOW
        result = await self.session.execute(
            select(func.count())
            .select_from(CheckinAttempt)
            .where(CheckinAttempt.user_id == user_id, CheckinAttempt.created_at >= since)
        )
        return int(result.scalar_one())

    async def _guard_rate_limit(self, user_id: int) -> None:
        if await self.recent_attempts(user_id) >= CHECKIN_RATE_LIMIT_ATTEMPTS:
            raise TooManyAttemptsError(f"user {user_id}")

    async def _record_attempt(self, user_id: int, *, success: bool) -> None:
        self.session.add(CheckinAttempt(user_id=user_id, created_at=self.now(), success=success))
        await self.session.flush()

    # --- the operation -----------------------------------------------------------------------

    async def check_in(
        self,
        *,
        user: User,
        code: str,
        method: str,
        event_id: int | None = None,
    ) -> CheckinOutcome:
        # A method the client made up is a malformed request, not a guess: it costs no attempt.
        if method not in CHECKIN_METHODS:
            raise ValidationFailedError("method", f"unknown check-in method {method!r}")
        await self._guard_rate_limit(user.id)

        try:
            event = await self._resolve(code, event_id=event_id)
        except DomainError:
            await self._record_attempt(user.id, success=False)
            raise
        await self._record_attempt(user.id, success=True)
        return await self._register(user=user, event=event, method=method)

    async def _resolve(self, code: str, *, event_id: int | None) -> Event:
        """Find the event this code checks into, or raise. Records nothing itself."""
        if not codes.is_well_formed(code):
            raise InvalidCodeError("code is not six digits")
        if event_id is not None:
            return await self._resolve_named_event(event_id, code)
        return await self._resolve_event_by_code(code)

    async def _resolve_named_event(self, event_id: int, code: str) -> Event:
        """The mini-app path: the deep link already said which event it is."""
        event = await self._events.require(event_id)
        if not event.checkin_open:
            raise CheckinClosedError(f"event {event.id}")
        if not self._events.is_within_checkin_window(event, now=self.now()):
            raise CheckinNotInTimeWindowError(f"event {event.id}")
        if not self._events.verify_code(event, code, now=self.now()):
            raise InvalidCodeError(f"code rejected for event {event.id}")
        return event

    async def _resolve_event_by_code(self, code: str) -> Event:
        """The chat path: a bare six-digit code, matched against every open event (§5)."""
        candidates = await self._events.list_open_for_checkin()
        matches = self._matching(candidates, code)
        if not matches:
            raise InvalidCodeError("no open event matches this code")
        if len(matches) > 1:
            raise AmbiguousCodeError(tuple(event.id for event in matches))
        return matches[0]

    def _matching(self, candidates: Sequence[Event], code: str) -> list[Event]:
        now = self.now()
        return [event for event in candidates if self._events.verify_code(event, code, now=now)]

    async def _register(self, *, user: User, event: Event, method: str) -> CheckinOutcome:
        """Insert the check-in idempotently and queue what follows from it."""
        done_before = await self._onboarding.done_step_keys(user.id)
        inserted = await self.session.execute(
            pg_insert(Checkin)
            .values(user_id=user.id, event_id=event.id, method=method, created_at=self.now())
            .on_conflict_do_nothing(index_elements=[Checkin.user_id, Checkin.event_id])
            .returning(Checkin.event_id)
        )
        already = inserted.scalar_one_or_none() is None
        points_total = await self._points(user.id)
        if already:
            return CheckinOutcome(
                event, already=True, points_total=points_total, completed_step_key=None
            )

        completed = await self._newly_completed_step(user.id, done_before)
        await self._outbox.enqueue(
            user_id=user.id,
            kind="checkin_confirmed",
            payload={"event_id": event.id, "points": event.points, "method": method},
            dedup_key=f"checkin_confirmed:{user.id}:{event.id}",
        )
        if completed is not None:
            await self._outbox.enqueue(
                user_id=user.id,
                kind="step_completed",
                payload={"step_key": completed, "event_id": event.id},
                dedup_key=f"step_completed:{user.id}:{completed}",
            )
        return CheckinOutcome(
            event, already=False, points_total=points_total, completed_step_key=completed
        )

    async def _newly_completed_step(self, user_id: int, done_before: frozenset[str]) -> str | None:
        """Which onboarding step this very check-in closed, if any."""
        done_after = await self._onboarding.done_step_keys(user_id)
        newly = done_after - done_before
        if not newly:
            return None
        # Keep the configured order, so the answer is stable when a check-in closes two steps.
        for step in self.config.university.onboarding_steps:
            if step.key in newly:
                return step.key
        return next(iter(sorted(newly)))

    async def _points(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.sum(Event.points), 0))
            .select_from(Checkin)
            .join(Event, Event.id == Checkin.event_id)
            .where(Checkin.user_id == user_id)
        )
        return int(result.scalar_one())

    # --- view --------------------------------------------------------------------------------

    async def result_view(self, outcome: CheckinOutcome, *, user: User) -> CheckinResult:
        lang = self.config.language_or_default(user.lang)
        completed: StepView | None = None
        if outcome.completed_step_key is not None:
            completed = self._onboarding.step_view(outcome.completed_step_key, lang=lang, done=True)
        return CheckinResult(
            event=await self._events.view(outcome.event, viewer=user, lang=lang),
            already=outcome.already,
            points_total=outcome.points_total,
            completed_step=completed,
        )

    async def check_in_view(
        self, *, user: User, code: str, method: str, event_id: int | None = None
    ) -> CheckinResult:
        """The whole operation as the api returns it (§7 POST /api/checkins)."""
        outcome = await self.check_in(user=user, code=code, method=method, event_id=event_id)
        return await self.result_view(outcome, user=user)
