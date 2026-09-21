"""The demo data behind `python -m campus.seed`."""

from datetime import timedelta

from sqlalchemy import func, select

from campus.db.models import (
    Checkin,
    Event,
    ManualStepCompletion,
    Organizer,
    OrganizerInvite,
    OutboxMessage,
    Rsvp,
    User,
)
from campus.domain.services import EventService
from campus.seed import DEMO_MAX_USER_ID_BASE, seed
from tests.integration.factories import World


async def _count(world: World, model: type) -> int:
    result = await world.session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


async def test_seeding_creates_a_populated_campus(world: World) -> None:
    report = await seed(world.session, world.config, world.clock)

    assert report.users > 0
    assert report.organizers > 0
    assert report.events > 0
    assert await _count(world, User) == report.users
    assert await _count(world, Organizer) == report.organizers


async def test_seeded_manual_progress_does_not_send_messages_to_demo_users(world: World) -> None:
    await seed(world.session, world.config, world.clock)

    assert await _count(world, ManualStepCompletion) > 0
    assert await _count(world, OutboxMessage) == 0


async def test_reseeding_cancels_pending_notifications_from_old_seed(world: World) -> None:
    await seed(world.session, world.config, world.clock)
    student = (
        await world.session.execute(
            select(User).where(User.max_user_id == DEMO_MAX_USER_ID_BASE + 2)
        )
    ).scalar_one()
    manual = next(
        step.key for step in world.config.university.onboarding_steps if step.type == "manual"
    )
    old = await world.outbox.enqueue(
        user_id=student.id,
        kind="step_completed",
        payload={"step_key": manual},
        dedup_key=f"step_completed:{student.id}:{manual}",
    )
    reminder = await world.outbox.enqueue(user_id=student.id, kind="reminder")
    real_user = await world.user()
    real_notification = await world.outbox.enqueue(user_id=real_user.id, kind="step_completed")
    assert old is not None
    assert reminder is not None
    assert real_notification is not None

    await seed(world.session, world.config, world.clock)

    assert old.status == "cancelled"
    assert reminder.status == "cancelled"
    assert real_notification.status == "pending"


async def test_seeding_twice_changes_nothing(world: World) -> None:
    first = await seed(world.session, world.config, world.clock)

    second = await seed(world.session, world.config, world.clock)

    assert (second.users, second.organizers, second.events) == (
        first.users,
        first.organizers,
        first.events,
    )
    assert await _count(world, Event) == first.events
    assert await _count(world, Checkin) == first.checkins


async def test_the_second_run_keeps_the_same_rows(world: World) -> None:
    await seed(world.session, world.config, world.clock)
    before = await world.session.execute(select(Event.id, Event.qr_seed).order_by(Event.id))
    original = before.all()

    await seed(world.session, world.config, world.clock)

    after = await world.session.execute(select(Event.id, Event.qr_seed).order_by(Event.id))
    assert after.all() == original


async def test_some_events_are_open_for_check_in_right_now(world: World) -> None:
    """A demo is useless if nobody can scan anything (§11: seed is a one-shot service)."""
    await seed(world.session, world.config, world.clock)

    events = EventService(world.session, world.config, world.clock)
    open_now = await events.list_open_for_checkin()

    assert len(open_now) >= 1
    assert all(event.checkin_open for event in open_now)


async def test_a_code_from_a_seeded_event_really_checks_someone_in(world: World) -> None:
    await seed(world.session, world.config, world.clock)
    events = EventService(world.session, world.config, world.clock)
    event = (await events.list_open_for_checkin())[0]
    newcomer = await world.user(first_name="Новенькая")

    outcome = await world.checkins.check_in(
        user=newcomer, code=world.code_for(event), method="qr", event_id=event.id
    )

    assert outcome.already is False


async def test_there_are_past_events_with_attendance(world: World) -> None:
    await seed(world.session, world.config, world.clock)

    events = EventService(world.session, world.config, world.clock)
    past = await events.list_by_scope("past")

    assert len(past) >= 1
    assert await _count(world, Checkin) > 0


async def test_students_have_said_they_are_coming(world: World) -> None:
    await seed(world.session, world.config, world.clock)

    assert await _count(world, Rsvp) > 0


async def test_the_report_hands_over_an_organizer_invitation(world: World) -> None:
    report = await seed(world.session, world.config, world.clock)

    assert report.invite.token
    assert report.invite.deeplink.endswith(report.invite.token)
    assert report.invite.expires_at > world.clock.now()


async def test_the_invitation_is_reused_rather_than_piled_up(world: World) -> None:
    first = await seed(world.session, world.config, world.clock)

    second = await seed(world.session, world.config, world.clock)

    assert second.invite.token == first.invite.token
    assert await _count(world, OrganizerInvite) == 1


async def test_a_used_invitation_is_replaced_on_the_next_run(world: World) -> None:
    first = await seed(world.session, world.config, world.clock)
    newcomer = await world.user(first_name="Новый организатор")
    await world.organizers.accept_invite(token=first.invite.token, user=newcomer)

    second = await seed(world.session, world.config, world.clock)

    assert second.invite.token != first.invite.token


async def test_an_expired_invitation_is_replaced_on_the_next_run(world: World) -> None:
    first = await seed(world.session, world.config, world.clock)

    world.clock.advance(world.config.invite_ttl + timedelta(days=1))
    second = await seed(world.session, world.config, world.clock)

    assert second.invite.token != first.invite.token


async def test_demo_accounts_live_in_their_own_id_range(world: World) -> None:
    """Synthetic data only (§1): no real MAX account can be mistaken for one of these."""
    await seed(world.session, world.config, world.clock)

    result = await world.session.execute(select(User.max_user_id))
    assert all(max_user_id >= DEMO_MAX_USER_ID_BASE for max_user_id in result.scalars().all())


async def test_every_demo_student_has_consented(world: World) -> None:
    await seed(world.session, world.config, world.clock)

    result = await world.session.execute(select(func.count()).where(User.consent_at.is_(None)))
    assert int(result.scalar_one()) == 0


async def test_seeded_events_only_use_configured_kinds(world: World) -> None:
    await seed(world.session, world.config, world.clock)

    result = await world.session.execute(select(Event.kind).distinct())
    known = {kind.key for kind in world.config.university.event_kinds}
    assert set(result.scalars().all()) <= known


async def test_reseeding_after_a_day_moves_the_events_along(world: World) -> None:
    """The demo is re-runnable: an event that was open is open again on the next day."""
    await seed(world.session, world.config, world.clock)
    events = EventService(world.session, world.config, world.clock)

    world.clock.advance(timedelta(days=1))
    assert await events.list_open_for_checkin() == []
    await seed(world.session, world.config, world.clock)

    assert len(await events.list_open_for_checkin()) >= 1
