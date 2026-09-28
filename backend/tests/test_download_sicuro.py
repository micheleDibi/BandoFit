"""Test del download sicuro dei PDF (WP3): resolver e rete FINTI, nessuna
connessione reale. Si esercita il vero `httpcore.AsyncConnectionPool` con il
backend validante sopra una rete simulata (flussi in memoria)."""

import asyncio

import httpcore
import pytest

from app.services import download_sicuro
from app.services.download_sicuro import (
    DOMINI_NEGATI,
    BackendValidante,
    _Concorrenza,
    host_negato,
    indirizzo_ammesso,
    scarica_pdf,
    url_negato,
)

PDF = b"%PDF-1.7\n" + b"x" * 500 + b"\n%%EOF"
IP_PUBBLICO = "93.184.216.34"
IP_PUBBLICO_2 = "151.101.1.69"
IP_PUBBLICO_6 = "2606:2800:220:1:248:1893:25c8:1946"


# ------------------------------------------------------------ finti


def risposta(
    status: int = 200,
    corpo: bytes = PDF,
    *,
    content_type: str | None = "application/pdf",
    location: str | None = None,
    content_length: bool = True,
    extra: list[tuple[str, str]] | None = None,
) -> bytes:
    motivo = {200: "OK", 301: "Moved", 302: "Found", 404: "Not Found"}.get(status, "X")
    righe = [f"HTTP/1.1 {status} {motivo}"]
    if content_type:
        righe.append(f"Content-Type: {content_type}")
    if location:
        righe.append(f"Location: {location}")
    if content_length:
        righe.append(f"Content-Length: {len(corpo)}")
    for chiave, valore in extra or []:
        righe.append(f"{chiave}: {valore}")
    righe.append("Connection: close")
    return ("\r\n".join(righe) + "\r\n\r\n").encode("latin-1") + corpo


class FlussoFinto(httpcore.AsyncNetworkStream):
    def __init__(self, dati: bytes, *, blocco: int = 256, attesa: float = 0.0) -> None:
        self._dati = dati
        self._pos = 0
        self._blocco = blocco
        self._attesa = attesa
        self.scritto = bytearray()
        self.sni: str | None = None
        self.chiuso = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._attesa:
            await asyncio.sleep(self._attesa)
        pezzo = self._dati[self._pos : self._pos + min(max_bytes, self._blocco)]
        self._pos += len(pezzo)
        return pezzo

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.scritto += buffer

    async def aclose(self) -> None:
        self.chiuso = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.sni = server_hostname
        return self

    def get_extra_info(self, info: str):
        return None

    @property
    def letti(self) -> int:
        return self._pos


class ReteFinta(httpcore.AsyncNetworkBackend):
    """Rete sotto il backend validante: una risposta (o una lista, una per
    connessione) per IP. Registra ogni connessione."""

    def __init__(self, risposte: dict[str, bytes | list[bytes]], **opzioni) -> None:
        self._risposte = {ip: list(r) if isinstance(r, list) else [r] for ip, r in risposte.items()}
        self._opzioni = opzioni
        self.connessioni: list[tuple[str, int]] = []
        self.flussi: list[FlussoFinto] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        self.connessioni.append((host, port))
        coda = self._risposte.get(host)
        if not coda:
            raise httpcore.ConnectError(f"nessuna risposta finta per {host}")
        dati = coda.pop(0) if len(coda) > 1 else coda[0]
        flusso = FlussoFinto(dati, **self._opzioni)
        self.flussi.append(flusso)
        return flusso

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def resolver_da(mappa: dict[str, list[str]]):
    chiamate: list[str] = []

    async def risolvi(host: str, porta: int) -> list[str]:
        chiamate.append(host)
        if host not in mappa:
            raise OSError("host sconosciuto")
        return mappa[host]

    risolvi.chiamate = chiamate  # type: ignore[attr-defined]
    return risolvi


async def scarica(url, rete, resolver, *, max_bytes=10_000, timeout_s=5.0):
    return await scarica_pdf(
        url, max_bytes=max_bytes, timeout_s=timeout_s, resolver=resolver, backend_rete=rete
    )


URL = "https://www.regione.example.it/bandi/avviso.pdf"
HOST = "www.regione.example.it"


# ------------------------------------------------------------ indirizzi


