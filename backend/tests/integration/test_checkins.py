"""CheckinService — the security-critical path of ARCHITECTURE.md §5."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from campus.db.models import CheckinAttempt, OutboxMessage
from campus.domain.context import CHECKIN_RATE_LIMIT_ATTEMPTS, CHECKIN_RATE_LIMIT_WINDOW
from campus.domain.errors import (
    AmbiguousCodeError,
    CheckinClosedError,
    CheckinNotStartedError,
    CheckinWindowOverError,
    CodeExpiredError,
    EventNotFoundError,
    InvalidCodeError,
    TooManyAttemptsError,
    ValidationFailedError,
)
from tests.integration.factories import World

MISSING_EVENT_ID = 10**12


async def _attempts(world: World, user_id: int) -> int:
    result = await world.session.execute(
        select(func.count()).select_from(CheckinAttempt).where(CheckinAttempt.user_id == user_id)
    )
    return int(result.scalar_one())


async def _outbox_kinds(world: World, user_id: int) -> list[str]:
    result = await world.session.execute(
        select(OutboxMessage.kind)
        .where(OutboxMessage.user_id == user_id)
        .order_by(OutboxMessage.id)
    )
    return list(result.scalars().all())


# --- the happy path ---------------------------------------------------------------------------


async def test_a_valid_code_checks_the_student_in(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, points=7)

    outcome = await world.checkins.check_in(
        user=student, code=world.code_for(event), method="qr", event_id=event.id
    )

    assert outcome.already is False
    assert outcome.event.id == event.id
    assert outcome.points_total == 7


async def test_points_accumulate_over_events(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    first = await world.open_event_now(organizer=organizer, points=7)
    second = await world.open_event_now(organizer=organizer, points=5)

    await world.checkins.check_in(user=student, code=world.code_for(first), method="qr")
    outcome = await world.checkins.check_in(user=student, code=world.code_for(second), method="qr")

    assert outcome.points_total == 12


async def test_a_bare_code_finds_the_one_open_event(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)

    outcome = await world.checkins.check_in(user=student, code=world.code_for(event), method="code")

    assert outcome.event.id == event.id


async def test_a_code_from_the_previous_window_still_works(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    code = world.code_for(event)

    world.clock.advance(timedelta(seconds=world.config.checkin_code_step_seconds))
    outcome = await world.checkins.check_in(user=student, code=code, method="qr")

    assert outcome.already is False


async def test_checking_in_twice_is_idempotent(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, points=7)

    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")
    again = await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert again.already is True
    assert again.points_total == 7


async def test_a_repeat_check_in_queues_nothing_new(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)

    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")
    before = await _outbox_kinds(world, student.id)
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert await _outbox_kinds(world, student.id) == before


# --- what is refused --------------------------------------------------------------------------


async def test_an_unknown_method_is_refused(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)

    with pytest.raises(ValidationFailedError):
        await world.checkins.check_in(user=student, code=world.code_for(event), method="telepathy")


async def test_a_malformed_code_is_refused(world: World) -> None:
    student = await world.user()

    with pytest.raises(InvalidCodeError):
        await world.checkins.check_in(user=student, code="12ab56", method="code")


async def test_a_closed_event_refuses_the_check_in(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=-timedelta(minutes=10))

    with pytest.raises(CheckinClosedError):
        await world.checkins.check_in(
            user=student, code=world.code_for(event), method="qr", event_id=event.id
        )


async def test_an_event_whose_window_has_not_opened_says_so(world: World) -> None:
    """§7 keeps `checkin_not_started` and `checkin_window_over` apart; the mini-app shows both."""
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, starts_in=timedelta(hours=5), checkin_open=True)

    with pytest.raises(CheckinNotStartedError):
        await world.checkins.check_in(
            user=student, code=world.code_for(event), method="qr", event_id=event.id
        )


async def test_an_event_whose_window_has_closed_says_so(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    world.clock.advance(timedelta(hours=4))  # well past ends_at + 30 minutes

    with pytest.raises(CheckinWindowOverError):
        await world.checkins.check_in(
            user=student, code=world.code_for(event), method="qr", event_id=event.id
        )


async def test_a_wrong_code_is_refused(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)

    with pytest.raises(InvalidCodeError):
        await world.checkins.check_in(
            user=student, code=world.code_never_valid_for(event), method="qr", event_id=event.id
        )


async def test_a_code_from_a_window_just_past_the_tolerance_is_expired_not_wrong(
    world: World,
) -> None:
    """Scanning is slow sometimes; §7 tells the student to scan again rather than "wrong code"."""
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    scanned = world.code_for(event)
    world.clock.advance(timedelta(seconds=60))

    with pytest.raises(CodeExpiredError):
        await world.checkins.check_in(user=student, code=scanned, method="qr", event_id=event.id)


async def test_a_code_older_than_the_lookback_is_simply_wrong(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    scanned = world.code_for(event)
    world.clock.advance(timedelta(minutes=10))

    with pytest.raises(InvalidCodeError):
        await world.checkins.check_in(user=student, code=scanned, method="qr", event_id=event.id)


async def test_a_bare_code_that_has_only_just_gone_stale_is_expired(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    scanned = world.code_for(event)
    world.clock.advance(timedelta(seconds=60))

    with pytest.raises(CodeExpiredError):
        await world.checkins.check_in(user=student, code=scanned, method="code")


async def test_a_missing_event_is_refused(world: World) -> None:
    student = await world.user()

    with pytest.raises(EventNotFoundError):
        await world.checkins.check_in(
            user=student, code="123456", method="qr", event_id=MISSING_EVENT_ID
        )


async def test_a_bare_code_matching_nothing_is_refused(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)

    with pytest.raises(InvalidCodeError):
        await world.checkins.check_in(
            user=student, code=world.code_never_valid_for(event), method="code"
        )


async def test_a_bare_code_matching_two_events_asks_which_one(world: World) -> None:
    """Two open events whose codes collide right now (§5: offer a choice, do not guess)."""
    organizer = await world.organizer()
    student = await world.user()
    first = await world.open_event_now(organizer=organizer)
    second = await world.open_event_now(organizer=organizer)
    target = world.code_for(first)
    second.qr_seed = seed_showing(world, event_id=second.id, code=target)
    await world.session.flush()

    with pytest.raises(AmbiguousCodeError) as raised:
        await world.checkins.check_in(user=student, code=target, method="code")

    assert set(raised.value.event_ids) == {first.id, second.id}


def seed_showing(world: World, *, event_id: int, code: str) -> bytes:
    """A seed that makes ``event_id`` accept ``code`` now.

    The event id is part of the HMAC message, so two events never show the same code by
    construction; the collision the contract has to handle is searched for instead. Any of the
    accepted windows counts, which makes the search a few times cheaper than aiming at one.
    """
    from campus.domain import codes

    step = world.config.checkin_code_step_seconds
    current = codes.window_for(world.clock.now(), step)
    windows = range(current - world.config.checkin_code_tolerance_steps, current + 1)
    for attempt in range(50_000_000):
        candidate = attempt.to_bytes(codes.SEED_BYTES, "big")
        if any(codes.compute_code(candidate, event_id, window) == code for window in windows):
            return candidate
    raise AssertionError("no colliding seed found")  # pragma: no cover


# --- rate limiting ----------------------------------------------------------------------------


async def test_every_attempt_past_the_gate_is_recorded(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)

    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert await _attempts(world, student.id) == 1


async def test_a_failed_attempt_is_recorded_too(world: World) -> None:
    student = await world.user()

    with pytest.raises(InvalidCodeError):
        await world.checkins.check_in(user=student, code="000000", method="code")

    assert await _attempts(world, student.id) == 1


async def test_probing_for_events_that_do_not_exist_still_costs_an_attempt(world: World) -> None:
    """Otherwise the rate limit never fires and the endpoint becomes an event-id oracle."""
    student = await world.user()

    with pytest.raises(EventNotFoundError):
        await world.checkins.check_in(
            user=student, code="123456", method="qr", event_id=MISSING_EVENT_ID
        )

    assert await _attempts(world, student.id) == 1


async def test_a_malformed_code_costs_exactly_one_attempt(world: World) -> None:
    student = await world.user()

    with pytest.raises(InvalidCodeError):
        await world.checkins.check_in(user=student, code="nope", method="code")

    assert await _attempts(world, student.id) == 1


async def test_the_eleventh_attempt_in_ten_minutes_is_refused(world: World) -> None:
    student = await world.user()

    for _ in range(CHECKIN_RATE_LIMIT_ATTEMPTS):
        with pytest.raises(InvalidCodeError):
            await world.checkins.check_in(user=student, code="000000", method="code")

    with pytest.raises(TooManyAttemptsError):
        await world.checkins.check_in(user=student, code="000000", method="code")


async def test_a_refused_request_does_not_extend_the_window(world: World) -> None:
    student = await world.user()
    for _ in range(CHECKIN_RATE_LIMIT_ATTEMPTS):
        with pytest.raises(InvalidCodeError):
            await world.checkins.check_in(user=student, code="000000", method="code")
    with pytest.raises(TooManyAttemptsError):
        await world.checkins.check_in(user=student, code="000000", method="code")

    assert await _attempts(world, student.id) == CHECKIN_RATE_LIMIT_ATTEMPTS


async def test_the_window_drains(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, duration=timedelta(hours=4))
    for _ in range(CHECKIN_RATE_LIMIT_ATTEMPTS):
        with pytest.raises(InvalidCodeError):
            await world.checkins.check_in(user=student, code="000000", method="code")

    world.clock.advance(CHECKIN_RATE_LIMIT_WINDOW + timedelta(seconds=1))
    outcome = await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert outcome.already is False


async def test_the_limit_is_per_user(world: World) -> None:
    organizer = await world.organizer()
    exhausted = await world.user()
    other = await world.user(first_name="Другая")
    event = await world.open_event_now(organizer=organizer)
    for _ in range(CHECKIN_RATE_LIMIT_ATTEMPTS):
        with pytest.raises(InvalidCodeError):
            await world.checkins.check_in(user=exhausted, code="000000", method="code")

    outcome = await world.checkins.check_in(user=other, code=world.code_for(event), method="qr")

    assert outcome.already is False


# --- what a check-in sets in motion -----------------------------------------------------------


async def test_a_check_in_queues_a_confirmation(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    # "volunteering" is the one configured kind no onboarding step points at.
    event = await world.open_event_now(organizer=organizer, kind="volunteering")

    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert await _outbox_kinds(world, student.id) == ["checkin_confirmed"]


async def test_a_check_in_that_closes_a_step_queues_both(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, kind="campus_tour")

    outcome = await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert outcome.completed_step_key == "campus_tour"
    assert await _outbox_kinds(world, student.id) == ["checkin_confirmed", "step_completed"]


async def test_an_event_named_step_closes_that_step(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, kind="club", onboarding_step="try_club")

    outcome = await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert outcome.completed_step_key == "try_club"


async def test_a_check_in_closing_no_step_says_so(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    first = await world.open_event_now(organizer=organizer, kind="campus_tour")
    second = await world.open_event_now(organizer=organizer, kind="campus_tour")
    await world.checkins.check_in(user=student, code=world.code_for(first), method="qr")

    outcome = await world.checkins.check_in(user=student, code=world.code_for(second), method="qr")

    assert outcome.completed_step_key is None


# --- the view the api returns -----------------------------------------------------------------


async def test_the_result_view_carries_the_event_and_the_step(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, kind="campus_tour", points=5)

    result = await world.checkins.check_in_view(
        user=student, code=world.code_for(event), method="qr", event_id=event.id
    )

    assert result.event.id == event.id
    assert result.event.checked_in is True
    assert result.already is False
    assert result.points_total == 5
    assert result.completed_step is not None
    assert result.completed_step.key == "campus_tour"
    assert result.completed_step.done is True


async def test_the_result_view_speaks_the_students_language(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user(lang="en")
    event = await world.open_event_now(organizer=organizer, kind="campus_tour")

    result = await world.checkins.check_in_view(
        user=student, code=world.code_for(event), method="qr"
    )

    assert result.event.kind_title == "Campus tour"
    assert result.completed_step is not None
    assert result.completed_step.title == "Take the campus tour"
