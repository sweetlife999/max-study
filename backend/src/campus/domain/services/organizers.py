"""Organizers and their single-use invitations (ARCHITECTURE.md §4, §7, §8)."""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from campus.db.models import Organizer, OrganizerInvite, User
from campus.domain.deeplinks import invite_deeplink
from campus.domain.errors import (
    InviteAlreadyUsedError,
    InviteExpiredError,
    InviteNotFoundError,
    OrganizerRequiredError,
)
from campus.domain.services.base import Service
from campus.domain.services.outbox import OutboxService
from campus.domain.views import InviteView

# secrets.token_urlsafe(32) is 256 bits of entropy, well over the 128 the contract requires.
INVITE_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class OrganizerService(Service):
    @property
    def _outbox(self) -> OutboxService:
        return OutboxService(self.session, self.config, self.clock)

    async def is_organizer(self, user_id: int) -> bool:
        result = await self.session.execute(
            select(Organizer.user_id).where(Organizer.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None

    async def require_organizer(self, user: User) -> None:
        if not await self.is_organizer(user.id):
            raise OrganizerRequiredError(f"user {user.id}")

    async def list_organizers(self) -> Sequence[Organizer]:
        result = await self.session.execute(select(Organizer).order_by(Organizer.created_at))
        return result.scalars().all()

    async def grant(self, *, user_id: int, invited_by: int | None = None) -> Organizer:
        """Make someone an organizer directly (seed data, admin action). Idempotent."""
        await self.session.execute(
            pg_insert(Organizer)
            .values(user_id=user_id, invited_by=invited_by)
            .on_conflict_do_nothing(index_elements=[Organizer.user_id])
        )
        result = await self.session.execute(select(Organizer).where(Organizer.user_id == user_id))
        return result.scalars().one()

    async def create_invite(
        self, *, created_by: int | None = None, ttl: timedelta | None = None
    ) -> InviteView:
        """Mint a single-use invitation. Only an admin should reach this (§7)."""
        expires_at = self.now() + (ttl if ttl is not None else self.config.invite_ttl)
        invite = OrganizerInvite(
            token=secrets.token_urlsafe(INVITE_TOKEN_BYTES),
            created_by=created_by,
            expires_at=expires_at,
        )
        self.session.add(invite)
        await self.session.flush()
        return InviteView(
            token=invite.token,
            deeplink=invite_deeplink(self.config.bot_username, invite.token),
            expires_at=invite.expires_at,
        )

    async def accept_invite(self, *, token: str, user: User) -> Organizer:
        """Redeem an invitation exactly once.

        The claim is a single conditional UPDATE, so two users racing on the same token cannot
        both become organizers; the loser is told the invitation was already used.
        """
        now = self.now()
        claimed = await self.session.execute(
            update(OrganizerInvite)
            .where(
                OrganizerInvite.token == token,
                OrganizerInvite.used_by.is_(None),
                OrganizerInvite.expires_at > now,
            )
            .values(used_by=user.id, used_at=now)
            .returning(OrganizerInvite.created_by)
        )
        row = claimed.one_or_none()
        if row is None:
            await self._explain_failed_claim(token, now)  # always raises
        invited_by = row[0]
        organizer = await self.grant(user_id=user.id, invited_by=invited_by)
        await self._outbox.enqueue(
            user_id=user.id,
            kind="invite_accepted",
            payload={"invited_by": invited_by},
            dedup_key=f"invite_accepted:{token}",
        )
        return organizer

    async def _explain_failed_claim(self, token: str, now: datetime) -> NoReturn:
        """Turn "the UPDATE matched nothing" into the precise reason."""
        result = await self.session.execute(
            select(OrganizerInvite).where(OrganizerInvite.token == token)
        )
        invite = result.scalars().one_or_none()
        if invite is None:
            raise InviteNotFoundError("unknown invite token")
        if invite.used_by is not None:
            raise InviteAlreadyUsedError(f"invite used at {invite.used_at}")
        raise InviteExpiredError(f"invite expired at {invite.expires_at}, now {now}")
