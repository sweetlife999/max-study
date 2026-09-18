"""Async engine and session factory.

Both entrypoints build one engine per process and hand sessions to domain services; services
never create their own engine so that tests can run everything inside one transaction.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 5
DEFAULT_POOL_TIMEOUT_SECONDS = 10.0


def create_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
) -> AsyncEngine:
    return create_async_engine(
        database_url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=DEFAULT_POOL_TIMEOUT_SECONDS,
        pool_pre_ping=True,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
    except BaseException:
        await session.rollback()
        raise
    else:
        await session.commit()
    finally:
        await session.close()


@asynccontextmanager
async def domain_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One unit of work for a request answered by the domain.

    A :class:`~campus.domain.errors.DomainError` is a *normal* outcome — the api turns it into a
    4xx answer — and the domain deliberately records state before raising one: the check-in
    attempt ledger that ARCHITECTURE.md §5 rate-limits against. Rolling that back would erase
    every failed guess and leave the limit unable to fire, so such a transaction is committed
    and the error re-raised. Services keep this safe by validating before they mutate, so a
    committed error path holds only what the domain meant to keep.

    Any other exception is a fault: everything rolls back.

    This is the scope both entrypoints should wrap a request in; ``session_scope`` stays right
    for background work that has no user waiting for an answer.
    """
    from campus.domain.errors import DomainError  # noqa: PLC0415 - avoids a db -> domain cycle

    session = factory()
    try:
        yield session
    except DomainError:
        await session.commit()
        raise
    except BaseException:
        await session.rollback()
        raise
    else:
        await session.commit()
    finally:
        await session.close()
