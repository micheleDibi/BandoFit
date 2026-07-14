"""Flussi auth con link email 100% di dominio.

Supabase è solo il deposito: gli utenti vengono creati/aggiornati via Admin
API (create_user / update_user_by_id) e NESSUN link viene generato da GoTrue.
I link nelle email portano token nostri (vedi token_service), verificati dal
backend; l'effetto (conferma email, cambio password) viene applicato via
Admin API. Il mailer di Supabase non viene mai attivato.
"""

import logging
import time

from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError, NotFoundError, UpstreamError
from app.services import email_service, job_position_service, token_service

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


def _link(path: str, token: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{path}?token={token}"


async def _find_profile_by_email(primary, email: str) -> dict | None:
    resp = (
        await primary.table("profiles")
        .select("id,email")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def register(
    primary,
    email: str,
    password: str,
    nome: str,
    cognome: str,
    azienda: str | None,
    telefono: str,
    job_position_slug: str,
    job_position_altro: str | None,
    plan_slug: str,
) -> dict:
    """Crea l'utente (Admin API, nessun link GoTrue) e invia l'email di
    conferma col nostro token dal nostro provider."""
    email = email.strip().lower()

    # I piani «su richiesta» non sono selezionabili alla registrazione (la UI
    # non li propone; questo è il backstop). PRIMA del cooldown: un tentativo
    # respinto non deve bruciare i 60 secondi della registrazione corretta.
    plan_resp = (
        await primary.table("subscription_plans")
        .select("tipo_prezzo")
        .eq("slug", plan_slug)
        .limit(1)
        .execute()
    )
    if plan_resp.data and plan_resp.data[0]["tipo_prezzo"] == "su_richiesta":
        raise BadRequestError(
            "Questo piano è disponibile solo su richiesta: registrati con un "
            "altro piano e contattaci dalla pagina Abbonamento"
        )
    # Slug inesistente o disattivato: comportamento invariato, il trigger
    # handle_new_user ripiega sul piano Gratuito.

    # La posizione viene da una lookup: uno slug ignoto o disattivato è un
    # client fuori sincrono col catalogo. Anche questo PRIMA del cooldown.
    position = await job_position_service.get_active_by_slug(primary, job_position_slug)
    if position is None:
        raise BadRequestError(
            "La posizione selezionata non è più disponibile: "
            "ricarica la pagina e riprova"
        )
    if position["slug"] != job_position_service.SLUG_ALTRO:
        job_position_altro = None

    _check_cooldown("register", email)
    try:
        created = await primary.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": False,
                "user_metadata": {
                    "nome": nome,
                    "cognome": cognome,
                    "azienda": azienda or None,
                    "telefono": telefono,
                    "job_position_slug": job_position_slug,
                    "job_position_altro": job_position_altro,
                    "plan_slug": plan_slug,
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
        logger.error("create_user fallita per %s: %s", email, exc)
        raise UpstreamError("Registrazione non riuscita, riprova") from exc

    user_id = str(created.user.id)
    token = await token_service.issue(primary, user_id, "confirm_email")
    sent = await email_service.send_confirmation_email(email, _link("/conferma-email", token))
    if not sent:
        logger.error("Email di conferma non inviata a %s", email)
    return {"confirmation_required": True}


async def confirm_email(primary, token: str) -> dict:
    """Applica la conferma indirizzo: consuma il token e sblocca il login."""
    user_id = await token_service.consume(primary, token, "confirm_email")
    if user_id is None:
        raise NotFoundError("Link di conferma non valido o scaduto")
    updated = await primary.auth.admin.update_user_by_id(user_id, {"email_confirm": True})
    return {"email": updated.user.email}


async def recover_password(primary, email: str) -> None:
    """Invia il link di reimpostazione password (token nostro). SEMPRE
    risposta neutra: non riveliamo se l'email esiste (anti-enumerazione)."""
    email = email.strip().lower()
    _check_cooldown("recover", email)
    profile = await _find_profile_by_email(primary, email)
    if profile is None:
        logger.warning("Recovery richiesta per email non registrata: %s", email)
        return
    token = await token_service.issue(primary, profile["id"], "recovery")
    await email_service.send_recovery_email(email, _link("/reimposta-password", token))


async def reset_password(primary, token: str, password: str) -> dict:
    """Consuma il token di recovery e imposta la nuova password via Admin API."""
    user_id = await token_service.consume(primary, token, "recovery")
    if user_id is None:
        raise NotFoundError("Link di reimpostazione non valido o scaduto")
    try:
        updated = await primary.auth.admin.update_user_by_id(user_id, {"password": password})
    except Exception as exc:
        if "password" in str(exc).lower():
            raise BadRequestError("La password non rispetta i requisiti minimi") from exc
        logger.error("Reset password fallito per %s: %s", user_id, exc)
        raise UpstreamError("Aggiornamento non riuscito, riprova") from exc
    return {"email": updated.user.email}


async def resend_confirmation(primary, email: str) -> None:
    """Reinvia il link di conferma a un utente non ancora confermato.
    Risposta neutra in ogni caso."""
    email = email.strip().lower()
    _check_cooldown("confirm", email)
    profile = await _find_profile_by_email(primary, email)
    if profile is None:
        logger.warning("Reinvio conferma per email non registrata: %s", email)
        return
    try:
        user = (await primary.auth.admin.get_user_by_id(profile["id"])).user
    except Exception as exc:
        logger.warning("Reinvio conferma: utente auth %s non leggibile: %s", profile["id"], exc)
        return
    if user is None or user.email_confirmed_at is not None:
        return  # già confermato: nulla da reinviare
    token = await token_service.issue(primary, profile["id"], "confirm_email")
    await email_service.send_confirmation_email(email, _link("/conferma-email", token))


async def invite_info(primary, token: str) -> dict:
    """Contesto dell'invito per la pagina /accetta-invito (NON consuma il token)."""
    from app.services import family_service  # import locale: evita cicli

    user_id = await token_service.peek(primary, token, "invite")
    if user_id is None:
        raise NotFoundError("Invito non valido o scaduto")
    membership = await family_service.get_membership(primary, user_id)
    if membership is None or membership["status"] != "pending":
        raise NotFoundError("Invito non più valido")
    return {
        "email": membership["invited_email"],
        "denominazione": membership["denominazione"],
        "parent_display_name": await family_service.parent_display_name(
            primary, membership["parent_id"]
        ),
    }


async def accept_invite(primary, token: str, password: str) -> dict:
    """Consuma il token d'invito: imposta la password, conferma l'email e
    attiva la membership. Ritorna l'email per l'auto-login del frontend."""
    from app.services import family_service  # import locale: evita cicli

    user_id = await token_service.consume(primary, token, "invite")
    if user_id is None:
        raise NotFoundError("Invito non valido o scaduto")

    membership = await family_service.get_membership(primary, user_id)
    if membership is None or membership["status"] != "pending":
        raise NotFoundError("Invito non più valido")

    try:
        updated = await primary.auth.admin.update_user_by_id(
            user_id, {"password": password, "email_confirm": True}
        )
    except Exception as exc:
        if "password" in str(exc).lower():
            raise BadRequestError("La password non rispetta i requisiti minimi") from exc
        logger.error("Attivazione invitato %s fallita: %s", user_id, exc)
        raise UpstreamError("Attivazione non riuscita, riprova") from exc

    await family_service.accept_invitation(primary, user_id, membership["id"])
    return {"email": updated.user.email}
