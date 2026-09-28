"""Mappa unica degli errori RPC del modulo partenariati (docs/partenariati.md, T2).

Il frontend ramifica sui `code`: qui si blinda la tripla (status, code,
messaggio) dei detail del WP3 e il ripiego fail-closed sui detail ignoti."""

import logging
import re

import pytest
from postgrest.exceptions import APIError

from app.core.errors import AppError, UpstreamError
from app.services import partenariato_errori
from app.services.partenariato_errori import RPC_ERRORS, raise_from_rpc


def _api_error(detail, code="P0001") -> APIError:
    return APIError({"message": "errore sql", "code": code, "details": detail, "hint": None})


class TestDetailWp3:
    @pytest.mark.parametrize(
        ("detail", "status", "code"),
        [
            ("partenariato_cooldown", 429, "partenariato_cooldown"),
            ("ai_limite_utente", 429, "ai_limite_giornaliero"),
            ("ai_limite_owner", 429, "ai_limite_giornaliero"),
            ("ai_budget_esaurito", 429, "ai_sospesa_oggi"),
        ],
    )
    def test_detail_mappato(self, detail, status, code):
        originale = _api_error(detail)
        with pytest.raises(AppError) as exc:
            raise_from_rpc(originale)
        assert (exc.value.status_code, exc.value.code) == (status, code)
        # la causa resta agganciata per i log, ma il testo SQL non esce
        assert exc.value.__cause__ is originale
        assert "errore sql" not in exc.value.message

    def test_messaggi_del_contratto(self):
        assert RPC_ERRORS["ai_limite_utente"][2] == (
            "Hai raggiunto il numero di analisi di oggi: riprova domani"
        )
        assert RPC_ERRORS["ai_limite_owner"][2] == RPC_ERRORS["ai_limite_utente"][2]
        assert RPC_ERRORS["ai_budget_esaurito"][2] == (
            "L'analisi automatica è sospesa per oggi: riprova domani"
        )

    def test_spazi_attorno_al_detail_non_contano(self):
        with pytest.raises(AppError) as exc:
            raise_from_rpc(_api_error("  ai_budget_esaurito \n"))
        assert exc.value.code == "ai_sospesa_oggi"


class TestDetailIgnoti:
    @pytest.mark.parametrize("detail", ["detail_mai_visto", "", None])
    def test_diventa_upstream_con_log(self, detail, caplog):
        originale = _api_error(detail, code="XX000")
        with caplog.at_level(logging.ERROR, logger="bandofit.partenariati"):
            with pytest.raises(UpstreamError) as exc:
                raise_from_rpc(originale)
        assert exc.value.status_code == 502
        assert exc.value.code == "upstream_error"
        assert exc.value.__cause__ is originale
        assert "XX000" in caplog.text

    def test_una_voce_aggiunta_da_un_wp_successivo_vale_subito(self, monkeypatch):
        """La mappa è il punto di estensione dei WP4-WP10: basta aggiungere."""
        monkeypatch.setitem(
            partenariato_errori.RPC_ERRORS, "call_non_trovata", (404, "not_found", "Call non trovata")
        )
        with pytest.raises(AppError) as exc:
            raise_from_rpc(_api_error("call_non_trovata"))
        assert (exc.value.status_code, exc.value.code) == (404, "not_found")


class TestFormaDellaMappa:
    def test_ogni_voce_e_una_tripla_coerente(self):
        for detail, (status, code, messaggio) in RPC_ERRORS.items():
            assert re.fullmatch(r"[a-z0-9_]+", detail), detail
            assert 400 <= status <= 599, detail
            assert re.fullmatch(r"[a-z0-9_]+", code), detail
            # messaggi per l'utente: italiano, senza punto finale (il frontend
            # li compone), mai la parola «Famiglia»
            assert messaggio and not messaggio.endswith("."), detail
            assert "amiglia" not in messaggio, detail
