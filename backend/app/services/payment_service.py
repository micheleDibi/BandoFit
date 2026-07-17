"""Checkout, riconciliazione ordini e storico acquisti (Fase 2 pagamenti).

Divisione dei ruoli, non negoziabile:
- il DENARO si muove solo su Revolut (ordine + widget / addebito su metodo
  salvato); qui non transita mai un dato carta;
- lo STATO si muove solo nelle RPC atomiche della 0026 (fn_complete_purchase /
  fn_fail_purchase): questo modulo prepara, interroga e RICONCILIA, mai
  applica a mano;
- prima di completare si rilegge SEMPRE l'ordine dal provider: il webhook è
  un suggerimento, la fonte di verità è GET /api/orders/{id};
- un incasso che non trova un purchase applicabile è un'ANOMALIA esplicita
  (audit + notifica agli admin), mai un no-op silenzioso.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from postgrest import APIError

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.billing import BillingProfileOut
from app.schemas.payment import (
    CheckoutIn,
    CheckoutOut,
    CheckoutPreviewOut,
    CheckoutTargetIn,
    PurchaseOut,
    PurchasesPage,
)
from app.services import billing_service, email_service, notification_service, pricing
from app.services.family_service import raise_from_rpc

logger = logging.getLogger("bandofit.payments")

_PURCHASE_SELECT = (
    "id,kind,status,oggetto_slug,oggetto_nome,descrizione,imponibile_cents,"
    "iva_cents,totale_cents,iva_aliquota,natura_iva,valuta,decline_reason,"
    "motivazione,created_at,paid_at,revolut_order_id,plan_id,addon_id"
)

# Stati ordine Revolut → transizione del purchase (verificati in Fase 0).
_STATI_FINALI_KO = {"failed": "scaduto", "cancelled": "annullato"}


def _map_purchase(row: dict) -> PurchaseOut:
    return PurchaseOut(
        id=str(row["id"]),
        kind=row["kind"],
        status=row["status"],
        oggetto_slug=row["oggetto_slug"],
        oggetto_nome=row["oggetto_nome"],
        descrizione=row["descrizione"],
        imponibile_cents=row["imponibile_cents"],
        iva_cents=row["iva_cents"],
        totale_cents=row["totale_cents"],
        iva_aliquota=str(row["iva_aliquota"]),
        natura_iva=row.get("natura_iva"),
        valuta=row["valuta"],
        decline_reason=row.get("decline_reason"),
        motivazione=row.get("motivazione"),
        created_at=row["created_at"],
        paid_at=row.get("paid_at"),
    )


# ------------------------------------------------------------------ contesto


async def _abbonamento_attivo(primary, user_id: str) -> dict | None:
    resp = (
        await primary.table("user_subscriptions")
        .select(
            "id,data_scadenza,auto_renew,"
            "subscription_plans(id,slug,nome,prezzo_annuale,ordering,tipo_prezzo)"
        )
        .eq("user_id", str(user_id))
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _piano_per_slug(primary, slug: str) -> dict | None:
    resp = (
        await primary.table("subscription_plans")
        .select("id,slug,nome,prezzo_annuale,ordering,tipo_prezzo,is_active")
        .eq("slug", slug)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _addon_per_slug(primary, slug: str) -> dict | None:
    resp = (
        await primary.table("addons")
        .select("id,slug,nome,prezzo,tipo_prezzo,is_active")
        .eq("slug", slug)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _giorni_residui(data_scadenza: str | date | None) -> int:
    if not data_scadenza:
        return 0
    scad = date.fromisoformat(data_scadenza) if isinstance(data_scadenza, str) else data_scadenza
    return (scad - date.today()).days


# ------------------------------------------------------------------- preview


async def _calcola(primary, user_id: str, target: CheckoutTargetIn,
                   billing: BillingProfileOut | None) -> CheckoutPreviewOut:
    tipo_soggetto = billing.tipo_soggetto if billing else None

    if target.addon_slug:
        addon = await _addon_per_slug(primary, target.addon_slug)
        if addon is None:
            raise NotFoundError("Addon non disponibile")
        if addon.get("tipo_prezzo") != "importo" or Decimal(str(addon["prezzo"])) <= 0:
            raise BadRequestError("Questo addon non è acquistabile online")
        imponibile_cents = pricing.in_cents(Decimal(str(addon["prezzo"])))
        iva_cents, aliquota, natura = pricing.iva_per_soggetto(imponibile_cents, tipo_soggetto)
        return CheckoutPreviewOut(
            kind="addon", oggetto_slug=addon["slug"], oggetto_nome=addon["nome"],
            listino_cents=imponibile_cents, credito_cents=0,
            imponibile_cents=imponibile_cents, iva_cents=iva_cents,
            iva_aliquota=str(aliquota), natura_iva=natura,
            totale_cents=imponibile_cents + iva_cents,
            dettaglio={"listino": str(addon["prezzo"])},
        )

    piano = await _piano_per_slug(primary, target.plan_slug)
    if piano is None:
        raise NotFoundError("Piano non disponibile")
    if piano.get("tipo_prezzo") != "importo" or Decimal(str(piano["prezzo_annuale"])) <= 0:
        # I 'su_richiesta' restano fuori dal self-service (guard esistente);
        # i gratuiti si prendono col downgrade, non col checkout.
        raise BadRequestError("Questo piano non è acquistabile online")

    sub = await _abbonamento_attivo(primary, user_id)
    corrente = (sub or {}).get("subscription_plans") or {}
    if corrente and piano["ordering"] <= corrente.get("ordering", 0):
        raise BadRequestError(
            "Puoi acquistare solo un piano superiore al tuo: per scendere di "
            "piano usa il cambio programmato alla scadenza"
        )

    prezzo_nuovo = Decimal(str(piano["prezzo_annuale"]))
    prezzo_vecchio = Decimal(str(corrente.get("prezzo_annuale", "0")))
    giorni = _giorni_residui((sub or {}).get("data_scadenza"))
    imponibile, credito = pricing.imponibile_upgrade(prezzo_nuovo, prezzo_vecchio, giorni)
    if imponibile <= 0:
        raise BadRequestError(
            "L'importo dell'upgrade risulta nullo: contatta l'assistenza"
        )
    imponibile_cents = pricing.in_cents(imponibile)
    iva_cents, aliquota, natura = pricing.iva_per_soggetto(imponibile_cents, tipo_soggetto)
    return CheckoutPreviewOut(
        kind="piano", oggetto_slug=piano["slug"], oggetto_nome=piano["nome"],
        listino_cents=pricing.in_cents(prezzo_nuovo),
        credito_cents=pricing.in_cents(credito),
        imponibile_cents=imponibile_cents, iva_cents=iva_cents,
        iva_aliquota=str(aliquota), natura_iva=natura,
        totale_cents=imponibile_cents + iva_cents,
        scadenza_risultante=date.today() + timedelta(days=365),
        dettaglio={
            "formula": "prezzo_nuovo - min(prezzo_vecchio * giorni_residui/365, prezzo_vecchio)",
            "prezzo_nuovo": str(prezzo_nuovo), "prezzo_vecchio": str(prezzo_vecchio),
            "giorni_residui": max(0, giorni), "credito": str(credito),
        },
    )


async def preview(primary, user_id: str, target: CheckoutTargetIn) -> CheckoutPreviewOut:
    billing = await billing_service.get_billing_profile(primary, user_id)
    return await _calcola(primary, user_id, target, billing)


# ------------------------------------------------------------------ checkout


async def _customer_revolut(primary, revolut, user_id: str, email: str) -> str:
    resp = (
        await primary.table("revolut_customers")
        .select("revolut_customer_id")
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["revolut_customer_id"]
    cust = await revolut.create_customer(email)
    await (
        primary.table("revolut_customers")
        .upsert({"user_id": str(user_id), "revolut_customer_id": cust["id"]},
                on_conflict="user_id")
        .execute()
    )
    return cust["id"]


async def checkout(primary, revolut, user_id: str, email: str, data: CheckoutIn) -> CheckoutOut:
    billing = await billing_service.get_billing_profile(primary, user_id)
    if billing is None:
        raise BadRequestError(
            "Prima di acquistare completa i tuoi dati di fatturazione"
        )
    calcolo = await _calcola(primary, user_id, data, billing)

    riga = {
        "user_id": str(user_id),
        "kind": calcolo.kind,
        "status": "in_attesa",
        "oggetto_slug": calcolo.oggetto_slug,
        "oggetto_nome": calcolo.oggetto_nome,
        "descrizione": (
            f"Abbonamento {calcolo.oggetto_nome} (12 mesi)"
            if calcolo.kind == "piano" else f"Addon {calcolo.oggetto_nome}"
        ),
        "imponibile_cents": calcolo.imponibile_cents,
        "iva_cents": calcolo.iva_cents,
        "totale_cents": calcolo.totale_cents,
        "iva_aliquota": calcolo.iva_aliquota,
        "natura_iva": calcolo.natura_iva,
        "valuta": calcolo.valuta,
        "dettaglio_calcolo": calcolo.dettaglio,
        "billing_snapshot": billing.model_dump(mode="json"),
        "auto_renew_scelto": data.auto_renew if calcolo.kind == "piano" else None,
    }
    if calcolo.kind == "piano":
        piano = await _piano_per_slug(primary, calcolo.oggetto_slug)
        riga["plan_id"] = piano["id"]
    else:
        addon = await _addon_per_slug(primary, calcolo.oggetto_slug)
        riga["addon_id"] = addon["id"]

    try:
        resp = await primary.table("purchases").insert(riga).execute()
    except Exception as exc:  # UNIQUE un solo in_attesa per utente
        if "purchases_one_pending" in str(exc):
            raise ConflictError(
                "Hai già un pagamento in corso: completalo o attendi qualche "
                "minuto e riprova"
            ) from exc
        raise
    purchase_id = str(resp.data[0]["id"])

    try:
        customer_id = await _customer_revolut(primary, revolut, user_id, email)
        ordine = await revolut.create_order(
            amount_cents=calcolo.totale_cents,
            currency=calcolo.valuta,
            description=riga["descrizione"],
            customer_id=customer_id,
            metadata={"purchase_id": purchase_id},
            expire_pending_after="PT1H",
        )
    except Exception:
        # L'ordine non esiste: il purchase non deve restare a bloccare l'utente.
        await primary.rpc(
            "fn_fail_purchase",
            {"p_purchase_id": purchase_id, "p_status": "annullato",
             "p_decline_reason": "ordine_non_creato"},
        ).execute()
        raise

    await (
        primary.table("purchases")
        .update({"revolut_order_id": ordine["id"]})
        .eq("id", purchase_id)
        .execute()
    )
    return CheckoutOut(
        purchase_id=purchase_id,
        revolut_order_token=ordine["token"],
        checkout_url=ordine.get("checkout_url"),
        totale_cents=calcolo.totale_cents,
    )


# ------------------------------------------------------------ riconciliazione


async def _purchase_per_ordine(primary, order_id: str) -> dict | None:
    resp = (
        await primary.table("purchases")
        .select(_PURCHASE_SELECT)
        .eq("revolut_order_id", order_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _segnala_anomalia(primary, *, order_id: str, motivo: str, dettagli: dict) -> None:
    """Un incasso non applicabile non deve MAI sparire in un log: audit +
    notifica a tutti gli admin (rimborso manuale in v1)."""
    logger.error("pagamento orfano su ordine %s: %s", order_id, motivo)
    try:
        await primary.table("audit_log").insert({
            "action": "payments.orphan",
            "payload": {"revolut_order_id": order_id, "motivo": motivo, **dettagli},
        }).execute()
        admins = await primary.table("profiles").select("id").eq("role", "admin").execute()
        await notification_service.notify(
            primary, [str(r["id"]) for r in (admins.data or [])],
            tipo="pagamento_orfano",
            titolo="Pagamento incassato da riconciliare",
            corpo="Un incasso Revolut non corrisponde a un acquisto applicabile: "
                  "serve una verifica manuale (possibile rimborso).",
            dedup_key=f"orphan:{order_id}:{motivo}",
        )
    except Exception:  # best-effort: l'anomalia resta nei log e in webhook_events
        logger.exception("segnalazione anomalia fallita per ordine %s", order_id)


async def _salva_metodo(primary, revolut, ordine: dict) -> None:
    """Se un ordine (acquisto con auto_renew o add-method a 0 €) ha salvato un
    metodo, lo si persiste su revolut_customers. Il customer_id sull'ordine
    lega il metodo all'utente. Best-effort: il vault è del provider."""
    customer_id = (ordine.get("customer") or {}).get("id")
    if not customer_id:
        return
    try:
        metodi = await revolut.get_payment_methods(customer_id)
    except Exception:
        return
    salvato = next((m for m in metodi if m.get("saved_for") == "merchant"), None)
    if not salvato:
        return
    dett = salvato.get("method_details") or salvato.get("card") or {}
    label = dett.get("last4") or dett.get("last_digits")
    await (
        primary.table("revolut_customers")
        .update({
            "saved_method_id": salvato["id"],
            "saved_method_type": salvato.get("type") or "card",
            "saved_method_label": f"•••• {label}" if label else None,
            "saved_method_at": "now()",
        })
        .eq("revolut_customer_id", customer_id)
        .execute()
    )


