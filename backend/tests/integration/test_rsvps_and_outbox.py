"""RsvpService and OutboxService (ARCHITECTURE.md §6, §7, §8)."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from campus.config import UniversityConfig
from campus.db.models import OutboxMessage, User
from campus.domain.context import OUTBOX_MAX_ATTEMPTS, OUTBOX_RETRY_MAX_DELAY
from campus.domain.errors import ValidationFailedError
from campus.domain.services import OutboxService
from campus.domain.services.outbox import retry_delay
from campus.domain.services.rsvps import reminder_dedup_key
from tests.integration.factories import World, make_world


async def _messages(world: World, user_id: int) -> list[OutboxMessage]:
    result = await world.session.execute(
        select(OutboxMessage)
        .where(OutboxMessage.user_id == user_id)
        .order_by(OutboxMessage.run_at, OutboxMessage.id)
    )
    return list(result.scalars().all())


# --- rsvps ------------------------------------------------------------------------------------


async def test_saying_i_am_going_is_recorded(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))

    await world.rsvps.put(user=student, event=event)

    assert await world.rsvps.has(student.id, event.id) is True
    assert await world.rsvps.count(event.id) == 1


async def test_saying_it_twice_changes_nothing(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))

    await world.rsvps.put(user=student, event=event)
    await world.rsvps.put(user=student, event=event)

    assert await world.rsvps.count(event.id) == 1


async def test_taking_it_back_removes_it(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))
    await world.rsvps.put(user=student, event=event)

    await world.rsvps.delete(user=student, event=event)

    assert await world.rsvps.has(student.id, event.id) is False


async def test_taking_back_something_never_said_is_not_an_error(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))

    await world.rsvps.delete(user=student, event=event)

    assert await world.rsvps.has(student.id, event.id) is False


# --- the reminders an rsvp schedules ----------------------------------------------------------


async def test_an_rsvp_queues_one_reminder_per_configured_offset(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))

    await world.rsvps.put(user=student, event=event)

    queued = await _messages(world, student.id)
    offsets = world.config.university.reminders_before
    assert [message.kind for message in queued] == ["reminder"] * len(offsets)
    assert [message.run_at for message in queued] == sorted(
        event.starts_at - offset for offset in offsets
    )


async def test_a_reminder_whose_moment_has_passed_is_not_queued(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    # PT24H is already in the past for an event starting in two hours; PT1H is not.
    event = await world.event(organizer=organizer, starts_in=timedelta(hours=2))

    await world.rsvps.put(user=student, event=event)

    queued = await _messages(world, student.id)
    assert [message.payload["offset_seconds"] for message in queued] == [3600]


async def test_repeating_the_rsvp_reschedules_rather_than_piles_up(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))
    await world.rsvps.put(user=student, event=event)

    await world.events.update(
        event,
        starts_at=event.starts_at + timedelta(days=1),
        ends_at=event.ends_at + timedelta(days=1),
    )
    await world.rsvps.put(user=student, event=event)

    queued = await _messages(world, student.id)
    assert len(queued) == len(world.config.university.reminders_before)
    assert [message.run_at for message in queued] == sorted(
        event.starts_at - offset for offset in world.config.university.reminders_before
    )


async def test_editing_event_reschedules_reminders_without_a_new_rsvp(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))
    await world.rsvps.put(user=student, event=event)

    await world.events.update(
        event,
        starts_at=event.starts_at + timedelta(days=1),
        ends_at=event.ends_at + timedelta(days=1),
    )

    queued = await _messages(world, student.id)
    assert [message.run_at for message in queued] == sorted(
        event.starts_at - offset for offset in world.config.university.reminders_before
    )


async def test_moving_event_nearby_cancels_reminders_whose_time_has_passed(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))
    await world.rsvps.put(user=student, event=event)

    await world.events.update(
        event,
        starts_at=world.clock.now() + timedelta(hours=2),
        ends_at=world.clock.now() + timedelta(hours=4),
    )

    pending = [row for row in await _messages(world, student.id) if row.status == "pending"]
    assert len(pending) == 1
    assert pending[0].run_at == event.starts_at - timedelta(hours=1)


async def test_cancelling_the_rsvp_cancels_the_reminders(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(days=3))
    await world.rsvps.put(user=student, event=event)

    await world.rsvps.delete(user=student, event=event)

    assert {message.status for message in await _messages(world, student.id)} == {"cancelled"}


async def test_reminders_are_scoped_to_one_user_and_event(world: World) -> None:
    key = reminder_dedup_key(7, 9, timedelta(hours=1))

    assert key != reminder_dedup_key(7, 10, timedelta(hours=1))
    assert key != reminder_dedup_key(8, 9, timedelta(hours=1))
    assert key != reminder_dedup_key(7, 9, timedelta(hours=24))


# --- the outbox itself ------------------------------------------------------------------------


async def test_an_unknown_kind_is_refused(world: World) -> None:
    student = await world.user()

    with pytest.raises(ValidationFailedError):
        await world.outbox.enqueue(user_id=student.id, kind="carrier_pigeon")


async def test_a_message_without_a_dedup_key_is_always_a_new_row(world: World) -> None:
    student = await world.user()

    await world.outbox.enqueue(user_id=student.id, kind="checkin_confirmed")
    await world.outbox.enqueue(user_id=student.id, kind="checkin_confirmed")

    assert len(await _messages(world, student.id)) == 2


async def test_a_dedup_key_makes_the_second_enqueue_an_update(world: World) -> None:
    student = await world.user()
    later = world.clock.now() + timedelta(hours=2)

    await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k")
    await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k", run_at=later)

    queued = await _messages(world, student.id)
    assert len(queued) == 1
    assert queued[0].run_at == later


async def test_a_sent_message_is_never_resurrected(world: World) -> None:
    student = await world.user()
    first = await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k")
    assert first is not None
    await world.outbox.mark_sent(first)

    again = await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k")

    assert again is None
    assert first.status == "sent"


async def test_a_cancelled_message_can_be_queued_again(world: World) -> None:
    student = await world.user()
    await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k")
    await world.outbox.cancel(["k"])

    revived = await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k")

    assert revived is not None
    assert revived.status == "pending"


async def test_cancel_reports_what_it_cancelled(world: World) -> None:
    student = await world.user()
    await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k")

    assert await world.outbox.cancel(["k", "", "missing"]) == 1
    assert await world.outbox.cancel([]) == 0


async def test_a_naive_run_at_is_refused(world: World) -> None:
    student = await world.user()

    with pytest.raises(ValidationFailedError):
        await world.outbox.enqueue(
            user_id=student.id,
            kind="reminder",
            run_at=world.clock.now().replace(tzinfo=None),
        )


# --- claiming ---------------------------------------------------------------------------------


async def test_claim_takes_only_what_is_due(world: World) -> None:
    student = await world.user()
    due = await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="due")
    await world.outbox.enqueue(
        user_id=student.id,
        kind="reminder",
        dedup_key="later",
        run_at=world.clock.now() + timedelta(hours=1),
    )
    assert due is not None

    claimed = await world.outbox.claim()

    assert [message.id for message in claimed] == [due.id]


async def test_claim_ignores_messages_that_are_not_pending(world: World) -> None:
    student = await world.user()
    message = await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="k")
    assert message is not None
    await world.outbox.mark_sent(message)

    assert await world.outbox.claim() == []


async def test_claim_respects_its_limit_and_takes_the_oldest_first(world: World) -> None:
    student = await world.user()
    for index in range(3):
        await world.outbox.enqueue(
            user_id=student.id,
            kind="reminder",
            dedup_key=f"k{index}",
            run_at=world.clock.now() - timedelta(minutes=index),
        )

    claimed = await world.outbox.claim(limit=2)

    assert len(claimed) == 2
    assert claimed[0].run_at < claimed[1].run_at


async def test_a_second_worker_skips_rows_the_first_holds(
    engine: AsyncEngine, university_config: UniversityConfig
) -> None:
    """FOR UPDATE SKIP LOCKED (§8): two workers never deliver the same message.

    This one cannot use the rolled-back ``session`` fixture: a lock is only visible to another
    connection once the row is committed. It therefore commits its own rows and deletes them
    again, which is also the only way the skip is actually exercised.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    other_world = make_world(factory(), university_config)
    worker_session = factory()
    student_id: int | None = None
    try:
        student = await other_world.user()
        student_id = student.id
        await other_world.outbox.enqueue(user_id=student_id, kind="reminder", dedup_key="only")
        await other_world.session.commit()

        held = await other_world.outbox.claim()
        assert len(held) == 1

        worker = OutboxService(worker_session, other_world.config, other_world.clock)
        assert await worker.claim() == []
    finally:
        await worker_session.rollback()
        await worker_session.close()
        await other_world.session.rollback()
        if student_id is not None:
            # Everything this test committed cascades from the user row.
            await other_world.session.execute(delete(User).where(User.id == student_id))
            await other_world.session.commit()
        await other_world.session.close()


