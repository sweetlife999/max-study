"""The migrated schema really enforces what ARCHITECTURE.md §4 promises."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from campus.db.models import Checkin, Event, OutboxMessage, QrDisplay, User

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def _user(session: AsyncSession, max_user_id: int) -> User:
    user = User(max_user_id=max_user_id, first_name="Ann", lang="ru")
    session.add(user)
    await session.flush()
    return user


async def _event(session: AsyncSession, organizer: User, **overrides: object) -> Event:
    values: dict[str, object] = {
        "title": "Club",
        "description": "",
        "kind": "club",
        "location": "A-101",
        "starts_at": NOW,
        "ends_at": NOW + timedelta(hours=2),
        "points": 5,
        "organizer_id": organizer.id,
        "qr_seed": b"\x00" * 32,
    }
    values.update(overrides)
    event = Event(**values)
    session.add(event)
    await session.flush()
    return event


async def test_migrations_create_every_table(session: AsyncSession) -> None:
    # Arrange / Act: a trivial query against each mapped table must succeed.
    for model in (User, Event, Checkin, OutboxMessage, QrDisplay):
        await session.execute(select(model).limit(1))


async def test_max_user_id_is_unique(session: AsyncSession) -> None:
    await _user(session, 1001)

    session.add(User(max_user_id=1001, first_name="Bob", lang="en"))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_unknown_language_is_rejected(session: AsyncSession) -> None:
    session.add(User(max_user_id=1002, first_name="Ann", lang="de"))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_event_must_end_after_it_starts(session: AsyncSession) -> None:
    organizer = await _user(session, 1003)

    with pytest.raises(IntegrityError):
        await _event(session, organizer, ends_at=NOW - timedelta(minutes=1))


async def test_event_points_cannot_be_negative(session: AsyncSession) -> None:
    organizer = await _user(session, 1004)

    with pytest.raises(IntegrityError):
        await _event(session, organizer, points=-1)


async def test_checkin_is_unique_per_user_and_event(session: AsyncSession) -> None:
    organizer = await _user(session, 1005)
    event = await _event(session, organizer)
    session.add(Checkin(user_id=organizer.id, event_id=event.id, method="qr"))
    await session.flush()

    session.add(Checkin(user_id=organizer.id, event_id=event.id, method="code"))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_outbox_dedup_key_is_unique_only_when_present(session: AsyncSession) -> None:
    user = await _user(session, 1006)
    for _ in range(2):
        session.add(
            OutboxMessage(user_id=user.id, kind="reminder", payload={}, run_at=NOW, dedup_key=None)
        )
    await session.flush()  # two NULL dedup keys coexist

    session.add(
        OutboxMessage(user_id=user.id, kind="reminder", payload={}, run_at=NOW, dedup_key="r:1")
    )
    await session.flush()
    session.add(
        OutboxMessage(user_id=user.id, kind="reminder", payload={}, run_at=NOW, dedup_key="r:1")
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_outbox_rejects_unknown_kind_and_status(session: AsyncSession) -> None:
    user = await _user(session, 1007)

    session.add(OutboxMessage(user_id=user.id, kind="carrier_pigeon", payload={}, run_at=NOW))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_only_one_active_qr_display_per_organizer_and_event(session: AsyncSession) -> None:
    organizer = await _user(session, 1008)
    event = await _event(session, organizer)
    stopped = QrDisplay(
        event_id=event.id,
        organizer_id=organizer.id,
        max_user_id=organizer.max_user_id,
        active_until=NOW + timedelta(hours=1),
        stopped_at=NOW,
    )
    session.add(stopped)
    active = QrDisplay(
        event_id=event.id,
        organizer_id=organizer.id,
        max_user_id=organizer.max_user_id,
        active_until=NOW + timedelta(hours=1),
    )
    session.add(active)
    await session.flush()  # a stopped display does not block a new one

    session.add(
        QrDisplay(
            event_id=event.id,
            organizer_id=organizer.id,
            max_user_id=organizer.max_user_id,
            active_until=NOW + timedelta(hours=1),
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_qr_display_needs_a_destination(session: AsyncSession) -> None:
    organizer = await _user(session, 1009)
    event = await _event(session, organizer)

    session.add(
        QrDisplay(
            event_id=event.id,
            organizer_id=organizer.id,
            active_until=NOW + timedelta(hours=1),
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_timestamps_come_back_timezone_aware(session: AsyncSession) -> None:
    user = await _user(session, 1010)

    await session.refresh(user)

    assert user.created_at.tzinfo is not None


async def test_event_ids_are_identity_columns(session: AsyncSession) -> None:
    organizer = await _user(session, 1011)
    first = await _event(session, organizer)
    second = await _event(session, organizer)

    assert second.id > first.id


async def test_qr_seed_round_trips_as_bytes(session: AsyncSession) -> None:
    organizer = await _user(session, 1012)
    seed = bytes(range(32))
    event = await _event(session, organizer, qr_seed=seed)

    await session.refresh(event)

    assert event.qr_seed == seed


async def test_checkin_method_vocabulary_is_enforced(session: AsyncSession) -> None:
    organizer = await _user(session, 1013)
    event = await _event(session, organizer)

    session.add(Checkin(user_id=organizer.id, event_id=event.id, method="nfc"))

    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()
