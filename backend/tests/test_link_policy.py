"""Test del filtro dei link verso domini esclusi (concorrenti).

Il dominio bloccato non deve mai uscire dall'API: né dai link diretti
(`link_bando`/`link_candidatura`), né dagli allegati, né dai segmenti
«link» annidati dentro `contenuto`. In più, il filtro dei link su tutte le
fonti della scheda (`link_pubblicabile`, `host_pubblicabile`).
"""

from types import SimpleNamespace

import pytest

from app.services.bandi_service import (
    fetch_bando_by_slug,
    fetch_bando_for_ai,
    map_detail,
    normalize_contenuto,
)
from app.services.link_policy import (
    BLOCKED_LINK_HOSTS,
    DOMINI_ESCLUSI,
    host_pubblicabile,
    is_blocked_link,
    link_pubblicabile,
    normalizza_host,
    scrub_bando_row,
    scrub_text_mentions,
)


# Varianti del dominio escluso che un browser porta comunque al dominio.
VARIANTI_ESCLUSO = [
    "https://WWW.ObiettivoEuropa.COM/x",
    "https://www.obiettivoeuropa.com./x",
    "https://www.obiettivoeuropa.com:443/x",
    "https://obiettivoeuropa%2Ecom/x",
    "https://www.obiettivoeuropa%2ecom/x",
    "https://www.obiettivoeuropa.com%2e/x",
    "https://ｏｂｉｅｔｔｉｖｏｅｕｒｏｐａ.com/x",
    "https://www.obiettivoeuropa\u3002com/x",
    "https://www.\u1d52biettivoeuropa.com/x",
]
IDN_LEGITTIMO = "https://www.citt\u00e0.it/bando"


class TestIsBlockedLink:
    @pytest.mark.parametrize("url", VARIANTI_ESCLUSO)
    def test_varianti_del_dominio_escluso(self, url):
        assert is_blocked_link(url)

    def test_host_non_normalizzabile_escluso(self):
        assert is_blocked_link("https://\u1da0acebook.com/x")
        assert is_blocked_link("https://www.\u1da0.it/x")

    def test_dominio_internazionale_ammesso(self):
        assert not is_blocked_link(IDN_LEGITTIMO)

    def test_link_non_web_invariati(self):
        # Nessun host da normalizzare o host che non è il dominio escluso.
        assert not is_blocked_link("tel:+39 06 1234")
        assert not is_blocked_link("mailto:info@regione.it")
        assert not is_blocked_link("/bandi/x")
        assert not is_blocked_link("#sezione")

    def test_blocca_dominio_esatto_e_sottodomini(self):
        assert is_blocked_link("https://obiettivoeuropa.com/bandi/x")
        assert is_blocked_link("https://www.obiettivoeuropa.com/bandi/x")
        assert is_blocked_link("http://api.obiettivoeuropa.com/call/1")

    def test_case_insensitive_e_trailing_dot(self):
        assert is_blocked_link("https://WWW.ObiettivoEuropa.COM/bandi/x")
        assert is_blocked_link("https://www.obiettivoeuropa.com./bandi/x")

    def test_blocca_forme_sciatte_ma_cliccabili(self):
        # I browser normalizzano backslash e tollerano schemi degradati:
        # il filtro deve reggere le stesse forme.
        assert is_blocked_link("https:/www.obiettivoeuropa.com/bandi/x")
        assert is_blocked_link("https:\\\\www.obiettivoeuropa.com\\bandi\\x")
        assert is_blocked_link("//www.obiettivoeuropa.com/bandi/x")
        assert is_blocked_link("www.obiettivoeuropa.com/bandi/x")
        assert is_blocked_link("obiettivoeuropa.com")
        assert is_blocked_link("https://www.obiettivoeuropa.com:443/x")

    def test_non_blocca_host_simili(self):
        # Il confronto è sull'host, non substring sull'URL intero.
        assert not is_blocked_link("https://notobiettivoeuropa.com/x")
        assert not is_blocked_link("https://obiettivoeuropa.com.evil.com/x")
        assert not is_blocked_link("https://example.com/?ref=obiettivoeuropa.com")

    def test_non_blocca_host_legittimi(self):
        assert not is_blocked_link("https://bandi.regione.piemonte.it/x")
        assert not is_blocked_link("https://www.lazioeuropa.it/bandi/y")

    def test_host_nudo(self):
        # `fonte_ufficiale_host` è un host senza schema né percorso.
        assert is_blocked_link("obiettivoeuropa.com")
        assert is_blocked_link("www.obiettivoeuropa.com")
        assert is_blocked_link("WWW.ObiettivoEuropa.com.")
        assert not is_blocked_link("bandi.regione.piemonte.it")
        assert not is_blocked_link("obiettivoeuropa.com.evil.com")
        assert not is_blocked_link("notobiettivoeuropa.com")

    def test_valori_non_url_non_bloccati(self):
        assert not is_blocked_link(None)
        assert not is_blocked_link("")
        assert not is_blocked_link("non è un url")
        assert not is_blocked_link(42)
        assert not is_blocked_link(["https://www.obiettivoeuropa.com"])


