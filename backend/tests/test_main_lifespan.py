"""Avvio degli scheduler nel lifespan: il failsafe del bilancio ufficiale parte
solo a storico dei bilanci acceso, riceve il client primario e quello openapi e
si cancella allo spegnimento come gli altri task."""

import asyncio

import pytest

from app.services import bilancio_ufficiale_scheduler as bus


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "ALERT_SCHEDULER_ATTIVO": "false",
        "PARTENARIATI_ATTIVO": "false",
        "RIMAPPATURA_FUSI_MODALITA": "spenta",
        "ANTHROPIC_API_KEY": "",
        "REVOLUT_SECRET_KEY": "",
        "OPENAPI_API_KEY": "",
    }.items():
        monkeypatch.setenv(chiave, valore)
    monkeypatch.delenv("BILANCI_STORICO_ATTIVO", raising=False)
    from app.core.config import Settings, get_settings

    # Il .env locale di chi sviluppa non deve poter falsare il caso «assente».
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _avvia(monkeypatch, storico: str | None):
    from app.core.config import get_settings

    if storico is not None:
        monkeypatch.setenv("BILANCI_STORICO_ATTIVO", storico)
    get_settings.cache_clear()
    import app.main as main

    primario, secondario = object(), object()
    client = iter([primario, secondario])

    async def client_finto(settings):
        return next(client)

    partiti: list = []

    async def run_forever(primary, openapi):
        partiti.append((primary, openapi))
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "create_primary_client", client_finto)
    monkeypatch.setattr(main, "create_secondary_client", client_finto)
    monkeypatch.setattr(bus, "run_forever", run_forever)
    async with main.lifespan(main.app):
        task = main.app.state.bilancio_ufficiale_task
        openapi = main.app.state.openapi
        await asyncio.sleep(0)
        avviato = task is not None and not task.done()
    return avviato, task, partiti, (primario, openapi)


async def test_parte_a_storico_acceso_e_si_cancella_allo_spegnimento(monkeypatch):
    avviato, task, partiti, attesi = await _avvia(monkeypatch, "true")
    assert avviato is True
    assert partiti == [attesi]
    assert task.cancelled()


@pytest.mark.parametrize("storico", [None, "false"])
async def test_non_parte_a_storico_spento(monkeypatch, storico):
    avviato, task, partiti, _ = await _avvia(monkeypatch, storico)
    assert avviato is False and task is None and partiti == []
