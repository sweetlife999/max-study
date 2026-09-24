"""Everything the domain needs besides a database session.

Services take a :class:`DomainConfig` rather than the process ``Settings``, so the domain never
reads the environment and a test can build one in a line.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Self

from campus.config import UniversityConfig

if TYPE_CHECKING:  # pragma: no cover - import only needed for the adapter below
    from campus.config import Settings

# ARCHITECTURE.md §5.
CHECKIN_WINDOW_MARGIN = timedelta(minutes=30)
CHECKIN_RATE_LIMIT_ATTEMPTS = 10
CHECKIN_RATE_LIMIT_WINDOW = timedelta(minutes=10)
# A MAX-signed direct launch proves when this exact QR was opened. Keep a short allowance for
# loading the mini-app and sending the request without extending the QR code's own lifetime.
CHECKIN_QR_SUBMIT_GRACE = timedelta(seconds=30)

# §7 separates `invalid_code` from `code_expired`. This is how far back a code is still recognised
# as this event's own, only stale, so the client can tell the student to scan again. Recognition
# never makes an old code valid: only the current window is accepted (§5).
CHECKIN_CODE_EXPIRED_LOOKBACK = timedelta(minutes=2)

# ARCHITECTURE.md §8.
OUTBOX_MAX_ATTEMPTS = 5
OUTBOX_RETRY_BASE_DELAY = timedelta(seconds=30)
OUTBOX_RETRY_MAX_DELAY = timedelta(hours=1)

# The contract fixes neither an invite lifetime nor a QR display lifetime, only that invites
# expire (§4) and that a display runs until ends_at or a Stop button (§8).
DEFAULT_INVITE_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class DomainConfig:
    university: UniversityConfig
    admin_max_user_ids: frozenset[int] = frozenset()
    checkin_code_step_seconds: int = 10
    bot_username: str | None = None
    invite_ttl: timedelta = DEFAULT_INVITE_TTL

    @classmethod
    def from_settings(cls, settings: "Settings", university: UniversityConfig) -> Self:
        return cls(
            university=university,
            admin_max_user_ids=settings.admin_max_user_ids,
            checkin_code_step_seconds=settings.checkin_code_step_seconds,
            bot_username=settings.max_bot_username,
        )

    @property
    def checkin_code_expired_lookback_steps(self) -> int:
        """:data:`CHECKIN_CODE_EXPIRED_LOOKBACK` expressed in code windows, rounded up."""
        step = self.checkin_code_step_seconds
        if step <= 0:  # pragma: no cover - Settings rejects it, a hand-built config might not
            return 0
        return -(-int(CHECKIN_CODE_EXPIRED_LOOKBACK.total_seconds()) // step)

    @property
    def default_language(self) -> str:
        return self.university.default_language

    def language_or_default(self, lang: str | None) -> str:
        """The requested language when the university offers it, otherwise the default."""
        if lang and lang in self.university.languages:
            return lang
        return self.default_language