class TestIndirizzi:
    @pytest.mark.parametrize("ip", [IP_PUBBLICO, IP_PUBBLICO_2, IP_PUBBLICO_6])
    def test_pubblici_ammessi(self, ip):
        assert indirizzo_ammesso(ip)

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254",
            "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255",
            "198.18.0.1", "::1", "::", "fc00::1", "fd12:3456::1", "fe80::1", "fe80::1%en0",
            "ff02::1",
            # forme che incorporano un IPv4 (is_global le accetterebbe)
            "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::127.0.0.1", "::a9fe:a9fe",
            "64:ff9b::a9fe:a9fe", "64:ff9b::7f00:1", "64:ff9b:1::a9fe:a9fe",
            "2002:a9fe:a9fe::", "2002:7f00:1::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
            # site-local deprecato: per ipaddress è «globale»
            "fec0::1", "feff:ffff::1",
            "non-un-ip", "",
        ],
    )
    def test_non_pubblici_o_incorporati_rifiutati(self, ip):
        assert not indirizzo_ammesso(ip)

    def test_denylist_con_sottodomini(self):
        assert host_negato("www.fasi.eu")
        assert host_negato("FACEBOOK.COM.")
        assert host_negato("m.youtube.com")
        assert not host_negato("notfasi.eu")
        assert not host_negato("www.regione.piemonte.it")
        assert "obiettivoeuropa.com" in DOMINI_NEGATI

    def test_url_negato(self):
        assert url_negato("https://www.obiettivoeuropa.com/x.pdf")  # link_policy
        assert url_negato("https://sub.bandi.it/avviso.pdf")  # denylist locale
        assert url_negato(None)
        assert not url_negato(URL)

    @pytest.mark.parametrize(
        "url",
        [
            # il browser decodifica e normalizza l'host: porterebbe al concorrente
            "https://obiettivoeuropa%2Ecom/avviso.pdf",
            "https://www.obiettivoeuropa%2ecom/avviso.pdf",
            "https://ｏｂｉｅｔｔｉｖｏｅｕｒｏｐａ.com/avviso.pdf",
            "https://ｆａｓｉ.eu/avviso.pdf",
            "https://www.fasi%2Eeu/avviso.pdf",
        ],
    )
    def test_url_negato_con_host_normalizzato_dal_browser(self, url):
        assert url_negato(url)


# ------------------------------------------------------------ URL rifiutati


