"""Synthetic demo data: ``python -m campus.seed``.

Everything here is invented (ARCHITECTURE.md §1: "Все данные в демо — синтетические"). Demo
accounts sit in their own ``max_user_id`` range so that they can never collide with a real MAX
account, and the whole script is idempotent: running it again moves the events to fresh times
around "now" and leaves every row — ``qr_seed`` included — where it was.

It finishes by printing an organizer invitation, which is how a demo operator becomes an
organizer without an admin id in the environment.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from campus.config import Settings, load_university_config
from campus.db.models import Checkin, Event, OrganizerInvite, User
from campus.db.session import create_engine, create_session_factory, session_scope
from campus.domain.clock import Clock, SystemClock
from campus.domain.context import DomainConfig
from campus.domain.deeplinks import invite_deeplink
from campus.domain.services import (
    EventService,
    OnboardingService,
    OrganizerService,
    RsvpService,
    UserService,
)
from campus.domain.views import InviteView
from campus.logs import configure_logging

logger = logging.getLogger("campus.seed")

# Demo accounts live above this; a real MAX user id will not reach it.
DEMO_MAX_USER_ID_BASE = 900_000_000_000

ORGANIZER_NAMES = ("Марина", "Игорь")
STUDENT_NAMES = (
    "Аня",
    "Борис",
    "Вера",
    "Глеб",
    "Дина",
    "Егор",
    "Жанна",
    "Захар",
)
MANUAL_STEP_TAKERS = 3


@dataclass(frozen=True, slots=True)
class DemoEvent:
    """One event of the demo timetable, positioned relative to "now"."""

    title: str
    kind: str
    location: str
    description: str
    starts_in: timedelta
    duration: timedelta
    checkin_open: bool = False
    onboarding_step: str | None = None
    organizer_index: int = 0
    attendees: int = 0


# Two events are running right now with check-in open, so a demo can be scanned immediately.
DEMO_TIMETABLE: tuple[DemoEvent, ...] = (
    DemoEvent(
        title="Экскурсия по кампусу",
        kind="campus_tour",
        location="Холл главного корпуса",
        description="Учебные корпуса, библиотека, столовая и деканат за один час.",
        starts_in=-timedelta(minutes=20),
        duration=timedelta(hours=2),
        checkin_open=True,
        onboarding_step="campus_tour",
        attendees=2,
    ),
    DemoEvent(
        title="Встреча с куратором группы",
        kind="curator_meeting",
        location="Аудитория 312",
        description="Знакомство с куратором, расписание и первые вопросы.",
        starts_in=-timedelta(minutes=10),
        duration=timedelta(hours=1),
        checkin_open=True,
        organizer_index=1,
        attendees=1,
    ),
    DemoEvent(
        title="Клуб настольных игр",
        kind="club",
        location="Студенческое пространство, 2 этаж",
        description="Приходите играть: правила объясним на месте.",
        starts_in=timedelta(hours=5),
        duration=timedelta(hours=3),
        organizer_index=1,
    ),
    DemoEvent(
        title="Открытый студсовет",
        kind="council",
        location="Актовый зал",
        description="Как устроено студенческое самоуправление и куда нести свои идеи.",
        starts_in=timedelta(days=2),
        duration=timedelta(hours=2),
    ),
    DemoEvent(
        title="Субботник в парке кампуса",
        kind="volunteering",
        location="Парк за общежитием №3",
        description="Инвентарь и чай выдаём на месте.",
        starts_in=-timedelta(days=6),
        duration=timedelta(hours=4),
        organizer_index=1,
        attendees=5,
    ),
    DemoEvent(
        title="Экскурсия по библиотеке",
        kind="campus_tour",
        location="Библиотека, вход со двора",
        description="Где искать учебники и как продлить книги.",
        starts_in=-timedelta(days=3),
        duration=timedelta(hours=1),
        attendees=4,
    ),
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    users: int
    organizers: int
    events: int
    checkins: int
    rsvps: int
    invite: InviteView

    def lines(self) -> list[str]:
        return [
            f"users:      {self.users}",
            f"organizers: {self.organizers}",
            f"events:     {self.events}",
            f"check-ins:  {self.checkins}",
            f"rsvps:      {self.rsvps}",
            "",
            "Organizer invitation (single use):",
            f"  token:    {self.invite.token}",
            f"  deeplink: {self.invite.deeplink}",
            f"  expires:  {self.invite.expires_at:%Y-%m-%d %H:%M %Z}",
        ]


async def seed(session: AsyncSession, config: DomainConfig, clock: Clock) -> SeedReport:
    """Create or refresh the demo campus. Safe to run repeatedly."""
    users = UserService(session, config, clock)
    organizers = OrganizerService(session, config, clock)
    events = EventService(session, config, clock)
    rsvps = RsvpService(session, config, clock)
    onboarding = OnboardingService(session, config, clock)

    staff = await _people(users, ORGANIZER_NAMES, offset=0)
    for person in staff:
        await organizers.grant(user_id=person.id)
    students = await _people(users, STUDENT_NAMES, offset=len(ORGANIZER_NAMES))

    timetable = [
        await _event(events, planned, organizer=staff[planned.organizer_index], clock=clock)
        for planned in DEMO_TIMETABLE
    ]

    checkins = await _attendance(session, timetable, students, clock)
    rsvp_count = await _interest(rsvps, timetable, students, clock)
    await _manual_steps(onboarding, config, students)

    return SeedReport(
        users=len(staff) + len(students),
        organizers=len(staff),
        events=len(timetable),
        checkins=checkins,
        rsvps=rsvp_count,
        invite=await _invitation(session, organizers, clock),
    )


async def _people(users: UserService, names: Sequence[str], *, offset: int) -> list[User]:
    """One stable demo account per name; the id is derived from its position, not a sequence."""
    people: list[User] = []
    for index, name in enumerate(names):
        person = await users.get_or_create(
            max_user_id=DEMO_MAX_USER_ID_BASE + offset + index, first_name=name
        )
        await users.give_consent(person)
        people.append(person)
    return people


async def _event(
    events: EventService, planned: DemoEvent, *, organizer: User, clock: Clock
) -> Event:
    """Create the event, or move an existing one to its place in today's timetable."""
    starts_at = clock.now() + planned.starts_in
    ends_at = starts_at + planned.duration
    event = await _find_event(events, organizer_id=organizer.id, title=planned.title)
    if event is None:
        event = await events.create(
            organizer=organizer,
            title=planned.title,
            kind=planned.kind,
            location=planned.location,
            description=planned.description,
            starts_at=starts_at,
            ends_at=ends_at,
            onboarding_step=planned.onboarding_step,
        )
    # Also on the first run: create() cannot open check-in, and re-running has to move the
    # whole timetable to the new "now" without touching the seed the QR codes come from.
    return await events.update(
        event, starts_at=starts_at, ends_at=ends_at, checkin_open=planned.checkin_open
    )


