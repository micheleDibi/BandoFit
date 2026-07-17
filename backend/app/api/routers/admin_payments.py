from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, OpenapiDep, PrimaryClient
from app.schemas.common import Page
from app.schemas.payment import PurchaseOut
from app.services import admin_payment_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/purchases", response_model=Page[PurchaseOut])
async def list_purchases(
    _admin: AdminUser,
    primary: PrimaryClient,
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[PurchaseOut]:
    return await admin_payment_service.list_purchases(
        primary, status, kind, page, page_size
    )


@router.get("/invoices")
async def list_invoices(
    _admin: AdminUser,
    primary: PrimaryClient,
    stato: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    return await admin_payment_service.list_invoices(primary, stato, page, page_size)


@router.post("/invoices/{invoice_id}/retry")
async def retry_invoice(
    invoice_id: str, _admin: AdminUser, primary: PrimaryClient, openapi: OpenapiDep
) -> dict:
    """Ritrasmette una fattura in errore/scartata (dopo correzione dei dati)
    con lo STESSO numero e la STESSA data."""
    return await admin_payment_service.retry_invoice(primary, openapi, invoice_id)


@router.get("/payment-anomalies")
async def list_anomalies(
    _admin: AdminUser,
    primary: PrimaryClient,
    stato: Literal["aperta", "risolta"] = Query(default="aperta"),
) -> dict:
    """Incassi orfani da riconciliare (rimborso manuale in v1)."""
    return await admin_payment_service.list_anomalies(primary, stato)


@router.post("/payment-anomalies/{audit_id}/resolve")
async def resolve_anomaly(
    audit_id: int, admin: AdminUser, primary: PrimaryClient
) -> dict:
    return await admin_payment_service.resolve_anomaly(primary, audit_id, admin["id"])
