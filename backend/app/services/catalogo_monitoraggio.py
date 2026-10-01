"""Monitoraggio del catalogo bandi per il pannello admin «Catalogo»
(contratto DB bandi §14).

Il DB del catalogo espone una funzione di sola lettura che restituisce un
riepilogo neutro dello stato della raccolta dei bandi, protetta da una
chiave generata sul server (`MONITORAGGIO_CATALOGO_CHIAVE`; nel catalogo è
registrata solo la sua impronta).

- Chiave non configurata → `non_configurato`, senza chiamare.
- Chiamata: SOLO `POST /rest/v1/rpc/monitoraggio_catalogo` con
  `{"p_chiave": ...}` nel corpo, sul client del catalogo (chiave anon): mai
  GET, che metterebbe la chiave in un URL (e riceve comunque 42501). Timeout
  di `TIMEOUT_SECONDI`.
- Cache in-process di `CACHE_SECONDI` di QUALUNQUE esito, errori compresi,
  con una sola chiamata alla volta (lock): il riepilogo si ricalcola circa
  ogni 15 minuti e il contratto chiede al massimo una chiamata al minuto. La
  cache vale per processo (il deploy avvia un solo processo uvicorn).
- Errori (§14.2), distinti dal codice nel corpo e mai dal solo HTTP: 42501 →
  `chiave_non_valida`; PGRST202 → `non_disponibile`; PGRST3xx, o un 401 senza
  il corpo di PostgREST (gateway) → `accesso_db_non_valido`; rete, timeout,
  5xx e il resto → `non_raggiungibile`. Una risposta che non è un oggetto,
  con `versione` diversa da 1 o che non passa i modelli →
  `formato_non_supportato`.

La chiave non compare MAI in log, eccezioni, risposte o repr: nel log vanno
solo lo `stato_accesso` e il codice (o il tipo dell'eccezione), mai messaggi
o dettagli degli errori.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.catalogo_monitoraggio import (
    BustaMonitoraggio,
    MonitoraggioCatalogoOut,
    StatoAccesso,
)

logger = logging.getLogger("bandofit.catalogo_monitoraggio")

FUNZIONE = "monitoraggio_catalogo"
VERSIONE = 1
TIMEOUT_SECONDI = 10.0
CACHE_SECONDI = 60.0

_orologio = time.monotonic
_lock = asyncio.Lock()
_cache: tuple[float, MonitoraggioCatalogoOut] | None = None


def azzera_cache() -> None:
    """Svuota la cache e ricrea il lock (test, riavvio della configurazione)."""
    global _cache, _lock
    _cache = None
    _lock = asyncio.Lock()


def _adesso() -> datetime:
    return datetime.now(UTC)


def _esito(stato: StatoAccesso, busta: BustaMonitoraggio | None = None) -> MonitoraggioCatalogoOut:
    return MonitoraggioCatalogoOut(stato_accesso=stato, letto_at=_adesso(), busta=busta)


def _stato_da_codice(codice: Any) -> StatoAccesso:
    """Lo stato dal `code` di un errore di PostgREST. Senza il corpo di
    PostgREST la libreria mette in `code` lo status HTTP (un intero)."""
    if codice == "42501":
        return "chiave_non_valida"
    if codice == "PGRST202":
        return "non_disponibile"
    if isinstance(codice, str) and codice.startswith("PGRST3"):
        return "accesso_db_non_valido"
    if codice in (401, "401"):
        return "accesso_db_non_valido"
    return "non_raggiungibile"


def _codice_per_log(exc: Exception) -> str:
    codice = getattr(exc, "code", None)
    if isinstance(codice, bool) or not isinstance(codice, (str, int)) or codice == "":
        return type(exc).__name__
    return str(codice)


def _busta(dati: Any) -> BustaMonitoraggio | None:
    """La busta validata; None se il formato non è quello della versione 1."""
    if not isinstance(dati, dict):
        return None
    versione = dati.get("versione")
    if isinstance(versione, bool) or not isinstance(versione, int) or versione != VERSIONE:
        return None
    try:
        return BustaMonitoraggio.model_validate(dati)
    except ValidationError:
        return None


async def _chiama(secondary, chiave: str) -> MonitoraggioCatalogoOut:
    try:
        resp = await asyncio.wait_for(
            secondary.rpc(FUNZIONE, {"p_chiave": chiave}).execute(), timeout=TIMEOUT_SECONDI
        )
    except Exception as exc:  # noqa: BLE001 — ogni errore diventa uno stato
        stato = _stato_da_codice(exc.code) if isinstance(exc, APIError) else "non_raggiungibile"
        logger.warning("monitoraggio catalogo: %s (%s)", stato, _codice_per_log(exc))
        return _esito(stato)
    busta = _busta(getattr(resp, "data", None))
    if busta is None:
        logger.warning("monitoraggio catalogo: formato_non_supportato")
        return _esito("formato_non_supportato")
    return _esito("ok", busta)


async def leggi(secondary) -> MonitoraggioCatalogoOut:
    """Lo stato del monitoraggio del catalogo. Non solleva mai."""
    global _cache
    chiave = get_settings().monitoraggio_catalogo_chiave
    if chiave is None:
        return _esito("non_configurato")
    async with _lock:
        adesso = _orologio()
        if _cache is not None and adesso - _cache[0] < CACHE_SECONDI:
            return _cache[1]
        esito = await _chiama(secondary, chiave.get_secret_value())
        _cache = (_orologio(), esito)
        return esito
