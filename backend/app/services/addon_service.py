"""Gestione del catalogo add-on (DB primario, service_role).

Gemello di plan_service: gli add-on non si eliminano, si disattivano
(is_active) — lo slug è l'identificativo stabile per le funzionalità future.
"""

from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.addon import AddonCreate, AddonOut, AddonUpdate

ADDON_SELECT = (
    "id,nome,slug,descrizione,prezzo,tipo_prezzo,etichetta_prezzo,"
    "ordering,is_active,updated_at"
)

_UNIQUE_VIOLATION = "23505"


async def list_active_addons(primary) -> list[AddonOut]:
    resp = (
        await primary.table("addons")
        .select(ADDON_SELECT)
        .eq("is_active", True)
        .order("ordering")
        .execute()
    )
    return [AddonOut(**row) for row in resp.data]


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
