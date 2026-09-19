"""App factory. Importing or exporting OpenAPI never connects to the database."""

import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.utils import format_datetime
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from campus.api.routers import organizer, student
from campus.api.schemas import ErrorBody, Health
from campus.config import Settings, load_university_config
from campus.db.runtime import database_healthy
from campus.db.session import create_engine, create_session_factory
from campus.domain.clock import Clock, SystemClock
from campus.domain.context import CHECKIN_RATE_LIMIT_WINDOW, DomainConfig
from campus.domain.errors import DomainError, ValidationFailedError
from campus.i18n import translator

logger = logging.getLogger(__name__)

_TOO_MANY_REQUESTS: Final = 429
# Bodies are small by §7: the largest field any endpoint accepts is a 4000-character
# description. Anything past this is refused before it is read into memory.
MAX_REQUEST_BODY_BYTES: Final = 256 * 1024


def _route_template(scope: Scope) -> str | None:
    """The route pattern, never the resolved path: §7 keeps ids and codes out of the logs."""
    route = scope.get("route")
    return getattr(route, "path", None)


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
                # Assignment, not append: a second Date would violate RFC 9110 the moment
                # this runs behind a server that writes its own.
                headers = MutableHeaders(scope=message)
                headers["date"] = format_datetime(self.clock.now(), usegmt=True)
                headers["cache-control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, dated_send)
        except Exception as error:
            if scope["type"] != "http" or started:
                raise
            # Uvicorn otherwise logs even handled 500 tracebacks, potentially exposing SQL
            # parameters. End the HTTP failure here and log only its type.
            logger.error(
                "Request failed",
                extra={
                    "error_type": type(error).__name__,
                    "method": scope.get("method"),
                    "route": _route_template(scope),
                    # Frames carry file, line and source text — never parameter values.
                    "frames": traceback.format_tb(error.__traceback__),
                },
            )
            await Response(status_code=500)(scope, receive, dated_send)


class BodySizeLimitMiddleware:
    """Refuse an oversized body by its Content-Length, before anything reads it."""

    def __init__(self, app: ASGIApp, limit: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            declared = Headers(scope=scope).get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > self.limit:
                error = ValidationFailedError("body")
                response = JSONResponse(
                    {
                        "error": {
                            "code": error.code,
                            "message": translator().error("ru", error.message_key, **error.params),
                        }
                    },
                    status_code=error.http_status,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def error_response(request: Request, error: DomainError) -> Response:
    if not error.api_visible:
        logger.error("Internal domain failure", extra={"error_type": type(error).__name__})
        return Response(status_code=500)
    language = getattr(request.state, "lang", "ru")
    headers = (
        {"Retry-After": str(int(CHECKIN_RATE_LIMIT_WINDOW.total_seconds()))}
        if error.http_status == _TOO_MANY_REQUESTS
        else {}
    )
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
    expose_docs: bool = False,
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

    # §7 promises the mini-app a Date on every response and a Retry-After on 429; the types
    # are generated from this document, so both belong in it.
    date_header = {
        "Date": {"schema": {"type": "string"}, "description": "Server time, UTC (RFC 9110)."}
    }
    responses: dict[int | str, dict[str, Any]] = {
        status: {"model": ErrorBody, "headers": dict(date_header)}
        for status in (400, 401, 403, 404, 409, 422)
    }
    responses[429] = {
        "model": ErrorBody,
        "headers": {
            **date_header,
            "Retry-After": {
                "schema": {"type": "integer"},
                "description": "Seconds to wait before retrying.",
            },
        },
    }
    app = FastAPI(
        title="Campus API",
        version="0.2.0",
        lifespan=lifespan,
        # The contract is published as docs/openapi.json (§7). A live Swagger UI would put a
        # third-party CDN script on the mini-app's own origin for no one's benefit.
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
    )
    app.state.clock = active_clock
    app.state.settings = settings
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.add_middleware(DateHeaderMiddleware, clock=active_clock)
    app.add_middleware(BodySizeLimitMiddleware)

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
