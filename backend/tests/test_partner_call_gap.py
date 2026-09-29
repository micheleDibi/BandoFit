"""Gap analysis della call (WP5): requisiti tipizzati e copertura del creatore.

Proprietà difese:
- requisiti dall'AI-check sui report REALI dello scoring (`score_report` e
  `facet_prechecks` sul bando di tests/fixtures): il testo libero diventa
  sempre `manuale`, il verdetto conta solo come copertura;
- requisiti dai pre-check (id dal catalogo) e dallo snapshot delle regole:
  composizione → tipo_soggetto consorzio, vincoli territoriali e regole per
  partner → ogni_membro;
- `unisci`: deduplica territoriale e settoriale, prevale il peggiore;
- copertura del creatore sull'esempio guida (X copre B e D → cercati A e C);
- `copertura_nota` solo da template: nessun testo del modello (motivazioni,
  dati aziendali con i bilanci esatti) finisce nei requisiti;
- Q11: regole finanziarie solo dalle regole confermate dello snapshot.
"""

import copy
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas.ai_check import ExtractionResult, MatchingResult
from app.schemas.partenariato_criteri import (
    CriterioAteco,
    CriterioDimensione,
    CriterioManuale,
    CriterioRegione,
    CriterioRegolaFinanziaria,
    CriterioSettore,
    CriterioTag,
    CriterioTipoSoggetto,
)
from app.schemas.partner_call import RegoleCallSnapshot, RequisitoIn
from app.services import partenariato_validatore as pv
from app.services import partner_call_gap as gap
from app.services.ai_check_scoring import facet_prechecks, score_report
from app.services.bilanci_indicatori import EsercizioBilancio
from app.services.partenariato_criteri import profilo_candidato_da

FIXTURES = Path(__file__).parent / "fixtures" / "ai_check"
BANDO = json.loads((FIXTURES / "bando_flash.json").read_text())  # Lombardia, ATECO 49/52
LOMBARDIA, CALABRIA = 9, 4

CANARY_MOTIVAZIONE = "CANARYMOTIVO fatturato 7.654.321 euro"
CANARY_DATO = "CANARYDATO 7654321"

META = {"model": "test", "prompt_version": 1, "generated_at": "2026-09-29T00:00:00Z",
        "bando_hash": "x", "extraction_cached": False}
SEZIONI = {
    "META": "Regioni ammesse (catalogo): Lombardia",
    "S1": "Possono partecipare le imprese con sede operativa in Lombardia.",
    "S2": "Le imprese devono essere iscritte al Registro delle imprese da almeno due anni.",
    "S3": "Il settore di attività deve rientrare nei trasporti e nella logistica.",
    "S4": "Fino a 10 punti se nel partenariato è presente un organismo di ricerca.",
    "S5": "Fino a 20 punti per la qualità del progetto.",
}


def _citazione(sezione: str) -> dict:
    return {"sezione": sezione, "testo_esatto": SEZIONI[sezione]}


def estrazione(requisiti=None) -> ExtractionResult:
    return ExtractionResult.model_validate(
        {
            "requisiti_obbligatori": requisiti if requisiti is not None else [
                {"id": "R1", "testo": "Sede operativa in Lombardia", "categoria": "territoriale",
                 "dato_richiesto": "sede", "citazione": _citazione("S1")},
                {"id": "R2", "testo": "Iscrizione al Registro delle imprese da almeno due anni",
                 "categoria": "formale", "dato_richiesto": "data di iscrizione",
                 "citazione": _citazione("S2")},
                {"id": "R3", "testo": "Attività nel settore dei trasporti",
                 "categoria": "settoriale", "dato_richiesto": "ATECO",
                 "citazione": _citazione("S3")},
            ],
            "criteri_valutazione": [
                {"id": "C1", "nome": "Presenza di un organismo di ricerca",
                 "categoria": "soggettivo", "punti_max": 10, "citazione": _citazione("S4")},
                {"id": "C2", "nome": "Qualità del progetto", "categoria": "altro",
                 "punti_max": 20, "citazione": _citazione("S5")},
            ],
            "griglia": {"presente": True, "fonte": "contenuto", "punteggio_max_totale": 30,
                        "soglia_minima": None, "note": None},
        }
    )


def matching(esiti: dict[str, str]) -> MatchingResult:
    def verdetto(id_, esito):
        return {
            "id": id_,
            "esito": esito,
            "dato_azienda": {"campo": "bilanci.2024.fatturato", "valore": CANARY_DATO},
            "motivazione": CANARY_MOTIVAZIONE,
        }

    return MatchingResult.model_validate(
        {
            "requisiti": [verdetto(k, v) for k, v in esiti.items() if k.startswith("R")],
            "criteri": [verdetto(k, v) for k, v in esiti.items() if k.startswith("C")],
            "punti_di_forza": [{"testo": CANARY_MOTIVAZIONE, "ref": "R1"}],
            "punti_di_debolezza": [{"testo": CANARY_MOTIVAZIONE, "ref": None}],
            "dati_mancanti": [{"campo": "x", "descrizione": CANARY_MOTIVAZIONE, "ref": None}],
        }
    )


# Il creatore dell'AI-check: sede in Lombardia, ATECO 49, settore dichiarato 86.
COMPANY = {"settore_id": 86, "regione_nome": "Lombardia", "beneficiari": []}
DERIVED = {"regioni_ids": [LOMBARDIA], "regione_id": LOMBARDIA, "ateco_principale": "49.41",
           "ateco_divisione": "49", "classe_dimensionale": "piccola"}


def report_reale(esiti=None, *, derived=DERIVED, requisiti=None) -> dict:
    """Report assemblato dallo scoring vero sul bando della fixture."""
    esiti = esiti or {"R1": "soddisfatto", "R2": "dato_mancante", "R3": "soddisfatto",
                      "C1": "non_soddisfatto", "C2": "parzialmente_soddisfatto"}
    prechecks = facet_prechecks(BANDO, COMPANY, derived)
    return score_report(estrazione(requisiti), matching(esiti), prechecks, SEZIONI, META)


def creatore(**sovrascrivi):
    dati = {
        "company": COMPANY,
        "derived": DERIVED,
        "dossier": {"flags": {}},
        "profilo_partner": {"competenze": ["logistica_supply_chain"]},
        "esercizi": [],
        "storico_completo": True,
    }
    dati.update(sovrascrivi)
    return profilo_candidato_da(**dati)


def citazione_verificata(testo="Passaggio del bando") -> dict:
    return {"sezione": "D1-p3", "fonte_etichetta": "Avviso — pag. 3", "testo": testo,
            "verificata": True}


def regola(id_="F1", ambito="ciascun_partner", soglia="0.6") -> dict:
    return {
        "id": id_, "descrizione": "Costo della quota non superiore al 60% del fatturato medio",
        "ambito": ambito, "numeratore": "costo_quota", "denominatore": "fatturato_medio_2",
        "operatore": "le", "soglia": soglia, "soglia_variabile": None,
        "soglia_coefficiente": None, "unita": "rapporto",
        "origine_voce": "confermata", "citazione": citazione_verificata(),
    }


def snapshot(**voci) -> RegoleCallSnapshot:
    dati = {"modalita": {"valore": "obbligatorio", "origine_voce": "confermata",
                         "citazione": citazione_verificata()}}
    dati.update(voci)
    return RegoleCallSnapshot.model_validate(dati)


