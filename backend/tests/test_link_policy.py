"""Test del filtro dei link verso domini esclusi (concorrenti).

Il dominio bloccato non deve mai uscire dall'API: né dai link diretti
(`link_bando`/`link_candidatura`), né dagli allegati, né dai segmenti
«link» annidati dentro `contenuto`.
"""

from types import SimpleNamespace

from app.services.bandi_service import (
    fetch_bando_by_slug,
    fetch_bando_for_ai,
    map_detail,
    normalize_contenuto,
)
from app.services.link_policy import (
    is_blocked_link,
    scrub_bando_row,
    scrub_text_mentions,
)


class TestIsBlockedLink:
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
        detail = map_detail(scrub_bando_row(row))
        assert "obiettivoeuropa" not in detail.model_dump_json()
        assert detail.link_bando is None
        assert detail.link_candidatura is None
        assert detail.allegati == []


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
    """Catena select→eq→limit→execute del client PostgREST, senza rete."""

    def __init__(self, row: dict):
        self.row = row

    def table(self, name: str):
        assert name == "bando"
        fake = self

        class _Query:
            def select(self, *args, **kwargs):
                return self

            def eq(self, *args):
                return self

            def limit(self, *args):
                return self

            async def execute(self):
                return SimpleNamespace(data=[dict(fake.row)])

        return _Query()


class TestFetchApplicaIlFiltro:
    """I veri punti di applicazione: senza questi test, rimuovere le
    chiamate a scrub_bando_row non farebbe fallire la suite."""

    async def test_fetch_bando_by_slug_filtra(self):
        detail = await fetch_bando_by_slug(FakeSecondary(riga_dettaglio()), "bando-test")
        assert "obiettivoeuropa" not in detail.model_dump_json()
        assert detail.link_bando is None

    async def test_fetch_bando_for_ai_filtra(self):
        row = await fetch_bando_for_ai(FakeSecondary(riga_dettaglio()), "bando-test")
        assert "obiettivoeuropa" not in str(row)
        assert row["link_bando"] is None
        assert row["allegati"] == []
        # Il contenuto arriva normalizzato E filtrato alla pipeline AI.
        assert row["contenuto"]["sections"][0]["segments"] == [
            {"kind": "text", "text": "vedi"}
        ]
