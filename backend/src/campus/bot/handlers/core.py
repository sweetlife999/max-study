"""Conversation routing. All persistence and business decisions belong to domain services."""

from datetime import datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from campus.bot.keyboards import callback, menu, qr_stop
from campus.db.models import User
from campus.domain.clock import Clock
from campus.domain.codes import is_well_formed
from campus.domain.context import DomainConfig
from campus.domain.errors import AmbiguousCodeError, InvalidCodeError, ValidationFailedError
from campus.domain.services.checkins import CheckinService
from campus.domain.services.events import EventService
from campus.domain.services.kv import KeyValueService
from campus.domain.services.onboarding import OnboardingService
from campus.domain.services.organizers import OrganizerService
from campus.domain.services.outbox import OutboxService
from campus.domain.services.qr_displays import QrDisplayService
from campus.domain.services.rsvps import RsvpService
from campus.domain.services.users import UserService
from campus.domain.views import EventView
from campus.i18n import translator
from campus.max.client import MaxClient
from campus.max.types import (
    Attachment,
    BotStartedUpdate,
    Button,
    InlineKeyboardAttachment,
    MessageCallbackUpdate,
    MessageCreatedUpdate,
    NewMessageBody,
    OpenAppButton,
    Update,
    keyboard,
)
from campus.max.types import User as MaxUser

