"""Translate MAX updates into domain operations, isolating one transaction per update."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus.bot.handlers.core import Handler
from campus.db.session import domain_scope
from campus.domain.clock import Clock, SystemClock
from campus.domain.context import DomainConfig
from campus.domain.errors import DomainError
from campus.i18n import translator
from campus.max.client import MaxApiError, MaxAuthError, MaxClient, MaxError
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
        if isinstance(update, MessageCallbackUpdate):
            try:
                await self.client.answer_callback(callback_id=update.callback.callback_id)
            except MaxAuthError:
                raise
            except MaxError as exc:
                # Acknowledging the UI must not prevent the authenticated user's action.
                # MAX may reject an empty answer or an expired callback identifier.
                logger.warning(
                    "callback_answer_failed",
                    extra={
                        "error_type": type(exc).__name__,
                        "max_method": "POST /answers",
                        "max_status": exc.status_code if isinstance(exc, MaxApiError) else None,
                    },
                )
        handler: Handler | None = None
        try:
            async with domain_scope(self.session_factory) as session:
                handler = Handler(session, self.config, self.clock, self.client, actor.user_id)
                await handler.handle(update, actor)
        except DomainError as exc:
            lang = handler.lang if handler else self.config.default_language
            await self.client.send_message(
                user_id=actor.user_id,
                body=NewMessageBody(text=translator().error(lang, exc.message_key, **exc.params)),
            )