def composizione(id_="K1", tipo="organismo_ricerca", **extra) -> dict:
    return {"id": id_, "tipo_soggetto": tipo, "minimo": 1, "ruolo": "partner",
            "origine_voce": "confermata", "citazione": citazione_verificata(), **extra}


def vincolo(id_="V1", tipo="sede_operativa_regione", momento="domanda") -> dict:
    return {"id": id_, "tipo": tipo, "descrizione": "Sede operativa nella regione",
            "momento": momento, "origine_voce": "modificata"}


def _riga_requisito(bozza: gap.RequisitoBozza) -> dict:
    """La riga `partner_call_requisiti` che il validatore rilegge da una bozza."""
    return {"id": bozza.rif_origine or "R", "etichetta": "R1", "testo": bozza.testo,
            "criterio": None, "ambito": bozza.ambito, "origine": bozza.origine,
            "citazione": bozza.citazione}


# --------------------------------------------------------------- AI-check


class TestDaAiCheck:
    def test_report_reale_diventa_manuale(self):
        bozze = gap.requisiti_da_ai_check(report_reale())
        per_rif = {gap.id_voce(b.rif_origine): b for b in bozze}
        # requisiti obbligatori tutti, criteri solo su attributi di un partner
        assert set(per_rif) == {"R1", "R2", "R3", "C1"}
        for bozza in bozze:
            assert bozza.origine == "ai_check"
            assert isinstance(bozza.criterio, CriterioManuale)
            assert bozza.ambito == "consorzio"
        assert per_rif["R1"].categoria == "territoriale"
        assert per_rif["R1"].esiti_ai_check == ("coperto",)
        assert per_rif["R2"].esiti_ai_check == ("dato_mancante",)
        assert per_rif["C1"].premiale and per_rif["C1"].esiti_ai_check == ("non_coperto",)
        assert per_rif["C1"].testo == "Criterio di valutazione: Presenza di un organismo di ricerca"
        # la scheda del catalogo non fa fede: ritrovata dallo scoring, ma non verificata
        assert per_rif["R1"].citazione == {
            "sezione": "S1", "testo": SEZIONI["S1"], "verificata": False,
        }

    def test_citazioni_della_scheda_mai_verificate(self):
        """Correzione WP9: l'AI-check legge solo la scheda del catalogo (META,
        S1…), testo generato o classificato. Il suo «verificata» (passaggio
        ritrovato nella scheda) non basta: fa fede solo una pagina di un
        documento ufficiale, come per i pre-check e le regole. Il requisito
        resta, con la stessa copertura, e il validatore lo attribuisce al
        creatore."""
        report = {"requisiti": [
            {"id": "R1", "testo": "Sede in Puglia", "categoria": "soggettivo",
             "verdetto": "soddisfatto",
             "riferimento_bando": {"sezione": "S3", "testo": "sede in Puglia",
                                   "verificata": True}},
            {"id": "V2", "testo": "Vincolo di catalogo: PMI", "categoria": "dimensionale",
             "verdetto": "non_soddisfatto",
             "riferimento_bando": {"sezione": "META", "testo": "PMI", "verificata": True}},
            {"id": "R3", "testo": "Bilancio depositato", "categoria": "formale",
             "verdetto": "soddisfatto",
             "riferimento_bando": {"sezione": "D1-p4", "testo": "bilancio depositato",
                                   "verificata": True}},
        ], "criteri": [
            {"id": "C1", "nome": "Presenza di un organismo di ricerca",
             "categoria": "soggettivo", "verdetto": "non_soddisfatto",
             "riferimento_bando": {"sezione": "S2", "testo": "organismo", "verificata": True}},
        ]}
        bozze = gap.requisiti_da_ai_check(report)
        per_rif = {gap.id_voce(b.rif_origine): b for b in bozze}
        assert {k: b.citazione["verificata"] for k, b in per_rif.items()} == {
            "R1": False, "V2": False, "R3": True, "C1": False}
        assert per_rif["V2"].sintetico  # la voce sintetica resta riconosciuta
        assert [b.esiti_ai_check for b in bozze] == [
            ("coperto",), ("non_coperto",), ("coperto",), ("non_coperto",)]
        # al salvataggio, anche invariata, la citazione della scheda non fa fede
        richiesti = [
            RequisitoIn.model_validate({
                "testo": b.testo, "criterio": gap._json_criterio(b.criterio),
                "ambito": b.ambito, "origine": b.origine, "rif_origine": b.rif_origine,
                "citazione": {**b.citazione, "verificata": True}})
            for b in bozze
        ]
        uscita = gap.citazioni_dal_server(richiesti, da_ai_check=bozze)
        assert [c["verificata"] for c in uscita] == [False, False, True, False]
        fonti = [pv.requisito_da_riga({**_riga_requisito(b), "citazione": c}).regola.fonte
                 for b, c in zip(bozze, uscita, strict=True)]
        assert fonti == ["creatore", "creatore", "bando", "creatore"]

    def test_voce_sintetica_del_catalogo(self):
        # azienda fuori regione e nessun requisito territoriale estratto: lo
        # scoring aggiunge «Vincolo di catalogo» (sezione META)
        fuori = {**DERIVED, "regioni_ids": [CALABRIA], "regione_id": CALABRIA}
        solo_formale = [
            {"id": "R2", "testo": "Iscrizione al registro", "categoria": "formale",
             "dato_richiesto": "x", "citazione": _citazione("S2")},
        ]
        report = report_reale({"R2": "soddisfatto"}, derived=fuori, requisiti=solo_formale)
        sintetiche = [b for b in gap.requisiti_da_ai_check(report) if b.sintetico]
        assert len(sintetiche) == 1
        assert sintetiche[0].categoria == "territoriale"
        assert sintetiche[0].esiti_ai_check == ("non_coperto",)

    def test_report_assente_o_vuoto(self):
        assert gap.requisiti_da_ai_check(None) == []
        assert gap.requisiti_da_ai_check({"requisiti": [{"id": "R1", "testo": "ab"}, "x"]}) == []

    def test_testo_lungo_tagliato(self):
        report = {"requisiti": [{"id": "R1" * 30, "testo": "parola " * 200,
                                 "categoria": "altro", "verdetto": "boh"}]}
        (bozza,) = gap.requisiti_da_ai_check(report)
        assert len(bozza.testo) == 500 and bozza.testo.endswith("…")
        assert len(bozza.rif_origine) == 40
        assert bozza.esiti_ai_check == ()


# ------------------------------------------------------------- pre-check


