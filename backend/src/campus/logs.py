"""Structured JSON logging that cannot leak a secret (ARCHITECTURE.md §7, §11).

§11 asks for structured logs "без токена, initData, qr_seed и кодов", and §7 repeats it for the
check-in codes in particular. Redaction is therefore not left to whoever writes the log line:

* every structured field whose *name* looks sensitive is replaced, however deeply it is nested,
  and ``bytes`` are never rendered at all — a ``qr_seed`` has no safe representation;
* the formatted message is scanned for the shapes a secret takes in free text: a ``ci_<id>_<code>``
  deep link, a ``WebAppData`` blob, an ``Authorization`` header, a ``hash=`` parameter.

Both api and bot call :func:`configure_logging` once at start-up. Nothing here reads the
environment: the level comes from ``LOG_LEVEL`` via ``Settings`` (§10).
"""

import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

MASK: Final = "***"

# Field names that must never be rendered. Compared with separators removed and case folded, so
# "qr_seed", "QR-Seed" and "X-Max-Init-Data" all match.
SENSITIVE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "bottoken",
        "checkincode",
        "code",
        "hash",
        "initdata",
        "maxbottoken",
        "password",
        "qrseed",
        "secret",
        "secretkey",
        "seed",
        "startparam",
        "startapp",
        "token",
        "xmaxinitdata",
    }
)

# Record attributes logging puts on every record; they are not "extra" and are rendered by name.
_STANDARD_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_SEPARATORS: Final = re.compile(r"[^a-z0-9]+")

# A check-in deep link or start param: keep the event, drop the code (§7).
_CHECKIN_PAYLOAD: Final = re.compile(r"(ci_\d{1,18}_)\d{6}")
# Launch data, as a fragment parameter or a header value: everything after it is signed payload.
_LAUNCH_DATA: Final = re.compile(r"(WebAppData|X-Max-Init-Data)\s*[=:]\s*\S+", re.IGNORECASE)
_HASH_PARAM: Final = re.compile(r"\b(hash)\s*[=:]\s*[A-Za-z0-9%]+", re.IGNORECASE)
_AUTHORIZATION: Final = re.compile(r"(Authorization)\s*[=:]\s*\S+", re.IGNORECASE)
# `name=value` / `name: value` inside free text — the shape a repr takes. Anything json cannot
# serialise is logged as its repr (a frozen view, an ORM object), and a repr is free text: without
# this, `QrCodeView(code='123456', ...)` put a live check-in code in the log, and `qr_seed=b'...'`
# a seed. The value is a quoted string, a bytes literal or a run of non-delimiter characters.
# The separator tolerates a closing quote so that the JSON and SQL-parameter forms
# (`"code": "123456"`) are caught as surely as the repr form (`code='123456'`).
_ASSIGNMENT: Final = re.compile(
    r"([A-Za-z][A-Za-z0-9_.\- ]*?)([\"']?\s*[=:]\s*)(b?'[^']*'|b?\"[^\"]*\"|[^\s,;)\]}]+)"
)

_MAX_DEPTH: Final = 6


def _normalized(name: str) -> str:
    return _SEPARATORS.sub("", name.lower())


def is_sensitive(name: str) -> bool:
    return _normalized(name) in SENSITIVE_FIELDS


def _mask_assignment(match: re.Match[str]) -> str:
    name, separator, _ = match.groups()
    return f"{name}{separator}{MASK}" if is_sensitive(name.strip()) else match.group(0)


def mask_text(text: str) -> str:
    """Redact the shapes a secret takes inside a message string."""
    masked = _CHECKIN_PAYLOAD.sub(rf"\1{MASK}", text)
    masked = _LAUNCH_DATA.sub(rf"\1={MASK}", masked)
    masked = _AUTHORIZATION.sub(rf"\1: {MASK}", masked)
    masked = _HASH_PARAM.sub(rf"\1={MASK}", masked)
    return _ASSIGNMENT.sub(_mask_assignment, masked)


def mask_value(name: str, value: object, *, depth: int = 0) -> Any:
    """``value`` as it may be logged under the key ``name``.

    Bytes are always masked: the only ``bytes`` this product holds is a ``qr_seed``.
    """
    if is_sensitive(name) or isinstance(value, bytes | bytearray | memoryview):
        return MASK
    if depth >= _MAX_DEPTH:
        return MASK
    if isinstance(value, Mapping):
        return {
            str(key): mask_value(str(key), item, depth=depth + 1)
            for key, item in value.items()  # pyright: ignore[reportUnknownVariableType]
        }
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, Sequence):
        return [mask_value(name, item, depth=depth + 1) for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with every field passed through the mask."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRIBUTES or key.startswith("_"):
                continue
            payload[key] = mask_value(key, value)
        if record.exc_info is not None:
            payload["exception"] = mask_text(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = mask_text(self.formatStack(record.stack_info))
        return json.dumps(payload, ensure_ascii=False, default=_fallback)


def _fallback(value: object) -> str:
    """Anything json cannot serialise is logged as its repr — masked, never dropped silently."""
    return mask_text(repr(value))


_THIRD_PARTY_LOGGERS: Final = (
    "sqlalchemy",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "asyncpg",
    "httpx",
    "httpcore",
    "uvicorn.access",
)


def configure_logging(level: str = "INFO", *, stream: Any = None) -> None:
    """Install the JSON formatter as the process's only root handler. Safe to call twice."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
    # LOG_LEVEL is ours to set (§10), but DEBUG on the root logger would also switch on
    # SQLAlchemy's statement echo — every INSERT with its bound parameters, which is where the
    # check-in codes and qr_seeds live. These libraries never say anything we need below
    # WARNING, so they are pinned there whatever the process level is.
    for noisy in _THIRD_PARTY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)