async def _crea_fattura(primary, purchase_id: str) -> None:
    """Crea la riga fattura dal purchase pagato (con paid_at aggiornato).
    Best-effort: un guasto qui non deve annullare il pagamento già applicato.
    La rete di sicurezza è invoice_service.recupera_fatture_mancanti (passo 6
    dello scheduler), che scansiona i pagati senza fattura e la ricrea."""
    from app.services import invoice_service

    resp = (
        await primary.table("purchases")
        .select("id,kind,descrizione,imponibile_cents,iva_cents,totale_cents,"
                "billing_snapshot,paid_at")
        .eq("id", purchase_id).limit(1).execute()
    )
    if resp.data:
        try:
            await invoice_service.crea_fattura_da_purchase(primary, resp.data[0])
        except Exception:
            logger.warning("creazione fattura rimandata per purchase %s", purchase_id)


async def _invia_ricevuta(primary, purchase: dict) -> None:
    resp = (
        await primary.table("profiles").select("email")
        .eq("id", purchase["user_id"]).limit(1).execute()
    )
    if not resp.data:
        return
    from app.core.config import get_settings

    url = f"{get_settings().frontend_url.rstrip('/')}/app/acquisti"
    try:
        await email_service.send_ricevuta_pagamento_email(
            resp.data[0]["email"], purchase["descrizione"],
            purchase["totale_cents"], url,
        )
    except Exception:
        logger.warning("ricevuta non inviata per purchase %s", purchase["id"])


