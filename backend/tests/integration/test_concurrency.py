"""What happens when two requests arrive at once (ARCHITECTURE.md §5).

The rest of the integration suite runs each test inside one transaction that is rolled back, so
it cannot see a race at all: every statement is the only statement in flight. These tests do the
opposite — real sessions on real connections, committed, then deleted again — because the two
guarantees of §5 that matter are both about parallelism:

* "Повторная отметка — идемпотентна": two scans of the same QR must leave one row, never two;
* "не более 10 попыток за 10 минут на пользователя": a guesser who opens N connections must not
  get N times the attempts.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from campus.config import UniversityConfig
from campus.db.session import create_session_factory, domain_scope
from campus.domain.clock import FixedClock
from campus.domain.context import CHECKIN_RATE_LIMIT_ATTEMPTS
from campus.domain.errors import DomainError, TooManyAttemptsError
from tests.integration.factories import NOW, make_world

# As many parallel scans as §5 lets one user make in ten minutes: any more and the rate limit,
# not the idempotency, would be what the first test measured.
PARALLEL = CHECKIN_RATE_LIMIT_ATTEMPTS
UNKNOWN_CODE = "000000"


@pytest.fixture
async def committed(
    engine: Any, university_config: UniversityConfig
) -> AsyncIterator[tuple[async_sessionmaker[Any], UniversityConfig, FixedClock, int, int, str]]:
    """A student and a running event, really committed, with every row removed afterwards."""
    factory = create_session_factory(engine)
    clock = FixedClock(NOW)
    async with domain_scope(factory) as session:
        world = make_world(session, university_config, clock=clock)
        organizer = await world.organizer()
        student = await world.user()
        event = await world.open_event_now(organizer=organizer)
        ids = (student.id, event.id, world.code_for(event))
        owners = [organizer.id, student.id]
    try:
        yield (factory, university_config, clock, *ids)
    finally:
        async with domain_scope(factory) as session:
            await session.execute(text("delete from events where id = :e"), {"e": ids[1]})
            await session.execute(text("delete from users where id = any(:ids)"), {"ids": owners})


async def _check_in(
    factory: async_sessionmaker[Any],
    university: UniversityConfig,
    clock: FixedClock,
    user_id: int,
    code: str,
) -> str:
    """One whole request, on its own session, exactly as the api will run it."""
    async with domain_scope(factory) as session:
        world = make_world(session, university, clock=clock)
        user = await world.users.require(user_id)
        try:
            outcome = await world.checkins.check_in(user=user, code=code, method="qr")
        except TooManyAttemptsError:
            return "rate_limited"
        except DomainError as exc:
            return type(exc).__name__
        return "already" if outcome.already else "checked_in"


async def test_parallel_scans_of_the_same_qr_leave_exactly_one_check_in(
    committed: tuple[Any, ...],
) -> None:
    factory, university, clock, user_id, event_id, code = committed

    results = await asyncio.gather(
        *(_check_in(factory, university, clock, user_id, code) for _ in range(PARALLEL))
    )

    assert results.count("checked_in") == 1
    assert results.count("already") == PARALLEL - 1
    async with domain_scope(factory) as session:
        rows = await session.execute(
            text("select count(*) from checkins where user_id = :u and event_id = :e"),
            {"u": user_id, "e": event_id},
        )
        assert rows.scalar_one() == 1


async def test_a_guesser_cannot_buy_extra_attempts_with_extra_connections(
    committed: tuple[Any, ...],
) -> None:
    """§5 caps the attempts per user, not per connection."""
    factory, university, clock, user_id, _event_id, _code = committed
    fan_out = CHECKIN_RATE_LIMIT_ATTEMPTS * 4

    results = await asyncio.gather(
        *(_check_in(factory, university, clock, user_id, UNKNOWN_CODE) for _ in range(fan_out))
    )

    assert results.count("rate_limited") == fan_out - CHECKIN_RATE_LIMIT_ATTEMPTS
    async with domain_scope(factory) as session:
        rows = await session.execute(
            text("select count(*) from checkin_attempts where user_id = :u"), {"u": user_id}
        )
        assert rows.scalar_one() == CHECKIN_RATE_LIMIT_ATTEMPTS
