"""MAX wire types: parsing is forgiving, serialisation is exact."""

from campus.max.types import (
    BotStartedUpdate,
    CallbackButton,
    ImageAttachment,
    LinkButton,
    Message,
    MessageCallbackUpdate,
    MessageCreatedUpdate,
    NewMessageBody,
    OpenAppButton,
    UnknownUpdate,
    UpdatesPage,
    image_from_token,
    keyboard,
    parse_update,
)


def test_unknown_fields_are_ignored() -> None:
    update = parse_update(
        {
            "update_type": "bot_started",
            "timestamp": 1,
            "chat_id": 7,
            "user": {"user_id": 5, "first_name": "Ann", "is_bot": False, "brand_new": "x"},
            "something_new": True,
        }
    )

    assert isinstance(update, BotStartedUpdate)
    assert update.user.user_id == 5


def test_bot_started_carries_the_deep_link_payload() -> None:
    update = parse_update(
        {
            "update_type": "bot_started",
            "chat_id": 7,
            "user": {"user_id": 5, "first_name": "Ann"},
            "payload": "org_abc",
        }
    )

    assert isinstance(update, BotStartedUpdate)
    assert update.payload == "org_abc"


def test_message_created_exposes_text_and_message_id() -> None:
    update = parse_update(
        {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": 5, "first_name": "Ann"},
                "recipient": {"user_id": 5, "chat_id": 7, "chat_type": "dialog"},
                "body": {"mid": "mid-1", "seq": 2, "text": "123456"},
            },
        }
    )

    assert isinstance(update, MessageCreatedUpdate)
    assert update.message.text == "123456"
    assert update.message.message_id == "mid-1"


def test_message_callback_carries_callback_id_and_payload() -> None:
    update = parse_update(
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb-1",
                "payload": "menu:onboarding",
                "user": {"user_id": 5, "first_name": "Ann"},
            },
        }
    )

    assert isinstance(update, MessageCallbackUpdate)
    assert update.callback.callback_id == "cb-1"
    assert update.callback.payload == "menu:onboarding"
    assert update.message is None


def test_unhandled_event_type_becomes_unknown_update() -> None:
    update = parse_update({"update_type": "user_added", "chat_id": 1})

    assert isinstance(update, UnknownUpdate)
    assert update.update_type == "user_added"


def test_malformed_known_event_becomes_unknown_update_instead_of_raising() -> None:
    # A single broken update must not stop the polling loop (§8).
    update = parse_update({"update_type": "message_created"})

    assert isinstance(update, UnknownUpdate)


def test_event_without_a_type_becomes_unknown_update() -> None:
    assert isinstance(parse_update({"timestamp": 1}), UnknownUpdate)


def test_updates_page_parses_updates_and_marker() -> None:
    page = UpdatesPage.from_payload(
        {
            "updates": [
                {"update_type": "bot_started", "chat_id": 1, "user": {"user_id": 2}},
                "not-an-object",
            ],
            "marker": 99,
        }
    )

    assert len(page.updates) == 1
    assert page.marker == 99


def test_updates_page_tolerates_a_missing_marker() -> None:
    page = UpdatesPage.from_payload({"updates": []})

    assert page.marker is None
    assert page.updates == ()


def test_message_text_defaults_to_empty_string() -> None:
    assert Message().text == ""
    assert Message().message_id is None


# --- outbound ---------------------------------------------------------------------------------


def test_new_message_body_omits_unset_fields() -> None:
    payload = NewMessageBody(text="hi").to_payload()

    assert payload == {"text": "hi"}


def test_empty_attachment_list_survives_serialisation() -> None:
    # MAX reads `[]` as "remove all attachments" and a missing field as "leave unchanged".
    payload = NewMessageBody(text="hi", attachments=[]).to_payload()

    assert payload["attachments"] == []


def test_keyboard_serialises_to_the_documented_shape() -> None:
    attachment = keyboard([[LinkButton(text="Открыть сайт", url="https://example.com")]])

    payload = NewMessageBody(text="x", attachments=[attachment]).to_payload()

    assert payload["attachments"] == [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "link", "text": "Открыть сайт", "url": "https://example.com"}]
                ]
            },
        }
    ]


def test_callback_button_defaults_to_the_default_intent() -> None:
    button = CallbackButton(text="Иду", payload="rsvp:1")

    assert button.model_dump(exclude_none=True) == {
        "type": "callback",
        "text": "Иду",
        "payload": "rsvp:1",
        "intent": "default",
    }


def test_open_app_button_omits_fields_it_was_not_given() -> None:
    button = OpenAppButton(text="Открыть приложение", payload="ci_1_000000")

    assert button.model_dump(exclude_none=True) == {
        "type": "open_app",
        "text": "Открыть приложение",
        "payload": "ci_1_000000",
    }


def test_image_attachment_references_an_upload_token() -> None:
    attachment: ImageAttachment = image_from_token("tok-1")

    assert NewMessageBody(attachments=[attachment]).to_payload()["attachments"] == [
        {"type": "image", "payload": {"token": "tok-1"}}
    ]
