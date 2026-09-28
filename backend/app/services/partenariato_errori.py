"""Errori delle funzioni SQL del modulo partenariati (docs/partenariati.md, T2).

Mappa UNICA per tutto il modulo: le RPC sollevano con `detail` = codice
macchina e qui diventa un `AppError(status, code, messaggio)`. I `code` sono
specifici perché il frontend ci ramifica sopra (niente `rate_limited`
generico). Ogni WP successivo AGGIUNGE voci a `RPC_ERRORS`, senza cambiare
quelle esistenti: un detail non mappato è un guasto interno (log + 502).
"""

import logging
from typing import NoReturn

from postgrest.exceptions import APIError

from app.core.errors import AppError, UpstreamError

logger = logging.getLogger("bandofit.partenariati")

_LIMITE_GIORNALIERO = "Hai raggiunto il numero di analisi di oggi: riprova domani"

# detail della RPC → (status HTTP, code macchina, messaggio per l'utente)
RPC_ERRORS: dict[str, tuple[int, str, str]] = {
    # WP3 — estrazione delle regole di partenariato e budget AI del modulo
    "partenariato_cooldown": (
        429,
        "partenariato_cooldown",
        "Le regole di questo bando sono state analizzate da poco: riprova più tardi",
    ),
    "ai_limite_utente": (429, "ai_limite_giornaliero", _LIMITE_GIORNALIERO),
    "ai_limite_owner": (429, "ai_limite_giornaliero", _LIMITE_GIORNALIERO),
    "ai_budget_esaurito": (
        429,
        "ai_sospesa_oggi",
        "L'analisi automatica è sospesa per oggi: riprova domani",
    ),
}


def raise_from_rpc(exc: APIError) -> NoReturn:
    """Traduce l'errore di una funzione SQL del modulo in `AppError`.

    Detail non mappato (o assente) → log con code e detail, poi
    `UpstreamError`: il chiamante non deve mai vedere il testo grezzo di
    Postgres."""
    detail = (exc.details or "").strip()
    mappato = RPC_ERRORS.get(detail)
    if mappato:
        raise AppError(*mappato) from exc
    logger.error(
        "partenariati: errore RPC non mappato (code=%s, detail=%s)", exc.code, detail or None
    )
    raise UpstreamError() from exc
