"""Test dei pulsanti e degli allegati della scheda (contratto DB bandi §5.1):
ordine del pulsante principale, pulsante «Fonte ufficiale», allegati senza
doppioni, filtro dei link su tutte le fonti, lettura di `bando_link` e suo
degrado."""

import logging
from types import SimpleNamespace

import httpx
import pytest
from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.schemas.bando import AllegatoScheda
from app.services.bando_scheda_link import (
    BANDO_LINK_SELECT,
    ETICHETTA_RIPIEGO,
    calcola_allegati,
    calcola_cta,
    calcola_link_fonte,
    carica_link_scheda,
    chiave_url_catalogo,
    fonte_ufficiale_pubblicabile,
)

FONTE = "https://www.regione.it/bando"
BANDO = "https://portale.regione.it/bando"
MODULO = "https://servizi.regione.it/modulo"


def _riga(**campi) -> dict:
    """Riga del bando con i soli campi che servono alla scheda."""
    base = {
        "link_candidatura": None,
        "link_bando": None,
        "allegati": None,
        "fonte_ufficiale_url": None,
        "fonte_ufficiale_host": None,
        "fonte_ufficiale_stato": None,
    }
    return {**base, **campi}


def _fonte(**campi) -> dict:
    return _riga(
        fonte_ufficiale_url=FONTE,
        fonte_ufficiale_host="www.regione.it",
        fonte_ufficiale_stato="trovata",
        **campi,
    )


def _link(id_: int, tipo: str, url: str, *, visto: str | None = "2026-09-29T08:00:00+00:00",
          etichetta: str | None = None, content_type: str | None = None) -> dict:
    return {
        "id": id_, "bando_id": 1, "url": url, "dominio": None, "tipo": tipo,
        "etichetta": etichetta, "content_type": content_type, "ultimo_visto_at": visto,
    }


def _cta(riga: dict, link: list[dict] | None = None) -> tuple | None:
    cta = calcola_cta(riga, link or [])
    return (cta.origine, cta.url) if cta else None


# ------------------------------------------------------ pulsante principale


class TestOrdineCta:
    def test_1_riga_candidatura_vince_su_tutto(self):
        riga = _fonte(link_candidatura=MODULO, link_bando=BANDO)
        link = [
            _link(2, "portale", "https://portale.gov.it/"),
            _link(3, "candidatura", "https://domande.regione.it/"),
        ]
        assert _cta(riga, link) == ("candidatura", "https://domande.regione.it/")

    def test_2_link_candidatura_prima_della_fonte(self):
        riga = _fonte(link_candidatura=MODULO, link_bando=BANDO)
        link = [_link(2, "portale", "https://portale.gov.it/")]
        assert _cta(riga, link) == ("link_candidatura", MODULO)

    def test_3_fonte_trovata_prima_del_portale(self):
        riga = _fonte(link_bando=BANDO)
        link = [_link(2, "portale", "https://portale.gov.it/")]
        assert _cta(riga, link) == ("fonte_ufficiale", FONTE)

    @pytest.mark.parametrize("stato", ["in_verifica", "non_trovata", None, "trovata "])
    def test_3_fonte_non_trovata_si_salta(self, stato):
        riga = _fonte(link_bando=BANDO)
        riga["fonte_ufficiale_stato"] = stato
        link = [_link(2, "portale", "https://portale.gov.it/")]
        assert _cta(riga, link) == ("portale", "https://portale.gov.it/")

    def test_4_portale_prima_di_link_bando(self):
        riga = _riga(link_bando=BANDO)
        link = [_link(2, "portale", "https://portale.gov.it/")]
        assert _cta(riga, link) == ("portale", "https://portale.gov.it/")

    def test_5_link_bando_ultimo_ripiego(self):
        assert _cta(_riga(link_bando=BANDO)) == ("link_bando", BANDO)

    def test_nessun_candidato(self):
        assert _cta(_riga()) is None
        assert calcola_cta({}, []) is None

    def test_atto_e_allegato_non_sono_pulsanti(self):
        link = [_link(1, "atto", "https://x.it/a.pdf"), _link(2, "allegato", "https://x.it/b")]
        assert _cta(_riga(), link) is None


