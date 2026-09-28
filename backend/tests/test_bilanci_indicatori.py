"""Indicatori, fasce e regole finanziarie (services/bilanci_indicatori.py).

Oltre ai calcoli, qui si difendono due proprietà:
- lo STESSO esercizio per numeratore e denominatore (mai rapporti tra anni
  diversi) e anni consecutivi per medie e crescita;
- in vista «terzi» l'esito dipende solo dalla FASCIA: variando il budget,
  l'esito cambia solo ai bordi delle fasce e nessun testo contiene i valori
  esatti (CGC 7.3, decisione Q11).
"""

import re
from dataclasses import asdict
from decimal import Decimal
from typing import get_args

import pytest

from app.schemas.bilanci import FasceOut, IndicatoreOut
from app.schemas.regole_finanziarie import RegolaFinanziaria, VariabileFinanziaria
from app.services import bilanci_indicatori as bi
from app.services.bilanci_indicatori import (
    CHIAVI_INDICATORI,
    FASCE_DIPENDENTI,
    FASCE_FATTURATO,
    FASCE_PATRIMONIO_NETTO,
    EsercizioBilancio,
    calcola_fasce,
    calcola_indicatori,
    intervallo_fascia,
    valida_regola,
    valuta_regola_finanziaria,
)
from app.services.openapi_mapping import codice_fascia_fatturato


def D(valore) -> Decimal:
    return Decimal(str(valore))


def es(anno: int, **valori) -> EsercizioBilancio:
    return EsercizioBilancio(
        anno=anno, valori={k: (None if v is None else D(v)) for k, v in valori.items()}
    )


def indicatori(esercizi) -> dict:
    return {i.chiave: i for i in calcola_indicatori(esercizi)}


def regola(numeratore, operatore, *, denominatore=None, soglia=None, soglia_variabile=None,
           coefficiente=None, unita=None, ambito="ciascun_partner") -> RegolaFinanziaria:
    if unita is None:
        if denominatore is not None:
            unita = "rapporto"
        elif numeratore in ("dipendenti", "bilanci_approvati_n"):
            unita = "numero"
        else:
            unita = "euro"
    return RegolaFinanziaria(
        id="r", descrizione="regola di prova", ambito=ambito, numeratore=numeratore,
        denominatore=denominatore, operatore=operatore, soglia=soglia,
        soglia_variabile=soglia_variabile, soglia_coefficiente=coefficiente, unita=unita,
    )


def punto(valore) -> tuple[Decimal, Decimal]:
    return (D(valore), D(valore))


# Le quattro regole note del piano (§2.2 B7).
COSTO_SU_FATTURATO = regola("costo_quota", "le", denominatore="fatturato_medio_2", soglia="0.6")
PN_META_COSTO = regola("patrimonio_netto", "gt", soglia_variabile="costo_quota", coefficiente="0.5")
ONERI_SU_FATTURATO = regola("oneri_finanziari", "lt", denominatore="fatturato", soglia="0.05")
DUE_BILANCI = regola("bilanci_approvati_n", "ge", soglia="2")


# ------------------------------------------------------------- indicatori

