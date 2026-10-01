"""Lookup delle faccette di filtro (DB secondario), con cache in-process.

I valori cambiano raramente (regioni, settori, ...): una cache TTL di un'ora
evita 7 round-trip a ogni apertura della pagina bandi.

Un errore di contratto del catalogo (colonna o tabella sparite, permesso
negato: `42703`, `42501`, `PGRST2xx`): si serve la cache scaduta se c'è;
senza cache, chi legge e mostra (`degrada=True`: elenco, dettaglio, facet,
`GET /lookups`) riceve liste vuote, tutti gli altri (import a pagamento,
salvataggi che convalidano gli id, alert) un 503 ritentabile, così nessuno
scrive dati parziali né spende credito. Il nuovo tentativo si rinvia di un
minuto per non ripetere sette letture a ogni richiesta. Errori di rete,
timeout e ogni altro codice risalgono come prima.
"""

import asyncio
import logging
import time

from postgrest.exceptions import APIError

from app.core.errors import CatalogoNonDisponibileError
from app.schemas.bando import LookupsOut
from app.services.postgrest_errori import errore_di_contratto

logger = logging.getLogger("bandofit.lookups")

_CACHE_TTL_SECONDS = 3600
_RINVIO_ERRORE_SECONDS = 60

_cache: LookupsOut | None = None
_cache_at: float = 0.0
_errore_at: float | None = None
_lock = asyncio.Lock()


def _vuoti() -> LookupsOut:
    return LookupsOut(
        regioni=[],
        settori=[],
        beneficiari=[],
        codici_ateco=[],
        tipologie_bando=[],
        modalita_erogazione=[],
        programmi=[],
    )


async def _fetch_all(secondary) -> LookupsOut:
    async def rows(table: str, select: str, order: str) -> list[dict]:
        resp = await secondary.table(table).select(select).order(order).execute()
        return resp.data

    (
        regioni,
        settori,
        beneficiari,
        codici_ateco,
        tipologie,
        modalita,
        programmi,
    ) = await asyncio.gather(
        rows("regioni", "id,nome", "nome"),
        rows("settori", "id,nome", "nome"),
        rows("beneficiari", "id,nome", "nome"),
        rows("codici_ateco", "id,codice,descrizione", "codice"),
        rows("tipologie_bando", "id,nome", "id"),
        rows("modalita_erogazione", "id,nome", "id"),
        rows("programmi", "id,nome", "nome"),
    )
    return LookupsOut(
        regioni=regioni,
        settori=settori,
        beneficiari=beneficiari,
        codici_ateco=codici_ateco,
        tipologie_bando=tipologie,
        modalita_erogazione=modalita,
        programmi=programmi,
    )


def _cache_valida() -> bool:
    return _cache is not None and (time.monotonic() - _cache_at) < _CACHE_TTL_SECONDS


def _in_rinvio() -> bool:
    return _errore_at is not None and (time.monotonic() - _errore_at) < _RINVIO_ERRORE_SECONDS


def in_degrado() -> bool:
    """Vero dall'ultimo errore di contratto fino alla prima lettura riuscita:
    si stanno servendo la cache scaduta o liste vuote."""
    return _errore_at is not None


def vuoti_in_degrado(lookups: LookupsOut) -> bool:
    """Vero se `lookups` sono le liste vuote del degrado (catalogo non
    leggibile e nessuna cache). Va chiamata subito dopo `get_lookups`, senza
    `await` in mezzo. Con questi lookup i facet dell'azienda perderebbero le
    divisioni ATECO secondarie: chi li usa non li calcola (nessun badge,
    facet vuoti) invece di mostrarli parziali."""
    return in_degrado() and not any(
        getattr(lookups, campo, None) for campo in LookupsOut.model_fields
    )


def _senza_cache(degrada: bool, codice: str | None) -> LookupsOut:
    """Esito senza cache: liste vuote per chi degrada, altrimenti 503. Il log
    solo al momento dell'errore (`codice`), non a ogni richiesta rinviata."""
    if degrada:
        if codice:
            logger.error("lookup del catalogo non leggibili (%s): liste vuote", codice)
        return _vuoti()
    if codice:
        logger.error("lookup del catalogo non leggibili (%s): operazione rifiutata", codice)
    raise CatalogoNonDisponibileError()


async def get_lookups(secondary, *, degrada: bool = False) -> LookupsOut:
    """I lookup dalla cache (entro il TTL) o dal catalogo. Su un errore di
    contratto: la cache scaduta se c'è (WARNING, per tutti); senza cache,
    con `degrada=True` liste vuote (ERROR; solo per chi legge e mostra),
    altrimenti `CatalogoNonDisponibileError` (503 ritentabile: chi scrive o
    paga non deve farlo con dati parziali). Nel minuto successivo non si
    ritenta e vale lo stesso esito."""
    global _cache, _cache_at, _errore_at
    if _cache_valida():
        return _cache
    async with _lock:
        if _cache_valida():
            return _cache
        if _in_rinvio():
            return _cache if _cache is not None else _senza_cache(degrada, None)
        try:
            lookups = await _fetch_all(secondary)
        except APIError as exc:
            if not errore_di_contratto(exc):
                raise
            _errore_at = time.monotonic()
            if _cache is not None:
                logger.warning(
                    "lookup del catalogo non leggibili (%s): servita la cache scaduta", exc.code
                )
                return _cache
            return _senza_cache(degrada, exc.code)
        _cache, _cache_at, _errore_at = lookups, time.monotonic(), None
        return _cache