class TestPiuRigheDelloStessoTipo:
    @pytest.mark.parametrize("tipo", ["candidatura", "portale"])
    def test_vince_l_id_piu_basso(self, tipo):
        # `ultimo_visto_at` avanza a ogni verifica: non decide il pulsante.
        link = [
            _link(1, tipo, "https://a.it/", visto="2026-09-01T10:00:00+00:00"),
            _link(2, tipo, "https://b.it/", visto="2026-09-20T10:00:00+00:00"),
        ]
        assert _cta(_riga(), link) == (tipo, "https://a.it/")

    @pytest.mark.parametrize("tipo", ["candidatura", "portale"])
    def test_l_ordine_di_arrivo_non_conta(self, tipo):
        link = [
            _link(9, tipo, "https://b.it/", visto="2026-09-20T10:00:00+00:00"),
            _link(4, tipo, "https://a.it/", visto="2026-09-01T10:00:00+00:00"),
        ]
        assert _cta(_riga(), link) == (tipo, "https://a.it/")

    @pytest.mark.parametrize(
        "visto", [None, "", "non-una-data", 12345, "2020-01-01T00:00:00+00:00",
                  "2030-01-01T00:00:00+00:00"]
    )
    def test_ultimo_visto_non_cambia_il_pulsante(self, visto):
        fisso = "2026-09-10T00:00:00+00:00"
        link = [
            _link(1, "candidatura", "https://a.it/", visto=visto),
            _link(2, "candidatura", "https://b.it/", visto=fisso),
        ]
        assert _cta(_riga(), link) == ("candidatura", "https://a.it/")
        link = [
            _link(1, "candidatura", "https://a.it/", visto=fisso),
            _link(2, "candidatura", "https://b.it/", visto=visto),
        ]
        assert _cta(_riga(), link) == ("candidatura", "https://a.it/")

    def test_righe_malformate_ignorate(self):
        link = ["non-un-dict", {"tipo": "candidatura"}, {"tipo": "candidatura", "url": None},
                _link(5, "candidatura", "https://ok.it/")]
        assert _cta(_riga(), link) == ("candidatura", "https://ok.it/")


class TestFiltroSulPulsante:
    def test_url_scartato_passa_alla_riga_successiva_dello_stesso_tipo(self):
        link = [
            _link(1, "candidatura", "https://www.facebook.com/bando",
                  visto="2026-09-29T00:00:00+00:00"),
            _link(2, "candidatura", "https://domande.regione.it/",
                  visto="2026-09-01T00:00:00+00:00"),
        ]
        assert _cta(_riga(), link) == ("candidatura", "https://domande.regione.it/")

    def test_url_scartato_passa_al_passo_successivo(self):
        riga = _fonte(link_candidatura="https://it.linkedin.com/posts/x")
        assert _cta(riga) == ("fonte_ufficiale", FONTE)

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "mailto:bandi@regione.it",
            "ftp://ftp.regione.it/bando.pdf",
            "www.regione.it/bando",
            "https://www.obiettivoeuropa.com/bandi/x",
            "https://news.fasi.eu/bando",
            "https://t.me/canale",
            "https://utente:segreto@regione.it/",
            "https://regio ne.it/a",
            "https://regione.it/a.pdf https://regione.it/b.pdf",
            "https://regione.it/a\tb",
        ],
    )
    def test_url_non_pubblicabili_mai_pulsante(self, url):
        riga = _riga(link_candidatura=url, link_bando=url)
        assert _cta(riga, [_link(1, "candidatura", url)]) is None

    def test_spazio_nel_percorso_codificato_nel_pulsante(self):
        atteso = "https://regione.it/avviso%202026/domanda"
        link = [_link(1, "candidatura", "https://regione.it/avviso 2026/domanda")]
        assert _cta(_riga(), link) == ("candidatura", atteso)
        riga = _riga(link_bando="https://regione.it/avviso 2026/domanda")
        assert _cta(riga) == ("link_bando", atteso)
        assert calcola_link_fonte(riga).url == atteso

    def test_host_normalizzato_per_le_righe(self):
        cta = calcola_cta(_riga(), [_link(1, "portale", "https://WWW.Mimit.GOV.it:443/x")])
        assert cta.host == "www.mimit.gov.it"
        assert cta.url == "https://WWW.Mimit.GOV.it:443/x"

    def test_etichetta_della_fonte_e_l_host_dell_url(self):
        riga = _fonte()
        riga["fonte_ufficiale_host"] = "Regione.it"
        assert calcola_cta(riga, []).host == "www.regione.it"
        assert calcola_link_fonte(riga).host == "www.regione.it"

    @pytest.mark.parametrize("host", [None, "", "   "])
    def test_host_della_fonte_vuoto_ripiega_sull_url(self, host):
        riga = _fonte()
        riga["fonte_ufficiale_host"] = host
        cta = calcola_cta(riga, [])
        assert (cta.origine, cta.host) == ("fonte_ufficiale", "www.regione.it")

    @pytest.mark.parametrize("host", ["www.youtube.com", "obiettivoeuropa.com", "localhost"])
    def test_host_della_fonte_escluso_scarta_la_fonte(self, host):
        # url e host vanno insieme, come in scrub_bando_row: si passa al
        # candidato successivo.
        riga = _fonte(link_bando=BANDO)
        riga["fonte_ufficiale_host"] = host
        link = [_link(2, "portale", "https://portale.gov.it/")]
        assert _cta(riga, link) == ("portale", "https://portale.gov.it/")
        assert _cta(riga) == ("link_bando", BANDO)


# --------------------------------------------------------- fonte ufficiale