class TestIndicatori:
    def test_ordine_e_compatibilita_con_lo_schema(self):
        lista = calcola_indicatori([es(2022, fatturato=100), es(2023, fatturato=110)])
        assert tuple(i.chiave for i in lista) == CHIAVI_INDICATORI
        for indicatore in lista:
            out = IndicatoreOut(**asdict(indicatore))
            assert out.chiave == indicatore.chiave

    def test_crescita_su_anni_consecutivi(self):
        ind = indicatori([es(2022, fatturato=100), es(2023, fatturato=110)])
        assert ind["crescita_fatturato_pct"].valore == D("10.00")
        assert ind["crescita_fatturato_pct"].anni == [2022, 2023]

    def test_crescita_anni_non_consecutivi(self):
        ind = indicatori([es(2020, fatturato=100), es(2023, fatturato=110)])
        assert ind["crescita_fatturato_pct"].valore is None
        assert ind["crescita_fatturato_pct"].motivo_mancanza == "esercizi non consecutivi"

    def test_crescita_un_solo_esercizio(self):
        ind = indicatori([es(2023, fatturato=110)])
        assert ind["crescita_fatturato_pct"].valore is None
        assert "due esercizi" in ind["crescita_fatturato_pct"].motivo_mancanza

    def test_crescita_coppia_consecutiva_piu_recente(self):
        ind = indicatori([
            es(2019, fatturato=50), es(2020, fatturato=100),
            es(2022, fatturato=200), es(2023, fatturato=150),
        ])
        assert ind["crescita_fatturato_pct"].anni == [2022, 2023]
        assert ind["crescita_fatturato_pct"].valore == D("-25.00")

    def test_crescita_base_nulla_o_negativa(self):
        ind = indicatori([es(2022, fatturato=0), es(2023, fatturato=100)])
        assert ind["crescita_fatturato_pct"].valore is None
        assert "nullo o negativo" in ind["crescita_fatturato_pct"].motivo_mancanza

    def test_medie_consecutive(self):
        ind = indicatori([es(2021, fatturato=90), es(2022, fatturato=100), es(2023, fatturato=111)])
        assert ind["fatturato_medio_2"].valore == D("105.50")
        assert ind["fatturato_medio_2"].anni == [2022, 2023]
        assert ind["fatturato_medio_3"].valore == D("100.33")
        assert ind["fatturato_medio_3"].anni == [2021, 2022, 2023]

    def test_medie_anni_non_consecutivi(self):
        ind = indicatori([es(2019, fatturato=1), es(2021, fatturato=90), es(2023, fatturato=111)])
        assert ind["fatturato_medio_2"].valore is None
        assert ind["fatturato_medio_2"].motivo_mancanza == "esercizi non consecutivi"
        assert ind["fatturato_medio_3"].motivo_mancanza == "esercizi non consecutivi"

    def test_medie_esercizi_insufficienti(self):
        ind = indicatori([es(2022, fatturato=100), es(2023, fatturato=110)])
        assert ind["fatturato_medio_2"].valore == D("105.00")
        assert ind["fatturato_medio_3"].valore is None
        assert ind["fatturato_medio_3"].motivo_mancanza == "servono i fatturati di 3 esercizi"

    def test_rapporto_dallo_stesso_esercizio(self):
        # PN solo nel 2021 (IT-full), totale attivo 2021-2023 (IT-advanced):
        # mai PN 2021 / attivo 2023.
        ind = indicatori([
            es(2021, patrimonio_netto=200, totale_attivo=1000),
            es(2022, patrimonio_netto=None, totale_attivo=2000, fatturato=5),
            es(2023, totale_attivo=4000, fatturato=5),
        ])
        assert ind["indipendenza_finanziaria"].valore == D("0.2000")
        assert ind["indipendenza_finanziaria"].anni == [2021]

    def test_denominatore_nullo_o_negativo(self):
        ind = indicatori([es(2023, patrimonio_netto=200, totale_attivo=0)])
        assert ind["indipendenza_finanziaria"].valore is None
        assert ind["indipendenza_finanziaria"].motivo_mancanza == "totale attivo nullo o negativo"
        assert ind["indipendenza_finanziaria"].anni == [2023]
        ind = indicatori([es(2023, fatturato=-10, ebitda=5)])
        assert ind["mol_su_fatturato"].valore is None
        assert ind["mol_su_fatturato"].motivo_mancanza == "fatturato nullo o negativo"

    def test_percentuali(self):
        ind = indicatori([es(2023, fatturato=1000, ebitda=161.3, oneri_finanziari=12)])
        assert ind["mol_su_fatturato"].valore == D("16.13")
        assert ind["mol_su_fatturato"].unita == "percentuale"
        assert ind["oneri_finanziari_su_fatturato"].valore == D("1.20")

    def test_mancanze_motivate(self):
        ind = indicatori([])
        for chiave in CHIAVI_INDICATORI:
            assert ind[chiave].valore is None and ind[chiave].motivo_mancanza, chiave
        assert "immobilizzazioni" in ind["copertura_immobilizzazioni"].motivo_mancanza
        # testi per l'utente: niente gergo di versione
        assert all("v1" not in (i.motivo_mancanza or "") for i in ind.values())
        # voce mai valorizzata: non si fa credere che manchi un anno
        assert ind["oneri_finanziari_su_fatturato"].motivo_mancanza == (
            "servono oneri finanziari e fatturato dello stesso esercizio"
        )

    def test_copertura_sempre_none(self):
        ind = indicatori([es(2023, patrimonio_netto=1, totale_attivo=2, fatturato=3)])
        assert ind["copertura_immobilizzazioni"].valore is None

    def test_da_riga_db(self):
        riga = {
            "anno": 2023, "data_chiusura": "2023-12-31", "fatturato": 1234.5,
            "patrimonio_netto": "99.10", "ebitda": None, "tipo_bilancio": "abbreviato",
            "fonte_per_campo": {"fatturato": "it_advanced", "patrimonio_netto": "it_full"},
        }
        e = EsercizioBilancio.da_riga(riga)
        assert e.anno == 2023 and e.data_chiusura.isoformat() == "2023-12-31"
        assert e.valore("fatturato") == D("1234.5") and e.valore("patrimonio_netto") == D("99.10")
        assert e.valore("ebitda") is None and e.tipo_bilancio == "abbreviato"
        assert e.fonti["patrimonio_netto"] == "it_full" and e.ha_core


