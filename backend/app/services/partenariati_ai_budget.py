"""Budget FAIL-CLOSED della spesa LLM del modulo partenariati (docs/partenariati.md T6).

Ogni chiamata a un modello pagata dalla piattaforma (estrazione WP3, bozza
profilo WP4, posizioni e testi WP5, bozze WP10) passa da un'unica
prenotazione atomica a DB (`fn_partenariati_ai_prenota`, advisory lock
globale, giorno Europe/Rome):
- limite per richiedente e per owner sul servizio (NULL = nessun limite);
- budget giornaliero del GRUPPO: somma di `coalesce(cost_cents, riserva)`
  delle esecuzioni di oggi + la nuova riserva ≤ budget; budget ≤ 0 = spesa
  negata.
La riserva è il caso peggiore (`ai_prezzi.stima_cents`); a fine chiamata
`concludi` scrive il costo reale. Un costo `None` = esito ignoto (timeout,
errore di rete dopo l'invio): la riserva resta nel budget. `rate_limit_service`
(fail-open) non è MAI un tetto di spesa.

L'estrazione per bando (WP3) prenota con `fn_partenariato_prenota`, che chiama
la stessa funzione SQL insieme al claim del bando; qui passano i servizi che
non hanno un claim proprio.

Gruppi: `bando` (estrazioni utente/call/admin), `altri` (WP4/WP5/WP10),
`batch` (scheduler notturno), `valutazione` (script, budget passato a mano).
"""

from typing import Literal

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.services.partenariato_errori import raise_from_rpc

Gruppo = Literal["bando", "altri", "batch", "valutazione"]
StatoConclusione = Literal[
    "riusata", "conclusa", "nessun_segnale", "errore", "timeout", "interrotta"
]


def budget_cents_gruppo(gruppo: Gruppo) -> int:
    """Budget giornaliero del gruppo dalle Settings (centesimi USD). Il
    gruppo `valutazione` non ha un budget di configurazione: lo passa lo
    script, e qui vale 0 (spesa negata)."""
    settings = get_settings()
    return {
        "bando": settings.partenariato_budget_cents_giorno,
        "altri": settings.partenariati_ai_budget_cents_giorno_altri,
        "batch": settings.partenariato_batch_budget_cents_giorno,
    }.get(gruppo, 0)


def _uuid(valore) -> str | None:
    return str(valore) if valore else None


async def prenota(
    primary,
    *,
    servizio: str,
    origine: str,
    gruppo: Gruppo,
    budget_cents: int,
    costo_riservato_cents: int,
    richiedente: str | None,
    limite_richiedente: int | None,
    owner: str | None,
    limite_owner: int | None,
    company_id: str | None = None,
    bando_id: int | None = None,
) -> str:
    """Prenota una spesa LLM: id dell'esecuzione `in_corso`. I rifiuti della
    funzione SQL diventano `AppError` con il code della mappa del modulo
    (`ai_limite_giornaliero`, `ai_sospesa_oggi`, …); qualunque altro guasto
    è un 502: senza prenotazione non si chiama il modello."""
    try:
        resp = await primary.rpc(
            "fn_partenariati_ai_prenota",
            {
                "p_servizio": servizio,
                "p_origine": origine,
                "p_gruppo": gruppo,
                "p_budget_cents": int(budget_cents),
                "p_costo_riservato_cents": max(int(costo_riservato_cents), 0),
                "p_richiedente": _uuid(richiedente),
                "p_limite_richiedente": limite_richiedente,
                "p_owner": _uuid(owner),
                "p_limite_owner": limite_owner,
                "p_company": _uuid(company_id),
                "p_bando_id": bando_id,
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    esecuzione_id = resp.data
    if not isinstance(esecuzione_id, str) or not esecuzione_id:
        # Mai proseguire senza un'esecuzione registrata: la spesa non
        # sarebbe contata nel budget.
        raise UpstreamError()
    return esecuzione_id


async def concludi(
    primary,
    esecuzione_id: str,
    *,
    stato: StatoConclusione,
    cost_cents: int | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str | None = None,
    errore: str | None = None,
) -> None:
    """Chiude l'esecuzione con costo e token reali. `cost_cents=None` = costo
    ignoto: la riserva resta nel budget del giorno (fail-closed). Già chiusa
    → nessun effetto."""
    try:
        await primary.rpc(
            "fn_partenariati_ai_concludi",
            {
                "p_esecuzione_id": str(esecuzione_id),
                "p_stato": stato,
                "p_cost_cents": None if cost_cents is None else max(int(cost_cents), 0),
                "p_input_tokens": max(int(input_tokens or 0), 0),
                "p_output_tokens": max(int(output_tokens or 0), 0),
                "p_model": model,
                "p_errore": errore,
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
