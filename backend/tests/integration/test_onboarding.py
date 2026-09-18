"""OnboardingService: progress is computed, never stored (ARCHITECTURE.md §4, §7)."""

import pytest
from sqlalchemy import select

from campus.db.models import OutboxMessage
from campus.domain.errors import StepNotFoundError, StepNotManualError
from tests.integration.factories import World

MANUAL_STEP = "join_group_chat"
TOUR_STEP = "campus_tour"


async def _step_completed_payloads(world: World, user_id: int) -> list[str]:
    result = await world.session.execute(
        select(OutboxMessage.payload)
        .where(OutboxMessage.user_id == user_id, OutboxMessage.kind == "step_completed")
        .order_by(OutboxMessage.id)
    )
    return [payload["step_key"] for payload in result.scalars().all()]


# --- progress ---------------------------------------------------------------------------------


async def test_a_new_student_has_done_nothing(world: World) -> None:
    student = await world.user()

    progress = await world.onboarding.progress(student)

    assert progress.total == len(world.config.university.onboarding_steps)
    assert progress.done_count == 0
    assert all(step.done is False for step in progress.steps)


async def test_the_steps_keep_the_configured_order(world: World) -> None:
    student = await world.user()

    progress = await world.onboarding.progress(student)

    assert [step.key for step in progress.steps] == [
        step.key for step in world.config.university.onboarding_steps
    ]


async def test_a_check_in_of_the_right_kind_closes_the_step(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, kind="campus_tour")

    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    done = await world.onboarding.done_step_keys(student.id)
    assert TOUR_STEP in done


async def test_an_event_naming_a_step_closes_that_step_whatever_its_kind(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(
        organizer=organizer, kind="volunteering", onboarding_step=TOUR_STEP
    )

    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert TOUR_STEP in await world.onboarding.done_step_keys(student.id)


async def test_a_check_in_of_another_kind_closes_nothing(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, kind="volunteering")

    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert await world.onboarding.done_step_keys(student.id) == frozenset()


async def test_progress_counts_what_is_done(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, kind="campus_tour")
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")
    await world.onboarding.complete_manual(student, MANUAL_STEP)

    progress = await world.onboarding.progress(student)

    assert progress.done_count == 2
    assert {step.key for step in progress.steps if step.done} == {TOUR_STEP, MANUAL_STEP}


async def test_progress_is_per_student(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    other = await world.user(first_name="Другая")
    event = await world.open_event_now(organizer=organizer, kind="campus_tour")
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    assert (await world.onboarding.progress(other)).done_count == 0


async def test_progress_is_localized(world: World) -> None:
    student = await world.user(lang="en")

    progress = await world.onboarding.progress(student)

    titles = {step.key: step.title for step in progress.steps}
    assert titles[MANUAL_STEP] == "Join your group chat"


async def test_an_explicit_language_overrides_the_students_own(world: World) -> None:
    student = await world.user(lang="ru")

    progress = await world.onboarding.progress(student, lang="en")

    assert progress.steps[0].title == "Join your group chat"


async def test_the_view_carries_the_step_type_and_kind(world: World) -> None:
    student = await world.user()

    progress = await world.onboarding.progress(student)

    by_key = {step.key: step for step in progress.steps}
    assert by_key[MANUAL_STEP].type == "manual"
    assert by_key[MANUAL_STEP].event_kind is None
    assert by_key[TOUR_STEP].type == "event_kind"
    assert by_key[TOUR_STEP].event_kind == "campus_tour"


# --- completing a manual step -----------------------------------------------------------------


async def test_a_manual_step_can_be_ticked(world: World) -> None:
    student = await world.user()

    view = await world.onboarding.complete_manual(student, MANUAL_STEP)

    assert view.key == MANUAL_STEP
    assert view.done is True


async def test_ticking_a_manual_step_queues_one_notification(world: World) -> None:
    student = await world.user()

    await world.onboarding.complete_manual(student, MANUAL_STEP)
    await world.onboarding.complete_manual(student, MANUAL_STEP)

    assert await _step_completed_payloads(world, student.id) == [MANUAL_STEP]


async def test_ticking_it_twice_is_not_an_error(world: World) -> None:
    student = await world.user()

    await world.onboarding.complete_manual(student, MANUAL_STEP)
    view = await world.onboarding.complete_manual(student, MANUAL_STEP)

    assert view.done is True
    assert (await world.onboarding.progress(student)).done_count == 1


async def test_an_event_kind_step_cannot_be_ticked_by_hand(world: World) -> None:
    student = await world.user()

    with pytest.raises(StepNotManualError):
        await world.onboarding.complete_manual(student, TOUR_STEP)


async def test_an_unknown_step_is_not_found(world: World) -> None:
    student = await world.user()

    with pytest.raises(StepNotFoundError):
        await world.onboarding.complete_manual(student, "learn_to_fly")


# --- single-step views ------------------------------------------------------------------------


async def test_a_single_step_view_can_be_built_by_key(world: World) -> None:
    view = world.onboarding.step_view(TOUR_STEP, lang="ru", done=True)

    assert view is not None
    assert view.title == "Пройти экскурсию по кампусу"
    assert view.done is True


async def test_an_unknown_key_has_no_view(world: World) -> None:
    assert world.onboarding.step_view("learn_to_fly", lang="ru", done=True) is None
