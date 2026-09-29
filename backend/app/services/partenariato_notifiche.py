"""Notifiche proattive, call seguite e digest settimanale dei partenariati
(WP6, docs/partenariati.md M5, M6, Q6).

- `fan_out_pubblicazione`: alla pubblicazione di una call, claim del fan-out
  (`fn_partner_fanout_claim`, con scadenza: se il processo muore a metà lo
  riprende lo scheduler, passo `fanout_pendenti`), suggeriti della call con
  almeno un requisito cercato coperto e punteggio ≥ soglia, ricontrollo
  live, le prime K → `fn_partner_claim_notifica` (dedup azienda × call e
  tetto a settimana per azienda, sotto lock) → notifica in-app ai titolari e
  ai membri con visibilità sull'azienda → `fanout_completato_at`. Nessun dato
  di chi ha creato la call: solo il titolo del bando (catalogo pubblico) e
  quanti requisiti mancanti si coprono. Le call solo su invito non generano
  notifiche proattive. Una call che il matching non può valutare (fuori
  dall'indice, o creatore senza collegamenti calcolati) resta pendente per lo
  scheduler; alla ripresa, un'azienda già reclamata da un giro interrotto
  riceve di nuovo la notifica (idempotente sul dedup_key).
- `notifica_salvate`: «Salva» vale come «segui» (M6): alla modifica di una
  call pubblicata e alla sua chiusura, una notifica a chi l'ha salvata (dedup
  per versione / chiusura e per azienda).
- Digest settimanale (`esegui_digest`, dal lunedì alle 08:30): claim per
  settimana (INSERT su `partner_digest_runs`), solo utenti con il digest
  attivo di aziende con l'opt-in visibile e con notifiche non ancora incluse,
  email verificate e non soppresse (`filtra_recapitabili`), ledger per utente
  e settimana (at-most-once: un invio interrotto diventa `incerta` e non si
  ritenta), link e header di disiscrizione propri (RFC 8058). Nessun
  contenuto → nessuna email.
- Impostazioni email per utente (`settings_per_utente`, `aggiorna_settings`)
  e disiscrizione pubblica per token (`unsubscribe_by_token`).

Tutto best-effort verso chi chiama (mai un errore alla pubblicazione o alla
chiusura per una notifica). Log: solo id e conteggi; mai email, nomi o testi.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.services import bandi_service, bando_alert_service, email_service, partenariato_indice
from app.services import partenariato_matching as pm
from app.services.notification_service import notify

logger = logging.getLogger("bandofit.partenariati")

TIPO_PER_TE = "partenariato.call_per_te"
TIPO_SEGUITA_MODIFICATA = "partenariato.call_seguita_modificata"
TIPO_SEGUITA_CHIUSA = "partenariato.call_seguita_chiusa"
# Claim del fan-out: oltre questa durata un fan-out non completato si
# riprende (scheduler); il dedup di fn_partner_claim_notifica rende sicura la
# ripresa.
FANOUT_TTL_SECONDI = 900
# Call con il fan-out da riprendere per giro dello scheduler.
FANOUT_PENDENTI_MAX = 50
# Notifiche non ancora incluse in un digest che vale la pena mandare.
FINESTRA_DIGEST = timedelta(days=14)
PAGINA = 1000
BLOCCO_ID = 100

Evento = Literal["modificata", "chiusa"]
TipoEmail = Literal["digest", "eventi"]


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def lunedi(adesso: datetime | None = None) -> date:
    """Lunedì della settimana ISO in Europe/Rome (chiave del tetto e del
    digest: i CHECK della 0038 rifiutano qualunque altro giorno)."""
    locale = (adesso or _adesso()).astimezone(ZoneInfo(get_settings().alert_fuso))
    return locale.date() - timedelta(days=locale.weekday())


def _blocchi(ids: list[str]) -> Iterable[list[str]]:
    for inizio in range(0, len(ids), BLOCCO_ID):
        yield ids[inizio : inizio + BLOCCO_ID]


def _codice(exc: Exception) -> str:
    return str(exc.code) if isinstance(exc, APIError) else type(exc).__name__


# ------------------------------------------------------------ destinatari


async def destinatari_per_azienda(primary, company_ids: Iterable[str]) -> dict[str, list[dict]]:
    """Per ogni azienda: il titolare e i membri ATTIVI con visibilità su di
    lei (0031), tutti con profilo attivo ed email (stessa logica degli alert
    sui bandi, `bando_alert_service`). → `{azienda: [{id, email, ...}]}`."""
    ids = sorted({str(c) for c in company_ids})
    owner_di: dict[str, str] = {}
    for blocco in _blocchi(ids):
        resp = (
            await primary.table("company_profiles").select("id,parent_id").in_("id", blocco)
            .execute()
        )
        owner_di.update({str(r["id"]): str(r["parent_id"]) for r in resp.data or []})
    owners = sorted(set(owner_di.values()))
    if not owners:
        return {}
    # A blocchi di owner, come ogni `in_` del modulo: le funzioni degli alert
    # non paginano (max-rows 1000 di PostgREST) e un URL con centinaia di id
    # rischia di essere rifiutato.
    per_owner: dict[str, list[dict]] = {}
    visibilita: dict[str, set[str]] = {}
    for blocco in _blocchi(owners):
        per_owner.update(await bando_alert_service.carica_destinatari(primary, blocco))
        for membro, aziende in (
            await bando_alert_service.carica_visibilita_membri(primary, blocco)
        ).items():
            visibilita.setdefault(membro, set()).update(aziende)
    uscita: dict[str, list[dict]] = {}
    for cid, owner in owner_di.items():
        uscita[cid] = [
            d for d in per_owner.get(owner, [])
            if str(d["id"]) == owner or cid in visibilita.get(str(d["id"]), set())
        ]
    return uscita


async def destinatari_azienda(primary, company_id: str) -> list[dict]:
    """Titolare + membri attivi con visibilità sull'azienda, profilo attivo."""
    return (await destinatari_per_azienda(primary, [company_id])).get(str(company_id), [])