# ------------------------------------------------------------------ fasce

def _fascia(scala, valore):
    return bi._codice_fascia(scala, D(valore))


class TestFasce:
    @pytest.mark.parametrize("valore,codice", [
        (0, "fino_100k"), (100_000, "fino_100k"), ("100000.01", "100k_500k"),
        (500_000, "100k_500k"), ("500000.01", "500k_2m"), (2_000_000, "500k_2m"),
        (10_000_000, "2m_10m"), (50_000_000, "10m_50m"), ("50000000.01", "oltre_50m"),
    ])
    def test_fatturato_ai_bordi(self, valore, codice):
        assert calcola_fasce([es(2023, fatturato=valore)]).fatturato == codice
        # stessa tabella dell'enum di company_profiles e degli intervalli per i terzi
        assert codice_fascia_fatturato(D(valore)) == codice
        assert _fascia(FASCE_FATTURATO, valore) == codice

    def test_fatturato_negativo_senza_fascia(self):
        assert calcola_fasce([es(2023, fatturato=-1)]).fatturato is None

    @pytest.mark.parametrize("valore,codice", [
        ("-0.01", "negativo"), (0, "fino_100k"), (100_000, "fino_100k"),
        ("100000.01", "100k_500k"), (500_000, "100k_500k"), (2_000_000, "500k_2m"),
        (10_000_000, "2m_10m"), ("10000000.01", "oltre_10m"),
    ])
    def test_patrimonio_netto_ai_bordi(self, valore, codice):
        assert calcola_fasce([es(2023, patrimonio_netto=valore)]).patrimonio_netto == codice

    @pytest.mark.parametrize("valore,codice", [
        (0, "0"), ("0.5", "1_9"), (1, "1_9"), (9, "1_9"), ("9.99", "1_9"), (10, "10_49"),
        (49, "10_49"), (50, "50_249"), (249, "50_249"), (250, "250_oltre"),
    ])
    def test_dipendenti_ai_bordi(self, valore, codice):
        assert calcola_fasce([es(2023, fatturato=1, dipendenti=valore)]).dipendenti == codice

    @pytest.mark.parametrize("corrente,trend", [
        (105, "stabile"), ("105.01", "crescita"), (95, "stabile"), ("94.99", "calo"),
    ])
    def test_trend_ai_bordi(self, corrente, trend):
        fasce = calcola_fasce([es(2022, fatturato=100), es(2023, fatturato=corrente)])
        assert fasce.trend_fatturato == trend

    def test_trend_senza_esercizi_consecutivi(self):
        assert calcola_fasce([es(2021, fatturato=1), es(2023, fatturato=2)]).trend_fatturato is None

    def test_trend_solo_sulla_coppia_dell_anno_di_riferimento(self):
        # 2019→2020 in crescita, poi un buco e il 2023: la card direbbe «In
        # crescita — esercizio 2023» con la crescita di tre anni prima.
        fasce = calcola_fasce([
            es(2019, fatturato=100), es(2020, fatturato=200), es(2023, fatturato=150),
        ])
        assert fasce.anno_riferimento == 2023 and fasce.trend_fatturato is None

    def test_ultimo_esercizio_con_il_dato_e_anno_di_riferimento(self):
        fasce = calcola_fasce([
            es(2021, fatturato=4_000_000, patrimonio_netto=563_473),
            es(2022, fatturato=5_000_000, dipendenti=33),
        ])
        assert fasce == bi.Fasce("2m_10m", "500k_2m", "10_49", "crescita", 2022)
        assert FasceOut(**asdict(fasce)).anno_riferimento == 2022
        # senza fatturato: il più recente tra le altre fasce
        fasce = calcola_fasce([es(2020, patrimonio_netto=10), es(2021, risultato_esercizio=1,
                                                                 dipendenti=3)])
        assert fasce.fatturato is None and fasce.anno_riferimento == 2021

    def test_nessun_esercizio(self):
        assert calcola_fasce([]) == bi.Fasce(None, None, None, None, None)

    def test_intervallo_fascia(self):
        assert intervallo_fascia("fatturato", "100k_500k") == (D(100_000), D(500_000))
        assert intervallo_fascia("fatturato", "oltre_50m") == (D(50_000_000), None)
        assert intervallo_fascia("patrimonio_netto", "negativo") == (None, D(0))
        assert intervallo_fascia("dipendenti", "0") == (D(0), D(0))
        with pytest.raises(ValueError):
            intervallo_fascia("fatturato", "1m_5m")
        with pytest.raises(ValueError):
            intervallo_fascia("costo_quota", "fino_100k")

    @pytest.mark.parametrize("scala", [FASCE_FATTURATO, FASCE_PATRIMONIO_NETTO, FASCE_DIPENDENTI])
    def test_ogni_valore_cade_nella_sua_fascia(self, scala):
        """Il codice calcolato su un valore e l'intervallo usato per i terzi
        devono essere coerenti, altrimenti la vista terzi darebbe esiti falsi."""
        campioni = [D("-5"), D(0), D("0.5"), D(9), D(10), D(100_000), D("100000.01"),
                    D(499_999), D(500_000), D(2_000_000), D(10_000_000), D(60_000_000)]
        for valore in campioni:
            codice = bi._codice_fascia(scala, valore)
            if codice is None:
                continue
            fascia = next(f for f in scala if f[0] == codice)
            assert bi._contiene(fascia, valore)
            # nessun'altra fascia lo contiene
            assert sum(bi._contiene(f, valore) for f in scala) == 1


