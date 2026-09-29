"""Schemi dei piani: modalità di visualizzazione prezzo (migration 0010),
alert, bullet custom e limiti del modulo partenariati (0036); coerenza tra
PlanOut e le select del piano (PLAN_SELECT e gli embed di user_service)."""

import ast
import inspect
import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.plan import PlanCreate, PlanOut, PlanUpdate
from app.services import plan_service, user_service


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


class TestLimitiPartenariato:
    """0036: None = illimitato, 0 = esclusa (semantica OPPOSTA ad
    alert_ritardo_giorni), default 0 come la colonna."""

    CAMPI = ("partner_calls_attive_max", "partner_candidature_mese")

    @pytest.mark.parametrize("campo", CAMPI)
    def test_create_default_zero(self, campo):
        plan = make_create()
        assert getattr(plan, campo) == 0
        # Il default arriva all'insert: un piano nuovo nasce escluso anche lato API.
        assert plan.model_dump(mode="json")[campo] == 0

    @pytest.mark.parametrize("campo", CAMPI)
    def test_create_none_vuol_dire_illimitato(self, campo):
        plan = make_create(**{campo: None})
        assert plan.model_dump(mode="json")[campo] is None

    @pytest.mark.parametrize("campo", CAMPI)
    @pytest.mark.parametrize("valore", [0, 1, 50])
    def test_create_valori_ammessi(self, campo, valore):
        assert getattr(make_create(**{campo: valore}), campo) == valore

    @pytest.mark.parametrize("campo", CAMPI)
    def test_negativo_respinto(self, campo):
        with pytest.raises(ValidationError):
            make_create(**{campo: -1})
        with pytest.raises(ValidationError):
            PlanUpdate(**{campo: -1})

    @pytest.mark.parametrize("campo", CAMPI)
    def test_update_none_esplicito_vuol_dire_illimitato(self, campo):
        changes = PlanUpdate(**{campo: None}).model_dump(mode="json", exclude_unset=True)
        assert changes == {campo: None}

    @pytest.mark.parametrize("campo", CAMPI)
    def test_update_zero_esclude(self, campo):
        changes = PlanUpdate(**{campo: 0}).model_dump(mode="json", exclude_unset=True)
        assert changes == {campo: 0}

    def test_update_senza_i_campi_non_li_tocca(self):
        changes = PlanUpdate(ai_check=10).model_dump(mode="json", exclude_unset=True)
        assert changes == {"ai_check": 10}

    def test_out_senza_colonne_vale_escluso(self):
        # Robustezza sugli embed: una select senza le colonne ricade su 0
        # (esclusa), mai su None (illimitato).
        plan = PlanOut(
            id=1, nome="X", slug="x", prezzo_annuale=Decimal("0"), ai_check=0,
            alert_attivo=False, num_account_aziendali=1, ordering=0, is_active=True,
        )
        assert plan.partner_calls_attive_max == 0
        assert plan.partner_candidature_mese == 0

    def test_out_conserva_null(self):
        plan = PlanOut(
            id=1, nome="X", slug="x", prezzo_annuale=Decimal("0"), ai_check=0,
            alert_attivo=False, num_account_aziendali=1, ordering=0, is_active=True,
            partner_calls_attive_max=None, partner_candidature_mese=None,
        )
        dump = plan.model_dump(mode="json")
        assert dump["partner_calls_attive_max"] is None
        assert dump["partner_candidature_mese"] is None


def _embed_del_piano(sorgente: str) -> list[set[str]]:
    """Colonne di ogni embed ``subscription_plans(...)`` COMPLETO (quelli che
    popolano PlanOut: contengono ``max_aziende``) nelle stringhe del modulo.
    Le stringhe adiacenti sono già un'unica costante nell'AST."""
    embed = []
    for nodo in ast.walk(ast.parse(sorgente)):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            for colonne in re.findall(r"subscription_plans\(([^)]*)\)", nodo.value):
                if "max_aziende" in colonne:
                    embed.append(set(colonne.split(",")))
    return embed


class TestSelectDelPiano:
    """Ogni campo di PlanOut arriva da ogni select del piano: un campo nuovo
    dimenticato in un embed ricadrebbe in silenzio sul default dello schema."""

    CAMPI = set(PlanOut.model_fields)

    def test_plan_select(self):
        assert set(plan_service.PLAN_SELECT.split(",")) == self.CAMPI

    def test_embed_di_user_service(self):
        embed = _embed_del_piano(inspect.getsource(user_service))
        # SUBSCRIPTION_EMBED, abbonamento attivo e lista admin degli utenti.
        assert len(embed) == 3
        for colonne in embed:
            assert colonne == self.CAMPI
