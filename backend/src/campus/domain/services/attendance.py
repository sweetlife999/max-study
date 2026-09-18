"""Who turned up, as a list and as a CSV export (ARCHITECTURE.md §7)."""

import csv
import io
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from campus.db.models import Checkin, Event, Rsvp, User
from campus.domain.services.base import Service
from campus.domain.views import AttendanceEntry, AttendanceView

# Excel only recognises a UTF-8 CSV when it starts with a byte-order mark, and this file is meant
# to be opened by a person, not parsed by a program.
CSV_ENCODING = "utf-8-sig"
CSV_HEADER = ("user_id", "first_name", "method", "checked_in_at")
CSV_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S %Z"

# A cell Excel, LibreOffice or Sheets reads as a formula rather than as text. `first_name` is
# whatever the student set in MAX, so a name of `=cmd|" /C calc"!A0` would run on the organizer's
# machine the moment they open the export. Quoting is not enough — the spreadsheet unquotes
# before it decides — so a leading trigger is neutralised with the documented apostrophe prefix.
CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
CSV_FORMULA_ESCAPE = "'"


def csv_safe(value: str) -> str:
    """``value`` as a cell no spreadsheet will evaluate. Ordinary text is returned unchanged."""
    return CSV_FORMULA_ESCAPE + value if value.startswith(CSV_FORMULA_TRIGGERS) else value


@dataclass(frozen=True, slots=True)
class AttendanceService(Service):
    async def listing(self, event: Event) -> AttendanceView:
        result = await self.session.execute(
            select(User.id, User.first_name, Checkin.method, Checkin.created_at)
            .join(Checkin, Checkin.user_id == User.id)
            .where(Checkin.event_id == event.id)
            .order_by(Checkin.created_at, User.id)
        )
        items = tuple(
            AttendanceEntry(
                user_id=user_id,
                first_name=first_name,
                method=method,
                checked_in_at=created_at,
            )
            for user_id, first_name, method, created_at in result.all()
        )
        return AttendanceView(
            items=items,
            rsvp_count=await self._rsvp_count(event.id),
            checkin_count=len(items),
        )

    async def _rsvp_count(self, event_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Rsvp).where(Rsvp.event_id == event_id)
        )
        return int(result.scalar_one())

    def to_csv(self, view: AttendanceView) -> bytes:
        """UTF-8 with BOM, timestamps in the university's own time zone, no live formulas."""
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        for entry in view.items:
            writer.writerow(
                [
                    entry.user_id,
                    csv_safe(entry.first_name),
                    csv_safe(entry.method),
                    csv_safe(self._local(entry.checked_in_at)),
                ]
            )
        return buffer.getvalue().encode(CSV_ENCODING)

    def _local(self, moment: datetime) -> str:
        return moment.astimezone(self.config.university.tz).strftime(CSV_TIMESTAMP_FORMAT)

    async def csv_for(self, event: Event) -> bytes:
        return self.to_csv(await self.listing(event))
