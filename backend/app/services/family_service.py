"""Gestione delle famiglie di account: inviti, ciclo di vita dei membri,
risoluzione dell'abbonamento ereditato.

Tutte le mutazioni passano dalle funzioni SQL SECURITY DEFINER (atomiche e
serializzate con lock sul padre); questo modulo le orchestra, gestisce i due
flussi di invito (email nuova via Supabase Auth, email esistente via Resend)
e traduce i codici errore delle RPC in errori API.
"""

import logging
from typing import NoReturn

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.errors import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
)
from app.schemas.family import (
    FamilyMemberOut,
    FamilyOut,
    InvitationOut,
    InviteMemberOut,
    MeFamilyOut,
)
from app.services import email_service

logger = logging.getLogger("bandofit.family")

# Stati che occupano/riservano una riga "corrente" (l'indice unico li copre).
CURRENT_STATUSES = ["pending", "active", "demoted"]

MEMBER_SELECT = (
    "id,parent_id,member_id,denominazione,invited_email,invite_kind,"
    "status,invited_at,joined_at,demoted_at"
)

# codice detail della RPC -> (classe errore, messaggio per l'utente)
_RPC_ERRORS: dict[str, tuple[type[AppError], str]] = {
    "cannot_invite_self": (BadRequestError, "Non puoi invitare te stesso"),
    "not_family_parent": (ForbiddenError, "Il tuo piano non prevede account aggiuntivi"),
    "parent_in_family": (
        ForbiddenError,
        "Un account collegato a una famiglia non può crearne una propria",
    ),
    "parent_not_found": (NotFoundError, "Account titolare non trovato"),
    "user_not_found": (NotFoundError, "Utente non trovato"),
    "target_not_found": (NotFoundError, "Utente da invitare non trovato"),
    "target_is_admin": (ConflictError, "Non è possibile invitare un amministratore"),
    "target_is_parent": (ConflictError, "L'utente è già titolare di una famiglia"),
    "already_in_family": (ConflictError, "L'utente fa già parte di una famiglia"),
    "invite_already_pending": (ConflictError, "C'è già un invito in attesa per questa email"),
    "family_limit_reached": (
        ConflictError,
        "Hai raggiunto il numero massimo di account del tuo piano",
    ),
    "family_full": (ConflictError, "La famiglia ha raggiunto il numero massimo di account"),
    "invitation_not_found": (NotFoundError, "Invito non trovato o non più valido"),
    "member_not_found": (NotFoundError, "Account collegato non trovato"),
    "child_plan_locked": (
        ForbiddenError,
        "Il piano si gestisce sull'account titolare della famiglia",
    ),
    "plan_not_available": (BadRequestError, "Piano inesistente o non attivo"),
}


def raise_from_rpc(exc: APIError) -> NoReturn:
    """Traduce l'errore di una funzione SQL (detail = codice macchina) in AppError."""
    detail = (exc.details or "").strip()
    mapped = _RPC_ERRORS.get(detail)
    if mapped:
        error_cls, message = mapped
        raise error_cls(message) from exc
    logger.error("Errore RPC famiglia non mappato: code=%s detail=%s message=%s",
                 exc.code, exc.details, exc.message)
    raise UpstreamError() from exc


def map_member(row: dict) -> FamilyMemberOut:
    return FamilyMemberOut(
        id=row["id"],
        member_id=row["member_id"],
        denominazione=row["denominazione"],
        email=row["invited_email"],
        status=row["status"],
        invite_kind=row["invite_kind"],
        invited_at=row["invited_at"],
        joined_at=row.get("joined_at"),
        demoted_at=row.get("demoted_at"),
    )


