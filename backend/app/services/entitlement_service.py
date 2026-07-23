"""Il modulo entitlement lato backend (migration 0030).

La FORMULA dei limiti vive in SQL (`fn_entitlement_detail`: base del piano +
unità di addon allocativi, con dormienza quando la base non abilita la
capability); qui c'è solo la LETTURA — lo snapshot servito da GET
/me/entitlements e riusato dai serializer esistenti (quota AI-check, limite
famiglia). Nessun servizio deve più derivare un limite leggendo le colonne del
piano: chi ha bisogno del numero passa da qui o dai wrapper SQL
(`fn_family_limit` / `fn_effective_max_aziende`).
"""

from app.schemas.entitlement import (
    AiChecksEntitlement,
    EntitlementsOut,
    ResourceEntitlement,
)
from app.services.family_service import owner_and_editable

_VUOTA = {"base": 0, "extra": 0, "effettivo": 0, "usato": 0, "residuo": 0}


async def snapshot_for_owner(primary, owner_id: str) -> dict:
    """Snapshot grezzo (jsonb di `fn_entitlement_snapshot`) per un TITOLARE
    già risolto. I chiamanti che partono da un utente generico usano
    :func:`get_entitlements`, che prima risolve il titolare."""
    resp = await primary.rpc(
        "fn_entitlement_snapshot", {"p_user_id": str(owner_id)}
    ).execute()
    return resp.data or {}


def _risorsa(snap: dict, chiave: str) -> dict:
    return {**_VUOTA, **(snap.get(chiave) or {})}


async def get_entitlements(primary, user: dict) -> EntitlementsOut:
    """Per un collegato ATTIVO risolve il titolare (pool condiviso della
    famiglia, editable=False); titolari, pending e retrocessi hanno il
    proprio snapshot."""
    owner_id, editable = await owner_and_editable(primary, user)
    snap = await snapshot_for_owner(primary, owner_id)
    return EntitlementsOut(
        editable=editable,
        seats=ResourceEntitlement(**_risorsa(snap, "seats")),
        companies=ResourceEntitlement(**_risorsa(snap, "companies")),
        ai_checks=AiChecksEntitlement(**_risorsa(snap, "ai_checks")),
    )
