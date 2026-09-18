"""Typed domain errors.

Every error carries a machine ``code`` and the HTTP status the api answers with. The codes are
**not** free-form: ARCHITECTURE.md §7 fixes a closed list, the mini-app branches on those exact
strings, and :data:`API_ERROR_STATUSES` below is that list. ``tests/unit/test_error_codes.py``
holds a second, independent copy of the contract table and fails if the two drift apart.

Two fields separate concerns that a single ``code`` used to conflate:

``code``
    what goes on the wire. Several errors deliberately share one: every validation failure is
    ``validation_error``, whatever field was wrong.
``message_key``
    which ``errors.<key>`` line of the i18n bundle the user reads. It defaults to ``code`` and is
    overridden where a shared code would otherwise cost the user a precise message.

``api_visible = False`` marks the errors the api can never legitimately return — a bug or a
bot-only path. They are answered as 500 and are exempt from the closed list.
"""

from typing import ClassVar, Final

# ARCHITECTURE.md §7. A new entry here is a change to the contract and to the mini-app.
API_ERROR_STATUSES: Final[dict[str, int]] = {
    "invalid_init_data": 401,
    "consent_required": 403,
    "not_organizer": 403,
    "not_admin": 403,
    "not_owner": 403,
    "checkin_closed": 403,
    "checkin_not_started": 403,
    "checkin_window_over": 403,
    "invalid_code": 400,
    "code_expired": 400,
    "ambiguous_code": 409,
    "event_not_found": 404,
    "step_not_found": 404,
    "step_not_manual": 409,
    "invite_invalid": 404,
    "invite_used": 409,
    "invite_expired": 409,
    "rate_limited": 429,
    "validation_error": 422,
}

API_ERROR_CODES: Final[frozenset[str]] = frozenset(API_ERROR_STATUSES)

INTERNAL_ERROR_STATUS: Final[int] = 500


class DomainError(Exception):
    code: ClassVar[str] = "domain_error"
    http_status: ClassVar[int] = 400
    message_key: ClassVar[str] = "domain_error"
    api_visible: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        # A subclass that names a new ``code`` reads from the i18n bundle under that same key,
        # unless it deliberately asks for another message.
        super().__init_subclass__(**kwargs)
        if "message_key" not in cls.__dict__:
            cls.message_key = cls.code

    def __init__(self, detail: str = "", **params: str | int) -> None:
        super().__init__(detail or self.code)
        self.detail = detail
        self.params: dict[str, str | int] = dict(params)


# --- 422: invalid input -----------------------------------------------------------------------
# §7 has a single code for all of them; the subclasses exist for their messages and for callers
# that want to catch one specific failure.


class ValidationFailedError(DomainError):
    code = "validation_error"
    http_status = 422
    api_visible = True

    def __init__(self, field: str, detail: str = "") -> None:
        super().__init__(detail or f"invalid {field}", field=field)
        self.field = field


class UnknownEventKindError(ValidationFailedError):
    message_key = "unknown_event_kind"

    def __init__(self, kind: str) -> None:
        super().__init__("kind", f"unknown event kind {kind!r}")


class UnknownOnboardingStepError(ValidationFailedError):
    message_key = "unknown_onboarding_step"

    def __init__(self, step: str) -> None:
        super().__init__("onboarding_step", f"unknown or non event_kind step {step!r}")


class InvalidTimeRangeError(ValidationFailedError):
    message_key = "invalid_time_range"

    def __init__(self) -> None:
        super().__init__("ends_at", "ends_at must be after starts_at")


class UnsupportedLanguageError(ValidationFailedError):
    message_key = "unsupported_language"

    def __init__(self, lang: str) -> None:
        super().__init__("lang", f"unsupported language {lang!r}")


# --- 401 / 403: identity and permissions ------------------------------------------------------


class InvalidInitDataError(DomainError):
    code = "invalid_init_data"
    http_status = 401
    api_visible = True


class ConsentRequiredError(DomainError):
    code = "consent_required"
    http_status = 403
    api_visible = True


