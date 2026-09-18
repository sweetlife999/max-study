"""initData verification against dev.max.ru/docs/webapps/validation."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest

from campus.domain.errors import InvalidInitDataError
from campus.max.initdata import (
    launch_params,
    secret_key,
    signature,
    verify_init_data,
)

BOT_TOKEN = "test-bot-token"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
TTL = 86400

USER = {
    "id": 67890,
    "first_name": "Max",
    "last_name": "User",
    "username": None,
    "language_code": "ru",
    "photo_url": None,
}


def sign(fields: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    """Build a signed init-data string exactly as the documentation describes."""
    pairs = sorted(fields.items())
    params = "\n".join(f"{key}={value}" for key, value in pairs)
    digest = hmac.new(
        hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest(),
        params.encode(),
        hashlib.sha256,
    ).hexdigest()
    encoded = "&".join(f"{key}={quote(value, safe='')}" for key, value in pairs)
    return f"{encoded}&hash={digest}"


def valid_fields(**overrides: str) -> dict[str, str]:
    fields = {
        "auth_date": str(int(NOW.timestamp())),
        "chat": json.dumps({"id": 12345, "type": "DIALOG"}),
        "query_id": "4c0ab423-342b-4e45-aea4-2747dbc500cd",
        "user": json.dumps(USER),
    }
    fields.update(overrides)
    return fields


def verify(init_data: str, *, now: datetime = NOW, ttl: int = TTL):
    return verify_init_data(init_data, bot_token=BOT_TOKEN, now=now, ttl_seconds=ttl)


# --- the documented algorithm -----------------------------------------------------------------


def test_secret_key_signs_the_token_with_the_literal_salt() -> None:
    # The salt is the HMAC *key* and the bot token is the message, not the other way round.
    expected = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()

    assert secret_key(BOT_TOKEN) == expected


def test_launch_params_are_sorted_and_newline_joined_without_hash() -> None:
    pairs = [("user", "u"), ("auth_date", "1"), ("hash", "deadbeef"), ("chat", "c")]

    assert launch_params(pairs) == "auth_date=1\nchat=c\nuser=u"


def test_signature_matches_the_documented_example_shape() -> None:
    params = "auth_date=1771409719\nip=192.168.0.1"

    assert (
        signature(BOT_TOKEN, params)
        == hmac.new(secret_key(BOT_TOKEN), params.encode(), hashlib.sha256).hexdigest()
    )


def test_valid_init_data_is_accepted() -> None:
    result = verify(sign(valid_fields()))

    assert result.max_user_id == USER["id"]
    assert result.user.first_name == "Max"
    assert result.user.language_code == "ru"
    assert result.chat_id == 12345
    assert result.chat_type == "dialog"
    assert result.auth_date == NOW


def test_values_are_url_decoded_before_signing() -> None:
    # A value with characters that must be percent-encoded still verifies.
    result = verify(sign(valid_fields(start_param="ci_7_123456")))

    assert result.start_param == "ci_7_123456"


def test_start_param_is_exposed_for_deep_link_routing() -> None:
    result = verify(sign(valid_fields(start_param="ci_42_000123")))

    assert result.start_param == "ci_42_000123"


# --- rejection --------------------------------------------------------------------------------


def test_forged_signature_is_rejected() -> None:
    init_data = sign(valid_fields())
    forged = init_data[: -len("deadbeef")] + "deadbeef"

    with pytest.raises(InvalidInitDataError):
        verify(forged)


def test_signature_from_another_bot_token_is_rejected() -> None:
    init_data = sign(valid_fields(), token="someone-elses-token")

    with pytest.raises(InvalidInitDataError):
        verify(init_data)


def test_tampering_with_a_field_invalidates_the_signature() -> None:
    fields = valid_fields()
    init_data = sign(fields)
    tampered = init_data.replace(quote(fields["query_id"], safe=""), "spoofed")

    with pytest.raises(InvalidInitDataError):
        verify(tampered)


def test_expired_auth_date_is_rejected() -> None:
    init_data = sign(valid_fields())

    with pytest.raises(InvalidInitDataError):
        verify(init_data, now=NOW + timedelta(seconds=TTL + 1))


def test_auth_date_exactly_at_the_ttl_boundary_is_accepted() -> None:
    init_data = sign(valid_fields())

    assert verify(init_data, now=NOW + timedelta(seconds=TTL)).auth_date == NOW


def test_auth_date_far_in_the_future_is_rejected() -> None:
    init_data = sign(valid_fields())

    with pytest.raises(InvalidInitDataError):
        verify(init_data, now=NOW - timedelta(hours=1))


def test_small_clock_skew_is_tolerated() -> None:
    init_data = sign(valid_fields())

    assert verify(init_data, now=NOW - timedelta(minutes=1)) is not None


def test_missing_hash_is_rejected() -> None:
    fields = valid_fields()
    encoded = "&".join(f"{key}={quote(value, safe='')}" for key, value in sorted(fields.items()))

    with pytest.raises(InvalidInitDataError):
        verify(encoded)


def test_duplicate_hash_is_rejected() -> None:
    init_data = sign(valid_fields())

    with pytest.raises(InvalidInitDataError):
        verify(f"{init_data}&hash=deadbeef")


def test_duplicate_parameter_is_rejected() -> None:
    init_data = sign(valid_fields())

    with pytest.raises(InvalidInitDataError):
        verify(f"{init_data}&auth_date=1")


def test_missing_auth_date_is_rejected() -> None:
    fields = {"user": json.dumps(USER)}

    with pytest.raises(InvalidInitDataError):
        verify(sign(fields))


def test_non_numeric_auth_date_is_rejected() -> None:
    with pytest.raises(InvalidInitDataError):
        verify(sign(valid_fields(auth_date="yesterday")))


def test_missing_user_is_rejected() -> None:
    fields = {"auth_date": str(int(NOW.timestamp()))}

    with pytest.raises(InvalidInitDataError):
        verify(sign(fields))


def test_user_without_id_is_rejected() -> None:
    with pytest.raises(InvalidInitDataError):
        verify(sign(valid_fields(user=json.dumps({"first_name": "Max"}))))


def test_malformed_user_json_is_rejected() -> None:
    with pytest.raises(InvalidInitDataError):
        verify(sign(valid_fields(user="{not json")))


def test_empty_init_data_is_rejected() -> None:
    with pytest.raises(InvalidInitDataError):
        verify("")


def test_pair_without_separator_is_rejected() -> None:
    with pytest.raises(InvalidInitDataError):
        verify("garbage")


def test_missing_bot_token_is_rejected() -> None:
    with pytest.raises(InvalidInitDataError):
        verify_init_data(sign(valid_fields()), bot_token="", now=NOW, ttl_seconds=TTL)


def test_non_positive_ttl_is_rejected() -> None:
    with pytest.raises(InvalidInitDataError):
        verify(sign(valid_fields()), ttl=0)


def test_value_containing_an_encoded_equals_sign_survives_round_trip() -> None:
    # `partition` keeps everything after the first `=`, so a value with an `=` cannot be
    # confused with a shorter one that shares its prefix.
    result = verify(sign(valid_fields(start_param="a_b")))

    assert result.start_param == "a_b"


def test_chat_is_optional() -> None:
    fields = {
        "auth_date": str(int(NOW.timestamp())),
        "user": json.dumps(USER),
    }

    result = verify(sign(fields))

    assert result.chat_id is None
    assert result.chat_type is None
