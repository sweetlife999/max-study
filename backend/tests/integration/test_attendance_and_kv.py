"""AttendanceService and KeyValueService (ARCHITECTURE.md §4, §7, §8)."""

import csv
import io
from datetime import timedelta

from campus.domain.services.attendance import CSV_HEADER
from campus.domain.services.kv import UPDATES_MARKER_KEY
from tests.integration.factories import World

UTF8_BOM = b"\xef\xbb\xbf"


# --- attendance -------------------------------------------------------------------------------


async def test_an_event_nobody_came_to_has_an_empty_listing(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    listing = await world.attendance.listing(event)

    assert listing.items == ()
    assert listing.checkin_count == 0
    assert listing.rsvp_count == 0


async def test_the_listing_names_everyone_who_checked_in(world: World) -> None:
    organizer = await world.organizer()
    first = await world.user(first_name="Аня")
    second = await world.user(first_name="Борис")
    event = await world.open_event_now(organizer=organizer)
    await world.checkins.check_in(user=first, code=world.code_for(event), method="qr")
    world.clock.advance(timedelta(minutes=1))
    await world.checkins.check_in(user=second, code=world.code_for(event), method="code")

    listing = await world.attendance.listing(event)

    assert [entry.first_name for entry in listing.items] == ["Аня", "Борис"]
    assert [entry.method for entry in listing.items] == ["qr", "code"]
    assert listing.checkin_count == 2


async def test_the_listing_counts_rsvps_separately(world: World) -> None:
    organizer = await world.organizer()
    coming = await world.user()
    curious = await world.user(first_name="Борис")
    event = await world.open_event_now(organizer=organizer)
    await world.rsvps.put(user=coming, event=event)
    await world.rsvps.put(user=curious, event=event)
    await world.checkins.check_in(user=coming, code=world.code_for(event), method="qr")

    listing = await world.attendance.listing(event)

    assert (listing.rsvp_count, listing.checkin_count) == (2, 1)


async def test_the_listing_is_scoped_to_one_event(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    mine = await world.open_event_now(organizer=organizer)
    other = await world.open_event_now(organizer=organizer)
    await world.checkins.check_in(user=student, code=world.code_for(other), method="qr")

    assert (await world.attendance.listing(mine)).items == ()


# --- the CSV export ---------------------------------------------------------------------------


async def test_the_csv_starts_with_a_byte_order_mark(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    exported = await world.attendance.csv_for(event)

    assert exported.startswith(UTF8_BOM)


async def test_the_csv_has_the_documented_header(world: World) -> None:
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)

    exported = await world.attendance.csv_for(event)

    header = exported.decode("utf-8-sig").splitlines()[0]
    assert header.split(",") == list(CSV_HEADER)


async def test_the_csv_carries_one_row_per_attendee(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user(first_name="Аня")
    event = await world.open_event_now(organizer=organizer)
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    exported = await world.attendance.csv_for(event)

    rows = exported.decode("utf-8-sig").strip().splitlines()
    assert len(rows) == 2
    assert rows[1].startswith(f"{student.id},Аня,qr,")


async def test_cyrillic_names_survive_the_round_trip(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user(first_name="Анна-Мария Ёлкина")
    event = await world.open_event_now(organizer=organizer)
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    exported = await world.attendance.csv_for(event)

    assert "Анна-Мария Ёлкина" in exported.decode("utf-8-sig")


async def test_a_name_with_a_comma_is_quoted(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user(first_name='Аня, "Староста"')
    event = await world.open_event_now(organizer=organizer)
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    exported = await world.attendance.csv_for(event)

    rows = list(csv.reader(io.StringIO(exported.decode("utf-8-sig"))))
    assert rows[1][1] == 'Аня, "Староста"'


async def test_timestamps_are_rendered_in_the_university_time_zone(world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    await world.checkins.check_in(user=student, code=world.code_for(event), method="qr")

    exported = await world.attendance.csv_for(event)

    listing = await world.attendance.listing(event)
    local = listing.items[0].checked_in_at.astimezone(world.config.university.tz)
    assert local.strftime("%Y-%m-%d %H:%M:%S") in exported.decode("utf-8-sig")


# --- the key/value table ----------------------------------------------------------------------


async def test_an_unknown_key_reads_as_nothing(world: World) -> None:
    assert await world.kv.get("nothing_here") is None


async def test_a_value_survives_a_round_trip(world: World) -> None:
    await world.kv.set("greeting", {"text": "привет", "count": 2})

    assert await world.kv.get("greeting") == {"text": "привет", "count": 2}


async def test_setting_a_key_again_replaces_it(world: World) -> None:
    await world.kv.set("greeting", {"text": "первый"})

    await world.kv.set("greeting", {"text": "второй"})

    assert await world.kv.get("greeting") == {"text": "второй"}


async def test_a_key_can_be_deleted(world: World) -> None:
    await world.kv.set("greeting", {"text": "привет"})

    await world.kv.delete("greeting")

    assert await world.kv.get("greeting") is None


async def test_deleting_an_unknown_key_is_not_an_error(world: World) -> None:
    await world.kv.delete("nothing_here")


# --- the polling marker -----------------------------------------------------------------------


async def test_there_is_no_marker_before_the_first_poll(world: World) -> None:
    assert await world.kv.updates_marker() is None


async def test_the_marker_survives_a_round_trip(world: World) -> None:
    await world.kv.set_updates_marker(42)

    assert await world.kv.updates_marker() == 42


async def test_clearing_the_marker_removes_the_row(world: World) -> None:
    await world.kv.set_updates_marker(42)

    await world.kv.set_updates_marker(None)

    assert await world.kv.updates_marker() is None
    assert await world.kv.get(UPDATES_MARKER_KEY) is None


async def test_a_marker_that_is_not_an_integer_reads_as_none(world: World) -> None:
    await world.kv.set(UPDATES_MARKER_KEY, {"marker": "not a number"})

    assert await world.kv.updates_marker() is None


async def test_a_boolean_is_not_a_marker(world: World) -> None:
    """``True`` is an int in Python; MAX markers are not."""
    await world.kv.set(UPDATES_MARKER_KEY, {"marker": True})

    assert await world.kv.updates_marker() is None


async def test_a_row_without_the_field_reads_as_none(world: World) -> None:
    await world.kv.set(UPDATES_MARKER_KEY, {})

    assert await world.kv.updates_marker() is None