class TestLinkFonte:
    def test_fonte_trovata(self):
        fonte = calcola_link_fonte(_fonte(link_bando=BANDO))
        assert (fonte.origine, fonte.url, fonte.host) == (
            "fonte_ufficiale", FONTE, "www.regione.it"
        )

    def test_fonte_non_trovata_ripiega_su_link_bando(self):
        riga = _fonte(link_bando=BANDO)
        riga["fonte_ufficiale_stato"] = "in_verifica"
        fonte = calcola_link_fonte(riga)
        assert (fonte.origine, fonte.url, fonte.host) == (
            "link_bando", BANDO, "portale.regione.it"
        )

    def test_fonte_scartata_ripiega_su_link_bando(self):
        riga = _fonte(link_bando=BANDO)
        riga["fonte_ufficiale_url"] = "https://www.facebook.com/regione"
        assert calcola_link_fonte(riga).origine == "link_bando"

    def test_host_della_fonte_escluso_ripiega_su_link_bando(self):
        riga = _fonte(link_bando=BANDO)
        riga["fonte_ufficiale_host"] = "m.facebook.com"
        assert calcola_link_fonte(riga).origine == "link_bando"


class TestFonteUfficialePubblicabile:
    def test_fonte_ammessa_invariata(self):
        assert fonte_ufficiale_pubblicabile(_fonte()) == (FONTE, "www.regione.it")

    def test_campi_assenti(self):
        assert fonte_ufficiale_pubblicabile({}) == (None, None)
        riga = _fonte()
        riga["fonte_ufficiale_host"] = None
        assert fonte_ufficiale_pubblicabile(riga) == (FONTE, None)

    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("https://www.facebook.com/regione", "www.regione.it"),
            ("javascript:alert(1)", "www.regione.it"),
            (FONTE, "www.youtube.com"),
            (None, "t.me"),
        ],
    )
    def test_cadono_insieme(self, url, host):
        riga = _fonte()
        riga["fonte_ufficiale_url"] = url
        riga["fonte_ufficiale_host"] = host
        assert fonte_ufficiale_pubblicabile(riga) == (None, None)

    def test_url_con_spazio_codificato_come_nei_pulsanti(self):
        riga = _fonte(link_bando=BANDO)
        riga["fonte_ufficiale_url"] = " https://www.regione.it/bando 2026/avviso.pdf "
        atteso = "https://www.regione.it/bando%202026/avviso.pdf"
        assert fonte_ufficiale_pubblicabile(riga) == (atteso, "www.regione.it")
        assert calcola_cta(riga, []).url == atteso
        assert calcola_link_fonte(riga).url == atteso

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.regio ne.it/bando",
            "https://www.regione.it/a.pdf https://www.regione.it/b.pdf",
            "https://www.regione.it/a b",
        ],
    )
    def test_url_con_spazio_non_codificabile_cadono_insieme(self, url):
        riga = _fonte()
        riga["fonte_ufficiale_url"] = url
        assert fonte_ufficiale_pubblicabile(riga) == (None, None)

    def test_niente_da_mostrare(self):
        assert calcola_link_fonte(_riga()) is None
        assert calcola_link_fonte(_riga(link_bando="javascript:void(0)")) is None

    def test_candidatura_non_e_fonte(self):
        assert calcola_link_fonte(_riga(link_candidatura=MODULO)) is None


# ---------------------------------------------------------------- allegati


def _allegati(riga: dict, link: list[dict] | None = None) -> list[tuple]:
    return [
        (a.url, a.etichetta, a.tipo, a.formato) for a in calcola_allegati(riga, link or [])
    ]