async def _find_event(events: EventService, *, organizer_id: int, title: str) -> Event | None:
    result = await events.session.execute(
        select(Event).where(Event.organizer_id == organizer_id, Event.title == title)
    )
    return result.scalars().first()


async def _attendance(
    session: AsyncSession, timetable: Sequence[Event], students: Sequence[User], clock: Clock
) -> int:
    """Manufacture the history a fresh demo needs.

    CheckinService deliberately refuses a check-in outside an event's window, which is exactly
    where past attendance belongs, so these rows are written directly.
    """
    total = 0
    for event, planned in zip(timetable, DEMO_TIMETABLE, strict=True):
        moment = min(event.starts_at + timedelta(minutes=5), clock.now())
        for student in students[: planned.attendees]:
            await session.execute(
                pg_insert(Checkin)
                .values(user_id=student.id, event_id=event.id, method="qr", created_at=moment)
                .on_conflict_do_nothing(index_elements=[Checkin.user_id, Checkin.event_id])
            )
        total += planned.attendees
    return total


async def _interest(
    rsvps: RsvpService, timetable: Sequence[Event], students: Sequence[User], clock: Clock
) -> int:
    """Half of the group says it is coming to each event that has not happened yet."""
    coming = students[: len(students) // 2]
    total = 0
    for event in timetable:
        if event.starts_at <= clock.now():
            continue
        for student in coming:
            await rsvps.put(user=student, event=event)
            total += 1
    return total


async def _manual_steps(
    onboarding: OnboardingService, config: DomainConfig, students: Sequence[User]
) -> None:
    """A few students have already ticked the first manual step, so progress is not all zeros."""
    manual = [step.key for step in config.university.onboarding_steps if step.type == "manual"]
    if not manual:
        return
    for student in students[:MANUAL_STEP_TAKERS]:
        await onboarding.complete_manual(student, manual[0])


async def _invitation(
    session: AsyncSession, organizers: OrganizerService, clock: Clock
) -> InviteView:
    """Reuse the demo invitation while it is still usable, otherwise mint a new one."""
    result = await session.execute(
        select(OrganizerInvite)
        .where(OrganizerInvite.used_by.is_(None), OrganizerInvite.expires_at > clock.now())
        .order_by(OrganizerInvite.expires_at.desc())
    )
    existing = result.scalars().first()
    if existing is not None:
        return InviteView(
            token=existing.token,
            deeplink=invite_deeplink(organizers.config.bot_username, existing.token),
            expires_at=existing.expires_at,
        )
    return await organizers.create_invite()


async def _run() -> SeedReport:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    university = load_university_config(settings.university_config_path)
    config = DomainConfig.from_settings(settings, university)
    engine = create_engine(settings.database_url)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            return await seed(session, config, SystemClock())
    finally:
        await engine.dispose()


def main() -> int:
    report = asyncio.run(_run())
    for line in report.lines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
