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

- **IT-advanced**: sincrona, ``data`` è un ARRAY di un elemento (storico dei
  bilanci fino a ~7 anni); 204 o 404/305 = nessun dato per quell'impresa.

Token per GRUPPO di scope: ``core`` (i prodotti storici, scope invariati),
``advanced`` (IT-advanced) e ``visure`` (bilancio-ottico e impresa, WP2). Un
prodotto non attivato in console fa fallire solo il mint del suo gruppo:
IT-full resta al riparo.

Regole di spesa: le chiamate COSTANO. Retry solo quando la richiesta non è
mai partita (errori di connessione); mai su ReadTimeout/5xx — l'esito resta
ignoto e la decisione di riprovare spetta all'utente. Su 401 il token del
gruppo viene rigenerato una sola volta (mint gratuito, la richiesta respinta
non è fatturata). ``OpenapiNonInviataError`` distingue le richieste che
certamente non sono partite (mint fallito, connessione rifiutata anche al
ritento): solo per queste un rimborso all'utente è sicuro.
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
        "visure": "https://visurecamerali.openapi.it",
    },
    "sandbox": {
        "oauth": "https://test.oauth.openapi.it",
        "company": "https://test.company.openapi.com",
        "risk": "https://test.risk.openapi.com",
        "visure": "https://test.visurecamerali.openapi.it",
    },
}

# Gruppi di scope con token separati (vedi docstring del modulo).
GRUPPI_TOKEN = ("core", "advanced", "visure")

_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # mint gratuito: token brevi, rigenerati al volo
# Timeout dedicato del VIES: la chiamata sta nel PUT interattivo
# dell'anagrafica di fatturazione — 30s di attesa sono troppi per un form.
_VIES_TIMEOUT_SECONDS = 8.0
_TOKEN_EXPIRY_MARGIN = 300
_POLL_INTERVAL_SECONDS = 3.0
_POLL_MAX_ATTEMPTS = 25
# Durata massima complessiva di it_full (prima chiamata + polling): DEVE
# restare sotto il TTL del lock di import (330s, che copre anche IT-advanced
# nell'anteprima), o un import concorrente potrebbe partire mentre questo è
# ancora in corso e pagare due volte.
_TOTAL_DEADLINE_SECONDS = 240.0
_PENDING_STATES = {"PENDING", "IN_PROGRESS", "RUNNING"}


def _mask_url(url: str) -> str:
    """URL per i log con l'identificativo finale mascherato: i path contengono
    P.IVA o CODICI FISCALI (dato personale) e non devono finire nei log."""
    base, _, last = url.rpartition("/")
    if not base or not last:
        return url
    return f"{base}/***"


_HOST_PRODOTTI = frozenset(
    url.removeprefix("https://")
    for hosts in _HOSTS.values()
    for nome, url in hosts.items()
    if nome != "oauth"
)


class _MascheraUrlHttpx(logging.Filter):
    """httpx scrive a INFO ogni richiesta con l'URL COMPLETO («HTTP Request:
    GET https://company.openapi.com/IT-advanced/<P.IVA> …») e main.py porta
    il root logger a INFO: senza questo filtro P.IVA e codici fiscali dei
    path openapi finirebbero in chiaro nei log del container. Maschera solo
    l'ultimo segmento degli URL dei prodotti openapi; il resto passa intatto."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 2:
            url = args[1]
            if getattr(url, "host", None) in _HOST_PRODOTTI:
                record.args = (args[0], _mask_url(str(url)), *args[2:])
        return True


_FILTRO_LOG_HTTPX = _MascheraUrlHttpx()
_logger_httpx = logging.getLogger("httpx")
if _FILTRO_LOG_HTTPX not in _logger_httpx.filters:
    _logger_httpx.addFilter(_FILTRO_LOG_HTTPX)


class OpenapiInvalidIdError(Exception):
    """L'identificativo richiesto (P.IVA/CF) è stato rifiutato dal provider
    (HTTP 406, error 222 "cf/piva not valid"). Il chiamante decide il codice
    HTTP appropriato in base al contesto."""


class OpenapiNessunDatoError(Exception):
    """IT-advanced non ha dati per l'impresa: HTTP 204, oppure 404/error 305
    («no company matches this id»). Non è un guasto: il chiamante lo traduce
    in «nessun bilancio disponibile». `status` distingue il 204 (possibile
    addebito) dal 404 (richiesta respinta)."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"nessun dato (HTTP {status})")


class OpenapiNonInviataError(OpenapiUpstreamError):
    """La richiesta CERTAMENTE non è arrivata al provider: mint del token
    fallito, oppure connessione rifiutata (ConnectError/PoolTimeout) anche al
    ritento. Nessun addebito possibile: è l'unico errore dopo cui rimborsare
    l'utente è sicuro. Resta un OpenapiUpstreamError (502) per i chiamanti
    che non lo distinguono."""


class OpenapiClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._email = settings.openapi_email
        self._api_key = settings.openapi_api_key
        env = "production" if settings.openapi_env == "production" else "sandbox"
        self.env = env
        self._hosts = _HOSTS[env]
        self._http = http or httpx.AsyncClient(timeout=settings.openapi_timeout_seconds)
        # Un token (con scadenza) e un lock per gruppo di scope.
        self._tokens: dict[str, tuple[str, float]] = {}
        self._token_locks: dict[str, asyncio.Lock] = {g: asyncio.Lock() for g in GRUPPI_TOKEN}

    @property
    def enabled(self) -> bool:
        return bool(self._email and self._api_key)

    @property
    def sandbox(self) -> bool:
        return self.env != "production"

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ token

    def _scopes(self, gruppo: str = "core") -> list[str]:
        """Scope del gruppo. `core` è IDENTICO agli scope storici: il mint dei
        prodotti già in produzione non cambia."""
        company = self._hosts["company"].removeprefix("https://")
        if gruppo == "core":
            risk = self._hosts["risk"].removeprefix("https://")
            return [
                f"GET:{company}/IT-full",
                f"GET:{company}/IT-check_id",
                f"GET:{company}/EU-start",
                f"GET:{risk}/IT-verifica_cf",
            ]
        if gruppo == "advanced":
            return [f"GET:{company}/IT-advanced"]
        if gruppo == "visure":
            # Solo bilancio-ottico e impresa: nessuna visura ordinaria.
            visure = self._hosts["visure"].removeprefix("https://")
            return [
                f"POST:{visure}/bilancio-ottico",
                f"GET:{visure}/bilancio-ottico",
                f"GET:{visure}/impresa",
            ]
        raise ValueError(f"gruppo di scope sconosciuto: {gruppo!r}")

    async def _mint_token(self, gruppo: str = "core") -> None:
        """Mint del token del gruppo. Un fallimento qui significa che il
        prodotto NON è stato chiamato: OpenapiNonInviataError."""
        try:
            resp = await self._http.post(
                f"{self._hosts['oauth']}/token",
                auth=(self._email, self._api_key),
                json={"scopes": self._scopes(gruppo), "ttl": _TOKEN_TTL_SECONDS},
            )
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("openapi: mint token fallito (gruppo=%s, %s)", gruppo, exc)
            raise OpenapiNonInviataError() from exc
        # Risposta NON-envelope: token/expire a livello radice.
        token = body.get("token") if isinstance(body, dict) else None
        if not token or not body.get("success"):
            logger.error(
                "openapi: mint token rifiutato (gruppo=%s): %s (error=%s)",
                gruppo,
                body.get("message") if isinstance(body, dict) else None,
                body.get("error") if isinstance(body, dict) else None,
            )
            raise OpenapiNonInviataError()
        self._tokens[gruppo] = (token, float(body.get("expire") or (time.time() + 3600)))
        logger.info("openapi: nuovo token emesso (env=%s, gruppo=%s)", self.env, gruppo)

    async def _get_token(self, gruppo: str = "core") -> str:
        async with self._token_locks[gruppo]:
            corrente = self._tokens.get(gruppo)
            if corrente is None or time.time() > corrente[1] - _TOKEN_EXPIRY_MARGIN:
                await self._mint_token(gruppo)
            return self._tokens[gruppo][0]

    async def prepara_token(self, gruppo: str) -> None:
        """Mint (gratuito) del token del gruppo, se manca o sta per scadere.
        Serve a chi misura il tetto di tempo della sola chiamata a pagamento:
        un mint lento dopo un riavvio non deve sembrare una richiesta partita
        a esito ignoto. Solleva OpenapiNonInviataError se il mint fallisce."""
        if not self.enabled:
            raise OpenapiNotConfiguredError()
        await self._get_token(gruppo)

    # --------------------------------------------------------------- requests

    async def _request(
        self, method: str, url: str, *, json: dict | None = None,
        timeout: float | None = None, gruppo: str = "core", _retry_auth: bool = True
    ) -> tuple[int, dict]:
        """Richiesta autenticata con gestione envelope. Ritorna (status, body);
        un 204 ritorna ``(204, {})`` senza leggere il corpo.

        Retry SOLO su errori di connessione (richiesta mai partita, non
        fatturata) e su 401 (token del gruppo scaduto: re-mint, la respinta
        non è fatturata). ReadTimeout e 5xx NON vengono ritentati: potrebbero
        essere già stati addebitati. ``timeout`` sovrascrive quello del
        client per le chiamate su percorsi interattivi (es. VIES).
        """
        if not self.enabled:
            raise OpenapiNotConfiguredError()
        token = await self._get_token(gruppo)
        headers = {"Authorization": f"Bearer {token}"}
        req_timeout = timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
        try:
            resp = await self._http.request(
                method, url, headers=headers, json=json, timeout=req_timeout
            )
        except httpx.ConnectError:
            logger.warning(
                "openapi: errore di connessione, ritento una volta (%s)", _mask_url(url)
            )
            try:
                resp = await self._http.request(
                    method, url, headers=headers, json=json, timeout=req_timeout
                )
            except (httpx.ConnectError, httpx.PoolTimeout) as exc:
                # Anche il ritento non è partito: nessun addebito possibile.
                raise OpenapiNonInviataError() from exc
            except httpx.TimeoutException as exc:
                # Il ritento È partito: esito (e addebito) ignoto.
                logger.error("openapi: timeout al ritento su %s", _mask_url(url))
                raise OpenapiTimeoutError() from exc
            except httpx.HTTPError as exc:
                # Es. RemoteProtocolError: la richiesta può essere arrivata.
                raise OpenapiUpstreamError() from exc
        except httpx.TimeoutException as exc:
            # Esito ignoto (possibile addebito): nessun retry automatico.
            logger.error("openapi: timeout su %s", _mask_url(url))
            raise OpenapiTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise OpenapiUpstreamError() from exc

        if resp.status_code == 401 and _retry_auth:
            async with self._token_locks[gruppo]:
                self._tokens.pop(gruppo, None)  # solo il token di QUESTO gruppo
            return await self._request(
                method, url, json=json, timeout=timeout, gruppo=gruppo, _retry_auth=False
            )

        if resp.status_code == 204:
            return 204, {}

        try:
            body = resp.json()
        except ValueError as exc:
            logger.error(
                "openapi: risposta non JSON da %s (HTTP %s)", _mask_url(url), resp.status_code
            )
            raise OpenapiUpstreamError() from exc
        return resp.status_code, body

    async def _get(
        self, url: str, *, timeout: float | None = None, gruppo: str = "core"
    ) -> tuple[int, dict]:
        return await self._request("GET", url, timeout=timeout, gruppo=gruppo)

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

    async def it_advanced(self, piva: str, *, timeout_s: float) -> dict:
        """Storico dei bilanci IT-advanced (A PAGAMENTO, sincrona). Ritorna
        ``data[0]`` (``data`` è un array). ``timeout_s`` è il tetto della
        singola chiamata: sta dentro il budget di tempo dell'anteprima.

        Solleva OpenapiNessunDatoError su 204 e su 404/305 (nessun dato per
        l'impresa), OpenapiInvalidIdError su identificativo rifiutato."""
        url = f"{self._hosts['company']}/IT-advanced/{piva}"
        status, body = await self._get(url, timeout=timeout_s, gruppo="advanced")
        if status == 204:
            raise OpenapiNessunDatoError(204)
        if not body.get("success") and (status == 404 or body.get("error") == 305):
            raise OpenapiNessunDatoError(status)
        data = self._check_envelope(status, body, url)
        if isinstance(data, list):
            if not data:
                raise OpenapiNessunDatoError(status)
            data = data[0]
        if not isinstance(data, dict):
            logger.error("openapi: payload IT-advanced inatteso (%r)", type(data).__name__)
            raise OpenapiUpstreamError()
        return data

    async def verifica_piva_ue(self, paese: str, partita_iva: str) -> bool:
        """Verifica di una P.IVA UE (scope EU-start): prova VIES per il
        reverse charge. Il prefisso VIES della Grecia è EL, non GR.

        Timeout dedicato più corto del client (30s): la chiamata vive nel
        salvataggio interattivo dell'anagrafica ed è comunque non bloccante
        (un timeout lascia l'esito a NULL, non blocca il salvataggio).

        Il formato del payload è difensivo (flag di validità se presente,
        altrimenti anagrafica restituita = P.IVA esistente): va CONFERMATO
        in sandbox alla prima esecuzione con la chiave di test."""
        prefisso = "EL" if paese.upper() == "GR" else paese.upper()
        url = f"{self._hosts['company']}/EU-start/{prefisso}{partita_iva}"
        try:
            status, body = await self._get(url, timeout=_VIES_TIMEOUT_SECONDS)
            data = self._check_envelope(status, body, url)
        except OpenapiInvalidIdError:
            return False  # identificativo rifiutato dal provider = non valido
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, dict):
            for chiave in ("valid", "isValid", "vies_valid", "validity"):
                if chiave in data:
                    return bool(data[chiave])
            return True  # anagrafica restituita senza flag: la P.IVA esiste
        return False

    async def verifica_cf(self, codice_fiscale: str) -> bool:
        """Verifica del codice fiscale all'Anagrafe Tributaria (sincrona)."""
        url = f"{self._hosts['risk']}/IT-verifica_cf/{codice_fiscale}"
        status, body = await self._get(url)
        data = self._check_envelope(status, body, url)
        if not isinstance(data, dict) or "validita" not in data:
            logger.error("openapi: payload verifica_cf inatteso: %r", data)
            raise OpenapiUpstreamError()
        return bool(data["validita"])
