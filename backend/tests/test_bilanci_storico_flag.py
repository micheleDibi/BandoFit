"""Interruttore dello storico dei bilanci (BILANCI_STORICO_ATTIVO).

- default spento, si accende da ambiente;
- `GET /me`: `funzioni.bilanci_storico` segue il flag;
- le 5 rotte a pagamento del router aziendale (storico IT-advanced e bilancio
  ufficiale): a flag spento 404 `not_found` anche senza token, senza arrivare
  al servizio né alle altre dipendenze (utente, azienda attiva, openapi); a
  flag acceso si arriva al servizio. `GET /me/company/bilanci`, gratuito, non
  cambia.
L'anteprima dell'import a storico spento sta in test_openapi_service.py
(TestStoricoSpento)."""

import os

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany, require_bilanci_storico_attivo
from app.api.routers import company
from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotFoundError, register_exception_handlers
from app.schemas.user import FunzioniOut
from app.services import bilanci_service, bilancio_ufficiale_service, user_service
from tests.test_partenariati_flag import (  # noqa: F401  me_senza_famiglia: fixture
    _PROFILO,
    _PrimarioFinto,
    me_senza_famiglia,
)

_OBBLIGATORIE = {
    "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
    "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
    "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
    "SECONDARY_SUPABASE_ANON_KEY": "k",
}
RICHIESTA = "50000000-0000-0000-0000-000000000001"
BASE = "/api/v1/me/company/bilanci"

# (metodo, percorso, corpo, modulo e funzione del servizio)
ROTTE = [
    ("POST", "/recupera", None, bilanci_service, "recupera_bilanci"),
    ("GET", "/ufficiale", None, bilancio_ufficiale_service, "lista"),
    ("POST", "/ufficiale", {"anno": 2024}, bilancio_ufficiale_service, "richiedi"),
    ("GET", f"/ufficiale/{RICHIESTA}", None, bilancio_ufficiale_service, "dettaglio"),
    ("GET", f"/ufficiale/{RICHIESTA}/pdf", None, bilancio_ufficiale_service, "scarica_pdf"),
]
_ID_ROTTE = [f"{m} {p.replace(RICHIESTA, '{id}')}" for m, p, *_ in ROTTE]


@pytest.fixture
def storico(monkeypatch):
    """Imposta il flag via ambiente (vince sul .env) e azzera la cache."""

    def imposta(valore: bool) -> None:
        for chiave, v in _OBBLIGATORIE.items():
            monkeypatch.setenv(chiave, v)
        monkeypatch.setenv("BILANCI_STORICO_ATTIVO", "true" if valore else "false")
        get_settings.cache_clear()

    yield imposta
    get_settings.cache_clear()


@pytest.fixture
def servizi(monkeypatch) -> list[str]:
    """Spie sui servizi delle rotte: registrano la chiamata e rispondono 418,
    così il test vede se la richiesta è arrivata al servizio."""
    chiamati: list[str] = []

    def spia(nome: str):
        async def finto(*_a, **_k):
            chiamati.append(nome)
            raise AppError(418, "servizio_raggiunto", nome)

        return finto

    for *_, modulo, nome in ROTTE:
        monkeypatch.setattr(modulo, nome, spia(nome))
    monkeypatch.setattr(bilanci_service, "get_bilanci", spia("get_bilanci"))
    return chiamati


def _app(dipendenze: list[str] | None = None, *, autenticato: bool = True) -> FastAPI:
    """Mini-app col router aziendale. `dipendenze` registra le dipendenze
    risolte; senza `autenticato` utente e azienda attiva restano quelli veri
    (niente token = 401)."""
    risolte = dipendenze if dipendenze is not None else []

    def registra(nome: str, valore):
        def dipendenza():
            risolte.append(nome)
            return valore

        return dipendenza

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(company.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_primary] = registra("primary", object())
    app.dependency_overrides[deps.get_secondary] = registra("secondary", object())
    app.dependency_overrides[deps.get_openapi] = registra("openapi", object())
    if autenticato:
        app.dependency_overrides[deps.get_current_user] = registra("utente", {"id": "u1"})
        app.dependency_overrides[deps.active_company] = registra(
            "azienda", ActiveCompany(company_id="c1", owner_id="u1", editable=True)
        )
    return app