# ---------------------------------------------------------------- fan-out


async def _gia_reclamata(primary, company_id: str, call_id: str) -> bool:
    """La riga azienda × call di `partner_notifiche_proattive` esiste già
    (claim false per dedup, non per tetto)."""
    resp = (
        await primary.table("partner_notifiche_proattive")
        .select("id")
        .eq("company_profile_id", str(company_id))
        .eq("partner_call_id", str(call_id))
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def _corpo_per_te(coperti: int, bando_titolo: str | None) -> str:
    requisiti = "1 requisito mancante" if coperti == 1 else f"{coperti} requisiti mancanti"
    return f"Copri {requisiti} per il bando «{bando_titolo or 'del catalogo'}»."


async def fan_out_pubblicazione(primary, secondary, call_id: str) -> dict:
    """Notifiche proattive di UNA call (vedi docstring del modulo). Solleva
    solo sugli errori prima del claim delle notifiche (claim del fan-out,
    indice): il fan-out resta non completato e lo riprende lo scheduler."""
    cid = str(call_id)
    esito = {"claim": False, "candidati": 0, "notificate": 0, "riconsegnate": 0, "saltate": 0,
             "errori": 0, "rinviato": False}
    resp = await primary.rpc(
        "fn_partner_fanout_claim", {"p_call": cid, "p_ttl_secondi": FANOUT_TTL_SECONDI}
    ).execute()
    if resp.data is not True:
        return esito
    esito["claim"] = True
    settings = get_settings()
    idx = await partenariato_indice.indice(primary, secondary)
    call = idx.matching.calls.get(cid)
    if call is None or (call.visibilita == "pubblica" and not call.creatore.collegamenti_ok):
        # Il matching non ha potuto valutare la call: fuori dall'indice (per
        # esempio il bando è momentaneamente assente dal catalogo) o senza
        # collegamenti calcolati del creatore (fail-closed: escluderebbe
        # tutti). Chiuderlo adesso perderebbe le notifiche per sempre: resta
        # pendente e lo riprende lo scheduler, che prima ricalcola i
        # collegamenti (passo `backfill_collegamenti`).
        esito["rinviato"] = True
        logger.info("partenariati: fan-out della call %s rinviato (call non valutabile)", cid)
        return esito
    scelti: list[pm.MatchInterno] = []
    if call.visibilita == "pubblica":
        risultati = pm.suggeriti_per_call(
            idx.matching,
            cid,
            pm.FiltriSuggeriti(min_coperti=1, min_punteggio=settings.partenariato_notifiche_soglia),
            oggi=bandi_service.today_italy(),
            pesi=pm.PesiMatching.da_settings(settings),
            # Il limite per owner è della lista mostrata al creatore, non di
            # chi riceve la notifica (WP7).
            limita_owner=False,
        )
        vivi = await partenariato_indice.ricontrollo_live(
            primary, [m.company_id for m in risultati]
        )
        scelti = [m for m in risultati if m.company_id in vivi][
            : max(0, settings.partenariato_notifiche_top_k)
        ]
    esito["candidati"] = len(scelti)
    bando = (idx.bacheca.get(cid).riga.get("bando_titolo") if cid in idx.bacheca else None)
    settimana = lunedi().isoformat()
    for m in scelti:
        try:
            claim = await primary.rpc("fn_partner_claim_notifica", {
                "p_company": m.company_id,
                "p_call": cid,
                "p_settimana": settimana,
                "p_tetto": settings.partenariato_notifiche_tetto_settimana,
                "p_copertura": min(m.coperti, 32767),
                "p_punteggio": max(0, min(100, m.punteggio)),
            }).execute()
            ripresa = False
            if claim.data is not True:
                if not await _gia_reclamata(primary, m.company_id, cid):
                    esito["saltate"] += 1  # tetto della settimana raggiunto
                    continue
                # La riga azienda × call c'è già: l'ha scritta un giro
                # interrotto di QUESTO fan-out (la riga nasce solo qui), che
                # può essersi fermato prima della consegna. Si riconsegna:
                # `notify` è idempotente sul dedup_key.
                ripresa = True
            destinatari = await destinatari_azienda(primary, m.company_id)
            await notify(
                primary,
                [str(d["id"]) for d in destinatari],
                tipo=TIPO_PER_TE,
                titolo="Nuova call di partenariato per te",
                corpo=_corpo_per_te(m.coperti, bando),
                url=f"/app/partenariati/call/{cid}?azienda={m.company_id}",
                dedup_key=f"partner-per-te:{cid}:{m.company_id}",
                company_profile_id=m.company_id,
            )
            esito["riconsegnate" if ripresa else "notificate"] += 1
        except Exception as exc:  # passo isolato per azienda
            esito["errori"] += 1
            logger.error("partenariati: notifica proattiva non riuscita (call %s, azienda %s, %s)",
                         cid, m.company_id, _codice(exc))
    if esito["errori"] == 0:
        # Completato solo senza errori: altrimenti lo riprende lo scheduler
        # (il dedup evita i doppioni).
        await primary.table("partner_calls").update(
            {"fanout_completato_at": _adesso().isoformat()}
        ).eq("id", cid).is_("fanout_completato_at", "null").execute()
    logger.info("partenariati: fan-out della call %s → %s", cid, esito)
    return esito


async def dopo_pubblicazione(primary, secondary, call_id: str, company_id: str) -> None:
    """Task in-process dopo la pubblicazione (best-effort, non solleva
    mai): chiavi dei collegamenti del creatore (senza il marker la sua call
    non avrebbe suggeriti fino al backfill) e fan-out delle notifiche."""
    await partenariato_indice.ricostruisci_collegamenti(primary, company_id)
    try:
        await fan_out_pubblicazione(primary, secondary, call_id)
    except Exception as exc:  # noqa: BLE001 — lo riprende lo scheduler
        logger.warning("partenariati: fan-out della call %s rinviato allo scheduler (%s)",
                       call_id, _codice(exc))


async def fanout_pendenti(primary, secondary) -> dict:
    """Passo dello scheduler: riprende i fan-out non completati (claim
    scaduto o mai preso), a partire dalle call pubblicate da più tempo.
    Ogni call è isolata."""
    resp = (
        await primary.table("partner_calls")
        .select("id")
        .eq("stato", "pubblicata")
        .is_("fanout_completato_at", "null")
        .order("pubblicata_at")
        .limit(FANOUT_PENDENTI_MAX)
        .execute()
    )
    esito = {"call": 0, "completate": 0, "notificate": 0, "errori": 0}
    for riga in resp.data or []:
        esito["call"] += 1
        try:
            parziale = await fan_out_pubblicazione(primary, secondary, str(riga["id"]))
        except Exception as exc:  # passo isolato per call
            esito["errori"] += 1
            logger.error("partenariati: fan-out della call %s non riuscito (%s)", riga.get("id"),
                         _codice(exc))
            continue
        esito["notificate"] += parziale["notificate"]
        if parziale["claim"] and parziale["errori"] == 0 and not parziale["rinviato"]:
            esito["completate"] += 1
    return esito


# -------------------------------------------------------- call seguite


async def notifica_salvate(primary, call: Mapping, evento: Evento) -> int:
    """Notifica a chi segue (ha salvato) la call che è stata modificata o
    chiusa. Best-effort: non solleva mai. → numero di aziende avvisate."""
    cid = str(call.get("id"))
    try:
        resp = (
            await primary.table("partner_call_salvate")
            .select("company_profile_id")
            .eq("partner_call_id", cid)
            .execute()
        )
        aziende = sorted({
            str(r["company_profile_id"]) for r in resp.data or []
            if str(r.get("company_profile_id")) != str(call.get("company_profile_id"))
        })
        if not aziende:
            return 0
        destinatari = await destinatari_per_azienda(primary, aziende)
        bando = call.get("bando_titolo") or "del catalogo"
        for company in aziende:
            if evento == "modificata":
                titolo = "Una call che segui è stata modificata"
                corpo = f"Controlla le novità della call per il bando «{bando}»."
                url = f"/app/partenariati/call/{cid}?azienda={company}"
                dedup = f"partner-seguita:{cid}:v{call.get('versione') or 0}:{company}"
                tipo = TIPO_SEGUITA_MODIFICATA
            else:
                titolo = "Una call che segui è stata chiusa"
                corpo = f"La call per il bando «{bando}» non cerca più partner."
                url = f"/app/partenariati?vista=salvate&azienda={company}"
                dedup = f"partner-seguita-chiusa:{cid}:{company}"
                tipo = TIPO_SEGUITA_CHIUSA
            await notify(
                primary,
                [str(d["id"]) for d in destinatari.get(company, [])],
                tipo=tipo,
                titolo=titolo,
                corpo=corpo,
                url=url,
                dedup_key=dedup,
                company_profile_id=company,
            )
        return len(aziende)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("partenariati: notifica alle aziende che seguono la call %s non "
                       "riuscita (%s)", cid, _codice(exc))
        return 0


# ------------------------------------------------------ impostazioni email


class EmailSettingsPartenariatiOut(BaseModel):
    """GET/PUT /me/partenariati/email-settings."""

    digest_abilitato: bool = True
    eventi_abilitati: bool = True


class EmailSettingsPartenariatiIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    digest_abilitato: bool
    eventi_abilitati: bool


async def settings_per_utente(primary, user_id: str) -> EmailSettingsPartenariatiOut:
    """Impostazioni dell'utente: una riga assente vale «tutto attivo»."""
    resp = (
        await primary.table("partner_email_settings")
        .select("digest_abilitato,eventi_abilitati")
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    riga = resp.data[0] if resp.data else {}
    return EmailSettingsPartenariatiOut(
        digest_abilitato=riga.get("digest_abilitato") is not False,
        eventi_abilitati=riga.get("eventi_abilitati") is not False,
    )


async def aggiorna_settings(
    primary, user_id: str, dati: EmailSettingsPartenariatiIn
) -> EmailSettingsPartenariatiOut:
    """Upsert della riga (stessa fonte di verità del link di disiscrizione)."""
    await primary.table("partner_email_settings").upsert(
        {
            "user_id": str(user_id),
            "digest_abilitato": dati.digest_abilitato,
            "eventi_abilitati": dati.eventi_abilitati,
        },
        on_conflict="user_id",
    ).execute()
    return await settings_per_utente(primary, user_id)


async def unsubscribe_by_token(primary, token: str, tipo: TipoEmail) -> None:
    """Disiscrizione a un clic (digest o email di evento): idempotente,
    silenziosa con un token ignoto (nessuna enumerazione). Audit
    best-effort, mai il token."""
    campo = "digest_abilitato" if tipo == "digest" else "eventi_abilitati"
    resp = (
        await primary.table("partner_email_settings")
        .update({campo: False})
        .eq("unsubscribe_token", token)
        .execute()
    )
    if resp.data:
        utente = str(resp.data[0]["user_id"])
        try:
            await primary.table("audit_log").insert({
                "actor_id": utente,
                "action": "partenariati.email_disiscrizione",
                "target_user_id": utente,
                "payload": {"tipo": tipo, "canale": "email"},
            }).execute()
        except Exception:  # noqa: BLE001 — audit best-effort
            logger.warning("partenariati: audit della disiscrizione non scritto")


# ------------------------------------------------------------------ digest


def prossimo_digest(adesso: datetime) -> datetime:
    """Il prossimo istante (aware) del digest: il giorno e l'ora
    configurati, a muro in `alert_fuso` (DST-safe)."""
    settings = get_settings()
    fuso = ZoneInfo(settings.alert_fuso)
    ore, minuti = (int(p) for p in settings.partenariato_digest_ora.split(":"))
    locale = adesso.astimezone(fuso)
    giorni = (settings.partenariato_digest_giorno - locale.weekday()) % 7
    obiettivo = datetime.combine(locale.date() + timedelta(days=giorni), time(ore, minuti),
                                 tzinfo=fuso)
    if obiettivo <= locale:
        obiettivo = datetime.combine(obiettivo.date() + timedelta(days=7), time(ore, minuti),
                                     tzinfo=fuso)
    return obiettivo


async def claim_digest(primary, settimana: date) -> bool:
    """True = il digest della settimana tocca a noi (23505 = già fatto)."""
    try:
        await primary.table("partner_digest_runs").insert(
            {"settimana": settimana.isoformat()}
        ).execute()
        return True
    except APIError as exc:
        if exc.code == "23505":
            return False
        raise


async def _tutte(costruisci, chiave: str) -> list[dict]:
    righe: list[dict] = []
    ultimo: Any = None
    while True:
        query = costruisci()
        if ultimo is not None:
            query = query.gt(chiave, ultimo)
        resp = await query.order(chiave).limit(PAGINA).execute()
        dati = [r for r in resp.data or [] if isinstance(r, dict)]
        righe.extend(dati)
        if len(resp.data or []) < PAGINA or not dati:
            return righe
        ultimo = dati[-1][chiave]


async def _impostazioni(primary, user_ids: list[str]) -> dict[str, dict]:
    """Righe delle impostazioni (create al primo uso: il token nasce con la
    riga)."""
    if not user_ids:
        return {}
    await primary.table("partner_email_settings").upsert(
        [{"user_id": uid} for uid in user_ids], on_conflict="user_id", ignore_duplicates=True
    ).execute()
    righe: dict[str, dict] = {}
    for blocco in _blocchi(sorted(user_ids)):
        resp = (
            await primary.table("partner_email_settings")
            .select("user_id,digest_abilitato,unsubscribe_token")
            .in_("user_id", blocco)
            .execute()
        )
        righe.update({str(r["user_id"]): r for r in resp.data or []})
    return righe


async def _marca_incerti(primary) -> int:
    """Invii rimasti `in_invio` da un digest interrotto: esito ignoto →
    `incerta`, MAI ritentati (at-most-once)."""
    resp = (
        await primary.table("partner_digest_invii")
        .update({"stato": "incerta", "errore": "esecuzione interrotta"})
        .eq("stato", "in_invio")
        .execute()
    )
    return len(resp.data or [])


async def _claim_invio(primary, user_id: str, settimana: date) -> int | None:
    resp = await primary.table("partner_digest_invii").upsert(
        {"user_id": user_id, "settimana": settimana.isoformat()},
        on_conflict="user_id,settimana",
        ignore_duplicates=True,
    ).execute()
    riga = resp.data[0] if resp.data else None
    return riga.get("id") if isinstance(riga, dict) else None


async def esegui_digest(primary, settimana: date) -> dict:
    """Il digest della settimana (vedi docstring del modulo). Non solleva:
    l'esito finisce nel riepilogo della riga di `partner_digest_runs`."""
    if not await claim_digest(primary, settimana):
        return {"esito": "gia_eseguito"}
    settings = get_settings()
    riepilogo = {"esito": "ok", "aziende": 0, "destinatari": 0, "inviate": 0, "fallite": 0,
                 "saltate": 0, "incerte": 0, "errori": 0}
    try:
        riepilogo["incerte"] = await _marca_incerti(primary)
        da = (_adesso() - FINESTRA_DIGEST).isoformat()
        notifiche = await _tutte(
            lambda: primary.table("partner_notifiche_proattive")
            .select("id,company_profile_id,partner_call_id,copertura")
            .is_("digest_incluso_at", "null")
            .gte("created_at", da),
            "id",
        )
        aziende = await partenariato_indice.ricontrollo_live(
            primary, [str(n["company_profile_id"]) for n in notifiche]
        )
        notifiche = [n for n in notifiche if str(n["company_profile_id"]) in aziende]
        call_vive = await partenariato_indice.ricontrollo_call_live(
            primary, [str(n["partner_call_id"]) for n in notifiche],
            oggi=bandi_service.today_italy(),
        )
        notifiche = [n for n in notifiche if str(n["partner_call_id"]) in call_vive]
        if notifiche:
            await _invia_digest(primary, settings, settimana, notifiche, riepilogo)
    except Exception as exc:  # difensivo: il digest non deve uccidere lo scheduler
        riepilogo["esito"] = "errore"
        riepilogo["errore"] = _codice(exc)
        logger.error("partenariati: digest della settimana %s non riuscito (%s)",
                     settimana.isoformat(), _codice(exc))
    try:
        await primary.table("partner_digest_runs").update({"riepilogo": riepilogo}).eq(
            "settimana", settimana.isoformat()
        ).execute()
    except Exception:  # noqa: BLE001
        logger.error("partenariati: riepilogo del digest non salvato")
    logger.info("partenariati: digest %s → %s", settimana.isoformat(), riepilogo)
    return riepilogo


async def _invia_digest(
    primary, settings, settimana: date, notifiche: list[dict], riepilogo: dict
) -> None:
    call_ids = sorted({str(n["partner_call_id"]) for n in notifiche})
    bandi: dict[str, str | None] = {}
    for blocco in _blocchi(call_ids):
        resp = (
            await primary.table("partner_calls").select("id,bando_titolo").in_("id", blocco)
            .execute()
        )
        bandi.update({str(r["id"]): r.get("bando_titolo") for r in resp.data or []})
    per_azienda: dict[str, list[dict]] = {}
    for n in sorted(notifiche, key=lambda r: (-(r.get("copertura") or 0), r["id"])):
        per_azienda.setdefault(str(n["company_profile_id"]), []).append(n)
    riepilogo["aziende"] = len(per_azienda)

    nomi: dict[str, str | None] = {}
    for blocco in _blocchi(sorted(per_azienda)):
        resp = (
            await primary.table("company_profiles").select("id,ragione_sociale")
            .in_("id", blocco).execute()
        )
        nomi.update({str(r["id"]): r.get("ragione_sociale") for r in resp.data or []})
    destinatari = await destinatari_per_azienda(primary, list(per_azienda))
    aziende_di: dict[str, list[str]] = {}
    utenti: dict[str, dict] = {}
    for company, lista in destinatari.items():
        for d in lista:
            utenti[str(d["id"])] = d
            aziende_di.setdefault(str(d["id"]), []).append(company)
    recapitabili = await bando_alert_service.filtra_recapitabili(primary, list(utenti.values()))
    impostazioni = await _impostazioni(primary, [str(d["id"]) for d in recapitabili])
    frontend = settings.frontend_url.rstrip("/")
    api = settings.api_public_url.rstrip("/")
    incluse: set[int] = set()
    try:
        for destinatario in recapitabili:
            uid = str(destinatario["id"])
            riga = impostazioni.get(uid)
            if not riga or riga.get("digest_abilitato") is False:
                riepilogo["saltate"] += 1  # opt-out valutato ADESSO, all'invio
                continue
            companies = sorted(aziende_di.get(uid, []))
            sezioni = [
                {
                    "azienda": nomi.get(company),
                    "call": [
                        {
                            "bando": bandi.get(str(n["partner_call_id"])),
                            "coperti": n.get("copertura") or 0,
                            "url": f"{frontend}/app/partenariati/call/{n['partner_call_id']}"
                                   f"?azienda={company}",
                        }
                        for n in per_azienda[company]
                    ],
                }
                for company in companies
            ]
            # Ogni destinatario è isolato: un errore su uno non ferma gli altri
            # (il claim della settimana è già preso, un'altra run non ci sarà).
            try:
                invio = await _claim_invio(primary, uid, settimana)
                if invio is None:
                    riepilogo["saltate"] += 1  # già inviato (o in corso) questa settimana
                    continue
                riepilogo["destinatari"] += 1
                ok = await email_service.send_partner_digest_email(
                    destinatario["email"],
                    sezioni,
                    f"{frontend}/app/partenariati?vista=per-te",
                    f"{api}/partenariati/email/unsubscribe?token={riga['unsubscribe_token']}"
                    "&tipo=digest",
                )
                if ok:
                    # Incluse appena inviate, prima del ledger: un errore dopo
                    # l'invio non deve farle rimandare la settimana dopo.
                    riepilogo["inviate"] += 1
                    incluse.update(int(n["id"]) for c in companies for n in per_azienda[c])
                else:
                    riepilogo["fallite"] += 1
                await primary.table("partner_digest_invii").update(
                    {"stato": "inviata" if ok else "fallita",
                     "errore": None if ok else "invio email fallito (vedi log)"}
                ).eq("id", invio).eq("stato", "in_invio").execute()
            except Exception as exc:  # passo isolato per destinatario
                riepilogo["errori"] += 1
                logger.error("partenariati: digest dell'utente %s non gestito (%s)", uid,
                             _codice(exc))
                continue
            await asyncio.sleep(settings.alert_pausa_invii_secondi)
    finally:
        # Anche dopo un errore imprevisto: le notifiche già inviate non si
        # ripropongono nel digest successivo.
        for inizio in range(0, len(incluse), BLOCCO_ID):
            blocco = sorted(incluse)[inizio : inizio + BLOCCO_ID]
            await primary.table("partner_notifiche_proattive").update(
                {"digest_incluso_at": _adesso().isoformat()}
            ).in_("id", blocco).execute()


async def digest_se_dovuto(primary, adesso: datetime) -> dict | None:
    """Il digest della settimana, solo dal giorno e dall'ora configurati
    (lunedì alle 08:30 di default) e una volta sola per settimana."""
    settings = get_settings()
    locale = adesso.astimezone(ZoneInfo(settings.alert_fuso))
    ore, minuti = (int(p) for p in settings.partenariato_digest_ora.split(":"))
    if locale.weekday() != settings.partenariato_digest_giorno:
        return None
    if (locale.hour, locale.minute) < (ore, minuti):
        return None
    return await esegui_digest(primary, locale.date() - timedelta(days=locale.weekday()))
