"""Scheduler del catalogo (rimappatura dei bandi fusi, contratto DB bandi
§6.2): modalità `spenta` | `prova` | `attiva` normalizzata dalla config,
intervallo con un minimo, loop che sopravvive agli errori, avvio nel
lifespan solo con una modalità accesa e mai bloccante con un valore non
valido."""

import asyncio
import logging

import pytest

from app.services import catalogo_scheduler as cs
from app.services import rimappatura_fusi


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(chiave, valore)
    monkeypatch.delenv("RIMAPPATURA_FUSI_MODALITA", raising=False)
    monkeypatch.delenv("RIMAPPATURA_FUSI_INTERVALLO_MINUTI", raising=False)
    from app.core.config import Settings, get_settings

    # Il .env locale di chi sviluppa non deve poter falsare i casi «assente».
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def imposta(monkeypatch, **valori):
    from app.core.config import get_settings

    for chiave, valore in valori.items():
        monkeypatch.setenv(chiave, str(valore))
    get_settings.cache_clear()


@pytest.fixture
def passi(monkeypatch):
    """Sostituisce `rimappatura_fusi.passo`: registra il flag `prova`."""
    chiamate: list[bool] = []

    async def passo(primary, secondary, *, prova):
        chiamate.append(prova)
        return {"modalita": "prova" if prova else "attiva", "totali": {}, "coppie": []}

    monkeypatch.setattr(rimappatura_fusi, "passo", passo)
    return chiamate


class TestModalita:
    def test_default_spenta(self):
        from app.core.config import get_settings

        assert get_settings().rimappatura_fusi_modalita == "spenta"
        assert get_settings().rimappatura_fusi_intervallo_minuti == 60
        assert cs.modalita_rimappatura() == "spenta"

    @pytest.mark.parametrize(("valore", "attesa"),
                             [(" Prova ", "prova"), ("ATTIVA", "attiva"), ("boh", None)])
    def test_normalizzata_e_validata(self, monkeypatch, valore, attesa):
        imposta(monkeypatch, RIMAPPATURA_FUSI_MODALITA=valore)
        assert cs.modalita_rimappatura() == attesa

    @pytest.mark.parametrize(("modalita", "prova"), [("prova", True), ("attiva", False)])
    async def test_passo_nella_modalita(self, monkeypatch, passi, caplog, modalita, prova):
        imposta(monkeypatch, RIMAPPATURA_FUSI_MODALITA=modalita)
        with caplog.at_level(logging.INFO, logger="bandofit.catalogo_scheduler"):
            report = await cs.esegui_passo(object(), object())
        assert passi == [prova] and report["modalita"] == modalita
        assert f"rimappatura fusi ({modalita})" in caplog.text

    @pytest.mark.parametrize("valore", ["spenta", "boh"])
    async def test_spenta_o_non_valida_nessun_passo(self, monkeypatch, passi, valore):
        imposta(monkeypatch, RIMAPPATURA_FUSI_MODALITA=valore)
        assert await cs.esegui_passo(object(), object()) is None
        assert passi == []

    @pytest.mark.parametrize(("minuti", "secondi"),
                             [(60, 3600), (15, 900), (5, 300), (4, 3600), (0, 3600), ("x", 3600)])
    def test_intervallo_con_minimo(self, monkeypatch, minuti, secondi):
        # Sotto il minimo o non intero vale il default (60), non il minimo.
        imposta(monkeypatch, RIMAPPATURA_FUSI_INTERVALLO_MINUTI=minuti)
        assert cs.intervallo_secondi() == secondi

    @pytest.mark.parametrize(("errori", "scartate", "livello"),
                             [(0, 0, logging.INFO), (2, 0, logging.WARNING),
                              (0, 1, logging.WARNING), (1, 3, logging.WARNING)])
    async def test_riassunto_a_warning_con_errori_o_scartate(
        self, monkeypatch, caplog, errori, scartate, livello
    ):
        imposta(monkeypatch, RIMAPPATURA_FUSI_MODALITA="prova")

        async def passo(primary, secondary, *, prova):
            return {"modalita": "prova", "in_uso": 3, "fusi": 1, "scartate": scartate,
                    "errori": errori, "totali": {}, "coppie": []}

        monkeypatch.setattr(rimappatura_fusi, "passo", passo)
        with caplog.at_level(logging.INFO, logger="bandofit.catalogo_scheduler"):
            await cs.esegui_passo(object(), object())
        righe = [r for r in caplog.records if r.name == "bandofit.catalogo_scheduler"]
        assert [r.levelno for r in righe] == [livello]
        assert "rimappatura fusi (prova)" in righe[0].getMessage()


