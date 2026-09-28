"""Mapping dei bilanci per esercizio (services/bilanci_mapping.py).

Fixture sintetiche derivate dagli esempi ufficiali OAS (società fittizia,
valori coerenti tra i due prodotti): IT-full 2021 e IT-advanced 2017-2024 con
2023-2024 segnaposto. Da sostituire con le risposte sandbox al gate G1.

La precedenza tra fonti si verifica sulla tabella di casi condivisa con il
test DB (tests/fixtures/bilanci/precedenza_casi.json): qui la sequenza di
chiamate alla RPC è simulata in Python con la stessa semantica (upsert per
anno e fonte, `sostituisci`, comparativo che non copre il corrente).
"""

import copy
import json
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.bilanci_mapping import (
    CAMPI_BILANCIO,
    CAMPI_CORE,
    MAPPING_BILANCI_VERSIONE,
    RANGO_FONTE,
    RigaFonte,
    a_payload_rpc,
    da_it_advanced,
    da_it_full,
    ids_advanced,
    unisci_fonti,
)

FIXTURES = Path(__file__).parent / "fixtures"
PIVA = "09876543217"


def it_full() -> dict:
    return json.loads((FIXTURES / "openapi" / "it_full_bilanci_sintetico.json").read_text())["data"]


def it_full_associazione() -> dict:
    return json.loads((FIXTURES / "openapi" / "it_full_sample.json").read_text())["data"]


def it_advanced() -> dict:
    return json.loads((FIXTURES / "openapi" / "it_advanced_sintetico.json").read_text())["data"][0]


def D(valore) -> Decimal:
    return Decimal(str(valore))


class TestCostanti:
    def test_campi_nell_ordine_della_migration(self):
        assert len(CAMPI_BILANCIO) == 15 and len(set(CAMPI_BILANCIO)) == 15
        assert CAMPI_BILANCIO[0] == "fatturato"
        assert CAMPI_BILANCIO[-1] == "retribuzione_media_lorda"
        assert set(CAMPI_CORE) <= set(CAMPI_BILANCIO)
        assert RANGO_FONTE == {"xbrl": 3, "it_full": 2, "it_advanced": 1}
        assert MAPPING_BILANCI_VERSIONE >= 1


# ---------------------------------------------------------------- IT-full