# ------------------------------------------------------------------ regole

DUE_ANNI = [es(2022, fatturato=1000), es(2023, fatturato=1000)]


class TestRegoleNote:
    """Le quattro regole note nei tre esiti (vista «proprio»)."""

    def test_costo_su_fatturato_medio(self):
        v = lambda c: valuta_regola_finanziaria(  # noqa: E731
            COSTO_SU_FATTURATO, DUE_ANNI, punto(c), vista="proprio"
        )
        assert v(500).esito == "soddisfatto"
        assert v(700).esito == "non_soddisfatto"
        mancante = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, [es(2023, fatturato=1000)], punto(500), vista="proprio"
        )
        assert mancante.esito == "dato_mancante"
        assert v(500).anni_usati == [2022, 2023]

    def test_bordo_0_6_minore_uguale_e_soddisfatto(self):
        esito = valuta_regola_finanziaria(COSTO_SU_FATTURATO, DUE_ANNI, punto(600), vista="proprio")
        assert esito.esito == "soddisfatto"
        # anche con un fatturato medio non intero (1000,5): 600,3 / 1000,5 = 0,6 esatto
        esercizi = [es(2022, fatturato=1000), es(2023, fatturato=1001)]
        esito = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, esercizi, punto("600.3"), vista="proprio"
        )
        assert esito.esito == "soddisfatto"
        stretto = regola("costo_quota", "lt", denominatore="fatturato_medio_2", soglia="0.6")
        assert valuta_regola_finanziaria(
            stretto, DUE_ANNI, punto(600), vista="proprio"
        ).esito == "non_soddisfatto"

    def test_soglia_con_virgola_decimale(self):
        r = regola("costo_quota", "le", denominatore="fatturato_medio_2", soglia="0,6")
        assert valuta_regola_finanziaria(r, DUE_ANNI, punto(600), vista="proprio").esito == (
            "soddisfatto"
        )

    def test_pn_maggiore_di_meta_costo(self):
        pn = lambda v: [es(2021, patrimonio_netto=v, fatturato=1)]  # noqa: E731
        assert valuta_regola_finanziaria(
            PN_META_COSTO, pn(600), punto(1000), vista="proprio"
        ).esito == "soddisfatto"
        # bordo: 500 > 500 è falso
        assert valuta_regola_finanziaria(
            PN_META_COSTO, pn(500), punto(1000), vista="proprio"
        ).esito == "non_soddisfatto"
        mancante = valuta_regola_finanziaria(
            PN_META_COSTO, [es(2021, fatturato=1)], punto(1000), vista="proprio"
        )
        assert mancante.esito == "dato_mancante"
        assert mancante.motivo == "manca il dato: patrimonio netto"

    def test_pn_dell_ultimo_esercizio_che_lo_ha(self):
        esercizi = [es(2021, patrimonio_netto=600, fatturato=1), es(2022, fatturato=2)]
        esito = valuta_regola_finanziaria(PN_META_COSTO, esercizi, punto(1000), vista="proprio")
        assert esito.esito == "soddisfatto" and esito.anni_usati == [2021]

    def test_oneri_su_fatturato(self):
        v = lambda oneri: valuta_regola_finanziaria(  # noqa: E731
            ONERI_SU_FATTURATO, [es(2023, fatturato=1000, oneri_finanziari=oneri)], None,
            vista="proprio",
        )
        assert v(40).esito == "soddisfatto"
        assert v(50).esito == "non_soddisfatto"  # bordo: 0,05 < 0,05 è falso
        assert v(None).esito == "dato_mancante"

    def test_denominatore_nullo_o_negativo(self):
        for fatturato in (0, -100):
            esito = valuta_regola_finanziaria(
                ONERI_SU_FATTURATO, [es(2023, fatturato=fatturato, oneri_finanziari=1)], None,
                vista="proprio",
            )
            assert esito.esito == "dato_mancante"
            assert esito.motivo == "denominatore nullo o negativo"

    def test_due_bilanci_approvati(self):
        v = lambda esercizi: valuta_regola_finanziaria(  # noqa: E731
            DUE_BILANCI, esercizi, None, vista="proprio"
        )
        assert v([es(2022, fatturato=1), es(2023, patrimonio_netto=1)]).esito == "soddisfatto"
        assert v([es(2023, fatturato=1)]).esito == "non_soddisfatto"
        vuoto = v([])
        assert vuoto.esito == "dato_mancante" and vuoto.motivo == "nessun bilancio disponibile"
        # un esercizio senza campi CORE non conta
        assert v([es(2022, dipendenti=3), es(2023, fatturato=1)]).esito == "non_soddisfatto"

    def test_due_bilanci_con_storico_incompleto(self):
        # Solo IT-full (IT-advanced saltato): 1 bilancio è un minimo, non un totale.
        esito = valuta_regola_finanziaria(
            DUE_BILANCI, [es(2023, fatturato=1)], None, vista="proprio", storico_completo=False
        )
        assert esito.esito == "dato_mancante"
        assert esito.motivo == bi.MOTIVO_STORICO_INCOMPLETO
        esito = valuta_regola_finanziaria(
            DUE_BILANCI, [es(2022, fatturato=1), es(2023, fatturato=1)], None,
            vista="proprio", storico_completo=False,
        )
        assert esito.esito == "soddisfatto"

    def test_costo_quota_mancante_o_intervallo(self):
        assert valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, DUE_ANNI, None, vista="proprio"
        ).motivo == "manca il costo della quota"
        esito = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, DUE_ANNI, (D(500), D(700)), vista="proprio"
        )
        assert esito.esito == "dato_mancante" and esito.motivo == bi.MOTIVO_DIPENDE_BUDGET
        # intervallo tutto sotto soglia: determinato; estremi invertiti tollerati
        assert valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, DUE_ANNI, (D(550), D(100)), vista="proprio"
        ).esito == "soddisfatto"

    def test_spiegazione_titolare_con_i_valori(self):
        esito = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, [es(2022, fatturato=1_000_000), es(2023, fatturato=1_000_000)],
            punto(600_000), vista="proprio",
        )
        assert esito.spiegazione_titolare.startswith("Soddisfatto")
        assert "600.000 €" in esito.spiegazione_titolare
        assert "1.000.000 €" in esito.spiegazione_titolare
        assert "0,6" in esito.spiegazione_titolare

    def test_stesso_esercizio_nei_rapporti(self):
        r = regola("patrimonio_netto", "ge", denominatore="totale_attivo", soglia="0.2")
        esercizi = [
            es(2021, patrimonio_netto=300, totale_attivo=1000),
            es(2022, totale_attivo=5000, fatturato=1),
        ]
        esito = valuta_regola_finanziaria(r, esercizi, None, vista="proprio")
        assert esito.esito == "soddisfatto" and esito.anni_usati == [2021]

    def test_variabili_non_supportate(self):
        r = regola("contributo_quota", "le", denominatore="fatturato", soglia="0.5")
        esito = valuta_regola_finanziaria(r, DUE_ANNI, punto(1), vista="proprio")
        assert esito.esito == "dato_mancante" and "non supportata" in esito.motivo

    def test_ogni_variabile_del_contratto_e_gestita(self):
        """Un'aggiunta al Literal senza gestione qui farebbe crollare la
        valutazione: ogni variabile deve avere un ramo."""
        gestite = (
            set(bi._COLONNA_ANNUALE) | set(bi._MEDIE) | bi._NON_SUPPORTATE
            | {"bilanci_approvati_n", "costo_quota"}
        )
        assert set(get_args(VariabileFinanziaria)) == gestite
        assert set(get_args(VariabileFinanziaria)) == set(bi._ETICHETTE_VARIABILI)