class TestLoop:
    async def test_sopravvive_agli_errori_e_attende_l_intervallo(self, monkeypatch, caplog):
        imposta(monkeypatch, RIMAPPATURA_FUSI_MODALITA="attiva",
                RIMAPPATURA_FUSI_INTERVALLO_MINUTI=10)
        giri: list[str] = []
        attese: list[int] = []

        async def esegui_passo(primary, secondary):
            giri.append("passo")
            if len(giri) == 1:
                raise RuntimeError("imprevisto")

        async def attendi(secondi):
            attese.append(secondi)
            if len(attese) == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(cs, "esegui_passo", esegui_passo)
        monkeypatch.setattr(cs.asyncio, "sleep", attendi)
        with caplog.at_level(logging.ERROR, logger="bandofit.catalogo_scheduler"):
            with pytest.raises(asyncio.CancelledError):
                await cs.run_forever(object(), object())
        assert giri == ["passo", "passo"] and attese == [600, 600]
        assert "errore inatteso" in caplog.text


class TestLifespan:
    async def _avvia(self, monkeypatch, modalita: str | None):
        valori = {"ALERT_SCHEDULER_ATTIVO": "false", "PARTENARIATI_ATTIVO": "false",
                  "ANTHROPIC_API_KEY": "", "REVOLUT_SECRET_KEY": "", "OPENAPI_API_KEY": ""}
        if modalita is not None:
            valori["RIMAPPATURA_FUSI_MODALITA"] = modalita
        imposta(monkeypatch, **valori)
        import app.main as main

        primario, secondario = object(), object()
        client = iter([primario, secondario])

        async def client_finto(settings):
            return next(client)

        partiti: list = []

        async def run_forever(primary, secondary):
            partiti.append((primary, secondary))
            await asyncio.Event().wait()

        monkeypatch.setattr(main, "create_primary_client", client_finto)
        monkeypatch.setattr(main, "create_secondary_client", client_finto)
        monkeypatch.setattr(cs, "run_forever", run_forever)
        async with main.lifespan(main.app):
            task = main.app.state.catalogo_task
            await asyncio.sleep(0)
            avviato = task is not None and not task.done()
        if task is not None:
            assert task.cancelled() or task.done()
        return avviato, partiti, (primario, secondario)

    @pytest.mark.parametrize("modalita", ["prova", "attiva"])
    async def test_parte_con_una_modalita_accesa(self, monkeypatch, modalita):
        avviato, partiti, client = await self._avvia(monkeypatch, modalita)
        assert avviato is True and partiti == [client]

    @pytest.mark.parametrize("modalita", [None, "spenta"])
    async def test_spenta_di_default(self, monkeypatch, modalita):
        avviato, partiti, _ = await self._avvia(monkeypatch, modalita)
        assert avviato is False and partiti == []

    async def test_valore_non_valido_non_blocca_l_avvio(self, monkeypatch, caplog):
        with caplog.at_level(logging.ERROR, logger="bandofit"):
            avviato, partiti, _ = await self._avvia(monkeypatch, "attivaa")
        assert avviato is False and partiti == []
        assert "RIMAPPATURA_FUSI_MODALITA non valida" in caplog.text
