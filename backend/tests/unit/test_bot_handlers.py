from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from campus.bot.dispatcher import Dispatcher
from campus.db.models import User
from campus.domain.context import DomainConfig
from campus.max.fake import FakeMaxClient
from campus.max.types import (
    BotStartedUpdate,
    Callback,
    Message,
    MessageBody,
    MessageCallbackUpdate,
)
from campus.max.types import User as MaxUser


@pytest.fixture
def setup(university_config):
    client = FakeMaxClient()
    session = AsyncMock()
    factory = MagicMock(return_value=session)
    user = User(id=10, max_user_id=123, first_name="Ada", lang="ru", consent_at=None)
    dispatcher = Dispatcher(DomainConfig(university_config), client, factory)
    return dispatcher, client, session, user


async def test_start_separate_consent_and_persist_payload(setup):
    dispatcher, client, session, user = setup
    with (
        patch("campus.bot.handlers.core.UserService.get_or_create", AsyncMock(return_value=user)),
        patch("campus.bot.handlers.core.KeyValueService.set", AsyncMock()) as save,
    ):
        await dispatcher.dispatch(
            BotStartedUpdate(chat_id=1, user=MaxUser(user_id=123), payload="ev_42")
        )
    assert len(client.sent) == 2
    assert "согласие" in client.sent[1].text.lower()
    assert "ev_42" in str(save.call_args)
    session.commit.assert_awaited_once()


async def test_protected_callback_requires_consent_and_clears_old_buttons(setup):
    dispatcher, client, _, user = setup
    with patch("campus.bot.handlers.core.UserService.get_or_create", AsyncMock(return_value=user)):
        await dispatcher.dispatch(
            MessageCallbackUpdate(
                callback=Callback(callback_id="cb", user=MaxUser(user_id=123), payload="events")
            )
        )
    assert client.answered[0][1].to_payload() == {"attachments": []}
    assert "согласие" in client.last_sent().text.lower()


async def test_consent_callback_clears_old_keyboard_before_sending_new_one(setup):
    dispatcher, client, session, user = setup
    with (
        patch("campus.bot.handlers.core.UserService.get_or_create", AsyncMock(return_value=user)),
        patch("campus.bot.handlers.core.Handler.resume", AsyncMock()),
    ):
        await dispatcher.dispatch(
            MessageCallbackUpdate(
                callback=Callback(callback_id="cb", user=MaxUser(user_id=123), payload="consent"),
                message=Message(body=MessageBody(mid="old", text="Data processing consent")),
            )
        )

    assert user.consent_at is not None
    assert client.sent
    assert client.answered[0][1].to_payload() == {
        "text": "Data processing consent",
        "attachments": [],
    }
    delivery_calls = [
        call.method for call in client.calls if call.method in {"answer_callback", "send_message"}
    ]
    assert delivery_calls[:2] == ["answer_callback", "send_message"]
    assert client.last_sent().body.attachments is not None
    session.commit.assert_awaited_once()


async def test_callback_uses_edit_fallback_and_still_posts_new_state(setup):
    from campus.max.client import MaxApiError

    dispatcher, client, _, user = setup
    user.consent_at = object()
    client.fail_next("answer_callback", MaxApiError(400, "expired", method="POST /answers"))
    with patch("campus.bot.handlers.core.UserService.get_or_create", AsyncMock(return_value=user)):
        await dispatcher.dispatch(
            MessageCallbackUpdate(
                callback=Callback(callback_id="cb", user=MaxUser(user_id=123), payload="language"),
                message=Message(body=MessageBody(mid="old", text="Old menu")),
            )
        )
    assert client.edited["old"].to_payload() == {"text": "Old menu", "attachments": []}
    assert client.last_sent().body.attachments is not None


async def test_invalid_callback_is_localized(setup):
    dispatcher, client, _, user = setup
    user.consent_at = object()
    with patch("campus.bot.handlers.core.UserService.get_or_create", AsyncMock(return_value=user)):
        await dispatcher.dispatch(
            MessageCallbackUpdate(
                callback=Callback(callback_id="cb", user=MaxUser(user_id=123), payload="event:wat")
            )
        )
    assert "Проверьте" in client.last_sent().text


async def test_unknown_bot_and_group_updates_do_not_touch_database(setup):
    from campus.max.types import Message, MessageCreatedUpdate, Recipient, UnknownUpdate

    dispatcher, client, session, _ = setup
    await dispatcher.dispatch(UnknownUpdate(update_type="message_removed"))
    await dispatcher.dispatch(BotStartedUpdate(chat_id=1, user=MaxUser(user_id=9, is_bot=True)))
    await dispatcher.dispatch(
        MessageCreatedUpdate(
            message=Message(sender=MaxUser(user_id=123), recipient=Recipient(chat_type="chat"))
        )
    )
    await dispatcher.dispatch(MessageCreatedUpdate(message=Message()))
    session.commit.assert_not_awaited()
    assert not client.sent


async def test_domain_failure_commits_but_unexpected_failure_rolls_back(setup):
    from campus.domain.errors import TooManyAttemptsError

    dispatcher, client, session, _ = setup
    with patch(
        "campus.bot.handlers.core.Handler.handle", AsyncMock(side_effect=TooManyAttemptsError)
    ):
        await dispatcher.dispatch(BotStartedUpdate(chat_id=1, user=MaxUser(user_id=123)))
    session.commit.assert_awaited_once()
    assert "Слишком много" in client.last_sent().text
    with (
        patch("campus.bot.handlers.core.Handler.handle", AsyncMock(side_effect=RuntimeError)),
        pytest.raises(RuntimeError),
    ):
        await dispatcher.dispatch(BotStartedUpdate(chat_id=1, user=MaxUser(user_id=123)))
    session.rollback.assert_awaited_once()


async def test_long_message_is_split_with_keyboard_on_final_chunk(setup):
    from campus.bot.handlers.core import Handler
    from campus.bot.keyboards import qr_stop

    dispatcher, client, session, _ = setup
    handler = Handler(session, dispatcher.config, dispatcher.clock, client, 123)
    await handler.send("A" * 9000, qr_stop("ru", 42))
    assert [len(message.text) for message in client.sent] == [4000, 4000, 1000]
    assert client.sent[0].body.attachments is None
    assert client.sent[-1].body.attachments


@pytest.mark.parametrize("value", ["", "-1", "0", "１２", "1.5", "9" * 40])
def test_callback_identifiers_reject_invalid_or_out_of_range_values(value):
    from campus.bot.handlers.core import Handler
    from campus.domain.errors import ValidationFailedError

    with pytest.raises(ValidationFailedError):
        Handler.identifier(value)
