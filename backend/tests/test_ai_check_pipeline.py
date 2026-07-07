"""Test dei builder di input dell'AI-check su BANDI REALI (fixture estratte
dal dump del catalogo): serializzazione indicizzata, citazioni risolvibili,
hash della cache, company pack."""

import json
from pathlib import Path

from app.services.ai_check_prompts import (
    NON_DISPONIBILE,
    build_bando_input,
    build_company_pack,
    build_matching_input,
    compute_content_hash,
)
from app.services.bandi_service import normalize_contenuto

FIXTURES = Path(__file__).parent / "fixtures" / "ai_check"


def load_bando(name: str) -> dict:
    bando = json.loads((FIXTURES / f"{name}.json").read_text())
    bando["contenuto"] = normalize_contenuto(bando.get("contenuto"))
    return bando


class TestBuildBandoInput:
    def test_guida_con_faq_sezioni_indicizzate(self):
        bando = load_bando("bando_guida_faq")
        text, sections = build_bando_input(bando, bando["contenuto"])
        assert "[META]" in text and "[S1]" in text and "[S21]" in text
        assert len(sections) == 22  # META + 21 sezioni
        # ogni indice del testo è risolvibile nella mappa (contratto citazioni)
        for key, value in sections.items():
            assert f"[{key}]" in text
            assert value in text
        # la FAQ è resa come D:/R:
        faq = [s for s in sections.values() if s.startswith("D: ")]
        assert faq and "R: " in faq[0]

    def test_meta_contiene_tutti_i_facet_del_catalogo(self):
        bando = load_bando("bando_guida_faq")
        _, sections = build_bando_input(bando, bando["contenuto"])
        meta = sections["META"]
        assert "Regioni ammesse (catalogo): Lazio" in meta
        assert "Codici ATECO (catalogo):" in meta and "13 — Industrie tessili" in meta
        assert "Beneficiari (catalogo): Imprese" in meta
        assert "Ente erogatore: Regione Lazio" in meta
        assert "Allegati ufficiali (NON inclusi in questo testo):" in meta

    def test_flash_con_liste_puntate(self):
        bando = load_bando("bando_flash")
        text, sections = build_bando_input(bando, bando["contenuto"])
        assert any(v.startswith("- ") or "\n- " in v for v in sections.values())
        assert "## " in text  # heading h2

    def test_contenuto_corrotto_degrada_a_solo_meta(self):
        # 5 bandi reali hanno contenuto doppio-encodato corrotto all'origine:
        # normalize_contenuto → None, l'input resta il solo blocco META.
        bando = load_bando("bando_double_encoded")
        assert bando["contenuto"] is None
        text, sections = build_bando_input(bando, bando["contenuto"])
        assert list(sections) == ["META"]
        assert bando["titolo"] and bando["titolo"] in sections["META"]

    def test_hook_allegati_futuri(self):
        bando = load_bando("bando_flash")
        text, sections = build_bando_input(
            bando, bando["contenuto"], allegati_texts=[("Avviso pubblico", "Testo del PDF")]
        )
        assert "[A1] Avviso pubblico" in text
        assert sections["A1"] == "Testo del PDF"


class TestContentHash:
    def test_hash_del_testo_serializzato_non_di_hash_bando(self):
        # hash_bando del catalogo NON copre i facet delle junction che entrano
        # in [META] e da cui l'estrazione deriva requisiti: la cache deve
        # invalidarsi su TUTTO ciò che il modello vede.
        bando = load_bando("bando_guida_faq")
        text, _ = build_bando_input(bando, bando["contenuto"])
        h = compute_content_hash(bando, text)
        assert len(h) == 64
        assert h != bando["hash_bando"]

    def test_cambia_se_cambiano_le_junction(self):
        bando = load_bando("bando_guida_faq")
        text1, _ = build_bando_input(bando, bando["contenuto"])
        bando["bando_regioni"] = [{"regioni": {"id": 10, "nome": "Lombardia"}}]
        text2, _ = build_bando_input(bando, bando["contenuto"])
        assert compute_content_hash(bando, text1) != compute_content_hash(bando, text2)

    def test_stabile_a_parita_di_input(self):
        bando = load_bando("bando_flash")
        text1, _ = build_bando_input(bando, bando["contenuto"])
        text2, _ = build_bando_input(bando, bando["contenuto"])
        assert text1 == text2
        assert compute_content_hash(bando, text1) == compute_content_hash(bando, text2)