class TestDaItFull:
    def test_una_riga_allineata_alla_data_di_chiusura(self):
        [riga] = da_it_full(it_full())
        assert riga.anno == 2021
        assert riga.data_chiusura == date(2021, 12, 31)
        assert riga.tipo_bilancio == "ignoto" and riga.ruolo == "corrente"
        v = riga.valori
        assert v["fatturato"] == D(4432761)
        # ecofin.netWorth è il PATRIMONIO NETTO; l'utile viene dalla voce CEE 179.
        assert v["patrimonio_netto"] == D(563473)
        assert v["risultato_esercizio"] == D(469366)
        assert v["ebitda"] == D(714856)
        assert v["ebit"] == D(612345)
        assert v["cash_flow"] == D(571711)
        assert v["valore_produzione"] == D(4502113)
        assert v["totale_attivo"] == D(2150432)
        assert v["capitale_sociale"] == D(50000)

    def test_campi_non_mappati_restano_none(self):
        [riga] = da_it_full(it_full())
        # organico corrente, non dell'esercizio; codici CEE non verificati
        for campo in (
            "dipendenti", "debiti_totali", "disponibilita_liquide",
            "oneri_finanziari", "costo_personale", "retribuzione_media_lorda",
        ):
            assert riga.valori[campo] is None, campo
        assert set(riga.valori) == set(CAMPI_BILANCIO)

    def test_variante_ipl(self):
        payload = it_full()
        for blocco in payload.values():
            if isinstance(blocco, list):
                for voce in blocco:
                    voce["code"] = voce["code"].replace("IIC", "IPL")
        [riga] = da_it_full(payload)
        assert riga.valori["risultato_esercizio"] == D(469366)
        assert riga.valori["valore_produzione"] == D(4502113)
        assert riga.valori["totale_attivo"] == D(2150432)

    def test_ripiego_utile_su_voce_a_ix(self):
        payload = it_full()
        del payload["annualResult"]
        [riga] = da_it_full(payload)
        assert riga.valori["risultato_esercizio"] == D(469366)

    def test_voce_179_prevale_su_083(self):
        payload = it_full()
        payload["netWorth"][-1]["value"] = 1.0
        [riga] = da_it_full(payload)
        assert riga.valori["risultato_esercizio"] == D(469366)

    def test_perdita_ammessa(self):
        payload = it_full()
        payload["annualResult"][-1]["value"] = -120000.5
        [riga] = da_it_full(payload)
        assert riga.valori["risultato_esercizio"] == D("-120000.50")

    def test_turnover_year_discordante_esclude_il_fatturato(self, caplog):
        payload = it_full()
        payload["ecofin"]["turnoverYear"] = 2022
        with caplog.at_level(logging.WARNING, logger="bandofit.bilanci"):
            [riga] = da_it_full(payload)
        assert riga.anno == 2021  # l'anno resta quello della chiusura
        assert riga.valori["fatturato"] is None
        # le voci del bilancio 2021 restano nella riga 2021
        assert riga.valori["risultato_esercizio"] == D(469366)
        assert riga.valori["patrimonio_netto"] == D(563473)
        assert any("turnoverYear" in r.getMessage() for r in caplog.records)
        assert PIVA not in caplog.text

    def test_turnover_year_assente_tiene_il_fatturato(self):
        payload = it_full()
        del payload["ecofin"]["turnoverYear"]
        [riga] = da_it_full(payload)
        assert riga.valori["fatturato"] == D(4432761)

    def test_data_in_mezzanotte_locale_serializzata(self):
        payload = it_full()
        payload["ecofin"]["balanceSheetDate"] = "2021-12-30T23:00:00"
        [riga] = da_it_full(payload)
        assert riga.anno == 2021 and riga.data_chiusura == date(2021, 12, 31)

    def test_senza_data_solo_il_fatturato_del_suo_anno(self):
        payload = it_full()
        del payload["ecofin"]["balanceSheetDate"]
        [riga] = da_it_full(payload)
        assert riga.anno == 2021 and riga.data_chiusura is None
        assert riga.valori["fatturato"] == D(4432761)
        valorizzati = {c for c, v in riga.valori.items() if v is not None}
        assert valorizzati == {"fatturato"}

    def test_senza_data_ne_anno_nessuna_riga(self):
        payload = it_full()
        del payload["ecofin"]["balanceSheetDate"]
        del payload["ecofin"]["turnoverYear"]
        assert da_it_full(payload) == []

    def test_fixture_reale_associazione_nessuna_riga(self):
        assert da_it_full(it_full_associazione()) == []

    def test_payload_vuoto_o_non_dict(self):
        assert da_it_full({}) == []
        assert da_it_full(None) == []  # type: ignore[arg-type]

    def test_valori_di_segno_impossibile(self, caplog):
        payload = it_full()
        payload["ecofin"]["turnover"] = -10.0
        payload["ecofin"]["shareCapital"] = -1.0
        payload["assetsAggregateValues"][0]["value"] = -5.0
        with caplog.at_level(logging.WARNING, logger="bandofit.bilanci"):
            [riga] = da_it_full(payload)
        assert riga.valori["fatturato"] is None
        assert riga.valori["capitale_sociale"] is None
        assert riga.valori["totale_attivo"] is None
        # il segno negativo del valore non finisce nel log
        assert "-10" not in caplog.text

    def test_valori_non_numerici_o_fuori_scala(self):
        payload = it_full()
        payload["ecofin"]["turnover"] = True  # bool non è un importo
        payload["ecofin"]["netWorth"] = "563473"  # stringa: non si interpreta
        payload["operatingResults"]["ebitda"] = 1e20  # oltre numeric(18,2)
        [riga] = da_it_full(payload)
        assert riga.valori["fatturato"] is None
        assert riga.valori["patrimonio_netto"] is None
        assert riga.valori["ebitda"] is None

    def test_anno_fuori_intervallo(self):
        payload = it_full()
        payload["ecofin"]["balanceSheetDate"] = "1985-12-31T00:00:00"
        payload["ecofin"]["turnoverYear"] = 1985
        assert da_it_full(payload) == []

    def test_arrotondamento_al_centesimo(self):
        payload = it_full()
        payload["operatingResults"]["ebitda"] = 0.1 + 0.2  # 0.30000000000000004
        payload["operatingResults"]["ebit"] = 10.005
        [riga] = da_it_full(payload)
        assert riga.valori["ebitda"] == D("0.30")
        assert riga.valori["ebit"] == D("10.01")


