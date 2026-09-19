"""Organizer routes; ownership is established before reading private data."""

from fastapi import APIRouter, Response

from campus.api.auth import Admin, Organizer, OwnedEvent
from campus.api.schemas import MAX_PAGE, CreateEvent, EventList, UpdateEvent
from campus.domain.services import AttendanceService, QrDisplayService
from campus.domain.views import AttendanceView, EventView, InviteView, QrCodeView

router = APIRouter(prefix="/api/org", tags=["organizer"])


@router.get("/events")
async def events(person: Organizer) -> EventList:
    items = await person.events.list_for_organizer(person.user.id, limit=MAX_PAGE)
    return EventList(items=list(await person.events.views(items, viewer=person.user)))


@router.post("/events")
async def create_event(body: CreateEvent, person: Organizer) -> EventView:
    event = await person.events.create(organizer=person.user, **body.model_dump())
    return await person.events.view(event, viewer=person.user)


@router.patch("/events/{event_id}")
async def update_event(body: UpdateEvent, person: Organizer, event: OwnedEvent) -> EventView:
    await person.events.update(event, **body.model_dump(exclude_unset=True))
    return await person.events.view(event, viewer=person.user)


@router.get("/events/{event_id}/qr")
async def qr(person: Organizer, event: OwnedEvent) -> QrCodeView:
    return person.events.current_code(event)


@router.post("/events/{event_id}/qr/chat", status_code=202, response_class=Response)
async def qr_chat(person: Organizer, event: OwnedEvent) -> Response:
    await QrDisplayService(person.session, person.config, person.clock).start(
        event=event, organizer=person.user, max_user_id=person.user.max_user_id
    )
    return Response(status_code=202)


@router.get("/events/{event_id}/attendance")
async def attendance(person: Organizer, event: OwnedEvent) -> AttendanceView:
    return await AttendanceService(person.session, person.config, person.clock).listing(event)


@router.get(
    "/events/{event_id}/attendance.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
async def attendance_csv(person: Organizer, event: OwnedEvent) -> Response:
    data = await AttendanceService(person.session, person.config, person.clock).csv_for(event)
    return Response(
        data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="attendance-{event.id}.csv"'},
    )


@router.post("/invites")
async def invite(person: Admin) -> InviteView:
    return await person.organizers.create_invite(created_by=person.user.id)
