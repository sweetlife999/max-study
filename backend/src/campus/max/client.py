"""The MAX client seen by the rest of the code: a Protocol plus its error types.

Handlers and workers depend on ``MaxClient`` only, so tests can pass ``FakeMaxClient`` and no
test ever touches the network (ARCHITECTURE.md §11).
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from campus.max.types import BotInfo, ImagePayload, Message, NewMessageBody, UpdatesPage

DEFAULT_POLL_TIMEOUT_SECONDS = 30
DEFAULT_POLL_LIMIT = 100


class MaxError(Exception):
    """Any failure while talking to MAX."""


class MaxTransportError(MaxError):
    """The request never produced an HTTP response (DNS, TLS, timeout, connection reset)."""


class MaxApiError(MaxError):
    """MAX answered with an error status or a ``success: false`` envelope."""

    def __init__(
        self, status_code: int, message: str = "", code: str | None = None, method: str = ""
    ) -> None:
        super().__init__(f"{method or 'MAX'} failed with {status_code}: {message or code or ''}")
        self.status_code = status_code
        self.message = message
        self.code = code
        self.method = method

    @property
    def is_retryable(self) -> bool:
        return self.status_code >= 500


class MaxRateLimitError(MaxApiError):
    """HTTP 429. ``retry_after`` is the delay MAX asked for, in seconds, when it stated one."""

    def __init__(
        self, message: str = "", retry_after: float | None = None, method: str = ""
    ) -> None:
        super().__init__(429, message, code="too_many_requests", method=method)
        self.retry_after = retry_after

    @property
    def is_retryable(self) -> bool:
        return True


class MaxAuthError(MaxApiError):
    """HTTP 401: the bot token is missing, malformed or revoked. Retrying cannot help."""

    def __init__(self, message: str = "", method: str = "") -> None:
        super().__init__(401, message, code="unauthorized", method=method)

    @property
    def is_retryable(self) -> bool:
        return False


_LOGGABLE_METHODS = frozenset(
    {
        "GET /me",
        "GET /updates",
        "POST /messages",
        "PUT /messages",
        "DELETE /messages",
        "POST /answers",
        "POST /uploads",
    }
)


def api_error_log_fields(error: Exception) -> dict[str, int | str | None]:
    """Expose useful MAX diagnostics without logging response text or upload URLs."""
    if not isinstance(error, MaxApiError):
        return {}
    return {
        "http_status": error.status_code,
        "max_method": error.method if error.method in _LOGGABLE_METHODS else "other",
        "max_error_code": error.code if error.method in _LOGGABLE_METHODS else None,
    }


@runtime_checkable
class MaxClient(Protocol):
    """The subset of the MAX Bot API this product uses."""

    async def get_me(self) -> BotInfo:
        """GET /me — bot identity; used at start-up to verify the token."""
        ...

    async def get_updates(
        self,
        *,
        marker: int | None = None,
        limit: int = DEFAULT_POLL_LIMIT,
        timeout: int = DEFAULT_POLL_TIMEOUT_SECONDS,  # noqa: ASYNC109 - MAX long-poll parameter
        types: Sequence[str] | None = None,
    ) -> UpdatesPage:
        """GET /updates — long polling. ``marker`` is the pointer stored in ``kv``."""
        ...

    async def send_message(
        self,
        *,
        body: NewMessageBody,
        user_id: int | None = None,
        chat_id: int | None = None,
        disable_link_preview: bool | None = None,
    ) -> Message:
        """POST /messages — exactly one of ``user_id`` / ``chat_id`` must be given."""
        ...

    async def edit_message(
        self,
        *,
        message_id: str,
        body: NewMessageBody,
        user_id: int | None = None,
        chat_id: int | None = None,
    ) -> None:
        """PUT /messages?message_id=... — used to rotate the QR image in place."""
        ...

    async def delete_message(self, *, message_id: str) -> None:
        """DELETE /messages?message_id=..."""
        ...

    async def answer_callback(
        self,
        *,
        callback_id: str,
        body: NewMessageBody | None = None,
        notification: str | None = None,
    ) -> None:
        """POST /answers?callback_id=... — send a message edit or notification."""
        ...

    async def upload_image(
        self, *, content: bytes, filename: str = "image.png", content_type: str = "image/png"
    ) -> ImagePayload:
        """Upload an image and return the payload MAX expects in an image attachment."""
        ...

    async def aclose(self) -> None:
        """Release transport resources."""
        ...
