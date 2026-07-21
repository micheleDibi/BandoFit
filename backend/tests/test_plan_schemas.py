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
        # (vedi anche TestAlertRitardo per il campo 0021)
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
        assert plan.alert_ritardo_giorni is None


class TestAlertRitardo:
    """Alert nuovi-bandi (0021): nullable-as-disabled, zero = stesso giorno."""

    def test_default_none(self):
        assert make_create().alert_ritardo_giorni is None

    def test_zero_valido(self):
        assert make_create(alert_ritardo_giorni=0).alert_ritardo_giorni == 0

    def test_negativo_respinto(self):
        with pytest.raises(ValueError):
            make_create(alert_ritardo_giorni=-1)

    def test_nessun_obbligo_con_alert_attivi(self):
        # Gate della feature = alert_attivo AND ritardo non-null: un piano può
        # avere gli alert scadenze attivi senza includere i nuovi-bandi.
        plan = make_create(alert_attivo=True, alert_giorni_preavviso=7)
        assert plan.alert_ritardo_giorni is None

    def test_update_azzera_esplicitamente(self):
        changes = PlanUpdate(alert_ritardo_giorni=None).model_dump(
            mode="json", exclude_unset=True
        )
        assert changes == {"alert_ritardo_giorni": None}


class TestFeaturesOverride:
    """Bullet custom della card piano (0029): trim, [] → None, limiti."""

    def test_default_none(self):
        assert make_create().features_override is None

    def test_lista_vuota_normalizzata_a_none(self):
        assert make_create(features_override=[]).features_override is None

    def test_trim_e_scarto_righe_vuote(self):
        plan = make_create(features_override=["  Proposta su misura  ", "", "  "])
        assert plan.features_override == ["Proposta su misura"]

    def test_limiti_voci_e_lunghezza(self):
        with pytest.raises(ValueError, match="8"):
            make_create(features_override=[f"voce {i}" for i in range(9)])
        with pytest.raises(ValueError, match="120"):
            make_create(features_override=["x" * 121])

    def test_update_azzera_esplicitamente(self):
        changes = PlanUpdate(features_override=None).model_dump(
            mode="json", exclude_unset=True
        )
        assert changes == {"features_override": None}

    def test_out_tollera_righe_senza_la_colonna(self):
        # Robustezza sugli embed pre-migration (come max_aziende).
        plan = PlanOut(
            id=1, nome="X", slug="x", prezzo_annuale=Decimal("0"), ai_check=0,
            alert_attivo=False, num_account_aziendali=1, ordering=0,
            is_active=True,
        )
        assert plan.features_override is None