class TestAllegati:
    def test_ordine_righe_per_id_poi_jsonb(self):
        # Le righe `atto` e `allegato` insieme, per id: il tipo non raggruppa.
        link = [
            _link(5, "allegato", "https://x.it/b.pdf"),
            _link(1, "allegato", "https://x.it/a.pdf"),
            _link(2, "atto", "https://x.it/delibera.pdf"),
            _link(0, "candidatura", "https://x.it/domanda"),
        ]
        riga = _riga(allegati=[
            {"label": "Z", "url": "https://x.it/z.pdf", "tipo": "modulo"},
            {"label": "Y", "url": "https://x.it/y.pdf", "tipo": None},
        ])
        allegati = _allegati(riga, link)
        assert [u for u, *_ in allegati] == [
            "https://x.it/a.pdf",
            "https://x.it/delibera.pdf",
            "https://x.it/b.pdf",
            "https://x.it/z.pdf",
            "https://x.it/y.pdf",
        ]
        assert [t for _, _, t, _ in allegati] == [
            "allegato", "atto", "allegato", "allegato", "allegato"
        ]

    def test_tipo_dalla_riga_allegato_per_il_jsonb(self):
        link = [_link(1, "atto", "https://x.it/a.pdf"), _link(2, "allegato", "https://x.it/b")]
        riga = _riga(allegati=[
            {"label": "M", "url": "https://x.it/m.pdf", "tipo": "pdf"},
            {"label": "N", "url": "https://x.it/n.pdf"},
        ])
        assert [t for _, _, t, _ in _allegati(riga, link)] == [
            "atto", "allegato", "allegato", "allegato"
        ]

    def test_doppione_con_barra_finale_vince_bando_link(self):
        link = [_link(1, "allegato", "https://x.it/doc/", etichetta="Dalla tabella")]
        riga = _riga(allegati=[{"label": "Dal jsonb", "url": "https://x.it/doc", "tipo": "x"}])
        assert _allegati(riga, link) == [("https://x.it/doc/", "Dalla tabella", "allegato", None)]

    @pytest.mark.parametrize("etichetta", [None, "", "   "])
    def test_etichetta_vuota_completata_dal_jsonb(self, etichetta):
        link = [_link(1, "atto", "https://x.it/a.pdf", etichetta=etichetta)]
        riga = _riga(allegati=[{"label": " Decreto ", "url": "https://x.it/a.pdf"}])
        assert _allegati(riga, link) == [("https://x.it/a.pdf", "Decreto", "atto", "pdf")]

    def test_percorso_query_e_doppia_barra_distinguono(self):
        # Maiuscole nel percorso, query non di tracciamento e doppia barra
        # fanno URL diversi; una sola barra finale no.
        riga = _riga(allegati=[
            {"label": "1", "url": "https://x.it/a"},
            {"label": "2", "url": "https://x.it/A"},
            {"label": "3", "url": "https://x.it/a?v=2"},
            {"label": "4", "url": "https://x.it/a/"},
            {"label": "5", "url": "https://x.it/a//"},
        ])
        assert [e for _, e, _, _ in _allegati(riga)] == ["1", "2", "3", "5"]

    def test_spazio_nel_percorso_codificato(self):
        link = [_link(1, "allegato", "https://x.it/docs/Avviso pubblico 2026.pdf",
                      etichetta="Avviso")]
        assert _allegati(_riga(), link) == [
            ("https://x.it/docs/Avviso%20pubblico%202026.pdf", "Avviso", "allegato", "pdf")
        ]

    def test_spazio_nel_percorso_codificato_anche_dal_jsonb(self):
        riga = _riga(allegati=[{"label": "Modulo", "url": "https://x.it/Modulo A.docx"}])
        assert _allegati(riga) == [("https://x.it/Modulo%20A.docx", "Modulo", "allegato", "docx")]

    def test_doppione_con_spazio_codificato_e_grezzo(self):
        # La riga arriva già codificata, la voce del jsonb no: è lo stesso file.
        link = [_link(1, "allegato", "https://x.it/Avviso%20pubblico.pdf", etichetta=None)]
        riga = _riga(allegati=[
            {"label": "Avviso", "url": "https://x.it/Avviso pubblico.pdf", "tipo": "pdf"},
        ])
        assert _allegati(riga, link) == [
            ("https://x.it/Avviso%20pubblico.pdf", "Avviso", "allegato", "pdf")
        ]
        # vale anche al contrario e dentro il solo jsonb
        link = [_link(1, "atto", "https://x.it/Avviso pubblico.pdf", etichetta="Atto")]
        riga = _riga(allegati=[
            {"label": "A", "url": "https://x.it/Avviso%20pubblico.pdf"},
            {"label": "B", "url": "https://x.it/Avviso pubblico.pdf"},
        ])
        assert _allegati(riga, link) == [
            ("https://x.it/Avviso%20pubblico.pdf", "Atto", "atto", "pdf")
        ]

    def test_allegato_con_due_url_o_spazio_nell_host_scartato(self):
        link = [
            _link(1, "allegato", "https://x.it/a.pdf https://x.it/b.pdf"),
            _link(2, "allegato", "https://x .it/a.pdf"),
            _link(3, "allegato", "https://x.it/ok.pdf"),
        ]
        assert [u for u, *_ in _allegati(_riga(), link)] == ["https://x.it/ok.pdf"]

    def test_doppioni_nel_jsonb(self):
        riga = _riga(allegati=[
            {"label": "", "url": "https://x.it/a.pdf"},
            {"label": "Secondo", "url": "https://x.it/a.pdf"},
        ])
        assert _allegati(riga) == [("https://x.it/a.pdf", "Secondo", "allegato", "pdf")]

    def test_chiavi_alternative_del_jsonb(self):
        riga = _riga(allegati=[
            {"nome": "Per nome", "link": "https://x.it/n.pdf"},
            {"titolo": "Per titolo", "url": "https://x.it/t.pdf"},
        ])
        assert [(u, e) for u, e, _, _ in _allegati(riga)] == [
            ("https://x.it/n.pdf", "Per nome"),
            ("https://x.it/t.pdf", "Per titolo"),
        ]

    @pytest.mark.parametrize("allegati", [None, "[]", {"url": "https://x.it/a"}, 3])
    def test_jsonb_non_lista(self, allegati):
        assert _allegati(_riga(allegati=allegati)) == []

    def test_voci_malformate_ignorate(self):
        riga = _riga(allegati=[None, "https://x.it/a.pdf", {"label": "senza url"},
                               {"url": 42}, {"url": "https://x.it/ok.pdf"}])
        assert [u for u, *_ in _allegati(riga)] == ["https://x.it/ok.pdf"]

    def test_url_scartati_non_si_mostrano(self):
        link = [
            _link(1, "atto", "https://www.youtube.com/watch?v=1"),
            _link(2, "allegato", "javascript:alert(1)"),
        ]
        riga = _riga(allegati=[
            {"label": "Concorrente", "url": "https://www.obiettivoeuropa.com/x.pdf"},
            {"label": "Aggregatore", "url": "https://sub.contributiregione.it/x.pdf"},
            {"label": "Buono", "url": "https://www.regione.it/x.pdf"},
        ])
        assert [e for _, e, _, _ in _allegati(riga, link)] == ["Buono"]

    @pytest.mark.parametrize(
        ("etichetta", "attese"),
        [
            # Stessa etichetta su due allegati: la seconda prende un numero.
            ("Scarica da www.obiettivoeuropa.com/bandi/x", ["Scarica da", "Scarica da (2)"]),
            # Etichetta che resta vuota dopo la pulizia: vale il nome del file.
            ("obiettivoeuropa.com", ["a", "b"]),
            ("Delibera n. 12", ["Delibera n. 12", "Delibera n. 12 (2)"]),
        ],
    )
    def test_etichetta_senza_menzioni_dei_domini_esclusi(self, etichetta, attese):
        link = [_link(1, "atto", "https://x.it/a.pdf", etichetta=etichetta)]
        riga = _riga(allegati=[{"label": etichetta, "url": "https://x.it/b.pdf"}])
        assert [e for _, e, _, _ in _allegati(riga, link)] == attese

    def test_etichetta_assente_nome_del_file(self):
        assert _allegati(_riga(allegati=[{"url": "https://x.it/a.pdf"}])) == [
            ("https://x.it/a.pdf", "a", "allegato", "pdf")
        ]

    def test_doppione_normalizzato_vince_bando_link_con_etichetta_del_jsonb(self):
        # Riga senza etichetta e voce del jsonb equivalente dopo la
        # normalizzazione: un solo allegato, URL della riga, etichetta del jsonb.
        link = [_link(1, "allegato", "https://www.ente.it/a.pdf")]
        riga = _riga(allegati=[
            {"label": "Avviso", "url": "https://ENTE.it/a.pdf#p2"},
            {"label": "Altro", "url": "https://ente.it/a.pdf?utm_source=x"},
            {"label": "Ancora", "url": "https://ente.it:443/a.pdf"},
        ])
        assert _allegati(riga, link) == [
            ("https://www.ente.it/a.pdf", "Avviso", "allegato", "pdf")
        ]


