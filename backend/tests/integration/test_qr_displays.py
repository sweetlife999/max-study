"""QrDisplayService: the bookkeeping behind the rotating QR in a chat (ARCHITECTURE.md §8)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from campus.db.models import OutboxMessage
from campus.domain import codes
from campus.domain.errors import (
    CheckinClosedError,
    QrDisplayNotFoundError,
    ValidationFailedError,
)
from tests.integration.factories import World

CHAT_ID = 555


async def test_starting_a_display_records_where_it_goes(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    assert display.event_id == event.id
    assert display.organizer_id == organizer.id
    assert display.max_chat_id == CHAT_ID
    assert display.active_until == event.ends_at
    assert display.stopped_at is None


async def test_a_display_can_target_a_user_instead_of_a_chat(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    display = await world.qr_displays.start(
        event=event, organizer=organizer, max_user_id=organizer.max_user_id
    )

    assert display.max_user_id == organizer.max_user_id
    assert display.max_chat_id is None


async def test_a_display_needs_somewhere_to_go(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    with pytest.raises(ValidationFailedError):
        await world.qr_displays.start(event=event, organizer=organizer)


async def test_a_closed_event_shows_no_qr(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(organizer=organizer)

    with pytest.raises(CheckinClosedError):
        await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)


async def test_only_one_display_per_organizer_and_event(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    first = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    again = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=777)

    assert again.id == first.id
    assert again.max_chat_id == 777


async def test_restarting_refreshes_the_end_time(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    later = event.ends_at + timedelta(hours=1)
    await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    again = await world.qr_displays.start(
        event=event, organizer=organizer, max_chat_id=CHAT_ID, active_until=later
    )

    assert again.active_until == later


async def test_a_naive_end_time_is_refused(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    with pytest.raises(ValidationFailedError):
        await world.qr_displays.start(
            event=event,
            organizer=organizer,
            max_chat_id=CHAT_ID,
            active_until=world.clock.now().replace(tzinfo=None),
        )


async def test_starting_a_display_asks_the_bot_to_draw_it(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    result = await world.session.execute(
        select(OutboxMessage).where(
            OutboxMessage.user_id == organizer.id, OutboxMessage.kind == "qr_display_start"
        )
    )
    queued = result.scalars().all()
    assert len(queued) == 1
    assert queued[0].payload == {"event_id": event.id, "qr_display_id": display.id}


# --- what the worker asks for -----------------------------------------------------------------


async def test_active_lists_running_displays(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    assert [found.id for found in await world.qr_displays.active()] == [display.id]


async def test_a_stopped_display_is_no_longer_active(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    await world.qr_displays.stop(display)

    assert await world.qr_displays.active() == []
    assert display.stopped_at == world.clock.now()


async def test_stopping_twice_keeps_the_first_moment(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)
    await world.qr_displays.stop(display)
    first = display.stopped_at

    world.clock.advance(timedelta(minutes=5))
    await world.qr_displays.stop(display)

    assert display.stopped_at == first


async def test_a_display_past_its_end_is_no_longer_active(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    world.clock.advance(timedelta(hours=4))

    assert await world.qr_displays.active() == []


async def test_expired_displays_are_retired(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    world.clock.advance(timedelta(hours=4))
    retired = await world.qr_displays.stop_expired()

    await world.session.refresh(display)
    assert retired == 1
    assert display.stopped_at is not None


async def test_stopping_by_event_finds_the_display(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    stopped = await world.qr_displays.stop_for(organizer_id=organizer.id, event_id=event.id)

    assert stopped.id == display.id


async def test_stopping_something_that_is_not_running_says_so(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    with pytest.raises(QrDisplayNotFoundError):
        await world.qr_displays.stop_for(organizer_id=organizer.id, event_id=event.id)


# --- window bookkeeping -----------------------------------------------------------------------


async def test_a_fresh_display_is_due_at_once(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    assert [found.id for found in await world.qr_displays.due()] == [display.id]


async def test_a_display_drawn_for_this_window_is_not_due(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)
    window = codes.window_for(world.clock.now(), world.config.checkin_code_step_seconds)

    await world.qr_displays.record_render(display, window=window, message_id="mid-1")

    assert await world.qr_displays.due() == []
    assert display.message_id == "mid-1"


async def test_it_is_due_again_in_the_next_window(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)
    step = world.config.checkin_code_step_seconds
    await world.qr_displays.record_render(
        display, window=codes.window_for(world.clock.now(), step), message_id="mid-1"
    )

    world.clock.advance(timedelta(seconds=step))

    assert [found.id for found in await world.qr_displays.due()] == [display.id]


async def test_a_redraw_keeps_the_message_id_when_none_is_given(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)
    await world.qr_displays.record_render(display, window=1, message_id="mid-1")

    await world.qr_displays.record_render(display, window=2, message_id=None)

    assert display.message_id == "mid-1"
    assert display.last_rendered_window == 2


async def test_the_next_window_is_when_this_code_expires(world: World) -> None:
    step = world.config.checkin_code_step_seconds
    now = world.clock.now()

    next_at = world.qr_displays.next_window_at()

    assert next_at > now
    assert next_at - now <= timedelta(seconds=step)
    assert codes.window_for(next_at, step) == codes.window_for(now, step) + 1


async def test_active_for_finds_only_this_organizers_display(world: World) -> None:
    organizer = await world.organizer()
    other = await world.organizer(first_name="Другой")
    event = await world.open_event_now(organizer=organizer)
    await world.qr_displays.start(event=event, organizer=organizer, max_chat_id=CHAT_ID)

    assert await world.qr_displays.active_for(organizer_id=other.id, event_id=event.id) is None
