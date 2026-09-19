"""Process infrastructure against PostgreSQL, including singleton exclusion."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from campus.db.runtime import BOT_LOCK_KEY, BotAlreadyRunningError, bot_lock, database_healthy
from tests.integration.factories import World


async def test_bot_lock_excludes_second_connection_and_releases(engine: AsyncEngine) -> None:
    assert await database_healthy(engine)
    async with bot_lock(engine) as probe:
        await probe()
        with pytest.raises(BotAlreadyRunningError):
            async with bot_lock(engine):
                pytest.fail("a second bot acquired the lock")
    async with bot_lock(engine):
        assert await database_healthy(engine)


async def test_bot_lock_probe_detects_terminated_connection(engine: AsyncEngine) -> None:
    async with bot_lock(engine) as probe:
        await probe()
        async with engine.connect() as connection:
            pid = await connection.scalar(
                text(
                    "SELECT pid FROM pg_locks WHERE locktype='advisory' "
                    "AND classid=:hi AND objid=:lo AND objsubid=1 AND granted"
                ),
                {"hi": BOT_LOCK_KEY >> 32, "lo": BOT_LOCK_KEY & 0xFFFFFFFF},
            )
            await connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with pytest.raises(DBAPIError):
            await probe()
    async with bot_lock(engine) as probe:
        await probe()


async def test_bot_lock_releases_after_failure(engine: AsyncEngine) -> None:
    with pytest.raises(ValueError, match="failure"):
        async with bot_lock(engine):
            raise ValueError("failure")
    async with bot_lock(engine):
        assert await database_healthy(engine)


async def test_outbox_respects_long_server_retry_after(world: World) -> None:
    user = await world.user()
    row = await world.outbox.enqueue(user_id=user.id, kind="invite_accepted")
    assert row is not None
    await world.outbox.mark_failed(row, "MaxRateLimitError", retry_after=120)
    assert row.run_at == world.clock.now() + timedelta(seconds=120)
    assert row.attempts == 1


async def test_display_lookup_and_locked_due(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    display = await world.qr_displays.start(
        event=event, organizer=organizer, max_user_id=organizer.max_user_id
    )
    assert await world.qr_displays.get(display.id, for_update=True) is display
    assert await world.qr_displays.get(-1) is None
    assert list(await world.qr_displays.due(for_update=True)) == [display]
