"""Token monouso per i link email di dominio (conferma, recovery, inviti).

Il token in chiaro (256 bit url-safe) viaggia SOLO nel link email; a riposo
esiste unicamente il suo SHA-256. Il consumo è atomico e monouso: un UPDATE
condizionato su ``used_at IS NULL`` fa da lock ottimistico, quindi due click
concorrenti sullo stesso link non possono consumarlo entrambi.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

TokenPurpose = Literal["confirm_email", "recovery", "invite"]

# TTL per scopo: il recovery è volutamente corto.
TTL: dict[TokenPurpose, timedelta] = {
    "confirm_email": timedelta(hours=48),
    "recovery": timedelta(hours=1),
    "invite": timedelta(hours=48),
}

# token_urlsafe(32) produce 43 caratteri base64url.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def is_well_formed(token: str) -> bool:
    """Filtro economico prima di toccare il DB (evita query su input spazzatura)."""
    return bool(_TOKEN_RE.fullmatch(token))


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def issue(primary, user_id: str, purpose: TokenPurpose) -> str:
    """Emette un nuovo token per utente+scopo, invalidando i precedenti
    (un solo link valido alla volta). Ritorna il token in chiaro."""
    token = secrets.token_urlsafe(32)

    await (
        primary.table("auth_tokens")
        .update({"used_at": _now().isoformat()})
        .eq("user_id", str(user_id))
        .eq("purpose", purpose)
        .is_("used_at", "null")
        .execute()
    )

    await (
        primary.table("auth_tokens")
        .insert(
            {
                "user_id": str(user_id),
                "purpose": purpose,
                "token_hash": hash_token(token),
                "expires_at": (_now() + TTL[purpose]).isoformat(),
            }
        )
        .execute()
    )
    return token


async def consume(primary, token: str, purpose: TokenPurpose) -> str | None:
    """Consuma il token (monouso, atomico). Ritorna lo user_id o None se il
    token è inesistente, di scopo diverso, scaduto o già usato."""
    if not is_well_formed(token):
        return None
    resp = (
        await primary.table("auth_tokens")
        .update({"used_at": _now().isoformat()})
        .eq("token_hash", hash_token(token))
        .eq("purpose", purpose)
        .is_("used_at", "null")
        .gt("expires_at", _now().isoformat())
        .execute()
    )
    if not resp.data:
        return None
    return resp.data[0]["user_id"]


async def peek(primary, token: str, purpose: TokenPurpose) -> str | None:
    """Valida il token SENZA consumarlo (per pagine che mostrano il contesto
    prima dell'azione, es. l'invito). Ritorna lo user_id o None."""
    if not is_well_formed(token):
        return None
    resp = (
        await primary.table("auth_tokens")
        .select("user_id")
        .eq("token_hash", hash_token(token))
        .eq("purpose", purpose)
        .is_("used_at", "null")
        .gt("expires_at", _now().isoformat())
        .limit(1)
        .execute()
    )
    return resp.data[0]["user_id"] if resp.data else None
