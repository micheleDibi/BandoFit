import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import AsyncClient

from app.clients.anthropic_ai import AiCheckClient as _AiCheckClient
from app.clients.openapi import OpenapiClient as _OpenapiClient
from app.clients.revolut import RevolutClient as _RevolutClient
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import decode_supabase_jwt
from app.services.user_service import PROFILE_SELECT, ensure_profile

_bearer = HTTPBearer(auto_error=False)


def get_primary(request: Request) -> AsyncClient:
    return request.app.state.primary


def get_secondary(request: Request) -> AsyncClient:
    return request.app.state.secondary


def get_openapi(request: Request) -> _OpenapiClient:
    return request.app.state.openapi


def get_ai(request: Request) -> _AiCheckClient:
    return request.app.state.ai


def get_revolut(request: Request) -> _RevolutClient:
    return request.app.state.revolut


PrimaryClient = Annotated[AsyncClient, Depends(get_primary)]
SecondaryClient = Annotated[AsyncClient, Depends(get_secondary)]
OpenapiDep = Annotated[_OpenapiClient, Depends(get_openapi)]
AiDep = Annotated[_AiCheckClient, Depends(get_ai)]
RevolutDep = Annotated[_RevolutClient, Depends(get_revolut)]


async def get_current_user(
    primary: PrimaryClient,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """Verifica il JWT di Supabase e carica il profilo. Ritorna il profilo come dict."""
    if credentials is None:
        raise UnauthorizedError("Autenticazione richiesta")

    claims = await decode_supabase_jwt(credentials.credentials)
    user_id = claims.get("sub")
    if not user_id:
        raise UnauthorizedError("Token privo del soggetto")

    resp = (
        await primary.table("profiles")
        .select(PROFILE_SELECT)
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        # Il trigger di provisioning è difensivo e non solleva mai: in rari casi
        # (email nullable, errore transitorio) può restare un utente auth senza
        # profilo. Lo ripariamo al volo, così l'account non resta bloccato in un
        # loop di logout invece di aspettare un intervento manuale.
        profile = await ensure_profile(primary, user_id, claims)
    else:
        profile = resp.data[0]

    if not profile["is_active"]:
        raise ForbiddenError("Account disattivato. Contatta l'assistenza.")
    return profile


CurrentUser = Annotated[dict, Depends(get_current_user)]


@dataclass
class ActiveCompany:
    """L'azienda su cui opera la richiesta corrente.

    Per il piano Advisor (multi-azienda) l'azienda attiva arriva dall'header
    `X-Active-Company`; per tutti gli altri (una sola azienda) è quella del
    titolare. `owner_id`/`editable` vengono da `owner_and_editable` (un figlio
    attivo legge i dati della famiglia in sola lettura). `company_id` è None se
    il titolare non ha ancora alcuna azienda. `is_multi` = il piano gestisce
    più aziende (limite effettivo > 1): è il gate dell'overlay del Gruppo A
    (bandi salvati/calendario/preferenze). Per un non-Advisor è False e quelle
    righe restano a `company_profile_id NULL`, identiche a prima.
    """

    company_id: str | None
    owner_id: str
    editable: bool
    is_multi: bool = False


async def active_company(
    request: Request, user: CurrentUser, primary: PrimaryClient
) -> ActiveCompany:
    """Risolve l'azienda attiva della richiesta, ri-autorizzandola sempre.

    Titolare/pending/retrocesso: l'header `X-Active-Company` è onorato solo se
    punta a un'azienda VIVA propria; senza header, la viva più vecchia.
    Membro ATTIVO (0031): l'insieme utile è la sua VISIBILITÀ ∩ aziende vive
    dell'owner — header fuori insieme → 404 (non rivela l'esistenza); default =
    appartenenza se nell'insieme, altrimenti la più vecchia dell'insieme,
    altrimenti nessuna azienda. `is_multi` resta sul limite dell'OWNER: governa
    lo scoping dei dati (Gruppo A), non lo switcher del membro (che usa il flag
    child-aware di /me)."""
    from app.services import company_service, family_service  # import locale: evita cicli

    membership = await family_service.get_membership(primary, user["id"])
    if membership and membership["status"] == "active":
        owner_id, editable = str(membership["parent_id"]), False
    else:
        owner_id, editable = str(user["id"]), True
        membership = None
    is_multi = await company_service.effective_max_aziende(primary, owner_id) > 1

    header = (request.headers.get("X-Active-Company") or "").strip()
    if header:
        try:
            header = str(uuid.UUID(header))
        except ValueError:
            # id malformato = azienda inesistente per il chiamante (evita il
            # 22P02 → 502 di Postgres su un uuid non valido).
            raise NotFoundError("Azienda non disponibile") from None

    if membership is not None:
        appartenenza, vive = await family_service.visible_companies(primary, membership)
        ids = [c["id"] for c in vive]
        if header:
            if header not in ids:
                raise NotFoundError("Azienda non disponibile")
            company_id = header
        elif appartenenza in ids:
            company_id = appartenenza
        else:
            # Appartenenza archiviata/cancellata o mai assegnata: fallback
            # sulla più vecchia visibile — il membro non resta mai bloccato.
            company_id = ids[0] if ids else None
        return ActiveCompany(
            company_id=company_id, owner_id=owner_id,
            editable=editable, is_multi=is_multi,
        )

    if header:
        resp = (
            await primary.table("company_profiles")
            .select("id")
            .eq("id", header)
            .eq("parent_id", owner_id)
            .is_("deleted_at", "null")
            .is_("archived_at", "null")
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise NotFoundError("Azienda non disponibile")
        return ActiveCompany(
            company_id=str(resp.data[0]["id"]), owner_id=owner_id,
            editable=editable, is_multi=is_multi,
        )

    resp = (
        await primary.table("company_profiles")
        .select("id")
        .eq("parent_id", owner_id)
        .is_("deleted_at", "null")
        .is_("archived_at", "null")
        .order("created_at")
        .limit(1)
        .execute()
    )
    company_id = str(resp.data[0]["id"]) if resp.data else None
    return ActiveCompany(
        company_id=company_id, owner_id=owner_id, editable=editable, is_multi=is_multi,
    )


ActiveCompanyDep = Annotated[ActiveCompany, Depends(active_company)]


async def require_admin(user: CurrentUser) -> dict:
    if user["role"] != "admin":
        raise ForbiddenError("Riservato agli amministratori")
    return user


AdminUser = Annotated[dict, Depends(require_admin)]


async def require_progettista(user: CurrentUser) -> dict:
    # Parità admin ↔ progettista: gli amministratori hanno le stesse funzioni
    # dell'area progettista (decisione di prodotto, migration 0019).
    if user["role"] not in ("progettista", "admin"):
        raise ForbiddenError("Riservato ai progettisti")
    return user


ProgettistaUser = Annotated[dict, Depends(require_progettista)]


async def require_parent(user: CurrentUser, primary: PrimaryClient) -> dict:
    """L'utente non deve essere membro (corrente) di una famiglia altrui:
    solo il titolare gestisce gli account collegati e i dati aziendali."""
    from app.services import family_service  # import locale: evita cicli

    membership = await family_service.get_membership(primary, user["id"])
    if membership is not None:
        raise ForbiddenError(
            "Solo il titolare dell'azienda può gestire gli account collegati"
        )
    return user


ParentUser = Annotated[dict, Depends(require_parent)]


async def require_billing_account(user: CurrentUser, primary: PrimaryClient) -> dict:
    """Gate degli endpoint di acquisto/fatturazione: bloccati SOLO i figli
    ATTIVI di una famiglia (ereditano il piano del titolare — è la stessa
    regola di child_plan_locked in fn_apply_plan_change). I membri `pending` e
    `demoted` sono account indipendenti con piano proprio e POSSONO comprare:
    require_parent li escluderebbe a torto."""
    from app.services import family_service  # import locale: evita cicli

    membership = await family_service.get_membership(primary, user["id"])
    if membership is not None and membership.get("status") == "active":
        raise ForbiddenError(
            "Il piano e i pagamenti si gestiscono sull'account titolare"
        )
    return user


BillingAccount = Annotated[dict, Depends(require_billing_account)]
