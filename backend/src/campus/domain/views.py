"""Read models the services return.

These are the shapes ARCHITECTURE.md §7 promises the mini-app; the api serialises them almost
one-to-one and the bot renders them into text. They are frozen: a caller cannot alter what a
service computed.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UniversityView:
    name: str
    timezone: str


@dataclass(frozen=True, slots=True)
class MeView:
    id: int
    first_name: str
    lang: str
    consent: bool
    is_organizer: bool
    is_admin: bool
    points: int
    university: UniversityView


@dataclass(frozen=True, slots=True)
class EventView:
    id: int
    title: str
    description: str
    kind: str
    kind_title: str
    location: str
    starts_at: datetime
    ends_at: datetime
    points: int
    onboarding_step: str | None
    checkin_open: bool
    rsvp: bool
    checked_in: bool
    attendees_count: int


@dataclass(frozen=True, slots=True)
class StepView:
    key: str
    type: str
    title: str
    description: str
    done: bool
    event_kind: str | None = None


@dataclass(frozen=True, slots=True)
class OnboardingView:
    steps: tuple[StepView, ...]
    done_count: int
    total: int


@dataclass(frozen=True, slots=True)
class CheckinResult:
    event: EventView
    already: bool
    points_total: int
    completed_step: StepView | None = None


@dataclass(frozen=True, slots=True)
class QrCodeView:
    """The current rotating code and its deep link. ``qr_seed`` is never part of this."""

    code: str
    deeplink: str
    window_started_at: datetime
    expires_at: datetime
    step_seconds: int


@dataclass(frozen=True, slots=True)
class InviteView:
    token: str
    deeplink: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AttendanceEntry:
    user_id: int
    first_name: str
    method: str
    checked_in_at: datetime


@dataclass(frozen=True, slots=True)
class AttendanceView:
    items: tuple[AttendanceEntry, ...]
    rsvp_count: int
    checkin_count: int
