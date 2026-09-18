"""A real PostgreSQL for the integration tests (ARCHITECTURE.md §11).

`TEST_DATABASE_URL` (or `DATABASE_URL`) points at an existing database — that is how CI uses its
service container. Without one, a disposable container is started via testcontainers.

The schema is created by running the real ``alembic upgrade head``, so a model that drifts from the
migrations fails here rather than in production. Each test runs inside a transaction that is rolled
back afterwards, which keeps tests independent without re-migrating between them.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus.db.session import create_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_ASYNC_DRIVER = "postgresql+asyncpg://"


def _async_url(url: str) -> str:
    for scheme in (_ASYNC_DRIVER, "postgresql://", "postgres://"):
        if url.startswith(scheme):
            return _ASYNC_DRIVER + url.removeprefix(scheme)
    msg = f"not a PostgreSQL URL: {url!r}"
    raise ValueError(msg)


def _start_container() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:  # pragma: no cover - only when dev extras are missing
        pytest.skip("set TEST_DATABASE_URL or install testcontainers to run integration tests")
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    configured = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if configured:
        yield _async_url(configured)
        return
    yield from _start_container()


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    """Run the real migrations once per session; a failure here is a migration bug."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-x", f"database_url={database_url}", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return database_url


@pytest.fixture(scope="session")
async def engine(migrated_database_url: str) -> AsyncIterator[Any]:
    created = create_engine(migrated_database_url)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def session(engine: Any) -> AsyncIterator[AsyncSession]:
    """A session whose work is always rolled back, so tests never see each other's rows."""
    connection = await engine.connect()
    transaction = await connection.begin()
    # join_transaction_mode: a service's commit() releases a savepoint, so the outer
    # transaction below still rolls the whole test back.
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    made = factory()
    try:
        yield made
    finally:
        await made.close()
        await transaction.rollback()
        await connection.close()