async def get_membership(primary, user_id: str) -> dict | None:
    """Membership corrente dell'utente COME MEMBRO (None se non è in famiglia)."""
    resp = (
        await primary.table("family_members")
        .select(MEMBER_SELECT)
        .eq("member_id", user_id)
        .in_("status", CURRENT_STATUSES)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def parent_display_name(primary, parent_id: str) -> str:
    """Nome mostrato della famiglia: ragione sociale → azienda → nome cognome → email."""
    resp = (
        await primary.table("profiles")
        .select("nome,cognome,email,azienda,company_profiles(ragione_sociale)")
        .eq("id", parent_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return "il titolare"
    row = resp.data[0]
    company = row.get("company_profiles") or {}
    if isinstance(company, list):  # embed 1:1 può arrivare come lista
        company = company[0] if company else {}
    if company.get("ragione_sociale"):
        return company["ragione_sociale"]
    if row.get("azienda"):
        return row["azienda"]
    full_name = " ".join(filter(None, [row.get("nome"), row.get("cognome")]))
    return full_name or row.get("email") or "il titolare"


async def family_limit(primary, parent_id: str) -> int:
    resp = (
        await primary.table("user_subscriptions")
        .select("subscription_plans(num_account_aziendali)")
        .eq("user_id", parent_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not resp.data:
        return 0
    plan = resp.data[0].get("subscription_plans") or {}
    return plan.get("num_account_aziendali") or 0


async def get_family_overview(primary, parent_id: str) -> FamilyOut:
    members_resp = (
        await primary.table("family_members")
        .select(MEMBER_SELECT)
        .eq("parent_id", parent_id)
        .in_("status", CURRENT_STATUSES)
        .order("invited_at")
        .execute()
    )
    members = [map_member(row) for row in members_resp.data]
    used = 1 + sum(1 for m in members if m.status in ("pending", "active"))
    return FamilyOut(
        limit=await family_limit(primary, parent_id),
        used=used,
        members=members,
    )


async def _find_profile_by_email(primary, email: str) -> dict | None:
    resp = (
        await primary.table("profiles")
        .select("id,email,role,is_active")
        .ilike("email", email)  # senza wildcard = uguaglianza case-insensitive
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _create_membership_rpc(
    primary, parent_id: str, member_id: str, denominazione: str, email: str, kind: str
) -> None:
    try:
        await primary.rpc(
            "fn_create_family_member",
            {
                "p_parent_id": str(parent_id),
                "p_member_id": str(member_id),
                "p_denominazione": denominazione,
                "p_email": email,
                "p_kind": kind,
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)


async def invite_member(
    primary, parent_profile: dict, email: str, denominazione: str
) -> InviteMemberOut:
    settings = get_settings()
    email = email.strip().lower()
    parent_id = parent_profile["id"]
    email_sent = True

    existing = await _find_profile_by_email(primary, email)

    if existing:
        # Utente già registrato: prima la validazione atomica, poi l'email best-effort.
        await _create_membership_rpc(
            primary, parent_id, existing["id"], denominazione, email, "existing_user"
        )
        display_name = await parent_display_name(primary, parent_id)
        email_sent = await email_service.send_family_invitation_email(
            email, display_name, denominazione
        )
    else:
        # Email nuova: l'invito Supabase crea l'utente auth (il trigger crea il
        # profilo SENZA abbonamento grazie al metadata family_invite).
        try:
            invited = await primary.auth.admin.invite_user_by_email(
                email,
                {
                    "data": {
                        "family_invite": "true",
                        "parent_id": str(parent_id),
                        "denominazione": denominazione,
                    },
                    "redirect_to": f"{settings.frontend_url.rstrip('/')}/accetta-invito",
                },
            )
        except Exception as exc:  # AuthApiError e affini
            message = str(exc).lower()
            if "already" in message or "exists" in message:
                # Race: registrazione avvenuta tra il lookup e l'invito.
                registered = await _find_profile_by_email(primary, email)
                if registered:
                    return await invite_member(primary, parent_profile, email, denominazione)
            logger.error("Invito Supabase fallito per %s: %s", email, exc)
            raise UpstreamError("Invio dell'invito non riuscito, riprova") from exc

        member_id = invited.user.id
        # Upsert difensivo: se il trigger avesse inghiottito un errore, la RPC
        # fallirebbe sulla FK del profilo.
        await primary.table("profiles").upsert(
            {"id": str(member_id), "email": email, "nome": denominazione},
            on_conflict="id",
        ).execute()

        try:
            await _create_membership_rpc(
                primary, parent_id, member_id, denominazione, email, "new_user"
            )
        except AppError:
            # Compensazione: l'invito non è valido, liberiamo l'email.
            try:
                await primary.auth.admin.delete_user(str(member_id))
            except Exception as cleanup_exc:  # best-effort
                logger.error("Cleanup utente invitato %s fallito: %s", member_id, cleanup_exc)
            raise

    return InviteMemberOut(
        family=await get_family_overview(primary, parent_id),
        email_sent=email_sent,
    )


async def _get_parent_membership_row(primary, parent_id: str, membership_id: str) -> dict:
    resp = (
        await primary.table("family_members")
        .select(MEMBER_SELECT)
        .eq("id", str(membership_id))
        .eq("parent_id", str(parent_id))
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Account collegato non trovato")
    return resp.data[0]


async def resend_invite(primary, parent_profile: dict, membership_id: str) -> InviteMemberOut:
    settings = get_settings()
    parent_id = parent_profile["id"]
    row = await _get_parent_membership_row(primary, parent_id, membership_id)
    if row["status"] != "pending":
        raise ConflictError("L'invito non è più in attesa")

    email_sent = True
    display_name = await parent_display_name(primary, parent_id)

    if row["invite_kind"] == "existing_user":
        email_sent = await email_service.send_family_invitation_email(
            row["invited_email"], display_name, row["denominazione"]
        )
    else:
        # Utente creato dall'invito: GoTrue rifiuta un secondo /invite, quindi
        # si rigenera il link d'invito e lo si spedisce via Resend.
        redirect_to = f"{settings.frontend_url.rstrip('/')}/accetta-invito"
        try:
            await primary.auth.admin.invite_user_by_email(
                row["invited_email"], {"redirect_to": redirect_to}
            )
        except Exception:
            try:
                link_resp = await primary.auth.admin.generate_link(
                    {
                        "type": "invite",
                        "email": row["invited_email"],
                        "options": {"redirect_to": redirect_to},
                    }
                )
                action_link = link_resp.properties.action_link
                email_sent = await email_service.send_family_invitation_email(
                    row["invited_email"], display_name, row["denominazione"],
                    cta_url=action_link,
                )
            except Exception as exc:
                logger.error("Reinvio invito fallito per %s: %s", row["invited_email"], exc)
                raise UpstreamError("Reinvio dell'invito non riuscito, riprova") from exc

    await primary.table("audit_log").insert(
        {
            "actor_id": str(parent_id),
            "action": "family.invite_resent",
            "target_user_id": row["member_id"],
            "family_parent_id": str(parent_id),
            "payload": {"membership_id": str(membership_id), "email_sent": email_sent},
        }
    ).execute()

    return InviteMemberOut(
        family=await get_family_overview(primary, parent_id), email_sent=email_sent
    )


async def remove_member(primary, parent_profile: dict, membership_id: str) -> FamilyOut:
    parent_id = parent_profile["id"]
    try:
        resp = await primary.rpc(
            "fn_remove_family_member",
            {"p_parent_id": str(parent_id), "p_membership_id": str(membership_id)},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)

    result = resp.data or {}
    # Un invito a email nuova mai accettato lascia un utente auth senza password:
    # lo si elimina per liberare l'email (best-effort).
    if result.get("prior_status") == "pending" and result.get("invite_kind") == "new_user":
        try:
            await primary.auth.admin.delete_user(result["member_id"])
        except Exception as exc:
            logger.error("Cleanup utente invitato %s fallito: %s", result.get("member_id"), exc)

    return await get_family_overview(primary, parent_id)


async def reactivate_member(primary, parent_profile: dict, membership_id: str) -> FamilyOut:
    parent_id = parent_profile["id"]
    try:
        await primary.rpc(
            "fn_reactivate_family_member",
            {"p_parent_id": str(parent_id), "p_membership_id": str(membership_id)},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    return await get_family_overview(primary, parent_id)


async def list_my_invitations(primary, user_id: str) -> list[InvitationOut]:
    resp = (
        await primary.table("family_members")
        .select("id,denominazione,invited_at,parent_id")
        .eq("member_id", user_id)
        .eq("status", "pending")
        .order("invited_at", desc=True)
        .execute()
    )
    invitations = []
    for row in resp.data:
        invitations.append(
            InvitationOut(
                id=row["id"],
                denominazione=row["denominazione"],
                parent_display_name=await parent_display_name(primary, row["parent_id"]),
                invited_at=row["invited_at"],
            )
        )
    return invitations


async def accept_invitation(primary, user_id: str, membership_id: str) -> None:
    try:
        await primary.rpc(
            "fn_accept_invitation",
            {"p_membership_id": str(membership_id), "p_user_id": str(user_id)},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)


async def decline_invitation(primary, user_id: str, membership_id: str) -> None:
    try:
        await primary.rpc(
            "fn_decline_invitation",
            {"p_membership_id": str(membership_id), "p_user_id": str(user_id)},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)


async def cleanup_revoked_new_users(primary, adjustment: dict) -> None:
    """Dopo un downgrade: elimina gli utenti auth degli inviti 'new_user' revocati
    (non hanno mai impostato una password; si libera l'email)."""
    for revoked in adjustment.get("revoked_pending", []):
        if revoked.get("invite_kind") == "new_user":
            try:
                await primary.auth.admin.delete_user(revoked["member_id"])
            except Exception as exc:
                logger.error(
                    "Cleanup utente invitato %s fallito: %s", revoked.get("member_id"), exc
                )


async def build_me_family(
    primary,
    user_id: str,
    own_plan_limit: int | None,
    membership: dict | None = None,
) -> MeFamilyOut | None:
    """Contesto famiglia per GET /me. None se l'utente non c'entra con le famiglie.
    ``membership`` può essere passata dal chiamante per evitare una query doppia."""
    if membership is None:
        membership = await get_membership(primary, user_id)
    if membership:
        return MeFamilyOut(
            role="child",
            status=membership["status"],
            denominazione=membership["denominazione"],
            parent_display_name=await parent_display_name(primary, membership["parent_id"]),
        )

    members_resp = (
        await primary.table("family_members")
        .select("status", count="exact")
        .eq("parent_id", user_id)
        .in_("status", CURRENT_STATUSES)
        .execute()
    )
    member_rows = members_resp.data or []
    limit = own_plan_limit or 0
    if limit <= 1 and not member_rows:
        return None
    used = 1 + sum(1 for row in member_rows if row["status"] in ("pending", "active"))
    return MeFamilyOut(role="parent", limit=limit, used=used)