# -------------------------------------------------------------- vista terzi

def _numeri_esatti(valore: Decimal) -> set[str]:
    """Le forme in cui un importo esatto potrebbe comparire in un testo."""
    intero = int(valore)
    return {str(intero), f"{intero:,}".replace(",", "."), f"{intero:,}", str(valore)}


class TestVistaTerzi:
    # fatturato medio 2022-2023 = 1.234.567 → fascia 500k_2m = (500.000, 2.000.000]
    ESERCIZI = [es(2022, fatturato=1_200_000), es(2023, fatturato=1_269_134)]
    # stessa fascia, valore esatto diverso
    ESERCIZI_GEMELLI = [es(2022, fatturato=600_000), es(2023, fatturato=1_900_000)]

    def test_trenta_budget_esito_cambia_solo_ai_bordi_di_fascia(self):
        media = D("1234567")
        costi = [D(50_000) * k for k in range(1, 31)]  # 50k … 1,5M
        esiti = []
        for costo in costi:
            terzi = valuta_regola_finanziaria(
                COSTO_SU_FATTURATO, self.ESERCIZI, punto(costo), vista="terzi"
            )
            gemello = valuta_regola_finanziaria(
                COSTO_SU_FATTURATO, self.ESERCIZI_GEMELLI, punto(costo), vista="terzi"
            )
            # l'esito verso i terzi non distingue due aziende nella stessa fascia
            assert (terzi.esito, terzi.motivo, terzi.spiegazione_terzi) == (
                gemello.esito, gemello.motivo, gemello.spiegazione_terzi
            )
            assert terzi.spiegazione_titolare is None
            for testo in (terzi.spiegazione_terzi, terzi.motivo or ""):
                for vietato in _numeri_esatti(media) | {"1.200.000", "1.269.134", "1200000",
                                                       "1269134"}:
                    assert vietato not in testo
            esiti.append(terzi.esito)
            # sound: un esito determinato verso i terzi coincide con quello esatto
            proprio = valuta_regola_finanziaria(
                COSTO_SU_FATTURATO, self.ESERCIZI, punto(costo), vista="proprio"
            )
            if terzi.esito != "dato_mancante":
                assert terzi.esito == proprio.esito

        # Bordi attesi: 0,6 × 500.000 = 300.000 e 0,6 × 2.000.000 = 1.200.000.
        # L'esito esatto cambierebbe a 0,6 × 1.234.567 ≈ 740.740: lì NON deve cambiare.
        for costo, esito in zip(costi, esiti):
            if costo <= 300_000:
                assert esito == "soddisfatto", costo
            elif costo <= 1_200_000:
                assert esito == "dato_mancante", costo
            else:
                assert esito == "non_soddisfatto", costo
        cambi = [costi[i] for i in range(1, len(costi)) if esiti[i] != esiti[i - 1]]
        assert cambi == [D(350_000), D(1_250_000)]

    def test_motivi_budget_o_fascia(self):
        esito = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, self.ESERCIZI, punto(900_000), vista="terzi"
        )
        assert esito.esito == "dato_mancante" and esito.motivo == bi.MOTIVO_DIPENDE_FASCIA
        # budget da 100k (sempre ok sulla fascia) a 1,5M (mai ok): dipende dal budget
        esito = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, self.ESERCIZI, (D(100_000), D(1_500_000)), vista="terzi"
        )
        assert esito.esito == "dato_mancante" and esito.motivo == bi.MOTIVO_DIPENDE_BUDGET

    def test_spiegazione_terzi_solo_esito_e_fasce(self):
        esito = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, self.ESERCIZI, punto(100_000), vista="terzi"
        )
        assert esito.esito == "soddisfatto"
        assert "500k_2m" in esito.spiegazione_terzi
        assert "€" not in esito.spiegazione_terzi
        # nessun numero oltre agli anni e ai codici di fascia
        numeri = re.findall(r"\d[\d.,]*", esito.spiegazione_terzi)
        consentiti = {"2", "2022", "2023", "500", "2m"}
        assert all(n.rstrip(".,") in consentiti for n in numeri), numeri

    def test_pn_sulla_fascia(self):
        esercizi = [es(2021, patrimonio_netto=563_473, fatturato=1)]  # 500k_2m
        v = lambda c: valuta_regola_finanziaria(  # noqa: E731
            PN_META_COSTO, esercizi, punto(c), vista="terzi"
        )
        assert v(900_000).esito == "soddisfatto"  # PN > 500k ≥ 450k su tutta la fascia
        assert v(1_000_000).esito == "soddisfatto"  # PN > 500k: il bordo escluso conta
        assert v(4_000_000).esito == "non_soddisfatto"  # PN ≤ 2M = 4M/2
        assert v(2_000_000).esito == "dato_mancante"
        for costo in (900_000, 1_000_000, 4_000_000, 2_000_000):
            assert "563" not in v(costo).spiegazione_terzi

    def test_estremi_aperti_delle_fasce(self):
        # 100k_500k esclude 100.000: «> 100.000» è certo per tutta la fascia
        maggiore = regola("fatturato", "gt", soglia="100000")
        esito = valuta_regola_finanziaria(maggiore, [es(2023, fatturato=250_000)], None,
                                          vista="terzi")
        assert esito.esito == "soddisfatto"
        almeno = regola("fatturato", "ge", soglia="500000")
        esito = valuta_regola_finanziaria(almeno, [es(2023, fatturato=300_000)], None,
                                          vista="terzi")
        assert esito.esito == "dato_mancante"  # 500.000 è nella fascia
        dieci = regola("dipendenti", "ge", soglia="10")
        assert valuta_regola_finanziaria(
            dieci, [es(2023, fatturato=1, dipendenti=5)], None, vista="terzi"
        ).esito == "non_soddisfatto"  # 1_9 = (0, 10)
        assert valuta_regola_finanziaria(
            dieci, [es(2023, fatturato=1, dipendenti=12)], None, vista="terzi"
        ).esito == "soddisfatto"  # 10_49 = [10, 50)

    def test_denominatore_che_puo_essere_zero(self):
        # fino_100k contiene lo zero: sul valore esatto 0 l'esito sarebbe
        # «dato mancante», quindi la vista terzi non può decidere.
        esercizi = [es(2022, fatturato=50_000), es(2023, fatturato=50_000)]
        esito = valuta_regola_finanziaria(
            COSTO_SU_FATTURATO, esercizi, punto(10_000_000), vista="terzi"
        )
        assert esito.esito == "dato_mancante" and esito.motivo == bi.MOTIVO_DIPENDE_FASCIA

    def test_patrimonio_negativo_come_denominatore(self):
        r = regola("costo_quota", "le", denominatore="patrimonio_netto", soglia="2")
        esito = valuta_regola_finanziaria(
            r, [es(2023, patrimonio_netto=-10)], punto(1), vista="terzi"
        )
        assert esito.esito == "dato_mancante"
        assert esito.motivo == "denominatore nullo o negativo"

    def test_conteggio_bilanci_esatto_anche_per_i_terzi(self):
        esito = valuta_regola_finanziaria(
            DUE_BILANCI, [es(2022, fatturato=1), es(2023, fatturato=1)], None, vista="terzi"
        )
        assert esito.esito == "soddisfatto" and esito.spiegazione_titolare is None


