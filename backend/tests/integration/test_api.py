"""Mini-app journeys through HTTP, real migrations, transactions and domain services."""

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus.api.app import create_app
from campus.config import Settings
from campus.db.models import User
from campus.domain.context import DomainConfig
from campus.max.initdata import launch_params, signature
from tests.integration.factories import World

TOKEN = "unit-test-token"


def headers(
    world: World,
    user: User,
    *,
    age: timedelta = timedelta(),
    start_param: str | None = None,
) -> dict[str, str]:
    values = {
        "auth_date": str(int((world.clock.now() - age).timestamp())),
        "user": json.dumps(
            {"id": user.max_user_id, "first_name": user.first_name, "language_code": user.lang}
        ),
    }
    if start_param is not None:
        values["start_param"] = start_param
    values["hash"] = signature(TOKEN, launch_params(list(values.items())))
    return {"X-Max-Init-Data": urlencode(values, quote_via=quote)}


@pytest.fixture
async def client(
    world: World, session: AsyncSession, example_config_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        max_bot_token=SecretStr(TOKEN),
        university_config_path=example_config_path,
    )
    factory = async_sessionmaker(
        bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    app = create_app(
        settings=settings, config=world.config, session_factory=factory, clock=world.clock
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as made:
        yield made


async def test_consent_language_and_all_student_reads(client: httpx.AsyncClient, world: World):
    user = await world.user(consent=False)
    await world.session.commit()
    auth = headers(world, user)
    assert (await client.get("/api/me", headers=auth)).json()["consent"] is False
    for path in ("/api/config", "/api/events", "/api/onboarding", "/api/org/events"):
        response = await client.get(path, headers=auth)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "consent_required"
    assert (await client.patch("/api/me", json={"lang": "en"}, headers=auth)).json()["lang"] == "en"
    response = await client.get("/api/events", headers=auth)
    assert response.json()["error"]["message"].isascii()
    assert (await client.post("/api/me/consent", headers=auth)).json()["consent"] is True
    assert (await client.post("/api/me/consent", headers=auth)).status_code == 200
    assert (await client.get("/api/config", headers=auth)).json()["event_kinds"]
    progress = (await client.get("/api/onboarding", headers=auth)).json()
    manual = next(step["key"] for step in progress["steps"] if step["type"] == "manual")
    assert (await client.post(f"/api/onboarding/{manual}/complete", headers=auth)).json()["done"]
    assert (await client.get("/api/events", headers=auth)).json() == {"items": []}
    assert (await client.get("/api/events?scope=broken", headers=auth)).json()["error"][
        "code"
    ] == "validation_error"
    assert (await client.get("/api/events/999999999", headers=auth)).json()["error"][
        "code"
    ] == "event_not_found"


async def test_organizer_event_rsvp_checkin_csv_and_qr(client: httpx.AsyncClient, world: World):
    organizer = await world.organizer()
    user = await world.user(first_name="=formula")
    await world.session.commit()
    owner = headers(world, organizer)
    student = headers(world, user)
    body = {
        "title": "Activity",
        "kind": "club",
        "starts_at": world.clock.now().isoformat(),
        "ends_at": (world.clock.now() + timedelta(hours=1)).isoformat(),
    }
    created = await client.post("/api/org/events", json=body, headers=owner)
    assert created.status_code == 200
    event_id = created.json()["id"]
    assert "qr_seed" not in created.text
    path = f"/api/org/events/{event_id}"
    assert (await client.get(path + "/qr", headers=owner)).json()["error"][
        "code"
    ] == "checkin_closed"
    assert (await client.patch(path, json={"checkin_open": True}, headers=owner)).json()[
        "checkin_open"
    ]
    assert len((await client.get("/api/org/events", headers=owner)).json()["items"]) == 1
    qr = (await client.get(path + "/qr", headers=owner)).json()
    assert len(qr["code"]) == 6
    assert (await client.post(path + "/qr/chat", headers=owner)).status_code == 202
    assert (await client.get(f"/api/events/{event_id}", headers=student)).status_code == 200
    assert (await client.put(f"/api/events/{event_id}/rsvp", headers=student)).json()["rsvp"]
    assert not (await client.delete(f"/api/events/{event_id}/rsvp", headers=student)).json()["rsvp"]
    checkin = {"event_id": event_id, "code": qr["code"], "method": "qr"}
    first = await client.post("/api/checkins", json=checkin, headers=student)
    assert first.status_code == 200
    assert first.json()["already"] is False
    assert (await client.post("/api/checkins", json=checkin, headers=student)).json()[
        "already"
    ] is True
    attendance = (await client.get(path + "/attendance", headers=owner)).json()
    assert attendance["checkin_count"] == 1
    assert attendance["items"][0]["first_name"] == "=formula"
    csv = await client.get(path + "/attendance.csv", headers=owner)
    assert csv.content.startswith(b"\xef\xbb\xbf")
    assert "'=formula" in csv.text
    assert csv.headers["content-type"].startswith("text/csv")
    assert (await client.get("/api/events", headers=student)).json()["items"][0][
        "attendees_count"
    ] == 1


async def test_an_expired_qr_returns_code_expired(client: httpx.AsyncClient, world: World) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    scanned_code = world.code_for(event)
    await world.session.commit()
    world.clock.advance(timedelta(seconds=world.config.checkin_code_step_seconds))

    response = await client.post(
        "/api/checkins",
        headers=headers(world, student, start_param=f"ci_{event.id}_{scanned_code}"),
        json={"event_id": event.id, "code": scanned_code, "method": "qr"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "code_expired"


async def test_a_qr_scanned_while_fresh_survives_the_signed_launch_delay(
    client: httpx.AsyncClient, world: World
) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    scanned_code = world.code_for(event)
    scan_headers = headers(world, student, start_param=f"ci_{event.id}_{scanned_code}")
    await world.session.commit()
    world.clock.advance(timedelta(seconds=15))

    response = await client.post(
        "/api/checkins",
        headers=scan_headers,
        json={"event_id": event.id, "code": scanned_code, "method": "qr"},
    )

    assert response.status_code == 200
    assert response.json()["already"] is False


async def test_signed_launch_time_only_applies_to_the_exact_qr(
    client: httpx.AsyncClient, world: World
) -> None:
    organizer = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    scanned_code = world.code_for(event)
    different_code = world.code_never_valid_for(event)
    launch_headers = headers(world, student, start_param=f"ci_{event.id}_{different_code}")
    await world.session.commit()
    world.clock.advance(timedelta(seconds=15))

    response = await client.post(
        "/api/checkins",
        headers=launch_headers,
        json={"event_id": event.id, "code": scanned_code, "method": "qr"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "code_expired"


async def test_finished_event_rejects_qr_chat_without_enqueueing(
    client: httpx.AsyncClient, world: World
) -> None:
    organizer = await world.organizer()
    event = await world.event(
        organizer=organizer,
        starts_in=-timedelta(hours=2),
        duration=timedelta(hours=1),
    )
    await world.session.commit()

    response = await client.post(
        f"/api/org/events/{event.id}/qr/chat", headers=headers(world, organizer)
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "checkin_window_over",
            "message": "Отметка на это событие уже закрылась.",
        }
    }
    assert await world.qr_displays.active_for(organizer_id=organizer.id, event_id=event.id) is None
    assert await world.outbox.count() == 0
    qr_response = await client.get(
        f"/api/org/events/{event.id}/qr", headers=headers(world, organizer)
    )
    assert qr_response.status_code == 403
    assert qr_response.json()["error"]["code"] == "checkin_window_over"


async def test_roles_and_ownership_block_all_private_routes(
    client: httpx.AsyncClient, world: World
):
    organizer = await world.organizer()
    other = await world.organizer()
    student = await world.user()
    event = await world.open_event_now(organizer=organizer)
    await world.session.commit()
    for method, suffix in (
        ("GET", "/qr"),
        ("POST", "/qr/chat"),
        ("GET", "/attendance"),
        ("GET", "/attendance.csv"),
        ("PATCH", ""),
    ):
        for user, code in ((other, "not_owner"), (student, "not_organizer")):
            response = await client.request(
                method,
                f"/api/org/events/{event.id}{suffix}",
                headers=headers(world, user),
                json={} if method == "PATCH" else None,
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == code
    response = await client.post("/api/org/invites", headers=headers(world, organizer))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_admin"


async def test_failed_guesses_are_committed_and_rate_limited(
    client: httpx.AsyncClient, world: World
):
    user = await world.user()
    organizer = await world.organizer()
    event = await world.open_event_now(organizer=organizer)
    invalid = world.code_never_valid_for(event)
    await world.session.commit()
    auth = headers(world, user)
    for _ in range(10):
        response = await client.post(
            "/api/checkins",
            headers=auth,
            json={"event_id": event.id, "code": invalid, "method": "code"},
        )
        assert response.status_code == 400
    response = await client.post(
        "/api/checkins",
        headers=auth,
        json={"event_id": event.id, "code": world.code_for(event), "method": "code"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "600"
    assert response.headers["Date"].endswith("GMT")


async def test_stale_launch_and_invalid_bodies(client: httpx.AsyncClient, world: World):
    user = await world.organizer()
    event = await world.event(organizer=user)
    await world.session.commit()
    response = await client.get("/api/me", headers=headers(world, user, age=timedelta(days=2)))
    assert response.status_code == 401
    auth = headers(world, user)
    for body in ({"title": None}, {"points": -1}, {"checkin_open": "false"}, {"qr_seed": "secret"}):
        response = await client.patch(f"/api/org/events/{event.id}", headers=auth, json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert "secret" not in response.text
    response = await client.post(
        "/api/checkins", headers=auth, json={"event_id": event.id, "code": 123456, "method": "qr"}
    )
    assert response.status_code == 422


async def test_admin_can_invite_without_organizer_role(world: World, example_config_path: Path):
    user = await world.user()
    await world.session.commit()
    config = DomainConfig(
        university=world.config.university,
        bot_username="campus_bot",
        admin_max_user_ids=frozenset({user.max_user_id}),
    )
    app = create_app(
        settings=Settings(
            database_url="postgresql+asyncpg://unused/unused",
            university_config_path=example_config_path,
            max_bot_token=SecretStr(TOKEN),
        ),
        config=config,
        clock=world.clock,
        session_factory=async_sessionmaker(
            bind=world.session.bind,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/org/invites", headers=headers(world, user))
        assert response.status_code == 200
        assert len(response.json()["token"]) >= 22
        assert "org_" in response.json()["deeplink"]


async def test_first_signed_contact_creates_user(client: httpx.AsyncClient, world: World):
    from tests.integration.factories import next_max_user_id

    first_contact = User(max_user_id=next_max_user_id(), first_name="New student", lang="en")
    auth = headers(world, first_contact)
    response = await client.get("/api/me", headers=auth)
    assert response.status_code == 200
    assert response.json()["consent"] is False
    assert response.json()["first_name"] == "New student"
    repeated = await client.get("/api/me", headers=auth)
    assert repeated.json()["id"] == response.json()["id"]
