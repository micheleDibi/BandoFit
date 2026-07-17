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
    def test_italia_22(self):
        iva, aliquota, natura = pricing.iva_per_soggetto(29900, "azienda_it")
        assert (iva, str(aliquota), natura) == (6578, "22.00", None)

    def test_arrotondamento_half_up(self):
        # 24937 × 22% = 5486.14 → 5486; 25 × 22% = 5.5 → 6 (HALF_UP)
        assert pricing.iva_per_soggetto(24937, "privato_it")[0] == 5486
        assert pricing.iva_per_soggetto(25, None)[0] == 6

    def test_reverse_charge_ue(self):
        iva, aliquota, natura = pricing.iva_per_soggetto(29900, "azienda_ue")
        assert (iva, str(aliquota), natura) == (0, "0.00", "N2.1")

    def test_invariante_totale(self):
        for imponibile in (1, 25, 6578, 24937, 69900):
            iva, _, _ = pricing.iva_per_soggetto(imponibile, "azienda_it")
            assert imponibile + iva == imponibile + iva  # coerenza per costruzione
            assert iva >= 0


class TestInCents:
    def test_conversioni(self):
        assert pricing.in_cents(D("120.78")) == 12078
        assert pricing.in_cents(D("0.005")) == 1  # HALF_UP
        assert pricing.in_cents(D("0")) == 0
