"""Test dello scoring deterministico (Stadio C): gate di ammissibilità,
punteggio stima/euristico, merge coi pre-check, guardie anti-allucinazione."""

from app.schemas.ai_check import ExtractionResult, MatchingResult
from app.services.ai_check_scoring import FRAC, facet_prechecks, score_report

SECTIONS = {
    "META": "Regioni ammesse (catalogo): Lazio",
    "S1": "Possono presentare domanda le micro e piccole imprese con sede nel Lazio.",
    "S2": "Fino a 20 punti per il grado di innovazione del progetto.",
}

META = {"model": "test", "prompt_version": 1, "generated_at": "2026-07-07T00:00:00Z",
        "bando_hash": "x", "extraction_cached": False}


def requisito(id_="R1", categoria="dimensionale", sezione="S1",
              testo_esatto="Possono presentare domanda le micro e piccole imprese con sede nel Lazio."):
    return {
        "id": id_, "testo": "Micro e piccole imprese", "categoria": categoria,
        "dato_richiesto": "classe dimensionale",
        "citazione": {"sezione": sezione, "testo_esatto": testo_esatto},
    }


def criterio(id_="C1", punti_max=None):
    return {
        "id": id_, "nome": "Innovazione", "categoria": "altro", "punti_max": punti_max,
        "citazione": {"sezione": "S2", "testo_esatto": "Fino a 20 punti per il grado di innovazione"},
    }


def extraction(requisiti=None, criteri=None, griglia=None):
    return ExtractionResult.model_validate({
        "requisiti_obbligatori": requisiti if requisiti is not None else [requisito()],
        "criteri_valutazione": criteri if criteri is not None else [],
        "griglia": griglia or {"presente": False, "fonte": "assente",
                               "punteggio_max_totale": None, "soglia_minima": None, "note": None},
    })


def verdict(id_="R1", esito="soddisfatto", campo="classe_dimensionale", valore="piccola"):
    dato = {"campo": campo, "valore": valore} if campo else None
    return {"id": id_, "esito": esito, "dato_azienda": dato, "motivazione": "ok"}


def matching(requisiti=None, criteri=None, **extra):
    return MatchingResult.model_validate({
        "requisiti": requisiti or [],
        "criteri": criteri or [],
        "punti_di_forza": extra.get("punti_di_forza", []),
        "punti_di_debolezza": extra.get("punti_di_debolezza", []),
        "dati_mancanti": extra.get("dati_mancanti", []),
    })


NO_PRECHECKS = {
    "regione": {"esito": "non_applicabile", "bando": [], "azienda": None},
    "ateco": {"esito": "non_applicabile", "bando": [], "azienda": []},
    "settore": {"esito": "non_applicabile", "bando": [], "azienda": None},
    "beneficiari": {"esito": "non_applicabile", "bando": [], "azienda": []},
    "stato_bando": {"esito": "soddisfatto", "valore": "aperto"},
}


class TestGateAmmissibilita:
    def test_un_non_soddisfatto_rende_non_ammissibile_anche_con_punteggio_alto(self):
        ext = extraction(
            requisiti=[requisito("R1"), requisito("R2")],
            criteri=[criterio("C1", punti_max=20)],
            griglia={"presente": True, "fonte": "contenuto", "punteggio_max_totale": 20,
                     "soglia_minima": None, "note": None},
        )
        m = matching(
            requisiti=[verdict("R1", "soddisfatto"), verdict("R2", "non_soddisfatto")],
            criteri=[verdict("C1", "soddisfatto")],
        )
        report = score_report(ext, m, NO_PRECHECKS, SECTIONS, META)
        assert report["esito_ammissibilita"] == "non_ammissibile"
        assert report["punteggio_totale"] == 100  # calcolato ma l'esito prevale

    def test_dato_mancante_su_obbligatorio_e_mai_ammissibile(self):
        report = score_report(
            extraction(), matching(requisiti=[verdict("R1", "dato_mancante", campo=None)]),
            NO_PRECHECKS, SECTIONS, META,
        )
        assert report["esito_ammissibilita"] == "da_verificare"

    def test_tutti_soddisfatti_ammissibile(self):
        report = score_report(
            extraction(), matching(requisiti=[verdict("R1")]),
            NO_PRECHECKS, SECTIONS, META,
        )
        assert report["esito_ammissibilita"] == "ammissibile"

    def test_verdetto_mancante_dal_modello_diventa_dato_mancante(self):
        report = score_report(extraction(), matching(), NO_PRECHECKS, SECTIONS, META)
        assert report["requisiti"][0]["verdetto"] == "dato_mancante"
        assert report["esito_ammissibilita"] == "da_verificare"


