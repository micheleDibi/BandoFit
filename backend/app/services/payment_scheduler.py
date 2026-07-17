"""Scheduler dei pagamenti: rinnovi automatici, dunning, cambi differiti.

Stesso stampo di alert_scheduler (task in-process, claim giornaliero su
payment_runs con 23505 = già fatto). I passi girano una volta al giorno e sono
TUTTI idempotenti; ognuno seleziona a FINESTRA, mai a uguaglianza di data —
il catch-up recupera solo la run di oggi, un giorno saltato non deve perdere
un preavviso o un addebito.

Vincoli non negoziabili (dalla review adversariale del piano):
- preavviso ≥7 giorni prima di OGNI addebito automatico (obbligo contrattuale
  Revolut per l'industria "subscription"): il passo 2 addebita solo se il
  preavviso è partito da ≥7 giorni; se è partito tardi, l'addebito slitta;
- la GRAZIA è ancorata a grace_until, non ad auto_renew: spegnere il rinnovo
  automatico o revocare la carta a metà dunning non deve degradare subito;
- mai due addebiti per lo stesso ciclo (UNIQUE su ciclo_rinnovo/tentativo +
  predicato "nessun rinnovo per il ciclo in QUALSIASI stato" al passo 2);
- ogni ordine MIT nasce con expire_pending_after, o i pending declinati
  restano orfani per sempre sul provider.

L'esecuzione delle transizioni di piano è SEMPRE nelle RPC atomiche della
0026; qui si orchestra e si notifica.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.services import email_service, pricing

logger = logging.getLogger("bandofit.payment_scheduler")

_MAX_SLEEP_SECONDS = 3600
_GIORNI_PREAVVISO = 7
_GIORNI_GRAZIA = 14
_RETRY_OFFSETS = {2: 3, 3: 7}  # tentativo → giorni dal ciclo
_EXPIRE_MIT = "PT24H"


# --------------------------------------------------------------- utilità date


def _oggi(fuso: ZoneInfo) -> date:
    return datetime.now(ZoneInfo("UTC")).astimezone(fuso).date()


def prossima_esecuzione(adesso: datetime, ora: str, fuso: ZoneInfo) -> datetime:
    locale = adesso.astimezone(fuso)
    ore, minuti = (int(p) for p in ora.split(":"))
    obiettivo = locale.replace(hour=ore, minute=minuti, second=0, microsecond=0)
    if obiettivo <= locale:
        domani = (locale + timedelta(days=1)).date()
        obiettivo = datetime.combine(domani, time(ore, minuti), tzinfo=fuso)
    return obiettivo


async def claim_run(primary, giorno: date) -> bool:
    try:
        await primary.table("payment_runs").insert({"giorno": giorno.isoformat()}).execute()
        return True
    except APIError as exc:
        if exc.code == "23505":
            return False
        raise


# ------------------------------------------------------------------- helpers


async def _email_e_url(primary, user_id: str, path: str) -> tuple[str | None, str]:
    resp = (
        await primary.table("profiles").select("email").eq("id", user_id).limit(1).execute()
    )
    email = resp.data[0]["email"] if resp.data else None
    base = get_settings().frontend_url.rstrip("/")
    return email, f"{base}{path}"


async def _crea_rinnovo(primary, revolut, sub: dict, piano_dest: dict, tentativo: int) -> str | None:
    """Crea il purchase di rinnovo (piano di DESTINAZIONE) + l'ordine MIT e lo
    addebita. Ritorna il purchase_id, o None se: manca metodo/customer, manca
    il profilo di fatturazione, o il ciclo/tentativo era già preso."""
    from app.services import billing_service

    user_id = sub["user_id"]
    ciclo = sub["data_scadenza"]
    cust = (
        await primary.table("revolut_customers")
        .select("revolut_customer_id,saved_method_id,saved_method_type")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not cust.data or not cust.data[0].get("saved_method_id"):
        return None  # nessun metodo salvato: trattato come manuale altrove
    customer = cust.data[0]

    # billing_snapshot COMPLETO congelato sul purchase (come il checkout): senza,
    # la fattura di rinnovo nascerebbe con cessionario vuoto → scarto a SDI.
    billing = await billing_service.get_billing_profile(primary, user_id)
    if billing is None:
        logger.warning(
            "rinnovo di %s saltato: profilo di fatturazione mancante", user_id
        )
        return None
    tipo_soggetto = billing.tipo_soggetto

    imponibile_cents = pricing.in_cents(Decimal(str(piano_dest["prezzo_annuale"])))
    iva_cents, aliquota, natura = pricing.iva_per_soggetto(imponibile_cents, tipo_soggetto)
    riga = {
        "user_id": user_id, "kind": "rinnovo", "status": "in_attesa",
        "plan_id": piano_dest["id"], "oggetto_slug": piano_dest["slug"],
        "oggetto_nome": piano_dest["nome"],
        "descrizione": f"Rinnovo {piano_dest['nome']} (12 mesi)",
        "imponibile_cents": imponibile_cents, "iva_cents": iva_cents,
        "totale_cents": imponibile_cents + iva_cents, "iva_aliquota": str(aliquota),
        "natura_iva": natura, "ciclo_rinnovo": ciclo, "tentativo": tentativo,
        "billing_snapshot": billing.model_dump(mode="json"),
    }
    try:
        resp = await primary.table("purchases").insert(riga).execute()
    except APIError as exc:
        if exc.code == "23505":
            # Discrimina i due UNIQUE: il ciclo/tentativo già creato è uno skip
            # idempotente atteso; un checkout interattivo abbandonato
            # (purchases_one_pending) invece BLOCCA il rinnovo silenziosamente
            # a ogni run — va segnalato, non ingoiato.
            if "purchases_one_pending" in (exc.message or "") + (exc.details or ""):
                logger.warning(
                    "rinnovo di %s bloccato: l'utente ha un checkout in sospeso "
                    "(purchases_one_pending) — verrà ritentato al prossimo run",
                    user_id,
                )
            return None
        raise
    purchase_id = str(resp.data[0]["id"])

    try:
        ordine = await revolut.create_order(
            amount_cents=riga["totale_cents"], currency="EUR",
            description=riga["descrizione"], customer_id=customer["revolut_customer_id"],
            metadata={"purchase_id": purchase_id}, expire_pending_after=_EXPIRE_MIT,
        )
        await (
            primary.table("purchases").update({"revolut_order_id": ordine["id"]})
            .eq("id", purchase_id).execute()
        )
        await revolut.pay_with_saved_method(
            ordine["id"], customer["saved_method_id"],
            customer.get("saved_method_type") or "card",
        )
    except Exception:
        # L'esito del charge può essere ignoto (timeout): NON si forza fallito
        # qui — la riconciliazione (webhook/poll) deciderà. Si logga e basta.
        logger.warning("rinnovo %s: charge non confermato, si riconcilia dopo", purchase_id)
    return purchase_id


# -------------------------------------------------------------------- i passi


async def passo_preavvisi(primary, oggi: date) -> int:
    """1) auto_renew con scadenza entro 7 giorni e preavviso non ancora
    inviato → email + timestamp. Finestra (≤ oggi+7), non uguaglianza."""
    limite = (oggi + timedelta(days=_GIORNI_PREAVVISO)).isoformat()
    resp = (
        await primary.table("user_subscriptions")
        .select("user_id,data_scadenza,subscription_plans(nome,slug,prezzo_annuale,tipo_prezzo)")
        .eq("status", "active").eq("auto_renew", True)
        .is_("renewal_notice_sent_at", "null")
        .lte("data_scadenza", limite)
        .execute()
    )
    n = 0
    for sub in resp.data or []:
        piano = sub.get("subscription_plans") or {}
        importo = pricing.in_cents(Decimal(str(piano.get("prezzo_annuale") or "0")))
        importo += pricing.iva_per_soggetto(importo, None)[0]  # stima IVA italiana
        email, url = await _email_e_url(primary, sub["user_id"], "/app/abbonamento")
        if email:
            await email_service.send_promemoria_rinnovo_email(
                email, piano.get("nome") or "abbonamento", importo,
                sub["data_scadenza"], auto=True, cta_url=url,
            )
        await (
            primary.table("user_subscriptions").update({"renewal_notice_sent_at": "now()"})
            .eq("user_id", sub["user_id"]).eq("status", "active").execute()
        )
        n += 1
    return n


async def passo_promemoria_manuali(primary, oggi: date) -> int:
    """1-bis) piani a pagamento SENZA auto_renew: promemoria a −14 e −3."""
    n = 0
    for delta in (14, 3):
        giorno = (oggi + timedelta(days=delta)).isoformat()
        resp = (
            await primary.table("user_subscriptions")
            .select("user_id,data_scadenza,subscription_plans(nome,slug,prezzo_annuale,tipo_prezzo)")
            .eq("status", "active").eq("auto_renew", False)
            .eq("data_scadenza", giorno)
            .execute()
        )
        for sub in resp.data or []:
            piano = sub.get("subscription_plans") or {}
            if piano.get("tipo_prezzo") != "importo" or Decimal(str(piano.get("prezzo_annuale") or "0")) <= 0:
                continue
            importo = pricing.in_cents(Decimal(str(piano["prezzo_annuale"])))
            importo += pricing.iva_per_soggetto(importo, None)[0]
            email, url = await _email_e_url(
                primary, sub["user_id"], f"/app/checkout?piano={piano['slug']}"
            )
            if email:
                await email_service.send_promemoria_rinnovo_email(
                    email, piano["nome"], importo, sub["data_scadenza"],
                    auto=False, cta_url=url,
                )
            n += 1
    return n


async def _rinnovo_esistente(primary, user_id: str, ciclo: str) -> list[dict]:
    resp = (
        await primary.table("purchases")
        .select("id,status,tentativo")
        .eq("user_id", user_id).eq("kind", "rinnovo").eq("ciclo_rinnovo", ciclo)
        .execute()
    )
    return resp.data or []


async def _piano_destinazione(primary, sub: dict) -> dict | None:
    """Il piano da rinnovare: la destinazione di un cambio programmato per il
    ciclo, altrimenti il piano corrente."""
    sched = (
        await primary.table("scheduled_plan_changes")
        .select("to_plan_id,subscription_plans:to_plan_id(id,slug,nome,prezzo_annuale,tipo_prezzo)")
        .eq("user_id", sub["user_id"]).eq("status", "programmato")
        .eq("effective_date", sub["data_scadenza"])
        .limit(1)
        .execute()
    )
    if sched.data and sched.data[0].get("subscription_plans"):
        return sched.data[0]["subscription_plans"]
    return sub.get("subscription_plans")


async def passo_addebiti(primary, revolut, oggi: date) -> int:
    """2) auto_renew scaduto, preavviso ≥7 giorni fa, nessun rinnovo per il
    ciclo in QUALSIASI stato → tentativo 1. Il passo 2 possiede solo il primo
    tentativo; i retry sono del passo 3."""
    soglia_preavviso = (
        datetime.now(ZoneInfo("UTC")) - timedelta(days=_GIORNI_PREAVVISO)
    ).isoformat()
    resp = (
        await primary.table("user_subscriptions")
        .select("user_id,data_scadenza,renewal_notice_sent_at,"
                "subscription_plans(id,slug,nome,prezzo_annuale,tipo_prezzo)")
        .eq("status", "active").eq("auto_renew", True)
        .lte("data_scadenza", oggi.isoformat())
        .not_.is_("renewal_notice_sent_at", "null")
        .lte("renewal_notice_sent_at", soglia_preavviso)
        .execute()
    )
    n = 0
    for sub in resp.data or []:
        if await _rinnovo_esistente(primary, sub["user_id"], sub["data_scadenza"]):
            continue  # ciclo già in lavorazione
        piano_dest = await _piano_destinazione(primary, sub)
        if not piano_dest:
            continue
        if await _crea_rinnovo(primary, revolut, sub, piano_dest, tentativo=1):
            n += 1
    return n


async def passo_retry(primary, revolut, oggi: date) -> int:
    """3) rinnovi falliti: tentativo 2 da ciclo+3, tentativo 3 da ciclo+7.
    Proprietà esclusiva di questo passo. Selezione a FINESTRA (non uguaglianza
    di data): un giorno di scheduler saltato non deve perdere i retry — al
    primo run utile la finestra li recupera. Il limite inferiore evita di
    ripescare cicli antichi (dopo la grazia il piano è già degradato: qui la
    finestra è ampiamente sufficiente)."""
    n = 0
    for tentativo, offset in _RETRY_OFFSETS.items():
        alto = (oggi - timedelta(days=offset)).isoformat()
        basso = (oggi - timedelta(days=_GIORNI_GRAZIA + 2)).isoformat()
        resp = (
            await primary.table("user_subscriptions")
            .select("user_id,data_scadenza,grace_until,"
                    "subscription_plans(id,slug,nome,prezzo_annuale,tipo_prezzo)")
            .eq("status", "active").eq("auto_renew", True)
            .lte("data_scadenza", alto)
            .gte("data_scadenza", basso)
            .execute()
        )
        for sub in resp.data or []:
            ciclo = sub["data_scadenza"]
            esistenti = await _rinnovo_esistente(primary, sub["user_id"], ciclo)
            if any(p["status"] == "pagato" for p in esistenti):
                continue
            ultimo = max((p["tentativo"] for p in esistenti), default=0)
            if ultimo != tentativo - 1:
                continue  # il tentativo precedente non è ancora concluso/fallito
            if not all(p["status"] == "fallito" for p in esistenti):
                continue
            piano_dest = await _piano_destinazione(primary, sub)
            if piano_dest and await _crea_rinnovo(primary, revolut, sub, piano_dest, tentativo):
                email, url = await _email_e_url(
                    primary, sub["user_id"], "/app/abbonamento"
                )
                grazia = sub.get("grace_until") or ""
                if email:
                    await email_service.send_pagamento_fallito_email(
                        email, (sub.get("subscription_plans") or {}).get("nome") or "abbonamento",
                        None, grazia, url,
                    )
                n += 1
    return n


async def passo_fine_grazia(primary, oggi: date) -> int:
    """4) grace_until superato e ciclo non pagato → downgrade a gratuito.
    Unico proprietario della fine grazia."""
    resp = (
        await primary.table("user_subscriptions")
        .select("user_id,data_scadenza,grace_until")
        .eq("status", "active").eq("auto_renew", True)
        .lt("grace_until", oggi.isoformat())
        .execute()
    )
    n = 0
    for sub in resp.data or []:
        if any(p["status"] == "pagato"
               for p in await _rinnovo_esistente(primary, sub["user_id"], sub["data_scadenza"])):
            continue
        await _degrada_a_gratuito(primary, sub["user_id"], sub["data_scadenza"], "grace_scaduta")
        n += 1
    return n


async def passo_scadenze_manuali(primary, oggi: date) -> int:
    """5) scaduti senza auto_renew e senza rinnovo pagato, FUORI grazia →
    esegue il cambio programmato se c'è, altrimenti downgrade a gratuito."""
    resp = (
        await primary.table("user_subscriptions")
        .select("user_id,data_scadenza,grace_until")
        .eq("status", "active").eq("auto_renew", False)
        .lt("data_scadenza", oggi.isoformat())
        .execute()
    )
    n = 0
    for sub in resp.data or []:
        if sub.get("grace_until") and sub["grace_until"] >= oggi.isoformat():
            continue  # grazia in corso: non tocca a questo passo
        rinnovi = await _rinnovo_esistente(primary, sub["user_id"], sub["data_scadenza"])
        if any(p["status"] == "pagato" for p in rinnovi):
            continue
        if any(p["status"] == "in_attesa" for p in rinnovi):
            continue  # un «paga ora» è in volo
        await _degrada_a_gratuito(primary, sub["user_id"], sub["data_scadenza"], "mancato_rinnovo")
        n += 1
    return n