# ------------------------------------------------------------ IT-advanced

class TestDaItAdvanced:
    def test_segnaposto_scartati_e_anni_crescenti(self):
        righe = da_it_advanced(it_advanced())
        assert [r.anno for r in righe] == [2017, 2018, 2019, 2020, 2021, 2022]

    def test_networth_advanced_e_utile_non_patrimonio(self):
        """netWorth di IT-advanced è l'UTILE («annual profit»): va in
        risultato_esercizio e MAI in patrimonio_netto. Verificato per incrocio
        con IT-full della stessa società: 2021 netWorth = utile IIC179, non PN."""
        righe = {r.anno: r for r in da_it_advanced(it_advanced())}
        for riga in righe.values():
            assert riga.valori["patrimonio_netto"] is None
        grezze = {g["year"]: g for g in it_advanced()["balanceSheets"]["all"]}
        for anno, riga in righe.items():
            assert riga.valori["risultato_esercizio"] == D(grezze[anno]["netWorth"])
        [full] = da_it_full(it_full())
        assert righe[2021].valori["risultato_esercizio"] == full.valori["risultato_esercizio"]
        assert righe[2021].valori["risultato_esercizio"] != full.valori["patrimonio_netto"]

    def test_campi_dell_anno_con_dettaglio(self):
        riga = {r.anno: r for r in da_it_advanced(it_advanced())}[2022]
        assert riga.data_chiusura == date(2022, 12, 31)
        v = riga.valori
        assert v["fatturato"] == D(5102233)
        assert v["totale_attivo"] == D(2388710)
        assert v["dipendenti"] == D(33)
        assert v["capitale_sociale"] == D(50000)
        assert v["costo_personale"] == D(1450221)
        assert v["retribuzione_media_lorda"] == D("43945.90")
        assert v["ebitda"] is None and v["valore_produzione"] is None

    def test_anno_dalla_data_non_da_year(self, caplog):
        dato = it_advanced()
        dato["balanceSheets"]["all"] = [
            {"year": 2020, "balanceSheetDate": "2021-06-30", "turnover": 100, "netWorth": 5},
        ]
        with caplog.at_level(logging.WARNING, logger="bandofit.bilanci"):
            [riga] = da_it_advanced(dato)
        assert riga.anno == 2021  # esercizio a cavallo: vale la chiusura
        assert riga.data_chiusura == date(2021, 6, 30)
        assert any("year 2020" in r.getMessage() for r in caplog.records)

    def test_ripiego_su_year_senza_data(self):
        dato = it_advanced()
        dato["balanceSheets"]["all"] = [
            {"year": 2019, "balanceSheetDate": None, "turnover": 100, "netWorth": None},
        ]
        [riga] = da_it_advanced(dato)
        assert riga.anno == 2019 and riga.data_chiusura is None

    def test_ripiego_su_last(self):
        dato = it_advanced()
        dato["balanceSheets"]["all"] = []
        [riga] = da_it_advanced(dato)
        assert riga.anno == 2022
        del dato["balanceSheets"]["all"]
        assert [r.anno for r in da_it_advanced(dato)] == [2022]
        dato["balanceSheets"]["last"] = None
        assert da_it_advanced(dato) == []

    def test_righe_senza_core_scartate(self):
        dato = it_advanced()
        dato["balanceSheets"]["all"] = [
            # data presente (non è un segnaposto) ma nessun valore CORE
            {"year": 2016, "balanceSheetDate": "2016-12-31", "turnover": None,
             "netWorth": None, "employees": 5, "shareCapital": 10000},
        ]
        assert da_it_advanced(dato) == []

    def test_valori_insensati(self):
        dato = it_advanced()
        dato["balanceSheets"]["all"] = [
            {"year": 2020, "balanceSheetDate": "2020-12-31", "turnover": -100,
             "netWorth": -50, "employees": -3, "totalAssets": 1000},
        ]
        [riga] = da_it_advanced(dato)
        assert riga.valori["fatturato"] is None
        assert riga.valori["dipendenti"] is None
        assert riga.valori["risultato_esercizio"] == D(-50)  # una perdita è plausibile
        assert riga.valori["totale_attivo"] == D(1000)

    def test_limiti_delle_colonne_dopo_l_arrotondamento(self):
        # Come la RPC: il limite di numeric(10,2) vale sul valore ARROTONDATO,
        # altrimenti una riga passerebbe qui e farebbe fallire tutta la chiamata.
        dato = it_advanced()
        dato["balanceSheets"]["all"] = [
            {"year": 2020, "balanceSheetDate": "2020-12-31", "turnover": 1e300,
             "netWorth": 5, "employees": 99_999_999.996, "avgGrossSalary": 9_999_999_999.99},
        ]
        [riga] = da_it_advanced(dato)
        assert riga.valori["fatturato"] is None
        assert riga.valori["dipendenti"] is None
        assert riga.valori["retribuzione_media_lorda"] == D("9999999999.99")

    def test_anni_fuori_intervallo_o_illeggibili(self):
        dato = it_advanced()
        dato["balanceSheets"]["all"] = [
            {"year": 1980, "balanceSheetDate": "1980-12-31", "turnover": 100, "netWorth": 1},
            {"year": "boh", "balanceSheetDate": None, "turnover": 100, "netWorth": 1},
            "non-un-dict",
        ]
        assert da_it_advanced(dato) == []

    def test_anno_duplicato_tiene_la_riga_piu_completa(self):
        dato = it_advanced()
        dato["balanceSheets"]["all"] = [
            {"year": 2021, "balanceSheetDate": "2021-12-31", "turnover": 100, "netWorth": None},
            {"year": 2021, "balanceSheetDate": None, "turnover": 110, "netWorth": 7,
             "employees": 4},
        ]
        [riga] = da_it_advanced(dato)
        assert riga.valori["fatturato"] == D(110)
        assert riga.valori["risultato_esercizio"] == D(7)

    def test_dato_non_valido(self):
        assert da_it_advanced({}) == []
        assert da_it_advanced(None) == []  # type: ignore[arg-type]


