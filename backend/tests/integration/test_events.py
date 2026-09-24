"""EventService against a real database (ARCHITECTURE.md §4, §5, §7)."""

from datetime import timedelta

import pytest

from campus.domain.codes import SEED_BYTES
from campus.domain.errors import (
    CheckinClosedError,
    EventNotFoundError,
    InvalidTimeRangeError,
    NotEventOwnerError,
    UnknownEventKindError,
    UnknownOnboardingStepError,
    ValidationFailedError,
)
from tests.integration.factories import BOT_USERNAME, World

# --- creation ---------------------------------------------------------------------------------


async def test_creation_stores_the_contract_fields(world: World) -> None:
    organizer = await world.organizer()

    event = await world.event(organizer=organizer, kind="council", title="Открытый студсовет")

    assert event.id > 0
    assert event.organizer_id == organizer.id
    assert event.kind == "council"
    assert event.checkin_open is False
    assert len(event.qr_seed) == SEED_BYTES


async def test_every_event_gets_its_own_seed(world: World) -> None:
    organizer = await world.organizer()

    first = await world.event(organizer=organizer)
    second = await world.event(organizer=organizer)

    assert first.qr_seed != second.qr_seed


async def test_points_default_to_the_kind_configured_value(world: World) -> None:
    organizer = await world.organizer()

    event = await world.event(organizer=organizer, kind="volunteering")

    kind = world.config.university.event_kind("volunteering")
    assert kind is not None
    assert event.points == kind.default_points


async def test_explicit_points_win_over_the_default(world: World) -> None:
    organizer = await world.organizer()

    event = await world.event(organizer=organizer, kind="volunteering", points=3)

    assert event.points == 3


async def test_negative_points_are_rejected(world: World) -> None:
    organizer = await world.organizer()

    with pytest.raises(ValidationFailedError):
        await world.event(organizer=organizer, points=-1)


async def test_unknown_kind_is_rejected(world: World) -> None:
    organizer = await world.organizer()

    with pytest.raises(UnknownEventKindError):
        await world.event(organizer=organizer, kind="quidditch")


async def test_manual_onboarding_step_cannot_be_attached_to_an_event(world: World) -> None:
    organizer = await world.organizer()

    with pytest.raises(UnknownOnboardingStepError):
        await world.event(organizer=organizer, onboarding_step="join_group_chat")


async def test_event_kind_onboarding_step_is_accepted(world: World) -> None:
    organizer = await world.organizer()

    event = await world.event(
        organizer=organizer, kind="campus_tour", onboarding_step="campus_tour"
    )

    assert event.onboarding_step == "campus_tour"


async def test_an_event_must_end_after_it_starts(world: World) -> None:
    organizer = await world.organizer()

    with pytest.raises(InvalidTimeRangeError):
        await world.event(organizer=organizer, duration=-timedelta(hours=1))


async def test_a_naive_datetime_is_refused_at_the_boundary(world: World) -> None:
    organizer = await world.organizer()
    starts_at = world.clock.now().replace(tzinfo=None)

    with pytest.raises(ValidationFailedError):
        await world.events.create(
            organizer=organizer,
            title="Клуб",
            kind="club",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
        )


async def test_blank_title_is_refused(world: World) -> None:
    organizer = await world.organizer()

    with pytest.raises(ValidationFailedError):
        await world.event(organizer=organizer, title="   ")


async def test_overlong_title_is_refused(world: World) -> None:
    organizer = await world.organizer()

    with pytest.raises(ValidationFailedError):
        await world.event(organizer=organizer, title="я" * 201)


# --- editing ----------------------------------------------------------------------------------


async def test_update_touches_only_the_fields_passed(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, title="Клуб", kind="club")

    await world.events.update(event, title="Клуб настолок")

    assert event.title == "Клуб настолок"
    assert event.kind == "club"


async def test_update_can_clear_the_onboarding_step(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(
        organizer=organizer, kind="campus_tour", onboarding_step="campus_tour"
    )

    await world.events.update(event, onboarding_step=None)

    assert event.onboarding_step is None


async def test_update_rejects_an_unknown_kind(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, kind="club")

    with pytest.raises(UnknownEventKindError):
        await world.events.update(event, kind="quidditch")

    assert event.kind == "club"


async def test_a_rejected_update_leaves_the_event_untouched(world: World) -> None:
    """A DomainError must not leave half-applied changes behind (the api commits on one)."""
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, title="Клуб")
    original_ends_at = event.ends_at

    with pytest.raises(InvalidTimeRangeError):
        await world.events.update(event, title="Новое имя", ends_at=event.starts_at)

    assert event.title == "Клуб"
    assert event.ends_at == original_ends_at


