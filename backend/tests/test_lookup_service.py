"""Lookup delle faccette con cache in-process (`services/lookup_service.py`):
cache servita entro il TTL; un errore di contratto del catalogo (42703, 42501,
PGRST2xx) non dà 5xx — cache scaduta se c'è, altrimenti liste vuote — e il
nuovo tentativo si rinvia di un minuto; guasti veri e timeout risalgono come
prima. Catalogo finto."""

import logging
import time
from types import SimpleNamespace

import httpx
import pytest
from postgrest.exceptions import APIError

from app.api.deps import ActiveCompany
from app.core.errors import CatalogoNonDisponibileError
from app.schemas.company import CompanyIn
from app.schemas.preferences import PreferencesPayload
from app.services import company_service, openapi_service, preferences_service
from app.services import lookup_service as ls
from tests.test_company_service import VALID
from tests.test_openapi_service import PIVA, USER, FakePrimary, fake_openapi

TABELLE = ("regioni", "settori", "beneficiari", "codici_ateco", "tipologie_bando",
           "modalita_erogazione", "programmi")


def errore(codice: str) -> APIError:
    return APIError({"message": "messaggio del catalogo", "code": codice, "hint": None,
                     "details": None})


class FakeSecondary:
    def __init__(self, guasto: Exception | None = None):
        self.guasto = guasto
        self.letture: list[str] = []

    def table(self, nome):
        sec = self

        class _Q:
            def select(self, *a, **k):
                return self

            def order(self, *a, **k):
                return self

            async def execute(self):
                sec.letture.append(nome)
                if sec.guasto is not None:
                    raise sec.guasto
                if nome == "codici_ateco":
                    return SimpleNamespace(data=[{"id": 1, "codice": "62", "descrizione": "Sw"}])
                return SimpleNamespace(data=[{"id": 1, "nome": nome}])

        return _Q()


@pytest.fixture(autouse=True)
def cache_pulita(monkeypatch):
    monkeypatch.setattr(ls, "_cache", None)
    monkeypatch.setattr(ls, "_cache_at", 0.0)
    monkeypatch.setattr(ls, "_errore_at", None)


def cache_scaduta(monkeypatch, lookups) -> None:
    monkeypatch.setattr(ls, "_cache", lookups)
    monkeypatch.setattr(ls, "_cache_at", time.monotonic() - ls._CACHE_TTL_SECONDS - 1)


class TestCache:
    async def test_prima_lettura_riempie_la_cache(self):
        secondary = FakeSecondary()
        lookups = await ls.get_lookups(secondary)
        assert sorted(secondary.letture) == sorted(TABELLE)
        assert lookups.regioni[0].nome == "regioni" and lookups.codici_ateco[0].codice == "62"
        assert await ls.get_lookups(secondary) is lookups
        assert len(secondary.letture) == 7  # nessuna nuova lettura entro il TTL

    async def test_cache_scaduta_si_rilegge(self, monkeypatch):
        vecchi = await ls.get_lookups(FakeSecondary())
        cache_scaduta(monkeypatch, vecchi)
        secondary = FakeSecondary()
        nuovi = await ls.get_lookups(secondary)
        assert nuovi is not vecchi and len(secondary.letture) == 7