# ---------------------------------------------------------------- validità

class TestValidaRegola:
    def test_regole_note_valide(self):
        for r in (COSTO_SU_FATTURATO, PN_META_COSTO, ONERI_SU_FATTURATO, DUE_BILANCI):
            assert valida_regola(r) == [], r

    @pytest.mark.parametrize("kwargs,frammento", [
        (dict(soglia="0.6", soglia_variabile="costo_quota"), "esattamente una"),
        (dict(), "esattamente una"),
        (dict(soglia="abc"), "soglia non è"),
        (dict(soglia="1.000.000"), "soglia non è"),
        (dict(soglia="NaN"), "soglia non è"),
        (dict(soglia="Infinity"), "soglia non è"),
        (dict(soglia="0.6", coefficiente="0.5"), "solo con soglia_variabile"),
        (dict(soglia_variabile="costo_quota", coefficiente="-1"), "positivo"),
        (dict(soglia_variabile="costo_quota", coefficiente="x"), "coefficiente non è"),
    ])
    def test_errori(self, kwargs, frammento):
        r = regola("patrimonio_netto", "gt", **kwargs)
        errori = valida_regola(r)
        assert any(frammento in e for e in errori), errori

    def test_rapporto_degenere_e_unita(self):
        assert valida_regola(regola("fatturato", "le", denominatore="fatturato", soglia="1"))
        assert valida_regola(
            regola("patrimonio_netto", "gt", soglia_variabile="patrimonio_netto")
        )
        errori = valida_regola(regola("fatturato", "ge", soglia="100", unita="rapporto"))
        assert any("unità" in e for e in errori)
        errori = valida_regola(
            regola("costo_quota", "le", denominatore="fatturato", soglia="0.6", unita="euro")
        )
        assert any("unità" in e for e in errori)

    def test_regola_non_valida_non_decide(self):
        r = regola("patrimonio_netto", "gt", soglia="0.6", soglia_variabile="costo_quota")
        for vista in ("proprio", "terzi"):
            esito = valuta_regola_finanziaria(r, DUE_ANNI, punto(1), vista=vista)
            assert esito.esito == "dato_mancante" and esito.motivo == "regola non valida"