async def test_update_can_open_check_in(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(organizer=organizer)

    await world.events.update(event, checkin_open=True)

    assert event.checkin_open is True


# --- ownership and lookup ---------------------------------------------------------------------


async def test_require_raises_for_a_missing_event(world: World) -> None:
    with pytest.raises(EventNotFoundError):
        await world.events.require(10**12)


async def test_only_the_owner_passes_the_ownership_check(world: World) -> None:
    owner = await world.organizer()
    other = await world.organizer(first_name="Другой")
    event = await world.event(organizer=owner)

    world.events.require_owner(event, owner)
    with pytest.raises(NotEventOwnerError):
        world.events.require_owner(event, other)


# --- listings ---------------------------------------------------------------------------------


async def test_upcoming_holds_everything_that_has_not_ended(world: World) -> None:
    organizer = await world.organizer()
    soon = await world.event(organizer=organizer, starts_in=timedelta(hours=1))
    running = await world.event(organizer=organizer, starts_in=-timedelta(minutes=5))
    await world.event(organizer=organizer, starts_in=-timedelta(days=3))

    upcoming = await world.events.list_by_scope("upcoming")

    assert {event.id for event in upcoming} == {soon.id, running.id}


async def test_past_holds_everything_that_has_ended(world: World) -> None:
    organizer = await world.organizer()
    await world.event(organizer=organizer, starts_in=timedelta(hours=1))
    finished = await world.event(organizer=organizer, starts_in=-timedelta(days=3))

    past = await world.events.list_by_scope("past")

    assert [event.id for event in past] == [finished.id]


async def test_upcoming_is_ordered_by_start(world: World) -> None:
    organizer = await world.organizer()
    later = await world.event(organizer=organizer, starts_in=timedelta(days=2))
    sooner = await world.event(organizer=organizer, starts_in=timedelta(hours=2))

    upcoming = await world.events.list_by_scope("upcoming")

    assert [event.id for event in upcoming] == [sooner.id, later.id]


async def test_an_unknown_scope_is_rejected(world: World) -> None:
    with pytest.raises(ValidationFailedError):
        await world.events.list_by_scope("whenever")


async def test_limit_caps_a_listing(world: World) -> None:
    organizer = await world.organizer()
    for hours in (1, 2, 3):
        await world.event(organizer=organizer, starts_in=timedelta(hours=hours))

    upcoming = await world.events.list_by_scope("upcoming", limit=2)

    assert len(upcoming) == 2


async def test_organizer_listing_shows_only_their_own(world: World) -> None:
    mine = await world.organizer()
    theirs = await world.organizer(first_name="Другой")
    my_event = await world.event(organizer=mine)
    await world.event(organizer=theirs)

    listing = await world.events.list_for_organizer(mine.id)

    assert [event.id for event in listing] == [my_event.id]


async def test_open_for_checkin_only_lists_open_events_inside_their_window(world: World) -> None:
    organizer = await world.organizer()
    open_now = await world.open_event_now(organizer=organizer)
    await world.event(organizer=organizer, starts_in=-timedelta(minutes=10))  # closed
    await world.event(organizer=organizer, starts_in=timedelta(days=2), checkin_open=True)

    listing = await world.events.list_open_for_checkin()

    assert [event.id for event in listing] == [open_now.id]


async def test_the_checkin_window_extends_thirty_minutes_either_side(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(
        organizer=organizer, starts_in=timedelta(minutes=25), checkin_open=True
    )

    listing = await world.events.list_open_for_checkin()

    assert [found.id for found in listing] == [event.id]


async def test_an_event_is_out_of_the_window_more_than_thirty_minutes_early(world: World) -> None:
    organizer = await world.organizer()
    await world.event(organizer=organizer, starts_in=timedelta(minutes=31), checkin_open=True)

    assert await world.events.list_open_for_checkin() == []


# --- codes ------------------------------------------------------------------------------------


async def test_the_current_code_matches_the_deep_link(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    view = world.events.current_code(event)

    assert view.deeplink == f"https://max.ru/{BOT_USERNAME}?startapp=ci_{event.id}_{view.code}"
    assert view.step_seconds == world.config.checkin_code_step_seconds
    assert view.expires_at - view.window_started_at == timedelta(
        seconds=world.config.checkin_code_step_seconds
    )


async def test_the_code_is_refused_while_check_in_is_closed(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(organizer=organizer)

    with pytest.raises(CheckinClosedError):
        world.events.current_code(event)


async def test_a_code_from_a_previous_window_is_refused(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    code = world.code_for(event)

    world.clock.advance(timedelta(seconds=world.config.checkin_code_step_seconds))

    assert world.events.verify_code(event, code) is False


async def test_a_code_from_the_current_window_is_accepted(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    code = world.code_for(event)
    assert world.events.verify_code(event, code) is True


async def test_another_events_code_is_refused(world: World) -> None:
    organizer = await world.organizer()
    mine = await world.open_event_now(organizer=organizer)
    other = await world.open_event_now(organizer=organizer)

    assert world.events.verify_code(mine, world.code_for(other)) is False


# --- views ------------------------------------------------------------------------------------


async def test_the_view_carries_what_the_api_promises(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.event(organizer=organizer, kind="council")

    view = await world.events.view(event, viewer=student)

    assert view.id == event.id
    assert view.kind_title == "Студсовет"
    assert view.rsvp is False
    assert view.checked_in is False
    assert view.attendees_count == 0


async def test_the_view_is_localized_for_the_viewer(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user(lang="en")
    event = await world.event(organizer=organizer, kind="council")

    view = await world.events.view(event, viewer=student)

    assert view.kind_title == "Student council"


async def test_the_view_reflects_this_viewers_rsvp_and_check_in(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    stranger = await world.user(first_name="Другая")
    event = await world.open_event_now(organizer=organizer)
    await world.rsvps.put(user=student, event=event)
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    mine = await world.events.view(event, viewer=student)
    theirs = await world.events.view(event, viewer=stranger)

    assert (mine.rsvp, mine.checked_in, mine.attendees_count) == (True, True, 1)
    assert (theirs.rsvp, theirs.checked_in, theirs.attendees_count) == (False, False, 1)


async def test_views_of_an_empty_list_is_empty(world: World) -> None:
    assert await world.events.views([]) == ()


async def test_a_kind_that_left_the_config_degrades_to_its_key(world: World) -> None:
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, kind="club")
    event.kind = "retired_kind"

    view = await world.events.view(event)

    assert view.kind_title == "retired_kind"
