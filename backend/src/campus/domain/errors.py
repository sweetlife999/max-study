"""Typed domain errors.

Every error carries a machine ``code`` (snake_case, also the i18n key ``errors.<code>``) and the
HTTP status the api maps it to. Messages for users come from i18n, never from ``str(error)``.
"""

from typing import ClassVar


class DomainError(Exception):
    code: ClassVar[str] = "domain_error"
    http_status: ClassVar[int] = 400

    def __init__(self, detail: str = "", **params: str | int) -> None:
        super().__init__(detail or self.code)
        self.detail = detail
        self.params: dict[str, str | int] = dict(params)


# --- 400 / 422: invalid input -------------------------------------------------------------


class ValidationFailedError(DomainError):
    code = "validation_failed"
    http_status = 422

    def __init__(self, field: str, detail: str = "") -> None:
        super().__init__(detail or f"invalid {field}", field=field)
        self.field = field


class UnknownEventKindError(ValidationFailedError):
    code = "unknown_event_kind"

    def __init__(self, kind: str) -> None:
        super().__init__("kind", f"unknown event kind {kind!r}")


class UnknownOnboardingStepError(ValidationFailedError):
    code = "unknown_onboarding_step"

    def __init__(self, step: str) -> None:
        super().__init__("onboarding_step", f"unknown or non event_kind step {step!r}")


class InvalidTimeRangeError(ValidationFailedError):
    code = "invalid_time_range"

    def __init__(self) -> None:
        super().__init__("ends_at", "ends_at must be after starts_at")


class UnsupportedLanguageError(ValidationFailedError):
    code = "unsupported_language"

    def __init__(self, lang: str) -> None:
        super().__init__("lang", f"unsupported language {lang!r}")


class StepNotManualError(DomainError):
    code = "step_not_manual"
    http_status = 409


# --- 401 / 403: identity and permissions ----------------------------------------------------


class InvalidInitDataError(DomainError):
    code = "invalid_init_data"
    http_status = 401


class ConsentRequiredError(DomainError):
    code = "consent_required"
    http_status = 403


class OrganizerRequiredError(DomainError):
    code = "organizer_required"
    http_status = 403


class AdminRequiredError(DomainError):
    code = "admin_required"
    http_status = 403


class NotEventOwnerError(DomainError):
    code = "not_event_owner"
    http_status = 403


class CheckinClosedError(DomainError):
    code = "checkin_closed"
    http_status = 403


# --- 404: missing ------------------------------------------------------------------------------


class UserNotFoundError(DomainError):
    code = "user_not_found"
    http_status = 404


class EventNotFoundError(DomainError):
    code = "event_not_found"
    http_status = 404


class StepNotFoundError(DomainError):
    code = "step_not_found"
    http_status = 404


class InviteNotFoundError(DomainError):
    code = "invite_not_found"
    http_status = 404


class QrDisplayNotFoundError(DomainError):
    code = "qr_display_not_found"
    http_status = 404


# --- 409: state conflicts ---------------------------------------------------------------------


class InviteExpiredError(DomainError):
    code = "invite_expired"
    http_status = 409


class InviteAlreadyUsedError(DomainError):
    code = "invite_already_used"
    http_status = 409


class EventAlreadyEndedError(DomainError):
    code = "event_already_ended"
    http_status = 409


class CheckinNotInTimeWindowError(DomainError):
    code = "checkin_not_in_time_window"
    http_status = 409


class InvalidCodeError(DomainError):
    code = "invalid_code"
    http_status = 400


# --- 429 --------------------------------------------------------------------------------------


class TooManyAttemptsError(DomainError):
    code = "too_many_attempts"
    http_status = 429


# --- programming / deployment errors (500) -----------------------------------------------------


class ConfigurationError(DomainError):
    code = "configuration_error"
    http_status = 500


def all_error_classes() -> list[type[DomainError]]:
    """Every concrete error class, used to check that i18n has a message for each code."""
    found: list[type[DomainError]] = []
    pending: list[type[DomainError]] = [DomainError]
    while pending:
        cls = pending.pop()
        found.append(cls)
        pending.extend(cls.__subclasses__())
    return found