BLOCKED = "https://www.obiettivoeuropa.com/bandi/qualcosa"
OK = "https://bandi.regione.piemonte.it/bando/1"


class TestScrubLinkDiretti:
    def test_azzera_solo_i_link_bloccati(self):
        row = scrub_bando_row({"link_bando": BLOCKED, "link_candidatura": OK})
        assert row["link_bando"] is None
        assert row["link_candidatura"] == OK

    def test_link_assenti_o_null_restano_tali(self):
        row = scrub_bando_row({"link_bando": None})
        assert row["link_bando"] is None
        assert "link_candidatura" not in row

    def test_non_muta_la_riga_originale(self):
        original = {"link_bando": BLOCKED, "contenuto": {"sections": []}}
        scrub_bando_row(original)
        assert original["link_bando"] == BLOCKED


def _fonte(url, host) -> dict:
    return {
        "fonte_ufficiale_url": url,
        "fonte_ufficiale_host": host,
        "fonte_ufficiale_tipo": "portale_pubblico",
        "fonte_ufficiale_stato": "trovata",
        "fonte_ufficiale_verificata_at": "2026-09-20T10:00:00+00:00",
    }


class TestScrubFonteUfficiale:
    def test_url_bloccato_azzera_url_e_host(self):
        row = scrub_bando_row(_fonte(BLOCKED, "www.obiettivoeuropa.com"))
        assert row["fonte_ufficiale_url"] is None
        assert row["fonte_ufficiale_host"] is None
        # Gli altri campi della fonte restano: decide il frontend cosa mostrare.
        assert row["fonte_ufficiale_tipo"] == "portale_pubblico"
        assert row["fonte_ufficiale_stato"] == "trovata"
        assert row["fonte_ufficiale_verificata_at"] == "2026-09-20T10:00:00+00:00"

    def test_url_bloccato_con_host_legittimo_azzera_entrambi(self):
        row = scrub_bando_row(_fonte(BLOCKED, "bandi.regione.piemonte.it"))
        assert row["fonte_ufficiale_url"] is None
        assert row["fonte_ufficiale_host"] is None

    def test_solo_host_bloccato_azzera_entrambi(self):
        # L'host è l'etichetta del pulsante: non deve comparire nemmeno lì.
        row = scrub_bando_row(_fonte(OK, "www.obiettivoeuropa.com"))
        assert row["fonte_ufficiale_url"] is None
        assert row["fonte_ufficiale_host"] is None
        assert row["fonte_ufficiale_stato"] == "trovata"

    def test_host_nudo_senza_url_bloccato(self):
        row = scrub_bando_row(_fonte(None, "obiettivoeuropa.com"))
        assert row["fonte_ufficiale_url"] is None
        assert row["fonte_ufficiale_host"] is None

    def test_fonte_legittima_intatta(self):
        originale = _fonte(OK, "bandi.regione.piemonte.it")
        assert scrub_bando_row(originale) == originale

    @pytest.mark.parametrize("url", [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "  JavaScript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<b>ciao</b>",
        "www.regione.piemonte.it/bando/1",  # senza schema
        "//www.regione.piemonte.it/bando/1",  # senza schema
        "ftp://www.regione.piemonte.it/bando/1",
        "",
        "http://[::1",  # urlsplit solleva ValueError
        42,  # non stringa
    ])
    def test_url_non_http_azzera_url_e_host(self, url):
        row = scrub_bando_row(_fonte(url, "www.regione.piemonte.it"))
        assert row["fonte_ufficiale_url"] is None
        assert row["fonte_ufficiale_host"] is None
        assert row["fonte_ufficiale_tipo"] == "portale_pubblico"
        assert row["fonte_ufficiale_stato"] == "trovata"

    @pytest.mark.parametrize("url", [
        "https://www.regione.piemonte.it/bando/1",
        "http://www.regione.piemonte.it/bando/1",
        "HTTPS://www.regione.piemonte.it/bando/1",
        "  https://www.regione.piemonte.it/bando/1  ",
    ])
    def test_url_http_o_https_intatto(self, url):
        originale = _fonte(url, "www.regione.piemonte.it")
        assert scrub_bando_row(originale) == originale

    def test_url_none_lascia_l_host(self):
        row = scrub_bando_row(_fonte(None, "www.regione.piemonte.it"))
        assert row["fonte_ufficiale_url"] is None
        assert row["fonte_ufficiale_host"] == "www.regione.piemonte.it"

    def test_schema_non_controllato_su_link_bando_e_candidatura(self):
        # Il controllo dello schema vale solo per la fonte ufficiale: i link
        # storici mantengono il comportamento di prima (solo domini esclusi).
        row = scrub_bando_row(
            {"link_bando": "www.regione.piemonte.it/x", "link_candidatura": "mailto:a@b.it"}
        )
        assert row["link_bando"] == "www.regione.piemonte.it/x"
        assert row["link_candidatura"] == "mailto:a@b.it"

    def test_campi_assenti_non_aggiunti(self):
        row = scrub_bando_row({"link_bando": OK})
        assert "fonte_ufficiale_url" not in row
        assert "fonte_ufficiale_host" not in row

    def test_non_muta_la_riga_originale(self):
        originale = _fonte(BLOCKED, "www.obiettivoeuropa.com")
        scrub_bando_row(originale)
        assert originale["fonte_ufficiale_url"] == BLOCKED
        assert originale["fonte_ufficiale_host"] == "www.obiettivoeuropa.com"