# ------------------------------------------------------------------ schemi

class TestSchemi:
    def test_bilanci_out_dal_calcolo(self):
        import json

        from app.schemas.bilanci import BilanciOut, EsercizioOut, ImportPreviewBilanci

        esercizi = [
            EsercizioBilancio(
                anno=2021, valori={"fatturato": D("4432761.00"), "patrimonio_netto": D(563473)},
                fonti={"fatturato": "it_full", "patrimonio_netto": "it_full"},
            ),
            EsercizioBilancio(anno=2022, valori={"fatturato": D(5102233)},
                              fonti={"fatturato": "it_advanced"}),
        ]
        out = BilanciOut(
            editable=True,
            stato="disponibili",
            storico_esito="ok",
            esercizi=[
                EsercizioOut(anno=e.anno, fonti=e.fonti, **{k: v for k, v in e.valori.items()})
                for e in esercizi
            ],
            indicatori=[IndicatoreOut(**asdict(i)) for i in calcola_indicatori(esercizi)],
            fasce=FasceOut(**asdict(calcola_fasce(esercizi))),
        )
        dati = json.loads(out.model_dump_json())
        assert dati["esercizi"][0]["fatturato"] == 4432761.0  # numero JSON, non stringa
        assert dati["esercizi"][0]["tipo_bilancio"] == "ignoto"
        assert dati["esercizi"][1]["fonti"] == {"fatturato": "it_advanced"}
        crescita = next(i for i in dati["indicatori"] if i["chiave"] == "crescita_fatturato_pct")
        assert crescita["valore"] == 15.1 and crescita["unita"] == "percentuale"
        assert dati["fasce"] == {
            "fatturato": "2m_10m", "patrimonio_netto": "500k_2m", "dipendenti": None,
            "trend_fatturato": "crescita", "anno_riferimento": 2022,
        }
        assert ImportPreviewBilanci().model_dump() == {
            "stato": "non_disponibili", "motivo": "non_richiesto", "anni": [],
        }

    def test_vocabolari_chiusi(self):
        from pydantic import ValidationError

        from app.schemas.bilanci import BilanciOut, ImportPreviewBilanci

        with pytest.raises(ValidationError):
            ImportPreviewBilanci(stato="disponibili", motivo="inventato")
        with pytest.raises(ValidationError):
            BilanciOut(editable=False, stato="boh")
        with pytest.raises(ValidationError):
            RegolaFinanziaria(
                id="r", descrizione="d", ambito="ciascun_partner", numeratore="utile_esercizio",
                denominatore=None, operatore="le", soglia="1", soglia_variabile=None,
                soglia_coefficiente=None, unita="euro",
            )
