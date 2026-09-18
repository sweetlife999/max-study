"""Verification of mini-app launch data (https://dev.max.ru/docs/webapps/validation, 2026-09-18).

The documented algorithm, applied to the value of the ``WebAppData`` URL fragment parameter — the
same string the client reads from ``window.WebApp.initData``:

1. split it on ``&`` into ``key=value`` pairs; every key must occur exactly once, ``hash`` included;
2. keep the original ``hash`` and drop that pair;
3. URL-decode every value;
4. sort the pairs by key, a -> z;
5. join them as ``key=value`` with ``\\n``; call that ``launch_params``;
6. ``secret_key = HMAC-SHA256(key="WebAppData", message=BOT_TOKEN)``;
7. ``signature = hex(HMAC-SHA256(key=secret_key, message=launch_params))``;
8. accept when ``signature`` equals ``hash``.

Two deliberate readings of the specification:

* a pair is split on its **first** ``=`` only. MAX percent-encodes values, so an unencoded ``=``
  should not occur; the reference TypeScript snippet would silently truncate such a value, which
  would let two different payloads share one signature.
* the comparison uses ``hmac.compare_digest``, not ``==``.

``auth_date`` is a Unix timestamp in seconds; the caller supplies the maximum age
(``INIT_DATA_TTL_SECONDS``, §7) and the current time, so this module stays free of hidden clocks.
"""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import unquote

from campus.domain.errors import InvalidInitDataError

SECRET_KEY_SALT: Final = b"WebAppData"
HASH_FIELD: Final = "hash"
AUTH_DATE_FIELD: Final = "auth_date"
USER_FIELD: Final = "user"
CHAT_FIELD: Final = "chat"
START_PARAM_FIELD: Final = "start_param"
QUERY_ID_FIELD: Final = "query_id"
# The fragment parameter that carries the signed payload.
FRAGMENT_PARAM: Final = "WebAppData"

# A SHA-256 signature is 64 hex characters. Anything else can never equal ours, and checking the
# shape first keeps ``hmac.compare_digest`` away from input it refuses to compare: fed two str it
# raises TypeError on a non-ASCII character, which on this unauthenticated path would turn a
# forged header into a 500 instead of the 401 of §7.
HASH_HEX_LENGTH: Final = hashlib.sha256().digest_size * 2
_HASH_RE: Final = re.compile(rf"[0-9a-fA-F]{{{HASH_HEX_LENGTH}}}")


@dataclass(frozen=True, slots=True)
class InitDataUser:
    id: int
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


@dataclass(frozen=True, slots=True)
class InitData:
    """Verified launch data. Only fields this product uses are exposed."""

    user: InitDataUser
    auth_date: datetime
    query_id: str | None = None
    chat_id: int | None = None
    chat_type: str | None = None
    start_param: str | None = None

    @property
    def max_user_id(self) -> int:
        return self.user.id


def secret_key(bot_token: str) -> bytes:
    """``HMAC-SHA256("WebAppData", BOT_TOKEN)`` — the salt is the key, the token is the message."""
    return hmac.new(SECRET_KEY_SALT, bot_token.encode("utf-8"), hashlib.sha256).digest()


def _pairs(init_data: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for chunk in init_data.split("&"):
        if not chunk:
            continue
        key, separator, raw_value = chunk.partition("=")
        if not separator or not key:
            raise InvalidInitDataError("malformed key=value pair")
        pairs.append((key, unquote(raw_value)))
    if not pairs:
        raise InvalidInitDataError("empty init data")
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise InvalidInitDataError(f"duplicate parameter {key!r}")
        seen.add(key)
    return pairs


def launch_params(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(pairs) if key != HASH_FIELD)


def signature(bot_token: str, params: str) -> str:
    return hmac.new(secret_key(bot_token), params.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_init_data(
    init_data: str,
    *,
    bot_token: str,
    now: datetime,
    ttl_seconds: int,
) -> InitData:
    """Check the signature and the age, then return the parsed payload.

    Raises :class:`InvalidInitDataError` for every failure — a bad signature, a stale
    ``auth_date`` and a missing field are all indistinguishable to the caller on purpose.
    """
    if not bot_token:
        raise InvalidInitDataError("bot token is not configured")
    if not init_data:
        raise InvalidInitDataError("init data is empty")

    pairs = _pairs(init_data)
    values = dict(pairs)
    provided_hash = values.get(HASH_FIELD)
    if not provided_hash:
        raise InvalidInitDataError("hash is missing")
    if not _HASH_RE.fullmatch(provided_hash):
        # Rejecting on shape leaks nothing: no signature of ours could have had this one.
        raise InvalidInitDataError("hash is not a sha256 hex digest")

    expected = signature(bot_token, launch_params(pairs))
    # Bytes, so the comparison cannot raise; hex is case-insensitive, so an upper-case digest is
    # the same signature and not a false negative.
    if not hmac.compare_digest(expected.encode("ascii"), provided_hash.lower().encode("ascii")):
        raise InvalidInitDataError("signature mismatch")

    auth_date = _auth_date(values.get(AUTH_DATE_FIELD))
    _check_freshness(auth_date, now=now, ttl_seconds=ttl_seconds)
    chat_id, chat_type = _chat(values.get(CHAT_FIELD))
    return InitData(
        user=_user(values.get(USER_FIELD)),
        auth_date=auth_date,
        query_id=values.get(QUERY_ID_FIELD),
        chat_id=chat_id,
        chat_type=chat_type,
        start_param=values.get(START_PARAM_FIELD) or None,
    )


def _auth_date(raw: str | None) -> datetime:
    if raw is None:
        raise InvalidInitDataError("auth_date is missing")
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise InvalidInitDataError("auth_date is not an integer") from exc
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidInitDataError("auth_date is out of range") from exc


def _check_freshness(auth_date: datetime, *, now: datetime, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        raise InvalidInitDataError("ttl must be positive")
    age = now - auth_date
    if age > timedelta(seconds=ttl_seconds):
        raise InvalidInitDataError("auth_date is too old")
    # A little clock skew is normal; launch data from the far future is not.
    if age < -_MAX_CLOCK_SKEW:
        raise InvalidInitDataError("auth_date is in the future")


_MAX_CLOCK_SKEW: Final = timedelta(minutes=5)


def _json_object(raw: str | None, field: str) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise InvalidInitDataError(f"{field} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise InvalidInitDataError(f"{field} is not a JSON object")
    return parsed


def _user(raw: str | None) -> InitDataUser:
    parsed = _json_object(raw, USER_FIELD)
    if parsed is None:
        raise InvalidInitDataError("user is missing")
    user_id = parsed.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise InvalidInitDataError("user.id is missing or invalid")
    return InitDataUser(
        id=user_id,
        first_name=_text(parsed.get("first_name")) or "",
        last_name=_text(parsed.get("last_name")),
        username=_text(parsed.get("username")),
        language_code=_text(parsed.get("language_code")),
    )


def _chat(raw: str | None) -> tuple[int | None, str | None]:
    parsed = _json_object(raw, CHAT_FIELD)
    if parsed is None:
        return None, None
    chat_id = parsed.get("id")
    chat_type = _text(parsed.get("type"))
    return (chat_id if isinstance(chat_id, int) and not isinstance(chat_id, bool) else None), (
        chat_type.lower() if chat_type else None
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
