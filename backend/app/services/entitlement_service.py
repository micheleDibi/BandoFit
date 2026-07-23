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
from app.services.family_service import get_membership

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


async def usati_membro(
    primary, owner_id: str, member_id: str,
    periodo_inizio: str | None, periodo_fine: str | None,
) -> int:
    """Consumi AI-check del MEMBRO nella finestra del ciclo (righe ai_checks
    pending|ready con user_id = membro; gli errori non contano, come il pool).
    Unica sede della formula: il gate di consumo (ai_check_service) delega qui."""
    from datetime import date, timedelta

    query = (
        primary.table("ai_checks")
        .select("id", count="exact")
        .eq("family_parent_id", str(owner_id))
        .eq("user_id", str(member_id))
        .in_("status", ["pending", "ready"])
    )
    if periodo_inizio:
        query = query.gte("created_at", periodo_inizio)
    if periodo_fine:
        try:
            end = (date.fromisoformat(periodo_fine) + timedelta(days=1)).isoformat()
            query = query.lt("created_at", end)
        except ValueError:
            pass
    resp = await query.limit(1).execute()
    return resp.count or 0


async def get_entitlements(primary, user: dict) -> EntitlementsOut:
    """Per un collegato ATTIVO risolve il titolare (pool condiviso della
    famiglia, editable=False) e aggiunge budget/consumi propri (WP6);
    titolari, pending e retrocessi hanno il proprio snapshot."""
    membership = await get_membership(primary, user["id"])
    if membership and membership["status"] == "active":
        owner_id, editable = str(membership["parent_id"]), False
    else:
        owner_id, editable = str(user["id"]), True
        membership = None
    snap = await snapshot_for_owner(primary, owner_id)
    ai = AiChecksEntitlement(**_risorsa(snap, "ai_checks"))
    if membership is not None:
        ai.budget_membro = membership.get("ai_check_budget")
        ai.usati_membro = await usati_membro(
            primary, owner_id, str(user["id"]), ai.periodo_inizio, ai.periodo_fine
        )
    return EntitlementsOut(
        editable=editable,
        seats=ResourceEntitlement(**_risorsa(snap, "seats")),
        companies=ResourceEntitlement(**_risorsa(snap, "companies")),
        ai_checks=ai,
    )
