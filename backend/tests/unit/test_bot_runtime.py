import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from campus.bot.polling import POLL_RETRY_MAX_DELAY, process_page, retry_delay
from campus.max.client import MaxApiError, MaxRateLimitError, MaxTransportError
from campus.max.types import UnknownUpdate, UpdatesPage


async def test_batch_isolates_handler_error_and_then_checkpoints() -> None:
    calls: list[str] = []

    async def dispatch(update: object) -> None:
        calls.append("dispatch")
        if len(calls) == 1:
            raise ValueError("private")

    async def save(marker: int | None) -> None:
        assert marker == 123
        calls.append("checkpoint")

    await process_page(
        UpdatesPage(updates=(UnknownUpdate(), UnknownUpdate()), marker=123), dispatch, save
    )
    assert calls == ["dispatch", "dispatch", "checkpoint"]


async def test_update_failure_logs_safe_max_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    async def dispatch(update: object) -> None:
        raise MaxApiError(400, "private", code="invalid_callback", method="POST /answers")

    await process_page(UpdatesPage(updates=(UnknownUpdate(),), marker=None), dispatch, AsyncMock())

    record = next(record for record in caplog.records if record.message == "update_failed")
    assert record.__dict__["update_type"] == "unknown"
    assert record.__dict__["http_status"] == 400
    assert record.__dict__["max_method"] == "POST /answers"
    assert record.__dict__["max_error_code"] == "invalid_callback"
    assert "private" not in record.__dict__.values()


async def test_cancelled_batch_does_not_checkpoint() -> None:
    dispatch = AsyncMock(side_effect=asyncio.CancelledError)
    save = AsyncMock()
    import pytest

    with pytest.raises(asyncio.CancelledError):
        await process_page(UpdatesPage(updates=(UnknownUpdate(),), marker=123), dispatch, save)
    save.assert_not_called()


async def test_page_without_a_marker_keeps_the_stored_one() -> None:
    # An idle long poll answers with neither updates nor a marker. Checkpointing that would
    # delete the stored marker and make MAX replay its whole retention on the next call.
    dispatch = AsyncMock()
    save = AsyncMock()

    await process_page(UpdatesPage(updates=(), marker=None), dispatch, save)

    dispatch.assert_not_called()
    save.assert_not_called()


async def test_handled_batch_without_a_marker_still_does_not_checkpoint() -> None:
    dispatch = AsyncMock()
    save = AsyncMock()

    await process_page(UpdatesPage(updates=(UnknownUpdate(),), marker=None), dispatch, save)

    dispatch.assert_awaited_once()
    save.assert_not_called()


def test_backoff_honors_retry_after_but_stays_bounded() -> None:
    assert retry_delay(MaxRateLimitError(retry_after=30)) == 30
    assert retry_delay(MaxTransportError()) == 5
    # A long Retry-After must not stop the bot for that long: §8 makes polling its only ear.
    assert retry_delay(MaxRateLimitError(retry_after=600)) == POLL_RETRY_MAX_DELAY
    assert retry_delay(MaxRateLimitError(retry_after=3600)) == POLL_RETRY_MAX_DELAY


async def test_supervisor_cancels_workers_on_shutdown() -> None:
    from campus.bot.runtime import supervise

    started = asyncio.Event()
    stopped = asyncio.Event()
    cleaned = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(supervise([worker()], stopped))
    await started.wait()
    stopped.set()
    await asyncio.wait_for(task, 1)
    assert cleaned.is_set()


async def test_supervisor_propagates_failure_and_cleans_other_worker() -> None:
    import pytest

    from campus.bot.runtime import supervise

    cleaned = asyncio.Event()

    async def bad() -> None:
        await asyncio.sleep(0)
        raise ValueError("failed")

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    with pytest.raises(ValueError, match="failed"):
        await supervise([worker(), bad()], asyncio.Event())
    assert cleaned.is_set()


