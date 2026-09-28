"""Prezzi e costi delle chiamate Anthropic in centesimi di USD (services/ai_prezzi.py).

Il costo serve al budget fail-closed: si arrotonda sempre per eccesso e un
modello sconosciuto costa come il più caro."""

import logging

import pytest

from app.services.ai_prezzi import (
    CARATTERI_PER_TOKEN,
    PREZZI_CENTS_PER_MTOK,
    costo_cents,
    stima_cents,
)


def test_tabella_del_contratto():
    assert PREZZI_CENTS_PER_MTOK == {
        "claude-sonnet-5": (200, 1000),
        "claude-sonnet-4-6": (300, 1500),
        "claude-opus-5": (500, 2500),
        "claude-haiku-4-5": (100, 500),
    }
    assert CARATTERI_PER_TOKEN == 2.5


class TestCosto:
    @pytest.mark.parametrize(
        ("model", "atteso"),
        [
            ("claude-sonnet-5", 1200),
            ("claude-sonnet-4-6", 1800),
            ("claude-opus-5", 3000),
            ("claude-haiku-4-5", 600),
        ],
    )
    def test_un_milione_di_token_per_lato(self, model, atteso):
        assert costo_cents(model, 1_000_000, 1_000_000) == atteso

    def test_caso_tipico_dell_estrazione(self):
        # 30k in / 8k out su sonnet-5: 0,06 $ + 0,08 $ = 14 centesimi
        assert costo_cents("claude-sonnet-5", 30_000, 8_000) == 14

    def test_arrotonda_per_eccesso(self):
        assert costo_cents("claude-sonnet-5", 1, 0) == 1
        assert costo_cents("claude-sonnet-5", 0, 1) == 1
        assert costo_cents("claude-sonnet-5", 5_001, 0) == 2

    def test_confine_esatto_non_arrotonda(self):
        # 5000 token × 200 cent/MTok = esattamente 1 centesimo
        assert costo_cents("claude-sonnet-5", 5_000, 0) == 1
        assert costo_cents("claude-sonnet-5", 1_000, 1_000) == 2  # 0,2 + 1,0 → 1,2 → 2

    @pytest.mark.parametrize(("tok_in", "tok_out"), [(0, 0), (None, None), (-10, -5)])
    def test_token_assenti_o_negativi_valgono_zero(self, tok_in, tok_out):
        assert costo_cents("claude-sonnet-5", tok_in, tok_out) == 0

    def test_modello_sconosciuto_costa_come_il_piu_caro(self, caplog):
        with caplog.at_level(logging.WARNING, logger="bandofit.ai"):
            costo = costo_cents("claude-futuro-9", 1_000_000, 1_000_000)
        assert costo == 3000  # opus-5: 500 + 2500
        assert "claude-futuro-9" in caplog.text

    def test_il_piu_caro_e_per_componente(self, monkeypatch):
        """Anche con una tabella in cui nessun modello è il più caro su
        entrambi i lati, l'ignoto prende il massimo di ciascuno."""
        monkeypatch.setattr(
            "app.services.ai_prezzi.PREZZI_CENTS_PER_MTOK",
            {"a": (900, 100), "b": (100, 900)},
        )
        assert costo_cents("ignoto", 1_000_000, 1_000_000) == 1800


class TestStima:
    def test_caso_peggiore_dei_tetti_wp3(self):
        # 180.000 caratteri → 72.000 token in; 16.000 out tutti usati
        # 72.000 × 200 + 16.000 × 1000 = 30.400.000 micro → 31 centesimi
        assert stima_cents("claude-sonnet-5", 180_000, 16_000) == 31

    def test_caratteri_per_token_per_eccesso(self):
        # 5 caratteri = 2 token esatti; 6 caratteri = 3 token (per eccesso)
        assert stima_cents("claude-opus-5", 5_000_000, 0) == costo_cents(
            "claude-opus-5", 2_000_000, 0
        )
        assert stima_cents("claude-opus-5", 6, 0) == costo_cents("claude-opus-5", 3, 0)

    def test_copre_il_tokenizer_di_sonnet_5(self):
        """Testo che sui modelli precedenti rende 3,5 caratteri per token: il
        tokenizer di claude-sonnet-5 ne produce fino a ~1,35 volte tanti
        (skill claude-api, model-migration). La riserva deve coprirlo."""
        import math

        for caratteri in (10_000, 100_000, 207_000):
            tok_sonnet_5 = math.ceil(caratteri / 3.5 * 1.35)
            assert costo_cents("claude-sonnet-5", tok_sonnet_5, 16_000) <= stima_cents(
                "claude-sonnet-5", caratteri, 16_000
            )

    def test_la_stima_copre_ogni_uso_entro_i_tetti(self):
        """Proprietà: con input davvero a CARATTERI_PER_TOKEN caratteri per
        token (o più) e output entro max_tokens, il costo reale non supera
        mai la riserva."""
        for caratteri in (0, 1, 350, 9_999, 180_000):
            for out_max in (0, 1, 4_000, 16_000):
                riserva = stima_cents("claude-sonnet-5", caratteri, out_max)
                tok_in_reali = int(caratteri / CARATTERI_PER_TOKEN)
                for out_reale in (0, out_max // 2, out_max):
                    assert costo_cents("claude-sonnet-5", tok_in_reali, out_reale) <= riserva

    def test_modello_sconosciuto_stima_col_piu_caro(self):
        assert stima_cents("sconosciuto", 180_000, 16_000) == stima_cents(
            "claude-opus-5", 180_000, 16_000
        )

    def test_valori_negativi_non_riducono_la_stima(self):
        assert stima_cents("claude-sonnet-5", -100, -5) == 0
