"""Render QR images; the domain owns display lifetime and window bookkeeping."""

import io
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import segno
from sqlalchemy.ext.asyncio import AsyncSession

from campus.db.models import QrDisplay
from campus.domain import codes
from campus.domain.clock import Clock
from campus.domain.context import DomainConfig
from campus.domain.services import EventService, QrDisplayService, UserService
from campus.i18n import translator
from campus.max.client import MaxAuthError, MaxClient, MaxRateLimitError
from campus.max.types import CallbackButton, NewMessageBody, image_from_token, keyboard

logger = logging.getLogger(__name__)


def qr_png(deeplink: str) -> bytes:
    output = io.BytesIO()
    segno.make(deeplink, micro=False).save(output, kind="png", scale=8, border=4)
    return output.getvalue()


class QrWorker:
    def __init__(self, client: MaxClient, config: DomainConfig, clock: Clock) -> None:
        self.client, self.config, self.clock = client, config, clock
        self.retry_at: dict[int, datetime] = {}

    async def render(self, session: AsyncSession, display: QrDisplay) -> None:
        displays = QrDisplayService(session, self.config, self.clock)
        events = EventService(session, self.config, self.clock)
        event = await events.require(display.event_id)
        now = self.clock.now()
        if display.stopped_at is not None:
            return
        if now >= min(display.active_until, event.ends_at) or not event.checkin_open:
            await displays.stop(display)
            return
        window = codes.window_for(now, self.config.checkin_code_step_seconds)
        if display.last_rendered_window == window:
            return
        user = await UserService(session, self.config, self.clock).require(display.organizer_id)
        view = await events.view(event)
        code = events.current_code(event)
        token = await self.client.upload_image(content=qr_png(code.deeplink))
        text = translator().text(
            user.lang,
            "bot.qr_caption",
            code=code.code,
            attendees_count=view.attendees_count,
            ends_at=display.active_until.astimezone(
                ZoneInfo(self.config.university.university.timezone)
            ).strftime(translator().text(user.lang, "bot.short_datetime_format")),
        )
        body = NewMessageBody(
            text=text[:4000],
            attachments=[
                image_from_token(token),
                keyboard(
                    [
                        [
                            CallbackButton(
                                text=translator().text(user.lang, "bot.stop"),
                                payload=f"qr_stop:{event.id}",
                            )
                        ]
                    ]
                ),
            ],
            notify=False,
        )
        message_id = display.message_id
        if message_id is None:
            sent = await self.client.send_message(
                body=body, chat_id=display.max_chat_id, user_id=display.max_user_id
            )
            message_id = sent.message_id
            if message_id is None:
                raise RuntimeError("MAX did not return a message identifier")
        else:
            await self.client.edit_message(
                message_id=message_id,
                body=body,
                user_id=display.max_user_id,
                chat_id=display.max_chat_id,
            )
        await displays.record_render(display, window=window, message_id=message_id)

    async def tick(self, session: AsyncSession) -> None:
        displays = QrDisplayService(session, self.config, self.clock)
        await displays.stop_expired()
        for display in await displays.due(for_update=True):
            if self.retry_at.get(display.id, self.clock.now()) > self.clock.now():
                continue
            try:
                await self.render(session, display)
                self.retry_at.pop(display.id, None)
            except MaxAuthError:
                raise
            except Exception as exc:
                delay = (
                    max(1.0, exc.retry_after or 5.0) if isinstance(exc, MaxRateLimitError) else 5.0
                )
                self.retry_at[display.id] = self.clock.now() + timedelta(seconds=delay)
                logger.warning("qr_render_failed", extra={"error_type": type(exc).__name__})
