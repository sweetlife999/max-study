"""UserService and OrganizerService against a real database."""

from datetime import timedelta

import pytest

from campus.domain.errors import (
    ConsentRequiredError,
    InviteAlreadyUsedError,
    InviteExpiredError,
    InviteNotFoundError,
    OrganizerRequiredError,
    UnsupportedLanguageError,
    UserNotFoundError,
)
from tests.integration.factories import World, make_world, next_max_user_id

# --- users ------------------------------------------------------------------------------------


async def test_first_contact_creates_the_user(world: World) -> None:
    max_user_id = next_max_user_id()

    created = await world.users.get_or_create(max_user_id=max_user_id, first_name="Аня")

    assert created.id > 0
    assert created.max_user_id == max_user_id
    assert created.consent_at is None


async def test_second_contact_returns_the_same_user(world: World) -> None:
    max_user_id = next_max_user_id()
    first = await world.users.get_or_create(max_user_id=max_user_id, first_name="Аня")

    second = await world.users.get_or_create(max_user_id=max_user_id, first_name="Аня")

    assert second.id == first.id


async def test_display_name_follows_max_but_is_never_blanked(world: World) -> None:
    max_user_id = next_max_user_id()
    await world.users.get_or_create(max_user_id=max_user_id, first_name="Аня")

    renamed = await world.users.get_or_create(max_user_id=max_user_id, first_name="Анна")
    kept = await world.users.get_or_create(max_user_id=max_user_id, first_name="")

    assert renamed.first_name == "Анна"
    assert kept.first_name == "Анна"


async def test_language_defaults_to_the_university_default(world: World) -> None:
    created = await world.users.get_or_create(max_user_id=next_max_user_id(), lang="de")

    assert created.lang == world.config.university.default_language


async def test_supported_language_is_kept(world: World) -> None:
    created = await world.users.get_or_create(max_user_id=next_max_user_id(), lang="en")

    assert created.lang == "en"


async def test_consent_is_recorded_once(world: World) -> None:
    person = await world.user(consent=False)

    await world.users.give_consent(person)
    first_moment = person.consent_at
    world.clock.advance(timedelta(hours=1))
    await world.users.give_consent(person)

    assert person.consent_at == first_moment


async def test_missing_consent_blocks_the_rest_of_the_api(world: World) -> None:
    person = await world.user(consent=False)

    with pytest.raises(ConsentRequiredError):
        world.users.require_consent(person)


async def test_language_can_be_changed_to_a_supported_one(world: World) -> None:
    person = await world.user()

    await world.users.set_language(person, "en")

    assert person.lang == "en"


async def test_unsupported_language_is_rejected(world: World) -> None:
    person = await world.user()

    with pytest.raises(UnsupportedLanguageError):
        await world.users.set_language(person, "de")


async def test_requiring_an_unknown_user_fails(world: World) -> None:
    with pytest.raises(UserNotFoundError):
        await world.users.require(10**12)


async def test_points_are_summed_from_check_ins(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer, points=7)

    await world.checkins.check_in(
        user=student, code=world.code_for(event), method="qr", event_id=event.id
    )

    assert await world.users.points(student.id) == 7


async def test_points_start_at_zero(world: World) -> None:
    student = await world.user()

    assert await world.users.points(student.id) == 0


async def test_me_reports_roles_and_the_university(world: World) -> None:
    person = await world.user(lang="en")
    admin_world = make_world(
        world.session,
        world.config.university,
        admin_max_user_ids=frozenset({person.max_user_id}),
        clock=world.clock,
    )

    view = await admin_world.users.me(person)

    assert view.is_admin is True
    assert view.is_organizer is False
    assert view.consent is True
    assert view.university.name == "University N"
    assert view.university.timezone == "Europe/Moscow"


async def test_me_uses_the_users_language_for_the_university_name(world: World) -> None:
    person = await world.user(lang="ru")

    view = await world.users.me(person)

    assert view.university.name == "Университет N"


# --- organizers -------------------------------------------------------------------------------


async def test_granting_organizer_twice_is_harmless(world: World) -> None:
    person = await world.user()

    await world.organizers.grant(user_id=person.id)
    await world.organizers.grant(user_id=person.id)

    assert await world.organizers.is_organizer(person.id)


async def test_a_student_is_not_an_organizer(world: World) -> None:
    person = await world.user()

    with pytest.raises(OrganizerRequiredError):
        await world.organizers.require_organizer(person)


async def test_invite_token_is_long_and_url_safe(world: World) -> None:
    invite = await world.organizers.create_invite()

    assert len(invite.token) >= 40  # 32 random bytes, base64url encoded
    assert invite.token.replace("-", "").replace("_", "").isalnum()


async def test_invite_deeplink_uses_the_bot_start_payload(world: World) -> None:
    invite = await world.organizers.create_invite()

    assert invite.deeplink == f"https://max.ru/campus_bot?start=org_{invite.token}"


async def test_accepting_an_invite_makes_an_organizer(world: World) -> None:
    admin = await world.user()
    invite = await world.organizers.create_invite(created_by=admin.id)
    person = await world.user()

    organizer = await world.organizers.accept_invite(token=invite.token, user=person)

    assert organizer.user_id == person.id
    assert organizer.invited_by == admin.id
    assert await world.organizers.is_organizer(person.id)


async def test_accepting_an_invite_queues_a_notification(world: World) -> None:
    invite = await world.organizers.create_invite()
    person = await world.user()

    await world.organizers.accept_invite(token=invite.token, user=person)

    assert await world.outbox.count(status="pending") == 1


async def test_an_invite_works_only_once(world: World) -> None:
    invite = await world.organizers.create_invite()
    first = await world.user()
    second = await world.user()
    await world.organizers.accept_invite(token=invite.token, user=first)

    with pytest.raises(InviteAlreadyUsedError):
        await world.organizers.accept_invite(token=invite.token, user=second)

    assert not await world.organizers.is_organizer(second.id)


async def test_an_expired_invite_is_refused(world: World) -> None:
    invite = await world.organizers.create_invite(ttl=timedelta(hours=1))
    person = await world.user()
    world.clock.advance(timedelta(hours=2))

    with pytest.raises(InviteExpiredError):
        await world.organizers.accept_invite(token=invite.token, user=person)


async def test_an_unknown_invite_is_refused(world: World) -> None:
    person = await world.user()

    with pytest.raises(InviteNotFoundError):
        await world.organizers.accept_invite(token="no-such-token", user=person)