class TestEtichettaDiRipiego:
    """Etichetta: `etichetta` della riga, poi `label` del jsonb, poi il nome
    del file dall'URL, infine «Allegato»: mai vuota."""

    @pytest.mark.parametrize(
        ("url", "attesa"),
        [
            ("https://ente.it/doc/Avviso%20pubblico.pdf", "Avviso pubblico"),
            ("https://ente.it/doc/Avviso pubblico.pdf", "Avviso pubblico"),
            ("https://x.it/modulo.DOCX", "modulo"),
            ("https://x.it/archivio.tar.gz", "archivio.tar.gz"),  # estensione non nota
            ("https://x.it/scarica?id=1", "scarica"),  # senza query
            ("https://x.it/cartella/", "cartella"),
            ("https://x.it/a.pdf#p2", "a"),
            ("https://x.it/", ETICHETTA_RIPIEGO),
            ("https://x.it", ETICHETTA_RIPIEGO),
            ("https://x.it/.pdf", ETICHETTA_RIPIEGO),
            ("https://x.it/docs/bando%20obiettivoeuropa.com.pdf", "bando"),
            # Caratteri di controllo e di direzione del testo (Cc, Cf) via.
            ("https://x.it/bando%E2%80%AEfdp.exe", "bandofdp.exe"),
            ("https://x.it/a%00b%07c.pdf", "abc"),
            ("https://x.it/%E2%80%AE.pdf", ETICHETTA_RIPIEGO),
            ("https://x.it/a%E2%80%8Bb.pdf", "ab"),
        ],
    )
    def test_nome_del_file(self, url, attesa):
        [(_, etichetta, _, _)] = _allegati(_riga(allegati=[{"url": url}]))
        assert etichetta == attesa

    def test_ripieghi_uguali_numerati(self):
        riga = _riga(allegati=[
            {"url": "https://x.it/scarica?id=1"},
            {"url": "https://x.it/scarica?id=2"},
            {"url": "https://x.it/"},
            {"url": "https://x.it/scarica?id=3"},
            {"url": "https://x.it/?v=2"},
        ])
        assert [e for _, e, _, _ in _allegati(riga)] == [
            "scarica", "scarica (2)", ETICHETTA_RIPIEGO, "scarica (3)", f"{ETICHETTA_RIPIEGO} (2)"
        ]

    def test_etichette_sempre_distinte_anche_quelle_del_catalogo(self):
        link = [
            _link(1, "allegato", "https://x.it/a.pdf", etichetta="scarica"),
            _link(2, "allegato", "https://x.it/b.pdf", etichetta="scarica"),
            _link(3, "allegato", "https://x.it/scarica?id=1"),
        ]
        assert [e for _, e, _, _ in _allegati(_riga(), link)] == [
            "scarica", "scarica (2)", "scarica (3)"
        ]

    def test_ripiego_prima_e_catalogo_dopo(self):
        link = [
            _link(1, "allegato", "https://x.it/scarica?id=1"),
            _link(2, "allegato", "https://x.it/a.pdf", etichetta="scarica"),
        ]
        assert [e for _, e, _, _ in _allegati(_riga(), link)] == ["scarica", "scarica (2)"]

    def test_primo_numero_libero_contando_quelle_gia_numerate(self):
        link = [
            _link(1, "allegato", "https://x.it/a.pdf", etichetta="scarica"),
            _link(2, "allegato", "https://x.it/b.pdf", etichetta="scarica (2)"),
            _link(3, "allegato", "https://x.it/c.pdf", etichetta="scarica"),
            _link(4, "allegato", "https://x.it/d.pdf", etichetta="scarica (4)"),
            _link(5, "allegato", "https://x.it/scarica?id=9"),
        ]
        etichette = [e for _, e, _, _ in _allegati(_riga(), link)]
        assert etichette == ["scarica", "scarica (2)", "scarica (3)", "scarica (4)", "scarica (5)"]
        assert len(set(etichette)) == len(etichette)

    def test_nome_del_file_lungo_troncato(self):
        url = "https://x.it/" + "a" * 300 + ".pdf"
        [(_, etichetta, _, _)] = _allegati(_riga(allegati=[{"url": url}]))
        assert etichetta == "a" * 200

    def test_label_del_doppione_vince_sul_nome_del_file(self):
        link = [_link(1, "allegato", "https://x.it/Avviso.pdf")]
        riga = _riga(allegati=[{"label": "Avviso pubblico 2026", "url": "https://x.it/Avviso.pdf"}])
        assert _allegati(riga, link) == [
            ("https://x.it/Avviso.pdf", "Avviso pubblico 2026", "allegato", "pdf")
        ]

    def test_riga_con_etichetta_non_usa_il_nome_del_file(self):
        link = [_link(1, "atto", "https://x.it/DD_123.pdf", etichetta="Determina 123")]
        assert _allegati(_riga(), link) == [
            ("https://x.it/DD_123.pdf", "Determina 123", "atto", "pdf")
        ]

    @pytest.mark.parametrize("etichetta", [None, 42])
    def test_il_modello_esige_l_etichetta(self, etichetta):
        # L'API promette un'etichetta sempre presente: il modello lo impone.
        with pytest.raises(ValidationError):
            AllegatoScheda(url="https://x.it/a.pdf", etichetta=etichetta)
        with pytest.raises(ValidationError):
            AllegatoScheda(url="https://x.it/a.pdf")
        assert all(isinstance(a.etichetta, str) and a.etichetta for a in calcola_allegati(
            _riga(allegati=[{"url": "https://x.it/"}, {"url": "https://x.it/b.pdf"}]),
            [_link(1, "allegato", "https://x.it/c.pdf")],
        ))


