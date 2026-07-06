"""Test delle preferenze per utente: validazione contro le lookup,
denormalizzazione etichette e scrittura a diff."""

from types import SimpleNamespace

import pytest

from app.core.errors import BadRequestError
from app.schemas.preferences import PreferencesPayload
from app.services import preferences_service
from tests.test_openapi_service import FakePrimary, USER


@pytest.fixture(autouse=True)
def fake_lookups(monkeypatch):
    lookups = SimpleNamespace(
        regioni=[SimpleNamespace(id=9, nome="Lazio"), SimpleNamespace(id=16, nome="Puglia")],
        settori=[SimpleNamespace(id=3, nome="Agroalimentare")],
        beneficiari=[SimpleNamespace(id=2, nome="PMI")],
        codici_ateco=[SimpleNamespace(id=45, codice="02", descrizione="Silvicoltura")],
        tipologie_bando=[SimpleNamespace(id=1, nome="Contributo a fondo perduto")],
        modalita_erogazione=[SimpleNamespace(id=1, nome="Sportello")],
        programmi=[SimpleNamespace(id=7, nome="PNRR")],
    )

    async def get_lookups(secondary):
        return lookups

    monkeypatch.setattr("app.services.lookup_service.get_lookups", get_lookups)


class TestGet:
    async def test_vuote(self):
        primary = FakePrimary(selects={"user_preferences": []})
        prefs = await preferences_service.get_preferences(primary, USER["id"])
        assert prefs == PreferencesPayload()

    async def test_raggruppate_e_ordinate(self):
        primary = FakePrimary(
            selects={
                "user_preferences": [
                    {"facet": "regioni", "ref_id": 16},
                    {"facet": "regioni", "ref_id": 9},
                    {"facet": "codici_ateco", "ref_id": 45},
                ]
            }
        )
        prefs = await preferences_service.get_preferences(primary, USER["id"])
        assert prefs.regioni == [9, 16]
        assert prefs.codici_ateco == [45]
        assert prefs.settori == []


class TestSave:
    async def test_id_sconosciuto_400(self):
        primary = FakePrimary(selects={"user_preferences": []})
        with pytest.raises(BadRequestError):
            await preferences_service.save_preferences(
                primary, None, USER["id"], PreferencesPayload(regioni=[999])
            )
        assert primary.ops_for("user_preferences", "insert") == []

    async def test_diff_inserisce_e_cancella(self):
        primary = FakePrimary(
            selects={
                "user_preferences": [
                    {"id": "p1", "facet": "regioni", "ref_id": 9},
                    {"id": "p2", "facet": "settori", "ref_id": 3},
                ]
            }
        )
        await preferences_service.save_preferences(
            primary, None, USER["id"],
            PreferencesPayload(regioni=[9, 16], codici_ateco=[45]),  # settori rimossi
        )
        inserts = primary.ops_for("user_preferences", "insert")[0]
        chiavi = {(row["facet"], row["ref_id"]) for row in inserts}
        assert chiavi == {("regioni", 16), ("codici_ateco", 45)}  # il 9 esisteva già
        assert primary.ops_for("user_preferences", "delete")  # p2 rimossa

    async def test_nessuna_modifica_nessuna_scrittura(self):
        primary = FakePrimary(
            selects={"user_preferences": [{"id": "p1", "facet": "regioni", "ref_id": 9}]}
        )
        await preferences_service.save_preferences(
            primary, None, USER["id"], PreferencesPayload(regioni=[9])
        )
        assert primary.ops_for("user_preferences", "insert") == []
        assert primary.ops_for("user_preferences", "delete") == []

    async def test_etichette_denormalizzate(self):
        primary = FakePrimary(selects={"user_preferences": []})
        await preferences_service.save_preferences(
            primary, None, USER["id"],
            PreferencesPayload(codici_ateco=[45], tipologie=[1]),
        )
        inserts = primary.ops_for("user_preferences", "insert")[0]
        labels = {row["facet"]: row["label"] for row in inserts}
        assert labels["codici_ateco"] == "02 — Silvicoltura"
        assert labels["tipologie"] == "Contributo a fondo perduto"

    async def test_duplicati_nel_payload_deduplicati(self):
        primary = FakePrimary(selects={"user_preferences": []})
        await preferences_service.save_preferences(
            primary, None, USER["id"], PreferencesPayload(regioni=[9, 9, 9])
        )
        inserts = primary.ops_for("user_preferences", "insert")[0]
        assert len(inserts) == 1