async def elabora_ordine(primary, revolut, order_id: str) -> dict:
    """Riconciliazione condivisa da webhook e /sync: rilegge l'ordine dal
    provider e fa avanzare il purchase con le RPC idempotenti. Ritorna un
    esito sintetico (per il log del webhook / la risposta del sync)."""
    ordine = await revolut.get_order(order_id)
    stato = ordine.get("state")
    purchase = await _purchase_per_ordine(primary, order_id)
    if purchase is None:
        # Ordine "aggiungi metodo" (0 €): nessun purchase, ma al completamento
        # si salva il metodo. Non è un'anomalia.
        if stato == "completed" and (ordine.get("metadata") or {}).get("scopo") == "add_method":
            await _salva_metodo(primary, revolut, ordine)
            return {"esito": "metodo_salvato", "stato_ordine": stato}
        if stato == "completed":
            await _segnala_anomalia(primary, order_id=order_id,
                                    motivo="purchase_inesistente", dettagli={})
        return {"esito": "purchase_inesistente", "stato_ordine": stato}

    if stato == "completed":
        pagamenti = ordine.get("payments") or []
        payment_id = next(
            (p.get("id") for p in pagamenti if p.get("state") in ("captured", "completed")),
            None,
        )
        try:
            resp = await primary.rpc(
                "fn_complete_purchase",
                {"p_purchase_id": purchase["id"], "p_revolut_payment_id": payment_id},
            ).execute()
        except APIError as exc:
            raise_from_rpc(exc)
        esito = resp.data or {}
        if esito.get("esito") == "pagamento_orfano":
            await _segnala_anomalia(
                primary, order_id=order_id, motivo=esito.get("motivo") or "stato_incompatibile",
                dettagli={"purchase_id": purchase["id"],
                          "stato_purchase": esito.get("stato_purchase")},
            )
        elif esito.get("esito") == "applicato":
            # Metodo salvato al primo acquisto con auto_renew: persisti per i
            # rinnovi. Ricevuta di cortesia + riga fattura 'da_emettere' (il
            # worker la trasmette a SDI). La data fattura = data dell'incasso.
            await _salva_metodo(primary, revolut, ordine)
            await _invia_ricevuta(primary, purchase)
            await _crea_fattura(primary, purchase["id"])
        return esito

    if stato in _STATI_FINALI_KO:
        resp = await primary.rpc(
            "fn_fail_purchase",
            {"p_purchase_id": purchase["id"], "p_status": _STATI_FINALI_KO[stato],
             "p_decline_reason": None},
        ).execute()
        return resp.data or {}

    # pending/processing/authorised: si aggiorna solo l'ultimo motivo di
    # declino (il purchase resta in_attesa e il widget può ritentare).
    declino = next(
        (p.get("decline_reason") for p in reversed(ordine.get("payments") or [])
         if p.get("decline_reason")),
        None,
    )
    if declino and purchase["status"] == "in_attesa":
        await (
            primary.table("purchases")
            .update({"decline_reason": declino})
            .eq("id", purchase["id"])
            .execute()
        )
    return {"esito": "in_corso", "stato_ordine": stato, "decline_reason": declino}


