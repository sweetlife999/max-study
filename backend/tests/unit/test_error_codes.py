"""The API error vocabulary is closed (ARCHITECTURE.md §7).

The mini-app branches on these exact strings (``web/src/lib/checkinOutcome.ts``,
``web/src/api/errors.ts``), so this test copies the contract's table verbatim rather than reading
it from the code under test. A new code may only appear here together with an edit to §7.
"""

from campus.domain.errors import (
    API_ERROR_STATUSES,
    AdminRequiredError,
    AmbiguousCodeError,
    DomainError,
    OrganizerRequiredError,
    all_error_classes,
)

# ARCHITECTURE.md §7, "Коды ошибок (закрытый список, фронт на них завязан)".
CONTRACT: dict[str, int] = {
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


def test_the_declared_vocabulary_is_exactly_the_contract() -> None:
    assert API_ERROR_STATUSES == CONTRACT


def test_every_api_visible_error_uses_a_contract_code_and_status() -> None:
    offenders = {
        cls.__name__: (cls.code, cls.http_status)
        for cls in all_error_classes()
        if cls.api_visible and CONTRACT.get(cls.code) != cls.http_status
    }

    assert offenders == {}


def test_every_contract_code_is_raisable_by_some_error() -> None:
    covered = {cls.code for cls in all_error_classes() if cls.api_visible}

    assert set(CONTRACT) - covered == set()


def test_errors_that_never_reach_the_api_are_marked_as_such() -> None:
    """An error the api cannot translate must say so, instead of inventing a code for the wire."""
    internal = {cls.__name__ for cls in all_error_classes() if not cls.api_visible}

    assert internal == {
        "DomainError",
        "ConfigurationError",
        "UserNotFoundError",
        "QrDisplayNotFoundError",
    }


def test_the_base_error_is_not_part_of_the_api_vocabulary() -> None:
    assert DomainError.code not in CONTRACT


def test_an_ambiguous_code_carries_the_events_the_caller_must_choose_from() -> None:
    error = AmbiguousCodeError((7, 9))

    assert error.event_ids == (7, 9)
    assert error.code == "ambiguous_code"
    assert error.http_status == 409


def test_an_admin_only_endpoint_has_its_own_code() -> None:
    """§7 lists `not_admin` separately: the mini-app tells the two 403s apart."""
    assert AdminRequiredError.code == "not_admin"
    assert AdminRequiredError.http_status == 403
    # The message stays admin-specific even though the class inherits from the organizer error.
    assert AdminRequiredError.message_key == "admin_required"
    assert not issubclass(OrganizerRequiredError, AdminRequiredError)
