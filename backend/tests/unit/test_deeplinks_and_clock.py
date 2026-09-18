from datetime import UTC, datetime, timedelta

import pytest

from campus.domain import deeplinks
from campus.domain.clock import FixedClock, SystemClock
from campus.domain.errors import ConfigurationError


def test_checkin_deeplink_format() -> None:
    link = deeplinks.checkin_deeplink("campus_bot", 17, "004211")
    assert link == "https://max.ru/campus_bot?startapp=ci_17_004211"


def test_invite_deeplink_uses_bot_start_payload() -> None:
    link = deeplinks.invite_deeplink("campus_bot", "AbC-_123")
    assert link == "https://max.ru/campus_bot?start=org_AbC-_123"


def test_event_deeplink_uses_bot_start_payload() -> None:
    assert deeplinks.event_deeplink("campus_bot", 5) == "https://max.ru/campus_bot?start=ev_5"


@pytest.mark.parametrize("username", [None, "", "bad name", "a/b", "a?b", "a#b", "a&b"])
def test_deeplinks_require_a_safe_bot_username(username: str | None) -> None:
    with pytest.raises(ConfigurationError):
        deeplinks.checkin_deeplink(username, 1, "000000")


def test_start_payload_length_limit_is_enforced() -> None:
    with pytest.raises(ValueError, match="128"):
        deeplinks.invite_deeplink("bot", "x" * 125)


def test_startapp_payload_length_limit_is_enforced() -> None:
    with pytest.raises(ValueError, match="512"):
        deeplinks.build_startapp_link("bot", "x" * 513)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("org_AbC-_123", deeplinks.InvitePayload(token="AbC-_123")),
        ("ev_42", deeplinks.EventPayload(event_id=42)),
        ("", None),
        (None, None),
        ("ev_", None),
        ("ev_-1", None),
        ("ev_0x10", None),
        ("ev_٣", None),
        ("org_", None),
        ("org_a b", None),
        ("something", None),
    ],
)
def test_parse_start_payload(payload: str | None, expected: object) -> None:
    assert deeplinks.parse_start_payload(payload) == expected


@pytest.mark.parametrize(
    ("param", "expected"),
    [
        ("ci_17_004211", deeplinks.CheckinStartParam(event_id=17, code="004211")),
        ("ci_17_04211", None),
        ("ci_x_004211", None),
        ("ci_17_004211_1", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_checkin_start_param(param: str | None, expected: object) -> None:
    assert deeplinks.parse_checkin_start_param(param) == expected


def test_fixed_clock_is_aware_and_advances() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    clock.advance(timedelta(seconds=5))
    assert clock.now() == datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    clock.set(datetime(2027, 1, 1, tzinfo=UTC))
    assert clock.now().year == 2027
    with pytest.raises(ValueError, match="timezone"):
        FixedClock(datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone"):
        clock.set(datetime(2026, 1, 1))  # noqa: DTZ001


def test_system_clock_is_utc_aware() -> None:
    assert SystemClock().now().tzinfo is UTC
