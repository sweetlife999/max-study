"""Wire types for the MAX Bot API (https://dev.max.ru/docs-api, checked 2026-09-18).

Inbound models use ``extra="ignore"``: MAX adds fields over time and an unknown one must never
break the bot. Outbound models are serialised with ``exclude_none=True`` so that "field absent"
and "field explicitly null" stay different — MAX treats ``attachments: null`` as "leave unchanged"
and ``attachments: []`` as "remove all attachments".
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- documented limits ---------------------------------------------------------------------------

MESSAGE_TEXT_LIMIT = 4000  # NewMessageBody.text: "до 4000 символов"
CALLBACK_PAYLOAD_LIMIT = 256  # callback button payload, per the MAX client libraries
LINK_BUTTON_URL_LIMIT = 2048  # "Длина ссылки ограничена 2048 символами"
KEYBOARD_MAX_ROWS = 30
KEYBOARD_MAX_BUTTONS = 210
KEYBOARD_MAX_BUTTONS_PER_ROW = 7
KEYBOARD_MAX_WIDE_BUTTONS_PER_ROW = 3  # link / open_app / request_geo_location / request_contact
UPDATES_MAX_LIMIT = 1000
UPDATES_MAX_TIMEOUT_SECONDS = 90

UploadType = Literal["image", "video", "audio", "file"]
TextFormat = Literal["markdown", "html"]
ChatType = Literal["dialog", "chat", "channel"]

UPDATE_BOT_STARTED = "bot_started"
UPDATE_MESSAGE_CREATED = "message_created"
UPDATE_MESSAGE_CALLBACK = "message_callback"


class _In(BaseModel):
    """Anything MAX sends us."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class _Out(BaseModel):
    """Anything we send to MAX."""

    model_config = ConfigDict(extra="forbid")

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


# --- inbound -------------------------------------------------------------------------------------


class User(_In):
    user_id: int
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    is_bot: bool = False
    last_activity_time: int | None = None


class BotInfo(User):
    description: str | None = None
    avatar_url: str | None = None
    full_avatar_url: str | None = None


class Recipient(_In):
    user_id: int | None = None
    chat_id: int | None = None
    chat_type: str | None = None


class MessageBody(_In):
    mid: str
    seq: int | None = None
    text: str | None = None
    attachments: tuple[Mapping[str, Any], ...] = ()


class Message(_In):
    sender: User | None = None
    recipient: Recipient | None = None
    timestamp: int | None = None
    body: MessageBody | None = None

    @property
    def message_id(self) -> str | None:
        return self.body.mid if self.body else None

    @property
    def text(self) -> str:
        return (self.body.text if self.body else None) or ""


class Callback(_In):
    timestamp: int | None = None
    callback_id: str
    payload: str | None = None
    user: User


class BaseUpdate(_In):
    # `update_type` is declared by each concrete subclass, so that the Literal discriminator
    # is not an incompatible override of a plain `str` field.
    timestamp: int | None = None


class BotStartedUpdate(BaseUpdate):
    """A user opened the dialog, possibly through a ``?start=<payload>`` deep link."""

    update_type: Literal["bot_started"] = UPDATE_BOT_STARTED
    chat_id: int
    user: User
    user_locale: str | None = None
    payload: str | None = None


class MessageCreatedUpdate(BaseUpdate):
    update_type: Literal["message_created"] = UPDATE_MESSAGE_CREATED
    message: Message
    user_locale: str | None = None


class MessageCallbackUpdate(BaseUpdate):
    update_type: Literal["message_callback"] = UPDATE_MESSAGE_CALLBACK
    callback: Callback
    # Absent when the original message was deleted before the update reached us.
    message: Message | None = None
    user_locale: str | None = None


class UnknownUpdate(BaseUpdate):
    """Any event type the bot does not handle; kept so the polling loop can skip it safely."""

    update_type: str = "unknown"


Update = BotStartedUpdate | MessageCreatedUpdate | MessageCallbackUpdate | UnknownUpdate

