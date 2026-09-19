from datetime import timedelta

from campus.bot.workers.outbox import OutboxWorker
from campus.bot.workers.qr import QrWorker
from campus.max.client import MaxRateLimitError, MaxTransportError
from campus.max.fake import FakeMaxClient
from tests.integration.factories import World


async def test_qr_rotates_once_per_window_and_stops(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(
        event=event, organizer=organizer, max_user_id=organizer.max_user_id
    )
    client = FakeMaxClient()
    worker = QrWorker(client, world.config, world.clock)
    await worker.tick(world.session)
    assert client.uploaded[0].startswith(b"\x89PNG\r\n\x1a\n")
    assert len(client.sent) == 1
    assert display.message_id == client.sent[0].message_id
    await worker.tick(world.session)
    assert len(client.uploaded) == 1
    world.clock.advance(timedelta(seconds=10))
    await worker.tick(world.session)
    assert len(client.edited) == 1
    await world.qr_displays.stop(display)
    world.clock.advance(timedelta(seconds=10))
    await worker.tick(world.session)
    assert len(client.uploaded) == 2


async def test_outbox_retries_and_fails_without_leaking_errors(world: World) -> None:
    user = await world.user()
    row = await world.outbox.enqueue(user_id=user.id, kind="invite_accepted")
    assert row is not None
    client = FakeMaxClient()
    worker = OutboxWorker(client, world.config, world.clock)
    for attempt in range(5):
        client.fail_next("send_message", MaxTransportError("secret-token"))
        await worker.tick(world.session)
        assert row.attempts == attempt + 1
        assert row.last_error == "MaxTransportError"
        world.clock.advance(timedelta(hours=1))
    assert row.status == "failed"
    await worker.tick(world.session)
    assert len(client.calls) == 5


async def test_outbox_respects_retry_after(world: World) -> None:
    user = await world.user()
    row = await world.outbox.enqueue(user_id=user.id, kind="invite_accepted")
    assert row is not None
    client = FakeMaxClient()
    client.fail_next("send_message", MaxRateLimitError(retry_after=600))
    worker = OutboxWorker(client, world.config, world.clock)
    await worker.tick(world.session)
    assert row.run_at >= world.clock.now() + timedelta(seconds=600)
    world.clock.advance(timedelta(seconds=600))
    await worker.tick(world.session)
    assert row.status == "sent"


async def test_qr_closed_and_expired_displays_retire(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(
        event=event, organizer=organizer, max_user_id=organizer.max_user_id
    )
    client = FakeMaxClient()
    worker = QrWorker(client, world.config, world.clock)
    await world.events.update(event, checkin_open=False)
    await worker.tick(world.session)
    assert display.stopped_at is not None
    assert not client.uploaded
    await world.events.update(event, checkin_open=True)
    display = await world.qr_displays.start(
        event=event, organizer=organizer, max_user_id=organizer.max_user_id
    )
    world.clock.set(event.ends_at)
    await worker.tick(world.session)
    await world.session.refresh(display)
    assert display.stopped_at is not None
    assert not client.uploaded


async def test_qr_failure_does_not_advance_window_and_respects_cooldown(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(
        event=event, organizer=organizer, max_user_id=organizer.max_user_id
    )
    client = FakeMaxClient()
    worker = QrWorker(client, world.config, world.clock)
    client.fail_next("upload_image", MaxRateLimitError(retry_after=60))
    await worker.tick(world.session)
    assert display.last_rendered_window is None
    await worker.tick(world.session)
    assert len(client.calls) == 1
    world.clock.advance(timedelta(seconds=60))
    await worker.tick(world.session)
    assert display.message_id is not None


async def test_all_outbox_notifications_and_qr_start(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    user = await world.user(lang="en")
    for kind, payload in [
        ("reminder", {"event_id": event.id}),
        ("checkin_confirmed", {"event_id": event.id}),
        ("step_completed", {"step_key": world.config.university.onboarding_steps[0].key}),
        ("invite_accepted", {}),
    ]:
        await world.outbox.enqueue(user_id=user.id, kind=kind, payload=payload)
    display = await world.qr_displays.start(
        event=event, organizer=organizer, max_user_id=organizer.max_user_id
    )
    client = FakeMaxClient()
    worker = OutboxWorker(client, world.config, world.clock)
    await worker.tick(world.session)
    assert await world.outbox.count(status="sent") == 5
    assert len(client.sent) == 5
    assert display.message_id is not None
    assert all(not item.text.startswith("bot.") for item in client.sent)
    await worker.tick(world.session)
    assert len(client.sent) == 5


async def test_polling_persists_marker_only_after_dispatch(world: World) -> None:
    from campus.bot.polling import process_page
    from campus.max.types import UnknownUpdate, UpdatesPage

    await world.kv.set_updates_marker(10)

    async def dispatch(update: object) -> None:
        assert await world.kv.updates_marker() == 10

    await process_page(
        UpdatesPage(updates=(UnknownUpdate(), UnknownUpdate()), marker=20),
        dispatch,
        world.kv.set_updates_marker,
    )
    assert await world.kv.updates_marker() == 20