class TestScrubAllegati:
    def test_rimuove_allegati_bloccati_su_url_e_link(self):
        row = scrub_bando_row(
            {
                "allegati": [
                    {"url": OK, "label": "Determina"},
                    {"url": BLOCKED, "label": "Scheda"},
                    {"link": BLOCKED, "nome": "Vecchio formato"},
                ]
            }
        )
        assert row["allegati"] == [{"url": OK, "label": "Determina"}]

    def test_tollera_allegati_malformati(self):
        # Voci non-dict: non devono far esplodere il filtro né sparire.
        row = scrub_bando_row({"allegati": ["stringa", None, {"url": OK}]})
        assert row["allegati"] == ["stringa", None, {"url": OK}]

    def test_allegati_non_lista_passthrough(self):
        assert scrub_bando_row({"allegati": None})["allegati"] is None


class TestScrubContenuto:
    @pytest.mark.parametrize("url", VARIANTI_ESCLUSO)
    def test_segmento_link_con_variante_degrada_a_testo(self, url):
        contenuto = {"sections": [{"type": "paragraph", "segments": [
            {"kind": "link", "url": url, "text": "Fondazione Varesotto"},
        ]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "Fondazione Varesotto"}
        ]

    @pytest.mark.parametrize("url", VARIANTI_ESCLUSO)
    def test_link_diretti_con_variante_azzerati(self, url):
        row = scrub_bando_row({"link_bando": url, "link_candidatura": url,
                               "allegati": [{"url": url, "label": "x"}]})
        assert row["link_bando"] is None
        assert row["link_candidatura"] is None
        assert row["allegati"] == []

    def test_segmento_link_internazionale_intatto(self):
        segmento = {"kind": "link", "url": IDN_LEGITTIMO, "text": "Comune"}
        contenuto = {"sections": [{"type": "paragraph", "segments": [dict(segmento)]}]}
        row = scrub_bando_row({"contenuto": contenuto, "link_bando": IDN_LEGITTIMO})
        assert row["contenuto"]["sections"][0]["segments"] == [segmento]
        assert row["link_bando"] == IDN_LEGITTIMO

    def test_segmento_link_bloccato_degrada_a_testo(self):
        # Ancora in mezzo alla frase: il testo resta, il link no.
        contenuto = {
            "sections": [
                {
                    "type": "paragraph",
                    "segments": [
                        {"kind": "text", "text": "Fonte: "},
                        {"kind": "link", "url": BLOCKED, "text": "Fondazione Varesotto"},
                    ],
                }
            ]
        }
        row = scrub_bando_row({"contenuto": contenuto})
        segments = row["contenuto"]["sections"][0]["segments"]
        assert segments == [
            {"kind": "text", "text": "Fonte: "},
            {"kind": "text", "text": "Fondazione Varesotto"},
        ]

    def test_segmento_cade_se_il_dominio_e_nel_testo_visibile(self):
        contenuto = {
            "sections": [
                {
                    "type": "paragraph",
                    "segments": [
                        {"kind": "link", "url": BLOCKED, "text": "obiettivoeuropa.com - Bando"},
                        {"kind": "text", "text": "resto della frase"},
                    ],
                }
            ]
        }
        row = scrub_bando_row({"contenuto": contenuto})
        segments = row["contenuto"]["sections"][0]["segments"]
        assert segments == [{"kind": "text", "text": "resto della frase"}]

    def test_chiavi_url_alternative_href_e_link_bloccate(self):
        # Il renderer legge `href ?? url`; `link` per simmetria con gli allegati.
        for chiave in ("href", "link"):
            contenuto = {
                "sections": [
                    {
                        "type": "paragraph",
                        "segments": [{"kind": "link", chiave: BLOCKED, "text": "vedi qui"}],
                    }
                ]
            }
            row = scrub_bando_row({"contenuto": contenuto})
            assert row["contenuto"]["sections"][0]["segments"] == [
                {"kind": "text", "text": "vedi qui"}
            ], chiave

    def test_menzione_nel_testo_cade_anche_senza_link(self):
        # La regola vale per QUALUNQUE segmento, non solo per i «link».
        contenuto = {
            "sections": [
                {
                    "type": "paragraph",
                    "segments": [
                        {"kind": "text", "text": "vedi obiettivoeuropa.com per i dettagli"},
                        {"kind": "text", "text": "resto della frase"},
                    ],
                }
            ]
        }
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "resto della frase"}
        ]

    def test_kind_non_link_conservato(self):
        # Un grassetto con URL bloccato perde l'URL ma resta grassetto.
        contenuto = {
            "sections": [
                {
                    "type": "paragraph",
                    "segments": [{"kind": "bold", "url": BLOCKED, "text": "in evidenza"}],
                }
            ]
        }
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "bold", "text": "in evidenza"}
        ]

    def test_segments_annidati_in_items_e_faq(self):
        contenuto = {
            "sections": [
                {
                    "type": "bullet_list",
                    "items": [
                        {"segments": [{"kind": "link", "url": BLOCKED, "text": "voce"}]},
                        "voce semplice",
                    ],
                },
                {
                    "type": "faq",
                    "items": [
                        {
                            "q": "Dove candidarsi?",
                            "a": {"segments": [{"kind": "link", "url": BLOCKED, "text": "qui"}]},
                        }
                    ],
                },
            ]
        }
        row = scrub_bando_row({"contenuto": contenuto})
        lista, faq = row["contenuto"]["sections"]
        assert lista["items"][0]["segments"] == [{"kind": "text", "text": "voce"}]
        assert lista["items"][1] == "voce semplice"
        assert faq["items"][0]["a"]["segments"] == [{"kind": "text", "text": "qui"}]

    def test_link_legittimi_intatti(self):
        contenuto = {
            "sections": [
                {
                    "type": "paragraph",
                    "segments": [{"kind": "link", "url": OK, "text": "portale regionale"}],
                }
            ]
        }
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"] == contenuto

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.fasi.eu/x",
            "https://m.facebook.com/x",
            "https://youtu.be/x",
            "https://it.linkedin.com/company/x",
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,x",
            "mailto:a@b.it",
            "tel:+390212345",
            "ftp://ftp.regione.it/x",
            "www.regione.it/x",
            "/bandi/x",
            "https://ente.it/a\\b",
            "https://utente:segreto@ente.it/x",
            "https://ente.it/a.pdf https://ente.it/b.pdf",
            "https://en te.it/x",
        ],
    )
    @pytest.mark.parametrize("chiave", ["url", "href", "link"])
    def test_segmento_link_non_pubblicabile_degrada_a_testo(self, url, chiave):
        contenuto = {"sections": [{"type": "paragraph", "segments": [
            {"kind": "text", "text": "Scrivere a "},
            {"kind": "link", chiave: url, "text": "ufficio bandi"},
        ]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "Scrivere a "},
            {"kind": "text", "text": "ufficio bandi"},
        ]

    @pytest.mark.parametrize(
        "url",
        ["https://www.regione.it/x", "http://www.regione.it/x", "HTTPS://www.regione.it/x",
         "https://www.regione.it/x?a=1#sez"],
    )
    def test_segmento_link_pubblicabile_invariato(self, url):
        segmento = {"kind": "link", "url": url, "text": "portale"}
        contenuto = {"sections": [{"type": "paragraph", "segments": [dict(segmento)]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [segmento]

    def test_segmento_link_esce_con_l_url_del_filtro(self):
        contenuto = {"sections": [{"type": "paragraph", "segments": [
            {"kind": "link", "href": "  https://www.regione.it/avviso 2026.pdf ", "text": "avviso"},
        ]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "link", "href": "https://www.regione.it/avviso%202026.pdf", "text": "avviso"}
        ]

    def test_kind_non_link_con_url_non_pubblicabile_conservato(self):
        contenuto = {"sections": [{"type": "paragraph", "segments": [
            {"kind": "bold", "url": "mailto:a@b.it", "text": "in evidenza"},
            {"kind": "bold", "url": "https://www.regione.it/x", "text": "con link"},
        ]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "bold", "text": "in evidenza"},
            {"kind": "bold", "url": "https://www.regione.it/x", "text": "con link"},
        ]

    def test_chiave_url_nulla_vale_come_assente(self):
        segmento = {"kind": "link", "url": None, "href": OK, "text": "portale"}
        contenuto = {"sections": [{"type": "paragraph", "segments": [dict(segmento)]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [segmento]

    @pytest.mark.parametrize("valore", [123, "", "   ", ["https://www.regione.it/x"], {"a": 1}, 0])
    def test_chiave_url_presente_ma_non_valida_degrada_a_testo(self, valore):
        contenuto = {"sections": [{"type": "paragraph", "segments": [
            {"kind": "link", "url": valore, "href": OK, "text": "portale"},
        ]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "portale"}
        ]

    def test_piu_chiavi_url_tutte_o_nessuna(self):
        contenuto = {"sections": [{"type": "paragraph", "segments": [
            {"kind": "link", "href": OK, "url": "https://www.regione.it/a b", "text": "due buone"},
            {"kind": "link", "href": OK, "url": "https://t.me/canale", "text": "una esclusa"},
            {"kind": "link", "href": "mailto:a@b.it", "link": OK, "text": "una non web"},
        ]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "link", "href": OK, "url": "https://www.regione.it/a%20b",
             "text": "due buone"},
            {"kind": "text", "text": "una esclusa"},
            {"kind": "text", "text": "una non web"},
        ]

    def test_segmento_senza_chiavi_url_invariato(self):
        segmenti = [{"kind": "link", "text": "senza indirizzo"}, {"kind": "text", "text": "x"},
                    {"kind": "text", "text": "y", "url": None}]
        contenuto = {"sections": [{"type": "paragraph", "segments": [dict(s) for s in segmenti]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == segmenti

    def test_il_filtro_non_muta_il_contenuto_originale(self):
        segmento = {"kind": "link", "url": "https://www.regione.it/a b", "text": "avviso"}
        contenuto = {"sections": [{"type": "paragraph", "segments": [segmento]}]}
        scrub_bando_row({"contenuto": contenuto})
        assert segmento == {"kind": "link", "url": "https://www.regione.it/a b", "text": "avviso"}

    def test_menzioni_nel_testo_restano_sulla_lista_breve(self):
        # Il testo visibile non si tocca per i domini della lista ampia.
        contenuto = {"sections": [{"type": "paragraph", "segments": [
            {"kind": "text", "text": "seguici su x.com e t.me"},
            {"kind": "link", "url": "https://t.me/canale", "text": "canale t.me"},
        ]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "seguici su x.com e t.me"},
            {"kind": "text", "text": "canale t.me"},
        ]

    def test_contenuto_null_o_stringa_passthrough(self):
        assert scrub_bando_row({"contenuto": None})["contenuto"] is None
        # Il chiamante normalizza prima del filtro: una stringa residua
        # non viene attraversata ma nemmeno rotta.
        assert scrub_bando_row({"contenuto": "{}"})["contenuto"] == "{}"

    def test_segmenti_non_dict_tollerati(self):
        contenuto = {"sections": [{"type": "paragraph", "segments": ["testo", None]}]}
        row = scrub_bando_row({"contenuto": contenuto})
        assert row["contenuto"]["sections"][0]["segments"] == ["testo", None]

    def test_menzioni_fuori_dai_segmenti_ripulite(self):
        # Titoli di sezione, voci di elenco e risposte FAQ possono essere
        # stringhe semplici: la menzione va rimossa anche lì.
        contenuto = {
            "sections": [
                {"type": "h2", "text": "Fonte: obiettivoeuropa.com"},
                {
                    "type": "bullet_list",
                    "items": ["vedi https://www.obiettivoeuropa.com/bandi/x per info"],
                },
                {"type": "faq", "items": [{"q": "Dove?", "a": "su obiettivoeuropa.com"}]},
            ]
        }
        row = scrub_bando_row({"contenuto": contenuto})
        h2, lista, faq = row["contenuto"]["sections"]
        assert h2["text"] == "Fonte: "
        assert lista["items"] == ["vedi  per info"]
        assert faq["items"][0]["a"] == "su "
        assert "obiettivoeuropa" not in str(row)


def riga_dettaglio() -> dict:
    """Riga di dettaglio con rimandi al dominio bloccato ovunque possibile
    (contenuto doppio-encodato di proposito: il filtro deve vederlo comunque)."""
    return {
        "id": 1,
        "slug": "bando-test",
        "titolo": "Bando test",
        "titolo_breve": None,
        "descrizione_breve": None,
        "stato_bando": "aperto",
        "livello": "flash_bando",
        "data_pubblicazione": "2026-05-26",
        "data_apertura": None,
        "data_scadenza": None,
        "importo_totale_eur": None,
        "importo_max_per_progetto_eur": None,
        "ente_erogatore": None,
        "tipologie_bando": None,
        "modalita_erogazione": None,
        "bando_regioni": [],
        "area_geografica": None,
        "tematica": [],
        "link_bando": BLOCKED,
        "link_candidatura": BLOCKED,
        "contenuto": (
            '{"sections": [{"type": "paragraph", "segments": '
            '[{"kind": "link", "url": "' + BLOCKED + '", "text": "vedi"}]}]}'
        ),
        "allegati": [{"url": BLOCKED, "label": "Scheda"}],
        **_fonte(BLOCKED, "www.obiettivoeuropa.com"),
        "programmi": None,
        "bando_settori": [],
        "bando_beneficiari": [],
        "bando_codici_ateco": [],
    }


class TestDettaglioSerializzato:
    def test_il_dominio_non_esce_dal_json_del_dettaglio(self):
        # Stessa pipeline di fetch_bando_by_slug: normalizza → filtra → mappa.
        row = riga_dettaglio()
        row["contenuto"] = normalize_contenuto(row["contenuto"])
        detail = map_detail(scrub_bando_row(row), [{
            "id": 1, "bando_id": 1, "url": BLOCKED, "tipo": "candidatura",
            "etichetta": None, "content_type": None, "ultimo_visto_at": None,
        }])
        assert "obiettivoeuropa" not in detail.model_dump_json()
        assert detail.cta is None
        assert detail.link_fonte is None
        assert detail.allegati == []
        assert detail.fonte_ufficiale_url is None
        assert detail.fonte_ufficiale_host is None
        assert detail.fonte_ufficiale_stato == "trovata"


class TestScrubTextMentions:
    def test_rimuove_menzioni_e_url_dai_report_storici(self):
        report = {
            "requisiti": [
                {
                    "riferimento_bando": {
                        "testo": "Fonte: obiettivoeuropa.com - Basilicata Regimi di qualità"
                    },
                    "nota": "vedi https://www.obiettivoeuropa.com/bandi/x per dettagli",
                }
            ],
            "punteggio": 80,
        }
        pulito = scrub_text_mentions(report)
        assert "obiettivoeuropa" not in str(pulito)
        assert pulito["requisiti"][0]["nota"] == "vedi  per dettagli"
        assert pulito["punteggio"] == 80

    def test_testo_senza_menzioni_intatto(self):
        assert scrub_text_mentions("nessun riferimento al concorrente") == (
            "nessun riferimento al concorrente"
        )
        assert scrub_text_mentions(None) is None
        assert scrub_text_mentions(42) == 42


class FakeSecondary:
    """Catena select→eq→limit→execute del client PostgREST, senza rete.

    Di default ammette solo `bando_pubblico` (che restituisce `row` a ogni
    lettura) e `bando_link` (che restituisce `link`). Con `storico=[riga]`
    simula uno slug spostato: la lettura per slug su `bando_pubblico` non
    trova nulla, `bando_slug_storico` restituisce `storico` e la riletta del
    master per id restituisce `row`."""

    def __init__(self, row: dict, *, storico: list | None = None, link: list | None = None):
        self.row = row
        self.storico = storico
        self.link = link or []
        self.tabelle: list[str] = []

    def table(self, name: str):
        ammesse = {"bando_pubblico", "bando_link"}
        if self.storico is not None:
            ammesse.add("bando_slug_storico")
        assert name in ammesse
        self.tabelle.append(name)
        fake = self

        class _Query:
            per_slug = False

            def select(self, *args, **kwargs):
                return self

            def eq(self, column, value):
                if column == "slug":
                    self.per_slug = True
                return self

            @property
            def not_(self):
                return self

            def is_(self, *args):
                return self

            def in_(self, *args):
                return self

            def order(self, *args, **kwargs):
                return self

            def limit(self, *args):
                return self

            async def execute(self):
                if name == "bando_link":
                    return SimpleNamespace(data=[dict(r) for r in fake.link])
                if name == "bando_slug_storico":
                    return SimpleNamespace(data=list(fake.storico))
                if fake.storico is not None and self.per_slug:
                    return SimpleNamespace(data=[])
                return SimpleNamespace(data=[dict(fake.row)])

        return _Query()


STORICO_301 = {"slug": "vecchio-slug", "bando_id": 1, "esito": "301"}


class TestFetchApplicaIlFiltro:
    """I veri punti di applicazione: senza questi test, rimuovere le
    chiamate a scrub_bando_row non farebbe fallire la suite."""

    async def test_fetch_bando_by_slug_filtra(self):
        link = [
            {"id": i, "bando_id": 1, "url": url, "tipo": tipo, "etichetta": "x",
             "content_type": None, "ultimo_visto_at": None}
            for i, (tipo, url) in enumerate([
                ("candidatura", BLOCKED),
                ("portale", "https://m.facebook.com/regione"),
                ("atto", "https://news.infobandi.it/atto.pdf"),
                ("allegato", "https://www.youtube.com/watch?v=1"),
            ], start=1)
        ]
        detail = await fetch_bando_by_slug(
            FakeSecondary(riga_dettaglio(), link=link), "bando-test"
        )
        dump = detail.model_dump_json()
        for dominio in ("obiettivoeuropa", "facebook", "infobandi", "youtube"):
            assert dominio not in dump
        assert detail.cta is None
        assert detail.link_fonte is None
        assert detail.allegati == []

    async def test_fetch_bando_for_ai_filtra(self):
        row = await fetch_bando_for_ai(FakeSecondary(riga_dettaglio()), "bando-test")
        assert "obiettivoeuropa" not in str(row)
        assert row["link_bando"] is None
        assert row["allegati"] == []
        # Il contenuto arriva normalizzato E filtrato alla pipeline AI.
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "vedi"}
        ]
        assert row["fonte_ufficiale_url"] is None
        assert row["fonte_ufficiale_host"] is None

    async def test_fetch_bando_by_slug_filtra_anche_il_master(self):
        # Slug spostato: la riga del master passa dallo stesso filtro.
        db = FakeSecondary(riga_dettaglio(), storico=[STORICO_301])
        detail = await fetch_bando_by_slug(db, "vecchio-slug")
        assert db.tabelle == ["bando_pubblico", "bando_slug_storico", "bando_pubblico", "bando_link"]
        assert detail.slug == "bando-test"
        assert "obiettivoeuropa" not in detail.model_dump_json()
        assert detail.cta is None
        assert detail.fonte_ufficiale_url is None

    async def test_fetch_bando_for_ai_filtra_anche_il_master(self):
        db = FakeSecondary(riga_dettaglio(), storico=[STORICO_301])
        row = await fetch_bando_for_ai(db, "vecchio-slug")
        assert db.tabelle == ["bando_pubblico", "bando_slug_storico", "bando_pubblico"]
        assert row["slug"] == "bando-test"
        assert "obiettivoeuropa" not in str(row)
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "vedi"}
        ]


# ------------------------------------------- filtro dei link della scheda


class TestLinkPubblicabile:
    def test_link_ufficiale_ammesso(self):
        assert link_pubblicabile(OK) == (OK, "bandi.regione.piemonte.it")

    def test_spazi_ai_bordi_tolti(self):
        assert link_pubblicabile(f"  {OK}\n") == (OK, "bandi.regione.piemonte.it")

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JAVASCRIPT:alert(1)",
            "data:text/html,<a>x</a>",
            "mailto:bandi@regione.it",
            "ftp://ftp.regione.it/bando.pdf",
            "file:///etc/passwd",
            "www.regione.it/bando",
            "//www.regione.it/bando",
            "https:/www.regione.it/bando",
            "https:\\\\www.regione.it\\bando",
            "https://",
            "http:///percorso",
        ],
    )
    def test_solo_http_e_https_con_host(self, url):
        assert link_pubblicabile(url) is None

    def test_schema_maiuscolo_ammesso(self):
        assert link_pubblicabile("HTTPS://www.regione.it/x") == (
            "HTTPS://www.regione.it/x", "www.regione.it"
        )
        assert link_pubblicabile("http://www.regione.it/x")[1] == "www.regione.it"

    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("https://WWW.Regione.IT/x", "www.regione.it"),
            ("https://www.regione.it:8443/x", "www.regione.it"),
            ("https://www.regione.it./x", "www.regione.it"),
            ("https://www%2Eregione%2Eit/x", "www.regione.it"),
            ("https://ｗｗｗ．ｒｅｇｉｏｎｅ．ｉｔ/x", "www.regione.it"),
        ],
    )
    def test_host_normalizzato(self, url, host):
        assert link_pubblicabile(url)[1] == host

    @pytest.mark.parametrize(
        "url",
        [
            # aggregatori, sottodomini compresi
            "https://obiettivoeuropa.com/x",
            "https://www.fasi.eu/x",
            "https://europafacile.net/x",
            "https://www.contributiregione.it/x",
            "https://finanziamentinews.it/x",
            "https://bandi.it/x",
            "https://www.infobandi.it/x",
            "https://ticonsiglio.com/x",
            "https://contributieuropa.com/x",
            "https://first.aster.it/x",
            # social, video, messaggistica
            "https://www.facebook.com/regione",
            "https://m.facebook.com/regione",
            "https://instagram.com/x",
            "https://x.com/regione",
            "https://twitter.com/regione",
            "https://it.linkedin.com/company/x",
            "https://www.threads.net/@x",
            "https://pinterest.com/x",
            "https://www.tiktok.com/@x",
            "https://www.youtube.com/watch?v=1",
            "https://youtu.be/1",
            "https://vimeo.com/1",
            "https://t.me/canale",
            "https://telegram.me/canale",
            "https://wa.me/39333",
            "https://chat.whatsapp.com/x",
        ],
    )
    def test_domini_esclusi(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://WWW.FACEBOOK.COM/x",
            "https://www.facebook.com./x",
            "https://www.facebook.com:443/x",
            "https://www%2Efacebook%2Ecom/x",
            "https://ｗｗｗ．ｆａｃｅｂｏｏｋ．ｃｏｍ/x",
            "https://www.obiettivoeuropa.com%2e/x",
            "https://\u1da0acebook.com/x",
            "https://www.\u1d52biettivoeuropa.com/x",
            "https://\u1da0asi.eu/x",
        ],
    )
    def test_domini_esclusi_anche_camuffati(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize("url", VARIANTI_ESCLUSO)
    def test_varianti_del_dominio_escluso(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://box.com/a",
            "https://notfacebook.com/a",
            "https://facebook.com.regione.it/a",
            "https://www.regione.it/?ref=facebook.com",
            "https://www.bandi.regione.it/x",
            "https://tme.it/x",
        ],
    )
    def test_nessun_falso_positivo(self, url):
        assert link_pubblicabile(url) is not None

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.regione.it/a\tb",
            "https://www.regio\nne.it/a",
            "https://www.regione.it/\x00",
            "https://www.regione.it/a\u00a0b",
            "https://www.regione.it\\@facebook.com/",
            "https://www.regione.it/a\\b",
            "https://www.regione.it/a b\tc",
            "https://www.regione.it/a b\u00a0c",
            "https://www.regione.it/a b\\c",
            "https://www.regione.it/a b\x00",
        ],
    )
    def test_spazi_controllo_e_backslash(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize(
        ("url", "atteso"),
        [
            ("https://www.regione.it/a b.pdf", "https://www.regione.it/a%20b.pdf"),
            ("https://www.regione.it/a b", "https://www.regione.it/a%20b"),
            ("https://www.regione.it/avviso  2026   bis.pdf",
             "https://www.regione.it/avviso%20%202026%20%20%20bis.pdf"),
            ("https://www.regione.it/doc?nome=a b&v=1", "https://www.regione.it/doc?nome=a%20b&v=1"),
            ("https://www.regione.it/doc#parte due", "https://www.regione.it/doc#parte%20due"),
            ("https://www.regione.it/a b?q=c d#e f", "https://www.regione.it/a%20b?q=c%20d#e%20f"),
            ("HTTPS://www.regione.it/a b", "HTTPS://www.regione.it/a%20b"),
            ("https://www.regione.it/a b?", "https://www.regione.it/a%20b?"),
            ("https://www.regione.it/a b#", "https://www.regione.it/a%20b#"),
            ("  https://www.regione.it/a b.pdf \n", "https://www.regione.it/a%20b.pdf"),
            ("https://www.regione.it/a%20b c.pdf", "https://www.regione.it/a%20b%20c.pdf"),
            ("https://www.regione.it/a b?u=https://www.ente.it/c",
             "https://www.regione.it/a%20b?u=https://www.ente.it/c"),
        ],
    )
    def test_spazio_semplice_codificato(self, url, atteso):
        assert link_pubblicabile(url) == (atteso, "www.regione.it")

    def test_url_gia_codificato_invariato(self):
        url = "https://www.regione.it/a%20b.pdf"
        assert link_pubblicabile(url) == (url, "www.regione.it")

    @pytest.mark.parametrize(
        "url",
        [
            "https://ente.it/a.pdf https://fasi.eu/b",
            "https://ente.it/a.pdf https://www.regione.it/b.pdf",
            "https://ente.it/a.pdf HTTP://x.it",
            "https://ente.it/a.pdf   http://x.it",
            "https://ente.it/a b.pdf https://ente.it/c.pdf",
            "https://ente.it/?next= https://www.regione.it/",
        ],
    )
    def test_secondo_url_dopo_lo_spazio(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://en te.it/",
            "https://www.regione.it /a",
            "https:// www.regione.it/a",
            "https://www.regione.it: 80/a",
            "https://www.regione.it :80/a",
            "ht tp://www.regione.it/a",
            "ht tps://www.regione.it/a b",
            "https: //www.regione.it/a",
            "https:/ /www.regione.it/a",
            "www.regione.it/a b",
        ],
    )
    def test_spazio_nello_schema_o_nell_host(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://utente@www.regione.it/",
            "https://utente:segreto@www.regione.it/",
            "https://www.regione.it@www.facebook.com/",
        ],
    )
    def test_credenziali_nell_url(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost/x",
            "https://[::1]/x",
            "https://intranet/x",
            "https://www.regione%25.it/x",
            "https://" + "a" * 70 + ".it/x",
            "https://\U0001f130.it/x",
            "https://www.\u1da0.it/x",
        ],
    )
    def test_host_non_valido(self, url):
        assert link_pubblicabile(url) is None

    @pytest.mark.parametrize("valore", [None, "", "   ", 42, ["https://www.regione.it"]])
    def test_valori_non_stringa_o_vuoti(self, valore):
        assert link_pubblicabile(valore) is None


class TestHostPubblicabile:
    def test_host_normalizzato(self):
        assert host_pubblicabile(" WWW.Regione.it. ") == "www.regione.it"
        assert host_pubblicabile("www.regione.it:443") == "www.regione.it"

    @pytest.mark.parametrize(
        "host",
        [None, "", "www.youtube.com", "obiettivoeuropa.com", "m.facebook.com",
         "www.regione.it/percorso x", "localhost", 42],
    )
    def test_host_escluso_o_non_valido(self, host):
        assert host_pubblicabile(host) is None

    def test_normalizza_host(self):
        assert normalizza_host("WWW.Regione.IT.") == "www.regione.it"
        assert normalizza_host(None) is None
        assert normalizza_host("a..it") is None
        assert normalizza_host("\u1da0acebook.com") is None
        assert normalizza_host("www.citt\u00e0.it") == "www.xn--citt-3na.it"

    def test_host_internazionale_ammesso(self):
        assert link_pubblicabile("https://www.citt\u00e0.it/bando") == (
            "https://www.citt\u00e0.it/bando", "www.xn--citt-3na.it"
        )


class TestListeSeparate:
    def test_domini_esclusi_contengono_quelli_di_oggi(self):
        assert BLOCKED_LINK_HOSTS <= DOMINI_ESCLUSI

    def test_menzioni_nei_testi_invariate(self):
        # La denylist completa vale solo per i link della scheda: le regex
        # sulle menzioni nei testi non devono tagliare «x.com» o «t.me».
        assert BLOCKED_LINK_HOSTS == frozenset({"obiettivoeuropa.com"})
        testo = "scrivere a box.com, vedi x.com e t.me per aggiornamenti"
        assert scrub_text_mentions(testo) == testo
        assert not is_blocked_link("https://x.com/regione")
