"""Events: creation, editing, listings and the current check-in code (§5, §7)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select

from campus.config import EventKindStep
from campus.db.models import Checkin, Event, Rsvp, User
from campus.domain import codes
from campus.domain.context import CHECKIN_WINDOW_MARGIN as CHECKIN_MARGIN
from campus.domain.deeplinks import checkin_deeplink
from campus.domain.errors import (
    CheckinClosedError,
    EventNotFoundError,
    InvalidTimeRangeError,
    NotEventOwnerError,
    UnknownEventKindError,
    UnknownOnboardingStepError,
    ValidationFailedError,
)
from campus.domain.services.base import Service, require_aware
from campus.domain.views import EventView, QrCodeView

MAX_TITLE_LENGTH = 200
MAX_LOCATION_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4000
UNSET: Any = object()

EventScope = str
SCOPE_UPCOMING: EventScope = "upcoming"
SCOPE_PAST: EventScope = "past"


@dataclass(frozen=True, slots=True)
class EventService(Service):
    # --- reads -------------------------------------------------------------------------------

    async def get(self, event_id: int) -> Event | None:
        return await self.session.get(Event, event_id)

    async def require(self, event_id: int) -> Event:
        event = await self.get(event_id)
        if event is None:
            raise EventNotFoundError(f"event {event_id}")
        return event

    def require_owner(self, event: Event, actor: User) -> None:
        if event.organizer_id != actor.id:
            raise NotEventOwnerError(f"user {actor.id} does not own event {event.id}")

    async def list_by_scope(self, scope: str, *, limit: int | None = None) -> Sequence[Event]:
        """``upcoming`` is everything that has not ended yet; ``past`` is everything that has."""
        now = self.now()
        if scope == SCOPE_UPCOMING:
            statement = (
                select(Event).where(Event.ends_at >= now).order_by(Event.starts_at, Event.id)
            )
        elif scope == SCOPE_PAST:
            statement = (
                select(Event)
                .where(Event.ends_at < now)
                .order_by(Event.starts_at.desc(), Event.id.desc())
            )
        else:
            raise ValidationFailedError("scope", f"unknown scope {scope!r}")
        return await self._fetch(statement, limit)

    async def list_for_organizer(
        self, organizer_id: int, *, limit: int | None = None
    ) -> Sequence[Event]:
        statement = (
            select(Event)
            .where(Event.organizer_id == organizer_id)
            .order_by(Event.starts_at.desc(), Event.id.desc())
        )
        return await self._fetch(statement, limit)

    async def list_open_for_checkin(self) -> Sequence[Event]:
        """Events a bare six-digit code could belong to (§5)."""
        now = self.now()
        statement = (
            select(Event)
            .where(
                Event.checkin_open.is_(True),
                Event.starts_at - CHECKIN_MARGIN <= now,
                Event.ends_at + CHECKIN_MARGIN >= now,
            )
            .order_by(Event.starts_at, Event.id)
        )
        return await self._fetch(statement, None)

    async def _fetch(self, statement: Select[tuple[Event]], limit: int | None) -> Sequence[Event]:
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return (await self.session.execute(statement)).scalars().all()

    # --- writes ------------------------------------------------------------------------------

    async def create(
        self,
        *,
        organizer: User,
        title: str,
        kind: str,
        starts_at: datetime,
        ends_at: datetime,
        description: str = "",
        location: str = "",
        points: int | None = None,
        onboarding_step: str | None = None,
    ) -> Event:
        kind_config = self._require_kind(kind)
        event = Event(
            title=_require_text(title, "title", MAX_TITLE_LENGTH),
            description=_optional_text(description, "description", MAX_DESCRIPTION_LENGTH),
            kind=kind,
            location=_optional_text(location, "location", MAX_LOCATION_LENGTH),
            starts_at=require_aware(starts_at, "starts_at"),
            ends_at=require_aware(ends_at, "ends_at"),
            points=_require_points(points if points is not None else kind_config.default_points),
            onboarding_step=self._require_step(onboarding_step),
            organizer_id=organizer.id,
            qr_seed=codes.new_seed(),
            checkin_open=False,
        )
        _require_time_range(event.starts_at, event.ends_at)
        self.session.add(event)
        await self.session.flush()
        return event

    async def update(
        self,
        event: Event,
        *,
        title: str = UNSET,
        description: str = UNSET,
        kind: str = UNSET,
        location: str = UNSET,
        starts_at: datetime = UNSET,
        ends_at: datetime = UNSET,
        points: int = UNSET,
        onboarding_step: str | None = UNSET,
        checkin_open: bool = UNSET,
    ) -> Event:
        """Patch the fields that were actually passed; ``None`` clears ``onboarding_step``.

        Every value is validated *before* the first one is assigned, so a rejected patch leaves
        the event exactly as it was — the api commits its transaction on a domain error (§5's
        attempt ledger depends on that) and must never find half-applied changes.
        """
        changes: dict[str, Any] = {}
        if title is not UNSET:
            changes["title"] = _require_text(title, "title", MAX_TITLE_LENGTH)
        if description is not UNSET:
            changes["description"] = _optional_text(
                description, "description", MAX_DESCRIPTION_LENGTH
            )
        if kind is not UNSET:
            self._require_kind(kind)
            changes["kind"] = kind
        if location is not UNSET:
            changes["location"] = _optional_text(location, "location", MAX_LOCATION_LENGTH)
        if starts_at is not UNSET:
            changes["starts_at"] = require_aware(starts_at, "starts_at")
        if ends_at is not UNSET:
            changes["ends_at"] = require_aware(ends_at, "ends_at")
        if points is not UNSET:
            changes["points"] = _require_points(points)
        if onboarding_step is not UNSET:
            changes["onboarding_step"] = self._require_step(onboarding_step)
        if checkin_open is not UNSET:
            changes["checkin_open"] = bool(checkin_open)
        _require_time_range(
            changes.get("starts_at", event.starts_at), changes.get("ends_at", event.ends_at)
        )
        for field, value in changes.items():
            setattr(event, field, value)
        await self.session.flush()
        return event

    # --- validation --------------------------------------------------------------------------

    def _require_kind(self, kind: str) -> Any:
        kind_config = self.config.university.event_kind(kind)
        if kind_config is None:
            raise UnknownEventKindError(kind)
        return kind_config

    def _require_step(self, step_key: str | None) -> str | None:
        """Only an ``event_kind`` step can be attached to an event; ``manual`` steps cannot."""
        if step_key is None or step_key == "":
            return None
        step = self.config.university.step(step_key)
        if step is None or not isinstance(step, EventKindStep):
            raise UnknownOnboardingStepError(step_key)
        return step_key

    # --- codes -------------------------------------------------------------------------------

    def current_code(self, event: Event) -> QrCodeView:
        """The code for the window that contains "now", plus its deep link (§5).

        The caller must already have established ownership; ``checkin_open`` is enforced here
        because §7 makes GET /org/events/{id}/qr answer 403 when check-in is closed.
        """
        if not event.checkin_open:
            raise CheckinClosedError(f"event {event.id}")
        window = codes.current_code(
            event.qr_seed, event.id, self.now(), self.config.checkin_code_step_seconds
        )
        return QrCodeView(
            code=window.code,
            deeplink=checkin_deeplink(self.config.bot_username, event.id, window.code),
            window_started_at=window.window_started_at,
            expires_at=window.expires_at,
            step_seconds=self.config.checkin_code_step_seconds,
        )

    def verify_code(self, event: Event, code: str, *, now: datetime | None = None) -> bool:
        return codes.verify_code(
            event.qr_seed,
            event.id,
            code,
            now=now or self.now(),
            step_seconds=self.config.checkin_code_step_seconds,
            tolerance_steps=self.config.checkin_code_tolerance_steps,
        )

    def is_within_checkin_window(self, event: Event, *, now: datetime | None = None) -> bool:
        moment = now or self.now()
        return event.starts_at - CHECKIN_MARGIN <= moment <= event.ends_at + CHECKIN_MARGIN

    # --- views -------------------------------------------------------------------------------

    async def view(
        self, event: Event, *, viewer: User | None = None, lang: str | None = None
    ) -> EventView:
        views = await self.views([event], viewer=viewer, lang=lang)
        return views[0]

    async def views(
        self,
        events: Sequence[Event],
        *,
        viewer: User | None = None,
        lang: str | None = None,
    ) -> tuple[EventView, ...]:
        """Build views for a whole list with three queries instead of three per event."""
        if not events:
            return ()
        event_ids = [event.id for event in events]
        counts = await self._attendee_counts(event_ids)
        rsvps = await self._viewer_ids(Rsvp, viewer, event_ids)
        checkins = await self._viewer_ids(Checkin, viewer, event_ids)
        language = self.config.language_or_default(lang or (viewer.lang if viewer else None))
        return tuple(
            self._build_view(
                event,
                kind_title=self._kind_title(event.kind, language),
                rsvp=event.id in rsvps,
                checked_in=event.id in checkins,
                attendees_count=counts.get(event.id, 0),
            )
            for event in events
        )

    def _kind_title(self, kind: str, lang: str) -> str:
        kind_config = self.config.university.event_kind(kind)
        if kind_config is None:
            return kind
        return self.config.university.text(kind_config.title, lang)

    async def _attendee_counts(self, event_ids: Sequence[int]) -> dict[int, int]:
        result = await self.session.execute(
            select(Checkin.event_id, func.count())
            .where(Checkin.event_id.in_(event_ids))
            .group_by(Checkin.event_id)
        )
        return {event_id: int(count) for event_id, count in result.all()}

    async def _viewer_ids(
        self, model: type[Rsvp] | type[Checkin], viewer: User | None, event_ids: Sequence[int]
    ) -> set[int]:
        if viewer is None:
            return set()
        result = await self.session.execute(
            select(model.event_id).where(model.user_id == viewer.id, model.event_id.in_(event_ids))
        )
        return set(result.scalars().all())

    @staticmethod
    def _build_view(
        event: Event, *, kind_title: str, rsvp: bool, checked_in: bool, attendees_count: int
    ) -> EventView:
        return EventView(
            id=event.id,
            title=event.title,
            description=event.description,
            kind=event.kind,
            kind_title=kind_title,
            location=event.location,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            points=event.points,
            onboarding_step=event.onboarding_step,
            checkin_open=event.checkin_open,
            rsvp=rsvp,
            checked_in=checked_in,
            attendees_count=attendees_count,
        )


def _require_text(value: str, field: str, limit: int) -> str:
    trimmed = (value or "").strip()
    if not trimmed:
        raise ValidationFailedError(field, f"{field} must not be empty")
    if len(trimmed) > limit:
        raise ValidationFailedError(field, f"{field} must be at most {limit} characters")
    return trimmed


def _optional_text(value: str | None, field: str, limit: int) -> str:
    trimmed = (value or "").strip()
    if len(trimmed) > limit:
        raise ValidationFailedError(field, f"{field} must be at most {limit} characters")
    return trimmed


def _require_points(points: int) -> int:
    if points < 0:
        raise ValidationFailedError("points", "points must not be negative")
    return points


def _require_time_range(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at:
        raise InvalidTimeRangeError
