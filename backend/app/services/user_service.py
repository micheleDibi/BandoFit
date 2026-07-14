"""Profili utente e abbonamenti (DB primario, service_role)."""

import logging
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
    ProgettistaOut,
    SubscriptionOut,
)
from app.services import family_service, job_position_service

logger = logging.getLogger("bandofit.users")

PROFILE_SELECT = (
    "id,email,nome,cognome,azienda,telefono,codice_fiscale,cf_verified_at,"
    "job_position_id,job_position_altro,job_positions(id,nome,slug),"
    "role,is_active,created_at"
)

# Embed dell'abbonamento attivo con il piano; il filtro sullo status va
# applicato dal chiamante con .eq("user_subscriptions.status", "active").
SUBSCRIPTION_EMBED = (
    "user_subscriptions(id,status,data_inizio,data_scadenza,"
    "subscription_plans(id,nome,slug,descrizione,prezzo_annuale,tipo_prezzo,"
    "etichetta_prezzo,ai_check,"
    "alert_attivo,alert_giorni_preavviso,alert_ritardo_giorni,num_account_aziendali,ordering,is_active,updated_at))"
)


def profile_from_row(row: dict) -> ProfileOut:
    """Costruisce ProfileOut da una riga di ``profiles``: l'embed PostgREST
    arriva con il nome della tabella (``job_positions``), lo schema lo espone
    al singolare; gli embed estranei (abbonamento) vengono scartati."""
    data = {k: v for k, v in row.items() if k not in ("user_subscriptions", "job_positions")}
    data["job_position"] = row.get("job_positions")
    return ProfileOut(**data)


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
    # Stessa risoluzione slug→posizione del trigger handle_new_user (0022),
    # best-effort: slug assente/ignoto/disattivato → NULL.
    position = None
    if meta.get("job_position_slug"):
        position = await job_position_service.get_active_by_slug(
            primary, meta["job_position_slug"]
        )
    await primary.table("profiles").upsert(
        {
            "id": user_id,
            "email": claims.get("email") or f"{user_id}@sconosciuta.local",
            "nome": (meta.get("nome") or meta.get("denominazione") or None),
            "cognome": (meta.get("cognome") or None),
            "azienda": (meta.get("azienda") or None),
            "telefono": (meta.get("telefono") or None),
            "job_position_id": position["id"] if position else None,
            "job_position_altro": (
                (meta.get("job_position_altro") or None)
                if position and position["slug"] == job_position_service.SLUG_ALTRO
                else None
            ),
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
            "subscription_plans(id,nome,slug,descrizione,prezzo_annuale,tipo_prezzo,"
            "etichetta_prezzo,ai_check,"
            "alert_attivo,alert_giorni_preavviso,alert_ritardo_giorni,num_account_aziendali,ordering,"
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


async def _fetch_progettista(primary, user_id: str, role: str) -> ProgettistaOut | None:
    """Codice progettista per la risposta /me e admin. Per i ruoli con l'area
    progettista (parità admin, 0019): per un admin la riga può non esistere
    ancora (il codice arriva alla prima proposta) → None. Dopo una demozione
    a cliente la riga resta a DB (codice riservato) ma non si espone."""
    if role not in ("progettista", "admin"):
        return None
    resp = (
        await primary.table("progettisti")
        .select("codice")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return ProgettistaOut(codice=resp.data[0]["codice"])


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
    membership = await family_service.get_membership(primary, user_id)
    family = await family_service.build_me_family(
        primary, user_id, own_limit, membership=membership
    )

    # Un figlio ATTIVO eredita l'abbonamento del titolare della famiglia.
    if membership and membership["status"] == "active":
        inherited = await _fetch_active_subscription(primary, membership["parent_id"])
        if inherited:
            inherited.inherited = True
            subscription = inherited

    return MeOut(
        profile=profile_from_row(row),
        subscription=subscription,
        family=family,
        progettista=await _fetch_progettista(primary, user_id, row["role"]),
    )


async def update_profile(primary, user_id: str, data: ProfileUpdate) -> MeOut:
    changes = data.model_dump(exclude_unset=True)

    if changes.get("job_position_id") is not None:
        # La FK non basta: il catalogo è soft-disable, quindi bisogna
        # intercettare anche le posizioni DISATTIVATE (e rispondere 400
        # invece del 500 di una FK violation PostgREST).
        position = await job_position_service.get_active_by_id(
            primary, changes["job_position_id"]
        )
        if position is None:
            raise BadRequestError("La posizione selezionata non è disponibile")

    # La coerenza posizione/testo «Altro» la impone il trigger di riga
    # trg_profiles_job_position_altro (0022): race-free per costruzione,
    # a differenza di un check read-then-write fatto qui.
    if changes:
        await primary.table("profiles").update(changes).eq("id", user_id).execute()
    return await get_me(primary, user_id)


async def switch_plan(primary, user_id: str, plan_id: int, *, self_serve: bool = True) -> MeOut:
    """Cambio piano. Se l'utente è titolare di una famiglia e il nuovo piano ha
    meno account, la RPC retrocede/revoca automaticamente gli eccedenti.

    I piani «su richiesta» non sono attivabili self-serve: solo l'admin può
    assegnarli (self_serve=False) — è il completamento manuale del flusso.
    """
    if self_serve:
        plan_resp = (
            await primary.table("subscription_plans")
            .select("tipo_prezzo")
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )
        if plan_resp.data and plan_resp.data[0]["tipo_prezzo"] == "su_richiesta":
            raise BadRequestError(
                "Questo piano è disponibile solo su richiesta: contattaci per attivarlo"
            )
        # Piano inesistente: si prosegue, la RPC mantiene la sua mappatura errori.
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
                "subscription_plans(id,nome,slug,descrizione,prezzo_annuale,tipo_prezzo,"
                "etichetta_prezzo,ai_check,"
                "alert_attivo,alert_giorni_preavviso,alert_ritardo_giorni,num_account_aziendali,ordering,"
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

    # Codici dei progettisti (e degli admin, parità 0019) in pagina, batch.
    progettista_ids = [row["id"] for row in rows if row["role"] in ("progettista", "admin")]
    codici: dict[str, str] = {}
    if progettista_ids:
        prog_resp = (
            await primary.table("progettisti")
            .select("user_id,codice")
            .in_("user_id", progettista_ids)
            .execute()
        )
        codici = {r["user_id"]: r["codice"] for r in prog_resp.data}

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
        codice = codici.get(row["id"])
        items.append(
            AdminUserOut(
                profile=profile_from_row(row),
                subscription=subscription,
                family=family,
                progettista=ProgettistaOut(codice=codice) if codice else None,
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
        # Qualunque ruolo di destinazione diverso da admin è un auto-lockout.
        if changes.get("role") not in (None, "admin"):
            raise BadRequestError("Non puoi rimuovere il ruolo admin a te stesso")
        if changes.get("is_active") is False:
            raise BadRequestError("Non puoi disattivare il tuo stesso account")

    role_change = changes.get("role")

    # La promozione a progettista passa dalla RPC: serializza sul profilo,
    # assegna il codice identificativo (riusando quello di una promozione
    # precedente) e registra l'azione in audit_log.
    if role_change == "progettista":
        changes.pop("role")
        try:
            await primary.rpc(
                "fn_promote_progettista",
                {"p_user_id": str(target_user_id), "p_actor_id": str(acting_admin_id)},
            ).execute()
        except APIError as exc:
            family_service.raise_from_rpc(exc)

    if changes:
        resp = (
            await primary.table("profiles")
            .update(changes)
            .eq("id", str(target_user_id))
            .execute()
        )
        if not resp.data:
            raise NotFoundError("Utente non trovato")

    # I cambi di ruolo che non passano dalla RPC (demozioni, nomina admin)
    # sono comunque transizioni sensibili: best-effort, come gli altri audit.
    if role_change is not None and role_change != "progettista":
        try:
            await primary.table("audit_log").insert(
                {
                    "actor_id": str(acting_admin_id),
                    "action": "admin.role_changed",
                    "target_user_id": str(target_user_id),
                    "payload": {"role": role_change},
                }
            ).execute()
        except Exception:
            logger.warning("audit_log non scrivibile per admin.role_changed", exc_info=True)

    me = await get_me(primary, str(target_user_id))
    return AdminUserOut(
        profile=me.profile,
        subscription=me.subscription,
        family=me_family_to_admin(me),
        progettista=me.progettista,
    )


async def admin_switch_user_plan(primary, target_user_id: UUID, plan_id: int) -> AdminUserOut:
    exists = (
        await primary.table("profiles").select("id").eq("id", str(target_user_id)).limit(1).execute()
    )
    if not exists.data:
        raise NotFoundError("Utente non trovato")

    # I piani sono a livello famiglia: per un figlio ATTIVO si agisce sul
    # titolare. Un invitato pending è ancora un account indipendente con un
    # piano proprio, e un retrocesso ha il suo piano Gratuito: entrambi
    # restano gestibili singolarmente.
    membership = await family_service.get_membership(primary, str(target_user_id))
    if membership and membership["status"] == "active":
        raise ForbiddenError(
            "Il piano si gestisce sull'account titolare dell'azienda"
        )

    # self_serve=False: l'admin può assegnare anche i piani «su richiesta».
    me = await switch_plan(primary, str(target_user_id), plan_id, self_serve=False)
    return AdminUserOut(
        profile=me.profile,
        subscription=me.subscription,
        family=me_family_to_admin(me),
        progettista=me.progettista,
    )


def me_family_to_admin(me: MeOut) -> AdminFamilyInfo | None:
    if not me.family:
        return None
    if me.family.role == "parent":
        used = me.family.used or 1
        return AdminFamilyInfo(type="parent", members_count=max(0, used - 1))
    return AdminFamilyInfo(type="child", status=me.family.status)
