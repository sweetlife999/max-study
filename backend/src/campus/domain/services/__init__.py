"""Domain services. The api and the bot call these and hold no rules and no SQL of their own."""

from campus.domain.services.app_config import AppConfigService
from campus.domain.services.attendance import AttendanceService
from campus.domain.services.checkins import CheckinService
from campus.domain.services.events import EventService
from campus.domain.services.kv import UPDATES_MARKER_KEY, KeyValueService
from campus.domain.services.onboarding import OnboardingService
from campus.domain.services.organizers import OrganizerService
from campus.domain.services.outbox import OutboxService
from campus.domain.services.qr_displays import QrDisplayService
from campus.domain.services.rsvps import RsvpService
from campus.domain.services.users import UserService

__all__ = [
    "UPDATES_MARKER_KEY",
    "AppConfigService",
    "AttendanceService",
    "CheckinService",
    "EventService",
    "KeyValueService",
    "OnboardingService",
    "OrganizerService",
    "OutboxService",
    "QrDisplayService",
    "RsvpService",
    "UserService",
]