class TestIdsAdvanced:
    def test_primo_livello(self):
        assert ids_advanced(it_advanced()) == {PIVA}

    def test_company_details_prefisso_it_e_interi(self):
        dato = {"companyDetails": {"vatCode": "IT 09876543217", "taxCode": 97905810582}}
        assert ids_advanced(dato) == {"IT09876543217", PIVA, "97905810582"}

    def test_vuoti(self):
        assert ids_advanced({"vatCode": "", "taxCode": None}) == set()
        assert ids_advanced(None) == set()  # type: ignore[arg-type]


# ---------------------------------------------------------------- payload

class TestPayloadRpc:
    def test_formato(self):
        [riga] = da_it_full(it_full())
        [payload] = a_payload_rpc([riga])
        assert payload["anno"] == 2021
        assert payload["data_chiusura"] == "2021-12-31"
        assert payload["tipo_bilancio"] == "ignoto" and payload["ruolo"] == "corrente"
        assert payload["valori"]["fatturato"] == "4432761.00"
        assert "dipendenti" not in payload["valori"]  # i null si omettono
        assert set(payload["valori"]) <= set(CAMPI_BILANCIO)
        assert all(isinstance(v, str) for v in payload["valori"].values())
        json.dumps(payload)  # serializzabile così com'è

    def test_notazione_fissa_mai_esponenziale(self):
        riga = RigaFonte(2020, None, "ignoto", "corrente", {"fatturato": Decimal("1E+3")})
        assert a_payload_rpc([riga])[0]["valori"] == {"fatturato": "1000"}

    def test_campo_sconosciuto_rifiutato(self):
        riga = RigaFonte(2020, None, "ignoto", "corrente", {"fatturato": D(1)})
        riga.valori["utile"] = D(1)  # scavalca __post_init__
        with pytest.raises(ValueError):
            a_payload_rpc([riga])

    def test_riga_valida_per_costruzione(self):
        with pytest.raises(ValueError):
            RigaFonte(2020, None, "sconosciuto", "corrente", {})
        with pytest.raises(ValueError):
            RigaFonte(2020, None, "ignoto", "precedente", {})
        with pytest.raises(ValueError):
            RigaFonte(2020, None, "ignoto", "corrente", {"utile": D(1)})