class TestDaPrecheck:
    def test_facet_del_bando_tipizzati(self):
        prechecks = facet_prechecks(BANDO, COMPANY, DERIVED)
        bozze = gap.requisiti_da_precheck(prechecks, bando=BANDO)
        per_rif = {b.rif_origine: b for b in bozze}
        # ATECO e settore sono alternativi: con entrambi vale l'ATECO
        assert set(per_rif) == {"regione", "ateco", "beneficiari"}
        assert per_rif["regione"].criterio == CriterioRegione(regioni_ids=[LOMBARDIA])
        assert per_rif["regione"].ambito == "ogni_membro"
        assert per_rif["ateco"].criterio == CriterioAteco(divisioni=["49", "52"])
        assert per_rif["ateco"].ambito == "consorzio"
        assert per_rif["beneficiari"].criterio == CriterioTipoSoggetto(valori=["impresa"])
        assert "Lombardia" in per_rif["regione"].testo
        assert per_rif["regione"].citazione["sezione"] == "META"

    def test_citazione_della_scheda_non_verificata(self):
        """Completamento WP9: la citazione costruita dai metadati della scheda
        del catalogo (testo generato o classificato) non fa fede: esce non
        verificata. Il requisito resta (anche visibile ai terzi se cercato),
        la copertura del creatore non cambia e il validatore lo attribuisce ai
        dati del catalogo, senza citazione."""
        prechecks = facet_prechecks(BANDO, COMPANY, DERIVED)
        bozze = gap.requisiti_da_precheck(prechecks, bando=BANDO)
        assert {b.rif_origine for b in bozze} == {"regione", "ateco", "beneficiari"}
        for bozza in bozze:
            assert bozza.citazione["sezione"] == "META"
            assert bozza.citazione["verificata"] is False
            assert bozza.citazione["fonte_etichetta"] == "Scheda del bando"
        come_prima = [replace(b, citazione={**b.citazione, "verificata": True}) for b in bozze]
        profilo = creatore()
        dopo = gap.copertura_creatore(bozze, profilo)
        prima = gap.copertura_creatore(come_prima, profilo)
        assert [(r.copertura_creatore, r.copertura_fonte, r.cercato, r.criterio, r.ambito)
                for r in dopo] == [
            (r.copertura_creatore, r.copertura_fonte, r.cercato, r.criterio, r.ambito)
            for r in prima]
        assert [r.citazione.verificata for r in dopo] == [False] * len(bozze)
        for bozza in bozze:
            regola = pv.requisito_da_riga(_riga_requisito(bozza)).regola
            assert (regola.fonte, regola.citazione) == ("bando", None)

    def test_settore_senza_ateco(self):
        bando = {**BANDO, "bando_codici_ateco": []}
        prechecks = facet_prechecks(bando, COMPANY, DERIVED)
        per_rif = {b.rif_origine: b for b in gap.requisiti_da_precheck(prechecks, bando=bando)}
        assert isinstance(per_rif["settore"].criterio, CriterioSettore)
        assert per_rif["settore"].criterio.settori_ids == [47, 59, 72, 83, 86]

    def test_facet_non_applicabile_non_genera(self):
        bando = {**BANDO, "bando_regioni": [], "bando_codici_ateco": [], "bando_settori": [],
                 "bando_beneficiari": []}
        prechecks = facet_prechecks(bando, COMPANY, DERIVED)
        assert gap.requisiti_da_precheck(prechecks, bando=bando) == []
        assert gap.requisiti_da_precheck(None, bando=bando) == []

    def test_senza_id_del_catalogo_resta_manuale(self):
        # solo il pre-check del report (nomi, non id): regione manuale
        prechecks = report_reale()["verifiche_strutturate"]
        per_rif = {b.rif_origine: b for b in gap.requisiti_da_precheck(prechecks)}
        assert isinstance(per_rif["regione"].criterio, CriterioManuale)
        assert per_rif["regione"].ambito == "ogni_membro"
        # l'ATECO del pre-check ha già le divisioni
        assert per_rif["ateco"].criterio == CriterioAteco(divisioni=["49", "52"])

    def test_beneficiari_mappati_sul_vocabolario(self):
        assert gap.tipi_da_beneficiari([27, 31]) == ["pmi", "organismo_ricerca", "universita"]
        assert gap.tipi_da_beneficiari([999]) == []
        crit, ambito = gap.criterio_da_requisito("precheck", {"chiave": "beneficiari",
                                                              "ids": [1, 5]})
        assert isinstance(crit, CriterioManuale)  # solo «altro»: non valutabile


# ---------------------------------------------------------------- regole


class TestDaRegole:
    def test_composizione_vincoli_e_regole(self):
        snap = snapshot(
            composizione=[composizione(), composizione("K2", "altro",
                                                       tipo_soggetto_testo="Cluster regionale")],
            vincoli=[vincolo(), vincolo("V2", "indipendenza"),
                     vincolo("V3", "requisito_capofila")],
            regole_finanziarie=[regola(), regola("F2", ambito="capofila")],
        )
        bozze = gap.requisiti_da_regole(snap, regioni_bando=[CALABRIA])
        per_rif = {gap.id_voce(b.rif_origine): b for b in bozze}
        assert set(per_rif) == {"K1", "K2", "V1", "V3", "F1", "F2"}  # indipendenza no
        # le regole finanziarie tengono l'id dello snapshot (Q11)
        assert {b.rif_origine for b in bozze if b.origine == "regola_finanziaria"} == {"F1", "F2"}
        assert per_rif["K1"].criterio == CriterioTipoSoggetto(valori=["organismo_ricerca"])
        assert per_rif["K1"].ambito == "consorzio"
        assert per_rif["K1"].origine == "bando_partenariato"
        assert "organismo di ricerca come partner" in per_rif["K1"].testo
        assert isinstance(per_rif["K2"].criterio, CriterioManuale)
        assert "Cluster regionale" in per_rif["K2"].testo
        assert per_rif["V1"].criterio == CriterioRegione(regioni_ids=[CALABRIA])
        assert per_rif["V1"].ambito == "ogni_membro"
        assert isinstance(per_rif["V3"].criterio, CriterioManuale)
        assert isinstance(per_rif["F1"].criterio, CriterioRegolaFinanziaria)
        assert per_rif["F1"].origine == "regola_finanziaria"
        assert per_rif["F1"].ambito == "ogni_membro"
        assert per_rif["F2"].ambito == "consorzio"
        assert per_rif["K1"].citazione["verificata"] is True

    @pytest.mark.parametrize(
        "momento, modalita",
        [("domanda", "sede_attuale"), ("concessione", "sede_entro_erogazione"),
         ("prima_erogazione", "sede_entro_erogazione"), ("non_indicato", "sede_entro_erogazione")],
    )
    def test_momento_del_vincolo_territoriale(self, momento, modalita):
        snap = snapshot(vincoli=[vincolo(momento=momento)])
        (bozza,) = gap.requisiti_da_regole(snap, regioni_bando=[CALABRIA])
        assert bozza.criterio.modalita == modalita

    def test_vincolo_territoriale_senza_regioni_manuale(self):
        (bozza,) = gap.requisiti_da_regole(snapshot(vincoli=[vincolo()]))
        assert isinstance(bozza.criterio, CriterioManuale)
        assert bozza.ambito == "ogni_membro"

    @pytest.mark.parametrize("origine, fonte", [("confermata", "bando"), ("modificata", "creatore")])
    def test_citazione_fa_fede_solo_se_confermata(self, origine, fonte):
        # Una voce inserita come «modificata» (per esempio una voce della
        # scheda del catalogo, che entra solo così) porta la citazione come
        # riferimento, ma nel validatore non è «Regola del bando»: come
        # `_origine_voce` sullo snapshot.
        snap = snapshot(
            composizione=[composizione(origine_voce=origine)],
            vincoli=[dict(vincolo(), origine_voce=origine, citazione=citazione_verificata())],
        )
        bozze = gap.requisiti_da_regole(snap, regioni_bando=[CALABRIA])
        assert len(bozze) == 2
        for bozza in bozze:
            assert bozza.citazione["testo"] == "Passaggio del bando"
            assert bozza.citazione["sezione"] == "D1-p3"
            assert bozza.citazione["verificata"] is (origine == "confermata")
            assert pv.requisito_da_riga(_riga_requisito(bozza)).regola.fonte == fonte

    @pytest.mark.parametrize("sezione", ["META", "S2", "[d1 pag. 3]"])
    def test_confermata_fa_fede_solo_da_una_pagina_ufficiale(self, sezione):
        """Completamento WP9: uno snapshot confermato prima della regola dei
        documenti ufficiali può avere una voce `confermata` citata dalla
        scheda del catalogo: il suo requisito non è verificato. Una pagina
        ufficiale scritta in un'altra forma («[d1 pag. 3]») vale."""
        citazione = {**citazione_verificata(), "sezione": sezione}
        snap = snapshot(composizione=[composizione(citazione=citazione)])
        (bozza,) = gap.requisiti_da_regole(snap)
        ufficiale = sezione.startswith("[d1")
        assert bozza.citazione["verificata"] is ufficiale
        assert pv.requisito_da_riga(_riga_requisito(bozza)).regola.fonte == (
            "bando" if ufficiale else "creatore")

    def test_snapshot_come_dict_o_assente(self):
        assert gap.requisiti_da_regole(None) == []


