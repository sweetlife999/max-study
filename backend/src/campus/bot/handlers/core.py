"""Conversation routing. All persistence and business decisions belong to domain services."""

from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from campus.bot.keyboards import callback, menu, qr_stop
from campus.db.models import User
from campus.domain.clock import Clock
from campus.domain.context import DomainConfig
from campus.domain.errors import AmbiguousCodeError, InvalidCodeError, ValidationFailedError
from campus.domain.services.checkins import CheckinService
from campus.domain.services.events import EventService
from campus.domain.services.kv import KeyValueService
from campus.domain.services.onboarding import OnboardingService
from campus.domain.services.organizers import OrganizerService
from campus.domain.services.qr_displays import QrDisplayService
from campus.domain.services.rsvps import RsvpService
from campus.domain.services.users import UserService
from campus.i18n import translator
from campus.max.client import MaxClient
from campus.max.types import (
    Attachment,
    BotStartedUpdate,
    InlineKeyboardAttachment,
    MessageCallbackUpdate,
    MessageCreatedUpdate,
    NewMessageBody,
    OpenAppButton,
    Update,
    keyboard,
)
from campus.max.types import User as MaxUser


class Handler:
    def __init__(
        self,
        session: AsyncSession,
        config: DomainConfig,
        clock: Clock,
        client: MaxClient,
        max_user_id: int,
    ) -> None:
        self.config, self.clock, self.client = config, clock, client
        self.max_user_id = max_user_id
        self.lang = config.default_language
        self.users = UserService(session, config, clock)
        self.events = EventService(session, config, clock)
        self.onboarding = OnboardingService(session, config, clock)
        self.organizers = OrganizerService(session, config, clock)
        self.checkins = CheckinService(session, config, clock)
        self.rsvps = RsvpService(session, config, clock)
        self.displays = QrDisplayService(session, config, clock)
        self.kv = KeyValueService(session, config, clock)
        self.state_key = f"bot_conversation:{max_user_id}"

    def text(self, key: str, **params: str | int) -> str:
        return translator().text(self.lang, f"bot.{key}", **params)

    async def send(self, text: str, buttons: InlineKeyboardAttachment | None = None) -> None:
        # Split long descriptions without breaking MAX's documented 4000 character limit.
        chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [""]
        for index, chunk in enumerate(chunks):
            attachments: list[Attachment] | None = (
                [buttons] if buttons and index == len(chunks) - 1 else None
            )
            await self.client.send_message(
                user_id=self.max_user_id, body=NewMessageBody(text=chunk, attachments=attachments)
            )

    async def handle(self, update: Update, actor: MaxUser) -> None:
        locale = getattr(update, "user_locale", None)
        user = await self.users.get_or_create(
            max_user_id=actor.user_id, first_name=actor.first_name, lang=locale
        )
        self.lang = user.lang
        if isinstance(update, BotStartedUpdate):
            await self.start(user, update.payload)
        elif isinstance(update, MessageCallbackUpdate):
            await self.action(user, update.callback.payload or "")
        elif isinstance(update, MessageCreatedUpdate):
            text = update.message.text.strip()
            command, _, payload = text.partition(" ")
            if command == "/start":
                await self.start(user, payload or None)
            elif text == "/menu":
                self.users.require_consent(user)
                await self.main_menu(user)
            else:
                self.users.require_consent(user)
                state = await self.kv.get(self.state_key) or {}
                if state.get("mode") == "code":
                    await self.checkin(user, text)
                else:
                    await self.main_menu(user)

    async def consent(self) -> None:
        await self.send(self.text("consent"), keyboard([[callback(self.text("agree"), "consent")]]))

    async def start(self, user: User, payload: str | None) -> None:
        await self.kv.set(self.state_key, {"start": (payload or "")[:256]})
        await self.send(self.text("welcome"))
        if user.consent_at is None:
            await self.consent()
            return
        await self.languages()
        await self.resume(user)

    async def languages(self) -> None:
        await self.send(
            self.text("language"),
            keyboard(
                [
                    [callback(self.text(f"language_{lang}"), f"lang:{lang}")]
                    for lang in self.config.university.languages
                ]
            ),
        )

    async def resume(self, user: User) -> None:
        state = await self.kv.get(self.state_key) or {}
        payload = state.get("start", "")
        await self.kv.delete(self.state_key)
        if isinstance(payload, str) and payload.startswith("org_"):
            await self.organizers.accept_invite(token=payload[4:], user=user)
            await self.send(self.text("invite_accepted"))
        elif isinstance(payload, str) and payload.startswith("ev_"):
            await self.event_card(user, self.identifier(payload[3:]))
        await self.main_menu(user)

    async def main_menu(self, user: User) -> None:
        await self.send(
            self.text("menu"),
            menu(
                self.lang,
                organizer=await self.users.is_organizer(user.id),
                bot_username=self.config.bot_username,
            ),
        )

    @staticmethod
    def identifier(value: str) -> int:
        if not value.isascii() or not value.isdigit() or len(value) > 18 or int(value) <= 0:
            raise ValidationFailedError("callback")
        return int(value)

    async def action(self, user: User, payload: str) -> None:
        action, _, arg = payload.partition(":")
        if action == "consent":
            await self.users.give_consent(user)
            await self.languages()
            await self.resume(user)
            return
        if action == "lang":
            await self.users.set_language(user, arg)
            self.lang = user.lang
            if user.consent_at is None:
                await self.consent()
            else:
                await self.main_menu(user)
            return
        self.users.require_consent(user)
        await self.protected_action(user, action, arg)

    async def protected_action(self, user: User, action: str, arg: str) -> None:
        if action == "language":
            await self.languages()
        elif action == "onboarding":
            await self.progress(user)
        elif action == "complete":
            await self.onboarding.complete_manual(user, arg)
            await self.progress(user)
        elif action in {"events", "my_events"}:
            await self.event_list(user, own=action == "my_events")
        elif action == "event":
            await self.event_card(user, self.identifier(arg))
        elif action in {"rsvp", "unrsvp"}:
            event = await self.events.require(self.identifier(arg))
            if action == "rsvp":
                await self.rsvps.put(user=user, event=event)
            else:
                await self.rsvps.delete(user=user, event=event)
            await self.event_card(user, event.id)
        elif action in {"qr_start", "qr_stop", "checkin_open", "checkin_close"}:
            await self.organizer_action(user, action, self.identifier(arg))
        elif action == "code":
            await self.kv.set(self.state_key, {"mode": "code"})
            await self.send(self.text("enter_code"))
        elif action == "choose":
            await self.choose_event(user, self.identifier(arg))
        else:
            await self.main_menu(user)

    async def choose_event(self, user: User, event_id: int) -> None:
        state = await self.kv.get(self.state_key) or {}
        if event_id not in state.get("event_ids", []) or not isinstance(state.get("code"), str):
            raise InvalidCodeError
        await self.checkin(user, state["code"], event_id=event_id)

    async def progress(self, user: User) -> None:
        progress = await self.onboarding.progress(user)
        await self.send(self.text("progress", done=progress.done_count, total=progress.total))
        for step in progress.steps:
            status = self.text("done" if step.done else "todo")
            buttons = (
                keyboard([[callback(self.text("complete"), f"complete:{step.key}")]])
                if step.type == "manual" and not step.done
                else None
            )
            await self.send(
                self.text("step", status=status, title=step.title, description=step.description),
                buttons,
            )

    async def event_list(self, user: User, *, own: bool) -> None:
        if own:
            await self.organizers.require_organizer(user)
            events = await self.events.list_for_organizer(user.id, limit=25)
        else:
            events = await self.events.list_by_scope("upcoming", limit=25)
        if not events:
            await self.send(self.text("no_events"))
        for event in events:
            await self.event_card(user, event.id, organizer=own)

    async def event_card(self, user: User, event_id: int, *, organizer: bool = False) -> None:
        event = await self.events.require(event_id)
        view = await self.events.view(event, viewer=user)
        starts = view.starts_at.astimezone(
            ZoneInfo(self.config.university.university.timezone)
        ).strftime("%d.%m.%Y %H:%M %Z")
        rows = [
            [
                callback(
                    self.text("not_going" if view.rsvp else "going"),
                    f"{'unrsvp' if view.rsvp else 'rsvp'}:{view.id}",
                )
            ]
        ]
        buttons = keyboard(rows)
        buttons.payload.buttons.append(
            [
                OpenAppButton(
                    text=self.text("open_app"),
                    web_app=self.config.bot_username,
                    payload=f"ev_{view.id}",
                )
            ]
        )
        if organizer:
            buttons.payload.buttons.extend(
                [
                    [
                        callback(
                            self.text("close_checkin" if view.checkin_open else "open_checkin"),
                            f"{'checkin_close' if view.checkin_open else 'checkin_open'}:{view.id}",
                        )
                    ],
                    [callback(self.text("show_qr"), f"qr_start:{view.id}")],
                    [callback(self.text("stop"), f"qr_stop:{view.id}")],
                ]
            )
        await self.send(
            self.text(
                "event_card",
                title=view.title,
                description=view.description,
                location=view.location,
                starts_at=starts,
                points=view.points,
            ),
            buttons,
        )

    async def organizer_action(self, user: User, action: str, event_id: int) -> None:
        await self.organizers.require_organizer(user)
        event = await self.events.require(event_id)
        self.events.require_owner(event, user)
        if action == "qr_start":
            await self.displays.start(event=event, organizer=user, max_user_id=user.max_user_id)
            await self.send(self.text("qr_started"), qr_stop(self.lang, event_id))
        elif action == "qr_stop":
            await self.displays.stop_for(organizer_id=user.id, event_id=event_id)
            await self.send(self.text("qr_stopped"))
        else:
            await self.events.update(event, checkin_open=action == "checkin_open")
            await self.event_card(user, event_id, organizer=True)

    async def checkin(self, user: User, code: str, *, event_id: int | None = None) -> None:
        try:
            result = await self.checkins.check_in_view(
                user=user, code=code, method="code", event_id=event_id
            )
        except AmbiguousCodeError as exc:
            await self.kv.set(
                self.state_key, {"mode": "code", "code": code, "event_ids": list(exc.event_ids)}
            )
            for candidate in exc.event_ids:
                event = await self.events.require(candidate)
                await self.send(
                    self.text("choose_event"),
                    keyboard([[callback(event.title, f"choose:{candidate}")]]),
                )
            return
        await self.kv.delete(self.state_key)
        await self.send(
            self.text(
                "already_checked_in" if result.already else "checkin_confirmed",
                title=result.event.title,
                points_total=result.points_total,
            )
        )
