"""Lookup delle faccette di filtro (DB secondario), con cache in-process.

I valori cambiano raramente (regioni, settori, ...): una cache TTL di un'ora
evita 7 round-trip a ogni apertura della pagina bandi.
"""

import asyncio
import time

from app.schemas.bando import LookupsOut

_CACHE_TTL_SECONDS = 3600

_cache: LookupsOut | None = None
_cache_at: float = 0.0
_lock = asyncio.Lock()


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


async def get_lookups(secondary) -> LookupsOut:
    global _cache, _cache_at
    if _cache is not None and (time.monotonic() - _cache_at) < _CACHE_TTL_SECONDS:
        return _cache
    async with _lock:
        if _cache is not None and (time.monotonic() - _cache_at) < _CACHE_TTL_SECONDS:
            return _cache
        _cache = await _fetch_all(secondary)
        _cache_at = time.monotonic()
        return _cache