# ------------------------------------------------------- prova dal server


def _in(**campi) -> RequisitoIn:
    return RequisitoIn.model_validate({"testo": "Requisito di prova", **campi})


class TestCitazioniDalServer:
    """Completamento WP9: la citazione salvata la decide il server, mai il
    client (`citazioni_dal_server`)."""

    def _fonti(self):
        snap = snapshot(composizione=[composizione()], regole_finanziarie=[regola()])
        da_regole = gap.requisiti_da_regole(snap)
        da_ai_check = gap.requisiti_da_ai_check(report_reale())
        return da_regole, da_ai_check

    def _come_generato(self, bozza: gap.RequisitoBozza, **modifiche) -> RequisitoIn:
        dati = {"testo": bozza.testo, "criterio": gap._json_criterio(bozza.criterio),
                "ambito": bozza.ambito, "origine": bozza.origine,
                "rif_origine": bozza.rif_origine, "citazione": bozza.citazione}
        dati.update(modifiche)
        return RequisitoIn.model_validate(dati)

    def test_manuale_e_precheck_mai_verificati(self):
        falsa = {"sezione": "D1-p3", "testo": "Passaggio inventato", "verificata": True}
        richiesti = [_in(citazione=falsa),
                     _in(origine="precheck", rif_origine="regione", citazione=falsa)]
        uscita = gap.citazioni_dal_server(richiesti, da_regole=self._fonti()[0],
                                          da_ai_check=self._fonti()[1])
        assert [c["verificata"] for c in uscita] == [False, False]
        assert uscita[0]["testo"] == "Passaggio inventato"  # resta come riferimento
        riga = {**_riga_requisito(gap.RequisitoBozza(origine="manuale", testo="x",
                                                     criterio=None)),
                "citazione": uscita[0]}
        assert pv.requisito_da_riga(riga).regola.fonte == "creatore"

    def test_voce_dello_snapshot_confermata_e_ai_check(self):
        da_regole, da_ai_check = self._fonti()
        k1 = next(b for b in da_regole if b.origine == "bando_partenariato")
        f1 = next(b for b in da_regole if b.origine == "regola_finanziaria")
        r1 = next(b for b in da_ai_check if gap.id_voce(b.rif_origine) == "R1")
        # il client prova a cambiare il testo della citazione o a spegnerla
        falsa = {"sezione": "D9-p9", "testo": "Altro", "verificata": False}
        richiesti = [self._come_generato(k1, citazione=falsa), self._come_generato(f1),
                     self._come_generato(r1, citazione=falsa)]
        uscita = gap.citazioni_dal_server(richiesti, da_regole=da_regole,
                                          da_ai_check=da_ai_check)
        assert uscita == [k1.citazione, f1.citazione, r1.citazione]
        assert uscita[0]["verificata"] is True and uscita[0]["sezione"] == "D1-p3"

    @pytest.mark.parametrize("modifica", [
        {"testo": "Almeno un soggetto di tipo università come partner"},
        {"criterio": {"tipo": "tipo_soggetto", "valori": ["universita"]}},
        {"ambito": "ogni_membro"},
        {"rif_origine": "K9~0000000000"},
        {"origine": "ai_check"},
    ], ids=["testo", "criterio", "ambito", "riferimento", "origine"])
    def test_requisito_riscritto_non_verificato(self, modifica):
        da_regole, da_ai_check = self._fonti()
        k1 = next(b for b in da_regole if b.origine == "bando_partenariato")
        richiesto = self._come_generato(k1, **modifica)
        [citazione] = gap.citazioni_dal_server([richiesto], da_regole=da_regole,
                                               da_ai_check=da_ai_check)
        assert citazione["verificata"] is False

    def test_senza_citazione(self):
        assert gap.citazioni_dal_server([_in()]) == [None]
        dati = snapshot(composizione=[composizione()]).model_dump(mode="json")
        assert len(gap.requisiti_da_regole(dati)) == 1

    def test_origine_del_precheck_decisa_dal_server(self):
        """Correzione WP9: un `precheck` resta tale solo se coincide con uno
        generato ora dal catalogo; inventato, riscritto o senza catalogo è
        `manuale`, senza riferimento. Le altre origini restano (la loro prova
        la decide `citazioni_dal_server`). I caratteri invisibili che lo
        schema toglie al testo non bastano a declassare un pre-check vero."""
        # nel nome del catalogo un trattino morbido, che `RequisitoIn` toglie
        bando = {**BANDO, "bando_regioni": [{"regioni": {"id": LOMBARDIA,
                                                          "nome": "Lom­bardia"}}]}
        generati = gap.requisiti_da_precheck(None, bando=bando)
        regione = next(b for b in generati if b.rif_origine == "regione")
        vero = RequisitoIn.model_validate({
            "testo": regione.testo, "criterio": gap._json_criterio(regione.criterio),
            "ambito": regione.ambito, "origine": "precheck", "rif_origine": "regione"})
        assert "­" in regione.testo and "­" not in vero.testo
        richiesti = [
            vero,
            vero.model_copy(update={"testo": "Sede in Lombardia da almeno tre anni"}),
            vero.model_copy(update={"rif_origine": "ateco"}),
            _in(testo="Certificazione ISO 27001", origine="precheck", rif_origine="regione"),
            _in(origine="ai_check", rif_origine="R1~0123456789"),
        ]
        assert gap.origini_dal_server(richiesti, da_precheck=generati) == [
            ("precheck", "regione"), ("manuale", None), ("manuale", None),
            ("manuale", None), ("ai_check", "R1~0123456789")]
        assert gap.origini_dal_server([vero]) == [("manuale", None)]  # catalogo assente


# ---------------------------------------------------------------- unisci


