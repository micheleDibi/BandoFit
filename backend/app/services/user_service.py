"""Profili utente e abbonamenti (DB primario, service_role)."""

import re
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError, UnauthorizedError
from app.schemas.common import Page
from app.schemas.family import PlanSwitchAdjustment
from app.schemas.plan import PlanOut
from app.schemas.user import (
    AdminFamilyInfo,
    AdminUserOut,
    AdminUserUpdate,
    MeOut,
    ProfileOut,
    ProfileUpdate,
    SubscriptionOut,
)
from app.services import family_service

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


async def ensure_profile(primary, user_id: str, claims: dict) -> dict:
    """Crea un profilo (e un abbonamento Gratuito) per un utente autenticato che
    ne è privo, ripristinando un provisioning fallito a monte. Idempotente."""
    meta = claims.get("user_metadata") or {}
    await primary.table("profiles").upsert(
        {
            "id": user_id,
            "email": claims.get("email") or f"{user_id}@sconosciuta.local",
            "nome": (meta.get("nome") or meta.get("denominazione") or None),
            "cognome": (meta.get("cognome") or None),
            "azienda": (meta.get("azienda") or None),
        },
        on_conflict="id",
    ).execute()

    # Un membro di famiglia (invitato o attivo) eredita l'abbonamento dal padre:
    # il self-heal NON deve creargli un abbonamento gratuito concorrente.
    membership = await family_service.get_membership(primary, user_id)
    is_family_child = membership is not None and membership["status"] in ("pending", "active")

    active = (
        await primary.table("user_subscriptions")
        .select("id")
        .eq("user_id", user_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not active.data and not is_family_child:
        plan = (
            await primary.table("subscription_plans")
            .select("id")
            .eq("slug", "gratuito")
            .limit(1)
            .execute()
        )
        if plan.data:
            await primary.table("user_subscriptions").insert(
                {"user_id": user_id, "plan_id": plan.data[0]["id"]}
            ).execute()

    resp = (
        await primary.table("profiles").select(PROFILE_SELECT).eq("id", user_id).limit(1).execute()
    )
    if not resp.data:
        raise UnauthorizedError("Impossibile creare il profilo per questo account")
    return resp.data[0]


async def _fetch_active_subscription(primary, user_id: str) -> SubscriptionOut | None:
    resp = (
        await primary.table("user_subscriptions")
        .select(
            "id,status,data_inizio,data_scadenza,"
            "subscription_plans(id,nome,slug,descrizione,prezzo_annuale,ai_check,"
            "alert_attivo,alert_giorni_preavviso,num_account_aziendali,ordering,"
            "is_active,updated_at)"
        )
        .eq("user_id", user_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _map_subscription(resp.data)


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
    subscription = _map_subscription(row.get("user_subscriptions"))

    own_limit = subscription.plan.num_account_aziendali if subscription else 0
    family = await family_service.build_me_family(primary, user_id, own_limit)

    # Un figlio ATTIVO eredita l'abbonamento del titolare della famiglia.
    if family and family.role == "child" and family.status == "active":
        membership = await family_service.get_membership(primary, user_id)
        if membership:
            inherited = await _fetch_active_subscription(primary, membership["parent_id"])
            if inherited:
                inherited.inherited = True
                subscription = inherited

    return MeOut(
        profile=ProfileOut(**{k: row[k] for k in row if k != "user_subscriptions"}),
        subscription=subscription,
        family=family,
    )


async def update_profile(primary, user_id: str, data: ProfileUpdate) -> MeOut:
    changes = data.model_dump(exclude_unset=True)
    if changes:
        await primary.table("profiles").update(changes).eq("id", user_id).execute()
    return await get_me(primary, user_id)


async def switch_plan(primary, user_id: str, plan_id: int) -> MeOut:
    """Cambio piano. Se l'utente è titolare di una famiglia e il nuovo piano ha
    meno account, la RPC retrocede/revoca automaticamente gli eccedenti."""
    try:
        resp = await primary.rpc(
            "fn_switch_plan", {"p_user_id": str(user_id), "p_plan_id": plan_id}
        ).execute()
    except APIError as exc:
        family_service.raise_from_rpc(exc)

    adjustment = resp.data or {}
    await family_service.cleanup_revoked_new_users(primary, adjustment)

    me = await get_me(primary, user_id)
    if adjustment.get("demoted") or adjustment.get("revoked_pending"):
        me.plan_switch_adjustment = PlanSwitchAdjustment(
            demoted=adjustment.get("demoted") or [],
            revoked_pending=adjustment.get("revoked_pending") or [],
        )
    return me


def _sanitize_search(term: str) -> str:
    """Rimuove i caratteri che romperebbero la grammatica ``or=(...)`` di PostgREST
    (inclusi doppi apici, che aprirebbero un token quotato mai chiuso)."""
    return re.sub(r'[,()\\%*"]', " ", term).strip()


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

    rows = resp.data
    user_ids = [row["id"] for row in rows]
    memberships_by_member, counts_by_parent = await _family_context(primary, user_ids)

    # Per i figli attivi il piano mostrato è quello (ereditato) del titolare.
    active_parent_ids = list(
        {
            m["parent_id"]
            for m in memberships_by_member.values()
            if m["status"] == "active"
        }
    )
    parent_subs: dict[str, SubscriptionOut] = {}
    parent_emails: dict[str, str] = {}
    if active_parent_ids:
        subs_resp = (
            await primary.table("user_subscriptions")
            .select(
                "user_id,id,status,data_inizio,data_scadenza,"
                "subscription_plans(id,nome,slug,descrizione,prezzo_annuale,ai_check,"
                "alert_attivo,alert_giorni_preavviso,num_account_aziendali,ordering,"
                "is_active,updated_at)"
            )
            .in_("user_id", active_parent_ids)
            .eq("status", "active")
            .execute()
        )
        for sub_row in subs_resp.data:
            mapped = _map_subscription([sub_row])
            if mapped:
                mapped.inherited = True
                parent_subs[sub_row["user_id"]] = mapped
        emails_resp = (
            await primary.table("profiles")
            .select("id,email")
            .in_("id", active_parent_ids)
            .execute()
        )
        parent_emails = {r["id"]: r["email"] for r in emails_resp.data}

    items = []
    for row in rows:
        subscription = _map_subscription(row.get("user_subscriptions"))
        family: AdminFamilyInfo | None = None
        membership = memberships_by_member.get(row["id"])
        if membership:
            parent_id = membership["parent_id"]
            family = AdminFamilyInfo(
                type="child",
                status=membership["status"],
                parent_email=parent_emails.get(parent_id),
            )
            if membership["status"] == "active":
                subscription = parent_subs.get(parent_id) or subscription
        elif counts_by_parent.get(row["id"]):
            family = AdminFamilyInfo(type="parent", members_count=counts_by_parent[row["id"]])
        items.append(
            AdminUserOut(
                profile=ProfileOut(**{k: row[k] for k in row if k != "user_subscriptions"}),
                subscription=subscription,
                family=family,
            )
        )
    return Page.build(items, resp.count or 0, page, page_size)


async def _family_context(
    primary, user_ids: list[str]
) -> tuple[dict[str, dict], dict[str, int]]:
    """Membership correnti (per figlio) e conteggio membri (per padre) per una
    pagina di utenti, in due query batch."""
    if not user_ids:
        return {}, {}
    as_member = (
        await primary.table("family_members")
        .select("member_id,parent_id,status")
        .in_("member_id", user_ids)
        .in_("status", family_service.CURRENT_STATUSES)
        .execute()
    )
    memberships_by_member = {row["member_id"]: row for row in as_member.data}

    as_parent = (
        await primary.table("family_members")
        .select("parent_id")
        .in_("parent_id", user_ids)
        .in_("status", family_service.CURRENT_STATUSES)
        .execute()
    )
    counts_by_parent: dict[str, int] = {}
    for row in as_parent.data:
        counts_by_parent[row["parent_id"]] = counts_by_parent.get(row["parent_id"], 0) + 1
    return memberships_by_member, counts_by_parent


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
    return AdminUserOut(
        profile=me.profile, subscription=me.subscription, family=me_family_to_admin(me)
    )


async def admin_switch_user_plan(primary, target_user_id: UUID, plan_id: int) -> AdminUserOut:
    exists = (
        await primary.table("profiles").select("id").eq("id", str(target_user_id)).limit(1).execute()
    )
    if not exists.data:
        raise NotFoundError("Utente non trovato")

    # I piani sono a livello famiglia: per un figlio si agisce sul titolare.
    membership = await family_service.get_membership(primary, str(target_user_id))
    if membership and membership["status"] in ("pending", "active"):
        raise ForbiddenError(
            "Il piano si gestisce sull'account titolare della famiglia"
        )

    me = await switch_plan(primary, str(target_user_id), plan_id)
    return AdminUserOut(profile=me.profile, subscription=me.subscription, family=me_family_to_admin(me))


def me_family_to_admin(me: MeOut) -> AdminFamilyInfo | None:
    if not me.family:
        return None
    if me.family.role == "parent":
        used = me.family.used or 1
        return AdminFamilyInfo(type="parent", members_count=max(0, used - 1))
    return AdminFamilyInfo(type="child", status=me.family.status)
