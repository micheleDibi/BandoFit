"""Test dei nuovi campi di registrazione/profilo (0022): validazione del
telefono nei body Pydantic, gate della posizione PRIMA del cooldown in
register, coerenza posizione/«Altro» in update_profile, mapping dell'embed."""

import pytest
from pydantic import ValidationError

from app.api.routers.auth import RegisterIn
from app.core.errors import BadRequestError
from app.schemas.user import ProfileUpdate
from app.services import auth_service, user_service
from tests.test_openapi_service import USER, FakePrimary

REGISTER_BASE = {
    "email": "mario@test.it",
    "nome": "Mario",
    "cognome": "Rossi",
    "telefono": "347 1234567",
    "job_position_slug": "cto",
}

POSIZIONE_CTO = {"id": 3, "nome": "CTO / Direttore Tecnico", "slug": "cto"}
POSIZIONE_ALTRO = {"id": 29, "nome": "Altro", "slug": "altro"}


class TestRegisterIn:
    def test_telefono_normalizzato_in_e164(self):
        data = RegisterIn(**REGISTER_BASE)
        assert data.telefono == "+393471234567"

    def test_telefono_invalido_rifiutato(self):
        with pytest.raises(ValidationError):
            RegisterIn(**{**REGISTER_BASE, "telefono": "non un numero"})

    def test_telefono_e_posizione_obbligatori(self):
        for campo in ("telefono", "job_position_slug"):
            body = {k: v for k, v in REGISTER_BASE.items() if k != campo}
            with pytest.raises(ValidationError):
                RegisterIn(**body)

    def test_altro_facoltativo(self):
        data = RegisterIn(**{**REGISTER_BASE, "job_position_altro": "Responsabile qualità"})
        assert data.job_position_altro == "Responsabile qualità"
        assert RegisterIn(**REGISTER_BASE).job_position_altro is None


class TestRegisterGatePosizione:
    @pytest.fixture(autouse=True)
    def reset_cooldown(self):
        auth_service._last_sent.clear()
        yield
        auth_service._last_sent.clear()

    async def test_slug_ignoto_respinto_senza_bruciare_il_cooldown(self):
        primary = FakePrimary(selects={"job_positions": []})
        with pytest.raises(BadRequestError):
            await auth_service.register(
                primary,
                email="mario@test.it",
                nome="Mario",
                cognome="Rossi",
                azienda=None,
                telefono="+393471234567",
                job_position_slug="astronauta",
                job_position_altro=None,
                plan_slug="gratuito",
            )
        # Il tentativo respinto non deve consumare il cooldown della
        # registrazione corretta che seguirà.
        assert auth_service._last_sent == {}


class TestUpdateProfilePosizione:
    @pytest.fixture(autouse=True)
    def stub_get_me(self, monkeypatch):
        async def fake_get_me(primary, user_id):
            return "me-sentinel"

        monkeypatch.setattr(user_service, "get_me", fake_get_me)

    async def test_posizione_valida_passa_cosi_come_inviata(self):
        # La coerenza col testo «Altro» è del trigger di riga (0022, test db):
        # il service valida solo che la posizione esista e sia attiva.
        primary = FakePrimary(selects={"job_positions": [POSIZIONE_CTO]})
        result = await user_service.update_profile(
            primary,
            USER["id"],
            ProfileUpdate(job_position_id=3, job_position_altro="testo residuo"),
        )
        assert result == "me-sentinel"
        [update] = primary.ops_for("profiles", "update")
        assert update == {"job_position_id": 3, "job_position_altro": "testo residuo"}

    async def test_posizione_disattivata_respinta_senza_update(self):
        # get_active_by_id non trova nulla (voce disattivata o inesistente).
        primary = FakePrimary(selects={"job_positions": []})
        with pytest.raises(BadRequestError):
            await user_service.update_profile(
                primary, USER["id"], ProfileUpdate(job_position_id=99)
            )
        assert primary.ops_for("profiles", "update") == []

    async def test_azzeramento_posizione_senza_lookup(self):
        # job_position_id esplicitamente None: nessuna validazione necessaria.
        primary = FakePrimary()
        await user_service.update_profile(
            primary, USER["id"], ProfileUpdate(job_position_id=None)
        )
        [update] = primary.ops_for("profiles", "update")
        assert update == {"job_position_id": None}

    async def test_telefono_assente_non_viene_toccato(self):
        # Il payload senza la chiave telefono non deve produrre alcun campo
        # telefono nell'update: i valori legacy non-E.164 restano intatti.
        primary = FakePrimary()
        await user_service.update_profile(
            primary, USER["id"], ProfileUpdate(nome="Mario")
        )
        [update] = primary.ops_for("profiles", "update")
        assert update == {"nome": "Mario"}


class TestProfileUpdateTelefono:
    def test_normalizzato(self):
        assert ProfileUpdate(telefono="347 1234567").telefono == "+393471234567"

    def test_vuoto_diventa_none(self):
        assert ProfileUpdate(telefono="").telefono is None
        assert ProfileUpdate(telefono="   ").telefono is None

    def test_invalido_rifiutato(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(telefono="non un numero")

    def test_altro_ripulito(self):
        assert ProfileUpdate(job_position_altro="  ").job_position_altro is None
        assert ProfileUpdate(job_position_altro=" x ").job_position_altro == "x"


class TestProfileFromRow:
    ROW = {
        "id": "a0000000-0000-0000-0000-000000000001",
        "email": "u@test.it",
        "nome": None,
        "cognome": None,
        "azienda": None,
        "telefono": "+393471234567",
        "codice_fiscale": None,
        "cf_verified_at": None,
        "job_position_id": 3,
        "job_position_altro": None,
        "job_positions": {"id": 3, "nome": "CTO / Direttore Tecnico", "slug": "cto"},
        "role": "cliente",
        "is_active": True,
        "created_at": "2026-07-14T08:00:00+00:00",
        "user_subscriptions": [{"scartato": True}],
    }

    def test_embed_rinominato_e_scarti(self):
        profile = user_service.profile_from_row(self.ROW)
        assert profile.job_position is not None
        assert profile.job_position.slug == "cto"
        assert profile.job_position_id == 3

    def test_senza_posizione(self):
        row = {**self.ROW, "job_position_id": None, "job_positions": None}
        profile = user_service.profile_from_row(row)
        assert profile.job_position is None
