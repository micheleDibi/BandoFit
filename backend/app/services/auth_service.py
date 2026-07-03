"""Flussi auth con email inviate DAL BACKEND (SMTP/OVH), mai da Supabase.

I link firmati (conferma, recovery, invito) vengono generati con la Admin API
``generate_link`` — pensata proprio per «custom email provider» — e spediti
con i template di email_service. Il mailer di Supabase non viene mai attivato:
il frontend non chiama signUp/resetPasswordForEmail/resend.
"""

import logging
import time

from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError, UpstreamError
from app.services import email_service

logger = logging.getLogger("bandofit.auth")

# Anti-abuso: questi endpoint sono pubblici e fanno partire email reali.
# Cooldown in-process per destinatario (sufficiente per un singolo processo).
_COOLDOWN_SECONDS = 60
_last_sent: dict[tuple[str, str], float] = {}


def _check_cooldown(kind: str, email: str) -> None:
    now = time.monotonic()
    # pulizia opportunistica per non far crescere il dizionario
    if len(_last_sent) > 5000:
        cutoff = now - _COOLDOWN_SECONDS
        for key in [k for k, ts in _last_sent.items() if ts < cutoff]:
            _last_sent.pop(key, None)
    key = (kind, email)
    if now - _last_sent.get(key, 0.0) < _COOLDOWN_SECONDS:
        raise ConflictError("Email già inviata da poco: attendi un minuto e riprova")
    _last_sent[key] = now


def _redirect(path: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{path}"


async def register(
    primary,
    email: str,
    password: str,
    nome: str,
    cognome: str,
    azienda: str | None,
    plan_slug: str,
) -> dict:
    """Crea l'utente e invia l'email di conferma dal nostro SMTP.

    Ritorna {"confirmation_required": bool}: False se il progetto ha la
    conferma email disattivata (l'utente può accedere subito).
    """
    email = email.strip().lower()
    _check_cooldown("register", email)
    try:
        link = await primary.auth.admin.generate_link(
            {
                "type": "signup",
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "nome": nome,
                        "cognome": cognome,
                        "azienda": azienda or None,
                        "plan_slug": plan_slug,
                    },
                    "redirect_to": _redirect("/conferma-email"),
                },
            }
        )
    except Exception as exc:
        message = str(exc).lower()
        if "already" in message or "exists" in message or "registered" in message:
            raise ConflictError(
                "Esiste già un account con questa email. Prova ad accedere."
            ) from exc
        if "password" in message:
            raise BadRequestError("La password non rispetta i requisiti minimi") from exc
        logger.error("generate_link signup fallita per %s: %s", email, exc)
        raise UpstreamError("Registrazione non riuscita, riprova") from exc

    confirmation_required = link.user.email_confirmed_at is None
    if confirmation_required:
        email_sent = await email_service.send_confirmation_email(
            email, link.properties.action_link
        )
        if not email_sent:
            logger.error("Email di conferma non inviata a %s", email)
    return {"confirmation_required": confirmation_required}


async def recover_password(primary, email: str) -> None:
    """Invia il link di reimpostazione password. SEMPRE risposta neutra:
    non riveliamo se l'email esiste (anti-enumerazione)."""
    email = email.strip().lower()
    _check_cooldown("recover", email)
    try:
        link = await primary.auth.admin.generate_link(
            {
                "type": "recovery",
                "email": email,
                "options": {"redirect_to": _redirect("/reimposta-password")},
            }
        )
    except Exception as exc:
        # Utente inesistente o errore: logghiamo e basta, la risposta non cambia.
        logger.info("Recovery non generata per %s: %s", email, exc)
        return
    await email_service.send_recovery_email(email, link.properties.action_link)


async def resend_confirmation(primary, email: str) -> None:
    """Reinvia il link di conferma a un utente non ancora confermato.
    Risposta neutra in ogni caso. Il magiclink verificato conferma l'email."""
    email = email.strip().lower()
    _check_cooldown("confirm", email)
    try:
        link = await primary.auth.admin.generate_link(
            {
                "type": "magiclink",
                "email": email,
                "options": {"redirect_to": _redirect("/conferma-email")},
            }
        )
    except Exception as exc:
        logger.info("Reinvio conferma non generato per %s: %s", email, exc)
        return
    await email_service.send_confirmation_email(email, link.properties.action_link)
