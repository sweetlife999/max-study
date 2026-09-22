"""Client-side rate limiting, driven by a fake clock so nothing actually waits."""

import asyncio

import pytest

from campus.max.ratelimit import (
    DEFAULT_GLOBAL_BURST,
    DEFAULT_GLOBAL_RPS,
    DEFAULT_PER_CHAT_INTERVAL_SECONDS,
    PerChatLimiter,
    TokenBucket,
)

# dev.max.ru/docs-api: 30 requests per second per domain, and "не более двух сообщений в секунду
# в один чат". ARCHITECTURE.md §8 spends that budget with headroom: ≤ 25 rps and ≤ 1 msg/s per
# chat. These are the numbers the defaults must stay inside.
MAX_DOCUMENTED_RPS = 25.0
MAX_DOCUMENTED_PER_CHAT_INTERVAL = 1.0


def test_the_defaults_stay_inside_the_limits_of_the_contract() -> None:
    assert DEFAULT_GLOBAL_RPS <= MAX_DOCUMENTED_RPS
    # A burst larger than the rate would let a whole second's budget go out at once, and then
    # a second one before the first has refilled.
    assert DEFAULT_GLOBAL_BURST <= DEFAULT_GLOBAL_RPS
    assert DEFAULT_PER_CHAT_INTERVAL_SECONDS >= MAX_DOCUMENTED_PER_CHAT_INTERVAL


class FakeTime:
    """A monotonic clock that only moves when a sleep is awaited."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_burst_is_served_without_waiting() -> None:
    time = FakeTime()
    bucket = TokenBucket(rate=25.0, burst=25.0, monotonic=time.monotonic, sleep=time.sleep)

    for _ in range(25):
        await bucket.acquire()

    assert time.slept == []


async def test_exhausted_bucket_waits_for_a_refill() -> None:
    time = FakeTime()
    bucket = TokenBucket(rate=25.0, burst=25.0, monotonic=time.monotonic, sleep=time.sleep)
    for _ in range(25):
        await bucket.acquire()

    await bucket.acquire()

    assert time.slept == [pytest.approx(1 / 25)]


async def test_sustained_rate_does_not_exceed_the_limit() -> None:
    time = FakeTime()
    bucket = TokenBucket(rate=10.0, burst=1.0, monotonic=time.monotonic, sleep=time.sleep)

    for _ in range(11):
        await bucket.acquire()

    # 1 free token, then 10 refills of 0.1s each.
    assert time.now == pytest.approx(1.0)


async def test_bucket_never_holds_more_than_its_burst() -> None:
    time = FakeTime()
    bucket = TokenBucket(rate=10.0, burst=2.0, monotonic=time.monotonic, sleep=time.sleep)
    time.now = 1000.0  # a long idle period

    for _ in range(2):
        await bucket.acquire()
    await bucket.acquire()

    assert time.slept == [pytest.approx(0.1)]


async def test_non_positive_rate_is_rejected() -> None:
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=0.0)


async def test_non_positive_burst_is_rejected() -> None:
    with pytest.raises(ValueError, match="burst"):
        TokenBucket(burst=0.0)


async def test_asking_for_more_than_the_burst_is_rejected() -> None:
    bucket = TokenBucket(rate=1.0, burst=1.0)

    with pytest.raises(ValueError, match="cannot acquire"):
        await bucket.acquire(2.0)


async def test_first_message_to_a_chat_is_immediate() -> None:
    time = FakeTime()
    limiter = PerChatLimiter(1.0, monotonic=time.monotonic, sleep=time.sleep)

    await limiter.acquire("chat:1")

    assert time.slept == []


async def test_second_message_to_the_same_chat_waits_a_second() -> None:
    time = FakeTime()
    limiter = PerChatLimiter(1.0, monotonic=time.monotonic, sleep=time.sleep)
    await limiter.acquire("chat:1")

    await limiter.acquire("chat:1")

    assert time.slept == [pytest.approx(1.0)]


async def test_different_chats_do_not_block_each_other() -> None:
    time = FakeTime()
    limiter = PerChatLimiter(1.0, monotonic=time.monotonic, sleep=time.sleep)

    await limiter.acquire("chat:1")
    await limiter.acquire("chat:2")

    assert time.slept == []


async def test_different_chats_can_acquire_while_one_chat_is_waiting() -> None:
    time = FakeTime()
    sleeper_started = asyncio.Event()
    release_sleeper = asyncio.Event()

    async def controlled_sleep(seconds: float) -> None:
        assert seconds == pytest.approx(1.0)
        sleeper_started.set()
        await release_sleeper.wait()
        time.now += seconds

    limiter = PerChatLimiter(1.0, monotonic=time.monotonic, sleep=controlled_sleep)
    await limiter.acquire("chat:1")

    waiting = asyncio.create_task(limiter.acquire("chat:1"))
    await sleeper_started.wait()

    other_chat = asyncio.create_task(limiter.acquire("chat:2"))
    await asyncio.wait_for(other_chat, timeout=0.1)

    release_sleeper.set()
    await waiting


async def test_a_chat_is_free_again_after_the_interval() -> None:
    time = FakeTime()
    limiter = PerChatLimiter(1.0, monotonic=time.monotonic, sleep=time.sleep)
    await limiter.acquire("chat:1")
    time.now += 5.0

    await limiter.acquire("chat:1")

    assert time.slept == []


async def test_calls_without_a_chat_key_are_not_limited() -> None:
    time = FakeTime()
    limiter = PerChatLimiter(1.0, monotonic=time.monotonic, sleep=time.sleep)

    for _ in range(5):
        await limiter.acquire(None)

    assert time.slept == []


async def test_negative_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="interval"):
        PerChatLimiter(-1.0)
