"""Transactional outbox delivery. Network retries are owned by the outbox schedule."""

import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from campus.bot.workers.qr import QrWorker
from campus.db.models import OutboxMessage
from campus.domain.clock import Clock
from campus.domain.context import DomainConfig
from campus.domain.services import EventService, OutboxService, QrDisplayService, UserService
from campus.i18n import translator
from campus.max.client import MaxAuthError, MaxClient, MaxRateLimitError
from campus.max.types import NewMessageBody

logger = logging.getLogger(__name__)


class OutboxWorker:
    def __init__(self, client: MaxClient, config: DomainConfig, clock: Clock) -> None:
        self.client, self.config, self.clock = client, config, clock
        self.qr = QrWorker(client, config, clock)

    async def deliver(self, session: AsyncSession, row: OutboxMessage) -> None:
        if row.kind == "qr_display_start":
            display = await QrDisplayService(session, self.config, self.clock).get(
                int(row.payload["qr_display_id"]), for_update=True
            )
            if display is not None:
                await self.qr.render(session, display)
            return
        users = UserService(session, self.config, self.clock)
        user = await users.require(row.user_id)
        params: dict[str, object] = {}
        if row.kind in {"reminder", "checkin_confirmed"}:
            event = await EventService(session, self.config, self.clock).require(
                int(row.payload["event_id"])
            )
            params.update(
                title=event.title,
                location=event.location,
                starts_at=event.starts_at.astimezone(
                    ZoneInfo(self.config.university.university.timezone)
                ).strftime(translator().text(user.lang, "bot.short_datetime_format")),
                points_total=await users.points(user.id),
            )
        elif row.kind == "step_completed":
            key = str(row.payload["step_key"])
            step = next(
                (item for item in self.config.university.onboarding_steps if item.key == key), None
            )
            params["title"] = self.config.university.text(step.title, user.lang) if step else key
        text = translator().text(user.lang, f"bot.{row.kind}", **params)
        await self.client.send_message(
            user_id=user.max_user_id, body=NewMessageBody(text=text[:4000])
        )

    async def tick(self, session: AsyncSession) -> None:
        outbox = OutboxService(session, self.config, self.clock)
        for row in await outbox.claim():
            try:
                await self.deliver(session, row)
            except MaxAuthError:
                # The token is gone: every remaining row would burn its five attempts against
                # the same 401 and end up `failed` beyond recovery. Let the process die instead
                # — polling and the QR worker already treat this the same way.
                raise
            except Exception as exc:
                retry_after = exc.retry_after if isinstance(exc, MaxRateLimitError) else None
                await outbox.mark_failed(
                    row,
                    type(exc).__name__,
                    retry_after=retry_after,
                )
                logger.warning(
                    "outbox_delivery_failed",
                    extra={
                        "error_type": type(exc).__name__,
                        "outbox_id": row.id,
                        "kind": row.kind,
                        "attempts": row.attempts,
                    },
                )
            else:
                await outbox.mark_sent(row)
