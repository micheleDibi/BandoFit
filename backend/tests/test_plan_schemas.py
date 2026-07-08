"""Schemi dei piani: modalità di visualizzazione prezzo (migration 0010)."""

from decimal import Decimal

import pytest

from app.schemas.plan import PlanCreate, PlanOut, PlanUpdate


def make_create(**overrides) -> PlanCreate:
    base = dict(
        nome="Enterprise",
        slug="enterprise",
        prezzo_annuale=Decimal("0"),
        ai_check=100,
        num_account_aziendali=10,
    )
    base.update(overrides)
    return PlanCreate(**base)


class TestTipoPrezzo:
    def test_default_importo(self):
        assert make_create().tipo_prezzo == "importo"

    def test_valore_non_valido_respinto(self):
        with pytest.raises(ValueError):
            make_create(tipo_prezzo="a_pagamento")

    def test_su_richiesta_senza_etichetta_valido(self):
        # Nessun vincolo cross-campo: la UI mostra il fallback «Su richiesta».
        plan = make_create(tipo_prezzo="su_richiesta")
        assert plan.etichetta_prezzo is None

    def test_update_azzera_etichetta_esplicitamente(self):
        changes = PlanUpdate(etichetta_prezzo=None).model_dump(
            mode="json", exclude_unset=True
        )
        assert changes == {"etichetta_prezzo": None}

    def test_out_tollera_righe_senza_campi_nuovi(self):
        # Robustezza sugli embed: una riga serializzata senza i campi 0010
        # (es. select non aggiornata) ricade sul default 'importo'.
        plan = PlanOut(
            id=1,
            nome="Smart",
            slug="smart",
            prezzo_annuale=Decimal("99"),
            ai_check=5,
            alert_attivo=True,
            alert_giorni_preavviso=7,
            num_account_aziendali=1,
            ordering=2,
            is_active=True,
        )
        assert plan.tipo_prezzo == "importo"
        assert plan.etichetta_prezzo is None