class TestErroreDiContratto:
    @pytest.mark.parametrize("codice", ["42703", "42501", "PGRST205", "PGRST200"])
    async def test_riconosciuto(self, codice):
        assert ls.errore_di_contratto(errore(codice)) is True

    @pytest.mark.parametrize("guasto", [errore("57014"), errore("PGRST000"), errore("PGRST103"),
                                        httpx.ReadTimeout("t"), RuntimeError("x")])
    async def test_guasti_veri_esclusi(self, guasto):
        assert ls.errore_di_contratto(guasto) is False

    @pytest.mark.parametrize("codice", ["42703", "42501", "PGRST205"])
    async def test_cache_scaduta_servita_e_nuovo_tentativo_rinviato(self, monkeypatch, caplog,
                                                                   codice):
        vecchi = await ls.get_lookups(FakeSecondary())
        cache_scaduta(monkeypatch, vecchi)
        secondary = FakeSecondary(guasto=errore(codice))
        with caplog.at_level(logging.WARNING, logger="bandofit.lookups"):
            assert await ls.get_lookups(secondary) is vecchi
        [record] = caplog.records
        assert record.levelno == logging.WARNING and codice in record.getMessage()
        assert "messaggio del catalogo" not in caplog.text
        # entro il minuto nessuna nuova lettura: sempre la cache scaduta
        letture = len(secondary.letture)
        assert await ls.get_lookups(secondary) is vecchi
        assert len(secondary.letture) == letture
        # passato il minuto si ritenta, e se il catalogo risponde la cache si rinnova
        monkeypatch.setattr(ls, "_errore_at", time.monotonic() - ls._RINVIO_ERRORE_SECONDS - 1)
        secondary.guasto = None
        nuovi = await ls.get_lookups(secondary)
        assert nuovi is not vecchi and len(secondary.letture) > letture
        assert ls._errore_at is None

    async def test_cache_vuota_chi_degrada_riceve_liste_vuote_con_log_error(self, monkeypatch,
                                                                            caplog):
        secondary = FakeSecondary(guasto=errore("42703"))
        with caplog.at_level(logging.ERROR, logger="bandofit.lookups"):
            lookups = await ls.get_lookups(secondary, degrada=True)
        assert lookups.regioni == [] and lookups.codici_ateco == [] and lookups.programmi == []
        [record] = caplog.records
        assert record.levelno == logging.ERROR and "42703" in record.getMessage()
        # la lista vuota non entra in cache, ma nel minuto non si rilegge
        # (e non si rilogga)
        assert ls._cache is None
        letture = len(secondary.letture)
        assert (await ls.get_lookups(secondary, degrada=True)).regioni == []
        assert len(secondary.letture) == letture and len(caplog.records) == 1
        monkeypatch.setattr(ls, "_errore_at", time.monotonic() - ls._RINVIO_ERRORE_SECONDS - 1)
        secondary.guasto = None
        assert (await ls.get_lookups(secondary, degrada=True)).regioni[0].nome == "regioni"

    @pytest.mark.parametrize("codice", ["42703", "42501", "PGRST205"])
    async def test_cache_vuota_senza_degrado_503_ritentabile(self, monkeypatch, caplog, codice):
        # Chi scrive o paga non procede con dati parziali: errore, non liste vuote.
        secondary = FakeSecondary(guasto=errore(codice))
        with caplog.at_level(logging.ERROR, logger="bandofit.lookups"):
            with pytest.raises(CatalogoNonDisponibileError) as exc:
                await ls.get_lookups(secondary)
        assert exc.value.status_code == 503 and exc.value.code == "catalogo_non_disponibile"
        [record] = caplog.records
        assert record.levelno == logging.ERROR and codice in record.getMessage()
        assert "messaggio del catalogo" not in caplog.text
        # nel minuto: stesso esito senza rilettura; chi degrada ha le liste vuote
        letture = len(secondary.letture)
        with pytest.raises(CatalogoNonDisponibileError):
            await ls.get_lookups(secondary)
        assert (await ls.get_lookups(secondary, degrada=True)).regioni == []
        assert len(secondary.letture) == letture and len(caplog.records) == 1
        # passato il minuto si ritenta e il catalogo torna a servire tutti
        monkeypatch.setattr(ls, "_errore_at", time.monotonic() - ls._RINVIO_ERRORE_SECONDS - 1)
        secondary.guasto = None
        assert (await ls.get_lookups(secondary)).regioni[0].nome == "regioni"

    async def test_cache_scaduta_servita_anche_a_chi_non_degrada(self, monkeypatch):
        vecchi = await ls.get_lookups(FakeSecondary())
        cache_scaduta(monkeypatch, vecchi)
        secondary = FakeSecondary(guasto=errore("42703"))
        assert await ls.get_lookups(secondary) is vecchi
        assert await ls.get_lookups(secondary, degrada=True) is vecchi

    async def test_in_degrado(self, monkeypatch):
        assert ls.in_degrado() is False
        secondary = FakeSecondary(guasto=errore("42703"))
        with pytest.raises(CatalogoNonDisponibileError):
            await ls.get_lookups(secondary)
        assert ls.in_degrado() is True
        monkeypatch.setattr(ls, "_errore_at", time.monotonic() - ls._RINVIO_ERRORE_SECONDS - 1)
        assert ls.in_degrado() is True  # finché una lettura non riesce
        secondary.guasto = None
        await ls.get_lookups(secondary)
        assert ls.in_degrado() is False


class PrimarioIntoccabile:
    """DB primario che non deve essere né letto né scritto."""

    def table(self, nome):
        raise AssertionError(f"accesso al primario non atteso: {nome}")


def _attivo() -> ActiveCompany:
    return ActiveCompany(company_id=None, owner_id=USER["id"], editable=True, is_multi=False)


@pytest.fixture
def ambiente_import(monkeypatch):
    """Impostazioni minime per `preview_import`, senza `.env`."""
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestChiScriveOPagaSiFerma:
    """A cache vuota con un errore di contratto, i percorsi che scrivono o
    pagano rispondono 503 prima di toccare il primario o il fornitore."""

    async def test_preferenze_non_scrivono(self):
        with pytest.raises(CatalogoNonDisponibileError):
            await preferences_service.save_preferences(
                PrimarioIntoccabile(), FakeSecondary(guasto=errore("42703")), USER["id"],
                _attivo(), PreferencesPayload(regioni=[1]),
            )

    async def test_dati_aziendali_non_scrivono(self):
        with pytest.raises(CatalogoNonDisponibileError):
            await company_service.upsert_company(
                PrimarioIntoccabile(), FakeSecondary(guasto=errore("42703")), _attivo(),
                CompanyIn(**VALID),
            )

    async def test_anteprima_import_non_paga(self, ambiente_import):
        openapi = fake_openapi()
        with pytest.raises(CatalogoNonDisponibileError):
            await openapi_service.preview_import(
                FakePrimary(), FakeSecondary(guasto=errore("42703")), openapi, _attivo(), PIVA
            )
        assert openapi.calls == {"it_full": [], "it_advanced": []}

    @pytest.mark.parametrize("guasto", [errore("57014"), errore("PGRST000"),
                                        httpx.ReadTimeout("timeout")],
                             ids=["timeout_sql", "connessione", "timeout_http"])
    async def test_guasto_vero_risale_come_prima(self, monkeypatch, guasto):
        vecchi = await ls.get_lookups(FakeSecondary())
        cache_scaduta(monkeypatch, vecchi)
        with pytest.raises(type(guasto)):
            await ls.get_lookups(FakeSecondary(guasto=guasto))
        assert ls._errore_at is None and ls._cache is vecchi
