import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.api.routers import (
    admin_plans,
    admin_users,
    bandi,
    company,
    family,
    health,
    lookups,
    me,
    plans,
)
from app.clients.supabase import create_primary_client, create_secondary_client
from app.core.config import get_settings
from app.core.errors import register_exception_handlers

logger = logging.getLogger("bandofit")

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.primary = await create_primary_client(settings)
    app.state.secondary = await create_secondary_client(settings)
    yield


app = FastAPI(
    title="BandoFit API",
    version="0.1.0",
    description="Backend della piattaforma BandoFit: catalogo bandi, utenti e abbonamenti.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.exception_handler(APIError)
async def postgrest_error_handler(_: Request, exc: APIError) -> JSONResponse:
    """Errori PostgREST non gestiti puntualmente dai servizi."""
    logger.error("Errore PostgREST: code=%s message=%s", exc.code, exc.message)
    if exc.code == "57014":  # statement timeout (3s per anon sul secondario)
        return JSONResponse(
            status_code=504,
            content={
                "error": {
                    "code": "search_timeout",
                    "message": "Ricerca troppo ampia: restringi i filtri e riprova",
                }
            },
        )
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "code": "upstream_error",
                "message": "Servizio dati momentaneamente non disponibile",
            }
        },
    )


@app.exception_handler(httpx.HTTPError)
async def httpx_error_handler(_: Request, exc: httpx.HTTPError) -> JSONResponse:
    logger.error("Errore di rete verso Supabase: %s", exc)
    return JSONResponse(
        status_code=504,
        content={
            "error": {
                "code": "upstream_timeout",
                "message": "Il servizio dati non risponde, riprova tra poco",
            }
        },
    )


for router in (
    health.router,
    plans.router,
    me.router,
    family.router,
    company.router,
    lookups.router,
    bandi.router,
    admin_users.router,
    admin_plans.router,
):
    app.include_router(router, prefix=API_PREFIX)
