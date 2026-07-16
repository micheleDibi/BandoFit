from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.api.deps import PrimaryClient
from app.core.net import client_ip
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    # Nessuna password: si sceglie confermando l'indirizzo (POST /auth/confirm).
    # È il fulcro dell'anti-enumerazione, non una preferenza di UX — vedi la
    # docstring di app/services/auth_service.
    email: EmailStr
    nome: str = Field(min_length=1, max_length=100)
    cognome: str = Field(min_length=1, max_length=100)
    azienda: str | None = Field(default=None, max_length=200)
    telefono: str = Field(min_length=1, max_length=50)
    job_position_slug: str = Field(min_length=1, max_length=100)
    # Testo libero abbinato alla posizione «Altro» (ignorato per le altre).
    job_position_altro: str | None = Field(default=None, max_length=100)
    plan_slug: str = Field(default="gratuito", max_length=100)

    @field_validator("telefono")
    @classmethod
    def check_telefono(cls, value: str) -> str:
        from app.services.telefono import is_valid_telefono, normalize_telefono

        normalized = normalize_telefono(value)
        if not is_valid_telefono(normalized):
            raise ValueError("Il numero di telefono non è valido")
        return normalized


class EmailIn(BaseModel):
    email: EmailStr


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=128)


class TokenPasswordIn(TokenIn):
    password: str = Field(min_length=8, max_length=200)


class EmailOut(BaseModel):
    email: str


class InviteInfoOut(BaseModel):
    email: str
    denominazione: str
    parent_display_name: str


@router.post("/register", status_code=202)
async def register(data: RegisterIn, request: Request, primary: PrimaryClient) -> dict:
    """Avvia la registrazione. Risposta **sempre neutra** `{"ok": true}`: non
    riveliamo se l'indirizzo è già registrato (anti-enumerazione), come
    /auth/recover e /auth/resend-confirmation.

    Nessun `response_model`, come gli altri due endpoint neutri: la risposta è
    una costante, non la proiezione di un risultato.
    """
    return await auth_service.register(
        primary,
        email=str(data.email),
        nome=data.nome.strip(),
        cognome=data.cognome.strip(),
        azienda=(data.azienda or "").strip() or None,
        telefono=data.telefono,
        job_position_slug=data.job_position_slug.strip(),
        job_position_altro=(data.job_position_altro or "").strip() or None,
        plan_slug=data.plan_slug,
        client_ip=client_ip(request),
    )


@router.post("/confirm", response_model=EmailOut)
async def confirm_email(data: TokenPasswordIn, primary: PrimaryClient) -> EmailOut:
    """Completa la registrazione: consuma il token del link, imposta la
    password scelta ora e conferma l'indirizzo."""
    return EmailOut(**await auth_service.confirm_email(primary, data.token, data.password))


@router.post("/recover", status_code=202)
async def recover(data: EmailIn, request: Request, primary: PrimaryClient) -> dict:
    """Richiesta di reimpostazione password. Risposta sempre neutra."""
    await auth_service.recover_password(primary, str(data.email), client_ip=client_ip(request))
    return {"ok": True}


@router.post("/reset", response_model=EmailOut)
async def reset_password(data: TokenPasswordIn, primary: PrimaryClient) -> EmailOut:
    """Imposta la nuova password (consuma il token di recovery)."""
    return EmailOut(**await auth_service.reset_password(primary, data.token, data.password))


@router.post("/resend-confirmation", status_code=202)
async def resend_confirmation(data: EmailIn, request: Request, primary: PrimaryClient) -> dict:
    """Reinvio del link di conferma email. Risposta sempre neutra."""
    await auth_service.resend_confirmation(primary, str(data.email), client_ip=client_ip(request))
    return {"ok": True}


@router.get("/invite-info", response_model=InviteInfoOut)
async def invite_info(
    primary: PrimaryClient,
    token: str = Query(min_length=20, max_length=128),
) -> InviteInfoOut:
    """Contesto dell'invito azienda per la pagina di accettazione
    (non consuma il token)."""
    return InviteInfoOut(**await auth_service.invite_info(primary, token))


@router.post("/accept-invite", response_model=EmailOut)
async def accept_invite(data: TokenPasswordIn, primary: PrimaryClient) -> EmailOut:
    """Attiva un account invitato: password + conferma email + ingresso
    nell'azienda (consuma il token d'invito)."""
    return EmailOut(**await auth_service.accept_invite(primary, data.token, data.password))
