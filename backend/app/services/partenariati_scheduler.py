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

Passi del WP5:
- `failsafe_ai_call`: i job AI delle call (posizioni e testi) rimasti
  `in_corso` oltre `partner_call_ai_stale_minuti` diventano `errore`
  (`interrotta`) e la loro esecuzione si chiude a costo ignoto con una riga
  `timeout_unknown` (`fn_partner_call_ai_chiudi_stale`);
- `chiusura_call`: chiusura d'ufficio delle bozze e delle call pubblicate
  (`partner_call_service.motivo_chiusura_auto`): azienda non viva →
  annullata; scadenza della call → scaduta; stato LIVE del bando
  (`bando_pubblico`, a blocchi): chiuso o sospeso → scaduta, revocato →
  annullata, assente → `bando_mancante_dal` e dopo 7 giorni annullata
  (`bando_non_disponibile`). Un errore di lettura del catalogo non è
  un'assenza: salta solo i motivi del bando. Snapshot del bando aggiornato
  quando cambia. A ogni chiusura una notifica al creatore e al titolare
  (dedup per call).

Passi del WP6:
- `backfill_collegamenti`: chiavi HMAC dei collegamenti societari delle
  aziende idonee senza marker aggiornato e pulizia di quelle non più idonee
  (`partenariato_collegamenti.backfill`); se cambia qualcosa l'indice del
  matching si invalida;
- `fanout_pendenti`: riprende i fan-out delle notifiche proattive non
  completati (processo morto a metà, claim scaduto);
- `digest_settimanale`: il digest delle call «per te», solo dal giorno e
  dall'ora configurati (lunedì 08:30), con claim per settimana su
  `partner_digest_runs`. Il loop si sveglia anche all'ora del digest, oltre
  che a quella della run giornaliera.