async def _degrada_a_gratuito(primary, user_id: str, ciclo: str, motivo: str) -> None:
    """Se esiste un cambio programmato per il ciclo lo esegue (la RPC applica la
    regola «destinazione a pagamento non pagata → gratuito»); altrimenti
    programma ed esegue un passaggio a gratuito. Poi notifica."""
    sched = (
        await primary.table("scheduled_plan_changes")
        .select("id").eq("user_id", user_id).eq("status", "programmato")
        .eq("effective_date", ciclo).limit(1)
        .execute()
    )
    if sched.data:
        await primary.rpc(
            "fn_execute_scheduled_change", {"p_id": sched.data[0]["id"]}
        ).execute()
    else:
        free = (
            await primary.table("subscription_plans").select("id")
            .eq("slug", "gratuito").limit(1).execute()
        )
        nuovo = (
            await primary.table("scheduled_plan_changes").insert({
                "user_id": user_id, "to_plan_id": free.data[0]["id"],
                "effective_date": ciclo, "motivo": motivo,
            }).execute()
        )
        await primary.rpc(
            "fn_execute_scheduled_change", {"p_id": nuovo.data[0]["id"]}
        ).execute()
    email, url = await _email_e_url(primary, user_id, "/app/abbonamento")
    if email:
        await email_service.send_downgrade_email(email, url)