class TestAllegatiEPulsante:
    """Un allegato con la stessa chiave del pulsante principale non si ripete
    fra gli allegati; se escluderlo lo decide il chiamante (`map_detail`:
    solo quando il pulsante viene mostrato)."""

    @pytest.mark.parametrize(
        "url",
        [
            MODULO,
            MODULO + "/",
            MODULO + "#p1",
            "https://www.servizi.regione.it/modulo",
            "https://servizi.regione.it/modulo?utm_source=x",
        ],
    )
    def test_riga_uguale_al_pulsante_esclusa(self, url):
        link = [_link(1, "allegato", url, etichetta="Modulo"), _link(2, "atto", "https://x.it/a.pdf")]
        assert [a.url for a in calcola_allegati(_riga(), link, escludi=MODULO)] == [
            "https://x.it/a.pdf"
        ]

    def test_voce_del_jsonb_uguale_al_pulsante_esclusa(self):
        riga = _riga(allegati=[
            {"label": "Modulo", "url": MODULO},
            {"label": "Avviso", "url": "https://x.it/a.pdf"},
        ])
        assert [a.etichetta for a in calcola_allegati(riga, [], escludi=MODULO)] == ["Avviso"]

    def test_le_varianti_non_completano_l_etichetta(self):
        # Esclusione prima del dedup: nessuna variante del pulsante resta.
        link = [_link(1, "allegato", MODULO)]
        riga = _riga(allegati=[{"label": "Modulo", "url": MODULO + "#p1"}])
        assert calcola_allegati(riga, link, escludi=MODULO) == []

    @pytest.mark.parametrize("escludi", [None, "https://x.it/altro.pdf"])
    def test_senza_pulsante_o_con_pulsante_diverso_resta(self, escludi):
        link = [_link(1, "allegato", MODULO, etichetta="Modulo")]
        assert [a.url for a in calcola_allegati(_riga(), link, escludi=escludi)] == [MODULO]


