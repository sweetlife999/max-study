"""Inline keyboard construction; callbacks contain identifiers, never check-in codes."""

from campus.i18n import translator
from campus.max.types import CallbackButton, InlineKeyboardAttachment, OpenAppButton, keyboard


def callback(text: str, payload: str) -> CallbackButton:
    return CallbackButton(text=text[:128], payload=payload)


def qr_stop(lang: str, event_id: int) -> InlineKeyboardAttachment:
    return keyboard([[callback(translator().text(lang, "bot.stop"), f"qr_stop:{event_id}")]])


def menu(lang: str, *, organizer: bool, bot_username: str | None) -> InlineKeyboardAttachment:
    t = translator()
    rows = [[callback(t.text(lang, f"bot.{key}"), key)] for key in ("onboarding", "events", "code")]
    if organizer:
        rows.append([callback(t.text(lang, "bot.my_events"), "my_events")])
    rows.append([callback(t.text(lang, "bot.language"), "language")])
    buttons = keyboard(rows)
    buttons.payload.buttons.append(
        [OpenAppButton(text=t.text(lang, "bot.open_app"), web_app=bot_username)]
    )
    return buttons