# Conversation state is a turn's scratch space, never a record: it holds a start payload or a
# check-in code, and both stop being meaningful long before this.
STATE_TTL: Final = timedelta(minutes=30)
# §5 documents `start` as at most 128 characters.
MAX_START_PAYLOAD: Final = 128
LIST_LIMIT: Final = 25


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
        self.outbox = OutboxService(session, config, clock)
        self.state_key = f"bot_conversation:{max_user_id}"
        self.defer_messages = False
        self.pending_messages: list[NewMessageBody] = []
        self.pending_cancellations: list[str] = []

    async def state(self) -> dict[str, Any]:
        """Conversation state, or nothing once it has expired.

        The value holds a start payload or a check-in code, so it must not outlive the turn it
        belongs to: an invitation token left here by someone who never consented would sit in
        the table forever.
        """
        stored = await self.kv.get(self.state_key) or {}
        deadline = stored.get("expires_at")
        if not isinstance(deadline, str) or self.clock.now() > datetime.fromisoformat(deadline):
            if stored:
                await self.kv.delete(self.state_key)
            return {}
        return dict(stored)

    async def remember(self, value: dict[str, Any]) -> None:
        await self.kv.set(
            self.state_key,
            {**value, "expires_at": (self.clock.now() + STATE_TTL).isoformat()},
        )

    def text(self, key: str, **params: str | int) -> str:
        return translator().text(self.lang, f"bot.{key}", **params)

    async def send(self, text: str, buttons: InlineKeyboardAttachment | None = None) -> None:
        # Split long descriptions without breaking MAX's documented 4000 character limit.
        chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [""]
        for index, chunk in enumerate(chunks):
            attachments: list[Attachment] | None = (
                [buttons] if buttons and index == len(chunks) - 1 else None
            )
            body = NewMessageBody(text=chunk, attachments=attachments)
            if self.defer_messages:
                self.pending_messages.append(body)
            else:
                await self.client.send_message(user_id=self.max_user_id, body=body)

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
                state = await self.state()
                if state.get("mode") == "code" and is_well_formed(text):
                    await self.checkin(user, text)
                elif state.get("mode") == "code":
                    # §8 promises the *next six digits* are a code. Anything else leaves the
                    # mode instead of being spent as a guess: ten stray words would otherwise
                    # exhaust the §5 rate limit while the user is standing under the QR.
                    await self.kv.delete(self.state_key)
                    await self.main_menu(user)
                elif text:
                    await self.main_menu(user)

    async def consent(self) -> None:
        await self.send(self.text("consent"), keyboard([[callback(self.text("agree"), "consent")]]))

    async def start(self, user: User, payload: str | None) -> None:
        await self.remember({"start": (payload or "")[:MAX_START_PAYLOAD]})
        if user.consent_at is None:
            await self.send(
                f"{self.text('welcome')}\n\n{self.text('consent')}",
                keyboard([[callback(self.text("agree"), "consent")]]),
            )
            return
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
        state = await self.state()
        payload = state.get("start", "")
        await self.kv.delete(self.state_key)
        if isinstance(payload, str) and payload.startswith("org_"):
            token = payload[4:]
            await self.organizers.accept_invite(token=token, user=user)
            # The domain queues the same notification for the outbox. Answering here keeps the
            # chat responsive, so cancel the queued twin rather than send it twice.
            key = f"invite_accepted:{token}"
            if self.defer_messages:
                self.pending_cancellations.append(key)
            else:
                await self.outbox.cancel([key])
            await self.send(
                f"{self.text('invite_accepted')}\n\n{self.text('menu')}",
                menu(
                    self.lang,
                    organizer=await self.users.is_organizer(user.id),
                    bot_username=self.config.bot_username,
                ),
            )
        elif isinstance(payload, str) and payload.startswith("ev_"):
            await self.event_card(user, self.identifier(payload[3:]))
        else:
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

    async def protected_action(self, user: User, action: str, arg: str) -> None:  # noqa: PLR0912
        if action == "language":
            await self.languages()
        elif action == "onboarding":
            await self.progress(user)
        elif action == "step_events":
            await self.event_list(user, own=False, step_key=arg)
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
            await self.remember({"mode": "code"})
            await self.send(self.text("enter_code"), self.menu_return())
        elif action == "choose":
            await self.choose_event(user, self.identifier(arg))
        elif action == "menu":
            await self.kv.delete(self.state_key)
            await self.main_menu(user)
        else:
            await self.main_menu(user)

    async def choose_event(self, user: User, event_id: int) -> None:
        state = await self.state()
        if event_id not in state.get("event_ids", []) or not isinstance(state.get("code"), str):
            raise InvalidCodeError
        await self.checkin(user, state["code"], event_id=event_id)

    async def progress(self, user: User) -> None:
        progress = await self.onboarding.progress(user)
        # One message, not one per step: updates are dispatched in sequence and MAX allows one
        # message per second per chat, so a per-step message would freeze the whole bot for as
        # many seconds as the user has steps.
        lines = [self.text("progress", done=progress.done_count, total=progress.total)]
        rows: list[list[Button]] = []
        has_event_steps = False
        for step in progress.steps:
            status = self.text("done" if step.done else "todo")
            lines.append(
                self.text("step", status=status, title=step.title, description=step.description)
            )
            if step.type == "manual" and not step.done:
                rows.append(
                    [callback(f"{self.text('complete')}: {step.title}", f"complete:{step.key}")]
                )
            elif step.type == "event_kind" and not step.done:
                has_event_steps = True
                rows.append(
                    [
                        callback(
                            f"{self.text('find_events')}: {step.title}",
                            f"step_events:{step.key}",
                        )
                    ]
                )
        if has_event_steps:
            lines.append(self.text("checkin_hint"))
        rows.append([callback(self.text("back_to_menu"), "menu")])
        await self.send("\n\n".join(lines), keyboard(rows))

    def menu_return(self) -> InlineKeyboardAttachment:
        return keyboard([[callback(self.text("back_to_menu"), "menu")]])

    async def event_list(self, user: User, *, own: bool, step_key: str | None = None) -> None:
        if own:
            await self.organizers.require_organizer(user)
            events = await self.events.list_for_organizer(user.id, limit=LIST_LIMIT)
        elif step_key is not None:
            events = await self.events.list_for_onboarding_step(step_key, limit=LIST_LIMIT)
        else:
            events = await self.events.list_by_scope("upcoming", limit=LIST_LIMIT)
        if not events:
            await self.send(self.text("no_events"), self.menu_return())
            return
        # The batch view costs three queries for the whole list; calling event_card per row
        # would cost three per event and a message per event on top.
        views = await self.events.views(events, viewer=user)
        lines = [self.summary(view) for view in views]
        rows = [[callback(view.title, f"event:{view.id}")] for view in views]
        rows.append([callback(self.text("back_to_menu"), "menu")])
        await self.send("\n\n".join(lines), keyboard(rows))

    def summary(self, view: EventView) -> str:
        return self.text(
            "event_card",
            title=view.title,
            description=view.description,
            location=view.location,
            starts_at=self.local_time(view.starts_at),
            points=view.points,
        )

    def local_time(self, moment: datetime) -> str:
        return moment.astimezone(ZoneInfo(self.config.university.university.timezone)).strftime(
            self.text("datetime_format")
        )

    async def event_card(self, user: User, event_id: int) -> None:
        event = await self.events.require(event_id)
        view = await self.events.view(event, viewer=user)
        # Ownership decides, not the screen the user arrived from: the same card is reached
        # from the list, from a deep link and after an RSVP.
        organizer = event.organizer_id == user.id and await self.users.is_organizer(user.id)
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
            buttons.payload.buttons.append(
                [
                    callback(
                        self.text("close_checkin" if view.checkin_open else "open_checkin"),
                        f"{'checkin_close' if view.checkin_open else 'checkin_open'}:{view.id}",
                    )
                ]
            )
            active_display = await self.displays.active_for(organizer_id=user.id, event_id=view.id)
            if active_display and active_display.active_until <= self.clock.now():
                active_display = None
            if active_display:
                buttons.payload.buttons.append([callback(self.text("stop"), f"qr_stop:{view.id}")])
            elif view.checkin_open:
                buttons.payload.buttons.append(
                    [callback(self.text("show_qr"), f"qr_start:{view.id}")]
                )
        buttons.payload.buttons.append(
            [
                callback(
                    self.text("back_to_my_events" if organizer else "back_to_events"),
                    "my_events" if organizer else "events",
                )
            ]
        )
        buttons.payload.buttons.append([callback(self.text("back_to_menu"), "menu")])
        await self.send(self.summary(view), buttons)

    async def organizer_action(self, user: User, action: str, event_id: int) -> None:
        await self.organizers.require_organizer(user)
        event = await self.events.require(event_id)
        self.events.require_owner(event, user)
        if action == "qr_start":
            await self.displays.start(event=event, organizer=user, max_user_id=user.max_user_id)
            await self.send(self.text("qr_started"), qr_stop(self.lang, event_id))
        elif action == "qr_stop":
            await self.displays.stop_for(organizer_id=user.id, event_id=event_id)
            await self.send(self.text("qr_stopped"), self.menu_return())
        else:
            await self.events.update(event, checkin_open=action == "checkin_open")
            await self.event_card(user, event_id)

    async def checkin(self, user: User, code: str, *, event_id: int | None = None) -> None:
        try:
            result = await self.checkins.check_in_view(
                user=user, code=code, method="code", event_id=event_id
            )
        except AmbiguousCodeError as exc:
            await self.remember({"mode": "code", "code": code, "event_ids": list(exc.event_ids)})
            rows = [
                [callback((await self.events.require(candidate)).title, f"choose:{candidate}")]
                for candidate in exc.event_ids
            ]
            await self.send(self.text("choose_event"), keyboard(rows))
            return
        await self.kv.delete(self.state_key)
        if not result.already:
            # Answered here and by the outbox otherwise; §8 gives the user one confirmation.
            key = f"checkin_confirmed:{user.id}:{result.event.id}"
            if self.defer_messages:
                self.pending_cancellations.append(key)
            else:
                await self.outbox.cancel([key])
        await self.send(
            self.text(
                "already_checked_in" if result.already else "checkin_confirmed",
                title=result.event.title,
                points_total=result.points_total,
            ),
            self.menu_return(),
        )
