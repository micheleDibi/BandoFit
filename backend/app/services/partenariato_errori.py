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
    # WP4 — profilo partner, consensi, referente e bozza AI (migration 0035)
    "company_not_found": (404, "not_found", "Azienda non trovata"),
    "azione_non_valida": (400, "bad_request", "Operazione non valida"),
    "origine_non_valida": (400, "bad_request", "Origine del consenso non valida"),
    "versione_non_valida": (400, "bad_request", "Versione dell'informativa non valida"),
    "attore_non_titolare": (
        403,
        "forbidden",
        "Il profilo partner lo gestisce il titolare dell'azienda",
    ),
    "anonimato_obbligatorio": (
        400,
        "anonimato_obbligatorio",
        "Scegli se mostrare il nome dell'azienda o restare anonima",
    ),
    "identita_non_verificata": (
        409,
        "identita_non_verificata",
        "Per comparire come partner importa prima i dati ufficiali dell'azienda dalla "
        "partita IVA: l'impresa deve risultare attiva nel Registro Imprese",
    ),
    "rappresentante_non_verificato": (
        409,
        "rappresentante_non_verificato",
        "Per mostrare il nome dell'azienda devi risultarne legale rappresentante: verifica "
        "il tuo codice fiscale nel profilo. Puoi comunque comparire in forma anonima",
    ),
    "profilo_sospeso": (
        409,
        "profilo_sospeso",
        "Il profilo partner è sospeso: per ora non può tornare visibile",
    ),
    "referente_non_valido": (
        400,
        "referente_non_valido",
        "La persona scelta deve essere un membro attivo con accesso a questa azienda",
    ),
    "nessuna_proposta_referente": (
        409,
        "nessuna_proposta_referente",
        "Non c'è nessuna proposta di referente da confermare",
    ),
    "bozza_in_corso": (
        409,
        "bozza_in_corso",
        "La bozza del profilo è già in preparazione: attendi qualche istante",
    ),
    "ai_limite_azienda": (
        429,
        "ai_limite_giornaliero",
        "Hai raggiunto le bozze di oggi per questa azienda: riprova domani",
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
