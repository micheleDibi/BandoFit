"""Inventario addon per utente + grant/revoca admin (migration 0028).

Il saldo (user_addon_inventory) è una cache del ledger append-only; ogni
scrittura passa dalle RPC atomiche della 0028. Qui si legge l'inventario e si
orchestrano grant/revoca (con notifica all'utente, best-effort).
"""

import logging
from typing import NoReturn

from postgrest.exceptions import APIError

from app.core.errors import AppError, BadRequestError, ConflictError, NotFoundError, UpstreamError
from app.schemas.addon import (
    AddonLedgerEntryOut,
    AdminAddonMovementOut,
    AdminGrantAddonIn,
    AdminRevokeAddonIn,
    MyAddonOut,
)
from app.services import notification_service

logger = logging.getLogger("bandofit.addon_inventory")

_INVENTORY_SELECT = (
    "addon_id,quantita,updated_at,addons(slug,nome,descrizione,tipo_fruizione,risorsa)"
)

# detail-code delle RPC 0028 → (classe errore, messaggio)
_RPC_ERRORS: dict[str, tuple[type[AppError], str]] = {
    "motivation_required": (BadRequestError, "La motivazione è obbligatoria"),
    "quantita_non_valida": (BadRequestError, "Quantità non valida"),
    "user_not_found": (NotFoundError, "Utente non trovato"),
    "addon_not_available": (NotFoundError, "Add-on non disponibile"),
    "addon_gia_posseduto": (ConflictError, "L'utente possiede già questo add-on"),
    "niente_da_revocare": (ConflictError, "Nessuna unità da revocare"),
}


def _raise_from_rpc(exc: APIError) -> NoReturn:
    detail = (exc.details or "").strip()
    mapped = _RPC_ERRORS.get(detail)
    if mapped:
        error_cls, message = mapped
        raise error_cls(message) from exc
    logger.error("RPC inventario addon non mappata: code=%s detail=%s message=%s",
                 exc.code, exc.details, exc.message)
    raise UpstreamError() from exc


def _map_inventory(row: dict, acquistate: dict[int, int],
                   consumate: dict[int, int]) -> MyAddonOut:
    addon = row.get("addons") or {}
    return MyAddonOut(
        addon_id=row["addon_id"],
        slug=addon.get("slug") or "",
        nome=addon.get("nome") or "",
        descrizione=addon.get("descrizione"),
        tipo_fruizione=addon.get("tipo_fruizione") or "consumabile",
        risorsa=addon.get("risorsa"),
        quantita=row["quantita"],
        acquistate=acquistate.get(row["addon_id"], 0),
        consumate=consumate.get(row["addon_id"], 0),
        updated_at=row.get("updated_at"),
    )


async def get_inventory(primary, user_id: str) -> list[MyAddonOut]:
    """L'inventario dell'utente. Dalla 0030 include ANCHE le voci a quantità 0
    (un consumabile esaurito resta visibile in «I miei addon») e i totali dal
    ledger — acquistate/consumate; le revoche admin non contano come consumo."""
    resp = (
        await primary.table("user_addon_inventory")
        .select(_INVENTORY_SELECT)
        .eq("user_id", str(user_id))
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return []
    ledger = (
        await primary.table("addon_ledger")
        .select("addon_id,tipo,delta")
        .eq("user_id", str(user_id))
        .execute()
    )
    acquistate: dict[int, int] = {}
    consumate: dict[int, int] = {}
    for m in ledger.data or []:
        aid = m["addon_id"]
        if m["delta"] > 0:
            acquistate[aid] = acquistate.get(aid, 0) + m["delta"]
        elif m["tipo"] == "consume":
            consumate[aid] = consumate.get(aid, 0) - m["delta"]
    return [_map_inventory(r, acquistate, consumate) for r in rows]


async def get_ledger(primary, user_id: str, addon_id: int | None = None,
                     limit: int = 20) -> list[AddonLedgerEntryOut]:
    """Ultimi movimenti dell'utente (storico a scomparsa)."""
    query = (
        primary.table("addon_ledger")
        .select("tipo,delta,note,created_at")
        .eq("user_id", str(user_id))
    )
    if addon_id is not None:
        query = query.eq("addon_id", addon_id)
    resp = await query.order("created_at", desc=True).limit(limit).execute()
    return [AddonLedgerEntryOut(**r) for r in (resp.data or [])]


async def grant(primary, admin_id: str, user_id: str, data: AdminGrantAddonIn) -> AdminAddonMovementOut:
    try:
        resp = await primary.rpc("fn_admin_grant_addon", {
            "p_admin_id": str(admin_id), "p_user_id": str(user_id),
            "p_addon_id": data.addon_id, "p_quantita": data.quantita,
            "p_motivazione": data.motivazione,
        }).execute()
    except APIError as exc:
        _raise_from_rpc(exc)
    esito = resp.data or {}

    # Post-commit: la RPC ha GIÀ committato (riga acquisto + ledger +
    # inventario). Da qui in poi è tutto best-effort — incluso il lookup del
    # nome addon: un suo guasto non deve far risalire un 500, o l'admin
    # crederebbe fallito un grant già applicato e lo ripeterebbe (doppio
    # accredito sui consumabili).
    try:
        nome = await _addon_nome(primary, data.addon_id)
        await notification_service.notify(
            primary, [str(user_id)],
            tipo="addon_grant",
            titolo="Ti è stato accreditato un add-on",
            corpo=f"Hai ricevuto {data.quantita}× {nome}. Lo trovi nella tua area abbonamento.",
            url="/app/abbonamento",
            dedup_key=f"addon-grant:{esito.get('purchase_id')}",
        )
    except Exception:
        logger.warning("notifica grant addon non inviata (grant già applicato)", exc_info=True)
    return AdminAddonMovementOut(
        purchase_id=str(esito.get("purchase_id")) if esito.get("purchase_id") else None,
        quantita_residua=esito.get("quantita_residua", 0),
    )


async def revoke(primary, admin_id: str, user_id: str, addon_id: int,
                 data: AdminRevokeAddonIn) -> AdminAddonMovementOut:
    try:
        resp = await primary.rpc("fn_admin_revoke_addon", {
            "p_admin_id": str(admin_id), "p_user_id": str(user_id),
            "p_addon_id": addon_id, "p_quantita": data.quantita,
            "p_motivazione": data.motivazione,
        }).execute()
    except APIError as exc:
        _raise_from_rpc(exc)
    esito = resp.data or {}
    return AdminAddonMovementOut(
        quantita_residua=esito.get("quantita_residua", 0),
        quantita_revocata=esito.get("quantita_revocata"),
    )


async def _addon_nome(primary, addon_id: int) -> str:
    resp = (
        await primary.table("addons").select("nome").eq("id", addon_id).limit(1).execute()
    )
    return resp.data[0]["nome"] if resp.data else "add-on"