_UPDATE_MODELS: dict[str, type[BaseUpdate]] = {
    UPDATE_BOT_STARTED: BotStartedUpdate,
    UPDATE_MESSAGE_CREATED: MessageCreatedUpdate,
    UPDATE_MESSAGE_CALLBACK: MessageCallbackUpdate,
}


def parse_update(raw: Mapping[str, Any]) -> Update:
    """Build the update model for ``raw``; unknown or malformed events become UnknownUpdate.

    A single bad update must never stop the polling loop (ARCHITECTURE.md §8), so this never
    raises: an event we cannot parse is still an event we must skip past.
    """
    update_type = raw.get("update_type")
    model = _UPDATE_MODELS.get(update_type) if isinstance(update_type, str) else None
    if model is None:
        return UnknownUpdate.model_validate({**raw, "update_type": update_type or "unknown"})
    try:
        return model.model_validate(raw)  # pyright: ignore[reportReturnType]
    except ValueError:
        return UnknownUpdate.model_validate({**raw, "update_type": update_type})


class UpdatesPage(_In):
    updates: tuple[Update, ...] = ()
    marker: int | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "UpdatesPage":
        raw_updates = payload.get("updates") or ()
        parsed = tuple(parse_update(item) for item in raw_updates if isinstance(item, Mapping))
        marker = payload.get("marker")
        return cls(updates=parsed, marker=marker if isinstance(marker, int) else None)


class UploadTarget(_In):
    """POST /uploads answers with the URL to push bytes to, and sometimes the token already."""

    url: str
    token: str | None = None


class SimpleResult(_In):
    """The ``{success, message}`` envelope of PUT/DELETE /messages and POST /answers."""

    success: bool = True
    message: str | None = None


# --- outbound ------------------------------------------------------------------------------------


class CallbackButton(_Out):
    type: Literal["callback"] = "callback"
    text: str
    payload: str
    intent: Literal["default", "positive", "negative"] = "default"


class LinkButton(_Out):
    type: Literal["link"] = "link"
    text: str
    url: str


class OpenAppButton(_Out):
    """Opens the mini-app.

    ``web_app`` is the bot's public username or bot link, not the hosted app URL. MAX opens
    the URL bound to that bot on the partners platform. ``payload`` enters ``start_param``.
    Source: MAX Bot API OpenAppButton schema at https://dev.max.ru/docs-api.
    """

    type: Literal["open_app"] = "open_app"
    text: str
    web_app: str | None = None
    contact_id: int | None = None
    payload: str | None = None


Button = CallbackButton | LinkButton | OpenAppButton


class InlineKeyboardPayload(_Out):
    buttons: list[list[Button]]


class InlineKeyboardAttachment(_Out):
    type: Literal["inline_keyboard"] = "inline_keyboard"
    payload: InlineKeyboardPayload


class ImagePayload(_Out):
    """Either a token from POST /uploads or a direct URL (images only)."""

    token: str | None = None
    url: str | None = None


class ImageAttachment(_Out):
    type: Literal["image"] = "image"
    payload: ImagePayload


Attachment = InlineKeyboardAttachment | ImageAttachment


class NewMessageBody(_Out):
    text: str | None = None
    attachments: list[Attachment] | None = None
    notify: bool | None = None
    format: TextFormat | None = None

    def to_payload(self) -> dict[str, Any]:
        # attachments=[] means "remove all attachments" and must survive exclude_none.
        payload = self.model_dump(mode="json", exclude_none=True)
        if self.attachments == []:
            payload["attachments"] = []
        return payload


def keyboard(rows: Sequence[Sequence[Button]]) -> InlineKeyboardAttachment:
    return InlineKeyboardAttachment(
        payload=InlineKeyboardPayload(buttons=[list(row) for row in rows])
    )


def image_from_token(token: str) -> ImageAttachment:
    return ImageAttachment(payload=ImagePayload(token=token))


class BotCommand(_Out):
    name: str = Field(max_length=64)
    description: str | None = Field(default=None, max_length=128)
