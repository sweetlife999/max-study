"""The two units of work in :mod:`campus.db.session`.

These commit for real, so each test cleans up after itself instead of using the rolled-back
``session`` fixture.
"""

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from campus.db.models import CheckinAttempt, User
from campus.db.session import create_session_factory, domain_scope, session_scope
from campus.domain.errors import DomainError, InvalidCodeError
from tests.integration.factories import next_max_user_id

MAX_USER_IDS: list[int] = []


def _new_user() -> User:
    max_user_id = next_max_user_id()
    MAX_USER_IDS.append(max_user_id)
    return User(max_user_id=max_user_id, first_name="Аня", lang="ru")


@pytest.fixture
async def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture(autouse=True)
async def _cleanup(factory: async_sessionmaker[AsyncSession]):
    yield
    async with factory() as session:
        await session.execute(delete(User).where(User.max_user_id.in_(MAX_USER_IDS)))
        await session.commit()
    MAX_USER_IDS.clear()


async def _exists(factory: async_sessionmaker[AsyncSession], max_user_id: int) -> bool:
    async with factory() as session:
        found = await session.execute(select(User.id).where(User.max_user_id == max_user_id))
        return found.scalar_one_or_none() is not None


# --- session_scope ----------------------------------------------------------------------------


async def test_session_scope_commits_a_successful_unit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    user = _new_user()

    async with session_scope(factory) as session:
        session.add(user)

    assert await _exists(factory, user.max_user_id) is True


async def test_session_scope_rolls_a_domain_error_back(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    user = _new_user()

    with pytest.raises(InvalidCodeError):
        async with session_scope(factory) as session:
            session.add(user)
            await session.flush()
            raise InvalidCodeError("nope")

    assert await _exists(factory, user.max_user_id) is False


# --- domain_scope -----------------------------------------------------------------------------


async def test_domain_scope_commits_a_successful_unit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    user = _new_user()

    async with domain_scope(factory) as session:
        session.add(user)

    assert await _exists(factory, user.max_user_id) is True


async def test_domain_scope_keeps_what_the_domain_recorded_before_raising(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The §5 rate limit only works if a rejected attempt outlives the error it raised."""
    user = _new_user()
    async with domain_scope(factory) as session:
        session.add(user)

    with pytest.raises(InvalidCodeError):
        async with domain_scope(factory) as session:
            session.add(CheckinAttempt(user_id=user.id, success=False))
            await session.flush()
            raise InvalidCodeError("wrong code")

    async with factory() as session:
        attempts = await session.execute(
            select(func.count())
            .select_from(CheckinAttempt)
            .where(CheckinAttempt.user_id == user.id)
        )
        assert int(attempts.scalar_one()) == 1


async def test_domain_scope_rolls_a_fault_back(factory: async_sessionmaker[AsyncSession]) -> None:
    user = _new_user()

    with pytest.raises(RuntimeError):
        async with domain_scope(factory) as session:
            session.add(user)
            await session.flush()
            msg = "the database went away"
            raise RuntimeError(msg)

    assert await _exists(factory, user.max_user_id) is False


async def test_a_domain_error_is_still_raised_to_the_caller(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(DomainError):
        async with domain_scope(factory):
            raise InvalidCodeError("nope")
