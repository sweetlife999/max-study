"""Builders shared by the domain integration tests."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count

from sqlalchemy.ext.asyncio import AsyncSession

from campus.config import UniversityConfig
from campus.db.models import Event, User
from campus.domain import codes
from campus.domain.clock import FixedClock
from campus.domain.context import DomainConfig
from campus.domain.services import (
    AttendanceService,
    CheckinService,
    EventService,
    KeyValueService,
    OnboardingService,
    OrganizerService,
    OutboxService,
    QrDisplayService,
    RsvpService,
    UserService,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BOT_USERNAME = "campus_bot"

_max_user_ids: Iterator[int] = count(900_000)


def next_max_user_id() -> int:
    return next(_max_user_ids)


@dataclass(frozen=True, slots=True)
class World:
    """One session, one clock and every service wired to them."""

    session: AsyncSession
    clock: FixedClock
    config: DomainConfig

    @property
    def users(self) -> UserService:
        return UserService(self.session, self.config, self.clock)

    @property
    def organizers(self) -> OrganizerService:
        return OrganizerService(self.session, self.config, self.clock)

    @property
    def events(self) -> EventService:
        return EventService(self.session, self.config, self.clock)

    @property
    def rsvps(self) -> RsvpService:
        return RsvpService(self.session, self.config, self.clock)

    @property
    def checkins(self) -> CheckinService:
        return CheckinService(self.session, self.config, self.clock)

    @property
    def onboarding(self) -> OnboardingService:
        return OnboardingService(self.session, self.config, self.clock)

    @property
    def outbox(self) -> OutboxService:
        return OutboxService(self.session, self.config, self.clock)

    @property
    def qr_displays(self) -> QrDisplayService:
        return QrDisplayService(self.session, self.config, self.clock)

    @property
    def kv(self) -> KeyValueService:
        return KeyValueService(self.session, self.config, self.clock)

    @property
    def attendance(self) -> AttendanceService:
        return AttendanceService(self.session, self.config, self.clock)

    # --- builders ----------------------------------------------------------------------------

    async def user(
        self, *, first_name: str = "Аня", lang: str = "ru", consent: bool = True
    ) -> User:
        created = await self.users.get_or_create(
            max_user_id=next_max_user_id(), first_name=first_name, lang=lang
        )
        if consent:
            await self.users.give_consent(created)
        return created

    async def organizer(self, *, first_name: str = "Организатор") -> User:
        person = await self.user(first_name=first_name)
        await self.organizers.grant(user_id=person.id)
        return person

    async def event(
        self,
        *,
        organizer: User,
        kind: str = "club",
        title: str = "Клуб",
        starts_in: timedelta = timedelta(hours=1),
        duration: timedelta = timedelta(hours=2),
        points: int | None = None,
        onboarding_step: str | None = None,
        checkin_open: bool = False,
    ) -> Event:
        starts_at = self.clock.now() + starts_in
        created = await self.events.create(
            organizer=organizer,
            title=title,
            kind=kind,
            starts_at=starts_at,
            ends_at=starts_at + duration,
            description="Описание",
            location="А-101",
            points=points,
            onboarding_step=onboarding_step,
        )
        if checkin_open:
            await self.events.update(created, checkin_open=True)
        return created

    async def open_event_now(self, *, organizer: User, **kwargs: object) -> Event:
        """An event that is running right now with check-in open."""
        return await self.event(
            organizer=organizer,
            starts_in=-timedelta(minutes=10),
            checkin_open=True,
            **kwargs,  # pyright: ignore[reportArgumentType]
        )

    def code_for(self, event: Event) -> str:
        """The code a scanner would read at this instant."""
        return codes.current_code(
            event.qr_seed, event.id, self.clock.now(), self.config.checkin_code_step_seconds
        ).code


def make_world(
    session: AsyncSession,
    university: UniversityConfig,
    *,
    admin_max_user_ids: frozenset[int] = frozenset(),
    clock: FixedClock | None = None,
) -> World:
    return World(
        session=session,
        clock=clock or FixedClock(NOW),
        config=DomainConfig(
            university=university,
            admin_max_user_ids=admin_max_user_ids,
            bot_username=BOT_USERNAME,
        ),
    )