class TestChiaveUrlCatalogo:
    """Chiave dei doppioni: la normalizzazione del catalogo replicata
    (contratto DB bandi §5.1)."""

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("https://www.ente.it/a.pdf", "https://ente.it/a.pdf"),
            ("https://ENTE.it/a.pdf", "https://ente.it/a.pdf"),
            ("HTTPS://ente.it/a.pdf", "https://ente.it/a.pdf"),
            ("https://ente.it:443/a.pdf", "https://ente.it/a.pdf"),
            ("http://ente.it:80/a.pdf", "http://ente.it/a.pdf"),
            ("https://ente.it/a.pdf#p2", "https://ente.it/a.pdf"),
            ("https://ente.it/a.pdf?utm_source=x", "https://ente.it/a.pdf"),
            ("https://ente.it/a.pdf?utm_source=x&a=1", "https://ente.it/a.pdf?a=1"),
            ("https://ente.it/a?a=1&fbclid=z&gclid=y&msclkid=w&_ga=v", "https://ente.it/a?a=1"),
            ("https://ente.it/a/", "https://ente.it/a"),
            ("  https://ente.it/a.pdf  ", "https://ente.it/a.pdf"),
        ],
    )
    def test_uguali(self, a, b):
        assert chiave_url_catalogo(a) == chiave_url_catalogo(b)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("http://ente.it/a.pdf", "https://ente.it/a.pdf"),
            ("https://ente.it/A.pdf", "https://ente.it/a.pdf"),
            ("https://ente.it/a?v=1", "https://ente.it/a?v=2"),
            ("https://ente.it/a?v=1", "https://ente.it/a"),
            ("https://ente.it/a?a", "https://ente.it/a?a="),
            ("https://ente.it/a?q=a/b", "https://ente.it/a?q=a%2Fb"),
            ("https://ente.it/a//", "https://ente.it/a"),
            ("https://ente.it:8443/a", "https://ente.it/a"),
            ("https://ente.it/a?utm=1", "https://ente.it/a"),  # non è `utm_*`
            ("https://sub.ente.it/a", "https://ente.it/a"),
        ],
    )
    def test_diversi(self, a, b):
        assert chiave_url_catalogo(a) != chiave_url_catalogo(b)

    def test_parametri_nell_ordine_originale_senza_ricodifica(self):
        assert chiave_url_catalogo("https://ente.it/a?b=2&utm_medium=m&a=1%202") == (
            "https://ente.it/a?b=2&a=1%202"
        )

    def test_url_non_analizzabile_resta_col_solo_trim(self):
        assert chiave_url_catalogo(" https://ente.it:abc/a ") == "https://ente.it:abc/a"


