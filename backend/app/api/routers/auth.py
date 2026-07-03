from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import PrimaryClient
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    nome: str = Field(min_length=1, max_length=100)
    cognome: str = Field(min_length=1, max_length=100)
    azienda: str | None = Field(default=None, max_length=200)
    plan_slug: str = Field(default="gratuito", max_length=100)


class RegisterOut(BaseModel):
    confirmation_required: bool


class EmailIn(BaseModel):
    email: EmailStr


@router.post("/register", response_model=RegisterOut, status_code=201)
async def register(data: RegisterIn, primary: PrimaryClient) -> RegisterOut:
    """Registrazione con email di conferma inviata dal NOSTRO provider
    (mai dal mailer di Supabase)."""
    result = await auth_service.register(
        primary,
        email=str(data.email),
        password=data.password,
        nome=data.nome.strip(),
        cognome=data.cognome.strip(),
        azienda=(data.azienda or "").strip() or None,
        plan_slug=data.plan_slug,
    )
    return RegisterOut(**result)


@router.post("/recover", status_code=202)
async def recover(data: EmailIn, primary: PrimaryClient) -> dict:
    """Richiesta di reimpostazione password. Risposta sempre neutra."""
    await auth_service.recover_password(primary, str(data.email))
    return {"ok": True}


@router.post("/resend-confirmation", status_code=202)
async def resend_confirmation(data: EmailIn, primary: PrimaryClient) -> dict:
    """Reinvio del link di conferma email. Risposta sempre neutra."""
    await auth_service.resend_confirmation(primary, str(data.email))
    return {"ok": True}