PROFILE = {"nome": "Michele", "cognome": "Rossi", "codice_fiscale": "RSSMRA80A01H501U",
           "cf_verified_at": "2026-07-01T00:00:00Z"}
COMPANY = {"ragione_sociale": "ACME Srl", "partita_iva": "01234567890",
           "regione_nome": "Lazio", "ateco_codice": "62.01", "settore_nome": None,
           "classe_dimensionale": "piccola", "numero_dipendenti": 12}


class TestCompanyPack:
    def test_campi_assenti_resi_non_disponibile(self):
        pack = build_company_pack(PROFILE, COMPANY, None, None, [], None, 1000)
        assert "settore_nome: NON DISPONIBILE" in pack
        assert "regione_nome: Lazio" in pack

    def test_cf_personale_mai_in_chiaro_nel_pack(self):
        # Il report è visibile a tutta l'azienda: il CF del titolare non deve
        # poter trapelare via dato_azienda — nel pack va solo lo stato.
        pack = build_company_pack(PROFILE, COMPANY, None, None, [], None, 1000)
        assert PROFILE["codice_fiscale"] not in pack.split("## Dati aziendali")[0]
        assert "codice_fiscale (stato): presente, verificato all'Anagrafe Tributaria" in pack
        senza_cf = build_company_pack({**PROFILE, "codice_fiscale": None}, COMPANY, None, None, [], None, 1000)
        assert "codice_fiscale (stato): NON DISPONIBILE" in senza_cf

    def test_dossier_e_derived_appiattiti_con_percorsi_citabili(self):
        dossier = {"anagrafica": {"denominazione": "ACME Srl", "rea": None},
                   "flags": {"startup_innovativa": True}}
        derived = {"beneficiari": [{"id": 2, "nome": "PMI"}], "ateco_divisione": "62"}
        pack = build_company_pack(PROFILE, COMPANY, dossier, derived, [], None, 1000)
        assert "dossier.anagrafica.denominazione: ACME Srl" in pack
        assert "dossier.flags.startup_innovativa: True" in pack
        assert "derived.ateco_divisione: 62" in pack
        assert "rea" not in pack.split("## Dossier")[1].split("##")[0]  # i None non compaiono

    def test_visura_troncata(self):
        pack = build_company_pack(PROFILE, COMPANY, None, None, [], "x" * 500, 100)
        assert "Testo della visura camerale (troncato)" in pack
        section = pack.split("## Testo della visura camerale (troncato)\n")[1]
        assert len(section) == 100

    def test_persone_e_cariche(self):
        people = [{"kind": "manager", "nome": "Anna", "cognome": "Bianchi",
                   "ruoli": [{"role": "Presidente"}], "is_legale_rappresentante": True}]
        pack = build_company_pack(PROFILE, COMPANY, None, None, people, None, 1000)
        assert "- manager: Anna Bianchi [legale rappresentante] (Presidente)" in pack

    def test_senza_company_row(self):
        pack = build_company_pack(PROFILE, None, None, None, [], None, 1000)
        assert NON_DISPONIBILE in pack


class TestMatchingInput:
    def test_composizione(self):
        text = build_matching_input({"requisiti_obbligatori": []}, {"regione": {"esito": "soddisfatto"}}, "PACK")
        assert "## Estrazione dal bando" in text
        assert "## Verifiche strutturate" in text
        assert text.endswith("## Profilo azienda\nPACK")
        assert "soddisfatto" in text