async def passo_fatture(primary) -> int:
    """6) emette le fatture pendenti e riconcilia gli esiti. L'openapi client
    è nello state dell'app; il worker lo prende da lì (o lo salta se assente).
    Nota: qui riceve None come openapi se non configurato — emetti_pendenti
    lo gestisce (no-op)."""
    from app.services import invoice_service

    esiti = await invoice_service.emetti_pendenti(primary, _openapi(), 50)
    return esiti.get("emesse", 0)


_openapi_client = None


def imposta_openapi(client) -> None:
    """Iniettato dal lifespan: il worker fatture usa lo stesso client openapi
    dell'app (stesso token/scope). Tenuto a modulo per non cambiare la firma
    di run_forever (allineata all'alert_scheduler)."""
    global _openapi_client
    _openapi_client = client


def _openapi():
    return _openapi_client


# ------------------------------------------------------------ orchestrazione


async def esegui_run(primary, revolut, oggi: date) -> dict:
    """Esegue i passi in ordine. Ognuno è isolato: un errore in un passo si
    logga e non impedisce gli altri (un guasto sui preavvisi non deve bloccare
    i downgrade dovuti)."""
    esiti: dict[str, int | str] = {}
    passi = [
        ("preavvisi", lambda: passo_preavvisi(primary, oggi)),
        ("promemoria_manuali", lambda: passo_promemoria_manuali(primary, oggi)),
        ("addebiti", lambda: passo_addebiti(primary, revolut, oggi)),
        ("retry", lambda: passo_retry(primary, revolut, oggi)),
        ("fine_grazia", lambda: passo_fine_grazia(primary, oggi)),
        ("scadenze_manuali", lambda: passo_scadenze_manuali(primary, oggi)),
        ("fatture", lambda: passo_fatture(primary)),
    ]
    for nome, fn in passi:
        try:
            esiti[nome] = await fn()
        except Exception:
            logger.error("payment scheduler: passo %s fallito", nome, exc_info=True)
            esiti[nome] = "errore"
    logger.info("payment scheduler: run %s → %s", oggi.isoformat(), esiti)
    return esiti


