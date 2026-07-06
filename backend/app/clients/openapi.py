"""Client per il marketplace openapi.it (Openapi SpA) — dati aziendali e verifiche.

Meccanica della piattaforma (verificata sul campo, vedi tests/fixtures/openapi/):

- **Token**: ``POST {oauth}/token`` con HTTP Basic (email + API key) e body
  ``{"scopes": [...], "ttl": ...}``. ATTENZIONE: la risposta NON usa l'envelope
  standard — ``token``, ``expire`` (timestamp Unix) e ``scopes`` sono a livello
  radice. Il token vale per i soli scope richiesti.
- **Envelope** delle API prodotto: ``{"data": ..., "success": bool, "message",
  "error"}``.
- **IT-full è asincrono quando il dato non è in cache**: la prima GET risponde
  302 con ``data = {"state": "PENDING", "id": ...}``; si fa polling su
  ``GET /IT-check_id/{id}`` (endpoint GRATUITO) finché non arriva il payload.
- **Verifica CF** (``risk``): sincrona, ``data = {"validita": bool}``.

Regole di spesa: le chiamate COSTANO. Retry solo quando la richiesta non è
mai partita (errori di connessione); mai su ReadTimeout/5xx — l'esito resta
ignoto e la decisione di riprovare spetta all'utente. Su 401 il token viene
rigenerato una sola volta (mint gratuito, la richiesta respinta non è fatturata).
"""

import asyncio
import logging
import time

import httpx

from app.core.config import Settings
from app.core.errors import (
    OpenapiNotConfiguredError,
    OpenapiTimeoutError,
    OpenapiUpstreamError,
)

logger = logging.getLogger("bandofit.openapi")

_HOSTS = {
    "production": {
        "oauth": "https://oauth.openapi.it",
        "company": "https://company.openapi.com",
        "risk": "https://risk.openapi.com",
    },
    "sandbox": {
        "oauth": "https://test.oauth.openapi.it",
        "company": "https://test.company.openapi.com",
        "risk": "https://test.risk.openapi.com",
    },
}

_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # mint gratuito: token brevi, rigenerati al volo
_TOKEN_EXPIRY_MARGIN = 300
_POLL_INTERVAL_SECONDS = 3.0
_POLL_MAX_ATTEMPTS = 25
# Durata massima complessiva di it_full (prima chiamata + polling): DEVE
# restare sotto il TTL del lock di import (300s), o un import concorrente
# potrebbe partire mentre questo è ancora in corso e pagare due volte.
_TOTAL_DEADLINE_SECONDS = 240.0
_PENDING_STATES = {"PENDING", "IN_PROGRESS", "RUNNING"}


def _mask_url(url: str) -> str:
    """URL per i log con l'identificativo finale mascherato: i path contengono
    P.IVA o CODICI FISCALI (dato personale) e non devono finire nei log."""
    base, _, last = url.rpartition("/")
    if not base or not last:
        return url
    return f"{base}/***"


class OpenapiInvalidIdError(Exception):
    """L'identificativo richiesto (P.IVA/CF) è stato rifiutato dal provider
    (HTTP 406, error 222 "cf/piva not valid"). Il chiamante decide il codice
    HTTP appropriato in base al contesto."""


class OpenapiClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._email = settings.openapi_email
        self._api_key = settings.openapi_api_key
        env = "production" if settings.openapi_env == "production" else "sandbox"
        self.env = env
        self._hosts = _HOSTS[env]
        self._http = http or httpx.AsyncClient(timeout=settings.openapi_timeout_seconds)
        self._token: str | None = None
        self._token_expire: float = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._email and self._api_key)

    @property
    def sandbox(self) -> bool:
        return self.env != "production"

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ token

    def _scopes(self) -> list[str]:
        company = self._hosts["company"].removeprefix("https://")
        risk = self._hosts["risk"].removeprefix("https://")
        return [
            f"GET:{company}/IT-full",
            f"GET:{company}/IT-check_id",
            f"GET:{risk}/IT-verifica_cf",
        ]

    async def _mint_token(self) -> None:
        try:
            resp = await self._http.post(
                f"{self._hosts['oauth']}/token",
                auth=(self._email, self._api_key),
                json={"scopes": self._scopes(), "ttl": _TOKEN_TTL_SECONDS},
            )
            body = resp.json()
        except httpx.HTTPError as exc:
            logger.error("openapi: mint token fallito (%s)", exc)
            raise OpenapiUpstreamError() from exc
        # Risposta NON-envelope: token/expire a livello radice.
        token = body.get("token")
        if not body.get("success") or not token:
            logger.error(
                "openapi: mint token rifiutato: %s (error=%s)",
                body.get("message"), body.get("error"),
            )
            raise OpenapiUpstreamError()
        self._token = token
        self._token_expire = float(body.get("expire") or (time.time() + 3600))
        logger.info("openapi: nuovo token emesso (env=%s)", self.env)

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token is None or time.time() > self._token_expire - _TOKEN_EXPIRY_MARGIN:
                await self._mint_token()
            return self._token  # type: ignore[return-value]

    # --------------------------------------------------------------- requests

    async def _get(self, url: str, *, _retry_auth: bool = True) -> tuple[int, dict]:
        """GET autenticata con gestione envelope. Ritorna (status, body).

        Retry SOLO su errori di connessione (richiesta mai partita, non
        fatturata) e su 401 (token scaduto: re-mint, la respinta non è
        fatturata). ReadTimeout e 5xx NON vengono ritentati: potrebbero
        essere già stati addebitati.
        """
        if not self.enabled:
            raise OpenapiNotConfiguredError()
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = await self._http.get(url, headers=headers)
        except httpx.ConnectError:
            logger.warning(
                "openapi: errore di connessione, ritento una volta (%s)", _mask_url(url)
            )
            try:
                resp = await self._http.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise OpenapiUpstreamError() from exc
        except httpx.TimeoutException as exc:
            # Esito ignoto (possibile addebito): nessun retry automatico.
            logger.error("openapi: timeout su %s", _mask_url(url))
            raise OpenapiTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise OpenapiUpstreamError() from exc

        if resp.status_code == 401 and _retry_auth:
            async with self._token_lock:
                self._token = None
            return await self._get(url, _retry_auth=False)

        try:
            body = resp.json()
        except ValueError as exc:
            logger.error(
                "openapi: risposta non JSON da %s (HTTP %s)", _mask_url(url), resp.status_code
            )
            raise OpenapiUpstreamError() from exc
        return resp.status_code, body

    @staticmethod
    def _check_envelope(status: int, body: dict, url: str) -> dict | None:
        """Valida l'envelope; ritorna ``data`` oppure solleva. None mai ritornato
        su success (data può però essere un dict vuoto)."""
        if body.get("success"):
            return body.get("data")
        message = str(body.get("message") or "")
        if status == 406 or body.get("error") == 222:
            raise OpenapiInvalidIdError(message)
        logger.error(
            "openapi: errore da %s: HTTP %s, message=%r, error=%s",
            _mask_url(url), status, message, body.get("error"),
        )
        raise OpenapiUpstreamError()

    # --------------------------------------------------------------- products

    async def it_full(self, piva: str) -> dict:
        """Visura completa IT-full. Gestisce il flusso asincrono: se il dato non
        è in cache al provider, la prima risposta è PENDING e si fa polling
        sull'endpoint gratuito IT-check_id — con deadline complessiva sotto il
        TTL del lock di import."""
        started = time.monotonic()
        url = f"{self._hosts['company']}/IT-full/{piva}"
        status, body = await self._get(url)
        data = self._check_envelope(status, body, url)

        attempts = 0
        while isinstance(data, dict) and str(data.get("state", "")).upper() in _PENDING_STATES:
            request_id = data.get("id")
            if not request_id:
                logger.error("openapi: risposta PENDING senza id da %s", _mask_url(url))
                raise OpenapiUpstreamError()
            attempts += 1
            if (
                attempts > _POLL_MAX_ATTEMPTS
                or time.monotonic() - started > _TOTAL_DEADLINE_SECONDS
            ):
                logger.error(
                    "openapi: IT-full ancora PENDING dopo %s tentativi (%.0fs)",
                    attempts - 1, time.monotonic() - started,
                )
                raise OpenapiTimeoutError()
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            poll_url = f"{self._hosts['company']}/IT-check_id/{request_id}"
            status, body = await self._get(poll_url)
            data = self._check_envelope(status, body, poll_url)

        if not isinstance(data, dict):
            logger.error("openapi: payload IT-full inatteso (%r)", type(data).__name__)
            raise OpenapiUpstreamError()
        return data

    async def verifica_cf(self, codice_fiscale: str) -> bool:
        """Verifica del codice fiscale all'Anagrafe Tributaria (sincrona)."""
        url = f"{self._hosts['risk']}/IT-verifica_cf/{codice_fiscale}"
        status, body = await self._get(url)
        data = self._check_envelope(status, body, url)
        if not isinstance(data, dict) or "validita" not in data:
            logger.error("openapi: payload verifica_cf inatteso: %r", data)
            raise OpenapiUpstreamError()
        return bool(data["validita"])
