"""Transport guarantees independent of PostgreSQL."""

from pathlib import Path

import httpx
from pydantic import SecretStr

from campus.api.app import create_app
from campus.config import Settings


async def test_missing_and_forged_initdata_are_closed_401_errors(example_config_path: Path):
    app = create_app(
        settings=Settings(
            database_url="postgresql+asyncpg://unused/unused",
            university_config_path=example_config_path,
            max_bot_token=SecretStr("test"),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for headers in ({}, {"X-Max-Init-Data": "hash=secret-code"}):
            response = await client.get("/api/me", headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "invalid_init_data"
            assert "Date" in response.headers
            assert "secret-code" not in response.text


def test_openapi_exposes_complete_contract():
    document = create_app().openapi()
    assert len(document["paths"]) == 17
    assert document["paths"]["/api/config"]["get"]["responses"]["200"]
    assert document["paths"]["/api/me"]["get"]["security"] == [{"MaxInitData": []}]
    for schema in document["components"]["schemas"].values():
        assert "qr_seed" not in schema.get("properties", {})


async def test_health_and_database_unavailable(monkeypatch):
    from unittest.mock import AsyncMock

    from campus.api import app as app_module

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for healthy, status in ((True, 200), (False, 503)):
            monkeypatch.setattr(app_module, "database_healthy", AsyncMock(return_value=healthy))
            response = await client.get("/health")
            assert response.status_code == status
            assert response.json()["db"] == ("ok" if healthy else "unavailable")
            assert "Date" in response.headers


async def test_internal_failures_expose_no_secrets():
    from campus.api.auth import actor
    from campus.domain.errors import UserNotFoundError

    def failure(error: Exception):
        async def fail():
            raise error

        return fail

    for error in (UserNotFoundError("secret"), RuntimeError("secret")):
        app = create_app()
        app.dependency_overrides[actor] = failure(error)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/me")
            assert response.status_code == 500
            assert response.text == ""
            assert "Date" in response.headers


def test_openapi_export_is_reproducible():
    import json

    from campus.api.openapi import export

    assert export() == export()
    assert json.loads(export())["info"]["title"] == "Campus API"


async def test_lifespan_validates_config_and_disposes_owned_engine(
    example_config_path: Path, monkeypatch
):
    from unittest.mock import AsyncMock, Mock

    from campus.api import app as app_module

    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        university_config_path=example_config_path,
        max_bot_token=SecretStr("test"),
    )
    engine = Mock(dispose=AsyncMock())
    factory = Mock()
    monkeypatch.setattr(app_module, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(app_module, "create_session_factory", Mock(return_value=factory))
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        assert app.state.config.university.languages
        assert app.state.session_factory is factory
    engine.dispose.assert_awaited_once()


def test_openapi_cli_checks_staleness(tmp_path: Path, monkeypatch):
    import pytest

    from campus.api.openapi import main

    artifact = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["openapi", str(artifact)])
    main()
    monkeypatch.setattr("sys.argv", ["openapi", "--check", str(artifact)])
    main()
    artifact.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as failure:
        main()
    assert failure.value.code == 1
