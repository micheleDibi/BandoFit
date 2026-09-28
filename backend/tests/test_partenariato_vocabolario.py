"""Vocabolario controllato del modulo partenariati (v1, appendice A di
docs/partenariati.md).

Il test portante è l'uguaglianza tra i `Literal` degli schemi strict e le
costanti del servizio: se divergono, il modello potrebbe restituire un codice
che il resto del modulo non conosce (o viceversa)."""

import json
import re
from typing import get_args

import pytest

from app.schemas import partenariato_vocabolario as schemi
from app.services import partenariato_vocabolario as voc


@pytest.mark.parametrize(
    ("literal", "costante"),
    [
        (schemi.TipoSoggetto, voc.TIPI_SOGGETTO),
        (schemi.Competenza, voc.COMPETENZE),
        (schemi.FormaAggregazione, voc.FORME),
        (schemi.RuoloPartenariato, voc.RUOLI),
        (schemi.Responsabilita, voc.RESPONSABILITA),
        (schemi.DocumentoPartenariato, voc.DOCUMENTI),
    ],
)
def test_literal_uguali_alle_costanti_anche_nell_ordine(literal, costante):
    assert get_args(literal) == tuple(costante)


def test_versione():
    assert voc.VOCABOLARIO_VERSIONE == 1


def test_codici_in_snake_case_ascii():
    for costante in (voc.TIPI_SOGGETTO, voc.COMPETENZE, voc.FORME, voc.RUOLI, voc.DOCUMENTI):
        for codice in costante:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", codice), codice


class TestTipiSoggetto:
    def test_ventisette_tipi_con_altro(self):
        assert len(voc.TIPI_SOGGETTO) == 27
        assert "altro" in voc.TIPI_SOGGETTO

    def test_id_beneficiari_tra_1_e_31(self):
        for codice, voce in voc.TIPI_SOGGETTO.items():
            for id_ in voce.beneficiari:
                assert 1 <= id_ <= 31, (codice, id_)
            assert len(set(voce.beneficiari)) == len(voce.beneficiari), codice

    def test_ogni_beneficiario_del_catalogo_ha_un_tipo(self):
        """I 31 beneficiari del catalogo sono tutti raggiungibili: nessun bando
        resta fuori dal matching per mancanza di mappatura."""
        coperti = {i for v in voc.TIPI_SOGGETTO.values() for i in v.beneficiari}
        assert coperti == set(range(1, 32))

    @pytest.mark.parametrize(
        ("codice", "attesi"),
        [
            ("impresa", [16]),
            ("pmi", [27]),
            ("impresa_sociale", [17, 8]),
            ("intermediario_finanziario", [4, 6, 18]),
            ("ente_sportivo", [3, 9, 14, 29]),
            ("organismo_ricerca", [31]),
            ("universita", [31]),
            ("altro", [1, 5, 7, 10]),
            ("media_impresa", []),
            ("pmi_innovativa", []),
            ("fondazione", []),
            ("codice_inesistente", []),
        ],
    )
    def test_beneficiari_per_tipo(self, codice, attesi):
        assert voc.beneficiari_per_tipo(codice) == attesi

    def test_beneficiari_per_tipo_restituisce_una_copia(self):
        lista = voc.beneficiari_per_tipo("impresa")
        lista.append(99)
        assert voc.beneficiari_per_tipo("impresa") == [16]


class TestCompetenze:
    def test_quarantadue_competenze(self):
        assert len(voc.COMPETENZE) == 42

    def test_aree(self):
        for codice, voce in voc.COMPETENZE.items():
            assert voce.area in voc.AREE_COMPETENZE, codice
        usate = {v.area for v in voc.COMPETENZE.values()}
        assert usate == set(voc.AREE_COMPETENZE)  # nessuna area vuota
        per_area = {}
        for voce in voc.COMPETENZE.values():
            per_area[voce.area] = per_area.get(voce.area, 0) + 1
        assert per_area == {
            "ricerca_innovazione": 5, "digitale": 7, "energia_ambiente": 7,
            "produzione": 10, "servizi": 8, "infrastrutture": 4, "altro": 1,
        }

    def test_codici_dell_appendice(self):
        for codice in ("prototipazione_testing", "automazione_industria40",
                       "progettazione_rendicontazione_fondi", "capacita_produttiva_scala"):
            assert codice in voc.COMPETENZE


