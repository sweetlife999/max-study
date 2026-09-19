"""Database probes and process-level ownership, kept outside transport modules."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

BOT_LOCK_KEY = 0x43414D505553


class BotAlreadyRunningError(RuntimeError):
    """Another process owns this database's bot polling stream."""


class BotLockLostError(RuntimeError):
    """The dedicated connection no longer owns the singleton lock."""


async def database_healthy(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text("SELECT 1")) == 1
    except (SQLAlchemyError, OSError):
        return False


@asynccontextmanager
async def bot_lock(engine: AsyncEngine) -> AsyncIterator[Callable[[], Awaitable[None]]]:
    """Hold a session advisory lock on a dedicated connection for the bot lifetime.

    Explicitly unlock before returning the connection to the pool: transaction rollback does
    not release a session advisory lock. A broken connection releases it server-side.
    """
    async with engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": BOT_LOCK_KEY}
        )
        await connection.commit()
        if not acquired:
            raise BotAlreadyRunningError("another bot process is already running")

        async def probe() -> None:
            if connection.invalidated or connection.closed:
                raise BotLockLostError("bot lock connection was lost")
            owns_lock = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = pg_backend_pid() AND classid = :hi AND objid = :lo "
                    "AND objsubid = 1 AND granted)"
                ),
                {"hi": BOT_LOCK_KEY >> 32, "lo": BOT_LOCK_KEY & 0xFFFFFFFF},
            )
            await connection.commit()
            if not owns_lock:
                raise BotLockLostError("bot singleton lock was lost")

        try:
            yield probe
        finally:
            if not connection.invalidated and not connection.closed:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": BOT_LOCK_KEY}
                )
                await connection.commit()