"""

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.errors import AppError
from app.services import (
    bandi_service,
    bando_fonti_service,
    partenariato_collegamenti,
    partenariato_indice,
    partenariato_notifiche,
    partenariato_service,
    partner_call_service,
)
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
# Call aperte lette per pagina (sotto il max-rows 1000 del PostgREST).
CALL_PAGINA = 500
CALL_SELECT_SCHEDULER = (
    "id,company_profile_id,family_parent_id,creato_da,bando_id,bando_titolo,stato,"
    "visibilita,sospesa_at,scadenza_call,bando_mancante_dal,bando_stato_effettivo,"
    "bando_scadenza"
)


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


async def failsafe_ai_call(primary) -> int:
    """Job AI delle call orfani (job perso in un riavvio) → errore
    `interrotta`; la riserva della loro esecuzione resta nel budget del
    giorno in cui sono partiti e va nel registro consumi come
    `timeout_unknown`. Ritorna quanti job ed esecuzioni ha chiuso."""
    resp = await primary.rpc(
        "fn_partner_call_ai_chiudi_stale",
        {"p_minuti": get_settings().partner_call_ai_stale_minuti},
    ).execute()
    return int(resp.data or 0)


async def _call_aperte(primary) -> list[dict]:
    """Bozze e call pubblicate, a pagine (lette tutte PRIMA di chiuderne:
    le chiusure spostano le righe fuori dal filtro)."""
    righe: list[dict] = []
    offset = 0
    while True:
        resp = (
            await primary.table("partner_calls")
            .select(CALL_SELECT_SCHEDULER)
            .in_("stato", ["bozza", "pubblicata"])
            .order("id")
            .range(offset, offset + CALL_PAGINA - 1)
            .execute()
        )
        dati = [r for r in resp.data or [] if isinstance(r, dict)]
        righe.extend(dati)
        if len(resp.data or []) < CALL_PAGINA:
            return righe
        offset += CALL_PAGINA


async def _aziende_non_vive(primary, ids: list[str]) -> set[str]:
    non_vive: set[str] = set()
    for inizio in range(0, len(ids), _BLOCCO_ID):
        resp = (
            await primary.table("company_profiles")
            .select("id,deleted_at,archived_at")
            .in_("id", ids[inizio : inizio + _BLOCCO_ID])
            .execute()
        )
        trovate = set()
        for riga in resp.data or []:
            trovate.add(str(riga["id"]))
            if riga.get("deleted_at") or riga.get("archived_at"):
                non_vive.add(str(riga["id"]))
        # Riga assente (cancellata tra le due letture): non viva.
        non_vive.update(set(ids[inizio : inizio + _BLOCCO_ID]) - trovate)
    return non_vive


def _snapshot_bando_cambiato(call: dict, stato) -> bool:
    scadenza = stato.data_scadenza.isoformat() if stato.data_scadenza else None
    return (
        call.get("bando_stato_effettivo") != stato.stato_effettivo
        or (str(call.get("bando_scadenza"))[:10] if call.get("bando_scadenza") else None)
        != scadenza
        or call.get("bando_mancante_dal") is not None
    )


async def chiusura_call(primary, secondary, oggi: date) -> dict:
    """Chiusure d'ufficio delle call (C6) e manutenzione dello snapshot del
    bando. Ogni call, ogni blocco di `bando_mancante_dal` e ogni bando da
    aggiornare sono isolati: un errore conta in `errori` e non ferma il
    resto."""
    calls = await _call_aperte(primary)
    esiti = {"controllate": len(calls), "chiuse": 0, "bando_mancante": 0,
             "snapshot_aggiornati": 0, "errori": 0, "bandi_letti": True}
    if not calls:
        return esiti
    bando_ids = sorted({int(c["bando_id"]) for c in calls})
    try:
        stati = await bando_fonti_service.leggi_stato_bandi(secondary, bando_ids)
    except Exception:
        logger.error("partenariati scheduler: stato dei bandi non leggibile", exc_info=True)
        stati, esiti["bandi_letti"] = {}, False
    non_vive = await _aziende_non_vive(
        primary, sorted({str(c["company_profile_id"]) for c in calls})
    )
    mancanti: list[str] = []
    da_aggiornare: dict[int, object] = {}
    for call in calls:
        try:
            stato = stati.get(int(call["bando_id"]))
            esito = partner_call_service.motivo_chiusura_auto(
                call,
                stato_bando=stato,
                bando_letto=esiti["bandi_letti"],
                azienda_viva=str(call["company_profile_id"]) not in non_vive,
                oggi=oggi,
            )
            if esito is not None:
                if await partner_call_service.chiudi_automaticamente(primary, call, *esito):
                    esiti["chiuse"] += 1
                continue
            if not esiti["bandi_letti"]:
                continue
            if stato is None:
                if call.get("bando_mancante_dal") is None:
                    mancanti.append(str(call["id"]))
            elif _snapshot_bando_cambiato(call, stato):
                da_aggiornare[int(call["bando_id"])] = stato
        except Exception:
            esiti["errori"] += 1
            logger.error("partenariati scheduler: call %s non controllata", call.get("id"),
                         exc_info=True)
    # Anche gli aggiornamenti a blocchi sono isolati: un errore su un blocco
    # non perde gli altri né il riepilogo del passo.
    for inizio in range(0, len(mancanti), _BLOCCO_ID):
        blocco = mancanti[inizio : inizio + _BLOCCO_ID]
        try:
            await primary.table("partner_calls").update(
                {"bando_mancante_dal": oggi.isoformat()}
            ).in_("id", blocco).is_("bando_mancante_dal", "null").execute()
            esiti["bando_mancante"] += len(blocco)
        except Exception:
            esiti["errori"] += 1
            logger.error("partenariati scheduler: bando_mancante_dal non aggiornato "
                         "(%d call)", len(blocco), exc_info=True)
    for bando_id, stato in da_aggiornare.items():
        try:
            await primary.table("partner_calls").update({
                "bando_stato_effettivo": stato.stato_effettivo,
                "bando_scadenza": stato.data_scadenza.isoformat() if stato.data_scadenza else None,
                "bando_verificato_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                "bando_mancante_dal": None,
            }).eq("bando_id", bando_id).in_("stato", ["bozza", "pubblicata"]).execute()
            esiti["snapshot_aggiornati"] += 1
        except Exception:
            esiti["errori"] += 1
            logger.error("partenariati scheduler: snapshot del bando %s non aggiornato",
                         bando_id, exc_info=True)
    return esiti


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


async def backfill_collegamenti(primary) -> dict:
    """Chiavi dei collegamenti (solo con il flag, solo aziende idonee)."""
    esito = await partenariato_collegamenti.backfill(primary)
    if esito.get("ricalcolate") or esito.get("rimosse"):
        partenariato_indice.invalida()
    return esito


async def fanout_pendenti(primary, secondary) -> dict:
    """Fan-out delle notifiche proattive rimasti a metà."""
    return await partenariato_notifiche.fanout_pendenti(primary, secondary)


async def digest_settimanale(primary, adesso: datetime) -> dict:
    """Il digest della settimana se è il suo momento (altrimenti nulla)."""
    esito = await partenariato_notifiche.digest_se_dovuto(primary, adesso)
    return esito if esito is not None else {"esito": "non_dovuto"}


async def _digest_isolato(primary, adesso: datetime) -> None:
    """Il digest fuori dalla run giornaliera (il loop si sveglia alla sua
    ora): mai un errore che fermi lo scheduler."""
    try:
        await partenariato_notifiche.digest_se_dovuto(primary, adesso)
    except Exception:
        logger.error("partenariati scheduler: digest settimanale fallito", exc_info=True)


# ------------------------------------------------------------ orchestrazione


async def _salva_riepilogo(primary, oggi: date, esiti: dict) -> None:
    try:
        await primary.table("partenariati_runs").update({"riepilogo": esiti}).eq(
            "giorno", oggi.isoformat()
        ).execute()
    except Exception:
        logger.error("partenariati scheduler: riepilogo non salvato", exc_info=True)


async def esegui_run(primary, secondary, ai, oggi: date, adesso: datetime | None = None) -> dict:
    """Esegue i passi in ordine, ciascuno isolato."""
    esiti: dict = {}
    istante = adesso or datetime.now(ZoneInfo("UTC"))
    passi = [
        ("failsafe_estrazioni", lambda: failsafe_estrazioni(primary)),
        ("failsafe_bozze_profilo", lambda: failsafe_bozze_profilo(primary)),
        ("failsafe_ai_call", lambda: failsafe_ai_call(primary)),
        ("chiusura_call", lambda: chiusura_call(primary, secondary, oggi)),
        ("batch_estrazioni", lambda: batch_estrazioni(primary, secondary, ai, oggi)),
        ("backfill_collegamenti", lambda: backfill_collegamenti(primary)),
        ("fanout_pendenti", lambda: fanout_pendenti(primary, secondary)),
        ("digest_settimanale", lambda: digest_settimanale(primary, istante)),
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
    return await esegui_run(primary, secondary, ai, oggi, adesso)


async def run_forever(primary, secondary, ai) -> None:
    """Loop dello scheduler: non muore mai in silenzio."""
    while True:
        try:
            await esegui_se_dovuto(primary, secondary, ai, datetime.now(ZoneInfo("UTC")))
            await _digest_isolato(primary, datetime.now(ZoneInfo("UTC")))
            adesso = datetime.now(ZoneInfo("UTC"))
            prossima = min(
                prossima_esecuzione(
                    adesso,
                    get_settings().partenariati_ora_esecuzione,
                    ZoneInfo(get_settings().alert_fuso),
                ),
                partenariato_notifiche.prossimo_digest(adesso),
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
