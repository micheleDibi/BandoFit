"""Test dei dati aziendali: validatori pydantic e risoluzione lookup."""

import pytest
from pydantic import ValidationError

from app.core.errors import BadRequestError
from app.schemas.bando import LookupsOut
from app.schemas.company import CompanyIn
from app.services.company_service import resolve_lookups

VALID = {
    "ragione_sociale": "ACME Srl",
    "partita_iva": "01234567890",
}

LOOKUPS = LookupsOut(
    regioni=[{"id": 10, "nome": "Lombardia"}],
    settori=[{"id": 5, "nome": "Automotive"}],
    beneficiari=[{"id": 7, "nome": "PMI"}, {"id": 9, "nome": "Organismi di formazione"}],
    codici_ateco=[{"id": 3, "codice": "49", "descrizione": "Trasporto terrestre"}],
    tipologie_bando=[],
    modalita_erogazione=[],
    programmi=[],
)


class TestValidators:
    def test_payload_minimo_valido(self):
        company = CompanyIn(**VALID)
        assert company.partita_iva == "01234567890"

    @pytest.mark.parametrize(
        "piva", ["0123456789", "012345678901", "ABCDEFGHILM", "", "12345 6789"]
    )
    def test_partita_iva_non_valida(self, piva):
        with pytest.raises(ValidationError):
            CompanyIn(**{**VALID, "partita_iva": piva})

    def test_partita_iva_con_prefisso_it_e_spazi(self):
        company = CompanyIn(**{**VALID, "partita_iva": "IT 01234567890"})
        assert company.partita_iva == "01234567890"

    @pytest.mark.parametrize("cap,ok", [("70125", True), ("7012", False), ("ABCDE", False)])
    def test_cap(self, cap, ok):
        if ok:
            assert CompanyIn(**{**VALID, "cap": cap}).cap == cap
        else:
            with pytest.raises(ValidationError):
                CompanyIn(**{**VALID, "cap": cap})

    def test_cap_vuoto_diventa_none(self):
        assert CompanyIn(**{**VALID, "cap": "  "}).cap is None

    @pytest.mark.parametrize(
        "pec,ok",
        [("azienda@pec.it", True), ("non-una-mail", False), (None, True), ("", True)],
    )
    def test_pec(self, pec, ok):
        if ok:
            CompanyIn(**{**VALID, "pec": pec})
        else:
            with pytest.raises(ValidationError):
                CompanyIn(**{**VALID, "pec": pec})

    def test_anno_fondazione_fuori_range(self):
        with pytest.raises(ValidationError):
            CompanyIn(**{**VALID, "anno_fondazione": 1700})

    def test_ragione_sociale_obbligatoria(self):
        with pytest.raises(ValidationError):
            CompanyIn(partita_iva="01234567890", ragione_sociale="")


class TestResolveLookups:
    def test_id_validi_denormalizzati(self):
        data = CompanyIn(**{**VALID, "ateco_id": 3, "settore_id": 5, "regione_id": 10})
        payload = resolve_lookups(data, LOOKUPS)
        assert payload["ateco_codice"] == "49"
        assert payload["ateco_descrizione"] == "Trasporto terrestre"
        assert payload["settore_nome"] == "Automotive"
        assert payload["regione_nome"] == "Lombardia"

    @pytest.mark.parametrize(
        "field,value",
        [("ateco_id", 999), ("settore_id", 999), ("regione_id", 999)],
    )
    def test_id_sconosciuto_400(self, field, value):
        data = CompanyIn(**{**VALID, field: value})
        with pytest.raises(BadRequestError):
            resolve_lookups(data, LOOKUPS)

    def test_senza_lookup_copie_null(self):
        payload = resolve_lookups(CompanyIn(**VALID), LOOKUPS)
        assert payload["ateco_codice"] is None
        assert payload["settore_nome"] is None
        assert payload["regione_nome"] is None
        assert payload["beneficiari"] == []

    def test_beneficiari_denormalizzati(self):
        # Multi-valore: in colonna finisce [{id, nome}], non la lista di id.
        data = CompanyIn(**{**VALID, "beneficiari_ids": [9, 7]})
        payload = resolve_lookups(data, LOOKUPS)
        assert payload["beneficiari"] == [
            {"id": 9, "nome": "Organismi di formazione"},
            {"id": 7, "nome": "PMI"},
        ]
        assert "beneficiari_ids" not in payload  # non è una colonna

    def test_beneficiari_duplicati_deduplicati(self):
        data = CompanyIn(**{**VALID, "beneficiari_ids": [7, 7, 9]})
        assert data.beneficiari_ids == [7, 9]

    def test_beneficiario_sconosciuto_400(self):
        data = CompanyIn(**{**VALID, "beneficiari_ids": [999]})
        with pytest.raises(BadRequestError):
            resolve_lookups(data, LOOKUPS)
