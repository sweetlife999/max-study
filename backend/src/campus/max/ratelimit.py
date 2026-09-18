"""Client-side rate limiting for MAX (ARCHITECTURE.md §8).

Two independent limits apply to every outgoing call:

* a global token bucket of at most 25 requests per second — MAX documents 30 rps on
  platform-api2.max.ru, so this leaves headroom;
* at most one message per second per chat — MAX allows two, so again this is the safer bound.

The monotonic clock and the sleep coroutine are injected, which makes both limiters testable
without real waiting.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]

DEFAULT_GLOBAL_RPS = 25.0
DEFAULT_GLOBAL_BURST = 25.0
DEFAULT_PER_CHAT_INTERVAL_SECONDS = 1.0

# Token counts are floats, so a bucket that should hold exactly one token can hold
# 0.9999999999999998. Without this tolerance the wait below rounds to zero and the loop
# spins forever instead of making progress.
_TOKEN_EPSILON = 1e-9
_MIN_SLEEP_SECONDS = 1e-4


class TokenBucket:
    """Classic token bucket: ``rate`` tokens refill per second, up to ``burst``."""

    def __init__(
        self,
        rate: float = DEFAULT_GLOBAL_RPS,
        burst: float = DEFAULT_GLOBAL_BURST,
        *,
        monotonic: MonotonicClock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            msg = "rate must be positive"
            raise ValueError(msg)
        if burst <= 0:
            msg = "burst must be positive"
            raise ValueError(msg)
        self._rate = rate
        self._burst = burst
        self._monotonic = monotonic
        self._sleep = sleep
        self._tokens = burst
        self._updated_at = monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = max(0.0, now - self._updated_at)
        self._updated_at = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)

    async def acquire(self, tokens: float = 1.0) -> None:
        if tokens > self._burst:
            msg = f"cannot acquire {tokens} tokens from a bucket of {self._burst}"
            raise ValueError(msg)
        async with self._lock:
            while True:
                self._refill()
                if self._tokens + _TOKEN_EPSILON >= tokens:
                    self._tokens = max(0.0, self._tokens - tokens)
                    return
                missing = tokens - self._tokens
                await self._sleep(max(missing / self._rate, _MIN_SLEEP_SECONDS))


class PerChatLimiter:
    """Keeps at least ``interval`` seconds between two sends to the same chat."""

    def __init__(
        self,
        interval: float = DEFAULT_PER_CHAT_INTERVAL_SECONDS,
        *,
        monotonic: MonotonicClock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        if interval < 0:
            msg = "interval must not be negative"
            raise ValueError(msg)
        self._interval = interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_allowed: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, chat_key: str | None) -> None:
        if chat_key is None or self._interval == 0:
            return
        async with self._lock:
            now = self._monotonic()
            earliest = self._next_allowed.get(chat_key, now)
            wait = earliest - now
            if wait > 0:
                await self._sleep(wait)
                now = earliest
            self._next_allowed[chat_key] = now + self._interval
            self._forget_stale(now)

    def _forget_stale(self, now: float) -> None:
        """Drop chats that are free again, so the map cannot grow without bound."""
        if len(self._next_allowed) <= _STALE_THRESHOLD:
            return
        self._next_allowed = {key: at for key, at in self._next_allowed.items() if at > now}


_STALE_THRESHOLD = 1024
