"""Aritmetica monetaria: i bordi decisi in review (giorno 0, ultimo giorno,
scaduto, gratuito, pari prezzo, ciclo da 366 giorni) e gli arrotondamenti."""

from decimal import Decimal

import pytest

from app.services import pricing

D = Decimal


class TestCredito:
    @pytest.mark.parametrize(
        ("prezzo", "giorni", "atteso"),
        [
            (D("99.00"), 183, D("49.64")),   # 99×183/365 = 49.6356… → HALF_UP
            (D("99.00"), 365, D("99.00")),   # primo giorno: credito pieno
            (D("99.00"), 366, D("99.00")),   # ciclo bisestile: clamp al prezzo
            (D("99.00"), 0, D("0.00")),      # ultimo giorno
            (D("99.00"), -30, D("0.00")),    # scaduto non processato: clamp a 0
            (D("0.00"), 200, D("0.00")),     # upgrade da gratuito
            (D("299.00"), 11, D("9.01")),    # 299×11/365 = 9.0109… → HALF_UP
        ],
    )
    def test_formula_e_clamp(self, prezzo, giorni, atteso):
        assert pricing.credito_residuo(prezzo, giorni) == atteso


class TestUpgrade:
    def test_esempio_del_piano(self):
        imponibile, credito = pricing.imponibile_upgrade(D("299.00"), D("99.00"), 183)
        assert (imponibile, credito) == (D("249.36"), D("49.64"))

    def test_pari_prezzo_da_rifiutare(self):
        # listini admin anomali: il chiamante deve rifiutare (422), qui si
        # verifica solo che il segno sia quello giusto per la guardia
        imponibile, _ = pricing.imponibile_upgrade(D("299.00"), D("299.00"), 365)
        assert imponibile <= 0

    def test_ciclo_366_non_va_in_negativo_coi_prezzi_uguali(self):
        imponibile, credito = pricing.imponibile_upgrade(D("299.00"), D("299.00"), 366)
        assert credito == D("299.00") and imponibile == D("0.00")  # mai negativo


class TestIva:
    """Tabella IVA del venditore croato (decisa il 2026-07-21): 0% reverse
    charge SOLO per le aziende UE ≠ HR con prova VIES; tutto il resto 25%."""

    def test_azienda_it_senza_vies_25(self):
        iva, aliquota, natura = pricing.iva_per_soggetto(
            29900, tipo_soggetto="azienda", paese="IT", vies_valid=None
        )
        assert (iva, str(aliquota), natura) == (7475, "25.00", None)

    def test_azienda_ue_con_vies_reverse_charge(self):
        for paese in ("DE", "IT"):  # l'Italia è UE come gli altri (venditore HR)
            iva, aliquota, natura = pricing.iva_per_soggetto(
                29900, tipo_soggetto="azienda", paese=paese, vies_valid=True
            )
            assert (iva, str(aliquota), natura) == (0, "0.00", "RC-UE")

    def test_azienda_hr_domestica_25_anche_con_vies(self):
        iva, aliquota, natura = pricing.iva_per_soggetto(
            29900, tipo_soggetto="azienda", paese="HR", vies_valid=True
        )
        assert (iva, str(aliquota), natura) == (7475, "25.00", None)

    def test_azienda_ue_senza_prova_vies_25(self):
        for vies in (False, None):
            assert pricing.iva_per_soggetto(
                29900, tipo_soggetto="azienda", paese="DE", vies_valid=vies
            )[0] == 7475

    def test_azienda_extra_ue_25_prudenziale(self):
        for paese in ("US", "CH", "GB"):
            iva, _, natura = pricing.iva_per_soggetto(
                29900, tipo_soggetto="azienda", paese=paese, vies_valid=True
            )
            assert iva == 7475 and natura is None

    def test_privato_25_qualsiasi_paese(self):
        for paese in ("IT", "FR"):
            assert pricing.iva_per_soggetto(
                29900, tipo_soggetto="privato", paese=paese, vies_valid=True
            )[0] == 7475

    def test_anagrafica_assente_25(self):
        iva, aliquota, natura = pricing.iva_per_soggetto(29900)
        assert (iva, str(aliquota), natura) == (7475, "25.00", None)

    def test_arrotondamento_half_up(self):
        # 24937 × 25% = 6234.25 → 6234; 2 × 25% = 0.5 → 1 (HALF_UP)
        assert pricing.iva_per_soggetto(24937, tipo_soggetto="privato", paese="IT")[0] == 6234
        assert pricing.iva_per_soggetto(2)[0] == 1

    def test_invariante_totale(self):
        for imponibile in (1, 25, 6578, 24937, 69900):
            iva, _, _ = pricing.iva_per_soggetto(
                imponibile, tipo_soggetto="azienda", paese="IT"
            )
            assert iva >= 0


class TestInCents:
    def test_conversioni(self):
        assert pricing.in_cents(D("120.78")) == 12078
        assert pricing.in_cents(D("0.005")) == 1  # HALF_UP
        assert pricing.in_cents(D("0")) == 0
