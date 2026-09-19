"""The outbox (ARCHITECTURE.md §2, §8).

The api never calls MAX; it writes a row here and the bot's worker drains it. Claiming uses
``FOR UPDATE SKIP LOCKED`` so several workers could run without sending anything twice, even
though the contract runs a single bot replica.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import text as sql_text

from campus.db.models import OUTBOX_KINDS, OutboxMessage
from campus.domain.context import (
    OUTBOX_MAX_ATTEMPTS,
    OUTBOX_RETRY_BASE_DELAY,
    OUTBOX_RETRY_MAX_DELAY,
)
from campus.domain.errors import ValidationFailedError
from campus.domain.services.base import Service, require_aware

DEFAULT_CLAIM_LIMIT = 20
_DEDUP_INDEX_WHERE = sql_text("dedup_key is not null")


def retry_delay(attempts: int) -> timedelta:
    """Exponential backoff, capped: 30s, 1m, 2m, 4m, ... up to an hour.

    The exponent is clamped before the multiplication, not after: ``timedelta * 2**attempts``
    raises OverflowError long before the cap could be applied, and a row that somehow carried a
    large ``attempts`` would then take the whole worker down instead of being retried late.
    """
    exponent = min(max(0, attempts - 1), _MAX_BACKOFF_EXPONENT)
    return min(OUTBOX_RETRY_BASE_DELAY * (2**exponent), OUTBOX_RETRY_MAX_DELAY)


# Past this the cap has certainly been reached: 30s << an hour << 30s * 2**32.
_MAX_BACKOFF_EXPONENT = 32


@dataclass(frozen=True, slots=True)
class OutboxService(Service):
    async def enqueue(
        self,
        *,
        user_id: int,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        run_at: datetime | None = None,
        dedup_key: str | None = None,
    ) -> OutboxMessage | None:
        """Queue one message.

        With a ``dedup_key`` this is an upsert: a pending or cancelled row with the same key is
        rescheduled, an already sent one is left alone and ``None`` comes back.
        """
        if kind not in OUTBOX_KINDS:
            raise ValidationFailedError("kind", f"unknown outbox kind {kind!r}")
        moment = require_aware(run_at, "run_at") if run_at is not None else self.now()
        values: dict[str, Any] = {
            "user_id": user_id,
            "kind": kind,
            "payload": dict(payload or {}),
            "run_at": moment,
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "dedup_key": dedup_key,
        }
        if dedup_key is None:
            message = OutboxMessage(**values)
            self.session.add(message)
            await self.session.flush()
            return message

        statement = (
            pg_insert(OutboxMessage)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[OutboxMessage.dedup_key],
                index_where=_DEDUP_INDEX_WHERE,
                set_={
                    "run_at": moment,
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None,
                    "payload": values["payload"],
                },
                # Never resurrect something the user already received.
                where=OutboxMessage.status != "sent",
            )
            .returning(OutboxMessage)
        )
        result = await self.session.execute(statement)
        return result.scalars().one_or_none()

    async def cancel(self, dedup_keys: Iterable[str]) -> int:
        """Cancel pending messages by dedup key; returns how many were cancelled."""
        keys = [key for key in dedup_keys if key]
        if not keys:
            return 0
        result = await self.session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.dedup_key.in_(keys), OutboxMessage.status == "pending")
            .values(status="cancelled")
            .returning(OutboxMessage.id)
        )
        return len(result.scalars().all())

    async def claim(self, *, limit: int = DEFAULT_CLAIM_LIMIT) -> Sequence[OutboxMessage]:
        """Lock up to ``limit`` due messages for this transaction.

        The rows stay locked until the caller commits or rolls back, which is what keeps a second
        worker from picking up the same message while it is being delivered.
        """
        result = await self.session.execute(
            select(OutboxMessage)
            .where(OutboxMessage.status == "pending", OutboxMessage.run_at <= self.now())
            .order_by(OutboxMessage.run_at, OutboxMessage.id)
            .limit(max(1, limit))
            .with_for_update(skip_locked=True)
        )
        return result.scalars().all()

    async def mark_sent(self, message: OutboxMessage) -> None:
        message.status = "sent"
        message.attempts += 1
        message.last_error = None
        await self.session.flush()

    async def mark_failed(
        self, message: OutboxMessage, error: str, *, retry_after: float | None = None
    ) -> None:
        """Schedule a retry, or give up after ``OUTBOX_MAX_ATTEMPTS``."""
        message.attempts += 1
        message.last_error = error[:_ERROR_LIMIT]
        if message.attempts >= OUTBOX_MAX_ATTEMPTS:
            message.status = "failed"
        else:
            message.status = "pending"
            delay = retry_delay(message.attempts)
            if retry_after is not None and retry_after > delay.total_seconds():
                delay = timedelta(seconds=retry_after)
            message.run_at = self.now() + delay
        await self.session.flush()

    async def count(self, *, status: str | None = None) -> int:
        statement = select(func.count()).select_from(OutboxMessage)
        if status is not None:
            statement = statement.where(OutboxMessage.status == status)
        return int((await self.session.execute(statement)).scalar_one())


_ERROR_LIMIT = 2000