class TestUnisci:
    def test_deduplica_territoriale_e_settoriale_prevale_il_peggiore(self):
        prechecks = facet_prechecks(BANDO, COMPANY, DERIVED)
        # l'AI-check dice che R1 (territoriale) NON è soddisfatto: il criterio
        # tipizzato (sede in Lombardia, coperto dal registro) resta, ma sulla
        # copertura prevale il peggiore
        report = report_reale({"R1": "non_soddisfatto", "R2": "soddisfatto",
                               "R3": "soddisfatto", "C1": "non_soddisfatto"})
        unite = gap.unisci(
            da_precheck=gap.requisiti_da_precheck(prechecks, bando=BANDO),
            da_ai_check=gap.requisiti_da_ai_check(report),
        )
        per_rif = {gap.id_voce(b.rif_origine): b for b in unite}
        assert set(per_rif) == {"regione", "ateco", "beneficiari", "R2", "C1"}
        assert tuple(map(gap.id_voce, per_rif["regione"].unito_con)) == ("R1",)
        assert tuple(map(gap.id_voce, per_rif["ateco"].unito_con)) == ("R3",)
        out = {gap.id_voce(r.rif_origine): r
               for r in gap.copertura_creatore(unite, creatore())}
        assert out["regione"].copertura_creatore == "non_coperto"
        assert out["regione"].copertura_fonte == "ai_check"
        assert out["ateco"].copertura_creatore == "coperto"
        assert out["ateco"].copertura_fonte == "registro"

    def test_senza_tipizzato_la_voce_resta(self):
        unite = gap.unisci(da_ai_check=gap.requisiti_da_ai_check(report_reale()))
        assert {gap.id_voce(b.rif_origine) for b in unite} == {"R1", "R2", "R3", "C1"}

    def test_premiale_mai_assorbito(self):
        prechecks = facet_prechecks(BANDO, COMPANY, DERIVED)
        report = {"criteri": [{"id": "C9", "nome": "Sede in area interna",
                               "categoria": "territoriale", "verdetto": "soddisfatto"}]}
        unite = gap.unisci(
            da_precheck=gap.requisiti_da_precheck(prechecks, bando=BANDO),
            da_ai_check=gap.requisiti_da_ai_check(report),
        )
        assert "C9" in {gap.id_voce(b.rif_origine) for b in unite}

    def test_doppioni_tipizzati_con_precedenza_alle_regole(self):
        prechecks = facet_prechecks(BANDO, COMPANY, DERIVED)
        snap = snapshot(
            composizione=[composizione("K1", "impresa")],
            vincoli=[vincolo(momento="concessione")],
        )
        unite = gap.unisci(
            da_regole=gap.requisiti_da_regole(snap, regioni_bando=[LOMBARDIA]),
            da_precheck=gap.requisiti_da_precheck(prechecks, bando=BANDO),
        )
        per_rif = {gap.id_voce(b.rif_origine): b for b in unite}
        # beneficiari «Imprese» = composizione «impresa»; regione del catalogo
        # = vincolo territoriale confermato (resta la sua modalità)
        assert set(per_rif) == {"K1", "V1", "ateco"}
        assert per_rif["K1"].unito_con == ("beneficiari",)
        assert per_rif["V1"].unito_con == ("regione",)
        assert per_rif["V1"].criterio.modalita == "sede_entro_erogazione"

    def test_non_modifica_gli_ingressi(self):
        bozze = gap.requisiti_da_precheck(facet_prechecks(BANDO, COMPANY, DERIVED), bando=BANDO)
        gap.unisci(da_precheck=bozze, da_ai_check=gap.requisiti_da_ai_check(report_reale()))
        assert all(b.unito_con == () and b.esiti_ai_check == () for b in bozze)


class TestEvidenzeAssorbite:
    """Al salvataggio i requisiti tipizzati ricevono i verdetti delle voci
    dell'AI-check che `unisci` ha assorbito nella proposta."""

    def test_stessa_copertura_della_proposta(self):
        prechecks = facet_prechecks(BANDO, COMPANY, DERIVED)
        report = report_reale({"R1": "non_soddisfatto", "R2": "soddisfatto",
                               "R3": "soddisfatto", "C1": "non_soddisfatto"})
        proposta = gap.copertura_creatore(
            gap.unisci(da_precheck=gap.requisiti_da_precheck(prechecks, bando=BANDO),
                       da_ai_check=gap.requisiti_da_ai_check(report)),
            creatore(),
        )
        # il client rimanda i requisiti senza evidenze (come RequisitoIn): il
        # servizio rimette il verdetto sul riferimento e poi quelli assorbiti
        da_ai = gap.requisiti_da_ai_check(report)
        propri = {b.rif_origine: b.esiti_ai_check for b in da_ai}
        salvati = [
            gap.RequisitoBozza(origine=r.origine, testo=r.testo, criterio=r.criterio,
                               ambito=r.ambito, rif_origine=r.rif_origine,
                               esiti_ai_check=propri.get(r.rif_origine, ())
                               if r.origine == "ai_check" else ())
            for r in proposta
        ]
        rimasti = {r.rif_origine for r in salvati if r.origine == "ai_check"}
        gap.evidenze_assorbite(salvati, [b for b in da_ai if b.rif_origine not in rimasti])
        regione = next(b for b in salvati if b.rif_origine == "regione")
        assert regione.esiti_ai_check == ("non_coperto",)
        ricalcolo = gap.copertura_creatore(salvati, creatore())
        assert [(r.copertura_creatore, r.copertura_fonte) for r in ricalcolo] == [
            (r.copertura_creatore, r.copertura_fonte) for r in proposta]
        # le voci dell'AI-check che restano requisiti tengono solo il loro verdetto
        assert all(len(b.esiti_ai_check) <= 1 for b in salvati if b.origine == "ai_check")

    def test_manuali_e_premiali_mai_bersaglio(self):
        manuale = gap.RequisitoBozza(origine="manuale", testo="Sede", ambito="ogni_membro",
                                     criterio=CriterioRegione(regioni_ids=[LOMBARDIA]))
        report = {"requisiti": [{"id": "R1", "testo": "Sede operativa in Lombardia",
                                 "categoria": "territoriale", "verdetto": "non_soddisfatto"}],
                  "criteri": [{"id": "C1", "nome": "Sede in area interna",
                               "categoria": "territoriale", "verdetto": "non_soddisfatto"}]}
        gap.evidenze_assorbite([manuale], gap.requisiti_da_ai_check(report))
        assert manuale.esiti_ai_check == ()


