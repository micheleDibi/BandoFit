"""Verifica dei JWT emessi da Supabase Auth (progetto primario).

Doppio binario:
- ES256/RS256 (progetti recenti): chiavi pubbliche dal JWKS del progetto, con cache.
- HS256 (legacy): segreto condiviso ``PRIMARY_SUPABASE_JWT_SECRET``.
"""

import asyncio
import logging
import threading

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from app.core.config import Settings, get_settings
from app.core.errors import AppError, UnauthorizedError

logger = logging.getLogger("bandofit.auth")


class AuthUnavailableError(AppError):
    """Il servizio di verifica token (JWKS) è temporaneamente irraggiungibile."""

    def __init__(self, message: str = "Servizio di autenticazione non disponibile, riprova"):
        super().__init__(503, "auth_unavailable", message)

_ALLOWED_ASYMMETRIC = ("ES256", "RS256")
_AUDIENCE = "authenticated"

_jwks_client: PyJWKClient | None = None
_jwks_lock = threading.Lock()


def _get_jwks_client(settings: Settings) -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        with _jwks_lock:
            if _jwks_client is None:
                _jwks_client = PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=3600)
    return _jwks_client


def _decode(token: str, settings: Settings) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Token malformato") from exc

    alg = header.get("alg")
    options = {"require": ["exp", "sub"]}

    try:
        if alg in _ALLOWED_ASYMMETRIC:
            # Un guasto di rete verso il JWKS non è colpa del token: va
            # distinto (503) da una firma non valida (401), altrimenti un
            # blip di rete disconnetterebbe tutti gli utenti.
            try:
                signing_key = _get_jwks_client(settings).get_signing_key_from_jwt(token)
            except PyJWKClientError as exc:
                logger.error("Recupero JWKS fallito: %s", exc)
                raise AuthUnavailableError() from exc
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=list(_ALLOWED_ASYMMETRIC),
                audience=_AUDIENCE,
                issuer=settings.jwt_issuer,
                options=options,
            )
        if alg == "HS256":
            if not settings.primary_supabase_jwt_secret:
                # Configurazione errata lato server: non esporre il dettaglio.
                logger.error("Token HS256 ricevuto ma PRIMARY_SUPABASE_JWT_SECRET non configurato")
                raise AuthUnavailableError()
            return jwt.decode(
                token,
                settings.primary_supabase_jwt_secret,
                algorithms=["HS256"],
                audience=_AUDIENCE,
                issuer=settings.jwt_issuer,
                options=options,
            )
    except AppError:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Sessione scaduta, effettua di nuovo il login") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Token non valido") from exc

    raise UnauthorizedError("Algoritmo di firma non supportato")


async def decode_supabase_jwt(token: str) -> dict:
    """Decodifica e verifica il token; ritorna i claim. Solleva UnauthorizedError se non valido.

    Eseguita in un thread: il primo accesso al JWKS fa una chiamata HTTP sincrona.
    """
    settings = get_settings()
    return await asyncio.to_thread(_decode, token, settings)
