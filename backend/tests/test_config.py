"""Controlli d'avvio sulle Settings.

Unico file che prende Settings come oggetto del test invece che come dipendenza
da stubbare: qui il comportamento È la configurazione. Le Settings si
costruiscono con `_env_file=None` perché un .env presente accanto al backend
altrimenti fornirebbe i valori che il test vuole assenti.
"""

import traceback

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

_OBBLIGATORIE = {
    "primary_supabase_url": "https://dummy.supabase.co",
    "primary_supabase_service_role_key": "k",
    "secondary_supabase_url": "https://d2.supabase.co",
    "secondary_supabase_anon_key": "k",
}


def _costruisci(**override) -> Settings:
    return Settings(_env_file=None, **{**_OBBLIGATORIE, **override})


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Le variabili della macchina non devono poter falsare il verdetto."""
    for key in ("ENV", "RATE_LIMIT_PEPPER"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_in_produzione_il_pepper_vuoto_impedisce_l_avvio():
    with pytest.raises(ValidationError) as exc:
        _costruisci(env="production", rate_limit_pepper="")
    # Il messaggio deve dire cosa fare: chi lo legge sta guardando un container
    # che non parte, non il sorgente.
    assert "RATE_LIMIT_PEPPER" in str(exc.value)
    assert "openssl rand -hex 32" in str(exc.value)


def test_in_produzione_il_pepper_di_soli_spazi_non_conta():
    with pytest.raises(ValidationError):
        _costruisci(env="production", rate_limit_pepper="   ")


def test_in_produzione_con_il_pepper_si_parte():
    settings = _costruisci(env="production", rate_limit_pepper="a" * 64)
    assert settings.rate_limit_pepper == "a" * 64


def test_in_sviluppo_il_pepper_vuoto_resta_lecito():
    """Il guard non deve costare l'ergonomia dello sviluppo locale: senza questo,
    `docker compose` in dev e l'intera suite pretenderebbero un segreto."""
    settings = _costruisci(rate_limit_pepper="")
    assert settings.env == "development"
    assert settings.rate_limit_pepper == ""


def test_env_si_confronta_senza_badare_a_maiuscole_e_spazi():
    """`ENV=Production` deve valere quanto `production`: un guard che non scatta
    per una maiuscola tace proprio quando serve, e nessuno se ne accorge."""
    for valore in ("Production", "PRODUCTION", " production "):
        with pytest.raises(ValidationError):
            _costruisci(env=valore, rate_limit_pepper="")


def test_il_default_di_env_non_e_produzione():
    """Blinda il presupposto del guard: se il default diventasse «production»,
    ogni sviluppatore senza pepper si troverebbe il backend che non parte."""
    assert _costruisci().env == "development"


# --- get_settings(): i motivi sì, i valori no ---------------------------------
#
# Settings si costruisce all'import di app.main, quindi una configurazione
# sbagliata si legge nei log del container. Il messaggio grezzo di pydantic
# porta con sé `input_value`, un estratto delle variabili lette.
#
# L'asserzione che conta è `"input_value" not in ...`, non le sentinelle:
# pydantic tronca quell'estratto a testa e coda, quindi QUALE segreto trapeli
# dipende dall'ordine dei campi di Settings. Un test costruito solo sulle
# sentinelle diventerebbe verde da solo il giorno in cui qualcuno aggiunge un
# campo in fondo alla classe — verde col leak intatto. Le sentinelle restano
# perché dicono a cosa serve il test, ma i denti sono nel marcatore.

_SEGRETI = {
    "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "SENTINELLA-SERVICE-ROLE",
    "SECONDARY_SUPABASE_ANON_KEY": "SENTINELLA-ANON",
    "SMTP_PASSWORD": "SENTINELLA-SMTP",
    "ANTHROPIC_API_KEY": "SENTINELLA-ANTHROPIC",
    "OPENAPI_API_KEY": "SENTINELLA-OPENAPI",
    "RATE_LIMIT_PEPPER": "SENTINELLA-PEPPER",
}


@pytest.fixture
def _env_con_segreti(monkeypatch, tmp_path):
    # Due sorgenti vanno neutralizzate, non una. Il `.env`: get_settings()
    # costruisce Settings() senza _env_file=None e lo legge dalla CWD, quindi su
    # una macchina che ne ha uno completo il test del campo mancante non vedrebbe
    # mancare nulla — si lavora da una directory vuota. E l'ambiente della shell:
    # un `export AI_CHECK_MODEL=...`, che docs/deploy.md documenta, entrerebbe in
    # Settings e sposterebbe il contenuto dell'estratto troncato.
    monkeypatch.chdir(tmp_path)
    for campo in Settings.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)
    monkeypatch.setenv("PRIMARY_SUPABASE_URL", "https://dummy.supabase.co")
    monkeypatch.setenv("SECONDARY_SUPABASE_URL", "https://d2.supabase.co")
    for key, value in _SEGRETI.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_un_campo_obbligatorio_mancante_non_stampa_il_env(_env_con_segreti):
    _env_con_segreti.delenv("PRIMARY_SUPABASE_URL")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError) as exc:
        get_settings()

    # Uguaglianza, non contenimento: dice in un colpo solo che il motivo c'è e
    # che non c'è nient'altro, senza dipendere da cosa capiti nella finestra di
    # troncamento di pydantic.
    assert str(exc.value) == "Configurazione non valida — primary_supabase_url: Field required"


def test_il_pepper_mancante_in_produzione_non_stampa_il_env(_env_con_segreti):
    _env_con_segreti.setenv("ENV", "production")
    _env_con_segreti.setenv("RATE_LIMIT_PEPPER", "")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError) as exc:
        get_settings()

    messaggio = str(exc.value)
    assert "input_value" not in messaggio, "il dump di pydantic è tornato nel messaggio"
    for sentinella in _SEGRETI.values():
        assert sentinella not in messaggio, f"{sentinella} finita nel messaggio d'errore"
    assert "RATE_LIMIT_PEPPER" in messaggio


def test_il_traceback_del_boot_non_contiene_segreti(_env_con_segreti):
    """Difende il `from None`, che è la metà indipendente della difesa.

    Con `from exc` il messaggio resterebbe pulito — e i test sul messaggio verdi
    — ma il traceback ristamperebbe sotto la ValidationError originale col suo
    dump. Nei log del container si legge il traceback, non `str(exc)`: si
    asserisce quindi sulla cosa vera, non su un suo surrogato.
    """
    _env_con_segreti.delenv("PRIMARY_SUPABASE_URL")
    get_settings.cache_clear()

    try:
        get_settings()
    except RuntimeError as exc:
        traccia = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        pytest.fail("get_settings() doveva sollevare")

    assert "input_value" not in traccia, "il dump di pydantic è nel traceback"
    assert "direct cause" not in traccia, "l'eccezione originale è ricomparsa incatenata"
    for sentinella in _SEGRETI.values():
        assert sentinella not in traccia, f"{sentinella} finita nel traceback"


def test_la_configurazione_valida_non_solleva(_env_con_segreti):
    _env_con_segreti.setenv("ENV", "production")
    get_settings.cache_clear()
    assert get_settings().rate_limit_pepper == "SENTINELLA-PEPPER"
