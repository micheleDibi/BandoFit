"""Gestione dei piani di abbonamento (DB primario, service_role)."""

from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.plan import PlanCreate, PlanOut, PlanUpdate

PLAN_SELECT = (
    "id,nome,slug,descrizione,prezzo_annuale,tipo_prezzo,etichetta_prezzo,"
    "ai_check,alert_attivo,"
    "alert_giorni_preavviso,alert_ritardo_giorni,num_account_aziendali,ordering,is_active,updated_at"
)

_UNIQUE_VIOLATION = "23505"


async def list_active_plans(primary) -> list[PlanOut]:
    resp = (
        await primary.table("subscription_plans")
        .select(PLAN_SELECT)
        .eq("is_active", True)
        .order("ordering")
        .execute()
    )
    return [PlanOut(**row) for row in resp.data]


async def list_all_plans(primary) -> list[PlanOut]:
    resp = await primary.table("subscription_plans").select(PLAN_SELECT).order("ordering").execute()
    return [PlanOut(**row) for row in resp.data]


async def create_plan(primary, data: PlanCreate) -> PlanOut:
    try:
        resp = (
            await primary.table("subscription_plans")
            .insert(data.model_dump(mode="json"))
            .execute()
        )
    except APIError as exc:
        if exc.code == _UNIQUE_VIOLATION:
            raise ConflictError(f"Esiste già un piano con slug '{data.slug}'") from exc
        raise
    return PlanOut(**resp.data[0])


async def update_plan(primary, plan_id: int, data: PlanUpdate) -> PlanOut:
    changes = data.model_dump(mode="json", exclude_unset=True)
    if not changes:
        raise BadRequestError("Nessun campo da aggiornare")
    resp = (
        await primary.table("subscription_plans")
        .update(changes)
        .eq("id", plan_id)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Piano non trovato")
    return PlanOut(**resp.data[0])