async def _chiama(app: FastAPI, metodo: str, percorso: str, corpo=None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(metodo, BASE + percorso, json=corpo)


# --- Settings --------------------------------------------------------------------


def test_default_spento(monkeypatch):
    # le variabili della macchina non devono falsare il verdetto
    for chiave in list(os.environ):
        if chiave.upper() == "BILANCI_STORICO_ATTIVO":
            monkeypatch.delenv(chiave)
    s = Settings(
        _env_file=None,
        primary_supabase_url="https://dummy.supabase.co",
        primary_supabase_service_role_key="k",
        secondary_supabase_url="https://d2.supabase.co",
        secondary_supabase_anon_key="k",
    )
    assert s.bilanci_storico_attivo is False


def test_si_accende_da_ambiente(storico):
    storico(True)
    assert get_settings().bilanci_storico_attivo is True
    storico(False)
    assert get_settings().bilanci_storico_attivo is False


async def test_dipendenza_diretta(storico):
    storico(False)
    with pytest.raises(NotFoundError) as exc:
        await require_bilanci_storico_attivo()
    assert exc.value.message == "Risorsa non trovata"
    storico(True)
    assert await require_bilanci_storico_attivo() is None


# --- GET /me: funzioni --------------------------------------------------------------


class TestMeFunzioni:
    @pytest.mark.usefixtures("me_senza_famiglia")
    @pytest.mark.parametrize("attivo", [True, False])
    async def test_segue_il_flag(self, storico, attivo):
        storico(attivo)
        me = await user_service.get_me(_PrimarioFinto(_PROFILO), _PROFILO["id"])
        assert me.funzioni.bilanci_storico is attivo
        assert me.model_dump(mode="json")["funzioni"]["bilanci_storico"] is attivo

    def test_default_spento(self):
        assert FunzioniOut().bilanci_storico is False


# --- Rotte a pagamento del router aziendale --------------------------------------


class TestRotte:
    @pytest.mark.parametrize(("metodo", "percorso", "corpo", "_m", "_n"), ROTTE, ids=_ID_ROTTE)
    async def test_spento_404_senza_servizio(
        self, storico, servizi, metodo, percorso, corpo, _m, _n
    ):
        storico(False)
        risolte: list[str] = []
        resp = await _chiama(_app(risolte), metodo, percorso, corpo)
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Risorsa non trovata"}}
        # niente servizio (lock, quota, registro consumi, follower) né dipendenze
        assert servizi == []
        assert risolte == []

    @pytest.mark.parametrize(("metodo", "percorso", "corpo", "_m", "_n"), ROTTE, ids=_ID_ROTTE)
    async def test_spento_404_senza_token(
        self, storico, servizi, metodo, percorso, corpo, _m, _n
    ):
        storico(False)
        resp = await _chiama(_app(autenticato=False), metodo, percorso, corpo)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
        assert servizi == []

    @pytest.mark.parametrize(("metodo", "percorso", "corpo", "_m", "nome"), ROTTE, ids=_ID_ROTTE)
    async def test_acceso_arriva_al_servizio(
        self, storico, servizi, metodo, percorso, corpo, _m, nome
    ):
        storico(True)
        resp = await _chiama(_app(), metodo, percorso, corpo)
        assert resp.status_code == 418
        assert resp.json()["error"]["code"] == "servizio_raggiunto"
        assert servizi == [nome]

    @pytest.mark.parametrize(("metodo", "percorso", "corpo", "_m", "_n"), ROTTE, ids=_ID_ROTTE)
    async def test_acceso_senza_token_401(
        self, storico, servizi, metodo, percorso, corpo, _m, _n
    ):
        """Prova dell'ordine: col flag acceso la stessa richiesta senza token
        arriva all'autenticazione, quindi a flag spento il 404 veniva prima."""
        storico(True)
        resp = await _chiama(_app(autenticato=False), metodo, percorso, corpo)
        assert resp.status_code == 401
        assert servizi == []

    async def test_spento_corpo_non_valido_404(self, storico, servizi):
        """Corpo JSON valido ma fuori schema: il flag viene prima della validazione."""
        storico(False)
        resp = await _chiama(_app(), "POST", "/ufficiale", {"anno": "boh"})
        assert resp.status_code == 404
        assert servizi == []

    @pytest.mark.parametrize("attivo", [True, False])
    async def test_get_bilanci_non_cambia(self, storico, servizi, attivo):
        storico(attivo)
        resp = await _chiama(_app(), "GET", "")
        assert resp.status_code == 418
        assert servizi == ["get_bilanci"]
