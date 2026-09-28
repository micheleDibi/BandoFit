"""Scheduler in-process del modulo partenariati (docs/partenariati.md T2).

Stesso stampo di `alert_scheduler` e `payment_scheduler`: un task asyncio
avviato nel lifespan (SOLO se `partenariati_attivo` e
`partenariati_scheduler_attivo`), claim della run del giorno con l'INSERT su
`partenariati_runs` (PK giorno: 23505 = già eseguita altrove), catch-up al
riavvio, passi ISOLATI (un errore in un passo si logga e non ferma gli altri).
Ogni WP aggiunge i suoi passi a `esegui_run`.

Passi del WP3:
- `failsafe_estrazioni`: chiude i claim scaduti (`fn_partenariato_chiudi_stale`);
- `batch_estrazioni`: con `partenariato_batch_budget_cents_giorno = 0` (default)
  non fa nulla; altrimenti estrae, in sequenza, i bandi APERTI con segnali
  forti nel catalogo e senza un risultato fresco, dentro il budget del gruppo
  `batch` (fail-closed nella RPC: al primo rifiuto di budget si ferma).

Passo del WP4:
- `failsafe_bozze_profilo`: le bozze AI del profilo partner rimaste `in_corso`
  oltre `partner_bozza_ai_stale_minuti` diventano `errore` (`interrotta`) e la
  loro esecuzione si chiude a costo ignoto con una riga `timeout_unknown` nel
  registro consumi; lo stesso per le esecuzioni rimaste in corso senza una
  bozza in corso (`fn_partner_bozza_ai_chiudi_stale`).
"""

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.errors import AppError
from app.services import bandi_service, partenariato_service
from app.services.ai_check_prompts import serializza_sezioni
from app.services.alert_scheduler import prossima_esecuzione
from app.services.partenariato_preclassificatore import preclassifica
from app.services.partenariato_prompts import PARTENARIATO_PROMPT_VERSION, SCHEMA_VERSION

logger = logging.getLogger("bandofit.partenariati_scheduler")

_MAX_SLEEP_SECONDS = 3600
# Batch: bandi letti per pagina dal catalogo (sotto il max-rows 1000) e tetto
# delle estrazioni per notte (oltre al budget).
BATCH_PAGINA = 200
BATCH_MAX_BANDI = 100
_BLOCCO_ID = 200
_CODICI_STOP = {"ai_sospesa_oggi"}


async def claim_run(primary, giorno: date) -> bool:
    """True = la run di oggi tocca a noi; False = già eseguita (23505)."""
    try:
        await primary.table("partenariati_runs").insert({"giorno": giorno.isoformat()}).execute()
        return True
    except APIError as exc:
        if exc.code == "23505":
            return False
        raise


# ------------------------------------------------------------------ passi


async def failsafe_estrazioni(primary) -> int:
    """Estrazioni con il claim scaduto → chiuse come `interrotta`: a costo 0
    se non erano arrivate all'analisi, altrimenti la riserva resta nel budget
    del giorno in cui sono partite (`fn_partenariato_esecuzione_scaduta`)."""
    resp = await primary.rpc("fn_partenariato_chiudi_stale", {}).execute()
    return int(resp.data or 0)


async def failsafe_bozze_profilo(primary) -> int:
    """Bozze AI del profilo partner orfane (job perso in un riavvio) → errore
    `interrotta`; la riserva della loro esecuzione resta nel budget del giorno
    in cui sono partite (costo ignoto) e va nel registro consumi come
    `timeout_unknown`. Ritorna quante bozze ed esecuzioni ha chiuso."""
    resp = await primary.rpc(
        "fn_partner_bozza_ai_chiudi_stale",
        {"p_minuti": get_settings().partner_bozza_ai_stale_minuti},
    ).execute()
    return int(resp.data or 0)


def _testo_catalogo(riga: dict) -> dict[str, str]:
    sezioni = {
        "META": "\n".join(
            str(riga.get(chiave) or "") for chiave in ("titolo", "titolo_breve", "descrizione_breve")
        )
    }
    sezioni.update(dict(serializza_sezioni(bandi_service.normalize_contenuto(riga.get("contenuto")))))
    return sezioni


def _forti(righe: list[dict]) -> list[dict]:
    return [r for r in righe if preclassifica(_testo_catalogo(r)).livello == "forte"]


async def _bandi_con_segnali(secondary, oggi: date) -> list[dict]:
    """Bandi APERTI del catalogo con segnali forti nella scheda, a pagine."""
    trovati: list[dict] = []
    offset = 0
    while len(trovati) < BATCH_MAX_BANDI * 3:
        query = secondary.table("bando").select(
            "id,slug,titolo,titolo_breve,descrizione_breve,contenuto"
        )
        query = bandi_service.apply_open_tier(
            query.eq("stato_processing", "completed").not_.is_("slug", "null"), oggi
        )
        resp = await query.order("id").range(offset, offset + BATCH_PAGINA - 1).execute()
        righe = [r for r in (resp.data or []) if isinstance(r, dict) and r.get("slug")]
        # Regex su centinaia di schede: fuori dall'event loop.
        trovati.extend(await asyncio.to_thread(_forti, righe))
        if len(resp.data or []) < BATCH_PAGINA:
            break
        offset += BATCH_PAGINA
    return trovati


