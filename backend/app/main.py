import asyncio
import logging
from contextlib import asynccontextmanager, suppress

# I logger applicativi (bandofit.*) devono essere visibili nei log del
# container: senza questa configurazione i livelli INFO/WARNING dei moduli
# (email, auth, famiglia) non venivano emessi affatto.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.api.routers import (
    addons,
    admin_addons,
    admin_alerts,
    admin_payments,
    admin_plans,
    admin_users,
    ai_check,
    alerts,
    auth,
    bandi,
    billing,
    calendar,
    companies,
    company,
    consulting,
    family,
    health,
    job_positions,
    lookups,
    me,
    notifications,
    payments,
    plans,
    preferences,
    progettista,
    saved_bandi,
    webhooks,
)
from app.clients.anthropic_ai import AiCheckClient
from app.clients.openapi import OpenapiClient
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
    app.state.openapi = OpenapiClient(settings)
    if not app.state.openapi.enabled:
        logger.warning("openapi.it non configurato: import dati e verifica CF disattivati")
    app.state.ai = AiCheckClient(settings)
    if not app.state.ai.enabled:
        logger.warning("API Anthropic non configurata: AI-check disattivato")
    # Import locale come alert_scheduler: un import top-level qui sarebbe un
    # nuovo E402 sulla baseline ruff (11, congelata).
    from app.clients.revolut import RevolutClient

    app.state.revolut = RevolutClient(settings)
    if not app.state.revolut.enabled:
        logger.warning("Revolut non configurato: modulo pagamenti disattivato")
    elif app.state.revolut.sandbox and settings.env.strip().lower() == "production":
        # Rete di sicurezza del deploy: incassare in sandbox = non incassare.
        logger.error("ATTENZIONE: ENV=production ma Revolut è in SANDBOX")
    # Scheduler degli alert nuovi-bandi: task in-process (uvicorn è un solo
    # processo); il claim a DB protegge comunque da esecuzioni concorrenti.
    # Import locale: in questo modulo ogni import top-level dopo basicConfig
    # aggiungerebbe un E402 alla baseline ruff.
    from app.services import alert_scheduler

    app.state.alert_task = None
    if settings.alert_scheduler_attivo:
        app.state.alert_task = asyncio.create_task(
            alert_scheduler.run_forever(app.state.primary, app.state.secondary)
        )
    # Scheduler pagamenti (rinnovi, dunning, cambi differiti): parte solo se il
    # provider è configurato — senza, non c'è nulla da addebitare.
    from app.services import payment_scheduler

    app.state.payment_task = None
    if settings.payment_scheduler_attivo and app.state.revolut.enabled:
        # Il worker fatture (passo 6) usa lo stesso client openapi dell'app.
        payment_scheduler.imposta_openapi(app.state.openapi)
        app.state.payment_task = asyncio.create_task(
            payment_scheduler.run_forever(app.state.primary, app.state.revolut)
        )
    yield
    for task_attr in ("alert_task", "payment_task"):
        task = getattr(app.state, task_attr, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    await app.state.openapi.aclose()
    await app.state.ai.aclose()
    await app.state.revolut.aclose()


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
    auth.router,
    alerts.router,
    alerts.me_router,
    plans.router,
    job_positions.router,
    addons.router,
    me.router,
    family.router,
    company.router,
    companies.router,
    preferences.router,
    notifications.router,
    consulting.router,
    progettista.router,
    ai_check.router,
    saved_bandi.router,
    calendar.router,
    lookups.router,
    bandi.router,
    billing.router,
    payments.router,
    webhooks.router,
    admin_users.router,
    admin_plans.router,
    admin_addons.router,
    admin_alerts.router,
    admin_payments.router,
):
    app.include_router(router, prefix=API_PREFIX)
