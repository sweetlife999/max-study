"""Chat journeys through real domain services and PostgreSQL, with MAX faked."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from campus.bot.handlers.core import Handler
from campus.db.models import OutboxMessage
from campus.domain.errors import NotEventOwnerError, TooManyAttemptsError
from campus.max.fake import FakeMaxClient
from campus.max.types import BotStartedUpdate, Callback, MessageCallbackUpdate
from campus.max.types import User as MaxUser


def make_handler(world, user):
    client = FakeMaxClient()
    return Handler(world.session, world.config, world.clock, client, user.max_user_id), client


async def test_invite_survives_consent_and_handler_restart(world):
    user = await world.user(consent=False)
    invite = await world.organizers.create_invite()
    handler, client = make_handler(world, user)
    await handler.handle(
        BotStartedUpdate(
            chat_id=42, user=MaxUser(user_id=user.max_user_id), payload=f"org_{invite.token}"
        ),
        MaxUser(user_id=user.max_user_id),
    )
    assert not await world.organizers.is_organizer(user.id)
    handler, client = make_handler(world, user)
    await handler.handle(
        MessageCallbackUpdate(
            callback=Callback(
                callback_id="consent", user=MaxUser(user_id=user.max_user_id), payload="consent"
            )
        ),
        MaxUser(user_id=user.max_user_id),
    )
    assert await world.organizers.is_organizer(user.id)
    assert user.consent_at is not None
    assert any("Приглашение принято" in message.text for message in client.sent)
    assert await world.kv.get(handler.state_key) is None


async def test_event_payload_resumes_and_rsvp_can_be_cancelled(world):
    organizer = await world.organizer()
    event = await world.event(organizer=organizer)
    user = await world.user(consent=False)
    handler, client = make_handler(world, user)
    await handler.start(user, f"ev_{event.id}")
    await handler.action(user, "consent")
    assert any(event.title in message.text for message in client.sent)
    await handler.action(user, f"rsvp:{event.id}")
    assert await world.rsvps.has(user.id, event.id)
    await handler.action(user, f"unrsvp:{event.id}")
    assert not await world.rsvps.has(user.id, event.id)


async def test_onboarding_manual_completion_and_language(world):
    user = await world.user()
    handler, client = make_handler(world, user)
    step = next(step for step in world.config.university.onboarding_steps if step.type == "manual")
    await handler.action(user, f"complete:{step.key}")
    assert step.key in await world.onboarding.done_step_keys(user.id)
    await handler.action(user, "lang:en")
    assert user.lang == "en"
    assert client.last_sent().text == "What would you like to do?"


async def test_onboarding_event_steps_offer_matching_events_after_manual_step(world):
    organizer = await world.organizer()
    tour = await world.event(organizer=organizer, kind="campus_tour", title="Экскурсия")
    linked = await world.event(
        organizer=organizer,
        kind="volunteering",
        onboarding_step="campus_tour",
        title="Зачётная экскурсия",
    )
    await world.event(organizer=organizer, kind="club", title="Другой клуб")
    user = await world.user()
    handler, client = make_handler(world, user)

    await handler.action(user, "complete:join_group_chat")
    progress = client.last_sent().body.model_dump_json()
    assert "step_events:campus_tour" in progress
    assert "complete:join_group_chat" not in progress
    assert "menu" in progress
    assert "отметки на подходящем событии" in client.last_sent().text

    await handler.action(user, "step_events:campus_tour")
    listing = client.last_sent().body.model_dump_json()
    assert f"event:{tour.id}" in listing
    assert f"event:{linked.id}" in listing
    assert "Другой клуб" not in listing

    await handler.action(user, f"event:{tour.id}")
    card = client.last_sent().body.model_dump_json()
    assert '"type":"open_app"' in card
    assert '"web_app":"campus_bot"' in card
    assert f'"payload":"ev_{tour.id}"' in card


async def test_organizer_qr_start_stop_and_owner_guard(world):
    organizer = await world.organizer()
    event = await world.event(organizer=organizer)
    handler, _ = make_handler(world, organizer)
    await handler.action(organizer, f"checkin_open:{event.id}")
    await handler.action(organizer, f"qr_start:{event.id}")
    assert await world.qr_displays.active_for(organizer_id=organizer.id, event_id=event.id)
    stranger = await world.organizer()
    other, _ = make_handler(world, stranger)
    with pytest.raises(NotEventOwnerError):
        await other.action(stranger, f"qr_stop:{event.id}")
    await handler.action(organizer, f"qr_stop:{event.id}")
    assert await world.qr_displays.active_for(organizer_id=organizer.id, event_id=event.id) is None
    await handler.action(organizer, f"checkin_close:{event.id}")
    assert not event.checkin_open


async def test_manual_code_and_repeat_are_idempotent(world):
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, checkin_open=True, starts_in=timedelta())
    user = await world.user()
    handler, client = make_handler(world, user)
    code = world.events.current_code(event).code
    await handler.action(user, "code")
    await handler.checkin(user, code)
    assert "подтверждена" in client.last_sent().text
    await handler.checkin(user, code)
    assert "уже отмечены" in client.last_sent().text
    assert await world.kv.get(handler.state_key) is None


async def test_ambiguous_code_selection_does_not_expose_code_in_callback(world, monkeypatch):
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, checkin_open=True, starts_in=timedelta())
    second = await world.event(organizer=organizer, checkin_open=True, starts_in=timedelta())
    user = await world.user()
    handler, client = make_handler(world, user)
    from campus.domain import codes

    monkeypatch.setattr(codes, "classify_code", lambda *args, **kwargs: "valid")
    await handler.checkin(user, "123456")
    state = await world.kv.get(handler.state_key)
    assert set(state["event_ids"]) == {event.id, second.id}
    for message in client.sent:
        assert "123456" not in message.body.model_dump_json()
    await handler.action(user, f"choose:{second.id}")
    assert (await world.events.view(second, viewer=user)).checked_in
    assert not (await world.events.view(event, viewer=user)).checked_in


async def test_malformed_manual_codes_count_toward_rate_limit(world):
    user = await world.user()
    handler, _ = make_handler(world, user)
    from campus.domain.errors import InvalidCodeError

    for _ in range(10):
        with pytest.raises(InvalidCodeError):
            await handler.checkin(user, "not-a-code")
    with pytest.raises(TooManyAttemptsError):
        await handler.checkin(user, "123456")


async def test_chat_commands_code_mode_and_event_lists(world):
    from campus.max.types import Message, MessageBody, MessageCreatedUpdate

    user = await world.user()
    handler, client = make_handler(world, user)
    actor = MaxUser(user_id=user.max_user_id)

    async def say(text):
        await handler.handle(
            MessageCreatedUpdate(
                message=Message(sender=actor, body=MessageBody(mid="m", text=text))
            ),
            actor,
        )

    await say("/start")
    await say("/menu")
    await say("hello")
    await handler.action(user, "events")
    assert client.last_sent().text == "Событий пока нет."
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, checkin_open=True, starts_in=timedelta())
    await handler.action(user, "events")
    assert event.title in client.last_sent().text
    await handler.action(user, f"event:{event.id}")
    await handler.action(user, "language")
    await handler.action(user, "onboarding")
    await handler.action(user, "code")
    await say(world.events.current_code(event).code)
    assert "подтверждена" in client.last_sent().text
    await handler.action(user, "unrecognized")
    own_handler, own_client = make_handler(world, organizer)
    await own_handler.action(organizer, "my_events")
    # The list is one message with a button per event; the organizer's controls live on the
    # card, so that twenty-five events cost one message rather than twenty-five.
    listing = own_client.last_sent().body.model_dump_json()
    assert f"event:{event.id}" in listing
    assert "qr_start:" not in listing
    await own_handler.action(organizer, f"event:{event.id}")
    assert "qr_start:" in own_client.last_sent().body.model_dump_json()


async def test_language_before_consent_keeps_pending_invite(world):
    user = await world.user(consent=False)
    invite = await world.organizers.create_invite()
    handler, client = make_handler(world, user)
    await handler.start(user, f"org_{invite.token}")
    await handler.action(user, "lang:en")
    assert "Data processing consent" in client.last_sent().text
    await handler.action(user, "consent")
    assert await world.organizers.is_organizer(user.id)


async def test_unrelated_ambiguous_selection_cannot_check_in(world):
    from campus.domain.errors import InvalidCodeError

    user = await world.user()
    handler, _ = make_handler(world, user)
    with pytest.raises(InvalidCodeError):
        await handler.action(user, "choose:99")


async def test_stray_text_leaves_code_mode_without_spending_an_attempt(world):
    """§8: the *next six digits* are a code. A stray word is not a guess (§5 allows ten)."""
    from campus.max.types import Message, MessageBody, MessageCreatedUpdate

    user = await world.user()
    handler, client = make_handler(world, user)
    actor = MaxUser(user_id=user.max_user_id)
    await handler.action(user, "code")

    for _ in range(12):
        await handler.handle(
            MessageCreatedUpdate(
                message=Message(sender=actor, body=MessageBody(mid="m", text="привет"))
            ),
            actor,
        )

    assert await world.checkins.recent_attempts(user.id) == 0
    # The mode is gone, so the user is back in the menu rather than trapped in code entry.
    assert client.last_sent().text == "Что хотите сделать?"


async def test_a_chat_checkin_is_confirmed_once(world):
    """The domain queues a confirmation for the outbox; answering in chat must cancel it."""
    organizer = await world.organizer()
    event = await world.event(organizer=organizer, checkin_open=True, starts_in=timedelta())
    user = await world.user()
    handler, client = make_handler(world, user)

    await handler.checkin(user, world.events.current_code(event).code)

    assert "подтверждена" in client.last_sent().text
    pending = (
        (
            await world.session.execute(
                select(OutboxMessage.kind).where(OutboxMessage.status == "pending")
            )
        )
        .scalars()
        .all()
    )
    # The confirmation was answered in chat, so its queued twin is gone. step_completed was
    # never sent from here and is still the outbox's to deliver.
    assert "checkin_confirmed" not in pending
    assert "step_completed" in pending


async def test_expired_conversation_state_is_forgotten(world):
    """An invitation token left by someone who never consented must not live in kv forever."""
    user = await world.user(consent=False)
    invite = await world.organizers.create_invite()
    handler, _ = make_handler(world, user)
    await handler.start(user, f"org_{invite.token}")

    world.clock.advance(timedelta(hours=2))

    assert await handler.state() == {}
    assert await world.kv.get(handler.state_key) is None
