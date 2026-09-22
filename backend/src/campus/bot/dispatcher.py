"""Translate MAX updates into domain operations, isolating one transaction per update."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus.bot.handlers.core import Handler
from campus.db.session import domain_scope, session_scope
from campus.domain.clock import Clock, SystemClock
from campus.domain.context import DomainConfig
from campus.domain.errors import DomainError
from campus.domain.services.outbox import OutboxService
from campus.i18n import translator
from campus.max.client import MaxClient, MaxError, api_error_log_fields
from campus.max.types import (
    BotStartedUpdate,
    MessageCallbackUpdate,
    MessageCreatedUpdate,
    NewMessageBody,
    Update,
)

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        config: DomainConfig,
        client: MaxClient,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Clock | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.session_factory = session_factory
        self.clock = clock or SystemClock()

    async def dispatch(self, update: Update) -> None:
        if isinstance(update, BotStartedUpdate):
            actor = update.user
        elif isinstance(update, MessageCallbackUpdate):
            actor = update.callback.user
            if (
                update.message
                and update.message.recipient
                and update.message.recipient.chat_type in {"chat", "channel"}
            ):
                return
        elif isinstance(update, MessageCreatedUpdate):
            if update.message.recipient and update.message.recipient.chat_type in {
                "chat",
                "channel",
            }:
                return
            actor = update.message.sender
        else:
            return
        if actor is None or actor.is_bot:
            return
        # Complete the domain transaction before sending any reply. A MAX failure must not
        # roll back a check-in whose update will nevertheless be checkpointed by polling.
        handler: Handler | None = None
        error_text: str | None = None
        try:
            async with domain_scope(self.session_factory) as session:
                handler = Handler(session, self.config, self.clock, self.client, actor.user_id)
                handler.defer_messages = True
                await handler.handle(update, actor)
        except DomainError as exc:
            lang = handler.lang if handler else self.config.default_language
            error_text = translator().error(lang, exc.message_key, **exc.params)

        if isinstance(update, MessageCallbackUpdate):
            await self._finish_callback(
                update, actor.user_id, handler.pending_messages if handler else [], error_text
            )
        else:
            await self._send_reply(
                actor.user_id, handler.pending_messages if handler else [], error_text
            )
        # Keep the queued notification as a fallback until the immediate reply succeeded.
        if handler and handler.pending_cancellations:
            async with session_scope(self.session_factory) as session:
                await OutboxService(session, self.config, self.clock).cancel(
                    handler.pending_cancellations
                )

    async def _send_reply(
        self, user_id: int, pending: list[NewMessageBody], error_text: str | None
    ) -> None:
        for body in pending:
            await self.client.send_message(user_id=user_id, body=body)
        if error_text is not None:
            await self.client.send_message(user_id=user_id, body=NewMessageBody(text=error_text))

    async def _finish_callback(
        self,
        update: MessageCallbackUpdate,
        user_id: int,
        pending: list[NewMessageBody],
        error_text: str | None,
    ) -> None:
        # MAX rejects an empty POST /answers body. The explicit empty attachments list removes
        # stale inline buttons; clearing an expired QR also prevents its code being reused.
        old_body = NewMessageBody(
            text=update.message.text or None if update.message else None,
            attachments=[],
        )
        try:
            await self.client.answer_callback(
                callback_id=update.callback.callback_id, body=old_body
            )
        except MaxError as exc:
            logger.warning(
                "callback_clear_failed",
                extra={"error_type": type(exc).__name__, **api_error_log_fields(exc)},
            )
            # A callback can expire before the update is processed. Editing by message ID
            # still clears the old keyboard when MAX gave us the original message.
            if update.message and update.message.message_id:
                try:
                    await self.client.edit_message(
                        message_id=update.message.message_id,
                        body=old_body,
                        user_id=user_id,
                    )
                except MaxError as edit_exc:
                    logger.warning(
                        "callback_edit_failed",
                        extra={
                            "error_type": type(edit_exc).__name__,
                            **api_error_log_fields(edit_exc),
                        },
                    )
        for body in pending:
            await self.client.send_message(user_id=user_id, body=body)
        if error_text is not None:
            await self.client.send_message(user_id=user_id, body=NewMessageBody(text=error_text))