# --- delivery bookkeeping ---------------------------------------------------------------------


async def test_a_sent_message_counts_its_attempt(world: World) -> None:
    student = await world.user()
    message = await world.outbox.enqueue(user_id=student.id, kind="reminder")
    assert message is not None

    await world.outbox.mark_sent(message)

    assert (message.status, message.attempts, message.last_error) == ("sent", 1, None)


async def test_a_failure_schedules_a_retry(world: World) -> None:
    student = await world.user()
    message = await world.outbox.enqueue(user_id=student.id, kind="reminder")
    assert message is not None

    await world.outbox.mark_failed(message, "MAX said 500")

    assert message.status == "pending"
    assert message.attempts == 1
    assert message.last_error == "MAX said 500"
    assert message.run_at == world.clock.now() + retry_delay(1)


async def test_retries_back_off_and_then_give_up(world: World) -> None:
    student = await world.user()
    message = await world.outbox.enqueue(user_id=student.id, kind="reminder")
    assert message is not None

    delays: list[timedelta] = []
    for _ in range(OUTBOX_MAX_ATTEMPTS - 1):
        before = message.run_at
        await world.outbox.mark_failed(message, "boom")
        delays.append(message.run_at - world.clock.now())
        assert message.run_at >= before
    await world.outbox.mark_failed(message, "boom")

    assert delays == sorted(delays)
    assert delays[0] < delays[-1]
    assert message.status == "failed"
    assert message.attempts == OUTBOX_MAX_ATTEMPTS


async def test_a_long_error_is_truncated_not_rejected(world: World) -> None:
    student = await world.user()
    message = await world.outbox.enqueue(user_id=student.id, kind="reminder")
    assert message is not None

    await world.outbox.mark_failed(message, "x" * 10_000)

    assert message.last_error is not None
    assert len(message.last_error) < 10_000


async def test_the_backoff_is_capped(world: World) -> None:
    assert retry_delay(1) < retry_delay(2) < retry_delay(3)
    assert retry_delay(100) == OUTBOX_RETRY_MAX_DELAY
    assert retry_delay(0) == retry_delay(1)


async def test_counting_by_status(world: World) -> None:
    # count() is a whole-table gauge for the worker's logs, so compare against a baseline.
    before_sent = await world.outbox.count(status="sent")
    before_pending = await world.outbox.count(status="pending")
    before_total = await world.outbox.count()
    student = await world.user()
    sent = await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="a")
    await world.outbox.enqueue(user_id=student.id, kind="reminder", dedup_key="b")
    assert sent is not None

    await world.outbox.mark_sent(sent)

    assert await world.outbox.count(status="sent") == before_sent + 1
    assert await world.outbox.count(status="pending") == before_pending + 1
    assert await world.outbox.count() == before_total + 2
