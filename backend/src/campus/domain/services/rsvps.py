"""RSVPs and the reminders they schedule (ARCHITECTURE.md §6, §7, §8)."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from campus.db.models import Event, Rsvp, User
from campus.domain.services.base import Service
from campus.domain.services.outbox import OutboxService

REMINDER_KIND = "reminder"


def reminder_dedup_key(user_id: int, event_id: int, offset: timedelta) -> str:
    """Stable per user, event and offset: re-saying "I'm going" reschedules, never piles up."""
    return f"{REMINDER_KIND}:{user_id}:{event_id}:{int(offset.total_seconds())}"


@dataclass(frozen=True, slots=True)
class RsvpService(Service):
    @property
    def _outbox(self) -> OutboxService:
        return OutboxService(self.session, self.config, self.clock)

    async def has(self, user_id: int, event_id: int) -> bool:
        result = await self.session.execute(
            select(Rsvp.user_id).where(Rsvp.user_id == user_id, Rsvp.event_id == event_id)
        )
        return result.scalar_one_or_none() is not None

    async def count(self, event_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Rsvp).where(Rsvp.event_id == event_id)
        )
        return int(result.scalar_one())

    async def put(self, *, user: User, event: Event) -> None:
        """Say "I'm going" and queue the reminders. Idempotent."""
        await self.session.execute(
            pg_insert(Rsvp)
            .values(user_id=user.id, event_id=event.id)
            .on_conflict_do_nothing(index_elements=[Rsvp.user_id, Rsvp.event_id])
        )
        await self._schedule_reminders(user=user, event=event)

    async def delete(self, *, user: User, event: Event) -> None:
        """Take it back and cancel the reminders that have not gone out yet."""
        await self.session.execute(
            delete(Rsvp).where(Rsvp.user_id == user.id, Rsvp.event_id == event.id)
        )
        await self._outbox.cancel(
            reminder_dedup_key(user.id, event.id, offset)
            for offset in self.config.university.reminders_before
        )

    async def _schedule_reminders(self, *, user: User, event: Event) -> None:
        now = self.now()
        for offset in self.config.university.reminders_before:
            run_at = event.starts_at - offset
            if run_at <= now:
                # The moment has passed; a reminder for it would arrive as spam.
                continue
            await self._outbox.enqueue(
                user_id=user.id,
                kind=REMINDER_KIND,
                payload={
                    "event_id": event.id,
                    "offset_seconds": int(offset.total_seconds()),
                },
                run_at=run_at,
                dedup_key=reminder_dedup_key(user.id, event.id, offset),
            )

    async def reschedule_for_event(self, event: Event) -> None:
        """Move pending RSVP reminders when an organizer changes the event start."""
        result = await self.session.execute(select(Rsvp.user_id).where(Rsvp.event_id == event.id))
        user_ids = result.scalars().all()
        now = self.now()
        for user_id in user_ids:
            for offset in self.config.university.reminders_before:
                key = reminder_dedup_key(user_id, event.id, offset)
                run_at = event.starts_at - offset
                if run_at <= now:
                    await self._outbox.cancel([key])
                else:
                    await self._outbox.enqueue(
                        user_id=user_id,
                        kind=REMINDER_KIND,
                        payload={
                            "event_id": event.id,
                            "offset_seconds": int(offset.total_seconds()),
                        },
                        run_at=run_at,
                        dedup_key=key,
                    )
