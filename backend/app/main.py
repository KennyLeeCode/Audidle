"""FastAPI application factory.

Wires routers, CORS, logging, and the single exception handler that turns every
domain error into a consistent JSON body. Deliberately thin: no game logic and
no provider construction happen here.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import config as config_router
from app.api import game as game_router
from app.api import health as health_router
from app.api import search as search_router
from app.config.settings import get_settings
from app.core.errors import AudidleError
from app.dependencies import shutdown_providers, validate_provider_combination
from app.schemas.game import ErrorResponse

API_PREFIX = "/api"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate configuration at startup, release resources at shutdown."""
    validate_provider_combination()
    yield
    await shutdown_providers()


def create_app() -> FastAPI:
    """Build the application. A factory so tests can construct isolated apps."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    app = FastAPI(
        title="Audidle API",
        description=(
            "Backend for Audidle, a progressive clip music guessing game. "
            "The server is authoritative about the answer, stage progression, "
            "and win or loss."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AudidleError)
    async def handle_audidle_error(_: Request, error: AudidleError) -> JSONResponse:
        """Map any domain error to its declared status code and error body.

        Having this in one place is why services can raise plain exceptions and
        stay free of HTTP concerns.
        """
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(code=error.code, message=error.message).model_dump(),
        )

    app.include_router(health_router.router, prefix=API_PREFIX)
    app.include_router(config_router.router, prefix=API_PREFIX)
    app.include_router(search_router.router, prefix=API_PREFIX)
    app.include_router(game_router.router, prefix=API_PREFIX)

    return app


app = create_app()