class TestGuardieAntiAllucinazione:
    def test_soddisfatto_senza_dato_azienda_retrocesso(self):
        report = score_report(
            extraction(), matching(requisiti=[verdict("R1", "soddisfatto", campo=None)]),
            NO_PRECHECKS, SECTIONS, META,
        )
        assert report["requisiti"][0]["verdetto"] == "dato_mancante"
        assert report["esito_ammissibilita"] == "da_verificare"
        assert any(d["ref"] == "R1" for d in report["dati_mancanti"])

    def test_verdetti_con_id_sconosciuti_scartati(self):
        report = score_report(
            extraction(), matching(requisiti=[verdict("R99", "non_soddisfatto")]),
            NO_PRECHECKS, SECTIONS, META,
        )
        # R99 ignorato; R1 resta senza verdetto → dato_mancante
        assert report["esito_ammissibilita"] == "da_verificare"

    def test_citazione_verificata_come_substring_della_sezione(self):
        ok = requisito("R1", testo_esatto="micro e piccole imprese")
        ko = requisito("R2", testo_esatto="testo inventato dal modello")
        report = score_report(
            extraction(requisiti=[ok, ko]),
            matching(requisiti=[verdict("R1"), verdict("R2")]),
            NO_PRECHECKS, SECTIONS, META,
        )
        assert report["requisiti"][0]["riferimento_bando"]["verificata"] is True
        assert report["requisiti"][1]["riferimento_bando"]["verificata"] is False

    def test_citazione_con_indice_tra_parentesi_quadre_verificata(self):
        # Il modello può copiare l'indice come lo vede nel testo ("[S1]").
        req = requisito("R1", sezione="[S1]", testo_esatto="micro e piccole imprese")
        report = score_report(
            extraction(requisiti=[req]), matching(requisiti=[verdict("R1")]),
            NO_PRECHECKS, SECTIONS, META,
        )
        assert report["requisiti"][0]["riferimento_bando"]["verificata"] is True


class TestMergePrecheck:
    def test_precheck_in_contraddizione_vince_sul_verdetto_llm(self):
        prechecks = {**NO_PRECHECKS, "regione": {"esito": "non_soddisfatto", "bando": ["Lombardia"], "azienda": "Lazio"}}
        report = score_report(
            extraction(requisiti=[requisito("R1", categoria="territoriale")]),
            matching(requisiti=[verdict("R1", "soddisfatto", campo="regione_nome", valore="Lazio")]),
            prechecks, SECTIONS, META,
        )
        assert report["requisiti"][0]["verdetto"] == "non_soddisfatto"
        assert report["esito_ammissibilita"] == "non_ammissibile"

    def test_precheck_migliore_non_promuove(self):
        prechecks = {**NO_PRECHECKS, "regione": {"esito": "soddisfatto", "bando": ["Lazio"], "azienda": "Lazio"}}
        report = score_report(
            extraction(requisiti=[requisito("R1", categoria="territoriale")]),
            matching(requisiti=[verdict("R1", "non_soddisfatto")]),
            prechecks, SECTIONS, META,
        )
        assert report["requisiti"][0]["verdetto"] == "non_soddisfatto"

    def test_precheck_dato_mancante_non_retrocede_il_verdetto_llm(self):
        # Senza import certificato il pre-check beneficiari non può girare:
        # non deve retrocedere un verdetto del modello fondato sui dati del
        # form (altrimenti le aziende senza import non sarebbero mai ammissibili).
        prechecks = {**NO_PRECHECKS, "beneficiari": {"esito": "dato_mancante", "bando": ["PMI"], "azienda": []}}
        report = score_report(
            extraction(requisiti=[requisito("R1", categoria="dimensionale")]),
            matching(requisiti=[verdict("R1", "soddisfatto", campo="classe_dimensionale", valore="piccola")]),
            prechecks, SECTIONS, META,
        )
        assert report["requisiti"][0]["verdetto"] == "soddisfatto"
        assert report["esito_ammissibilita"] == "ammissibile"

    def test_settoriale_best_of_ateco_batte_il_tag_settore(self):
        # ATECO esatto che combacia + tag settore tematico che non combacia:
        # sono evidenze alternative, basta un match — niente falso non ammissibile.
        prechecks = {
            **NO_PRECHECKS,
            "ateco": {"esito": "soddisfatto", "bando": ["13"], "azienda": ["13"]},
            "settore": {"esito": "non_soddisfatto", "bando": ["Artigianato"], "azienda": "Tessile"},
        }
        report = score_report(
            extraction(requisiti=[requisito("R1", categoria="settoriale")]),
            matching(requisiti=[verdict("R1", "soddisfatto", campo="ateco_codice", valore="13.10")]),
            prechecks, SECTIONS, META,
        )
        assert report["requisiti"][0]["verdetto"] == "soddisfatto"
        assert report["esito_ammissibilita"] == "ammissibile"

    def test_settoriale_retrocede_solo_se_nessuna_evidenza_combacia(self):
        prechecks = {
            **NO_PRECHECKS,
            "ateco": {"esito": "non_soddisfatto", "bando": ["13"], "azienda": ["62"]},
            "settore": {"esito": "non_applicabile", "bando": [], "azienda": None},
        }
        report = score_report(
            extraction(requisiti=[requisito("R1", categoria="settoriale")]),
            matching(requisiti=[verdict("R1", "soddisfatto")]),
            prechecks, SECTIONS, META,
        )
        assert report["requisiti"][0]["verdetto"] == "non_soddisfatto"


