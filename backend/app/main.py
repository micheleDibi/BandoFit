import asyncio
import logging
from contextlib import asynccontextmanager, suppress

# I logger applicativi (bandofit.*) devono essere visibili nei log del
# container: senza questa configurazione i livelli INFO/WARNING dei moduli
# (email, auth, famiglia) non venivano emessi affatto.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpcore, hpack e h2 (HTTP/2 dei client Supabase) scrivono solo a DEBUG,
# header compresi: fissati a WARNING restano muti anche se un domani il
# livello del root scende.
for _nome in ("httpcore", "hpack", "h2"):
    logging.getLogger(_nome).setLevel(logging.WARNING)

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.api.routers import (
    addons,
    admin_addons,
    admin_alerts,
    admin_partenariati,
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
    entitlements,
    family,
    health,
    job_positions,
    lookups,
    me,
    notifications,
    partenariati,
    partenariati_bandi,
    partenariati_bozze,
    partenariati_candidature,
    partenariati_chat,
    partenariati_consorzio,
    partenariati_email,
    partenariati_scoperta,
    partner_calls,
    partner_profile,
    payments,
    plans,
    preferences,
    progettista,
    saved_bandi,
    webhooks,
)
from app.clients.anthropic_ai import AiCheckClient
from app.clients.openapi import OpenapiClient, url_per_log
from app.clients.supabase import create_primary_client, create_secondary_client
from app.core.config import get_settings
from app.core.errors import register_exception_handlers

logger = logging.getLogger("bandofit")

API_PREFIX = "/api/v1"


class _AccessiSenzaQuery(logging.Filter):
    """Il log di accesso di uvicorn scrive il path delle richieste in entrata
    con la query: il path resta, la query va via. Vale per tutte le rotte: i
    log non contengono query string (per le chiamate in uscita lo fa il filtro
    di httpx in clients/openapi.py)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                url_per_log(arg) if isinstance(arg, str) and "?" in arg else arg
                for arg in args
            )
        return True


_FILTRO_ACCESSI = _AccessiSenzaQuery()
_logger_accessi = logging.getLogger("uvicorn.access")
if _FILTRO_ACCESSI not in _logger_accessi.filters:
    _logger_accessi.addFilter(_FILTRO_ACCESSI)


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
        app.state.payment_task = asyncio.create_task(
            payment_scheduler.run_forever(app.state.primary, app.state.revolut)
        )
    # Scheduler del modulo partenariati (failsafe e batch delle estrazioni):
    # solo con il modulo acceso. Import locale come gli altri scheduler.
    app.state.partenariati_task = None
    if settings.partenariati_attivo and settings.partenariati_scheduler_attivo:
        from app.services import partenariati_scheduler

        app.state.partenariati_task = asyncio.create_task(
            partenariati_scheduler.run_forever(
                app.state.primary, app.state.secondary, app.state.ai
            )
        )
    # Rimappatura dei bandi fusi (contratto DB bandi §6.2): solo in `prova` o
    # `attiva`; spenta di default. Un valore non ammesso non blocca l'avvio.
    from app.services import catalogo_scheduler

    app.state.catalogo_task = None
    modalita = settings.rimappatura_fusi_modalita
    if modalita not in catalogo_scheduler.MODALITA:
        logger.error("RIMAPPATURA_FUSI_MODALITA non valida (ammessi: spenta, prova, attiva): "
                     "rimappatura dei bandi fusi spenta")
    elif modalita != "spenta":
        app.state.catalogo_task = asyncio.create_task(
            catalogo_scheduler.run_forever(app.state.primary, app.state.secondary)
        )
    # Failsafe del bilancio ufficiale: ogni 10 minuti fa avanzare le richieste
    # aperte anche dopo un riavvio (il follower in-process si perde). Solo a
    # storico acceso, come le rotte. Import locale come gli altri scheduler.
    app.state.bilancio_ufficiale_task = None
    if settings.bilanci_storico_attivo:
        from app.services import bilancio_ufficiale_scheduler

        app.state.bilancio_ufficiale_task = asyncio.create_task(
            bilancio_ufficiale_scheduler.run_forever(app.state.primary, app.state.openapi)
        )
    yield
    for task_attr in (
        "alert_task", "payment_task", "partenariati_task", "catalogo_task",
        "bilancio_ufficiale_task",
    ):
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
    # Mai `str(exc)` di un HTTPStatusError: contiene l'URL completo, query
    # compresa. Nel log solo classe, metodo, URL ripulito e stato.
    try:
        dove = f"{exc.request.method} {url_per_log(exc.request.url)}"
    except RuntimeError:  # eccezione senza richiesta associata
        dove = "richiesta non nota"
    if isinstance(exc, httpx.HTTPStatusError):
        dettaglio = f"stato {exc.response.status_code}"
    else:
        dettaglio = str(exc)
    logger.error(
        "Errore di rete verso Supabase: %s %s: %s", type(exc).__name__, dove, dettaglio
    )
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
    entitlements.router,
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
    partenariati_bandi.router,
    partenariati.router,
    partner_profile.router,
    partner_calls.router,
    partenariati_scoperta.router,
    partenariati_candidature.router,
    partenariati_chat.router,
    partenariati_consorzio.router,
    partenariati_bozze.router,
    partenariati_email.router,
    billing.router,
    payments.router,
    webhooks.router,
    admin_users.router,
    admin_plans.router,
    admin_addons.router,
    admin_alerts.router,
    admin_payments.router,
    admin_partenariati.router,
):
    app.include_router(router, prefix=API_PREFIX)
