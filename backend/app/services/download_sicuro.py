"""Download server-side dei PDF ufficiali dei bandi, con difese SSRF (WP3).

L'URL arriva dal catalogo (`bando_link`, o `allegati` come ripiego): è un dato
NON fidato. Difese, nell'ordine:
- solo `https`, solo hostname (niente IP letterali, `localhost`, nomi a
  etichetta singola, `.local`/`.internal`…), niente credenziali nell'URL, solo
  la porta 443;
- `link_policy.is_blocked_link` più la denylist locale degli aggregatori e dei
  social del contratto DB §5 (sottodomini compresi);
- risoluzione DNS fatta QUI, con TUTTI gli indirizzi validati: niente privati,
  loopback, link-local (169.254.169.254 dei metadata), riservati, multicast,
  IPv4-mapped, IPv4-compatibili (`::/96`), 6to4, Teredo, NAT64; la
  connessione va all'IP validato (niente DNS rebinding tra controllo e
  connessione), mentre TLS e certificato restano sull'hostname;
- `httpcore.AsyncConnectionPool` diretto con il backend di rete validante:
  httpx 0.28 non accetta un `network_backend`, e httpcore non legge MAI le
  variabili d'ambiente dei proxy (equivale a `trust_env=False`, proxy=None);
- redirect gestiti a mano (massimo 3), ciascuno rivalidato da capo;
- niente `Accept-Encoding` (nessuna decompressione, nessuna bomba), tetto di
  byte applicato in streaming e sul `Content-Length` dichiarato;
- accettazione SOLO se i primi byte sono `%PDF-`: il content-type è informativo.
Concorrenza: 2 download in totale e 1 per host (per non martellare i portali).
Il tempo massimo copre SOLO il lavoro di rete: l'attesa del turno (stesso
portale, slot globali occupati) non lo consuma, altrimenti i documenti di uno
stesso bando, quasi sempre sullo stesso portale, andrebbero in timeout senza
essere mai stati chiesti.
"""

import asyncio
import hashlib
import ipaddress
import logging
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpcore

from app.services.link_policy import host_non_normalizzabile, is_blocked_link

logger = logging.getLogger("bandofit.partenariati")

StatoDownload = Literal[
    "ok", "errore_download", "bloccato_policy", "troppo_grande", "non_pdf", "schema_non_https"
]

MAX_REDIRECT = 3
CONNECT_TIMEOUT_SECONDS = 5.0
DOWNLOAD_TOTALI = 2
DOWNLOAD_PER_HOST = 1
MAX_URL = 2048
MAGIC_PDF = b"%PDF-"
USER_AGENT = "Mozilla/5.0 (compatible; BandoFit/1.0)"
_REDIRECT = frozenset({301, 302, 303, 307, 308})

# Contratto DB bandi §5: aggregatori (mai fonti ufficiali) e domini social,
# video e di messaggistica. Sottodomini compresi.
DOMINI_NEGATI = frozenset(
    {
        # aggregatori
        "obiettivoeuropa.com", "fasi.eu", "europafacile.net", "contributiregione.it",
        "finanziamentinews.it", "bandi.it", "infobandi.it", "ticonsiglio.com",
        "contributieuropa.com", "first.aster.it",
        # social, video, messaggistica
        "facebook.com", "instagram.com", "x.com", "twitter.com", "linkedin.com",
        "threads.net", "pinterest.com", "tiktok.com", "youtube.com", "youtu.be",
        "vimeo.com", "t.me", "telegram.me", "wa.me", "whatsapp.com",
    }
)

# Suffissi di reti private o di servizio: mai risolti verso l'esterno.
_SUFFISSI_INTERNI = (
    ".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan", ".intranet",
    ".corp", ".private",
)
_HOST_AMMESSO = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")

