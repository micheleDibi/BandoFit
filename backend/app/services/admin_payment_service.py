"""Viste e azioni admin sul modulo pagamenti: storico acquisti, registro
fatture (sola lettura), gestione anomalie (incassi orfani)."""

import logging

from app.schemas.common import Page
from app.schemas.payment import PurchaseOut
from app.services.payment_service import _map_purchase

logger = logging.getLogger("bandofit.admin_payments")

_PURCHASE_SELECT = (
    "id,kind,status,oggetto_slug,oggetto_nome,descrizione,imponibile_cents,"
    "iva_cents,totale_cents,iva_aliquota,natura_iva,valuta,decline_reason,"
    "motivazione,created_at,paid_at"
)


async def list_purchases(
    primary, status: str | None, kind: str | None, page: int, page_size: int
) -> Page[PurchaseOut]:
    start = (page - 1) * page_size
    query = primary.table("purchases").select(_PURCHASE_SELECT, count="exact")
    if status:
        query = query.eq("status", status)
    if kind:
        query = query.eq("kind", kind)
    resp = await query.order("created_at", desc=True).range(start, start + page_size - 1).execute()
    items = [_map_purchase(r) for r in (resp.data or [])]
    return Page.build(items, resp.count or 0, page, page_size)


async def list_invoices(primary, stato: str | None, page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    query = primary.table("invoices").select(
        "id,purchase_id,anno,serie,numero,data_documento,stato,provider_id,"
        "totale_cents,tentativi,created_at,emessa_at",
        count="exact",
    )
    if stato:
        query = query.eq("stato", stato)
    resp = await query.order("created_at", desc=True).range(start, start + page_size - 1).execute()
    return {
        "items": resp.data or [], "total": resp.count or 0,
        "page": page, "page_size": page_size,
    }


async def list_anomalies(primary, stato: str) -> dict:
    """Gli incassi orfani sono scritti in audit_log (action='payments.orphan')
    dal payment_service; la risoluzione aggiunge 'payments.orphan_resolved'."""
    aperte = (
        await primary.table("audit_log")
        .select("id,payload,created_at")
        .eq("action", "payments.orphan")
        .order("created_at", desc=True)
        .execute()
    )
    risolte = (
        await primary.table("audit_log")
        .select("payload")
        .eq("action", "payments.orphan_resolved")
        .execute()
    )
    risolti_id = {
        str((r.get("payload") or {}).get("audit_id")) for r in (risolte.data or [])
    }
    items = []
    for row in aperte.data or []:
        risolta = str(row["id"]) in risolti_id
        if (stato == "aperta") == risolta:
            continue
        items.append({"audit_id": row["id"], "payload": row.get("payload"),
                      "created_at": row["created_at"], "risolta": risolta})
    return {"items": items}


async def resolve_anomaly(primary, audit_id: int, admin_id: str) -> dict:
    await primary.table("audit_log").insert({
        "actor_id": str(admin_id),
        "action": "payments.orphan_resolved",
        "payload": {"audit_id": audit_id},
    }).execute()
    return {"ok": True}
