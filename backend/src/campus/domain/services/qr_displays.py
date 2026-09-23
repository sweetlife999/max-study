"""Rotating QR images shown in an organizer's chat (ARCHITECTURE.md §8).

The bot owns the rendering; this service owns the bookkeeping: which displays are live, which
window each has already drawn, and when the next one starts.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update

from campus.db.models import Event, QrDisplay, User
from campus.domain import codes
from campus.domain.errors import (
    CheckinClosedError,
    CheckinWindowOverError,
    QrDisplayNotFoundError,
    ValidationFailedError,
)
from campus.domain.services.base import Service, require_aware
from campus.domain.services.outbox import OutboxService


@dataclass(frozen=True, slots=True)
class QrDisplayService(Service):
    async def get(self, display_id: int, *, for_update: bool = False) -> QrDisplay | None:
        statement = select(QrDisplay).where(QrDisplay.id == display_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await self.session.execute(statement)).scalars().one_or_none()

    @property
    def _outbox(self) -> OutboxService:
        return OutboxService(self.session, self.config, self.clock)

    async def active_for(
        self, *, organizer_id: int, event_id: int, for_update: bool = False
    ) -> QrDisplay | None:
        statement = select(QrDisplay).where(
            QrDisplay.organizer_id == organizer_id,
            QrDisplay.event_id == event_id,
            QrDisplay.stopped_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await self.session.execute(statement)).scalars().one_or_none()

    async def active(
        self, *, now: datetime | None = None, for_update: bool = False
    ) -> Sequence[QrDisplay]:
        """Displays that should still be drawing: not stopped and not past their end."""
        moment = now or self.now()
        statement = (
            select(QrDisplay)
            .where(QrDisplay.stopped_at.is_(None), QrDisplay.active_until > moment)
            .order_by(QrDisplay.id)
        )
        if for_update:
            statement = statement.with_for_update(skip_locked=True).execution_options(
                populate_existing=True
            )
        return (await self.session.execute(statement)).scalars().all()

    async def due(
        self, *, now: datetime | None = None, for_update: bool = False
    ) -> Sequence[QrDisplay]:
        """Active displays whose drawn window is no longer the current one."""
        moment = now or self.now()
        window = codes.window_for(moment, self.config.checkin_code_step_seconds)
        return [
            display
            for display in await self.active(now=moment, for_update=for_update)
            if display.last_rendered_window != window
        ]

    def next_window_at(self, *, now: datetime | None = None) -> datetime:
        """When the current code expires and the next image is due."""
        moment = now or self.now()
        step = self.config.checkin_code_step_seconds
        return codes.window_start(codes.window_for(moment, step) + 1, step)

    async def start(
        self,
        *,
        event: Event,
        organizer: User,
        max_chat_id: int | None = None,
        max_user_id: int | None = None,
        active_until: datetime | None = None,
    ) -> QrDisplay:
        """Begin (or refresh) the single display for this organizer and event.

        Starting twice is not an error: the existing display is kept and its end time refreshed,
        which is what the partial unique index in §4 allows.
        """
        destination_user = max_user_id if max_chat_id is None else None
        if max_chat_id is None and destination_user is None:
            raise ValidationFailedError("max_chat_id", "a chat or a user id is required")
        until = require_aware(active_until, "active_until") if active_until else event.ends_at
        # A custom display lifetime may intentionally already be over (the bot uses this state
        # to render the "start again" control). The event deadline itself is the condition that
        # makes accepting a new chat delivery misleading.
        if self.now() >= event.ends_at:
            raise CheckinWindowOverError(f"event {event.id}")
        if not event.checkin_open:
            raise CheckinClosedError(f"event {event.id}")

        existing = await self.active_for(organizer_id=organizer.id, event_id=event.id)
        if existing is not None:
            existing.active_until = until
            existing.max_chat_id = max_chat_id
            existing.max_user_id = destination_user
            await self.session.flush()
            display = existing
        else:
            display = QrDisplay(
                event_id=event.id,
                organizer_id=organizer.id,
                max_chat_id=max_chat_id,
                max_user_id=destination_user,
                active_until=until,
            )
            self.session.add(display)
            await self.session.flush()

        await self._outbox.enqueue(
            user_id=organizer.id,
            kind="qr_display_start",
            payload={"event_id": event.id, "qr_display_id": display.id},
            dedup_key=f"qr_display_start:{display.id}",
        )
        return display

    async def record_render(
        self, display: QrDisplay, *, window: int, message_id: str | None
    ) -> QrDisplay:
        display.last_rendered_window = window
        if message_id is not None:
            display.message_id = message_id
        await self.session.flush()
        return display

    async def stop(self, display: QrDisplay) -> QrDisplay:
        if display.stopped_at is None:
            display.stopped_at = self.now()
            await self.session.flush()
        return display

    async def stop_for(self, *, organizer_id: int, event_id: int) -> QrDisplay:
        display = await self.active_for(
            organizer_id=organizer_id, event_id=event_id, for_update=True
        )
        if display is None:
            raise QrDisplayNotFoundError(f"no active display for event {event_id}")
        return await self.stop(display)

    async def stop_expired(self, *, now: datetime | None = None) -> int:
        """Retire displays whose event has ended; returns how many were retired."""
        moment = now or self.now()
        result = await self.session.execute(
            update(QrDisplay)
            .where(QrDisplay.stopped_at.is_(None), QrDisplay.active_until <= moment)
            .values(stopped_at=moment)
            .returning(QrDisplay.id)
        )
        return len(result.scalars().all())
