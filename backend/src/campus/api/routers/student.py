"""Student mini-app routes."""

from typing import Literal

from fastapi import APIRouter

from campus.api.auth import Authenticated, Consenting
from campus.api.schemas import MAX_PAGE, CheckinRequest, EventId, EventList, StepKey, UpdateMe
from campus.domain.deeplinks import parse_checkin_start_param
from campus.domain.services import AppConfigService, CheckinService, OnboardingService, RsvpService
from campus.domain.views import (
    AppConfigView,
    CheckinResult,
    EventView,
    MeView,
    OnboardingView,
    StepView,
)

router = APIRouter(prefix="/api", tags=["student"])


@router.get("/me")
async def me(person: Authenticated) -> MeView:
    return await person.users.me(person.user)


@router.post("/me/consent")
async def consent(person: Authenticated) -> MeView:
    await person.users.give_consent(person.user)
    return await person.users.me(person.user)


@router.patch("/me")
async def update_me(body: UpdateMe, person: Authenticated) -> MeView:
    await person.users.set_language(person.user, body.lang)
    return await person.users.me(person.user)


@router.get("/config")
async def config(person: Consenting) -> AppConfigView:
    return AppConfigService(person.config).view(lang=person.user.lang)


@router.get("/onboarding")
async def onboarding(person: Consenting) -> OnboardingView:
    return await OnboardingService(person.session, person.config, person.clock).progress(
        person.user
    )


@router.post("/onboarding/{key}/complete")
async def complete_step(key: StepKey, person: Consenting) -> StepView:
    return await OnboardingService(person.session, person.config, person.clock).complete_manual(
        person.user, key
    )


@router.get("/events")
async def events(person: Consenting, scope: Literal["upcoming", "past"] = "upcoming") -> EventList:
    items = await person.events.list_by_scope(scope, limit=MAX_PAGE)
    return EventList(items=list(await person.events.views(items, viewer=person.user)))


@router.get("/events/{event_id}")
async def event(event_id: EventId, person: Consenting) -> EventView:
    return await person.events.view(await person.events.require(event_id), viewer=person.user)


@router.put("/events/{event_id}/rsvp")
async def put_rsvp(event_id: EventId, person: Consenting) -> EventView:
    selected = await person.events.require(event_id)
    await RsvpService(person.session, person.config, person.clock).put(
        user=person.user, event=selected
    )
    return await person.events.view(selected, viewer=person.user)


@router.delete("/events/{event_id}/rsvp")
async def delete_rsvp(event_id: EventId, person: Consenting) -> EventView:
    selected = await person.events.require(event_id)
    await RsvpService(person.session, person.config, person.clock).delete(
        user=person.user, event=selected
    )
    return await person.events.view(selected, viewer=person.user)


@router.post("/checkins")
async def checkin(body: CheckinRequest, person: Consenting) -> CheckinResult:
    launch = parse_checkin_start_param(person.init_data.start_param)
    observed_at = (
        person.init_data.auth_date
        if body.method == "qr"
        and launch is not None
        and launch.event_id == body.event_id
        and launch.code == body.code
        else None
    )
    return await CheckinService(person.session, person.config, person.clock).check_in_view(
        user=person.user,
        code_observed_at=observed_at,
        **body.model_dump(),
    )
