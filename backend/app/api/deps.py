from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import AsyncClient

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_supabase_jwt
from app.services.user_service import PROFILE_SELECT, ensure_profile

_bearer = HTTPBearer(auto_error=False)


def get_primary(request: Request) -> AsyncClient:
    return request.app.state.primary


def get_secondary(request: Request) -> AsyncClient:
    return request.app.state.secondary


PrimaryClient = Annotated[AsyncClient, Depends(get_primary)]
SecondaryClient = Annotated[AsyncClient, Depends(get_secondary)]


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


async def require_admin(user: CurrentUser) -> dict:
    if user["role"] != "admin":
        raise ForbiddenError("Riservato agli amministratori")
    return user


AdminUser = Annotated[dict, Depends(require_admin)]


async def require_parent(user: CurrentUser, primary: PrimaryClient) -> dict:
    """L'utente non deve essere membro (corrente) di una famiglia altrui:
    solo il titolare gestisce gli account collegati e i dati aziendali."""
    from app.services import family_service  # import locale: evita cicli

    membership = await family_service.get_membership(primary, user["id"])
    if membership is not None:
        raise ForbiddenError(
            "Solo il titolare della famiglia può gestire gli account collegati"
        )
    return user


ParentUser = Annotated[dict, Depends(require_parent)]