# ------------------------------------------------------------- precedenza

def _riga_da_json(r: dict) -> RigaFonte:
    return RigaFonte(
        anno=r["anno"],
        data_chiusura=date.fromisoformat(r["data_chiusura"]) if r["data_chiusura"] else None,
        tipo_bilancio=r["tipo_bilancio"],
        ruolo=r["ruolo"],
        valori={k: (Decimal(v) if v is not None else None) for k, v in r["valori"].items()},
    )


class RpcSimulata:
    """Stessa semantica di fn_bilanci_registra_fonte + fn_bilanci_ricalcola_anno:
    - `sostituisci` cancella le righe di QUELLA fonte assenti dalla chiamata;
    - upsert per (anno, fonte), tranne un comparativo su un corrente;
    - ogni anno toccato si ricalcola da tutte le fonti con `unisci_fonti`."""

    def __init__(self) -> None:
        self.fonti: dict[tuple[int, str], RigaFonte] = {}
        self.fuse: dict[int, tuple[dict, dict]] = {}

    def registra(self, fonte: str, righe: list[RigaFonte], sostituisci: bool) -> dict:
        toccati: set[int] = set()
        if sostituisci:
            nuovi = {r.anno for r in righe}
            for anno, f in list(self.fonti):
                if f == fonte and anno not in nuovi:
                    del self.fonti[(anno, f)]
                    toccati.add(anno)
        for riga in righe:
            toccati.add(riga.anno)
            attuale = self.fonti.get((riga.anno, fonte))
            if attuale is not None and attuale.ruolo == "corrente" and riga.ruolo == "comparativo":
                continue
            self.fonti[(riga.anno, fonte)] = riga
        for anno in toccati:
            per_fonte = {f: r for (a, f), r in self.fonti.items() if a == anno}
            if per_fonte:
                self.fuse[anno] = unisci_fonti(per_fonte)
            else:
                self.fuse.pop(anno, None)
        return {"anni": sorted(toccati)}


CASI = json.loads((FIXTURES / "bilanci" / "precedenza_casi.json").read_text())["casi"]


