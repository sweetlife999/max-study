"""Onboarding progress (ARCHITECTURE.md §4, §7).

Progress is never stored. A step of type ``event_kind`` is done when the student has checked in
at an event of that kind, or at any event whose ``onboarding_step`` names this step; a ``manual``
step is done when there is a row in ``manual_step_completions``.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from campus.config import EventKindStep, ManualStep
from campus.db.models import Checkin, Event, ManualStepCompletion, User
from campus.domain.errors import StepNotFoundError, StepNotManualError
from campus.domain.services.base import Service
from campus.domain.services.outbox import OutboxService
from campus.domain.views import OnboardingView, StepView


@dataclass(frozen=True, slots=True)
class OnboardingService(Service):
    @property
    def _outbox(self) -> OutboxService:
        return OutboxService(self.session, self.config, self.clock)

    async def done_step_keys(self, user_id: int) -> frozenset[str]:
        """The keys of every step this user has already completed."""
        attended_kinds, attended_steps = await self._attendance(user_id)
        manual = await self._manual_completions(user_id)
        done: set[str] = set()
        for step in self.config.university.onboarding_steps:
            if isinstance(step, ManualStep):
                if step.key in manual:
                    done.add(step.key)
            elif step.key in attended_steps or step.event_kind in attended_kinds:
                done.add(step.key)
        return frozenset(done)

    async def _attendance(self, user_id: int) -> tuple[frozenset[str], frozenset[str]]:
        result = await self.session.execute(
            select(Event.kind, Event.onboarding_step)
            .join(Checkin, Checkin.event_id == Event.id)
            .where(Checkin.user_id == user_id)
        )
        kinds: set[str] = set()
        steps: set[str] = set()
        for kind, step_key in result.all():
            kinds.add(kind)
            if step_key:
                steps.add(step_key)
        return frozenset(kinds), frozenset(steps)

    async def _manual_completions(self, user_id: int) -> frozenset[str]:
        result = await self.session.execute(
            select(ManualStepCompletion.step_key).where(ManualStepCompletion.user_id == user_id)
        )
        return frozenset(result.scalars().all())

    async def progress(self, user: User, *, lang: str | None = None) -> OnboardingView:
        done = await self.done_step_keys(user.id)
        language = self.config.language_or_default(lang or user.lang)
        steps = tuple(
            self._step_view(step, language, done=step.key in done)
            for step in self.config.university.onboarding_steps
        )
        return OnboardingView(
            steps=steps,
            done_count=sum(1 for step in steps if step.done),
            total=len(steps),
        )

    async def complete_manual(self, user: User, step_key: str) -> StepView:
        """Tick a ``manual`` step. Repeating it is not an error."""
        step = self.config.university.step(step_key)
        if step is None:
            raise StepNotFoundError(f"step {step_key!r}")
        if not isinstance(step, ManualStep):
            raise StepNotManualError(f"step {step_key!r} is completed by checking in")
        inserted = await self.session.execute(
            pg_insert(ManualStepCompletion)
            .values(user_id=user.id, step_key=step_key)
            .on_conflict_do_nothing(
                index_elements=[ManualStepCompletion.user_id, ManualStepCompletion.step_key]
            )
            .returning(ManualStepCompletion.step_key)
        )
        if inserted.scalar_one_or_none() is not None:
            await self._outbox.enqueue(
                user_id=user.id,
                kind="step_completed",
                payload={"step_key": step_key},
                dedup_key=f"step_completed:{user.id}:{step_key}",
            )
        language = self.config.language_or_default(user.lang)
        return self._step_view(step, language, done=True)

    def step_view(self, step_key: str, *, lang: str, done: bool) -> StepView | None:
        step = self.config.university.step(step_key)
        return None if step is None else self._step_view(step, lang, done=done)

    def _step_view(self, step: EventKindStep | ManualStep, lang: str, *, done: bool) -> StepView:
        university = self.config.university
        return StepView(
            key=step.key,
            type=step.type,
            title=university.text(step.title, lang),
            description=university.text(step.description, lang),
            done=done,
            event_kind=step.event_kind if isinstance(step, EventKindStep) else None,
        )
