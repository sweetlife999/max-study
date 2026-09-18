"""Shared plumbing for the services."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from campus.domain.clock import Clock
from campus.domain.context import DomainConfig
from campus.domain.errors import ValidationFailedError


@dataclass(frozen=True, slots=True)
class Service:
    """A unit of business logic bound to one session, one configuration and one clock."""

    session: AsyncSession
    config: DomainConfig
    clock: Clock

    def now(self) -> datetime:
        return self.clock.now()


def require_aware(value: datetime, field: str) -> datetime:
    """Reject naive datetimes at the domain boundary; everything stored is timestamptz in UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailedError(field, f"{field} must be timezone-aware")
    return value
