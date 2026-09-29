"""Risoluzione dei miss sul catalogo bandi (DB secondario, rilascio R0-b).

Uno slug o un id che non trova più il bando vivo può essere stato:
- rinominato o fuso → `bando_slug_storico` con esito '301' punta al master corrente;
- ritirato su richiesta scritta → `bando_slug_storico` con esito '410';
- fuso in un altro bando → `bando_fusione` (per `slug_originale` o per `bando_id`).

Contratto del produttore v11, §6.2 e §6.3: il produttore appiattisce le catene,
quindi si risolve una volta sola, mai in modo ricorsivo. Si interroga solo sul
miss: il caso normale resta una sola richiesta.

Regola difensiva: gli errori delle letture di risoluzione (storico, fusione,
riletta del master) finiscono nel log e valgono come «non risolto». Una di
queste letture non deve mai trasformare un 404 in un 502/504. Nel log vanno
solo tabella e codice dell'errore: il messaggio di PostgREST può riportare lo
slug richiesto, che arriva dall'URL.

Il modulo non importa `bandi_service` (lo usa, non il contrario).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from postgrest.exceptions import APIError

from app.core.errors import BandoRitiratoError, NotFoundError

logger = logging.getLogger("bandofit.bandi_risoluzione")

STORICO_SELECT = "slug,bando_id,esito"
FUSIONE_SELECT = "bando_id,master_id,master_slug"

# Lunghezza massima del riferimento (slug) scritto nel log.
_LOG_RIF_MAX = 100


@dataclass(frozen=True)
class EsitoSlug:
    esito: Literal["spostato", "ritirato"]
    bando_id: int | None  # valorizzato solo per "spostato"


@dataclass(frozen=True)
class Fusione:
    master_id: int
    master_slug: str | None


def _intero(valore: Any) -> bool:
    """Id valido: intero JSON (i bigint di PostgREST arrivano come numero).
    `bool` è sottoclasse di `int` e va escluso esplicitamente."""
    return isinstance(valore, int) and not isinstance(valore, bool)


async def _leggi(query, tabella: str, rif: str) -> list | None:
    """Esegue una lettura di risoluzione. Su errore scrive un WARNING con
    tabella e codice (mai il messaggio di PostgREST) e restituisce None."""
    try:
        resp = await query.execute()
    except APIError as exc:
        codice = exc.code or "sconosciuto"
    except httpx.HTTPError as exc:
        codice = type(exc).__name__
    else:
        return resp.data or []
    logger.warning(
        "lettura di risoluzione non riuscita: tabella=%s codice=%s rif=%r",
        tabella,
        codice,
        rif[:_LOG_RIF_MAX],
    )
    return None


async def risolvi_slug(secondary, slug: str) -> EsitoSlug | None:
    """Dove porta uno slug che non trova più un bando vivo.

    None = non risolto (nessuna riga, righe malformate o errore di lettura).
    Se la lettura dello storico fallisce non si passa alla fusione: lo slug
    potrebbe essere ritirato e non si deve mostrare un master al suo posto.
    """
    righe = await _leggi(
        secondary.table("bando_slug_storico")
        .select(STORICO_SELECT)
        .eq("slug", slug)
        .limit(1),
        "bando_slug_storico",
        slug,
    )
    if righe is None:
        return None
    riga = righe[0] if righe and isinstance(righe[0], dict) else None
    if riga is not None:
        esito = riga.get("esito")
        if esito == "301" and _intero(riga.get("bando_id")):
            return EsitoSlug("spostato", riga["bando_id"])
        if esito == "410":
            return EsitoSlug("ritirato", None)
        # 'annullato', valore sconosciuto o 301 malformato: si prova la fusione.

    righe = await _leggi(
        secondary.table("bando_fusione")
        .select(FUSIONE_SELECT)
        .eq("slug_originale", slug)
        .limit(1),
        "bando_fusione",
        slug,
    )
    if not righe or not isinstance(righe[0], dict):
        return None
    master_id = righe[0].get("master_id")
    if not _intero(master_id):
        return None
    return EsitoSlug("spostato", master_id)


async def risolvi_fusioni(secondary, ids: Iterable[int]) -> dict[int, Fusione]:
    """Master correnti degli id fusi: `{bando_id: Fusione}`.

    Solo gli id presenti in `bando_fusione` compaiono nel risultato; righe
    malformate ignorate; errore di lettura → `{}`. Nessuna query se `ids`
    è vuoto.
    """
    richiesti = list(dict.fromkeys(i for i in ids if _intero(i)))
    if not richiesti:
        return {}
    righe = await _leggi(
        secondary.table("bando_fusione")
        .select(FUSIONE_SELECT)
        .in_("bando_id", richiesti),
        "bando_fusione",
        f"{len(richiesti)} id",
    )
    if not righe:
        return {}
    ammessi = set(richiesti)
    esito: dict[int, Fusione] = {}
    for riga in righe:
        if not isinstance(riga, dict):
            continue
        bando_id = riga.get("bando_id")
        master_id = riga.get("master_id")
        if not _intero(bando_id) or bando_id not in ammessi or not _intero(master_id):
            continue
        master_slug = riga.get("master_slug")
        esito[bando_id] = Fusione(
            master_id, master_slug if isinstance(master_slug, str) and master_slug else None
        )
    return esito


async def carica_per_slug(secondary, slug: str, select: str) -> dict:
    """Riga del bando pubblicato per slug, risolvendo i miss.

    - slug corrente → la sua riga;
    - slug spostato (storico 301 o fusione) → la riga del master, il cui
      `slug` è quello canonico;
    - slug ritirato → `BandoRitiratoError` (410);
    - altrimenti → `NotFoundError` (404).

    Gli errori della prima lettura NON si intercettano (vanno all'handler
    globale come prima di R0-b); quelli della risoluzione sì.
    """
    resp = (
        await secondary.table("bando")
        .select(select)
        .eq("slug", slug)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if resp.data:
        return dict(resp.data[0])

    esito = await risolvi_slug(secondary, slug)
    if esito is None:
        raise NotFoundError("Bando non trovato")
    if esito.esito == "ritirato":
        raise BandoRitiratoError()

    # Una sola risoluzione: il master si rilegge per id, mai di nuovo per slug.
    righe = await _leggi(
        secondary.table("bando")
        .select(select)
        .eq("id", esito.bando_id)
        .eq("stato_processing", "completed")
        .not_.is_("slug", "null")
        .limit(1),
        "bando",
        slug,
    )
    if not righe or not isinstance(righe[0], dict):
        raise NotFoundError("Bando non trovato")
    return dict(righe[0])