# Reti IPv6 che incorporano un IPv4 (o lo raggiungono via traduzione):
# `is_global` le considera pubbliche, ma possono portare a 127/8 o 169.254/16.
_RETI_IPV6_VIETATE = tuple(
    ipaddress.IPv6Network(rete)
    for rete in ("::/96", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16",
                 "2001::/32",
                 # site-local (deprecato, RFC 3879): per `ipaddress` è «globale»,
                 # ma sulle reti legacy è ancora instradato come rete interna.
                 "fec0::/10")
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class DocumentoScaricato:
    stato: StatoDownload
    sha256: str | None = None
    byte: int = 0
    contenuto: bytes | None = None
    # Informativi: URL dopo i redirect, content-type dichiarato, motivo breve
    # (per log e diagnostica; mai mostrato così com'è all'utente).
    url_finale: str | None = None
    content_type: str | None = None
    motivo: str | None = None


class IndirizzoNonAmmessoError(Exception):
    """L'host risolve (anche) verso un indirizzo non pubblico."""


# ------------------------------------------------------------ validazioni


def host_negato(host: str | None) -> bool:
    """True se l'host è nella denylist locale (dominio esatto o sottodominio)."""
    if not host:
        return False
    host = host.rstrip(".").lower()
    return any(host == d or host.endswith("." + d) for d in DOMINI_NEGATI)


def _host_come_browser(host: str) -> str | None:
    """Host normalizzato come un browser, minuscolo e senza punto finale.
    None se non è normalizzabile."""
    grezzo = unquote(host).rstrip(".")
    if host_non_normalizzabile(grezzo):
        return None
    try:
        return grezzo.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def url_negato(url: str | None) -> bool:
    """True se l'URL è escluso dalla policy dei link o dalla denylist locale,
    anche nella forma in cui lo normalizza il browser (host percent-encoded o
    a larghezza piena)."""
    if not isinstance(url, str):
        return True
    if is_blocked_link(url):
        return True
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return True
    if host_negato(host):
        return True
    if not host:
        return False
    normalizzato = _host_come_browser(host)
    if normalizzato is None:
        return True
    return host_negato(normalizzato) or is_blocked_link(f"https://{normalizzato}/")


def indirizzo_ammesso(ip: str) -> bool:
    """True solo per un indirizzo unicast pubblico, senza IPv4 incorporati."""
    try:
        indirizzo = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(indirizzo, ipaddress.IPv6Address):
        if indirizzo.scope_id:
            return False
        if indirizzo.ipv4_mapped or indirizzo.sixtofour or indirizzo.teredo:
            return False
        if any(indirizzo in rete for rete in _RETI_IPV6_VIETATE):
            return False
    if (
        indirizzo.is_private
        or indirizzo.is_loopback
        or indirizzo.is_link_local
        or indirizzo.is_multicast
        or indirizzo.is_reserved
        or indirizzo.is_unspecified
    ):
        return False
    return indirizzo.is_global


def _host_valido(host: str) -> bool:
    if not host or len(host) > 253 or not set(host) <= _HOST_AMMESSO:
        return False
    try:
        ipaddress.ip_address(host)
        return False  # IP letterale
    except ValueError:
        pass
    if "." not in host or host == "localhost" or host.endswith(_SUFFISSI_INTERNI):
        return False
    etichette = host.split(".")
    if any(not e or len(e) > 63 or e.startswith("-") or e.endswith("-") for e in etichette):
        return False
    # Un TLD solo numerico non esiste: «10.0.0.1.» o «0x7f.1» sono IP mascherati.
    return not etichette[-1].isdigit()


@dataclass(frozen=True)
class _Destinazione:
    host: str  # ASCII (IDNA), minuscolo
    target: str  # path + query, percent-encoded
    url: str  # forma canonica, senza frammento


def _valida_url(url: str) -> tuple[_Destinazione | None, StatoDownload | None, str | None]:
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL:
        return None, "bloccato_policy", "url_non_valido"
    url = url.strip()
    try:
        parti = urlsplit(url)
        porta = parti.port
    except ValueError:
        return None, "bloccato_policy", "url_non_valido"
    if parti.scheme.lower() != "https":
        return None, "schema_non_https", "schema"
    if parti.username is not None or parti.password is not None:
        return None, "bloccato_policy", "credenziali_nell_url"
    if porta not in (None, 443):
        return None, "bloccato_policy", "porta"
    host = (parti.hostname or "").rstrip(".")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None, "bloccato_policy", "host_non_valido"
    if not _host_valido(host):
        return None, "bloccato_policy", "host_non_valido"
    if is_blocked_link(url) or host_negato(host):
        return None, "bloccato_policy", "dominio_negato"
    path = quote(parti.path or "/", safe="/%:@!$&'()*+,;=-._~")
    target = path + ("?" + quote(parti.query, safe="/%:@!$&'()*+,;=-._~?") if parti.query else "")
    return _Destinazione(host=host, target=target, url=f"https://{host}{target}"), None, None


# ------------------------------------------------------------ rete


async def _risolvi_dns(host: str, porta: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, porta, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class BackendValidante(httpcore.AsyncNetworkBackend):
    """Backend di rete per httpcore: risolve l'host, valida TUTTI gli
    indirizzi e si connette a uno di quelli validati. TLS (start_tls) resta
    sull'hostname originale: il certificato si verifica sul nome."""

    def __init__(
        self,
        resolver: Resolver | None = None,
        rete: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolver = resolver or _risolvi_dns
        self._rete = rete or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        indirizzi = list(await self._resolver(host, port))
        if not indirizzi:
            raise httpcore.ConnectError("nessun indirizzo per l'host")
        if not all(indirizzo_ammesso(ip) for ip in indirizzi):
            raise IndirizzoNonAmmessoError(host)
        ultimo: Exception | None = None
        for ip in indirizzi:
            try:
                return await self._rete.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                ultimo = exc
        raise ultimo or httpcore.ConnectError("connessione non riuscita")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("socket unix non ammessi")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _Concorrenza:
    """Semaforo globale + un lock per host, ricreati per event loop (i test
    ne usano uno nuovo per ogni caso). Ordine fisso: prima il lock dell'host,
    poi lo slot globale (chi aspetta un portale occupato non tiene uno slot).
    Un task tiene al più UN lock di host alla volta (lo rilascia prima del
    salto successivo) e con lo slot globale non aspetta nient'altro: niente
    stalli."""

    def __init__(self, totale: int, per_host: int) -> None:
        self._totale_n = totale
        self._per_host_n = per_host
        self._loop: asyncio.AbstractEventLoop | None = None
        self._totale: asyncio.Semaphore | None = None
        self._host: dict[str, list] = {}

    def _prepara(self) -> None:
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop = loop
            self._totale = asyncio.Semaphore(self._totale_n)
            self._host = {}

    @asynccontextmanager
    async def totale(self) -> AsyncIterator[None]:
        self._prepara()
        async with self._totale:
            yield

    @asynccontextmanager
    async def host(self, host: str) -> AsyncIterator[None]:
        self._prepara()
        voce = self._host.setdefault(host, [asyncio.Semaphore(self._per_host_n), 0])
        voce[1] += 1
        try:
            async with voce[0]:
                yield
        finally:
            voce[1] -= 1
            if voce[1] == 0 and self._host.get(host) is voce:
                del self._host[host]


_CONCORRENZA = _Concorrenza(DOWNLOAD_TOTALI, DOWNLOAD_PER_HOST)


def _errore(motivo: str) -> DocumentoScaricato:
    return DocumentoScaricato(stato="errore_download", motivo=motivo)


def _header(risposta: httpcore.Response, nome: bytes) -> str | None:
    for chiave, valore in risposta.headers:
        if chiave.lower() == nome:
            return valore.decode("latin-1").strip()
    return None


# ------------------------------------------------------------ download


async def scarica_pdf(
    url: str,
    *,
    max_bytes: int,
    timeout_s: float,
    resolver: Resolver | None = None,
    backend_rete: httpcore.AsyncNetworkBackend | None = None,
) -> DocumentoScaricato:
    """Scarica un PDF con le difese del modulo. Non solleva mai: ogni esito è
    uno `stato`. `backend_rete` (la rete «vera» sotto la validazione) e
    `resolver` servono ai test: in produzione restano None."""
    destinazione, stato, motivo = _valida_url(url)
    if destinazione is None:
        return _esito(url, stato or "bloccato_policy", motivo)
    backend = BackendValidante(resolver=resolver, rete=backend_rete)
    try:
        risultato = await _segui_redirect(backend, destinazione, max_bytes, timeout_s)
    except IndirizzoNonAmmessoError:
        risultato = DocumentoScaricato(stato="bloccato_policy", motivo="ip_non_pubblico")
    except (TimeoutError, httpcore.TimeoutException):
        risultato = DocumentoScaricato(stato="errore_download", motivo="timeout")
    except (httpcore.NetworkError, httpcore.ProtocolError, OSError):
        risultato = DocumentoScaricato(stato="errore_download", motivo="rete")
    except Exception:  # noqa: BLE001 — il chiamante vuole sempre un esito
        logger.exception("download_partenariato: errore imprevisto host=%s", destinazione.host)
        risultato = DocumentoScaricato(stato="errore_download", motivo="imprevisto")
    return _esito(url, risultato.stato, risultato.motivo, risultato)


def _esito(
    url: str,
    stato: StatoDownload,
    motivo: str | None,
    risultato: DocumentoScaricato | None = None,
) -> DocumentoScaricato:
    try:
        host = urlsplit(url).hostname if isinstance(url, str) else None
    except ValueError:
        host = None
    livello = logging.INFO if stato == "ok" else logging.WARNING
    logger.log(livello, "download_partenariato: host=%s stato=%s motivo=%s", host, stato, motivo)
    if risultato is not None:
        return risultato
    return DocumentoScaricato(stato=stato, motivo=motivo)


async def _segui_redirect(
    backend: httpcore.AsyncNetworkBackend,
    destinazione: _Destinazione,
    max_bytes: int,
    timeout_s: float,
) -> DocumentoScaricato:
    """Richiesta e redirect. `timeout_s` è il budget di RETE di tutto il
    download (redirect compresi): scorre solo mentre il task ha il turno
    (lock dell'host e slot globale), mai durante l'attesa in coda."""
    corrente = destinazione
    restante = float(timeout_s)
    for salto in range(MAX_REDIRECT + 1):
        if restante <= 0:
            raise TimeoutError
        async with _CONCORRENZA.host(corrente.host), _CONCORRENZA.totale():
            inizio = time.monotonic()
            try:
                async with asyncio.timeout(restante):
                    async with httpcore.AsyncConnectionPool(
                        network_backend=backend, retries=0, http1=True, http2=False
                    ) as pool:
                        esito, prossimo = await _richiesta(pool, corrente, max_bytes, restante)
            finally:
                restante -= time.monotonic() - inizio
        if esito is not None:
            return esito
        if salto == MAX_REDIRECT:
            break
        nuova, stato, motivo = _valida_url(urljoin(corrente.url, prossimo or ""))
        if nuova is None:
            return DocumentoScaricato(stato=stato or "bloccato_policy", motivo=f"redirect_{motivo}")
        corrente = nuova
    return DocumentoScaricato(stato="errore_download", motivo="troppi_redirect")


async def _richiesta(
    pool: httpcore.AsyncConnectionPool,
    destinazione: _Destinazione,
    max_bytes: int,
    timeout_s: float,
) -> tuple[DocumentoScaricato | None, str | None]:
    """Una richiesta GET. Restituisce (esito, None) oppure (None, location)
    se la risposta è un redirect da seguire."""
    url = httpcore.URL(
        scheme=b"https",
        host=destinazione.host.encode("ascii"),
        port=None,
        target=destinazione.target.encode("ascii"),
    )
    headers = [
        (b"User-Agent", USER_AGENT.encode("ascii")),
        (b"Accept", b"application/pdf,*/*;q=0.5"),
        # Nessun Accept-Encoding: il corpo arriva così com'è, senza bombe.
    ]
    estensioni = {
        "timeout": {
            "connect": min(CONNECT_TIMEOUT_SECONDS, timeout_s),
            "read": timeout_s,
            "write": timeout_s,
            "pool": timeout_s,
        }
    }
    async with pool.stream("GET", url, headers=headers, extensions=estensioni) as risposta:
        if risposta.status in _REDIRECT:
            location = _header(risposta, b"location")
            if not location:
                return _errore("redirect_senza_location"), None
            return None, location
        if risposta.status != 200:
            return _errore(f"http_{risposta.status}"), None
        content_type = _header(risposta, b"content-type")
        dichiarata = _header(risposta, b"content-length")
        if dichiarata and dichiarata.isdigit() and int(dichiarata) > max_bytes:
            return DocumentoScaricato(stato="troppo_grande", motivo="content_length"), None
        corpo = bytearray()
        verificato = False
        async for blocco in risposta.aiter_stream():
            corpo += blocco
            if len(corpo) > max_bytes:
                return DocumentoScaricato(stato="troppo_grande", motivo="streaming"), None
            if not verificato and len(corpo) >= len(MAGIC_PDF):
                if not corpo.startswith(MAGIC_PDF):
                    return DocumentoScaricato(
                        stato="non_pdf", content_type=content_type, motivo="magic_bytes"
                    ), None
                verificato = True
        if not corpo.startswith(MAGIC_PDF):
            return DocumentoScaricato(
                stato="non_pdf", content_type=content_type, motivo="magic_bytes"
            ), None
        contenuto = bytes(corpo)
        return DocumentoScaricato(
            stato="ok",
            sha256=hashlib.sha256(contenuto).hexdigest(),
            byte=len(contenuto),
            contenuto=contenuto,
            url_finale=destinazione.url,
            content_type=content_type,
        ), None
