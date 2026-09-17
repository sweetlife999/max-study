import hashlib
import hmac
from datetime import UTC, datetime
from unittest import mock

import pytest

from campus.domain import codes

# RFC 4226, Appendix D: HMAC-SHA1 with secret "12345678901234567890", counters 0..9.
RFC4226_SECRET = b"12345678901234567890"
RFC4226_HOTP = [
    "755224",
    "287082",
    "359152",
    "969429",
    "338314",
    "254676",
    "287922",
    "162583",
    "399871",
    "520489",
]

SEED = bytes(range(32))
EVENT_ID = 42
STEP = 10
TOLERANCE = 2


def at(unix: float) -> datetime:
    return datetime.fromtimestamp(unix, tz=UTC)


@pytest.mark.parametrize(("counter", "expected"), list(enumerate(RFC4226_HOTP)))
def test_dynamic_truncation_matches_rfc4226_vectors(counter: int, expected: str) -> None:
    digest = hmac.new(RFC4226_SECRET, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    assert codes.dynamic_truncate(digest) == expected


def test_dynamic_truncation_matches_rfc4226_section_5_4_example() -> None:
    digest = bytes.fromhex("1f8698690e02ca16618550ef7f19da8e945b555a")
    # 0x50ef7f19 = 1357872921 -> last 6 digits
    assert codes.dynamic_truncate(digest) == "872921"


def test_dynamic_truncation_keeps_leading_zeros() -> None:
    # offset = 0; bytes 0..3 = 0x00 0x00 0x00 0x07 -> 7 -> "000007"
    digest = bytes([0, 0, 0, 7]) + bytes(27) + bytes([0])
    assert codes.dynamic_truncate(digest) == "000007"


def test_dynamic_truncation_masks_the_sign_bit() -> None:
    digest = bytes([0xFF, 0xFF, 0xFF, 0xFF]) + bytes(27) + bytes([0])
    assert codes.dynamic_truncate(digest) == str(0x7FFFFFFF % 1_000_000).zfill(6)


def test_compute_code_follows_the_contract_digest_layout() -> None:
    window = 123_456
    message = b"campus-checkin:" + EVENT_ID.to_bytes(8, "big") + window.to_bytes(8, "big")
    digest = hmac.new(SEED, message, hashlib.sha256).digest()
    assert codes.compute_code(SEED, EVENT_ID, window) == codes.dynamic_truncate(digest)


def test_compute_code_is_six_ascii_digits_and_depends_on_inputs() -> None:
    code = codes.compute_code(SEED, EVENT_ID, 1)
    assert len(code) == 6
    assert code.isascii()
    assert code.isdigit()
    others = {
        codes.compute_code(SEED, EVENT_ID, 2),
        codes.compute_code(SEED, EVENT_ID + 1, 1),
        codes.compute_code(bytes(32), EVENT_ID, 1),
    }
    assert len({code, *others}) > 1


def test_compute_code_rejects_bad_seed_and_negative_values() -> None:
    with pytest.raises(ValueError, match="seed"):
        codes.compute_code(b"short", EVENT_ID, 1)
    with pytest.raises(ValueError, match="event_id"):
        codes.compute_code(SEED, -1, 1)
    with pytest.raises(ValueError, match="window"):
        codes.compute_code(SEED, EVENT_ID, -1)


def test_window_for_is_floor_of_unix_time_divided_by_step() -> None:
    assert codes.window_for(at(0), STEP) == 0
    assert codes.window_for(at(9.999), STEP) == 0
    assert codes.window_for(at(10), STEP) == 1
    assert codes.window_for(at(1_700_000_005), STEP) == 170_000_000


def test_window_for_rejects_naive_datetime_and_bad_step() -> None:
    with pytest.raises(ValueError, match="timezone"):
        codes.window_for(datetime(2026, 1, 1), STEP)  # noqa: DTZ001
    with pytest.raises(ValueError, match="step"):
        codes.window_for(at(0), 0)


def test_current_code_reports_window_bounds() -> None:
    now = at(1_700_000_007)
    current = codes.current_code(SEED, EVENT_ID, now, STEP)
    assert current.window == 170_000_000
    assert current.window_started_at == at(1_700_000_000)
    assert current.expires_at == at(1_700_000_010)
    assert current.code == codes.compute_code(SEED, EVENT_ID, 170_000_000)


def _verify(code: str, now: datetime) -> bool:
    return codes.verify_code(SEED, EVENT_ID, code, now=now, step_seconds=STEP, tolerance_steps=2)


def test_verify_accepts_current_window() -> None:
    now = at(1_700_000_000)
    assert _verify(codes.compute_code(SEED, EVENT_ID, 170_000_000), now)


def test_verify_accepts_windows_within_tolerance_and_rejects_older() -> None:
    issued = codes.compute_code(SEED, EVENT_ID, 170_000_000)
    # Last second of window w+2 still accepts code of window w.
    assert _verify(issued, at(1_700_000_029.999))
    # First second of window w+3 rejects it.
    assert not _verify(issued, at(1_700_000_030))


def test_verify_rejects_future_windows() -> None:
    now = at(1_700_000_009.999)  # still window 170_000_000
    future = codes.compute_code(SEED, EVENT_ID, 170_000_001)
    assert not _verify(future, now)


def test_verify_with_zero_tolerance_only_accepts_current_window() -> None:
    now = at(1_700_000_010)
    previous = codes.compute_code(SEED, EVENT_ID, 170_000_000)
    assert not codes.verify_code(
        SEED, EVENT_ID, previous, now=now, step_seconds=STEP, tolerance_steps=0
    )


@pytest.mark.parametrize(
    "bad", ["", "12345", "1234567", "12a456", " 123456", "１２３４５６", "-12345"]
)
def test_verify_rejects_malformed_codes(bad: str) -> None:
    assert not _verify(bad, at(1_700_000_000))


def test_verify_uses_constant_time_comparison_for_every_window() -> None:
    now = at(1_700_000_000)
    with mock.patch.object(codes.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
        _verify("000000", now)
    assert spy.call_count == TOLERANCE + 1


def test_verify_near_epoch_does_not_use_negative_windows() -> None:
    now = at(5)  # window 0; tolerance would reach -2
    assert _verify(codes.compute_code(SEED, EVENT_ID, 0), now)


def test_new_seed_is_32_random_bytes() -> None:
    first, second = codes.new_seed(), codes.new_seed()
    assert len(first) == 32
    assert first != second
