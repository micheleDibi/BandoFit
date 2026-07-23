"""Gestione del catalogo add-on (DB primario, service_role).

Gemello di plan_service: gli add-on non si eliminano, si disattivano
(is_active) — lo slug è l'identificativo stabile per le funzionalità future.
"""

from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.addon import AddonCreate, AddonOut, AddonUpdate
from app.services import entitlement_service, family_service

ADDON_SELECT = (
    "id,nome,slug,descrizione,prezzo,tipo_prezzo,tipo_fruizione,risorsa,"
    "etichetta_prezzo,ordering,is_active,updated_at"
)

_UNIQUE_VIOLATION = "23505"


async def _applica_acquistabilita(primary, user: dict, addons: list[AddonOut]) -> None:
    """Acquistabilità per l'utente corrente (0030), solo sugli addon che si
    comprano dal checkout (`importo` con prezzo > 0): un collegato ATTIVO non
    compra (il checkout gli risponderebbe 403 → `solo_titolare`); un addon
    allocativo richiede un piano la cui BASE abiliti la risorsa
    (`piano_non_idoneo` — con base 1 l'extra sarebbe dormiente)."""
    a_pagamento = [a for a in addons if a.tipo_prezzo == "importo" and a.prezzo > 0]
    if not a_pagamento:
        return

    membership = await family_service.get_membership(primary, user["id"])
    if membership and membership.get("status") == "active":
        for addon in a_pagamento:
            addon.acquistabile = False
            addon.motivo_non_acquistabile = "solo_titolare"
        return

    allocativi = [a for a in a_pagamento if a.risorsa]
    if not allocativi:
        return
    snap = await entitlement_service.snapshot_for_owner(primary, str(user["id"]))
    for addon in allocativi:
        base = int(((snap.get(addon.risorsa) or {}).get("base")) or 0)
        if base <= 1:
            addon.acquistabile = False
            addon.motivo_non_acquistabile = "piano_non_idoneo"


async def list_active_addons(primary, user: dict | None = None) -> list[AddonOut]:
    resp = (
        await primary.table("addons")
        .select(ADDON_SELECT)
        .eq("is_active", True)
        .order("ordering")
        .execute()
    )
    addons = [AddonOut(**row) for row in resp.data]
    if user is not None:
        await _applica_acquistabilita(primary, user, addons)
    return addons


async def list_all_addons(primary) -> list[AddonOut]:
    resp = await primary.table("addons").select(ADDON_SELECT).order("ordering").execute()
    return [AddonOut(**row) for row in resp.data]


async def create_addon(primary, data: AddonCreate) -> AddonOut:
    try:
        resp = await primary.table("addons").insert(data.model_dump(mode="json")).execute()
    except APIError as exc:
        if exc.code == _UNIQUE_VIOLATION:
            raise ConflictError(f"Esiste già un add-on con slug '{data.slug}'") from exc
        raise
    return AddonOut(**resp.data[0])


async def update_addon(primary, addon_id: int, data: AddonUpdate) -> AddonOut:
    changes = data.model_dump(mode="json", exclude_unset=True)
    if not changes:
        raise BadRequestError("Nessun campo da aggiornare")
    resp = (
        await primary.table("addons")
        .update(changes)
        .eq("id", addon_id)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Add-on non trovato")
    return AddonOut(**resp.data[0])
