"""Test del mapping IT-full → BandoFit: funzioni pure sulla fixture reale
registrata dallo spike (tests/fixtures/openapi/it_full_sample.json) e, per i
dati economici, sulla fixture sintetica derivata dagli esempi OAS
(it_full_bilanci_sintetico.json: la fixture reale è un'associazione senza
bilanci, su cui il mapping economico non era mai stato esercitato)."""

import json
from pathlib import Path
from types import SimpleNamespace

from app.services.openapi_mapping import (
    _voce_cee,
    ateco_division,
    build_autofill,
    build_derived,
    build_dossier,
    classe_dimensionale,
    extract_people,
    fascia_fatturato,
    forma_giuridica_codice,
    normalize_region,
    parse_openapi_date,
    secondary_ateco_codes,
    stato_impresa,
    validate_partita_iva,
)

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"


def payload() -> dict:
    return json.loads((FIXTURES / "it_full_sample.json").read_text())["data"]


def payload_bilanci() -> dict:
    return json.loads((FIXTURES / "it_full_bilanci_sintetico.json").read_text())["data"]


def advanced() -> dict:
    return json.loads((FIXTURES / "it_advanced_sintetico.json").read_text())["data"][0]


def lookups(**overrides) -> SimpleNamespace:
    base = dict(
        codici_ateco=[
            SimpleNamespace(id=850, codice="85", descrizione="Istruzione"),
            SimpleNamespace(id=620, codice="62", descrizione="Software"),
            SimpleNamespace(id=10, codice="01", descrizione="Agricoltura"),
        ],
        regioni=[
            SimpleNamespace(id=12, nome="Lazio"),
            SimpleNamespace(id=4, nome="Trentino-Alto Adige/Südtirol"),
            SimpleNamespace(id=2, nome="Valle d'Aosta/Vallée d'Aoste"),
            SimpleNamespace(id=6, nome="Friuli-Venezia Giulia"),
        ],
        beneficiari=[
            SimpleNamespace(id=1, nome="Micro-imprese"),
            SimpleNamespace(id=2, nome="PMI"),
            SimpleNamespace(id=3, nome="Grandi Imprese"),
            SimpleNamespace(id=4, nome="Startup"),
            SimpleNamespace(id=5, nome="Cooperative sociali"),
        ],
        settori=[], tipologie=[], modalita=[], programmi=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestPartitaIva:
    def test_checksum_reali(self):
        assert validate_partita_iva("14061981008")  # dallo spike
        assert validate_partita_iva("12485671007")  # Openapi SpA

    def test_rifiuti(self):
        assert not validate_partita_iva("14061981009")  # checksum errata
        assert not validate_partita_iva("1234567890")   # 10 cifre
        assert not validate_partita_iva("abcdefghijk")


class TestAtecoDivision:
    def test_codici_openapi_senza_punti(self):
        assert ateco_division("85592") == "85"
        assert ateco_division("855209") == "85"

    def test_zero_pad_e_varianti(self):
        assert ateco_division("1") == "01"
        assert ateco_division("62.01.00") == "62"
        assert ateco_division(None) is None
        assert ateco_division("") is None
        assert ateco_division("--") is None


class TestNormalizeRegion:
    def test_maiuscole_openapi_vs_catalogo(self):
        assert normalize_region("LAZIO") == normalize_region("Lazio")

    def test_slash_e_accenti(self):
        assert normalize_region("TRENTINO-ALTO ADIGE") == normalize_region(
            "Trentino-Alto Adige/Südtirol"
        )
        assert normalize_region("VALLE D'AOSTA") == normalize_region(
            "Valle d'Aosta/Vallée d'Aoste"
        )
        assert normalize_region("FRIULI-VENEZIA GIULIA") == normalize_region(
            "Friuli-Venezia Giulia"
        )


class TestParseDate:
    def test_mezzanotte_locale_serializzata_arrotonda(self):
        # 1985-06-03T22:00 = nato il 4 giugno (confermato dal CF H04 nella fixture)
        assert parse_openapi_date("1985-06-03T22:00:00").isoformat() == "1985-06-04"
        assert parse_openapi_date("2020-12-19T23:00:00").isoformat() == "2020-12-20"

    def test_orari_diurni_invariati(self):
        assert parse_openapi_date("2026-06-23T10:33:39").isoformat() == "2026-06-23"

    def test_frazioni_lunghe_e_z(self):
        # lastUpdateDate reale: frazione a 7 cifre + Z
        assert parse_openapi_date("2026-06-23T18:33:39.0000000Z") is not None

    def test_robustezza(self):
        assert parse_openapi_date(None) is None
        assert parse_openapi_date("non-data") is None


class TestFasciaEClasse:
    def test_boundary_fasce(self):
        assert fascia_fatturato({"ecofin": {"turnover": 100_000}}) == "fino_100k"
        assert fascia_fatturato({"ecofin": {"turnover": 100_001}}) == "100k_500k"
        assert fascia_fatturato({"ecofin": {"turnover": 2_000_000}}) == "500k_2m"
        assert fascia_fatturato({"ecofin": {"turnover": 60_000_000}}) == "oltre_50m"
        assert fascia_fatturato(payload()) is None  # ente senza bilanci

    def test_classe_da_descrizione_o_dipendenti(self):
        assert classe_dimensionale({"ecofin": {"enterpriseSize": {"description": "Small"}}}) == "piccola"
        assert classe_dimensionale(payload()) == "micro"  # ND99 → fallback: 9 dipendenti
        assert classe_dimensionale({"employees": {"employee": 250}}) == "grande"
        assert classe_dimensionale({}) is None


class TestBeneficiari:
    def test_non_derivati_dalla_visura(self):
        # Le categorie del catalogo (Istituti Scolastici, Enti pubblici…) non
        # si deducono da attributi camerali: le dichiara l'utente su
        # company_profiles.beneficiari. `derived` non deve più contenerle.
        assert "beneficiari" not in build_derived(payload(), lookups())


class TestBuildDerived:
    def test_regioni_ids_di_tutte_le_sedi(self):
        # Fixture: sede legale LAZIO + unità locali PUGLIA/CALABRIA/LAZIO.
        lk = lookups(regioni=[
            SimpleNamespace(id=12, nome="Lazio"),
            SimpleNamespace(id=15, nome="Puglia"),
            SimpleNamespace(id=18, nome="Calabria"),
        ])
        derived = build_derived(payload(), lk)
        assert derived["regione_id"] == 12  # sede legale
        assert derived["regioni_ids"][0] == 12  # sede legale per prima
        assert set(derived["regioni_ids"]) == {12, 15, 18}


class TestPersone:
    def test_manager_dalla_fixture(self):
        people = extract_people(payload())
        managers = [p for p in people if p["kind"] == "manager"]
        assert len(managers) == 1
        m = managers[0]
        assert m["nome"] == "MICHELE"
        assert m["is_legale_rappresentante"] is True
        assert m["data_nascita"] == "1985-06-04"  # arrotondamento verificato dal CF
        assert m["ruoli"][0]["code"] == "LER"
        assert m["data_inizio_carica"] == "2022-06-01"

    def test_shareholders_e_auditors_assenti_ok(self):
        people = extract_people(payload())
        assert all(p["kind"] == "manager" for p in people)

    def test_shareholder_sintetico(self):
        people = extract_people(
            {"shareholders": [{"companyName": "HOLDING SRL", "taxCode": 123, "percentShare": "51.5"}]}
        )
        assert people[0]["kind"] == "shareholder"
        assert people[0]["denominazione"] == "HOLDING SRL"
        assert people[0]["quota_percentuale"] == 51.5


class TestAutofill:
    def test_compila_i_campi_vuoti(self):
        updates, applied, conflicts, suggestions = build_autofill(payload(), None, lookups())
        assert updates["ragione_sociale"].startswith("ENTE RICERCA")
        assert updates["ateco_id"] == 850 and updates["ateco_codice"] == "85"
        assert updates["regione_id"] == 12 and updates["regione_nome"] == "Lazio"
        assert updates["cap"] == "00187"
        assert updates["provincia"] == "RM"
        assert updates["anno_fondazione"] == 2020
        assert updates["numero_dipendenti"] == 9
        assert updates["classe_dimensionale"] == "micro"
        assert updates["pec"] == "ERSAF@PEC.IT"
        assert "fascia_fatturato" not in updates  # nessun bilancio
        assert conflicts == []
        assert set(applied) == set(updates.keys()) - {"ateco_codice", "ateco_descrizione", "regione_nome"}

    def test_mai_sovrascrivere_i_valori_utente(self):
        current = {
            "ragione_sociale": "Nome scelto dall'utente",
            "ateco_id": 620, "ateco_codice": "62",
            "regione_id": 12, "regione_nome": "Lazio",
            "pec": "ERSAF@PEC.IT",  # uguale (case-insensitive): nessun conflitto
        }
        updates, applied, conflicts, _ = build_autofill(payload(), current, lookups())
        assert "ragione_sociale" not in updates and "ateco_id" not in updates
        campi_in_conflitto = {c["campo"] for c in conflicts}
        assert "ragione_sociale" in campi_in_conflitto
        assert "ateco_id" in campi_in_conflitto  # 62 utente vs 85 certificato
        assert "regione_id" not in campi_in_conflitto  # coincide
        assert "pec" not in campi_in_conflitto

    def test_suggerimenti_escludono_la_divisione_principale(self):
        # fixture: secondario 855209 → divisione 85 == principale → nessun suggerimento
        _, _, _, suggestions = build_autofill(payload(), None, lookups())
        assert suggestions["codici_ateco"] == []

    def test_suggerimenti_su_divisione_diversa(self):
        data = payload()
        data["atecoClassification"]["secondaryAteco"] = ["620100", "015000"]
        _, _, _, suggestions = build_autofill(data, None, lookups())
        ids = [s["id"] for s in suggestions["codici_ateco"]]
        assert ids == [620, 10]


class TestTolleranzaDatiParziali:
    def test_ogni_blocco_rimovibile(self):
        base = payload()
        for key in list(base.keys()):
            data = {k: v for k, v in base.items() if k != key}
            build_dossier(data)
            build_autofill(data, None, lookups())
            build_derived(data, lookups())
            extract_people(data)

    def test_payload_vuoto(self):
        dossier = build_dossier({})
        assert dossier["anagrafica"]["denominazione"] is None
        updates, applied, conflicts, suggestions = build_autofill({}, None, lookups())
        assert updates == {} and applied == [] and conflicts == []
        assert extract_people({}) == []


class TestDossier:
    def test_sezioni_dalla_fixture(self):
        dossier = build_dossier(payload())
        assert dossier["anagrafica"]["stato"] == "Attiva"  # code A → italiano
        assert dossier["anagrafica"]["data_costituzione"] == "2020-12-20"
        assert dossier["attivita"]["ateco"]["codice"] == "85592"
        assert len(dossier["sede"]["unita_locali"]) == 5
        assert dossier["sede"]["numero_sedi"] == 4
        assert dossier["contatti"]["pec"] == "ERSAF@PEC.IT"
        assert dossier["dipendenti"]["numero"] == 9
        assert dossier["partecipazioni"][0]["quota"] == 92
        assert dossier["flags"]["startup_innovativa"] is False
        assert dossier["bilanci"]["fatturato"] is None

    def test_stato_impresa(self):
        assert stato_impresa(payload()) == "Attiva"
        assert stato_impresa({"companyStatus": {"activityStatus": {"code": "C"}}}) == "Cessata"

    def test_secondary_ateco_varianti(self):
        assert secondary_ateco_codes({"atecoClassification": {"secondaryAteco": "1, 2;3"}}) == ["1", "2", "3"]
        assert secondary_ateco_codes({"atecoClassification": {"secondaryAteco": ["620100"]}}) == ["620100"]
        assert secondary_ateco_codes({}) == []


class TestBilanciDossier:
    """Regressione del bug di produzione: `ecofin.ebitda` ed `ecofin.profit`
    non esistono in IT-full, quindi EBITDA e utile del dossier valevano
    sempre None."""

    def test_ebitda_utile_anno_dalla_fixture_sintetica(self):
        bilanci = build_dossier(payload_bilanci())["bilanci"]
        assert bilanci["ebitda"] == 714856  # operatingResults.ebitda
        assert bilanci["utile"] == 469366  # annualResult IIC179
        assert bilanci["patrimonio_netto"] == 563473  # ecofin.netWorth = PN
        assert bilanci["fatturato"] == 4432761
        assert bilanci["capitale_sociale"] == 50000
        assert bilanci["anno"] == 2021
        assert bilanci["data_chiusura"] == "2021-12-31"
        assert bilanci["dimensione_impresa"] == "Small"

    def test_variante_ipl(self):
        data = payload_bilanci()
        data["annualResult"] = [{"code": "IPL179", "value": 469366.0}]
        data["netWorth"] = [{"code": "IPL083", "value": 1.0}]
        assert build_dossier(data)["bilanci"]["utile"] == 469366

    def test_ripiego_su_voce_a_ix_iic_e_ipl(self):
        data = payload_bilanci()
        del data["annualResult"]
        assert build_dossier(data)["bilanci"]["utile"] == 469366
        data["netWorth"] = [{"code": "IPL083", "value": -2500.0}]
        assert build_dossier(data)["bilanci"]["utile"] == -2500.0  # perdita

    def test_voce_179_prevale_su_083(self):
        data = payload_bilanci()
        data["netWorth"][-1]["value"] = 1.0
        assert build_dossier(data)["bilanci"]["utile"] == 469366

    def test_anno_con_ripiego_su_turnover_year(self):
        data = payload_bilanci()
        del data["ecofin"]["balanceSheetDate"]
        bilanci = build_dossier(data)["bilanci"]
        assert bilanci["anno"] == 2021 and bilanci["data_chiusura"] is None

    def test_turnover_year_discordante_ha_il_suo_anno(self):
        # Gemello di test_bilanci_mapping: la tabella dei bilanci esclude quel
        # fatturato dall'esercizio 2021; il dossier non deve presentarlo come
        # «Esercizio 2021» — lo accompagna con il suo anno.
        data = payload_bilanci()
        data["ecofin"]["turnoverYear"] = "2022"  # anche come stringa
        bilanci = build_dossier(data)["bilanci"]
        assert bilanci["anno"] == 2021
        assert bilanci["fatturato"] == 4432761 and bilanci["anno_fatturato"] == 2022

    def test_anno_fatturato_coincidente(self):
        bilanci = build_dossier(payload_bilanci())["bilanci"]
        assert bilanci["anno_fatturato"] == bilanci["anno"] == 2021

    def test_fixture_reale_associazione_tutto_none(self):
        bilanci = build_dossier(payload())["bilanci"]
        for campo in (
            "fatturato", "capitale_sociale", "patrimonio_netto", "ebitda", "utile",
            "anno", "data_chiusura",
        ):
            assert bilanci[campo] is None, campo
        assert fascia_fatturato(payload()) is None
        assert fascia_fatturato(payload(), advanced={"balanceSheets": {"last": None}}) is None

    def test_valori_non_numerici_ignorati(self):
        data = payload_bilanci()
        data["operatingResults"]["ebitda"] = True
        data["annualResult"] = [{"code": "IIC179", "value": "469366"}]
        data["netWorth"] = []
        bilanci = build_dossier(data)["bilanci"]
        assert bilanci["ebitda"] is None and bilanci["utile"] is None


class TestVoceCee:
    def test_cerca_in_tutti_i_blocchi_di_primo_livello(self):
        data = {
            "altro": {"code": "IIC179", "value": 1},  # non è una lista: ignorato
            "blocco_a": [{"code": "IIC001", "value": 5}],
            "blocco_b": ["rumore", {"code": "iic179", "value": 7}],
        }
        assert _voce_cee(data, ("IIC179", "IPL179")) == 7

    def test_ordine_dei_codici(self):
        data = {"a": [{"code": "IPL179", "value": 1}], "b": [{"code": "IIC179", "value": 2}]}
        assert _voce_cee(data, ("IIC179", "IPL179")) == 2
        assert _voce_cee(data, ("IPL179", "IIC179")) == 1

    def test_assente_o_non_valido(self):
        assert _voce_cee({"a": [{"code": "IIC179", "value": None}]}, ("IIC179",)) is None
        assert _voce_cee({"a": [{"code": "IIC179", "value": float("nan")}]}, ("IIC179",)) is None
        assert _voce_cee({}, ("IIC179",)) is None
        assert _voce_cee(None, ("IIC179",)) is None


class TestFormaGiuridica:
    def test_collisione_sp_primo_livello_contro_dettaglio(self):
        # SpA: nel DETTAGLIO 'SP' significa società per azioni, il primo
        # livello è SC. Non deve mai risultare società di persone.
        spa = {"legalForm": {
            "legalForm": {"code": "SC", "description": "Capital company"},
            "detailedLegalForm": {"code": "SP", "description": "Joint-stock company"},
        }}
        assert forma_giuridica_codice(spa) == "SC"
        snc = {"legalForm": {
            "legalForm": {"code": "SP", "description": "Partnership"},
            "detailedLegalForm": {"code": "SN", "description": "General partnership"},
        }}
        assert forma_giuridica_codice(snc) == "SP"

    def test_solo_dettaglio_non_basta(self):
        assert forma_giuridica_codice({"legalForm": {"detailedLegalForm": {"code": "SP"}}}) is None
        assert forma_giuridica_codice({"detailedLegalForm": {"code": "SP"}}) is None

    def test_fixture_e_valori_anomali(self):
        assert forma_giuridica_codice(payload()) == "AL"  # associazione
        assert forma_giuridica_codice(payload_bilanci()) == "SC"
        assert forma_giuridica_codice({"legalForm": {"legalForm": {"code": " sc "}}}) == "SC"
        assert forma_giuridica_codice({"legalForm": {"legalForm": {"code": "XX"}}}) is None
        assert forma_giuridica_codice({}) is None


class TestFasciaFatturatoRipiego:
    def test_solo_ecofin_turnover(self):
        assert fascia_fatturato(payload_bilanci()) == "2m_10m"
        # i vecchi path morti non contano più
        assert fascia_fatturato({"ecofin": {"revenue": 5_000}}) is None
        assert fascia_fatturato({"balanceSheets": {"turnover": 5_000}}) is None
        assert fascia_fatturato({"ecofin": {"turnover": -1}}) is None
        assert fascia_fatturato({"ecofin": {"turnover": True}}) is None

    def test_ripiego_su_ultimo_bilancio_advanced(self):
        assert fascia_fatturato(payload(), advanced()) == "2m_10m"  # last 2022: 5.102.233
        dato = advanced()
        dato["balanceSheets"]["last"]["turnover"] = 80_000
        assert fascia_fatturato({}, dato) == "fino_100k"
        # IT-full, quando c'è, prevale
        assert fascia_fatturato({"ecofin": {"turnover": 60_000_000}}, dato) == "oltre_50m"

    def test_autofill_e_derived_con_advanced(self):
        updates, applied, _, _ = build_autofill(payload(), None, lookups(), advanced())
        assert updates["fascia_fatturato"] == "2m_10m" and "fascia_fatturato" in applied
        assert build_derived(payload(), lookups(), advanced())["fascia_fatturato"] == "2m_10m"
        # senza advanced il comportamento resta quello di prima
        updates, _, _, _ = build_autofill(payload(), None, lookups())
        assert "fascia_fatturato" not in updates
