"""Long polling with durable batch checkpoints and isolated update failures."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus.db.session import session_scope
from campus.domain.clock import Clock
from campus.domain.context import DomainConfig
from campus.domain.services import KeyValueService
from campus.max.client import MaxAuthError, MaxClient, MaxRateLimitError
from campus.max.types import Update, UpdatesPage

logger = logging.getLogger(__name__)
Dispatch = Callable[[Update], Awaitable[None]]
Checkpoint = Callable[[int | None], Awaitable[None]]


def retry_delay(error: Exception) -> float:
    if isinstance(error, MaxRateLimitError) and error.retry_after is not None:
        return max(0.0, error.retry_after)
    return 5.0


async def process_page(page: UpdatesPage, dispatch: Dispatch, checkpoint: Checkpoint) -> None:
    for update in page.updates:
        try:
            await dispatch(update)
        except Exception as exc:
            logger.warning("update_failed", extra={"error_type": type(exc).__name__})
    await checkpoint(page.marker)


async def poll(
    client: MaxClient,
    sessions: async_sessionmaker[AsyncSession],
    config: DomainConfig,
    clock: Clock,
    dispatch: Dispatch,
) -> None:
    async def checkpoint(marker: int | None) -> None:
        async with session_scope(sessions) as session:
            await KeyValueService(session, config, clock).set_updates_marker(marker)

    while True:
        try:
            async with session_scope(sessions) as session:
                marker = await KeyValueService(session, config, clock).updates_marker()
            page = await client.get_updates(marker=marker)
            await process_page(page, dispatch, checkpoint)
            if not page.updates:
                await asyncio.sleep(0.1)
        except MaxAuthError:
            raise
        except Exception as exc:
            logger.warning("poll_failed", extra={"error_type": type(exc).__name__})
            await asyncio.sleep(retry_delay(exc))