async def sync_purchase(primary, revolut, user_id: str, purchase_id: str) -> PurchaseOut:
    """Fallback di riconciliazione azionato dal FE (pagina esito): stessa
    strada del webhook, idempotente per costruzione."""
    resp = (
        await primary.table("purchases")
        .select(_PURCHASE_SELECT)
        .eq("id", purchase_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Acquisto non trovato")
    row = resp.data[0]
    if row["status"] == "in_attesa" and row.get("revolut_order_id"):
        await elabora_ordine(primary, revolut, row["revolut_order_id"])
        resp = (
            await primary.table("purchases")
            .select(_PURCHASE_SELECT)
            .eq("id", purchase_id)
            .limit(1)
            .execute()
        )
        row = resp.data[0]
    return _map_purchase(row)


# -------------------------------------------------------------------- storico


async def lista_acquisti(primary, user_id: str, page: int, page_size: int) -> PurchasesPage:
    start = (page - 1) * page_size
    resp = (
        await primary.table("purchases")
        .select(_PURCHASE_SELECT, count="exact")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .range(start, start + page_size - 1)
        .execute()
    )
    items = [_map_purchase(r) for r in (resp.data or [])]
    return PurchasesPage.build(items, resp.count or 0, page, page_size)


async def dettaglio_acquisto(primary, user_id: str, purchase_id: str) -> PurchaseOut:
    resp = (
        await primary.table("purchases")
        .select(_PURCHASE_SELECT)
        .eq("id", purchase_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Acquisto non trovato")
    return _map_purchase(resp.data[0])
