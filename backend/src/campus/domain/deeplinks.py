"""MAX deep links (dev.max.ru/help/deeplinks).

- bot:      https://max.ru/<botName>?start=<payload>      payload up to 128 characters
- mini-app: https://max.ru/<botName>?startapp=<payload>   payload up to 512 characters
"""

import re
from dataclasses import dataclass

from campus.domain.errors import ConfigurationError

MAX_START_PAYLOAD = 128
MAX_STARTAPP_PAYLOAD = 512
_BASE = "https://max.ru"
_USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
# Payloads we build are URL-safe by construction: no percent-encoding is needed.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
_ID_RE = re.compile(r"[1-9][0-9]{0,17}")  # fits into bigint
_CODE_RE = re.compile(r"[0-9]{6}")

INVITE_PREFIX = "org_"
EVENT_PREFIX = "ev_"
CHECKIN_PREFIX = "ci_"


@dataclass(frozen=True, slots=True)
class InvitePayload:
    token: str


@dataclass(frozen=True, slots=True)
class EventPayload:
    event_id: int


@dataclass(frozen=True, slots=True)
class CheckinStartParam:
    event_id: int
    code: str


def _username(bot_username: str | None) -> str:
    if not bot_username or not _USERNAME_RE.fullmatch(bot_username):
        msg = "MAX_BOT_USERNAME is missing or contains characters unsafe for a URL"
        raise ConfigurationError(msg)
    return bot_username


def build_start_link(bot_username: str | None, payload: str) -> str:
    if len(payload) > MAX_START_PAYLOAD:
        msg = f"start payload must be at most {MAX_START_PAYLOAD} characters"
        raise ValueError(msg)
    return f"{_BASE}/{_username(bot_username)}?start={payload}"


def build_startapp_link(bot_username: str | None, payload: str) -> str:
    if len(payload) > MAX_STARTAPP_PAYLOAD:
        msg = f"startapp payload must be at most {MAX_STARTAPP_PAYLOAD} characters"
        raise ValueError(msg)
    return f"{_BASE}/{_username(bot_username)}?startapp={payload}"


def checkin_deeplink(bot_username: str | None, event_id: int, code: str) -> str:
    return build_startapp_link(bot_username, f"{CHECKIN_PREFIX}{event_id}_{code}")


def invite_deeplink(bot_username: str | None, token: str) -> str:
    return build_start_link(bot_username, f"{INVITE_PREFIX}{token}")


def event_deeplink(bot_username: str | None, event_id: int) -> str:
    return build_start_link(bot_username, f"{EVENT_PREFIX}{event_id}")


def parse_start_payload(payload: str | None) -> InvitePayload | EventPayload | None:
    """Parse a bot ``start`` payload; anything unexpected is treated as no payload."""
    if not payload:
        return None
    if payload.startswith(INVITE_PREFIX):
        token = payload.removeprefix(INVITE_PREFIX)
        return InvitePayload(token) if _TOKEN_RE.fullmatch(token) else None
    if payload.startswith(EVENT_PREFIX):
        raw_id = payload.removeprefix(EVENT_PREFIX)
        return EventPayload(int(raw_id)) if _ID_RE.fullmatch(raw_id) else None
    return None


def parse_checkin_start_param(param: str | None) -> CheckinStartParam | None:
    if not param or not param.startswith(CHECKIN_PREFIX):
        return None
    parts = param.removeprefix(CHECKIN_PREFIX).split("_")
    if len(parts) != 2:
        return None
    raw_id, code = parts
    if not _ID_RE.fullmatch(raw_id) or not _CODE_RE.fullmatch(code):
        return None
    return CheckinStartParam(event_id=int(raw_id), code=code)