class TestGateFolding:
    def test_precheck_bocciato_entra_nel_gate_anche_senza_requisito_estratto(self):
        # L'estrazione non ha prodotto requisiti territoriali (LLM non
        # deterministico): il vincolo di catalogo bocciato deve comunque
        # rendere non ammissibile, con una voce sintetica spiegabile.
        prechecks = {**NO_PRECHECKS, "regione": {"esito": "non_soddisfatto", "bando": ["Lombardia"], "azienda": "Lazio"}}
        report = score_report(
            extraction(requisiti=[requisito("R1", categoria="formale")]),
            matching(requisiti=[verdict("R1", "soddisfatto", campo="pec", valore="x@pec.it")]),
            prechecks, SECTIONS, META,
        )
        assert report["esito_ammissibilita"] == "non_ammissibile"
        sintetici = [r for r in report["requisiti"] if r["id"].startswith("V")]
        assert len(sintetici) == 1
        assert sintetici[0]["verdetto"] == "non_soddisfatto"
        assert sintetici[0]["riferimento_bando"]["sezione"] == "META"
        assert "Lombardia" in sintetici[0]["riferimento_bando"]["testo"]

    def test_nessuna_voce_sintetica_se_la_categoria_e_gia_coperta(self):
        prechecks = {**NO_PRECHECKS, "regione": {"esito": "non_soddisfatto", "bando": ["Lombardia"], "azienda": "Lazio"}}
        report = score_report(
            extraction(requisiti=[requisito("R1", categoria="territoriale")]),
            matching(requisiti=[verdict("R1", "soddisfatto")]),
            prechecks, SECTIONS, META,
        )
        assert not [r for r in report["requisiti"] if r["id"].startswith("V")]
        assert report["esito_ammissibilita"] == "non_ammissibile"  # via merge

    def test_precheck_dato_mancante_non_entra_nel_gate(self):
        prechecks = {**NO_PRECHECKS, "beneficiari": {"esito": "dato_mancante", "bando": ["PMI"], "azienda": []}}
        report = score_report(
            extraction(), matching(requisiti=[verdict("R1")]),
            prechecks, SECTIONS, META,
        )
        assert report["esito_ammissibilita"] == "ammissibile"


