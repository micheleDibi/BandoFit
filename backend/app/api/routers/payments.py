from fastapi import APIRouter, Query

from app.api.deps import BillingAccount, PrimaryClient, RevolutDep
from app.core.errors import PaymentsNotConfiguredError
from app.schemas.payment import (
    AddMethodOut,
    AutoRenewIn,
    CheckoutIn,
    CheckoutOut,
    CheckoutPreviewOut,
    CheckoutTargetIn,
    DowngradeIn,
    PurchaseOut,
    PurchasesPage,
    SubscriptionMgmtOut,
)
from app.services import payment_service, subscription_mgmt_service

router = APIRouter(prefix="/me", tags=["payments"])


@router.post("/checkout/preview", response_model=CheckoutPreviewOut)
async def checkout_preview(
    data: CheckoutTargetIn, user: BillingAccount, primary: PrimaryClient
) -> CheckoutPreviewOut:
    """Preventivo puro (listino, credito residuo, IVA, totale): nessun effetto."""
    return await payment_service.preview(primary, user["id"], data)


@router.post("/checkout", response_model=CheckoutOut)
async def checkout(
    data: CheckoutIn, user: BillingAccount, primary: PrimaryClient, revolut: RevolutDep
) -> CheckoutOut:
    """Crea il purchase e l'ordine Revolut; il FE apre il widget col token.
    I dati carta vivono SOLO nell'iframe del provider."""
    if not revolut.enabled:
        raise PaymentsNotConfiguredError()
    return await payment_service.checkout(
        primary, revolut, user["id"], user["email"], data
    )


@router.get("/purchases", response_model=PurchasesPage)
async def lista_acquisti(
    user: BillingAccount,
    primary: PrimaryClient,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PurchasesPage:
    return await payment_service.lista_acquisti(primary, user["id"], page, page_size)


@router.get("/purchases/{purchase_id}", response_model=PurchaseOut)
async def dettaglio_acquisto(
    purchase_id: str, user: BillingAccount, primary: PrimaryClient
) -> PurchaseOut:
    return await payment_service.dettaglio_acquisto(primary, user["id"], purchase_id)


@router.post("/purchases/{purchase_id}/sync", response_model=PurchaseOut)
async def sync_acquisto(
    purchase_id: str, user: BillingAccount, primary: PrimaryClient, revolut: RevolutDep
) -> PurchaseOut:
    """Riconciliazione on-demand (pagina esito): stessa strada del webhook,
    idempotente — utile quando il webhook è in ritardo o perso."""
    if not revolut.enabled:
        raise PaymentsNotConfiguredError()
    return await payment_service.sync_purchase(primary, revolut, user["id"], purchase_id)


# --- gestione abbonamento (rinnovo, disdetta, metodo di pagamento) -----------


@router.get("/subscription/management", response_model=SubscriptionMgmtOut)
async def stato_abbonamento(
    user: BillingAccount, primary: PrimaryClient
) -> SubscriptionMgmtOut:
    return await subscription_mgmt_service.stato(primary, user["id"])


@router.post("/subscription/downgrade", response_model=SubscriptionMgmtOut)
async def programma_downgrade(
    data: DowngradeIn, user: BillingAccount, primary: PrimaryClient
) -> SubscriptionMgmtOut:
    """Programma il passaggio a un piano inferiore (o Gratuito = disdetta)
    alla scadenza. L'utente resta sul piano attuale fino a quel giorno."""
    return await subscription_mgmt_service.programma_downgrade(
        primary, user["id"], data.plan_slug
    )


@router.delete("/subscription/scheduled-change", response_model=SubscriptionMgmtOut)
async def annulla_cambio(
    user: BillingAccount, primary: PrimaryClient
) -> SubscriptionMgmtOut:
    return await subscription_mgmt_service.annulla_cambio_programmato(primary, user["id"])


@router.post("/subscription/auto-renew", response_model=SubscriptionMgmtOut)
async def imposta_auto_renew(
    data: AutoRenewIn, user: BillingAccount, primary: PrimaryClient
) -> SubscriptionMgmtOut:
    return await subscription_mgmt_service.imposta_auto_renew(
        primary, user["id"], data.enabled
    )


@router.post("/payment-method", response_model=AddMethodOut)
async def aggiungi_metodo(
    user: BillingAccount, primary: PrimaryClient, revolut: RevolutDep
) -> AddMethodOut:
    """Avvia il salvataggio di un metodo di pagamento (ordine a 0 €): il FE
    apre il widget col token; il metodo si persiste al completamento."""
    if not revolut.enabled:
        raise PaymentsNotConfiguredError()
    return await subscription_mgmt_service.avvia_aggiunta_metodo(
        primary, revolut, user["id"], user["email"]
    )


@router.delete("/payment-method", response_model=SubscriptionMgmtOut)
async def revoca_metodo(
    user: BillingAccount, primary: PrimaryClient
) -> SubscriptionMgmtOut:
    return await subscription_mgmt_service.revoca_metodo(primary, user["id"])
