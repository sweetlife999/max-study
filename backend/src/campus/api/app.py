"""App factory. Importing or exporting OpenAPI never connects to the database."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.utils import format_datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from campus.api.routers import organizer, student
from campus.api.schemas import ErrorBody, Health
from campus.config import Settings, load_university_config
from campus.db.runtime import database_healthy
from campus.db.session import create_engine, create_session_factory
from campus.domain.clock import Clock, SystemClock
from campus.domain.context import DomainConfig
from campus.domain.errors import DomainError, ValidationFailedError
from campus.i18n import translator

logger = logging.getLogger(__name__)


class DateHeaderMiddleware:
    """Include server time even on an unexpected exception handled by Starlette."""

    def __init__(self, app: ASGIApp, clock: Clock) -> None:
        self.app = app
        self.clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def dated_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                headers.append((b"date", format_datetime(self.clock.now(), usegmt=True).encode()))
                headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, dated_send)
        except Exception as error:
            if scope["type"] != "http" or started:
                raise
            # Uvicorn otherwise logs even handled 500 tracebacks, potentially exposing SQL
            # parameters. End the HTTP failure here and log only its type.
            logger.error("Request failed", extra={"error_type": type(error).__name__})
            await Response(status_code=500)(scope, receive, dated_send)


def error_response(request: Request, error: DomainError) -> Response:
    if not error.api_visible:
        logger.error("Internal domain failure", extra={"error_type": type(error).__name__})
        return Response(status_code=500)
    language = getattr(request.state, "lang", "ru")
    headers = {"Retry-After": "600"} if error.http_status == 429 else {}
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                "message": translator().error(language, error.message_key, **error.params),
            }
        },
        status_code=error.http_status,
        headers=headers,
    )


def create_app(
    *,
    settings: Settings | None = None,
    config: DomainConfig | None = None,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    active_clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        actual_settings = settings or Settings.from_env()
        actual_settings.require_bot_token()
        app.state.settings = actual_settings
        app.state.config = config or DomainConfig.from_settings(
            actual_settings, load_university_config(actual_settings.university_config_path)
        )
        owned_engine = engine is None
        actual_engine = engine or create_engine(actual_settings.database_url)
        app.state.engine = actual_engine
        app.state.session_factory = session_factory or create_session_factory(actual_engine)
        try:
            yield
        finally:
            if owned_engine:
                await actual_engine.dispose()

    responses: dict[int | str, dict[str, Any]] = {
        status: {"model": ErrorBody} for status in (400, 401, 403, 404, 409, 422, 429)
    }
    app = FastAPI(title="Campus API", version="0.2.0", lifespan=lifespan)
    app.state.clock = active_clock
    app.state.settings = settings
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.add_middleware(DateHeaderMiddleware, clock=active_clock)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> Response:
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error: RequestValidationError) -> Response:
        # Never serialize rejected values: they can contain launch data or check-in codes.
        return error_response(request, ValidationFailedError("request"))

    app.include_router(student.router, responses=responses)
    app.include_router(organizer.router, responses=responses)

    @app.get("/health", response_model=Health, responses={503: {"model": Health}})
    async def health() -> JSONResponse:
        healthy = await database_healthy(app.state.engine)
        value = "ok" if healthy else "unavailable"
        return JSONResponse({"status": value, "db": value}, status_code=200 if healthy else 503)

    return app
