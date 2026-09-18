"""Structured logs that cannot leak a secret (ARCHITECTURE.md §7 privacy, §11).

"в логах api, bot и Caddy коды, initData и qr_seed маскируются" — so the masking is tested from
both directions: by field name for structured extras, and by shape for anything that ends up
inside a message string.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import pytest

from campus.domain.views import QrCodeView
from campus.logs import MASK, JsonFormatter, configure_logging, mask_text, mask_value


@pytest.fixture
def formatter() -> JsonFormatter:
    return JsonFormatter()


def render(formatter: JsonFormatter, record: logging.LogRecord) -> dict[str, Any]:
    parsed: Any = json.loads(formatter.format(record))
    assert isinstance(parsed, dict)
    return parsed


def make_record(message: str = "hello", **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="campus.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# --- the envelope ------------------------------------------------------------------------------


def test_a_record_is_one_line_of_json_with_the_standard_fields(formatter: JsonFormatter) -> None:
    payload = render(formatter, make_record("started"))

    assert payload["message"] == "started"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "campus.test"
    assert payload["timestamp"].endswith("+00:00")
    assert "\n" not in formatter.format(make_record("started"))


def test_extra_fields_are_merged_into_the_object(formatter: JsonFormatter) -> None:
    payload = render(formatter, make_record("checked in", event_id=7, method="qr"))

    assert payload["event_id"] == 7
    assert payload["method"] == "qr"


def test_an_exception_is_rendered_as_text_not_swallowed(formatter: JsonFormatter) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "campus.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    payload = render(formatter, record)

    assert "ValueError: boom" in payload["exception"]


def test_a_traceback_is_masked_as_well(formatter: JsonFormatter) -> None:
    try:
        raise ValueError("deep link ci_42_123456")
    except ValueError:
        record = logging.LogRecord(
            "campus.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    payload = render(formatter, record)

    assert "123456" not in payload["exception"]


def test_a_value_json_cannot_serialise_still_renders(formatter: JsonFormatter) -> None:
    payload = render(formatter, make_record("odd", thing=object()))

    assert isinstance(payload["thing"], str)


def test_the_repr_of_an_unserialisable_value_is_masked_too() -> None:
    """A dataclass view is not JSON; it used to reach the log as a raw, unmasked repr."""
    view = QrCodeView(
        code="123456",
        deeplink="https://max.ru/bot?startapp=ci_7_123456",
        window_started_at=datetime(2026, 9, 18, tzinfo=UTC),
        expires_at=datetime(2026, 9, 18, tzinfo=UTC),
        step_seconds=10,
    )
    rendered = JsonFormatter().format(make_record("rendered", qr=view))

    assert "123456" not in rendered
    assert MASK in rendered


def test_a_set_of_codes_cannot_slip_through_as_a_repr() -> None:
    """``set`` is not a Sequence, so it reaches json.dumps rather than the recursive mask."""
    rendered = JsonFormatter().format(make_record("odd", payload={"code": {"000111"}}))

    assert "000111" not in rendered


def test_a_sensitive_assignment_inside_free_text_is_masked() -> None:
    assert "123456" not in mask_text("QrCodeView(code='123456', step_seconds=10)")
    assert "step_seconds=10" in mask_text("QrCodeView(code='123456', step_seconds=10)")
    assert "abcd" not in mask_text("Event(qr_seed=b'abcd')")
    assert "s3cret" not in mask_text("token: s3cret")


def test_an_insensitive_assignment_survives() -> None:
    assert mask_text("event_id=42 attendees=12") == "event_id=42 attendees=12"


# --- masking by field name ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "code",
        "checkin_code",
        "qr_seed",
        "seed",
        "init_data",
        "initData",
        "x_max_init_data",
        "token",
        "bot_token",
        "access_token",
        "authorization",
        "start_param",
        "startapp",
        "secret",
        "password",
        "hash",
    ],
)
def test_a_sensitive_field_never_reaches_the_output(formatter: JsonFormatter, field: str) -> None:
    payload = render(formatter, make_record("x", **{field: "123456"}))

    assert payload[field] == MASK


def test_masking_reaches_into_nested_structures(formatter: JsonFormatter) -> None:
    payload = render(formatter, make_record("x", ctx={"outer": [{"qr_seed": "abc"}], "ok": 1}))

    assert payload["ctx"] == {"outer": [{"qr_seed": MASK}], "ok": 1}


def test_bytes_are_never_logged_verbatim() -> None:
    assert mask_value("qr_seed", b"\x00\x01") == MASK
    assert mask_value("payload", b"\x00\x01") == MASK


def test_an_innocent_field_is_left_alone() -> None:
    assert mask_value("event_id", 7) == 7
    assert mask_value("title", "Студсовет") == "Студсовет"


def test_the_field_name_check_ignores_case_and_separators() -> None:
    assert mask_value("QR-Seed", "x") == MASK
    assert mask_value("X-Max-Init-Data", "x") == MASK


# --- masking inside free text -------------------------------------------------------------------


def test_a_check_in_deep_link_keeps_the_event_but_loses_the_code() -> None:
    masked = mask_text("opening https://max.ru/bot?startapp=ci_42_123456 now")

    assert "123456" not in masked
    assert "ci_42_" in masked


def test_a_bare_start_param_is_masked() -> None:
    assert "654321" not in mask_text("start_param=ci_7_654321")


def test_launch_data_is_masked_wholesale() -> None:
    masked = mask_text("WebAppData=auth_date%3D1&hash%3Ddeadbeef&user%3D%7B%7D")

    assert "deadbeef" not in masked
    assert MASK in masked


def test_an_authorization_header_is_masked() -> None:
    assert "s3cret" not in mask_text("Authorization: s3cret")


def test_a_hash_parameter_is_masked() -> None:
    assert "deadbeef" not in mask_text("checked hash=deadbeef against ours")


def test_ordinary_text_is_untouched() -> None:
    assert mask_text("event 42 has 12 attendees") == "event 42 has 12 attendees"


def test_the_message_of_a_record_is_masked_too(formatter: JsonFormatter) -> None:
    payload = render(formatter, make_record("deep link ci_42_123456"))

    assert "123456" not in payload["message"]


# --- installation --------------------------------------------------------------------------------


def test_configure_logging_installs_exactly_one_json_handler() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging("WARNING")
        configure_logging("WARNING")

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.WARNING
    finally:
        root.handlers = before
        root.setLevel(logging.WARNING)