class TestFormeAppendiceA:
    def test_le_sette_forme_piu_la_residuale(self):
        assert list(voc.FORME) == [
            "ats", "ati_rti", "rete_contratto", "rete_soggetto", "consorzio",
            "accordo_partenariato", "consorzio_ue", "altra",
        ]

    @pytest.mark.parametrize(
        ("forma", "specifici"),
        [
            ("ats", {"mandato_collettivo", "atto_costitutivo"}),
            ("ati_rti", {"impegno_costituire", "mandato_collettivo"}),
            ("rete_contratto", {"contratto_rete", "programma_rete"}),
            ("rete_soggetto", {
                "contratto_rete", "programma_rete", "fondo_patrimoniale", "organo_comune",
                "iscrizione_registro_imprese",
            }),
            ("consorzio", {"atto_costitutivo", "statuto"}),
            ("accordo_partenariato", {"accordo_partenariato", "mandato_collettivo"}),
            ("consorzio_ue", {
                "consortium_agreement", "dichiarazioni_affiliated_entities",
                "lettere_associated_partners",
            }),
            ("altra", set()),
        ],
    )
    def test_documenti_specifici(self, forma, specifici):
        assert set(voc.FORME[forma].documenti) == specifici

    def test_rete_soggetto_estende_rete_contratto(self):
        assert set(voc.FORME["rete_contratto"].documenti) < set(
            voc.FORME["rete_soggetto"].documenti
        )

    @pytest.mark.parametrize(
        ("forma", "responsabilita"),
        [
            ("ats", "pro_quota"),
            ("ati_rti", "solidale"),
            ("rete_contratto", "singoli_partecipanti"),
            ("altra", None),
        ],
    )
    def test_responsabilita_dell_appendice(self, forma, responsabilita):
        assert voc.FORME[forma].responsabilita == responsabilita

    def test_ogni_forma_ha_testi_e_codici_validi(self):
        for codice, voce in voc.FORME.items():
            assert voce.etichetta and voce.costituzione, codice
            assert voce.responsabilita is None or voce.responsabilita in voc.RESPONSABILITA
            for doc in voce.documenti:
                assert doc in voc.DOCUMENTI, (codice, doc)
                assert doc not in voc.DOCUMENTI_BASE, (codice, doc)

    def test_note_dell_appendice(self):
        assert "affiliated" in voc.FORME["consorzio_ue"].nota.lower()
        assert "MIMIT" in voc.FORME["accordo_partenariato"].nota
        assert "più raggruppamenti" in voc.FORME["ati_rti"].nota


class TestDocumentiForma:
    def test_base_poi_specifici_senza_doppioni(self):
        assert voc.DOCUMENTI_BASE == (
            "nda", "lettera_intenti", "term_sheet_mou", "dichiarazione_sostitutiva",
        )
        assert voc.documenti_forma("ats") == [
            *voc.DOCUMENTI_BASE, "mandato_collettivo", "atto_costitutivo",
        ]
        for forma in voc.FORME:
            docs = voc.documenti_forma(forma)
            assert len(docs) == len(set(docs))

    def test_forma_sconosciuta_solo_base(self):
        assert voc.documenti_forma("boh") == list(voc.DOCUMENTI_BASE)


class TestVocabolarioOut:
    def test_forma_json_del_contratto(self):
        body = json.loads(voc.vocabolario_out().model_dump_json())
        assert set(body) == {"versione", "tipi_soggetto", "competenze", "forme", "ruoli"}
        assert body["versione"] == 1
        assert set(body["tipi_soggetto"][0]) == {"codice", "etichetta", "beneficiari"}
        assert set(body["competenze"][0]) == {"codice", "etichetta", "area"}
        assert set(body["forme"][0]) == {
            "codice", "etichetta", "responsabilita", "costituzione", "documenti",
        }
        assert set(body["forme"][0]["documenti"][0]) == {"codice", "etichetta"}
        assert body["ruoli"] == [
            {"codice": "capofila", "etichetta": "Capofila"},
            {"codice": "partner", "etichetta": "Partner"},
        ]

    def test_contenuto(self):
        out = voc.vocabolario_out()
        assert [t.codice for t in out.tipi_soggetto] == list(voc.TIPI_SOGGETTO)
        assert [c.codice for c in out.competenze] == list(voc.COMPETENZE)
        assert [f.codice for f in out.forme] == list(voc.FORME)
        impresa = out.tipi_soggetto[0]
        assert (impresa.codice, impresa.beneficiari) == ("impresa", [16])
        assert out.competenze[0].area == "Ricerca e innovazione"  # etichetta, non codice
        ats = next(f for f in out.forme if f.codice == "ats")
        assert [d.codice for d in ats.documenti] == voc.documenti_forma("ats")

    def test_etichette_in_italiano_mai_famiglia(self):
        testo = voc.vocabolario_out().model_dump_json()
        assert "amiglia" not in testo