async def test_poll_reloads_checkpoint_after_recoverable_failure(monkeypatch: object) -> None:
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager
    from typing import Any

    import pytest

    from campus.bot import polling
    from campus.max.fake import FakeMaxClient

    marker: int | None = 10
    calls: list[int | None] = []

    @asynccontextmanager
    async def scope(factory: object) -> AsyncIterator[object]:
        yield object()

    class Store:
        def __init__(self, *args: object) -> None:
            pass

        async def updates_marker(self) -> int | None:
            return marker

        async def set_updates_marker(self, value: int | None) -> None:
            nonlocal marker
            marker = value

    class Client(FakeMaxClient):
        async def get_updates(self, **kwargs: Any) -> UpdatesPage:
            calls.append(kwargs["marker"])
            if len(calls) == 1:
                raise MaxTransportError("offline")
            if len(calls) == 2:
                return UpdatesPage(updates=(UnknownUpdate(),), marker=20)
            raise asyncio.CancelledError

    patch = monkeypatch
    patch.setattr(polling, "session_scope", scope)  # type: ignore[attr-defined]
    patch.setattr(polling, "KeyValueService", Store)  # type: ignore[attr-defined]
    patch.setattr(polling.asyncio, "sleep", AsyncMock())  # type: ignore[attr-defined]
    with pytest.raises(asyncio.CancelledError):
        await polling.poll(Client(), None, None, None, AsyncMock())  # type: ignore[arg-type]
    assert calls == [10, 10, 20]


async def test_runtime_owns_lock_client_and_engine(
    monkeypatch: object, example_config_path: Path
) -> None:
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    from campus.bot import runtime
    from campus.config import Settings
    from campus.max.fake import FakeMaxClient

    client = FakeMaxClient()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    held = False

    @asynccontextmanager
    async def lock(value: object) -> AsyncIterator[AsyncMock]:
        nonlocal held
        held = True
        try:
            yield AsyncMock()
        finally:
            held = False

    async def supervise(jobs: list[object], stop: object) -> None:
        assert held
        assert len(jobs) == 4
        for job in jobs:
            job.close()  # type: ignore[attr-defined]

    patch = monkeypatch
    patch.setattr(runtime, "create_engine", lambda _: engine)  # type: ignore[attr-defined]
    patch.setattr(runtime, "create_session_factory", lambda _: MagicMock())  # type: ignore[attr-defined]
    patch.setattr(runtime, "HttpMaxClient", lambda *args, **kwargs: client)  # type: ignore[attr-defined]
    patch.setattr(runtime, "bot_lock", lock)  # type: ignore[attr-defined]
    patch.setattr(runtime, "supervise", supervise)  # type: ignore[attr-defined]
    patch.setattr(runtime, "configure_logging", lambda _: None)  # type: ignore[attr-defined]
    settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        university_config_path=example_config_path,
        max_bot_token=SecretStr("test-token"),
        max_bot_username="campus_bot",
    )  # type: ignore[arg-type]
    await runtime.run(settings, stop=asyncio.Event())
    assert not held
    assert client.closed
    assert client.calls[0].method == "get_me"
    engine.dispose.assert_awaited_once()


async def test_heartbeat_failure_stops_other_jobs() -> None:
    import pytest

    from campus.bot.runtime import heartbeat, supervise

    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def probe() -> None:
        await started.wait()
        raise RuntimeError("lock connection lost")

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    with pytest.raises(RuntimeError, match="lock connection lost"):
        await supervise([worker(), heartbeat(probe)], asyncio.Event())
    assert cleaned.is_set()


async def test_heartbeat_rechecks_every_second(monkeypatch: object) -> None:
    import pytest

    from campus.bot import runtime

    probe = AsyncMock()
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(runtime.asyncio, "sleep", sleep)  # type: ignore[attr-defined]
    with pytest.raises(asyncio.CancelledError):
        await runtime.heartbeat(probe)
    assert probe.await_count == 2
    assert [call.args for call in sleep.await_args_list] == [(1.0,), (1.0,)]
