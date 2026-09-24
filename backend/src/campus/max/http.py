"""httpx implementation of :class:`~campus.max.client.MaxClient`.

Verified against https://dev.max.ru/docs-api on 2026-09-18:

* base URL ``https://platform-api2.max.ru``;
* the token goes in ``Authorization: <token>`` — *not* ``Bearer <token>``, and query-parameter
  auth is no longer supported;
* ``GET /updates`` takes ``limit`` (1-1000), ``timeout`` (0-90), ``marker``, ``types``;
* ``POST /messages`` takes ``user_id`` / ``chat_id`` / ``disable_link_preview`` as query
  parameters and NewMessageBody as the JSON body;
* ``PUT`` and ``DELETE /messages`` take ``message_id`` as a query parameter and answer
  ``{success, message}``;
* ``POST /answers`` takes ``callback_id`` as a query parameter;
* ``POST /uploads?type=image`` answers ``{url, token?}``; the bytes are then posted to that URL
  as multipart ``data``. The current image response is a ``photos`` map that must be passed to
  the image attachment unchanged; older responses may use a top-level ``token``.

TLS: the Russian Trusted Root CA that signs platform-api2.max.ru is *added* to the system trust
store. Verification is never disabled.
"""

import asyncio
import logging
import math
import ssl
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import httpx

