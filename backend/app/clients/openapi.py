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
- **Bilancio ottico** (``visure``, WP2): ``POST /bilancio-ottico`` (A PAGAMENTO
  se accettata, 4,50 €) → stato ``In ricerca``/``In erogazione`` → ``Dati
  disponibili``/``Visura evasa`` (o ``Annullata``) → ``GET /{id}/allegati``
  con lo ZIP in base64, letto in streaming con un tetto di byte. Rifiuti
  sincroni (gratuiti): 278 bilancio assente, 213 forma giuridica non ammessa,
  275 identificativo non valido. ``GET /impresa/{cf_piva}`` dice se il
  prodotto è disponibile per quell'impresa (pre-check a 0,001 €).

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
import json as _json
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
# Tetto della lista delle richieste di bilancio ottico (riconciliazione): la
# risposta cresce con lo storico dell'account e si legge in streaming.
_MAX_LISTA_BILANCI_BYTES = 5_000_000
# Codici d'errore dell'envelope per il credito del provider esaurito.
_ERRORI_CREDITO = frozenset({610, 611})
# Chiavi dell'envelope che fanno pensare a una lista paginata (la forma della
# lista non è ancora verificata in sandbox): la lista non è completa.
_CHIAVI_PAGINAZIONE = frozenset({
    "next", "next_page", "nextPage", "prev", "page", "pages", "per_page", "total",
    "totale", "total_count", "count", "has_more", "hasMore", "pagination", "paging",
    "skip", "limit", "offset", "cursor", "links",
})


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


class OpenapiBilancioNonDisponibileError(Exception):
    """Bilancio ottico: il Registro Imprese non ha il bilancio richiesto
    (404/error 278). Rifiuto SINCRONO della POST: la richiesta non è stata
    accettata."""


class OpenapiFormaNonAmmessaError(Exception):
    """Bilancio ottico: l'impresa non è una società di capitali (error 213,
    «the cf_piva_id does not belong to a societa capitale»). Rifiuto
    sincrono, gratuito (verificato sul campo con la vecchia visura)."""


class OpenapiIdentificativoNonValidoError(Exception):
    """Bilancio ottico: identificativo rifiutato (400/error 275, «cf_piva_id
    not valid»). Rifiuto sincrono della POST."""


class OpenapiCreditoProviderError(Exception):
    """Credito dell'account openapi esaurito (HTTP 402, error 610/611): la
    richiesta è respinta e non addebitata. Guasto di PIATTAFORMA, loggato a
    CRITICAL: finché qualcuno non ricarica, nessuna chiamata a pagamento
    passa."""


class OpenapiRispostaTroppoGrandeError(OpenapiUpstreamError):
    """La risposta supera il tetto di byte del chiamante: letta in streaming
    e interrotta PRIMA del parse JSON (nessun picco di memoria). `motivo` è
    un codice stabile per il chiamante."""

    motivo = "risposta_troppo_grande"


class OpenapiListaParzialeError(OpenapiUpstreamError):
    """La lista delle richieste di bilancio ottico ha segni di paginazione:
    `voci` sono quelle lette, ma l'ASSENZA di una richiesta non prova nulla
    (potrebbe stare in un'altra pagina)."""

    def __init__(self, voci: list[dict]):
        super().__init__()
        self.voci = voci


def _codice_errore(body: dict) -> int | None:
    """Codice `error` dell'envelope come intero (il provider lo manda come
    numero; una stringa numerica vale lo stesso)."""
    codice = body.get("error") if isinstance(body, dict) else None
    if isinstance(codice, bool):
        return None
    try:
        return int(codice) if codice is not None else None
    except (TypeError, ValueError):
        return None