class TestPunteggioStima:
    def test_griglia_pubblicata_normalizzata_su_100(self):
        ext = extraction(
            criteri=[criterio("C1", punti_max=20), criterio("C2", punti_max=30)],
            griglia={"presente": True, "fonte": "contenuto", "punteggio_max_totale": 50,
                     "soglia_minima": None, "note": None},
        )
        m = matching(
            requisiti=[verdict("R1")],
            criteri=[verdict("C1", "soddisfatto"), verdict("C2", "parzialmente_soddisfatto")],
        )
        report = score_report(ext, m, NO_PRECHECKS, SECTIONS, META)
        assert report["tipo_punteggio"] == "stima"
        # 20*1 + 30*0.5 = 35 su 50 → 70
        assert report["punteggio_totale"] == 70
        assert report["griglia"]["punti_ottenuti_stimati"] == 35.0
        assert report["criteri"][0]["punteggio_parziale"] == 20.0
        assert report["criteri"][1]["punteggio_parziale"] == 15.0

    def test_sotto_soglia_minima_segnalato_come_debolezza(self):
        ext = extraction(
            criteri=[criterio("C1", punti_max=20)],
            griglia={"presente": True, "fonte": "contenuto", "punteggio_max_totale": 20,
                     "soglia_minima": 15, "note": None},
        )
        m = matching(requisiti=[verdict("R1")], criteri=[verdict("C1", "parzialmente_soddisfatto")])
        report = score_report(ext, m, NO_PRECHECKS, SECTIONS, META)
        assert any("soglia minima" in p["testo"] for p in report["punti_di_debolezza"])

    def test_punti_estratti_oltre_il_totale_dichiarato_normalizzano_sulla_somma(self):
        # L'estrazione può "gonfiare" i punti rispetto al totale dichiarato:
        # si normalizza sul massimo tra i due, mai oltre 100.
        ext = extraction(
            criteri=[criterio("C1", punti_max=20), criterio("C2", punti_max=30)],
            griglia={"presente": True, "fonte": "contenuto", "punteggio_max_totale": 40,
                     "soglia_minima": None, "note": None},
        )
        m = matching(
            requisiti=[verdict("R1")],
            criteri=[verdict("C1", "soddisfatto"), verdict("C2", "soddisfatto")],
        )
        report = score_report(ext, m, NO_PRECHECKS, SECTIONS, META)
        assert report["punteggio_totale"] == 100  # 50/50, non 50/40 troncato

    def test_griglia_parziale_niente_falso_avviso_di_soglia(self):
        # Un solo criterio estratto (30 punti) su una griglia dichiarata da 100
        # con soglia 60: il confronto sarebbe tra scale diverse — niente avviso.
        ext = extraction(
            criteri=[criterio("C1", punti_max=30)],
            griglia={"presente": True, "fonte": "contenuto", "punteggio_max_totale": 100,
                     "soglia_minima": 60, "note": None},
        )
        m = matching(requisiti=[verdict("R1")], criteri=[verdict("C1", "soddisfatto")])
        report = score_report(ext, m, NO_PRECHECKS, SECTIONS, META)
        assert not any("soglia minima" in p["testo"] for p in report["punti_di_debolezza"])

    def test_griglia_presente_ma_senza_punti_ricade_su_euristico(self):
        ext = extraction(
            criteri=[criterio("C1", punti_max=None)],
            griglia={"presente": True, "fonte": "contenuto", "punteggio_max_totale": None,
                     "soglia_minima": None, "note": None},
        )
        report = score_report(
            ext, matching(requisiti=[verdict("R1")], criteri=[verdict("C1")]),
            NO_PRECHECKS, SECTIONS, META,
        )
        assert report["tipo_punteggio"] == "euristico"


