"""Injectable time source so that services and tests are deterministic."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware, in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FixedClock:
    """A manually driven clock for tests and scripted demos."""

    current: datetime = field(default_factory=lambda: datetime(2026, 9, 1, 12, 0, tzinfo=UTC))

    def __post_init__(self) -> None:
        if self.current.tzinfo is None:
            msg = "FixedClock needs a timezone-aware datetime"
            raise ValueError(msg)

    def now(self) -> datetime:
        return self.current.astimezone(UTC)

    def advance(self, delta: timedelta) -> None:
        self.current = self.current + delta

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            msg = "FixedClock needs a timezone-aware datetime"
            raise ValueError(msg)
        self.current = moment