class TestRiferimenti:
    """Il riferimento dei requisiti dall'AI-check e dalle voci dello snapshot
    porta l'impronta del contenuto: una voce rinumerata è un altro requisito."""

    def test_ai_check_stesso_id_testo_diverso(self):
        uno = gap.requisiti_da_ai_check(
            {"requisiti": [{"id": "R3", "testo": "Certificazione ISO 9001", "categoria": "altro"}]})
        due = gap.requisiti_da_ai_check(
            {"requisiti": [{"id": "R3", "testo": "Sede operativa in Puglia", "categoria": "altro"},
                           {"id": "R7", "testo": "Certificazione ISO 9001",
                            "categoria": "altro"}]})
        assert gap.id_voce(uno[0].rif_origine) == gap.id_voce(due[0].rif_origine) == "R3"
        assert uno[0].rif_origine != due[0].rif_origine
        # stesso testo con un altro id: riferimento diverso (l'id resta parte dell'identità)
        assert uno[0].rif_origine != due[1].rif_origine
        # stessa estrazione, stesso riferimento
        assert uno[0].rif_origine == gap.requisiti_da_ai_check(
            {"requisiti": [{"id": "R3", "testo": "Certificazione ISO 9001",
                            "categoria": "altro"}]})[0].rif_origine
        assert len(uno[0].rif_origine) <= gap.RIF_MAX

    def test_voci_dello_snapshot(self):
        def rif(**voci):
            return {b.origine + ":" + gap.id_voce(b.rif_origine): b.rif_origine
                    for b in gap.requisiti_da_regole(snapshot(**voci), regioni_bando=[CALABRIA])}

        base = rif(composizione=[composizione()], vincoli=[vincolo()])
        # ritoccare il numero minimo non cambia requisito; cambiare tipo sì
        assert rif(composizione=[composizione(minimo=2)], vincoli=[vincolo()]) == base
        altro_tipo = rif(composizione=[composizione("K1", "impresa")], vincoli=[vincolo()])
        assert altro_tipo["bando_partenariato:K1"] != base["bando_partenariato:K1"]
        # un vincolo diverso con lo stesso id (voce aggiunta e poi sostituita)
        diverso = dict(vincolo(), descrizione="Sede legale in Calabria da almeno due anni")
        assert rif(composizione=[composizione()], vincoli=[diverso])[
            "bando_partenariato:V1"] != base["bando_partenariato:V1"]


# ------------------------------------------------------------- copertura


def bozza(rif, criterio, ambito="consorzio", **extra):
    return gap.RequisitoBozza(origine="manuale", testo=f"Requisito {rif}", criterio=criterio,
                              ambito=ambito, rif_origine=rif, **extra)


class TestCopertura:
    def test_esempio_guida(self):
        """X (creatore, capofila): ATECO 62, piccola, sede in Calabria. Copre B
        e D → cercati A e C."""
        x = profilo_candidato_da(
            company={},
            derived={"regioni_ids": [CALABRIA], "ateco_principale": "62.01",
                     "classe_dimensionale": "piccola"},
            dossier={"flags": {}},
            profilo_partner={"competenze": ["sviluppo_software"]},
        )
        requisiti = [
            bozza("A", CriterioTipoSoggetto(valori=["organismo_ricerca"])),
            bozza("B", CriterioAteco(divisioni=["62"])),
            bozza("C", CriterioTag(tags=["prototipazione_testing"])),
            bozza("D", CriterioDimensione(valori=["micro", "piccola", "media"])),
            bozza("T", CriterioRegione(regioni_ids=[CALABRIA]), ambito="ogni_membro"),
        ]
        out = gap.copertura_creatore(requisiti, x)
        assert [r.etichetta for r in out] == ["A", "B", "C", "D", "E"]
        esiti = {r.rif_origine: r.copertura_creatore for r in out}
        assert esiti == {"A": "non_coperto", "B": "coperto", "C": "non_coperto",
                         "D": "coperto", "T": "coperto"}
        assert [r.rif_origine for r in out if r.cercato] == ["A", "C"]
        riep = gap.riepilogo(out)
        assert (riep.coperti, riep.non_coperti, riep.dato_mancante) == (3, 2, 0)

    def test_cercato_indicato_prevale_e_ogni_membro_mai_di_default(self):
        x = creatore()
        out = gap.copertura_creatore(
            [
                bozza("A", CriterioTipoSoggetto(valori=["universita"]), cercato=False),
                bozza("B", CriterioRegione(regioni_ids=[CALABRIA]), ambito="ogni_membro"),
                bozza("C", CriterioManuale(), premiale=True, esiti_ai_check=("non_coperto",)),
            ],
            x,
        )
        assert [r.cercato for r in out] == [False, False, False]
        assert out[1].copertura_creatore == "non_coperto"

    def test_manuale_con_verdetto_ai_check(self):
        out = gap.copertura_creatore(
            [bozza("R2", CriterioManuale(), esiti_ai_check=("dato_mancante",)),
             bozza("R4", CriterioManuale())],
            creatore(),
        )
        assert out[0].copertura_creatore == "dato_mancante"
        assert out[0].copertura_fonte == "ai_check"
        assert out[1].copertura_creatore == "non_valutabile"
        assert out[1].copertura_fonte == "nessuna"

    def test_regole_finanziarie_del_creatore(self):
        snap = snapshot(regole_finanziarie=[regola(), regola("F2", ambito="media_pesata_quote"),
                                            regola("F3", ambito="capofila")])
        esercizi = [EsercizioBilancio(anno=a, valori={"fatturato": Decimal(1_000_000)})
                    for a in (2023, 2024)]
        x = creatore(esercizi=esercizi)
        bozze = gap.requisiti_da_regole(snap)
        # senza budget: dato mancante con la nota che chiede budget e quota
        senza = gap.copertura_creatore(bozze, x)
        assert senza[0].copertura_creatore == "dato_mancante"
        assert senza[0].copertura_nota == (
            "Da verificare: indica il budget del progetto e la tua quota"
        )
        # aggregata: non si valuta sul solo creatore
        assert senza[1].copertura_creatore == "non_valutabile"
        assert senza[1].copertura_nota == "Si valuta sull'intero partenariato"
        # budget esatto 1 M€, quota 50%: 500k / 1 M€ = 0,5 ≤ 0,6
        quota = gap.intervallo_budget("500k_1m", Decimal(1_000_000))
        from app.services.partenariato_criteri import intervallo_costo_quota

        costo = intervallo_costo_quota(quota, Decimal(50))
        con = gap.copertura_creatore(bozze, x, costo, ruolo_creatore="cerco_capofila")
        assert con[0].copertura_creatore == "coperto"
        assert con[0].copertura_nota == "Coperto secondo i tuoi bilanci"
        # la regola del capofila in una call «cerco capofila» riguarda l'altro
        assert con[2].copertura_nota == "Riguarda il capofila che cerchi"
        # sulla sola fascia 500k_1m: 250k..500k su 1 M€ → sempre ≤ 0,6
        fascia = intervallo_costo_quota(gap.intervallo_budget("500k_1m"), Decimal(50))
        assert gap.copertura_creatore(bozze[:1], x, fascia)[0].copertura_creatore == "coperto"
        # fascia larga a cavallo della soglia → incerto «dipende dal budget»
        larga = intervallo_costo_quota(gap.intervallo_budget("1m_2m"), Decimal(50))
        incerto = gap.copertura_creatore(bozze[:1], x, larga)[0]
        assert incerto.copertura_creatore == "incerto"
        assert incerto.copertura_nota == "Da verificare: dipende dal budget del progetto"

    def test_da_righe_del_db_e_da_requisito_in(self):
        riga = {
            "id": "7a1d6a8e-9d59-4f6b-8b9a-0f3c1b2d4e5f", "testo": "Sede operativa in Lombardia",
            "criterio": {"tipo": "manuale"}, "ambito": "consorzio", "cercato": True,
            "origine": "ai_check", "rif_origine": "R1", "copertura_creatore": "non_coperto",
            "copertura_fonte": "ai_check", "citazione": {"sezione": "S1", "testo": "x",
                                                        "verificata": True},
        }
        entrata = RequisitoIn(testo="Almeno un'università", origine="manuale",
                              criterio={"tipo": "tipo_soggetto", "valori": ["universita"]})
        out = gap.copertura_creatore([riga, entrata], creatore())
        assert str(out[0].id) == riga["id"]
        assert out[0].copertura_creatore == "non_coperto"  # il verdetto salvato resta
        assert out[0].cercato is True
        assert out[0].citazione.sezione == "S1"
        assert out[1].copertura_creatore == "non_coperto"
        assert out[1].cercato is False  # indicato dal client (default dello schema)

    def test_citazione_con_url_non_https_scartata(self):
        b = bozza("A", CriterioManuale(), citazione={"sezione": "D1-p2", "testo": "t",
                                                     "verificata": True,
                                                     "url_documento": "http://x.it/a.pdf",
                                                     "pagina": 2})
        (out,) = gap.copertura_creatore([b], creatore())
        assert out.citazione.url_documento is None and out.citazione.pagina == 2