class TestPunteggioEuristico:
    def test_componenti_non_applicabili_escluse_dal_denominatore(self):
        # Facet non applicabili fuori dal denominatore: pesano requisiti (30×1)
        # e criteri (40×0.5) → 50/70 ≈ 71.
        ext = extraction(criteri=[criterio("C1"), criterio("C2")])
        m = matching(
            requisiti=[verdict("R1")],
            criteri=[verdict("C1", "soddisfatto"), verdict("C2", "non_soddisfatto")],
        )
        report = score_report(ext, m, NO_PRECHECKS, SECTIONS, META)
        assert report["tipo_punteggio"] == "euristico"
        assert report["punteggio_totale"] == 71

    def test_dato_mancante_resta_nel_denominatore(self):
        ext = extraction(criteri=[criterio("C1"), criterio("C2")])
        m = matching(
            requisiti=[verdict("R1")],
            criteri=[verdict("C1", "soddisfatto"), verdict("C2", "dato_mancante", campo=None)],
        )
        report = score_report(ext, m, NO_PRECHECKS, SECTIONS, META)
        # criteri 0.5 (l'assenza non gonfia), requisiti 1 → (30+20)/70 ≈ 71
        assert report["punteggio_totale"] == 71

    def test_i_requisiti_pesano_nel_punteggio(self):
        # A parità di facet e criteri, chi soddisfa meno requisiti scende:
        # è ciò che differenzia bandi simili tra loro.
        ext = extraction(
            requisiti=[requisito("R1"), requisito("R2", "formale")],
            criteri=[criterio("C1")],
        )
        pieno = matching(
            requisiti=[verdict("R1"), verdict("R2")],
            criteri=[verdict("C1", "soddisfatto")],
        )
        parziale = matching(
            requisiti=[verdict("R1"), verdict("R2", "non_soddisfatto")],
            criteri=[verdict("C1", "soddisfatto")],
        )
        alto = score_report(ext, pieno, NO_PRECHECKS, SECTIONS, META)
        basso = score_report(ext, parziale, NO_PRECHECKS, SECTIONS, META)
        assert alto["punteggio_totale"] == 100
        # requisiti 30×0.5 + criteri 40×1 → 55/70 ≈ 79
        assert basso["punteggio_totale"] == 79
        assert alto["punteggio_totale"] > basso["punteggio_totale"]

    def test_pesi_composti_con_prechecks(self):
        prechecks = {
            "regione": {"esito": "soddisfatto", "bando": ["Lazio"], "azienda": "Lazio"},
            "ateco": {"esito": "non_soddisfatto", "bando": ["13"], "azienda": ["62"]},
            "settore": {"esito": "non_soddisfatto", "bando": ["X"], "azienda": "Y"},
            "beneficiari": {"esito": "soddisfatto", "bando": ["PMI"], "azienda": ["PMI"]},
            "stato_bando": {"esito": "soddisfatto", "valore": "aperto"},
        }
        ext = extraction(criteri=[criterio("C1")])
        m = matching(requisiti=[verdict("R1")], criteri=[verdict("C1", "soddisfatto")])
        report = score_report(ext, m, prechecks, SECTIONS, META)
        # Il settoriale bocciato genera anche la voce sintetica nel gate →
        # requisiti 30×0.5 (R1 sì, V1 no) + criteri 40 + settoriale 12×0 +
        # regione 9 + beneficiari 9 → 73/100
        assert report["punteggio_totale"] == 73
        assert report["esito_ammissibilita"] == "non_ammissibile"
        assert report["pesi_euristici"] is not None

    def test_settoriale_best_of_nel_punteggio(self):
        prechecks = {
            "regione": {"esito": "non_applicabile", "bando": [], "azienda": None},
            "ateco": {"esito": "soddisfatto", "bando": ["13"], "azienda": ["13"]},
            "settore": {"esito": "non_soddisfatto", "bando": ["X"], "azienda": "Y"},
            "beneficiari": {"esito": "non_applicabile", "bando": [], "azienda": []},
            "stato_bando": {"esito": "soddisfatto", "valore": "aperto"},
        }
        report = score_report(
            extraction(), matching(requisiti=[verdict("R1")]),
            prechecks, SECTIONS, META,
        )
        # unica componente: settoriale best-of = soddisfatto → 100
        assert report["punteggio_totale"] == 100

    def test_precheck_dato_mancante_escluso_dal_punteggio(self):
        # "Confronto non possibile" (manca l'import) non deve costare punti:
        # è già segnalato in dati_mancanti — la componente esce dal denominatore.
        prechecks = {
            **NO_PRECHECKS,
            "beneficiari": {"esito": "dato_mancante", "bando": ["PMI"], "azienda": []},
        }
        ext = extraction(criteri=[criterio("C1")])
        m = matching(requisiti=[verdict("R1")], criteri=[verdict("C1", "soddisfatto")])
        report = score_report(ext, m, prechecks, SECTIONS, META)
        assert report["punteggio_totale"] == 100  # solo i criteri pesano

    def test_nessuna_componente_applicabile_punteggio_null(self):
        report = score_report(
            extraction(requisiti=[], criteri=[]), matching(),
            NO_PRECHECKS, SECTIONS, META,
        )
        assert report["punteggio_totale"] is None
        assert report["tipo_punteggio"] == "euristico"

    def test_bando_chiuso_segnalato_come_debolezza(self):
        prechecks = {**NO_PRECHECKS, "stato_bando": {"esito": "non_soddisfatto", "valore": "chiuso"}}
        report = score_report(
            extraction(), matching(requisiti=[verdict("R1")]),
            prechecks, SECTIONS, META,
        )
        assert any("non risulta aperto" in p["testo"] for p in report["punti_di_debolezza"])