class TestUrlRifiutati:
    @pytest.mark.parametrize(
        ("url", "stato"),
        [
            ("http://www.regione.example.it/avviso.pdf", "schema_non_https"),
            ("ftp://www.regione.example.it/avviso.pdf", "schema_non_https"),
            ("https://127.0.0.1/avviso.pdf", "bloccato_policy"),
            ("https://[::1]/avviso.pdf", "bloccato_policy"),
            ("https://169.254.169.254/latest/meta-data", "bloccato_policy"),
            ("https://localhost/avviso.pdf", "bloccato_policy"),
            ("https://api.localhost/avviso.pdf", "bloccato_policy"),
            ("https://intranet/avviso.pdf", "bloccato_policy"),
            ("https://nas.local/avviso.pdf", "bloccato_policy"),
            ("https://metadata.google.internal/x", "bloccato_policy"),
            ("https://utente:segreto@www.regione.example.it/a.pdf", "bloccato_policy"),
            ("https://www.regione.example.it:8443/avviso.pdf", "bloccato_policy"),
            ("https://10.0.0.1.1/avviso.pdf", "bloccato_policy"),
            ("https://www.obiettivoeuropa.com/avviso.pdf", "bloccato_policy"),
            ("https://www.fasi.eu/avviso.pdf", "bloccato_policy"),
            ("https://www.facebook.com/avviso.pdf", "bloccato_policy"),
            ("https://t.me/canale", "bloccato_policy"),
            ("", "bloccato_policy"),
            ("https://" + "a" * 2100 + ".it/x.pdf", "bloccato_policy"),
        ],
    )
    async def test_rifiutato_senza_connessione(self, url, stato):
        rete = ReteFinta({IP_PUBBLICO: risposta()})
        resolver = resolver_da({HOST: [IP_PUBBLICO]})
        esito = await scarica(url, rete, resolver)
        assert esito.stato == stato
        assert esito.contenuto is None
        assert rete.connessioni == []
        assert resolver.chiamate == []

    @pytest.mark.parametrize(
        "indirizzi",
        [
            ["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["::1"], ["fc00::7"],
            ["::ffff:10.0.0.1"], ["64:ff9b::a9fe:a9fe"], ["64:ff9b:1::a"], ["2002:a9fe:a9fe::"],
            ["2001:0:4136:e378:8000:63bf:3fff:fdd2"], ["::127.0.0.1"],
            # anche UN solo indirizzo cattivo tra tanti buoni blocca tutto
            [IP_PUBBLICO, "10.0.0.1"], ["192.168.0.1", IP_PUBBLICO],
        ],
    )
    async def test_dns_verso_indirizzi_non_pubblici(self, indirizzi):
        rete = ReteFinta({ip: risposta() for ip in indirizzi})
        esito = await scarica(URL, rete, resolver_da({HOST: indirizzi}))
        assert esito.stato == "bloccato_policy"
        assert esito.motivo == "ip_non_pubblico"
        assert rete.connessioni == []

    async def test_dns_vuoto_o_in_errore(self):
        rete = ReteFinta({})
        esito = await scarica(URL, rete, resolver_da({HOST: []}))
        assert esito.stato == "errore_download"
        esito = await scarica(URL, rete, resolver_da({}))
        assert esito.stato == "errore_download"
        assert rete.connessioni == []


# ------------------------------------------------------------ download


class TestDownload:
    async def test_pdf_scaricato_connettendosi_all_ip_validato(self):
        rete = ReteFinta({IP_PUBBLICO: risposta()})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "ok"
        assert esito.contenuto == PDF
        assert esito.byte == len(PDF)
        assert len(esito.sha256) == 64
        assert esito.url_finale == URL
        assert rete.connessioni == [(IP_PUBBLICO, 443)]
        # TLS e certificato sull'hostname, non sull'IP.
        assert rete.flussi[0].sni == HOST

    async def test_richiesta_senza_accept_encoding(self):
        rete = ReteFinta({IP_PUBBLICO: risposta()})
        await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        richiesta = bytes(rete.flussi[0].scritto).lower()
        assert richiesta.startswith(b"get /bandi/avviso.pdf http/1.1")
        assert b"host: www.regione.example.it" in richiesta
        assert b"accept-encoding" not in richiesta
        assert b"user-agent:" in richiesta
        assert b"cookie" not in richiesta

    async def test_url_con_spazi_e_accenti_codificato(self):
        rete = ReteFinta({IP_PUBBLICO: risposta()})
        url = "https://www.regione.example.it/bandi/avviso finale è.pdf?v=1&x=a b"
        esito = await scarica(url, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "ok"
        assert b"/bandi/avviso%20finale%20%C3%A8.pdf?v=1&x=a%20b" in bytes(rete.flussi[0].scritto)

    async def test_ipv6_pubblico(self):
        rete = ReteFinta({IP_PUBBLICO_6: risposta()})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO_6]}))
        assert esito.stato == "ok"

    async def test_content_type_html_ma_pdf_vero_accettato(self):
        rete = ReteFinta({IP_PUBBLICO: risposta(content_type="text/html; charset=utf-8")})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "ok"
        assert esito.content_type == "text/html; charset=utf-8"

    async def test_content_type_pdf_senza_magic_bytes_rifiutato(self):
        corpo = b"<html><body>Pagina di cortesia</body></html>" * 20
        rete = ReteFinta({IP_PUBBLICO: risposta(corpo=corpo)})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "non_pdf"
        assert esito.contenuto is None

    async def test_magic_bytes_non_in_testa(self):
        rete = ReteFinta({IP_PUBBLICO: risposta(corpo=b"\n\n" + PDF)})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "non_pdf"

    async def test_corpo_troppo_corto(self):
        rete = ReteFinta({IP_PUBBLICO: risposta(corpo=b"%PD")})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "non_pdf"

    async def test_tetto_sul_content_length_dichiarato(self):
        grande = b"%PDF-" + b"0" * 5000
        rete = ReteFinta({IP_PUBBLICO: risposta(corpo=grande)})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}), max_bytes=1000)
        assert esito.stato == "troppo_grande"
        assert esito.motivo == "content_length"
        # Il corpo non è stato letto: solo intestazioni e poco più.
        assert rete.flussi[0].letti < len(grande)

    async def test_tetto_in_streaming_senza_content_length(self):
        grande = b"%PDF-" + b"0" * 50_000
        rete = ReteFinta({IP_PUBBLICO: risposta(corpo=grande, content_length=False)})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}), max_bytes=1000)
        assert esito.stato == "troppo_grande"
        assert esito.motivo == "streaming"
        assert rete.flussi[0].letti < 5_000

    async def test_content_encoding_ignorato_quindi_non_pdf(self):
        # Senza Accept-Encoding un server corretto non comprime; se lo fa, i
        # byte compressi non iniziano con %PDF- e niente viene decompresso.
        compressi = b"\x1f\x8b\x08\x00" + b"\x00" * 200
        rete = ReteFinta(
            {IP_PUBBLICO: risposta(corpo=compressi, extra=[("Content-Encoding", "gzip")])}
        )
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "non_pdf"

    async def test_http_404(self):
        rete = ReteFinta({IP_PUBBLICO: risposta(404, b"non trovato", content_type="text/plain")})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "errore_download"
        assert esito.motivo == "http_404"

    async def test_timeout_totale(self):
        rete = ReteFinta({IP_PUBBLICO: risposta()}, attesa=1.0)
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}), timeout_s=0.2)
        assert esito.stato == "errore_download"
        assert esito.motivo == "timeout"

    async def test_connessione_rifiutata(self):
        rete = ReteFinta({})  # nessuna risposta per l'IP → ConnectError
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "errore_download"

    async def test_proxy_d_ambiente_ignorato(self, monkeypatch):
        for nome in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY",
                     "all_proxy"):
            monkeypatch.setenv(nome, "http://10.9.9.9:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        rete = ReteFinta({IP_PUBBLICO: risposta()})
        resolver = resolver_da({HOST: [IP_PUBBLICO]})
        esito = await scarica(URL, rete, resolver)
        assert esito.stato == "ok"
        assert rete.connessioni == [(IP_PUBBLICO, 443)]
        assert resolver.chiamate == [HOST]
        assert b"CONNECT" not in bytes(rete.flussi[0].scritto)


# ------------------------------------------------------------ redirect


class TestRedirect:
    async def test_redirect_seguito_e_rivalidato(self):
        altro = "cdn.regione.example.it"
        rete = ReteFinta({
            IP_PUBBLICO: risposta(302, b"", location=f"https://{altro}/file/avviso.pdf"),
            IP_PUBBLICO_2: risposta(),
        })
        resolver = resolver_da({HOST: [IP_PUBBLICO], altro: [IP_PUBBLICO_2]})
        esito = await scarica(URL, rete, resolver)
        assert esito.stato == "ok"
        assert esito.url_finale == f"https://{altro}/file/avviso.pdf"
        assert resolver.chiamate == [HOST, altro]
        assert rete.connessioni == [(IP_PUBBLICO, 443), (IP_PUBBLICO_2, 443)]

    async def test_redirect_relativo(self):
        rete = ReteFinta({IP_PUBBLICO: [risposta(301, b"", location="/nuovo/avviso.pdf"),
                                        risposta()]})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "ok"
        assert esito.url_finale == f"https://{HOST}/nuovo/avviso.pdf"

    async def test_redirect_verso_http(self):
        rete = ReteFinta({IP_PUBBLICO: risposta(302, b"", location=f"http://{HOST}/a.pdf")})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "schema_non_https"
        assert len(rete.connessioni) == 1

    @pytest.mark.parametrize(
        "destinazione",
        [
            "https://www.obiettivoeuropa.com/a.pdf",
            "https://www.fasi.eu/a.pdf",
            "https://www.linkedin.com/a.pdf",
            "https://127.0.0.1/a.pdf",
            "https://localhost/a.pdf",
            "https://metadata.internal/a.pdf",
        ],
    )
    async def test_redirect_verso_host_bloccato(self, destinazione):
        rete = ReteFinta({IP_PUBBLICO: risposta(302, b"", location=destinazione)})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "bloccato_policy"
        assert len(rete.connessioni) == 1

    async def test_redirect_verso_host_con_dns_privato(self):
        rete = ReteFinta({
            IP_PUBBLICO: risposta(302, b"", location="https://interno.example.it/a.pdf"),
            "10.0.0.8": risposta(),
        })
        resolver = resolver_da({HOST: [IP_PUBBLICO], "interno.example.it": ["10.0.0.8"]})
        esito = await scarica(URL, rete, resolver)
        assert esito.stato == "bloccato_policy"
        assert esito.motivo == "ip_non_pubblico"
        assert rete.connessioni == [(IP_PUBBLICO, 443)]

    async def test_tre_redirect_ammessi(self):
        catena = [risposta(302, b"", location=f"/salto{i}.pdf") for i in range(3)] + [risposta()]
        rete = ReteFinta({IP_PUBBLICO: catena})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "ok"
        assert len(rete.connessioni) == 4

    async def test_oltre_tre_redirect(self):
        catena = [risposta(302, b"", location=f"/salto{i}.pdf") for i in range(4)] + [risposta()]
        rete = ReteFinta({IP_PUBBLICO: catena})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "errore_download"
        assert esito.motivo == "troppi_redirect"
        assert len(rete.connessioni) == 4

    async def test_redirect_senza_location(self):
        rete = ReteFinta({IP_PUBBLICO: risposta(302, b"")})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "errore_download"


# ------------------------------------------------------------ backend e concorrenza


class TestBackendEConcorrenza:
    async def test_backend_validante_non_apre_socket_unix(self):
        backend = BackendValidante(resolver=resolver_da({}), rete=ReteFinta({}))
        with pytest.raises(httpcore.ConnectError):
            await backend.connect_unix_socket("/var/run/docker.sock")

    async def test_backend_prova_gli_indirizzi_validati_in_ordine(self):
        rete = ReteFinta({IP_PUBBLICO_2: risposta()})
        backend = BackendValidante(
            resolver=resolver_da({HOST: [IP_PUBBLICO, IP_PUBBLICO_2]}), rete=rete
        )
        flusso = await backend.connect_tcp(HOST, 443)
        assert isinstance(flusso, FlussoFinto)
        assert rete.connessioni == [(IP_PUBBLICO, 443), (IP_PUBBLICO_2, 443)]

    async def test_un_download_per_host_e_due_in_totale(self):
        concorrenza = _Concorrenza(2, 1)
        attivi_totale = 0
        attivi_host: dict[str, int] = {}
        massimi = {"totale": 0, "host": 0}

        async def lavoro(host: str) -> None:
            nonlocal attivi_totale
            async with concorrenza.totale():
                async with concorrenza.host(host):
                    attivi_totale += 1
                    attivi_host[host] = attivi_host.get(host, 0) + 1
                    massimi["totale"] = max(massimi["totale"], attivi_totale)
                    massimi["host"] = max(massimi["host"], attivi_host[host])
                    await asyncio.sleep(0.01)
                    attivi_totale -= 1
                    attivi_host[host] -= 1

        await asyncio.gather(*(lavoro(h) for h in ["a", "a", "a", "b", "c", "b", "d"]))
        assert massimi == {"totale": 2, "host": 1}
        assert concorrenza._host == {}

    async def test_attesa_del_turno_fuori_dal_timeout(self):
        """Quattro PDF sullo stesso portale (1 download per host): ognuno
        impiega circa il 60% del tempo massimo. L'attesa in coda non consuma
        il tempo di rete: arrivano tutti."""
        corpo = b"%PDF-1.7\n" + b"x" * 200
        rete = ReteFinta({IP_PUBBLICO: risposta(corpo=corpo)}, blocco=64, attesa=0.05)
        resolver = resolver_da({HOST: [IP_PUBBLICO]})
        urls = [f"https://{HOST}/doc{i}.pdf" for i in range(4)]
        esiti = await asyncio.gather(
            *(scarica(u, rete, resolver, timeout_s=0.45) for u in urls)
        )
        assert [(e.stato, e.motivo) for e in esiti] == [("ok", None)] * 4

    async def test_timeout_sul_tempo_di_rete_anche_con_i_redirect(self):
        # il budget è di TUTTO il download: due salti lenti lo esauriscono
        rete = ReteFinta(
            {
                IP_PUBBLICO: risposta(302, b"", location="/secondo.pdf", content_type=None),
            },
            blocco=16,
            attesa=0.05,
        )
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}), timeout_s=0.3)
        assert (esito.stato, esito.motivo) == ("errore_download", "timeout")

    async def test_semafori_modulo_riusabili_tra_loop(self):
        # Il limitatore del modulo si ricrea per ogni event loop (un test = un loop).
        rete = ReteFinta({IP_PUBBLICO: risposta()})
        esito = await scarica(URL, rete, resolver_da({HOST: [IP_PUBBLICO]}))
        assert esito.stato == "ok"
        assert download_sicuro._CONCORRENZA._loop is asyncio.get_running_loop()