class OrganizerRequiredError(DomainError):
    code = "not_organizer"
    http_status = 403
    api_visible = True


class AdminRequiredError(OrganizerRequiredError):
    """§7 gives the admin-only endpoints their own code, ``not_admin``.

    It still derives from :class:`OrganizerRequiredError` so that a caller which only wants to
    know "this actor lacks the elevated role" can catch the one class; the wire code and the
    message are both admin-specific.
    """

    code = "not_admin"
    message_key = "admin_required"


class NotEventOwnerError(DomainError):
    code = "not_owner"
    http_status = 403
    api_visible = True


class CheckinClosedError(DomainError):
    """``checkin_open`` is false: the organizer has not started the check-in."""

    code = "checkin_closed"
    http_status = 403
    api_visible = True


class CheckinNotStartedError(DomainError):
    """The event's check-in window has not opened yet (§5: ``starts_at - 30 min``)."""

    code = "checkin_not_started"
    http_status = 403
    api_visible = True


class CheckinWindowOverError(DomainError):
    """The event's check-in window has closed (§5: ``ends_at + 30 min``)."""

    code = "checkin_window_over"
    http_status = 403
    api_visible = True


# --- 404: missing -----------------------------------------------------------------------------


class EventNotFoundError(DomainError):
    code = "event_not_found"
    http_status = 404
    api_visible = True


class StepNotFoundError(DomainError):
    code = "step_not_found"
    http_status = 404
    api_visible = True


class InviteNotFoundError(DomainError):
    """§7 calls an unknown token ``invite_invalid``; an expired or used one has its own code."""

    code = "invite_invalid"
    http_status = 404
    api_visible = True


# --- 409: state conflicts ---------------------------------------------------------------------


class StepNotManualError(DomainError):
    code = "step_not_manual"
    http_status = 409
    api_visible = True


class InviteExpiredError(DomainError):
    code = "invite_expired"
    http_status = 409
    api_visible = True


class InviteAlreadyUsedError(DomainError):
    code = "invite_used"
    http_status = 409
    api_visible = True


class AmbiguousCodeError(DomainError):
    """A bare code matched several open events (§5); the caller must pick one."""

    code = "ambiguous_code"
    http_status = 409
    api_visible = True

    def __init__(self, event_ids: tuple[int, ...]) -> None:
        super().__init__(f"code matches events {list(event_ids)}")
        self.event_ids = event_ids


# --- 400: the code itself ---------------------------------------------------------------------


class InvalidCodeError(DomainError):
    """Malformed, or no accepted window ever produced it."""

    code = "invalid_code"
    http_status = 400
    api_visible = True


class CodeExpiredError(DomainError):
    """Genuine, but from a window older than the tolerance (§5): tell the user to scan again."""

    code = "code_expired"
    http_status = 400
    api_visible = True


# --- 429 --------------------------------------------------------------------------------------


class TooManyAttemptsError(DomainError):
    code = "rate_limited"
    http_status = 429
    api_visible = True


# --- internal: never a 4xx answer, always a bug or a bot-only path ----------------------------


class UserNotFoundError(DomainError):
    """The api resolves its user from init data, so it can never ask for one that is missing."""

    code = "user_not_found"
    http_status = INTERNAL_ERROR_STATUS


class QrDisplayNotFoundError(DomainError):
    """Raised by the bot's QR worker; §7 exposes no endpoint that could return it."""

    code = "qr_display_not_found"
    http_status = INTERNAL_ERROR_STATUS


class ConfigurationError(DomainError):
    code = "configuration_error"
    http_status = INTERNAL_ERROR_STATUS


def all_error_classes() -> list[type[DomainError]]:
    """Every error class, used to check the closed list and the i18n bundles."""
    found: list[type[DomainError]] = []
    pending: list[type[DomainError]] = [DomainError]
    while pending:
        cls = pending.pop()
        found.append(cls)
        pending.extend(cls.__subclasses__())
    return found