class TestFacetPrechecks:
    BANDO = {
        "stato_bando": "aperto",
        "bando_regioni": [{"regioni": {"id": 12, "nome": "Lazio"}}],
        "bando_settori": [{"settori": {"id": 5, "nome": "Artigianato"}}],
        "bando_beneficiari": [{"beneficiari": {"id": 2, "nome": "PMI"}}],
        "bando_codici_ateco": [{"codici_ateco": {"id": 620, "codice": "62", "descrizione": "ICT"}}],
    }

    def test_match_esatti(self):
        # I beneficiari sono dichiarati sul profilo, non dedotti dalla visura.
        company = {"regione_id": 12, "regione_nome": "Lazio", "settore_id": 5,
                   "settore_nome": "Artigianato", "ateco_codice": "62.01",
                   "beneficiari": [{"id": 2, "nome": "PMI"}]}
        derived = {"ateco_secondari": []}
        checks = facet_prechecks(self.BANDO, company, derived)
        assert checks["regione"]["esito"] == "soddisfatto"
        assert checks["settore"]["esito"] == "soddisfatto"
        assert checks["ateco"]["esito"] == "soddisfatto"
        assert checks["beneficiari"]["esito"] == "soddisfatto"
        assert checks["stato_bando"]["esito"] == "soddisfatto"

    def test_ateco_secondari_contano(self):
        company = {"ateco_codice": "85.59"}
        derived = {"ateco_secondari": ["62.02"]}
        checks = facet_prechecks(self.BANDO, company, derived)
        assert checks["ateco"]["esito"] == "soddisfatto"

    def test_beneficiari_non_dichiarati_sono_dato_mancante(self):
        # Campo vuoto ≠ «nessuna categoria»: non deve mai dire non_soddisfatto,
        # o il requisito peserebbe su un dato che l'utente non ha compilato.
        checks = facet_prechecks(self.BANDO, {"beneficiari": []}, {})
        assert checks["beneficiari"]["esito"] == "dato_mancante"

    def test_beneficiari_dichiarati_fuori_dal_bando(self):
        company = {"beneficiari": [{"id": 99, "nome": "Enti pubblici"}]}
        checks = facet_prechecks(self.BANDO, company, {})
        assert checks["beneficiari"]["esito"] == "non_soddisfatto"
        assert checks["beneficiari"]["azienda"] == ["Enti pubblici"]

    def test_dati_mancanti_e_non_applicabile(self):
        checks = facet_prechecks(self.BANDO, {}, {})
        assert checks["regione"]["esito"] == "dato_mancante"
        assert checks["ateco"]["esito"] == "dato_mancante"
        checks_empty_bando = facet_prechecks({"stato_bando": "aperto"}, {"regione_id": 12}, {})
        assert checks_empty_bando["regione"]["esito"] == "non_applicabile"

    def test_mismatch(self):
        company = {"regione_id": 10, "regione_nome": "Lombardia", "ateco_codice": "45.11"}
        checks = facet_prechecks(self.BANDO, company, {})
        assert checks["regione"]["esito"] == "non_soddisfatto"
        assert checks["ateco"]["esito"] == "non_soddisfatto"

    def test_regione_soddisfatta_da_sede_secondaria(self):
        # Sede legale in Lombardia (10, non ammessa) ma un'unità locale nel
        # Lazio (12, ammesso): il vincolo territoriale è soddisfatto.
        company = {"regione_id": 10, "regione_nome": "Lombardia"}
        derived = {"regioni_ids": [10, 12], "beneficiari": []}
        checks = facet_prechecks(self.BANDO, company, derived)
        assert checks["regione"]["esito"] == "soddisfatto"
        assert checks["regione"]["azienda_sedi"] == 2

    def test_regione_non_soddisfatta_con_piu_sedi(self):
        company = {"regione_id": 10}
        derived = {"regioni_ids": [10, 3]}  # nessuna sede in una regione ammessa
        checks = facet_prechecks(self.BANDO, company, derived)
        assert checks["regione"]["esito"] == "non_soddisfatto"


def test_frac_copre_tutti_i_verdetti():
    assert set(FRAC) == {"soddisfatto", "parzialmente_soddisfatto", "non_soddisfatto", "dato_mancante"}