class TestFormato:
    @pytest.mark.parametrize(
        ("content_type", "atteso"),
        [
            ("application/pdf", "pdf"),
            ("application/pdf; charset=binary", "pdf"),
            ("APPLICATION/PDF", "pdf"),
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
            ("application/vnd.ms-excel", "xls"),
            ("application/zip", "zip"),
            ("application/pkcs7-mime", "p7m"),
        ],
    )
    def test_da_content_type(self, content_type, atteso):
        link = [_link(1, "atto", "https://x.it/senza-estensione", content_type=content_type)]
        assert _allegati(_riga(), link)[0][3] == atteso

    @pytest.mark.parametrize(
        ("url", "atteso"),
        [
            ("https://x.it/a.pdf", "pdf"),
            ("https://x.it/A.PDF", "pdf"),
            ("https://x.it/a.pdf?download=1#p2", "pdf"),
            ("https://x.it/modulo.docx", "docx"),
            ("https://x.it/firmato.pdf.p7m", "p7m"),
            ("https://x.it/nome%20con%20spazi.xlsx", "xlsx"),
            ("https://x.it/pagina.html", None),
            ("https://x.it/cartella/", None),
            ("https://x.it/", None),
            ("https://x.it/v1.2/documento", None),
        ],
    )
    def test_da_estensione(self, url, atteso):
        assert _allegati(_riga(allegati=[{"url": url}]))[0][3] == atteso

    @pytest.mark.parametrize(
        ("url", "tipo", "atteso"),
        [
            ("https://x.it/scarica?id=1", "PDF", "pdf"),
            ("https://x.it/scarica?id=1", " docx ", "docx"),
            ("https://x.it/scarica?id=1", ".xlsx", "xlsx"),
            ("https://x.it/modulo.pdf", "zip", "zip"),  # il formato dichiarato vince
            ("https://x.it/modulo.pdf", "bando", "pdf"),  # sconosciuto: estensione
            ("https://x.it/modulo.pdf", None, "pdf"),
            ("https://x.it/scarica?id=1", "html", None),
            ("https://x.it/scarica?id=1", 3, None),
        ],
    )
    def test_formato_dal_tipo_del_jsonb(self, url, tipo, atteso):
        assert _allegati(_riga(allegati=[{"url": url, "tipo": tipo}]))[0][2:] == (
            "allegato", atteso
        )

    def test_content_type_sconosciuto_ripiega_sull_estensione(self):
        link = [_link(1, "atto", "https://x.it/a.odt", content_type="application/octet-stream")]
        assert _allegati(_riga(), link)[0][3] == "odt"

    def test_content_type_html_senza_estensione(self):
        link = [_link(1, "atto", "https://x.it/atto", content_type="text/html")]
        assert _allegati(_riga(), link)[0][3] is None


# ------------------------------------------------------ lettura bando_link


class RealSecondary:
    def __init__(self, client):
        self._client = client

    def table(self, name: str):
        return self._client.from_(name)


@pytest.fixture
async def postgrest_reale(monkeypatch):
    """(secondario, registro, stato): builder veri di postgrest-py, solo
    `execute` intercettato; `stato["righe"]` o `stato["errore"]`."""
    from postgrest import AsyncPostgrestClient

    registro: list = []
    stato: dict = {"righe": [], "errore": None}

    async def execute(self):
        tabella = str(self.request.path).rsplit("/", 1)[-1]
        registro.append((tabella, self.request.params.multi_items()))
        if stato["errore"] is not None:
            raise stato["errore"]
        return SimpleNamespace(data=stato["righe"])

    async with AsyncPostgrestClient("http://x") as client:
        builder = type(client.from_("bando_link").select("id"))
        monkeypatch.setattr(builder, "execute", execute)
        yield RealSecondary(client), registro, stato


class TestCaricaLinkScheda:
    async def test_richiesta_esatta(self, postgrest_reale):
        db, registro, stato = postgrest_reale
        stato["righe"] = [_link(1, "atto", "https://x.it/a.pdf")]

        assert await carica_link_scheda(db, 1) == stato["righe"]

        assert registro == [(
            "bando_link",
            [
                ("select", BANDO_LINK_SELECT),
                ("bando_id", "eq.1"),
                ("tipo", "in.(candidatura,portale,atto,allegato)"),
                ("order", "id.asc"),
                ("limit", "200"),
            ],
        )]
        # Colonne per nome: `select=*` su bando_link risponde 42501.
        assert "*" not in BANDO_LINK_SELECT
        # `ultimo_visto_at` non decide nulla nella scheda: non si legge.
        assert "ultimo_visto_at" not in BANDO_LINK_SELECT

    async def test_scarta_righe_di_altri_bandi_e_malformate(self, postgrest_reale):
        db, _, stato = postgrest_reale
        buona = _link(1, "atto", "https://x.it/a.pdf")
        stato["righe"] = [buona, {**buona, "bando_id": 2}, "x", None]
        assert await carica_link_scheda(db, 1) == [buona]

    async def test_risposta_vuota(self, postgrest_reale):
        db, _, stato = postgrest_reale
        stato["righe"] = None
        assert await carica_link_scheda(db, 1) == []

    @pytest.mark.parametrize(
        ("errore", "codice"),
        [
            (APIError({"message": "https://x.it/segreto", "code": "42501", "hint": None,
                       "details": None}), "42501"),
            (APIError({"message": "x", "code": None, "hint": None, "details": None}),
             "APIError"),
            (httpx.ReadTimeout("timeout"), "ReadTimeout"),
            (ValueError("json non valido"), "ValueError"),
        ],
    )
    async def test_errore_none_e_warning_senza_url(self, postgrest_reale, caplog, errore, codice):
        db, _, stato = postgrest_reale
        stato["errore"] = errore
        with caplog.at_level(logging.WARNING, logger="bandofit.bando_scheda_link"):
            assert await carica_link_scheda(db, 7) is None
        [record] = caplog.records
        assert record.levelno == logging.WARNING
        messaggio = record.getMessage()
        assert f"codice={codice}" in messaggio
        assert "bando_id=7" in messaggio
        assert "segreto" not in messaggio
