"""In-memory MaxClient for tests (ARCHITECTURE.md §11: MAX is only ever reached through this).

It records every call, lets a test queue updates and failures, and keeps the sent messages so
assertions can read them back.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from campus.max.client import DEFAULT_POLL_LIMIT, DEFAULT_POLL_TIMEOUT_SECONDS, MaxError
from campus.max.types import (
    BotInfo,
    Message,
    MessageBody,
    NewMessageBody,
    Recipient,
    UpdatesPage,
)


@dataclass(frozen=True, slots=True)
class RecordedCall:
    method: str
    kwargs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SentMessage:
    message_id: str
    body: NewMessageBody
    user_id: int | None
    chat_id: int | None

    @property
    def text(self) -> str:
        return self.body.text or ""


class FakeMaxClient:
    """A scripted MaxClient. Everything it did is available on ``calls``."""

    def __init__(
        self,
        *,
        bot: BotInfo | None = None,
        upload_token: str = "fake-upload-token",  # noqa: S107 - test double, not a secret
    ) -> None:
        self.bot = bot or BotInfo(
            user_id=1, first_name="Campus", username="campus_bot", is_bot=True
        )
        self.calls: list[RecordedCall] = []
        self.sent: list[SentMessage] = []
        self.edited: dict[str, NewMessageBody] = {}
        self.deleted: list[str] = []
        self.answered: list[tuple[str, NewMessageBody | None, str | None]] = []
        self.uploaded: list[bytes] = []
        self.upload_token = upload_token
        self.closed = False
        self._update_pages: list[UpdatesPage] = []
        self._failures: dict[str, list[Exception]] = {}
        self._next_message_id = 0

    # --- scripting -------------------------------------------------------------------------

    def queue_updates(self, page: UpdatesPage) -> None:
        self._update_pages.append(page)

    def fail_next(self, method: str, error: Exception) -> None:
        """Make the next call to ``method`` raise ``error``."""
        self._failures.setdefault(method, []).append(error)

    def _maybe_fail(self, method: str) -> None:
        queued = self._failures.get(method)
        if queued:
            raise queued.pop(0)

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append(RecordedCall(method, kwargs))
        self._maybe_fail(method)

    def _mint_message_id(self) -> str:
        self._next_message_id += 1
        return f"mid-{self._next_message_id}"

    # --- MaxClient -------------------------------------------------------------------------

    async def get_me(self) -> BotInfo:
        self._record("get_me")
        return self.bot

    async def get_updates(
        self,
        *,
        marker: int | None = None,
        limit: int = DEFAULT_POLL_LIMIT,
        timeout: int = DEFAULT_POLL_TIMEOUT_SECONDS,  # noqa: ASYNC109 - MAX long-poll parameter
        types: Sequence[str] | None = None,
    ) -> UpdatesPage:
        self._record(
            "get_updates",
            marker=marker,
            limit=limit,
            timeout=timeout,
            types=list(types) if types else None,
        )
        if not self._update_pages:
            return UpdatesPage(updates=(), marker=marker)
        return self._update_pages.pop(0)

    async def send_message(
        self,
        *,
        body: NewMessageBody,
        user_id: int | None = None,
        chat_id: int | None = None,
        disable_link_preview: bool | None = None,
    ) -> Message:
        if (user_id is None) == (chat_id is None):
            msg = "exactly one of user_id or chat_id must be given"
            raise ValueError(msg)
        self._record(
            "send_message",
            body=body,
            user_id=user_id,
            chat_id=chat_id,
            disable_link_preview=disable_link_preview,
        )
        message_id = self._mint_message_id()
        self.sent.append(SentMessage(message_id, body, user_id, chat_id))
        return Message(
            recipient=Recipient(user_id=user_id, chat_id=chat_id),
            body=MessageBody(mid=message_id, text=body.text),
        )

    async def edit_message(
        self,
        *,
        message_id: str,
        body: NewMessageBody,
        user_id: int | None = None,
        chat_id: int | None = None,
    ) -> None:
        self._record("edit_message", message_id=message_id, body=body)
        self.edited[message_id] = body

    async def delete_message(self, *, message_id: str) -> None:
        self._record("delete_message", message_id=message_id)
        self.deleted.append(message_id)

    async def answer_callback(
        self,
        *,
        callback_id: str,
        body: NewMessageBody | None = None,
        notification: str | None = None,
    ) -> None:
        self._record(
            "answer_callback", callback_id=callback_id, body=body, notification=notification
        )
        self.answered.append((callback_id, body, notification))

    async def upload_image(
        self, *, content: bytes, filename: str = "image.png", content_type: str = "image/png"
    ) -> str:
        self._record("upload_image", content=content, filename=filename, content_type=content_type)
        self.uploaded.append(content)
        return self.upload_token

    async def aclose(self) -> None:
        self._record("aclose")
        self.closed = True

    # --- assertions helpers ----------------------------------------------------------------

    def calls_to(self, method: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.method == method]

    def last_sent(self) -> SentMessage:
        if not self.sent:
            msg = "no message was sent"
            raise AssertionError(msg)
        return self.sent[-1]


@dataclass
class ScriptedMaxError(MaxError):
    """Convenience error for tests that need a MAX failure of no particular kind."""

    detail: str = "scripted failure"
    retries: int = field(default=0)

    def __str__(self) -> str:
        return self.detail