# --------------------------------------------------- testi solo da template


class TestNoteSoloDaTemplate:
    def test_nessun_testo_del_modello_nei_requisiti(self):
        prechecks = facet_prechecks(BANDO, COMPANY, DERIVED)
        report = report_reale()
        unite = gap.unisci(
            da_regole=gap.requisiti_da_regole(snapshot(regole_finanziarie=[regola()])),
            da_precheck=gap.requisiti_da_precheck(prechecks, bando=BANDO),
            da_ai_check=gap.requisiti_da_ai_check(report),
        )
        out = gap.copertura_creatore(unite, creatore())
        serializzato = json.dumps([r.model_dump(mode="json") for r in out], ensure_ascii=False)
        assert "CANARYMOTIVO" not in serializzato
        assert "CANARYDATO" not in serializzato
        assert "7654321" not in serializzato and "7.654.321" not in serializzato
        note = set(gap.NOTE_COPERTURA.values())
        for requisito in out:
            assert requisito.copertura_nota in note

    def test_ogni_combinazione_ha_un_template(self):
        note = set(gap.NOTE_COPERTURA.values())
        for esito in ("coperto", "non_coperto", "dato_mancante", "incerto", "non_valutabile"):
            for fonte in ("registro", "bilanci", "dichiarato", "ai_check", "nessuna"):
                for motivo in (None, "costo_quota_mancante", "dipende_budget", "ignoto"):
                    assert gap.nota_copertura(esito, fonte, motivo) in note


# --------------------------------------------------------- budget e regole


class TestBudgetERegole:
    def test_intervallo_budget(self):
        assert gap.intervallo_budget(None) is None
        assert gap.intervallo_budget("inventata") is None
        assert gap.intervallo_budget("50k_150k") == (Decimal(50_000), Decimal(150_000))
        assert gap.intervallo_budget("oltre_5m") == (Decimal(5_000_000), Decimal("Infinity"))
        # il budget esatto (riservato) prevale sulla fascia
        assert gap.intervallo_budget("oltre_5m", "1200000.50") == (
            Decimal("1200000.50"), Decimal("1200000.50"),
        )

    def test_regole_finanziarie_solo_se_confermate_e_identiche(self):
        snap = snapshot(regole_finanziarie=[regola()])
        (buona,) = gap.requisiti_da_regole(snap)
        assert gap.errori_regole_finanziarie([buona], snap) == []
        # soglia cambiata dal client: non è più la regola del bando
        dati = gap.criterio_da_requisito("regola_finanziaria",
                                         snap.regole_finanziarie[0])[0].model_dump()
        dati["regola"]["soglia"] = "0.9"
        alterata = RequisitoIn(testo="Regola", origine="regola_finanziaria",
                               rif_origine="F1", criterio=dati)
        contratto = {k: v for k, v in regola("F9").items()
                     if k not in ("origine_voce", "citazione")}
        ignota = RequisitoIn(testo="Regola", origine="regola_finanziaria", rif_origine="F9",
                             criterio={"tipo": "regola_finanziaria", "regola": contratto})
        errori = gap.errori_regole_finanziarie([alterata, ignota], snap)
        assert len(errori) == 2
        assert gap.errori_regole_finanziarie([alterata], None)

    def test_payload_per_la_rpc(self):
        (req,) = gap.copertura_creatore(
            [bozza("A", CriterioAteco(divisioni=["49"]), citazione={"sezione": "META",
                                                                    "testo": "t",
                                                                    "verificata": True})],
            creatore(),
        )
        payload = gap.payload_requisito(req)
        assert set(payload) <= set(gap.CHIAVI_RPC_REQUISITO)
        assert "id" not in payload and "etichetta" not in payload and "ordine" not in payload
        assert payload["criterio"] == {"tipo": "ateco", "divisioni": ["49"]}
        assert payload["copertura_nota"] == "Coperto dal Registro Imprese"
        assert gap.payload_requisito(req, con_etichetta=True)["etichetta"] == "A"
        json.dumps(payload)  # serializzabile così com'è

    def test_etichette_brevi(self):
        assert [gap.etichetta_breve(i) for i in (0, 1, 25, 26, 27, 51, 52)] == [
            "A", "B", "Z", "AA", "AB", "AZ", "BA",
        ]


# ------------------------------------- voci confermate contro l'estrazione


SEZ_REGOLE = {
    "META": "Titolo: Bando reti",
    "D1-p3": (
        "Le imprese possono partecipare in forma associata mediante ATS. Il partenariato è "
        "composto da almeno 3 imprese, tra cui almeno un organismo di ricerca."
    ),
    "D1-p4": (
        "Il costo della quota non può superare il 60% del fatturato medio degli ultimi due "
        "esercizi. Le imprese devono avere sede operativa in Piemonte o in Atlantide."
    ),
}
FONTI_REGOLE = [{"n": 1, "etichetta": "Avviso", "url": "https://regione.example.it/a.pdf"}]
LOOKUPS_REGOLE = {"regioni": [{"id": 1, "nome": "Piemonte"}, {"id": 2, "nome": "Lombardia"}]}


def _cit(sezione, testo):
    return {"sezione": sezione, "testo": testo}


def regole_estratte(*, altri_vincoli: tuple = (), sezioni: dict | None = None) -> dict:
    """Regole post-elaborate VERE (partenariato_regole.post_elabora)."""
    from app.schemas.partenariato import PartenariatoEstrazione
    from app.services.partenariato_regole import post_elabora

    # Forma compatta dello schema del modello: "" = assente, numeri in stringa.
    nessuna = _cit("", "")
    estrazione = {
        "modalita": "obbligatorio",
        "modalita_citazione": _cit("D1-p3", "partecipare in forma associata mediante ATS"),
        "forme_ammesse": [
            {"forma": "ats", "note": "", "citazione": _cit("D1-p3", "mediante ATS")},
        ],
        "costituzione": "non_indicato", "costituzione_citazione": nessuna,
        "partner_min": "3", "partner_min_citazione": _cit("D1-p3", "almeno 3 imprese"),
        "partner_max": "", "partner_max_citazione": nessuna, "conteggio_note": "",
        "composizione": [
            {"id": "K1", "tipo_soggetto": "organismo_ricerca", "tipo_soggetto_testo": "",
             "minimo": "1", "massimo": "", "ruolo": "partner", "regioni": [], "paesi": [],
             "vincolo_territoriale": "",
             "citazione": _cit("D1-p3", "almeno un organismo di ricerca")},
            # regione sconosciuta → da_verificare
            {"id": "K2", "tipo_soggetto": "pmi", "tipo_soggetto_testo": "", "minimo": "3",
             "massimo": "", "ruolo": "qualsiasi", "regioni": ["Piemonte", "Atlantide"],
             "paesi": [], "vincolo_territoriale": "sede operativa in Piemonte",
             "citazione": _cit("D1-p4", "sede operativa in Piemonte o in Atlantide")},
        ],
        "quote": [],
        "vincoli": [
            {"id": "V1", "tipo": "sede_operativa_regione", "descrizione": "Sede in Piemonte",
             "parametro": "", "momento": "domanda",
             "citazione": _cit("D1-p4", "devono avere sede operativa in Piemonte")},
            *altri_vincoli,
        ],
        "regole_finanziarie": [
            {"id": "RF1", "descrizione": "Quota al massimo il 60% del fatturato medio",
             "ambito": "ciascun_partner", "numeratore": "costo_quota",
             "denominatore": "fatturato_medio_2", "operatore": "le", "soglia": "0.6",
             "soglia_variabile": "", "soglia_coefficiente": "", "unita": "rapporto",
             "citazione": _cit("D1-p4", "non può superare il 60% del fatturato medio")},
        ],
        "documenti_richiesti": [], "fonti_insufficienti": False, "note": "",
    }
    return post_elabora(
        PartenariatoEstrazione.model_validate(estrazione), sezioni or SEZ_REGOLE, FONTI_REGOLE,
        LOOKUPS_REGOLE,
    )


