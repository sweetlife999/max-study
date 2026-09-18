"""Users and their consent (ARCHITECTURE.md §7)."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from campus.db.models import Checkin, Event, Organizer, User
from campus.domain.errors import ConsentRequiredError, UnsupportedLanguageError, UserNotFoundError
from campus.domain.services.base import Service
from campus.domain.views import MeView, UniversityView

DEFAULT_FIRST_NAME = ""
MAX_FIRST_NAME_LENGTH = 256


@dataclass(frozen=True, slots=True)
class UserService(Service):
    async def by_max_user_id(self, max_user_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.max_user_id == max_user_id))
        return result.scalars().one_or_none()

    async def get(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def require(self, user_id: int) -> User:
        user = await self.get(user_id)
        if user is None:
            raise UserNotFoundError(f"user {user_id}")
        return user

    async def get_or_create(
        self,
        *,
        max_user_id: int,
        first_name: str = DEFAULT_FIRST_NAME,
        lang: str | None = None,
    ) -> User:
        """Find the user behind a MAX id, creating them on first contact.

        Two concurrent first requests (the bot's ``bot_started`` and the mini-app's first call)
        race here, so the insert is an upsert on ``max_user_id`` rather than a check-then-insert.
        """
        statement = (
            pg_insert(User)
            .values(
                max_user_id=max_user_id,
                first_name=first_name[:MAX_FIRST_NAME_LENGTH],
                lang=self.config.language_or_default(lang),
            )
            .on_conflict_do_nothing(index_elements=[User.max_user_id])
            .returning(User)
        )
        created = (await self.session.execute(statement)).scalars().one_or_none()
        if created is not None:
            return created
        existing = await self.by_max_user_id(max_user_id)
        if existing is None:  # pragma: no cover - only if the row vanished between the statements
            raise UserNotFoundError(f"max user {max_user_id}")
        return await self._refresh_name(existing, first_name)

    async def _refresh_name(self, user: User, first_name: str) -> User:
        """Keep the display name in step with MAX, but never blank it."""
        trimmed = first_name[:MAX_FIRST_NAME_LENGTH]
        if trimmed and trimmed != user.first_name:
            user.first_name = trimmed
            await self.session.flush()
        return user

    async def give_consent(self, user: User) -> User:
        """Record consent (152-ФЗ art. 9). Repeating it keeps the original moment."""
        if user.consent_at is None:
            user.consent_at = self.now()
            await self.session.flush()
        return user

    async def set_language(self, user: User, lang: str) -> User:
        if lang not in self.config.university.languages:
            raise UnsupportedLanguageError(lang)
        if user.lang != lang:
            user.lang = lang
            await self.session.flush()
        return user

    def require_consent(self, user: User) -> None:
        """Everything except /me, /me/consent and PATCH /me needs consent first (§7)."""
        if user.consent_at is None:
            raise ConsentRequiredError(f"user {user.id}")

    def is_admin(self, user: User) -> bool:
        return user.max_user_id in self.config.admin_max_user_ids

    async def is_organizer(self, user_id: int) -> bool:
        result = await self.session.execute(
            select(Organizer.user_id).where(Organizer.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None

    async def points(self, user_id: int) -> int:
        """Points are never stored: they are the sum over the events the user checked in to."""
        result = await self.session.execute(
            select(func.coalesce(func.sum(Event.points), 0))
            .select_from(Checkin)
            .join(Event, Event.id == Checkin.event_id)
            .where(Checkin.user_id == user_id)
        )
        return int(result.scalar_one())

    async def me(self, user: User) -> MeView:
        university = self.config.university
        lang = self.config.language_or_default(user.lang)
        return MeView(
            id=user.id,
            first_name=user.first_name,
            lang=user.lang,
            consent=user.consent_at is not None,
            is_organizer=await self.is_organizer(user.id),
            is_admin=self.is_admin(user),
            points=await self.points(user.id),
            university=UniversityView(
                name=university.text(university.university.name, lang),
                timezone=university.university.timezone,
            ),
        )
