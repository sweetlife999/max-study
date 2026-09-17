"""Rotating check-in codes (ARCHITECTURE.md §5).

window = floor(unix_time / STEP)
digest = HMAC-SHA256(qr_seed, b"campus-checkin:" + event_id (8 bytes BE) + window (8 bytes BE))
code   = 6 digits, RFC 4226 dynamic truncation of digest, zero padded.

Accepted windows: [window - tolerance, window]; future windows are never accepted.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

SEED_BYTES = 32
CODE_DIGITS = 6
_CODE_MODULO = 10**CODE_DIGITS
_MESSAGE_PREFIX = b"campus-checkin:"
_UINT64_MAX = 2**64 - 1
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class CodeWindow:
    code: str
    window: int
    window_started_at: datetime
    expires_at: datetime


def new_seed() -> bytes:
    """A fresh per-event secret. It must never leave the backend."""
    return secrets.token_bytes(SEED_BYTES)


def dynamic_truncate(digest: bytes) -> str:
    """RFC 4226 §5.3 dynamic truncation to a zero-padded 6-digit string."""
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(binary % _CODE_MODULO).zfill(CODE_DIGITS)


def _check_uint64(name: str, value: int) -> None:
    if not 0 <= value <= _UINT64_MAX:
        msg = f"{name} must fit into an unsigned 64-bit integer"
        raise ValueError(msg)


def compute_code(seed: bytes, event_id: int, window: int) -> str:
    if len(seed) != SEED_BYTES:
        msg = f"seed must be exactly {SEED_BYTES} bytes"
        raise ValueError(msg)
    _check_uint64("event_id", event_id)
    _check_uint64("window", window)
    message = _MESSAGE_PREFIX + event_id.to_bytes(8, "big") + window.to_bytes(8, "big")
    digest = hmac.new(seed, message, hashlib.sha256).digest()
    return dynamic_truncate(digest)


def _step(step_seconds: int) -> timedelta:
    if step_seconds <= 0:
        msg = "step_seconds must be positive"
        raise ValueError(msg)
    return timedelta(seconds=step_seconds)


def window_for(now: datetime, step_seconds: int) -> int:
    """Exact integer floor(unix_time / step), without float rounding."""
    if now.tzinfo is None or now.utcoffset() is None:
        msg = "now must be timezone-aware"
        raise ValueError(msg)
    return (now - _EPOCH) // _step(step_seconds)


def window_start(window: int, step_seconds: int) -> datetime:
    return _EPOCH + window * _step(step_seconds)


def current_code(seed: bytes, event_id: int, now: datetime, step_seconds: int) -> CodeWindow:
    window = window_for(now, step_seconds)
    started = window_start(window, step_seconds)
    return CodeWindow(
        code=compute_code(seed, event_id, window),
        window=window,
        window_started_at=started,
        expires_at=started + _step(step_seconds),
    )


def is_well_formed(code: str) -> bool:
    return len(code) == CODE_DIGITS and code.isascii() and code.isdigit()


def verify_code(
    seed: bytes,
    event_id: int,
    code: str,
    *,
    now: datetime,
    step_seconds: int,
    tolerance_steps: int,
) -> bool:
    """Constant-time check of ``code`` against the current and previous windows."""
    if tolerance_steps < 0:
        msg = "tolerance_steps must not be negative"
        raise ValueError(msg)
    current = window_for(now, step_seconds)
    if not is_well_formed(code):
        return False
    candidate = code.encode("ascii")
    matched = False
    for window in range(max(0, current - tolerance_steps), current + 1):
        expected = compute_code(seed, event_id, window).encode("ascii")
        # No early exit: every accepted window is compared.
        matched = hmac.compare_digest(expected, candidate) | matched
    return matched