class _RispostaLetta:
    """Corpo letto in streaming entro il tetto: stessa interfaccia minima di
    httpx.Response usata da `_request` (status_code, json())."""

    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self.content = content

    def json(self):
        return _json.loads(self.content)


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

    async def _invia(
        self, method: str, url: str, *, headers: dict, json: dict | None, timeout,
        max_bytes: int | None,
    ):
        """Una richiesta HTTP. Con `max_bytes` il corpo si legge in STREAMING
        e si interrompe appena supera il tetto (anche se Content-Length è
        assente o falso): OpenapiRispostaTroppoGrandeError, mai un parse di
        una risposta enorme."""
        if max_bytes is None:
            return await self._http.request(
                method, url, headers=headers, json=json, timeout=timeout
            )
        async with self._http.stream(
            method, url, headers=headers, json=json, timeout=timeout
        ) as resp:
            dichiarata = str(resp.headers.get("content-length") or "")
            if dichiarata.isdigit() and int(dichiarata) > max_bytes:
                logger.error(
                    "openapi: risposta oltre il tetto di %s byte da %s (dichiarata)",
                    max_bytes, _mask_url(url),
                )
                raise OpenapiRispostaTroppoGrandeError()
            corpo = bytearray()
            async for pezzo in resp.aiter_bytes():
                corpo += pezzo
                if len(corpo) > max_bytes:
                    logger.error(
                        "openapi: risposta oltre il tetto di %s byte da %s",
                        max_bytes, _mask_url(url),
                    )
                    raise OpenapiRispostaTroppoGrandeError()
            return _RispostaLetta(resp.status_code, bytes(corpo))

    async def _request(
        self, method: str, url: str, *, json: dict | None = None,
        timeout: float | None = None, gruppo: str = "core", max_bytes: int | None = None,
        _retry_auth: bool = True,
    ) -> tuple[int, dict]:
        """Richiesta autenticata con gestione envelope. Ritorna (status, body);
        un 204 ritorna ``(204, {})`` senza leggere il corpo.

        Retry SOLO su errori di connessione (richiesta mai partita, non
        fatturata) e su 401 (token del gruppo scaduto: re-mint, la respinta
        non è fatturata). ReadTimeout e 5xx NON vengono ritentati: potrebbero
        essere già stati addebitati. ``timeout`` sovrascrive quello del
        client per le chiamate su percorsi interattivi (es. VIES).
        ``max_bytes``: tetto del corpo, letto in streaming (vedi `_invia`).
        """
        if not self.enabled:
            raise OpenapiNotConfiguredError()
        token = await self._get_token(gruppo)
        headers = {"Authorization": f"Bearer {token}"}
        req_timeout = timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
        try:
            resp = await self._invia(
                method, url, headers=headers, json=json, timeout=req_timeout,
                max_bytes=max_bytes,
            )
        except httpx.ConnectError:
            logger.warning(
                "openapi: errore di connessione, ritento una volta (%s)", _mask_url(url)
            )
            try:
                resp = await self._invia(
                    method, url, headers=headers, json=json, timeout=req_timeout,
                    max_bytes=max_bytes,
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
                method, url, json=json, timeout=timeout, gruppo=gruppo, max_bytes=max_bytes,
                _retry_auth=False,
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
        if not isinstance(body, dict):
            # Un JSON che non è l'envelope (lista, numero…): risposta malformata.
            logger.error(
                "openapi: risposta senza envelope da %s (HTTP %s)", _mask_url(url), resp.status_code
            )
            raise OpenapiUpstreamError()
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

    # ------------------------------------------------------------- visure

    @staticmethod
    def _rifiuti_bilancio(status: int, body: dict, url: str) -> None:
        """Rifiuti classificati della POST bilancio-ottico, prima
        dell'envelope generico. Non fa nulla su una risposta riuscita."""
        if body.get("success"):
            return
        codice = _codice_errore(body)
        message = str(body.get("message") or "")
        if status == 402 or codice in _ERRORI_CREDITO:
            logger.critical(
                "openapi: credito del provider esaurito (%s, HTTP %s, error=%s): "
                "ricaricare il conto openapi",
                _mask_url(url), status, codice,
            )
            raise OpenapiCreditoProviderError(message)
        if codice == 278:
            raise OpenapiBilancioNonDisponibileError(message)
        if codice == 213:
            raise OpenapiFormaNonAmmessaError(message)
        if codice in (275, 222):
            raise OpenapiIdentificativoNonValidoError(message)

    async def bilancio_ottico_richiedi(self, cf_piva: str, anno: int | None) -> dict:
        """Richiede il bilancio ottico (A PAGAMENTO se accettata). Nessuna
        callback (v1: poll). ``anno`` None = ultimo bilancio disponibile.

        Mai ritentata dopo la partenza: timeout e 5xx restano a esito ignoto
        (OpenapiTimeoutError/OpenapiUpstreamError). Rifiuti sincroni:
        OpenapiBilancioNonDisponibileError (278), OpenapiFormaNonAmmessaError
        (213), OpenapiIdentificativoNonValidoError (275), credito esaurito
        OpenapiCreditoProviderError; mint o connessione falliti
        OpenapiNonInviataError. Ritorna ``data`` (con ``id`` del provider)."""
        url = f"{self._hosts['visure']}/bilancio-ottico"
        corpo = {"cf_piva_id": cf_piva, "anno_chiusura": str(anno) if anno is not None else None}
        status, body = await self._request("POST", url, json=corpo, gruppo="visure")
        self._rifiuti_bilancio(status, body, url)
        try:
            data = self._check_envelope(status, body, url)
        except OpenapiInvalidIdError as exc:
            raise OpenapiIdentificativoNonValidoError(str(exc)) from exc
        if not isinstance(data, dict) or not data.get("id"):
            # Successo senza id: la richiesta può essere stata accettata.
            logger.error("openapi: risposta bilancio-ottico senza id (HTTP %s)", status)
            raise OpenapiUpstreamError()
        return data

    async def bilancio_ottico_stato(self, provider_id: str) -> dict:
        """Stato di una richiesta (0,001 €): ``stato_richiesta`` passa da «In
        ricerca»/«In erogazione» a «Dati disponibili»/«Visura evasa», oppure
        «Annullata»."""
        url = f"{self._hosts['visure']}/bilancio-ottico/{provider_id}"
        status, body = await self._get(url, gruppo="visure")
        data = self._check_envelope(status, body, url)
        if not isinstance(data, dict):
            logger.error("openapi: stato bilancio-ottico inatteso (%r)", type(data).__name__)
            raise OpenapiUpstreamError()
        return data

    async def bilancio_ottico_allegati(self, provider_id: str, *, max_bytes: int) -> dict | None:
        """Allegati della richiesta evasa: ``{nome, dimensione, file}`` con lo
        ZIP in base64. None se non ancora scaricabili (422/error 273). La
        risposta si legge in streaming: oltre ``max_bytes`` →
        OpenapiRispostaTroppoGrandeError, senza parse."""
        url = f"{self._hosts['visure']}/bilancio-ottico/{provider_id}/allegati"
        status, body = await self._request("GET", url, gruppo="visure", max_bytes=max_bytes)
        if not body.get("success") and (status == 422 or _codice_errore(body) == 273):
            return None
        data = self._check_envelope(status, body, url)
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict) and d.get("file")), None)
        if not isinstance(data, dict) or not isinstance(data.get("file"), str) or not data["file"]:
            logger.error("openapi: allegati bilancio-ottico senza file (HTTP %s)", status)
            raise OpenapiUpstreamError()
        return data

    async def bilancio_ottico_lista(self) -> list[dict]:
        """Richieste di bilancio ottico dell'account (0,001 €): serve alla
        riconciliazione di una POST a esito ignoto. Solo 404 CON error 270 =
        nessuna richiesta.

        Una lista vuota è la prova che una POST non è partita (e vale un
        rimborso): ogni altra forma dubbia FALLISCE CHIUSA. Un altro 404, un
        `data` nullo o senza forma di lista → OpenapiUpstreamError; segni di
        paginazione → OpenapiListaParzialeError con le voci lette (utili a
        riconciliare, mai a provare un'assenza)."""
        url = f"{self._hosts['visure']}/bilancio-ottico"
        status, body = await self._request(
            "GET", url, gruppo="visure", max_bytes=_MAX_LISTA_BILANCI_BYTES
        )
        if not body.get("success") and status == 404 and _codice_errore(body) == 270:
            return []
        data = self._check_envelope(status, body, url)
        if isinstance(data, dict) and data.get("id"):
            data = [data]  # una sola richiesta restituita come oggetto
        if not isinstance(data, list):
            logger.error("openapi: lista bilancio-ottico inattesa (%r)", type(data).__name__)
            raise OpenapiUpstreamError()
        voci = [voce for voce in data if isinstance(voce, dict)]
        if _CHIAVI_PAGINAZIONE & set(body):
            logger.warning(
                "openapi: lista bilancio-ottico con segni di paginazione (%s): letta solo in parte",
                ", ".join(sorted(_CHIAVI_PAGINAZIONE & set(body))),
            )
            raise OpenapiListaParzialeError(voci)
        return voci

    async def impresa(self, cf_piva: str) -> list[dict]:
        """Anagrafica Visure dell'impresa (0,001 €): ``chiamate_disponibili``
        dice se il bilancio ottico è richiedibile. 404 = impresa sconosciuta
        (lista vuota)."""
        url = f"{self._hosts['visure']}/impresa/{cf_piva}"
        status, body = await self._get(url, gruppo="visure")
        if not body.get("success") and status == 404:
            return []
        data = self._check_envelope(status, body, url)
        if data is None:
            return []
        if isinstance(data, dict):
            return [data]
        if not isinstance(data, list):
            logger.error("openapi: payload impresa inatteso (%r)", type(data).__name__)
            raise OpenapiUpstreamError()
        return [voce for voce in data if isinstance(voce, dict)]
