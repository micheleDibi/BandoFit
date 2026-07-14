"""Lookup delle posizioni aziendali (DB primario, service_role).

Catalogo seminato dalla migration 0022 sul pattern di addons: le voci non si
eliminano, si disattivano (is_active). Non c'è CRUD admin: si amministra
via SQL. Lo slug è l'identificativo stabile che viaggia nello user_metadata
alla registrazione (gli id identity variano tra ambienti).
"""

from app.schemas.job_position import JobPositionOut

JOB_POSITION_SELECT = "id,nome,slug"

# Slug della voce col campo di testo libero associato (job_position_altro).
SLUG_ALTRO = "altro"


async def list_active_positions(primary) -> list[JobPositionOut]:
    resp = (
        await primary.table("job_positions")
        .select(JOB_POSITION_SELECT)
        .eq("is_active", True)
        .order("ordering")
        .execute()
    )
    return [JobPositionOut(**row) for row in resp.data]


async def get_active_by_slug(primary, slug: str) -> dict | None:
    resp = (
        await primary.table("job_positions")
        .select(JOB_POSITION_SELECT)
        .eq("slug", slug)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def get_active_by_id(primary, position_id: int) -> dict | None:
    resp = (
        await primary.table("job_positions")
        .select(JOB_POSITION_SELECT)
        .eq("id", position_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None