async def _gia_freschi(primary, ids: list[int]) -> set[int]:
    """Bandi da saltare: in corso, oppure con un risultato alla versione
    corrente del prompt (la riverifica periodica la fanno le aperture)."""
    saltare: set[int] = set()
    for inizio in range(0, len(ids), _BLOCCO_ID):
        resp = (
            await primary.table("bando_partenariato")
            .select("bando_id,stato,esito,prompt_version,schema_version")
            .in_("bando_id", ids[inizio : inizio + _BLOCCO_ID])
            .execute()
        )
        for riga in resp.data or []:
            if riga.get("stato") == "in_corso" or (
                riga.get("esito")
                and riga.get("prompt_version") == PARTENARIATO_PROMPT_VERSION
                and riga.get("schema_version") == SCHEMA_VERSION
            ):
                saltare.add(int(riga["bando_id"]))
    return saltare


async def batch_estrazioni(primary, secondary, ai, oggi: date) -> dict:
    """Estrazioni notturne dentro il budget del gruppo `batch`. Spento con
    budget 0 (default): nessuna lettura, nessuna spesa."""
    budget = get_settings().partenariato_batch_budget_cents_giorno
    if budget <= 0:
        return {"eseguite": 0, "motivo": "spento"}
    if ai is None or not ai.enabled:
        return {"eseguite": 0, "motivo": "ai_non_configurata"}
    candidati = await _bandi_con_segnali(secondary, oggi)
    saltare = await _gia_freschi(primary, [int(r["id"]) for r in candidati])
    da_fare = [r for r in candidati if int(r["id"]) not in saltare][:BATCH_MAX_BANDI]
    esiti: dict[str, int] = {}
    motivo = "completato"
    for riga in da_fare:
        try:
            bando = await bandi_service.fetch_bando_for_ai(secondary, riga["slug"])
            esito = await partenariato_service.esegui_per_bando(
                primary, secondary, ai, bando, origine="batch", budget_cents=budget
            )
        except AppError as exc:
            if exc.code in _CODICI_STOP:
                motivo = "budget_esaurito"
                break
            esito = exc.code
        except Exception:
            logger.error("batch partenariati: bando %s non elaborato", riga.get("id"), exc_info=True)
            esito = "errore"
        esiti[esito] = esiti.get(esito, 0) + 1
    return {
        "candidati": len(candidati),
        "eseguite": sum(esiti.values()),
        "esiti": esiti,
        "motivo": motivo,
    }


# ------------------------------------------------------------ orchestrazione


async def _salva_riepilogo(primary, oggi: date, esiti: dict) -> None:
    try:
        await primary.table("partenariati_runs").update({"riepilogo": esiti}).eq(
            "giorno", oggi.isoformat()
        ).execute()
    except Exception:
        logger.error("partenariati scheduler: riepilogo non salvato", exc_info=True)


async def esegui_run(primary, secondary, ai, oggi: date) -> dict:
    """Esegue i passi in ordine, ciascuno isolato."""
    esiti: dict = {}
    passi = [
        ("failsafe_estrazioni", lambda: failsafe_estrazioni(primary)),
        ("failsafe_bozze_profilo", lambda: failsafe_bozze_profilo(primary)),
        ("batch_estrazioni", lambda: batch_estrazioni(primary, secondary, ai, oggi)),
    ]
    for nome, fn in passi:
        try:
            esiti[nome] = await fn()
        except Exception:
            logger.error("partenariati scheduler: passo %s fallito", nome, exc_info=True)
            esiti[nome] = "errore"
    logger.info("partenariati scheduler: run %s → %s", oggi.isoformat(), esiti)
    await _salva_riepilogo(primary, oggi, esiti)
    return esiti


async def esegui_se_dovuto(primary, secondary, ai, adesso: datetime) -> dict | None:
    settings = get_settings()
    locale = adesso.astimezone(ZoneInfo(settings.alert_fuso))
    ore, minuti = (int(p) for p in settings.partenariati_ora_esecuzione.split(":"))
    if (locale.hour, locale.minute) < (ore, minuti):
        return None
    oggi = locale.date()
    if not await claim_run(primary, oggi):
        return None
    return await esegui_run(primary, secondary, ai, oggi)


async def run_forever(primary, secondary, ai) -> None:
    """Loop dello scheduler: non muore mai in silenzio."""
    while True:
        try:
            await esegui_se_dovuto(primary, secondary, ai, datetime.now(ZoneInfo("UTC")))
            prossima = prossima_esecuzione(
                datetime.now(ZoneInfo("UTC")),
                get_settings().partenariati_ora_esecuzione,
                ZoneInfo(get_settings().alert_fuso),
            )
            while True:
                resta = (prossima - datetime.now(ZoneInfo("UTC"))).total_seconds()
                if resta <= 0:
                    break
                await asyncio.sleep(min(resta, _MAX_SLEEP_SECONDS))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "partenariati scheduler: errore inatteso, riprovo tra un'ora", exc_info=True
            )
            await asyncio.sleep(_MAX_SLEEP_SECONDS)
