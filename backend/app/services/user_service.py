"""Profili utente e abbonamenti (DB primario, service_role)."""

import re
from uuid import UUID

from app.core.errors import BadRequestError, NotFoundError
from app.schemas.common import Page
from app.schemas.plan import PlanOut
from app.schemas.user import (
    AdminUserOut,
    AdminUserUpdate,
    MeOut,
    ProfileOut,
    ProfileUpdate,
    SubscriptionOut,
)

PROFILE_SELECT = "id,email,nome,cognome,azienda,telefono,role,is_active,created_at"

# Embed dell'abbonamento attivo con il piano; il filtro sullo status va
# applicato dal chiamante con .eq("user_subscriptions.status", "active").
SUBSCRIPTION_EMBED = (
    "user_subscriptions(id,status,data_inizio,data_scadenza,"
    "subscription_plans(id,nome,slug,descrizione,prezzo_annuale,ai_check,"
    "alert_attivo,alert_giorni_preavviso,num_account_aziendali,ordering,is_active,updated_at))"
)


def _map_subscription(rows: list | None) -> SubscriptionOut | None:
    if not rows:
        return None
    sub = rows[0]
    plan = sub.get("subscription_plans")
    if not plan:
        return None
    return SubscriptionOut(
        id=sub["id"],
        status=sub["status"],
        data_inizio=sub["data_inizio"],
        data_scadenza=sub["data_scadenza"],
        plan=PlanOut(**plan),
    )


async def get_me(primary, user_id: str) -> MeOut:
    resp = (
        await primary.table("profiles")
        .select(f"{PROFILE_SELECT},{SUBSCRIPTION_EMBED}")
        .eq("id", user_id)
        .eq("user_subscriptions.status", "active")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Profilo non trovato")
    row = resp.data[0]
    return MeOut(
        profile=ProfileOut(**{k: row[k] for k in row if k != "user_subscriptions"}),
        subscription=_map_subscription(row.get("user_subscriptions")),
    )


async def update_profile(primary, user_id: str, data: ProfileUpdate) -> MeOut:
    changes = data.model_dump(exclude_unset=True)
    if changes:
        await primary.table("profiles").update(changes).eq("id", user_id).execute()
    return await get_me(primary, user_id)


async def switch_plan(primary, user_id: str, plan_id: int) -> MeOut:
    plan = (
        await primary.table("subscription_plans")
        .select("id,is_active")
        .eq("id", plan_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not plan.data:
        raise BadRequestError("Piano inesistente o non attivo")
    await primary.rpc("fn_switch_plan", {"p_user_id": str(user_id), "p_plan_id": plan_id}).execute()
    return await get_me(primary, user_id)


def _sanitize_search(term: str) -> str:
    """Rimuove i caratteri che romperebbero la grammatica ``or=(...)`` di PostgREST."""
    return re.sub(r"[,()\\%*]", " ", term).strip()


async def admin_list_users(
    primary,
    q: str | None,
    role: str | None,
    page: int,
    page_size: int,
) -> Page[AdminUserOut]:
    offset = (page - 1) * page_size
    query = (
        primary.table("profiles")
        .select(f"{PROFILE_SELECT},{SUBSCRIPTION_EMBED}", count="exact")
        .eq("user_subscriptions.status", "active")
    )
    if q:
        term = _sanitize_search(q)
        if term:
            query = query.or_(
                f"email.ilike.*{term}*,nome.ilike.*{term}*,"
                f"cognome.ilike.*{term}*,azienda.ilike.*{term}*"
            )
    if role:
        query = query.eq("role", role)
    resp = (
        await query.order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    items = [
        AdminUserOut(
            profile=ProfileOut(**{k: row[k] for k in row if k != "user_subscriptions"}),
            subscription=_map_subscription(row.get("user_subscriptions")),
        )
        for row in resp.data
    ]
    return Page.build(items, resp.count or 0, page, page_size)


async def admin_update_user(
    primary, target_user_id: UUID, data: AdminUserUpdate, acting_admin_id: str
) -> AdminUserOut:
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise BadRequestError("Nessun campo da aggiornare")

    if str(target_user_id) == str(acting_admin_id):
        if changes.get("role") == "cliente":
            raise BadRequestError("Non puoi rimuovere il ruolo admin a te stesso")
        if changes.get("is_active") is False:
            raise BadRequestError("Non puoi disattivare il tuo stesso account")

    resp = (
        await primary.table("profiles")
        .update(changes)
        .eq("id", str(target_user_id))
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Utente non trovato")
    me = await get_me(primary, str(target_user_id))
    return AdminUserOut(profile=me.profile, subscription=me.subscription)


async def admin_switch_user_plan(primary, target_user_id: UUID, plan_id: int) -> AdminUserOut:
    exists = (
        await primary.table("profiles").select("id").eq("id", str(target_user_id)).limit(1).execute()
    )
    if not exists.data:
        raise NotFoundError("Utente non trovato")
    me = await switch_plan(primary, str(target_user_id), plan_id)
    return AdminUserOut(profile=me.profile, subscription=me.subscription)
