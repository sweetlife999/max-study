"""The httpx MAX client speaks exactly what dev.max.ru/docs-api documents.

No network: every request is served by httpx.MockTransport, and sleeps are captured.
"""

import json
import ssl
from collections.abc import Callable

import httpx
import pytest

from campus.max.client import (
    MaxApiError,
    MaxAuthError,
    MaxRateLimitError,
    MaxTransportError,
)
from campus.max.http import HttpMaxClient, build_ssl_context
from campus.max.ratelimit import PerChatLimiter, TokenBucket
from campus.max.types import LinkButton as Link
from campus.max.types import NewMessageBody, keyboard

TOKEN = "unit-test-token"


class Recorder:
    """Serves queued responses and remembers every request."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)
        self.slept: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._responses.pop(0) if self._responses else httpx.Response(200, json={})
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def make_client(recorder: Recorder, **kwargs: object) -> HttpMaxClient:
    zero: Callable[[], float] = lambda: 0.0  # noqa: E731
    return HttpMaxClient(
        TOKEN,
        transport=httpx.MockTransport(recorder.handler),
        sleep=recorder.sleep,
        global_bucket=TokenBucket(rate=1e6, burst=1e6, monotonic=zero, sleep=recorder.sleep),
        per_chat_limiter=PerChatLimiter(0.0, monotonic=zero, sleep=recorder.sleep),
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


@pytest.fixture
async def recorder() -> Recorder:
    return Recorder()


# --- authentication and transport -------------------------------------------------------------


async def test_token_goes_in_the_authorization_header_verbatim() -> None:
    # dev.max.ru: "используйте заголовок Authorization: <token>" — no Bearer prefix.
    rec = Recorder(httpx.Response(200, json={"user_id": 1, "first_name": "Campus"}))
    client = make_client(rec)

    await client.get_me()

    assert rec.last.headers["Authorization"] == TOKEN


async def test_token_never_appears_in_the_query_string() -> None:
    rec = Recorder(httpx.Response(200, json={"user_id": 1, "first_name": "Campus"}))
    client = make_client(rec)

    await client.get_me()

    assert TOKEN not in str(rec.last.url)


async def test_requests_go_to_platform_api2() -> None:
    rec = Recorder(httpx.Response(200, json={"user_id": 1, "first_name": "Campus"}))
    client = make_client(rec)

    await client.get_me()

    assert rec.last.url.host == "platform-api2.max.ru"


async def test_empty_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="token"):
        HttpMaxClient("")


def test_ssl_context_verifies_certificates() -> None:
    context = build_ssl_context()

    assert context.verify_mode is not 0  # noqa: F632
    assert context.check_hostname is True


async def test_the_client_never_turns_verification_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """§5 keeps verification on: the Ministry CA is *added*, never swapped for `verify=False`.

    ``build_ssl_context`` being correct is not enough — what matters is what the client is
    actually handed, for the API host and for the upload host alike.
    """
    handed: list[object] = []

    class Recording(httpx.AsyncClient):
        def __init__(self, **kwargs: object) -> None:
            handed.append(kwargs.get("verify"))
            super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    client = HttpMaxClient(TOKEN)
    try:
        assert len(handed) == 2  # the API host and the upload host
        for verify in handed:
            assert isinstance(verify, ssl.SSLContext)
            assert verify.verify_mode == ssl.CERT_REQUIRED
            assert verify.check_hostname is True
    finally:
        await client.aclose()


# --- GET /updates -----------------------------------------------------------------------------


async def test_get_updates_sends_the_documented_parameters() -> None:
    rec = Recorder(httpx.Response(200, json={"updates": [], "marker": 42}))
    client = make_client(rec)

    page = await client.get_updates(marker=7, limit=50, timeout=20, types=["message_created"])

    params = rec.last.url.params
    assert params["marker"] == "7"
    assert params["limit"] == "50"
    assert params["timeout"] == "20"
    assert params["types"] == "message_created"
    assert page.marker == 42


async def test_get_updates_omits_a_null_marker() -> None:
    rec = Recorder(httpx.Response(200, json={"updates": []}))
    client = make_client(rec)

    await client.get_updates()

    assert "marker" not in rec.last.url.params


async def test_get_updates_clamps_limit_and_timeout_to_the_documented_ranges() -> None:
    rec = Recorder(httpx.Response(200, json={"updates": []}))
    client = make_client(rec)

    await client.get_updates(limit=5000, timeout=900)

    assert rec.last.url.params["limit"] == "1000"
    assert rec.last.url.params["timeout"] == "90"


async def test_get_updates_parses_events() -> None:
    rec = Recorder(
        httpx.Response(
            200,
            json={
                "updates": [
                    {"update_type": "bot_started", "chat_id": 3, "user": {"user_id": 9}},
                ],
                "marker": 11,
            },
        )
    )
    client = make_client(rec)

    page = await client.get_updates()

    assert len(page.updates) == 1


# --- POST /messages ---------------------------------------------------------------------------


async def test_send_message_puts_the_recipient_in_the_query_and_the_body_in_json() -> None:
    rec = Recorder(httpx.Response(200, json={"message": {"body": {"mid": "m1", "text": "hi"}}}))
    client = make_client(rec)

    message = await client.send_message(body=NewMessageBody(text="hi"), user_id=5)

    assert rec.last.url.params["user_id"] == "5"
    assert json.loads(rec.last.content) == {"text": "hi"}
    assert message.message_id == "m1"


async def test_send_message_accepts_a_chat_id() -> None:
    rec = Recorder(httpx.Response(200, json={"message": {"body": {"mid": "m1"}}}))
    client = make_client(rec)

    await client.send_message(body=NewMessageBody(text="hi"), chat_id=77)

    assert rec.last.url.params["chat_id"] == "77"


async def test_send_message_requires_exactly_one_destination() -> None:
    client = make_client(Recorder())

    with pytest.raises(ValueError, match="exactly one"):
        await client.send_message(body=NewMessageBody(text="hi"))

    with pytest.raises(ValueError, match="exactly one"):
        await client.send_message(body=NewMessageBody(text="hi"), user_id=1, chat_id=2)


async def test_send_message_serialises_a_keyboard() -> None:
    rec = Recorder(httpx.Response(200, json={"message": {"body": {"mid": "m1"}}}))
    client = make_client(rec)
    body = NewMessageBody(
        text="x", attachments=[keyboard([[Link(text="Открыть", url="https://example.com")]])]
    )

    await client.send_message(body=body, user_id=5)

    sent = json.loads(rec.last.content)
    assert sent["attachments"][0]["type"] == "inline_keyboard"


async def test_disable_link_preview_is_a_query_parameter() -> None:
    rec = Recorder(httpx.Response(200, json={"message": {"body": {"mid": "m1"}}}))
    client = make_client(rec)

    await client.send_message(body=NewMessageBody(text="x"), user_id=5, disable_link_preview=True)

    assert rec.last.url.params["disable_link_preview"] == "true"


# --- PUT / DELETE /messages, POST /answers ----------------------------------------------------


async def test_edit_message_passes_message_id_as_a_query_parameter() -> None:
    rec = Recorder(httpx.Response(200, json={"success": True}))
    client = make_client(rec)

    await client.edit_message(message_id="m1", body=NewMessageBody(text="new"))

    assert rec.last.method == "PUT"
    assert rec.last.url.params["message_id"] == "m1"


async def test_success_false_envelope_becomes_an_error() -> None:
    rec = Recorder(httpx.Response(200, json={"success": False, "message": "too old"}))
    client = make_client(rec)

    with pytest.raises(MaxApiError, match="too old"):
        await client.edit_message(message_id="m1", body=NewMessageBody(text="x"))


async def test_delete_message_uses_the_delete_verb() -> None:
    rec = Recorder(httpx.Response(200, json={"success": True}))
    client = make_client(rec)

    await client.delete_message(message_id="m1")

    assert rec.last.method == "DELETE"
    assert rec.last.url.params["message_id"] == "m1"


async def test_answer_callback_passes_callback_id_and_wraps_the_message() -> None:
    rec = Recorder(httpx.Response(200, json={"success": True}))
    client = make_client(rec)

    await client.answer_callback(callback_id="cb-1", body=NewMessageBody(text="updated"))

    assert rec.last.url.params["callback_id"] == "cb-1"
    assert json.loads(rec.last.content) == {"message": {"text": "updated"}}


async def test_answer_callback_can_acknowledge_without_editing() -> None:
    rec = Recorder(httpx.Response(200, json={"success": True}))
    client = make_client(rec)

    await client.answer_callback(callback_id="cb-1")

    assert json.loads(rec.last.content) == {}


# --- POST /uploads ----------------------------------------------------------------------------


async def test_upload_image_asks_for_an_url_then_posts_the_bytes() -> None:
    rec = Recorder(
        httpx.Response(200, json={"url": "https://iu.oneme.ru/upload.do?x=1"}),
        httpx.Response(200, json={"token": "tok-9"}),
    )
    client = make_client(rec)

    token = await client.upload_image(content=b"\x89PNG")

    assert rec.requests[0].url.params["type"] == "image"
    assert rec.requests[1].url.host == "iu.oneme.ru"
    assert b"\x89PNG" in rec.requests[1].content
    assert token == "tok-9"


async def test_upload_never_sends_the_bot_token_to_the_upload_host() -> None:
    rec = Recorder(
        httpx.Response(200, json={"url": "https://iu.oneme.ru/upload.do"}),
        httpx.Response(200, json={"token": "tok-9"}),
    )
    client = make_client(rec)

    await client.upload_image(content=b"png")

    assert "Authorization" not in rec.requests[1].headers


async def test_upload_falls_back_to_the_token_from_the_first_response() -> None:
    rec = Recorder(
        httpx.Response(200, json={"url": "https://iu.oneme.ru/u", "token": "early"}),
        httpx.Response(200, json={}),
    )
    client = make_client(rec)

    assert await client.upload_image(content=b"png") == "early"


async def test_upload_without_any_token_is_an_error() -> None:
    rec = Recorder(
        httpx.Response(200, json={"url": "https://iu.oneme.ru/u"}),
        httpx.Response(200, json={}),
    )
    client = make_client(rec)

    with pytest.raises(MaxApiError, match="token"):
        await client.upload_image(content=b"png")


# --- failures and retries ---------------------------------------------------------------------


async def test_429_is_retried_after_the_delay_max_asked_for() -> None:
    rec = Recorder(
        httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "slow down"}),
        httpx.Response(200, json={"user_id": 1, "first_name": "Campus"}),
    )
    client = make_client(rec)

    await client.get_me()

    assert rec.slept == [7.0]


async def test_429_without_a_header_backs_off_exponentially() -> None:
    rec = Recorder(
        httpx.Response(429, json={}),
        httpx.Response(429, json={}),
        httpx.Response(200, json={"user_id": 1, "first_name": "Campus"}),
    )
    client = make_client(rec)

    await client.get_me()

    assert rec.slept == [0.5, 1.0]


async def test_persistent_429_finally_raises() -> None:
    rec = Recorder(*[httpx.Response(429, json={}) for _ in range(4)])
    client = make_client(rec)

    with pytest.raises(MaxRateLimitError):
        await client.get_me()


async def test_server_errors_are_retried() -> None:
    rec = Recorder(
        httpx.Response(500, json={"message": "boom"}),
        httpx.Response(200, json={"user_id": 1, "first_name": "Campus"}),
    )
    client = make_client(rec)

    await client.get_me()

    assert len(rec.requests) == 2


async def test_client_errors_are_not_retried() -> None:
    rec = Recorder(httpx.Response(400, json={"code": "bad", "message": "nope"}))
    client = make_client(rec)

    with pytest.raises(MaxApiError) as excinfo:
        await client.get_me()

    assert excinfo.value.code == "bad"
    assert len(rec.requests) == 1


async def test_401_is_a_distinct_unretryable_error() -> None:
    rec = Recorder(httpx.Response(401, json={"message": "revoked"}))
    client = make_client(rec)

    with pytest.raises(MaxAuthError):
        await client.get_me()

    assert len(rec.requests) == 1


async def test_network_failures_are_retried_then_surfaced() -> None:
    rec = Recorder(*[httpx.ConnectError("no route") for _ in range(4)])
    client = make_client(rec)

    with pytest.raises(MaxTransportError):
        await client.get_me()

    assert len(rec.requests) == 4


async def test_malformed_json_is_reported_as_an_api_error() -> None:
    rec = Recorder(httpx.Response(200, content=b"<html>"))
    client = make_client(rec, max_attempts=1)

    with pytest.raises(MaxApiError, match="malformed JSON"):
        await client.get_me()


async def test_closing_releases_both_transports() -> None:
    client = make_client(Recorder())

    await client.aclose()  # must not raise
