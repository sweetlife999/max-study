"""Signed identity, consent and role dependencies; a unit of work per request."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from campus.api.schemas import EventId
from campus.db.models import Event, User
from campus.db.session import domain_scope
from campus.domain.clock import Clock
from campus.domain.context import DomainConfig
from campus.domain.errors import AdminRequiredError, InvalidInitDataError
from campus.domain.services import EventService, OrganizerService, UserService
from campus.max.initdata import InitData, verify_init_data

init_data_header = APIKeyHeader(name="X-Max-Init-Data", scheme_name="MaxInitData", auto_error=False)


async def identity(
    request: Request, raw: Annotated[str | None, Security(init_data_header)]
) -> InitData:
    if not raw or len(raw) > 16384:
        raise InvalidInitDataError()
    settings = request.app.state.settings
    verified = verify_init_data(
        raw,
        bot_token=settings.require_bot_token(),
        now=request.app.state.clock.now(),
        ttl_seconds=settings.init_data_ttl_seconds,
    )
    request.state.lang = request.app.state.config.language_or_default(verified.user.language_code)
    return verified


async def session(
    request: Request, _identity: Annotated[InitData, Depends(identity)]
) -> AsyncIterator[AsyncSession]:
    async with domain_scope(request.app.state.session_factory) as unit:
        yield unit


@dataclass(frozen=True)
class Actor:
    user: User
    session: AsyncSession
    config: DomainConfig
    clock: Clock
    init_data: InitData

    @property
    def users(self) -> UserService:
        return UserService(self.session, self.config, self.clock)

    @property
    def events(self) -> EventService:
        return EventService(self.session, self.config, self.clock)

    @property
    def organizers(self) -> OrganizerService:
        return OrganizerService(self.session, self.config, self.clock)


async def actor(
    request: Request,
    verified: Annotated[InitData, Depends(identity)],
    unit: Annotated[AsyncSession, Depends(session, scope="function")],
) -> Actor:
    config = request.app.state.config
    clock = request.app.state.clock
    user = await UserService(unit, config, clock).get_or_create(
        max_user_id=verified.user.id,
        first_name=verified.user.first_name,
        lang=verified.user.language_code,
    )
    request.state.lang = user.lang
    return Actor(user, unit, config, clock, verified)


async def consenting(person: Annotated[Actor, Depends(actor, scope="function")]) -> Actor:
    person.users.require_consent(person.user)
    return person


async def organizer(person: Annotated[Actor, Depends(consenting, scope="function")]) -> Actor:
    await person.organizers.require_organizer(person.user)
    return person


async def admin(person: Annotated[Actor, Depends(consenting, scope="function")]) -> Actor:
    if not person.users.is_admin(person.user):
        raise AdminRequiredError()
    return person


async def owned_event(
    event_id: EventId, person: Annotated[Actor, Depends(organizer, scope="function")]
) -> Event:
    event = await person.events.require(event_id)
    person.events.require_owner(event, person.user)
    return event


Authenticated = Annotated[Actor, Depends(actor, scope="function")]
Consenting = Annotated[Actor, Depends(consenting, scope="function")]
Organizer = Annotated[Actor, Depends(organizer, scope="function")]
Admin = Annotated[Actor, Depends(admin, scope="function")]
OwnedEvent = Annotated[Event, Depends(owned_event, scope="function")]