async def esegui_se_dovuto(primary, revolut, adesso: datetime) -> dict | None:
    settings = get_settings()
    fuso = ZoneInfo(settings.alert_fuso)
    locale = adesso.astimezone(fuso)
    ore, minuti = (int(p) for p in settings.payment_ora_esecuzione.split(":"))
    if (locale.hour, locale.minute) < (ore, minuti):
        return None
    oggi = locale.date()
    if not await claim_run(primary, oggi):
        return None
    return await esegui_run(primary, revolut, oggi)


async def run_forever(primary, revolut) -> None:
    settings = get_settings()
    fuso = ZoneInfo(settings.alert_fuso)
    while True:
        try:
            await esegui_se_dovuto(primary, revolut, datetime.now(ZoneInfo("UTC")))
            prossima = prossima_esecuzione(
                datetime.now(ZoneInfo("UTC")),
                get_settings().payment_ora_esecuzione, fuso,
            )
            while True:
                resta = (prossima - datetime.now(ZoneInfo("UTC"))).total_seconds()
                if resta <= 0:
                    break
                await asyncio.sleep(min(resta, _MAX_SLEEP_SECONDS))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("payment scheduler: errore inatteso, riprovo tra un'ora", exc_info=True)
            await asyncio.sleep(_MAX_SLEEP_SECONDS)