class TestPrecedenza:
    @pytest.mark.parametrize("caso", CASI, ids=[c["nome"] for c in CASI])
    def test_casi_condivisi_con_il_test_db(self, caso):
        rpc = RpcSimulata()
        for passo in caso["passi"]:
            righe = [_riga_da_json(r) for r in passo["righe"]]
            # andata e ritorno dal formato della RPC: nulla si perde
            for riga, originale in zip(righe, passo["righe"]):
                [payload] = a_payload_rpc([riga])
                attesi = {k: v for k, v in originale["valori"].items() if v is not None}
                assert {k: Decimal(v) for k, v in payload["valori"].items()} == {
                    k: Decimal(v) for k, v in attesi.items()
                }
            esito = rpc.registra(passo["fonte"], righe, passo["sostituisci"])
            assert set(esito["anni"]) >= {r.anno for r in righe}

        attesi_presenti = {int(a) for a, v in caso["atteso"].items() if v is not None}
        assert set(rpc.fuse) == attesi_presenti
        for anno_testo, atteso in caso["atteso"].items():
            anno = int(anno_testo)
            if atteso is None:
                assert anno not in rpc.fuse
                continue
            valori, fonte_per_campo = rpc.fuse[anno]
            for campo in CAMPI_BILANCIO:
                attesa = atteso["valori"].get(campo)
                if attesa is None:
                    assert valori[campo] is None, (caso["nome"], campo)
                else:
                    assert valori[campo] == Decimal(attesa), (caso["nome"], campo)
            assert fonte_per_campo == atteso["fonte_per_campo"]

    def test_ordine_di_arrivo_irrilevante_su_tutte_le_permutazioni(self):
        import itertools

        righe = {
            "xbrl": RigaFonte(2023, None, "ignoto", "corrente", {"fatturato": D(120)}),
            "it_full": RigaFonte(2023, None, "ignoto", "corrente",
                                 {"fatturato": D(110), "patrimonio_netto": D(50)}),
            "it_advanced": RigaFonte(2023, None, "ignoto", "corrente",
                                     {"fatturato": D(100), "risultato_esercizio": D(10)}),
        }
        risultati = set()
        for ordine in itertools.permutations(righe):
            rpc = RpcSimulata()
            for fonte in ordine:
                rpc.registra(fonte, [righe[fonte]], sostituisci=False)
            valori, fonti = rpc.fuse[2023]
            risultati.add((tuple(sorted((k, v) for k, v in valori.items() if v is not None)),
                           tuple(sorted(fonti.items()))))
        assert len(risultati) == 1

    def test_fonte_sconosciuta(self):
        with pytest.raises(ValueError):
            unisci_fonti({"visura": RigaFonte(2023, None, "ignoto", "corrente", {})})

    def test_nessuna_fonte(self):
        valori, fonti = unisci_fonti({})
        assert fonti == {} and all(v is None for v in valori.values())
        assert tuple(valori) == CAMPI_BILANCIO

    def test_fixture_sintetiche_fuse_per_anno(self):
        """IT-full 2021 e IT-advanced 2017-2022 della stessa società: nel 2021
        il PN viene solo da IT-full, l'utile coincide; nel 2022 c'è solo
        IT-advanced."""
        rpc = RpcSimulata()
        rpc.registra("it_advanced", da_it_advanced(it_advanced()), sostituisci=True)
        rpc.registra("it_full", da_it_full(it_full()), sostituisci=False)
        valori, fonti = rpc.fuse[2021]
        assert valori["patrimonio_netto"] == D(563473) and fonti["patrimonio_netto"] == "it_full"
        assert fonti["fatturato"] == "it_full" and fonti["risultato_esercizio"] == "it_full"
        assert valori["dipendenti"] == D(30) and fonti["dipendenti"] == "it_advanced"
        valori, fonti = rpc.fuse[2022]
        assert set(fonti.values()) == {"it_advanced"}
        assert valori["patrimonio_netto"] is None
        assert sorted(rpc.fuse) == [2017, 2018, 2019, 2020, 2021, 2022]

    def test_rimappatura_it_full_senza_sostituisci_non_perde_anni(self):
        """B5: la rimappatura pigra di it_full NON usa `sostituisci`: il PN
        degli import precedenti (altri anni) resta."""
        rpc = RpcSimulata()
        vecchio = copy.deepcopy(it_full())
        vecchio["ecofin"]["balanceSheetDate"] = "2020-12-31T00:00:00"
        vecchio["ecofin"]["turnoverYear"] = 2020
        rpc.registra("it_full", da_it_full(vecchio), sostituisci=False)
        rpc.registra("it_full", da_it_full(it_full()), sostituisci=False)
        assert sorted(rpc.fuse) == [2020, 2021]