_CAMPI_OUT_ESCLUSI = {"stato", "avvisi", "tipo_soggetto_etichetta", "beneficiari",
                      "regioni_nomi", "etichetta", "effettiva"}


def conferma_tutto(regole: dict) -> dict:
    """Snapshot «confermo tutte le voci verificate» come lo comporrebbe il
    wizard dalle regole estratte."""

    def voce(v: dict) -> dict:
        dati = {k: copy.deepcopy(x) for k, x in v.items() if k not in _CAMPI_OUT_ESCLUSI}
        return {**dati, "origine_voce": "confermata"}

    snap: dict = {"modalita": voce(regole["modalita"])}
    for chiave in ("costituzione", "partner_min", "partner_max"):
        if regole[chiave]["stato"] == "verificata" and regole[chiave].get("valore") is not None:
            snap[chiave] = voce(regole[chiave])
    for chiave in ("forme_ammesse", "composizione", "quote", "vincoli", "regole_finanziarie",
                   "documenti_richiesti"):
        snap[chiave] = [voce(v) for v in regole[chiave] if v["stato"] == "verificata"]
    return snap


class TestVociConfermate:
    def test_tutte_le_verificate_confermate(self):
        regole = regole_estratte()
        snap = RegoleCallSnapshot.model_validate(conferma_tutto(regole))
        assert [c.id for c in snap.composizione] == ["K1"]  # K2 è da verificare
        assert gap.errori_voci_confermate(snap, regole) == []
        # e ne escono i requisiti tipizzati
        rif = {gap.id_voce(b.rif_origine)
               for b in gap.requisiti_da_regole(snap, regioni_bando=[1])}
        assert rif == {"K1", "V1", "RF1"}

    def test_voce_da_verificare_non_si_conferma(self):
        regole = regole_estratte()
        dati = conferma_tutto(regole)
        k2 = next(v for v in regole["composizione"] if v["id"] == "K2")
        dati["composizione"].append(
            {k: x for k, x in k2.items() if k not in _CAMPI_OUT_ESCLUSI}
            | {"origine_voce": "confermata"}
        )
        errori = gap.errori_voci_confermate(RegoleCallSnapshot.model_validate(dati), regole)
        assert len(errori) == 1 and "composizione" in errori[0]
        # marcata «modificata» invece va bene
        dati["composizione"][-1]["origine_voce"] = "modificata"
        assert gap.errori_voci_confermate(RegoleCallSnapshot.model_validate(dati), regole) == []

    def test_voce_della_scheda_solo_modificata_e_del_creatore(self):
        """Un vincolo citato dalla scheda del catalogo (S2) e ritrovato alla
        lettera resta da verificare: non si conferma così com'è; inserito come
        «modificato», il suo requisito nel validatore è del creatore, non una
        «Regola del bando» con il testo della scheda."""
        from app.services.partenariato_regole import AVVISO_SCHEDA

        v2 = {"id": "V2", "tipo": "sede_operativa_regione", "descrizione": "Sede in Lombardia",
              "parametro": "", "momento": "domanda",
              "citazione": _cit("S2", "sede operativa in Lombardia")}
        sezioni = {**SEZ_REGOLE, "S2": "Possono partecipare le imprese con sede operativa in "
                                       "Lombardia."}
        regole = regole_estratte(altri_vincoli=(v2,), sezioni=sezioni)
        estratta = next(v for v in regole["vincoli"] if v["id"] == "V2")
        assert estratta["stato"] == "da_verificare"
        assert estratta["citazione"]["verificata"] is True  # ritrovata, ma nella scheda
        assert AVVISO_SCHEDA in estratta["avvisi"]

        dati = conferma_tutto(regole)
        assert [v["id"] for v in dati["vincoli"]] == ["V1"]
        dati["vincoli"].append(
            {k: x for k, x in estratta.items() if k not in _CAMPI_OUT_ESCLUSI}
            | {"origine_voce": "confermata"}
        )
        errori = gap.errori_voci_confermate(RegoleCallSnapshot.model_validate(dati), regole)
        assert len(errori) == 1 and "vincolo" in errori[0]
        dati["vincoli"][-1]["origine_voce"] = "modificata"
        snap = RegoleCallSnapshot.model_validate(dati)
        assert gap.errori_voci_confermate(snap, regole) == []

        bozze = {gap.id_voce(b.rif_origine): b
                 for b in gap.requisiti_da_regole(snap, regioni_bando=[1, 2])}
        assert bozze["V2"].citazione["testo"] == "sede operativa in Lombardia"
        fonti = {rif: pv.requisito_da_riga(_riga_requisito(bozze[rif])).regola.fonte
                 for rif in ("V1", "V2")}
        assert fonti == {"V1": "bando", "V2": "creatore"}

    def test_voce_cambiata_non_e_piu_confermata(self):
        regole = regole_estratte()
        for sezione, campo, valore in (
            ("composizione", "minimo", 2),
            ("vincoli", "momento", "concessione"),
            ("partner_min", "valore", 4),
            ("regole_finanziarie", "soglia", "0.7"),
        ):
            dati = conferma_tutto(regole)
            voce = dati[sezione][0] if isinstance(dati[sezione], list) else dati[sezione]
            voce[campo] = valore
            errori = gap.errori_voci_confermate(RegoleCallSnapshot.model_validate(dati), regole)
            assert len(errori) == 1, sezione

    def test_citazione_diversa_o_estrazione_assente(self):
        regole = regole_estratte()
        dati = conferma_tutto(regole)
        dati["vincoli"][0]["citazione"]["testo"] = "sede operativa in Lombardia"
        assert gap.errori_voci_confermate(RegoleCallSnapshot.model_validate(dati), regole)
        # senza estrazione corrente nessuna voce può dirsi confermata
        errori = gap.errori_voci_confermate(conferma_tutto(regole), None)
        assert len(errori) == 1 + 1 + 1 + 1 + 1 + 1  # modalità, min, forma, K1, V1, RF1
