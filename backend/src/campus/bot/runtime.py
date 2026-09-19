"""Own the bot lifetime, singleton lock, polling and background tasks."""

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus.bot.dispatcher import Dispatcher
from campus.bot.polling import poll, retry_delay
from campus.bot.workers.outbox import OutboxWorker
from campus.bot.workers.qr import QrWorker
from campus.config import Settings, load_university_config
from campus.db.runtime import bot_lock
from campus.db.session import create_engine, create_session_factory, session_scope
from campus.domain.clock import SystemClock
from campus.domain.context import DomainConfig
from campus.logs import configure_logging
from campus.max.client import MaxAuthError
from campus.max.http import HttpMaxClient

logger = logging.getLogger(__name__)


async def work(
    sessions: async_sessionmaker[AsyncSession], tick: Callable[[AsyncSession], Awaitable[None]]
) -> None:
    while True:
        delay = 1.0
        try:
            async with session_scope(sessions) as session:
                await tick(session)
        except MaxAuthError:
            raise
        except Exception as exc:
            logger.warning("worker_failed", extra={"error_type": type(exc).__name__})
            delay = retry_delay(exc)
        await asyncio.sleep(delay)


async def heartbeat(probe: Callable[[], Awaitable[None]]) -> None:
    """Fail the process when the connection holding singleton ownership is lost."""
    while True:
        await probe()
        await asyncio.sleep(1.0)


async def supervise(jobs: list[Awaitable[None]], stop: asyncio.Event) -> None:
    tasks = [asyncio.ensure_future(job) for job in jobs]

    async def wait_stop() -> None:
        await stop.wait()

    stopped = asyncio.create_task(wait_stop())
    try:
        done, _ = await asyncio.wait([*tasks, stopped], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not stopped:
                task.result()
    finally:
        for task in [*tasks, stopped]:
            task.cancel()
        await asyncio.gather(*tasks, stopped, return_exceptions=True)


async def run(settings: Settings | None = None, *, stop: asyncio.Event | None = None) -> None:
    settings = settings or Settings()  # pyright: ignore[reportCallIssue]
    university = load_university_config(settings.university_config_path)
    token = settings.require_bot_token()
    settings.require_bot_username()
    configure_logging(settings.log_level)
    config = DomainConfig.from_settings(settings, university)
    clock = SystemClock()
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with bot_lock(engine) as probe:
            client = HttpMaxClient(token, base_url=settings.max_api_base_url, max_attempts=1)
            try:
                await client.get_me()
                dispatcher = Dispatcher(config, client, sessions, clock=clock)
                await supervise(
                    [
                        heartbeat(probe),
                        poll(client, sessions, config, clock, dispatcher.dispatch),
                        work(sessions, OutboxWorker(client, config, clock).tick),
                        work(sessions, QrWorker(client, config, clock).tick),
                    ],
                    stop,
                )
            finally:
                await client.aclose()
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        await engine.dispose()