from campus.max.client import (
    DEFAULT_POLL_LIMIT,
    DEFAULT_POLL_TIMEOUT_SECONDS,
    MaxApiError,
    MaxAuthError,
    MaxRateLimitError,
    MaxTransportError,
)
from campus.max.ratelimit import PerChatLimiter, TokenBucket
from campus.max.types import (
    UPDATES_MAX_LIMIT,
    UPDATES_MAX_TIMEOUT_SECONDS,
    BotInfo,
    ImagePayload,
    Message,
    NewMessageBody,
    SimpleResult,
    UpdatesPage,
    UploadTarget,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final = "https://platform-api2.max.ru"
CA_BUNDLE_NAME: Final = "russian_trusted_root_ca.pem"
# Source checkout first, then the location the Docker image copies the bundle to.
CA_BUNDLE_CANDIDATES: Final = (
    Path(__file__).resolve().parents[3] / "certs" / CA_BUNDLE_NAME,
    Path("/app/certs") / CA_BUNDLE_NAME,
)

CONNECT_TIMEOUT_SECONDS: Final = 10.0
READ_TIMEOUT_SECONDS: Final = 30.0
WRITE_TIMEOUT_SECONDS: Final = 30.0
POOL_TIMEOUT_SECONDS: Final = 10.0
# Long polling must outlive the server-side timeout, or every poll would look like a failure.
POLL_READ_MARGIN_SECONDS: Final = 15.0

MAX_ATTEMPTS: Final = 4
RETRY_BASE_DELAY_SECONDS: Final = 0.5
RETRY_MAX_DELAY_SECONDS: Final = 30.0
UPLOAD_TIMEOUT_SECONDS: Final = 120.0

_HTTP_TOO_MANY_REQUESTS: Final = 429
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_BAD_REQUEST: Final = 400


def default_ca_bundle() -> Path | None:
    """Where the Russian Trusted Root CA lives, in a checkout or in the image."""
    return next((path for path in CA_BUNDLE_CANDIDATES if path.is_file()), None)


def build_ssl_context(ca_bundle: Path | None = None) -> ssl.SSLContext:
    """The system trust store *plus* the Ministry of Digital Development root CA.

    Verification stays on: the extra anchor is added, never substituted for the defaults.
    """
    context = ssl.create_default_context()
    path = ca_bundle or default_ca_bundle()
    if path is not None and path.is_file():
        context.load_verify_locations(cafile=str(path))
    else:  # pragma: no cover - only when the image was built without the certificate
        logger.warning(
            "max.ca_bundle_missing", extra={"candidates": [str(p) for p in CA_BUNDLE_CANDIDATES]}
        )
    return context


def _query(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class HttpMaxClient:
    """One instance per process; it owns the connection pool and the rate limiters."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        ca_bundle: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        global_bucket: TokenBucket | None = None,
        per_chat_limiter: PerChatLimiter | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: Any = asyncio.sleep,
    ) -> None:
        if not token:
            msg = "MAX bot token must not be empty"
            raise ValueError(msg)
        self._retry_not_before = 0.0
        self._token = token
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._bucket = global_bucket or TokenBucket()
        self._chats = per_chat_limiter or PerChatLimiter()
        self._message_destinations: dict[str, str] = {}
        verify: Any = build_ssl_context(ca_bundle) if transport is None else True
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": token, "Accept": "application/json"},
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=WRITE_TIMEOUT_SECONDS,
                pool=POOL_TIMEOUT_SECONDS,
            ),
            verify=verify,
            transport=transport,
            follow_redirects=False,
        )
        # The upload host is a different origin and gets credentials only after its host is
        # validated by ``upload_image``.
        self._uploads = httpx.AsyncClient(
            timeout=httpx.Timeout(UPLOAD_TIMEOUT_SECONDS),
            verify=verify,
            transport=transport,
            follow_redirects=False,
        )

    # --- plumbing --------------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
        chat_key: str | None = None,
        timeout: httpx.Timeout | None = None,  # noqa: ASYNC109 - httpx timeout, not asyncio
        client: httpx.AsyncClient | None = None,
    ) -> Any:
        """One API call with rate limiting, 429 backoff and bounded retries."""
        label = f"{method} {url}"
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            cooldown = self._retry_not_before - time.monotonic()
            if cooldown > 0:
                await self._sleep(cooldown)
            await self._bucket.acquire()
            await self._chats.acquire(chat_key)
            try:
                response = await (client or self._client).request(
                    method,
                    url,
                    # Passing an empty mapping makes httpx discard a query string already
                    # present in an absolute URL. MAX signs its CDN upload URL in that query.
                    params=dict(params) if params is not None else None,
                    json=json,
                    files=files,
                    headers=dict(headers or {}),
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                last_error = MaxTransportError(f"{label}: {exc}")
            else:
                try:
                    return self._parse(response, label)
                except MaxApiError as exc:
                    if not exc.is_retryable:
                        raise
                    last_error = exc
            if attempt == self._max_attempts:
                if isinstance(last_error, MaxRateLimitError):
                    self._retry_not_before = time.monotonic() + self._retry_delay(
                        attempt, last_error
                    )
                break
            await self._sleep(self._retry_delay(attempt, last_error))
        raise last_error if last_error else MaxTransportError(label)

    @staticmethod
    def _retry_delay(attempt: int, error: Exception | None) -> float:
        if isinstance(error, MaxRateLimitError) and error.retry_after is not None:
            # Respect the server's number, but never past the cap: this delay is also the
            # client-wide cooldown every later request waits out, so an hour-long Retry-After
            # from one chat would otherwise stop polling and QR rotation for that hour. The
            # outbox keeps the full value for its own scheduling, where nothing is blocked.
            return min(RETRY_MAX_DELAY_SECONDS, max(0.0, error.retry_after))
        return min(RETRY_MAX_DELAY_SECONDS, RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))

    @staticmethod
    def _parse(response: httpx.Response, label: str) -> Any:
        if response.status_code == _HTTP_TOO_MANY_REQUESTS:
            raise MaxRateLimitError(_error_message(response), _retry_after(response), method=label)
        if response.status_code == _HTTP_UNAUTHORIZED:
            raise MaxAuthError(_error_message(response), method=label)
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise MaxApiError(
                response.status_code,
                _error_message(response),
                _error_code(response),
                method=label,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise MaxApiError(response.status_code, f"malformed JSON: {exc}", method=label) from exc

    @staticmethod
    def _require_success(payload: Any, label: str) -> None:
        result = SimpleResult.model_validate(payload if isinstance(payload, Mapping) else {})
        if not result.success:
            raise MaxApiError(200, result.message or "success=false", method=label)

    # --- API -------------------------------------------------------------------------------

    async def get_me(self) -> BotInfo:
        return BotInfo.model_validate(await self._request("GET", "/me"))

    async def get_updates(
        self,
        *,
        marker: int | None = None,
        limit: int = DEFAULT_POLL_LIMIT,
        timeout: int = DEFAULT_POLL_TIMEOUT_SECONDS,  # noqa: ASYNC109 - MAX long-poll parameter
        types: Sequence[str] | None = None,
    ) -> UpdatesPage:
        bounded_limit = max(1, min(UPDATES_MAX_LIMIT, limit))
        bounded_timeout = max(0, min(UPDATES_MAX_TIMEOUT_SECONDS, timeout))
        payload = await self._request(
            "GET",
            "/updates",
            params=_query(
                marker=marker,
                limit=bounded_limit,
                timeout=bounded_timeout,
                types=",".join(types) if types else None,
            ),
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=bounded_timeout + POLL_READ_MARGIN_SECONDS,
                write=WRITE_TIMEOUT_SECONDS,
                pool=POOL_TIMEOUT_SECONDS,
            ),
        )
        return UpdatesPage.from_payload(payload if isinstance(payload, Mapping) else {})

    async def send_message(
        self,
        *,
        body: NewMessageBody,
        user_id: int | None = None,
        chat_id: int | None = None,
        disable_link_preview: bool | None = None,
    ) -> Message:
        _require_one_destination(user_id, chat_id)
        payload = await self._request(
            "POST",
            "/messages",
            params=_query(
                user_id=user_id, chat_id=chat_id, disable_link_preview=disable_link_preview
            ),
            json=body.to_payload(),
            chat_key=_chat_key(user_id, chat_id),
        )
        envelope = payload if isinstance(payload, Mapping) else {}
        message = Message.model_validate(envelope.get("message", envelope))
        if message.message_id is not None:
            if len(self._message_destinations) >= 4096:
                self._message_destinations.pop(next(iter(self._message_destinations)))
            self._message_destinations[message.message_id] = _chat_key(user_id, chat_id)
        return message

    async def edit_message(
        self,
        *,
        message_id: str,
        body: NewMessageBody,
        user_id: int | None = None,
        chat_id: int | None = None,
    ) -> None:
        payload = await self._request(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json=body.to_payload(),
            chat_key=(
                _chat_key(user_id, chat_id)
                if user_id is not None or chat_id is not None
                else self._message_destinations.get(message_id, f"message:{message_id}")
            ),
        )
        self._require_success(payload, "PUT /messages")

    async def delete_message(self, *, message_id: str) -> None:
        payload = await self._request(
            "DELETE",
            "/messages",
            params={"message_id": message_id},
            chat_key=f"message:{message_id}",
        )
        self._require_success(payload, "DELETE /messages")

    async def answer_callback(
        self,
        *,
        callback_id: str,
        body: NewMessageBody | None = None,
        notification: str | None = None,
    ) -> None:
        # UNCONFIRMED: dev.max.ru documents only `message` in the POST /answers body, while the
        # prose promises "одноразовое уведомление". `notification` follows the MAX client
        # libraries; it is simply omitted when the caller does not ask for one.
        if body is None and notification is None:
            msg = "callback answer requires a message or notification"
            raise ValueError(msg)
        json_body: dict[str, Any] = {}
        if body is not None:
            json_body["message"] = body.to_payload()
        if notification is not None:
            json_body["notification"] = notification
        payload = await self._request(
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            json=json_body,
        )
        self._require_success(payload, "POST /answers")

    async def upload_image(
        self, *, content: bytes, filename: str = "image.png", content_type: str = "image/png"
    ) -> ImagePayload:
        target = UploadTarget.model_validate(
            await self._request("POST", "/uploads", params={"type": "image"})
        )
        _require_trusted_image_upload_url(target.url)
        uploaded = await self._request(
            "POST",
            target.url,
            files={"data": (filename, content, content_type)},
            headers={"Authorization": self._token},
            timeout=httpx.Timeout(UPLOAD_TIMEOUT_SECONDS),
            client=self._uploads,
        )
        try:
            payload = _extract_image_payload(uploaded)
        except ValueError as exc:
            raise MaxApiError(200, str(exc), method="POST /uploads") from exc
        if payload is not None:
            return payload
        if isinstance(target.token, str) and target.token:
            return ImagePayload(token=target.token)
        raise MaxApiError(200, "upload did not return a token", method="POST /uploads")

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._uploads.aclose()


def _require_one_destination(user_id: int | None, chat_id: int | None) -> None:
    if (user_id is None) == (chat_id is None):
        msg = "exactly one of user_id or chat_id must be given"
        raise ValueError(msg)


def _require_trusted_image_upload_url(url: str) -> None:
    """The bot token may leave the API origin only for MAX's documented image upload host."""
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host not in {"iu.oneme.ru", "iusmile.oneme.ru"}:
        raise MaxApiError(200, "untrusted image upload URL", method="POST /uploads")


def _extract_image_payload(payload: object) -> ImagePayload | None:
    """Preserve MAX's image ``photos`` map; accept the older token response as fallback."""
    if not isinstance(payload, Mapping):
        raise ValueError("upload returned malformed token data")

    if "photos" in payload:
        photos = payload["photos"]
        if not isinstance(photos, Mapping) or not photos:
            raise ValueError("upload returned malformed photos data")
        normalized: dict[str, dict[str, Any]] = {}
        for photo_id, photo in photos.items():
            if not isinstance(photo_id, str) or not photo_id or not isinstance(photo, Mapping):
                raise ValueError("upload returned malformed photos data")
            token = photo.get("token")
            if not isinstance(token, str) or not token:
                raise ValueError("upload returned malformed photos data")
            normalized[photo_id] = dict(photo)
        return ImagePayload(photos=normalized)
    if "token" in payload:
        token = payload["token"]
        if not isinstance(token, str) or not token:
            raise ValueError("upload returned malformed token data")
        return ImagePayload(token=token)
    return None


def _chat_key(user_id: int | None, chat_id: int | None) -> str:
    return f"user:{user_id}" if user_id is not None else f"chat:{chat_id}"


def _body(response: httpx.Response) -> Mapping[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _error_message(response: httpx.Response) -> str:
    value = _body(response).get("message")
    return value if isinstance(value, str) else ""


def _error_code(response: httpx.Response) -> str | None:
    value = _body(response).get("code")
    return value if isinstance(value, str) else None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
        return value if math.isfinite(value) and value >= 0 else None
    except ValueError:
        return None
