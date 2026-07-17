"""Gestione dell'abbonamento lato utente (fase 3): downgrade/disdetta
differiti, rinnovo automatico, metodo di pagamento salvato.

Le transizioni di piano restano nelle RPC atomiche; qui si gestiscono le
INTENZIONI (cambio programmato, on/off del rinnovo) e il vault del metodo.
"""

import logging
from datetime import date

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.payment import (
    AddMethodOut,
    SavedMethodOut,
    ScheduledChangeOut,
    SubscriptionMgmtOut,
)

logger = logging.getLogger("bandofit.subscription")


async def _sub_attiva(primary, user_id: str) -> dict | None:
    resp = (
        await primary.table("user_subscriptions")
        .select("plan_id,data_scadenza,auto_renew,"
                "subscription_plans(id,slug,nome,ordering,prezzo_annuale,tipo_prezzo)")
        .eq("user_id", str(user_id)).eq("status", "active").limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _metodo(primary, user_id: str) -> SavedMethodOut:
    resp = (
        await primary.table("revolut_customers")
        .select("saved_method_id,saved_method_label")
        .eq("user_id", str(user_id)).limit(1)
        .execute()
    )
    if resp.data and resp.data[0].get("saved_method_id"):
        return SavedMethodOut(presente=True, label=resp.data[0].get("saved_method_label"))
    return SavedMethodOut(presente=False)


async def _cambio_programmato(primary, user_id: str) -> ScheduledChangeOut | None:
    resp = (
        await primary.table("scheduled_plan_changes")
        .select("effective_date,motivo,subscription_plans:to_plan_id(slug,nome)")
        .eq("user_id", str(user_id)).eq("status", "programmato").limit(1)
        .execute()
    )
    if not resp.data:
        return None
    row = resp.data[0]
    piano = row.get("subscription_plans") or {}
    return ScheduledChangeOut(
        to_plan_slug=piano.get("slug") or "", to_plan_nome=piano.get("nome") or "",
        effective_date=row["effective_date"], motivo=row["motivo"],
    )


async def stato(primary, user_id: str) -> SubscriptionMgmtOut:
    sub = await _sub_attiva(primary, user_id)
    return SubscriptionMgmtOut(
        auto_renew=bool(sub and sub.get("auto_renew")),
        data_scadenza=sub.get("data_scadenza") if sub else None,
        metodo=await _metodo(primary, user_id),
        cambio_programmato=await _cambio_programmato(primary, user_id),
    )


async def programma_downgrade(primary, user_id: str, plan_slug: str) -> SubscriptionMgmtOut:
    """Programma il passaggio a un piano INFERIORE (o Gratuito = disdetta) alla
    scadenza. Sostituisce un eventuale cambio già programmato."""
    sub = await _sub_attiva(primary, user_id)
    if not sub:
        raise NotFoundError("Nessun abbonamento attivo")
    corrente = sub.get("subscription_plans") or {}
    target = (
        await primary.table("subscription_plans")
        .select("id,slug,nome,ordering").eq("slug", plan_slug).eq("is_active", True)
        .limit(1).execute()
    )
    if not target.data:
        raise NotFoundError("Piano non disponibile")
    dest = target.data[0]
    if dest["ordering"] >= corrente.get("ordering", 0):
        raise BadRequestError(
            "Il cambio programmato serve per scendere di piano: per salire usa "
            "l'acquisto immediato"
        )
    scadenza = sub.get("data_scadenza")
    if not scadenza or date.fromisoformat(scadenza) <= date.today():
        raise ConflictError("L'abbonamento è già scaduto: non c'è nulla da programmare")

    await (
        primary.table("scheduled_plan_changes")
        .update({"status": "annullato", "cancelled_at": "now()"})
        .eq("user_id", str(user_id)).eq("status", "programmato").execute()
    )
    motivo = "disdetta" if dest["slug"] == "gratuito" else "downgrade"
    await primary.table("scheduled_plan_changes").insert({
        "user_id": str(user_id), "from_plan_id": sub["plan_id"],
        "to_plan_id": dest["id"], "effective_date": scadenza,
        "motivo": motivo, "created_by": str(user_id),
    }).execute()
    return await stato(primary, user_id)


async def annulla_cambio_programmato(primary, user_id: str) -> SubscriptionMgmtOut:
    resp = (
        await primary.table("scheduled_plan_changes")
        .update({"status": "annullato", "cancelled_at": "now()"})
        .eq("user_id", str(user_id)).eq("status", "programmato").execute()
    )
    if not resp.data:
        raise NotFoundError("Nessun cambio programmato da annullare")
    return await stato(primary, user_id)


async def imposta_auto_renew(primary, user_id: str, enabled: bool) -> SubscriptionMgmtOut:
    sub = await _sub_attiva(primary, user_id)
    if not sub:
        raise NotFoundError("Nessun abbonamento attivo")
    if enabled:
        metodo = await _metodo(primary, user_id)
        if not metodo.presente:
            raise ConflictError(
                "Per attivare il rinnovo automatico aggiungi prima un metodo di "
                "pagamento"
            )
    await (
        primary.table("user_subscriptions").update({"auto_renew": enabled})
        .eq("user_id", str(user_id)).eq("status", "active").execute()
    )
    return await stato(primary, user_id)


async def avvia_aggiunta_metodo(primary, revolut, user_id: str, email: str) -> AddMethodOut:
    """Ordine a 0 amount col solo scopo di salvare il metodo (verificato in
    sandbox). Il metodo si persiste al webhook/sync sull'ordine completato."""
    from app.services.payment_service import _customer_revolut  # riuso interno

    customer_id = await _customer_revolut(primary, revolut, user_id, email)
    ordine = await revolut.create_order(
        amount_cents=0, currency="EUR",
        description="Salvataggio metodo di pagamento",
        customer_id=customer_id, metadata={"user_id": str(user_id), "scopo": "add_method"},
        expire_pending_after="PT1H",
    )
    return AddMethodOut(revolut_order_token=ordine["token"])


async def revoca_metodo(primary, user_id: str) -> SubscriptionMgmtOut:
    """Revoca il metodo salvato e spegne il rinnovo automatico. NON tocca
    grace_until: chi è in grazia ci resta fino alla fine."""
    await (
        primary.table("revolut_customers")
        .update({"saved_method_id": None, "saved_method_type": None,
                 "saved_method_label": None, "saved_method_at": None})
        .eq("user_id", str(user_id)).execute()
    )
    await (
        primary.table("user_subscriptions").update({"auto_renew": False})
        .eq("user_id", str(user_id)).eq("status", "active").execute()
    )
    return await stato(primary, user_id)
