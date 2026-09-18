"""The fake client must behave like the real one, or tests built on it prove nothing."""

import pytest

from campus.max.client import MaxClient
from campus.max.fake import FakeMaxClient, ScriptedMaxError
from campus.max.http import HttpMaxClient
from campus.max.types import NewMessageBody, UpdatesPage, parse_update


def test_both_clients_satisfy_the_protocol() -> None:
    fake: MaxClient = FakeMaxClient()
    assert isinstance(fake, MaxClient)
    assert issubclass(HttpMaxClient, MaxClient)


async def test_sent_messages_are_recorded_with_their_destination() -> None:
    client = FakeMaxClient()

    await client.send_message(body=NewMessageBody(text="привет"), user_id=42)

    assert client.last_sent().text == "привет"
    assert client.last_sent().user_id == 42


async def test_send_message_returns_a_message_with_an_id() -> None:
    client = FakeMaxClient()

    first = await client.send_message(body=NewMessageBody(text="a"), user_id=1)
    second = await client.send_message(body=NewMessageBody(text="b"), chat_id=2)

    assert first.message_id != second.message_id


async def test_send_message_enforces_one_destination_like_the_real_client() -> None:
    client = FakeMaxClient()

    with pytest.raises(ValueError, match="exactly one"):
        await client.send_message(body=NewMessageBody(text="a"))


async def test_queued_updates_are_returned_once_then_exhausted() -> None:
    client = FakeMaxClient()
    page = UpdatesPage(
        updates=(
            parse_update({"update_type": "bot_started", "chat_id": 1, "user": {"user_id": 2}}),
        ),
        marker=5,
    )
    client.queue_updates(page)

    assert (await client.get_updates()).marker == 5
    assert (await client.get_updates()).updates == ()


async def test_scripted_failure_is_raised_once() -> None:
    client = FakeMaxClient()
    client.fail_next("send_message", ScriptedMaxError("nope"))

    with pytest.raises(ScriptedMaxError):
        await client.send_message(body=NewMessageBody(text="a"), user_id=1)

    await client.send_message(body=NewMessageBody(text="a"), user_id=1)


async def test_edits_deletes_answers_and_uploads_are_recorded() -> None:
    client = FakeMaxClient()

    await client.edit_message(message_id="m1", body=NewMessageBody(text="new"))
    await client.delete_message(message_id="m2")
    await client.answer_callback(callback_id="cb", notification="ok")
    token = await client.upload_image(content=b"png")

    assert client.edited["m1"].text == "new"
    assert client.deleted == ["m2"]
    assert client.answered == [("cb", None, "ok")]
    assert client.uploaded == [b"png"]
    assert token == client.upload_token


async def test_calls_can_be_filtered_by_method() -> None:
    client = FakeMaxClient()
    await client.get_me()
    await client.get_me()
    await client.aclose()

    assert len(client.calls_to("get_me")) == 2
    assert client.closed is True


def test_last_sent_without_any_message_fails_loudly() -> None:
    with pytest.raises(AssertionError):
        FakeMaxClient().last_sent()
