"""Test del failsafe del bilancio ufficiale: ogni giro fa avanzare le
richieste aperte con `avanza` del servizio (claim a DB compreso), non
solleva mai e nei log non scrive P.IVA.

Primario e provider finti di test_bilancio_ufficiale_service (a stato, con
le RPC della 0033). Nessuna rete."""

import asyncio
import logging

import pytest

from app.services import bilancio_ufficiale_scheduler as sched
from app.services import bilancio_ufficiale_service as svc
from tests.test_bilancio_ufficiale_service import (  # noqa: F401  fixture autouse
    PIVA,
    PIVA_MASCHERATA,
    FakeOpenapi,
    _api_error,
    _iso,
    db_base,
    db_con,
    facet_invalidati,
    notifiche,
    nuova_richiesta,
    spawned,
)


@pytest.fixture
def avanzate(monkeypatch):
    """`avanza` finto: registra gli id nell'ordine delle chiamate."""
    ids: list[str] = []

    async def fake(primary, openapi, riga, **_kwargs):
        ids.append(riga["id"])

    monkeypatch.setattr(svc, "avanza", fake)
    return ids


def _db_con_righe(*righe: dict):
    return db_base(company_bilancio_richieste=list(righe))


class TestPasso:
    async def test_solo_le_aperte_dalla_piu_vecchia(self, avanzate):
        invio = nuova_richiesta("in_invio", minuti_fa=3)
        lavorazione = nuova_richiesta("in_lavorazione", minuti_fa=90)
        ignoto = nuova_richiesta("esito_ignoto", minuti_fa=40)
        chiuse = [
            nuova_richiesta("completata", minuti_fa=500),
            nuova_richiesta("errore", minuti_fa=600, errore_codice="scaduta"),
            nuova_richiesta("non_disponibile", minuti_fa=700),
            nuova_richiesta("annullata", minuti_fa=800),
        ]
        db = _db_con_righe(invio, lavorazione, ignoto, *chiuse)
        n = await sched.esegui_passo(db, FakeOpenapi())
        assert n == 3
        assert avanzate == [lavorazione["id"], ignoto["id"], invio["id"]]

    async def test_al_piu_un_blocco_per_giro(self, avanzate, monkeypatch):
        monkeypatch.setattr(sched, "MAX_RICHIESTE_PASSO", 2)
        righe = [nuova_richiesta("in_lavorazione", minuti_fa=m) for m in (10, 30, 20)]
        db = _db_con_righe(*righe)
        assert await sched.esegui_passo(db, FakeOpenapi()) == 2
        assert avanzate == [righe[1]["id"], righe[2]["id"]]

    async def test_openapi_spento_nessun_giro(self, avanzate):
        riga = nuova_richiesta("in_lavorazione", minuti_fa=60 * 25)
        db = _db_con_righe(riga)
        assert await sched.esegui_passo(db, FakeOpenapi(enabled=False)) == 0
        assert avanzate == [] and db.ops == []
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"

    async def test_nessuna_aperta_nessun_log(self, avanzate, caplog):
        db = _db_con_righe(nuova_richiesta("completata"))
        with caplog.at_level(logging.INFO, logger=sched.logger.name):
            assert await sched.esegui_passo(db, FakeOpenapi()) == 0
        assert avanzate == []
        assert caplog.records == []

    async def test_lettura_fallita_non_solleva(self, avanzate, caplog):
        db = _db_con_righe(nuova_richiesta("in_lavorazione"))
        db.errors[("company_bilancio_richieste", "select")] = _api_error(None, code="08006")
        with caplog.at_level(logging.INFO, logger=sched.logger.name):
            assert await sched.esegui_passo(db, FakeOpenapi()) == 0
        assert avanzate == []
        [record] = caplog.records
        assert record.levelno == logging.ERROR and "08006" in record.getMessage()

    async def test_una_riga_in_errore_non_ferma_le_altre(self, monkeypatch, caplog):
        prima = nuova_richiesta("in_lavorazione", minuti_fa=30)
        seconda = nuova_richiesta("esito_ignoto", minuti_fa=20)
        viste: list[str] = []

        async def fake(primary, openapi, riga, **_kwargs):
            viste.append(riga["id"])
            if riga["id"] == prima["id"]:
                raise RuntimeError("guasto")

        monkeypatch.setattr(svc, "avanza", fake)
        with caplog.at_level(logging.ERROR, logger=sched.logger.name):
            assert await sched.esegui_passo(_db_con_righe(prima, seconda), FakeOpenapi()) == 2
        assert viste == [prima["id"], seconda["id"]]
        assert any(prima["id"] in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("sandbox", [False, True], ids=["produzione", "sandbox"])
    async def test_solo_l_ambiente_openapi_in_uso(self, avanzate, caplog, sandbox):
        """Dopo un cambio di OPENAPI_ENV le richieste dell'altro ambiente non
        si interrogano (il provider non le conosce): saltate e contate."""
        produzione = nuova_richiesta("in_lavorazione", minuti_fa=30, sandbox=False)
        di_prova = nuova_richiesta("esito_ignoto", minuti_fa=40, sandbox=True)
        db = _db_con_righe(produzione, di_prova)
        with caplog.at_level(logging.INFO, logger=sched.logger.name):
            assert await sched.esegui_passo(db, FakeOpenapi(sandbox=sandbox)) == 1
        assert avanzate == [(di_prova if sandbox else produzione)["id"]]
        [record] = caplog.records
        assert record.levelno == logging.INFO
        assert "1 richieste aperte esaminate, 1 saltate" in record.getMessage()

    async def test_le_saltate_non_occupano_il_blocco(self, avanzate, monkeypatch):
        monkeypatch.setattr(sched, "MAX_RICHIESTE_PASSO", 2)
        vecchie = [nuova_richiesta("esito_ignoto", minuti_fa=90 + m, sandbox=True)
                   for m in range(3)]
        nuova = nuova_richiesta("in_lavorazione", minuti_fa=10)
        await sched.esegui_passo(_db_con_righe(*vecchie, nuova), FakeOpenapi())
        assert avanzate == [nuova["id"]]


class TestConIlServizio:
    """`avanza` vero: il failsafe chiude le richieste che il follower (perso
    a un riavvio) avrebbe chiuso."""

    async def test_lavorazione_oltre_24_ore_scaduta_senza_rimborso(
        self, notifiche, caplog  # noqa: F811  fixture importata
    ):
        riga = nuova_richiesta("in_lavorazione", minuti_fa=60 * 25)
        db, openapi = db_con(riga), FakeOpenapi()
        with caplog.at_level(logging.INFO):
            assert await sched.esegui_passo(db, openapi) == 1
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "scaduta")
        assert chiusa["rimborsata_at"] is None and db.ledger("refund") == []
        assert db.inventario()["quantita"] == 1
        assert [n["tipo"] for n in notifiche] == ["bilancio_ufficiale.non_disponibile"]
        testo = " ".join(r.getMessage() for r in caplog.records)
        assert PIVA not in testo and PIVA_MASCHERATA not in testo

    async def test_esito_ignoto_non_partita_rimborsata(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = []
        await sched.esegui_passo(db, openapi)
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "non_inviata")
        assert chiusa["rimborsata_at"] and db.inventario()["quantita"] == 2
        assert openapi.nomi() == ["lista"]

    async def test_due_giri_un_solo_rimborso(self):
        """Due giri in parallelo, il follower in ritardo con la riga letta
        prima della chiusura e un terzo giro: la stessa richiesta non partita
        si rimborsa una volta sola."""
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = []
        await asyncio.gather(sched.esegui_passo(db, openapi), sched.esegui_passo(db, openapi))
        await svc.avanza(db, openapi, dict(riga))
        assert await sched.esegui_passo(db, openapi) == 0
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "non_inviata")
        assert len(db.ledger("refund")) == 1
        assert db.inventario()["quantita"] == 2
        assert len(db.chiusure()) == 1

    async def test_richiesta_dell_altro_ambiente_intatta(self):
        """Richiesta di prova ancora aperta, backend passato in produzione:
        la lista di produzione non la contiene, ma non è una prova che non
        sia partita. Nessuna chiamata, nessun rimborso."""
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31, sandbox=True)
        db, openapi = db_con(riga), FakeOpenapi(sandbox=False)
        openapi.lista = []
        assert await sched.esegui_passo(db, openapi) == 0
        assert db.richiesta(riga["id"])["stato"] == "esito_ignoto"
        assert openapi.chiamate == [] and db.ledger("refund") == []

    async def test_pronta_completata(self):
        riga = nuova_richiesta("in_lavorazione", minuti_fa=20)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Dati disponibili"}
        await sched.esegui_passo(db, openapi)
        assert db.richiesta(riga["id"])["stato"] == "completata"
        assert [d["tipo"] for d in db.documenti()] == ["pdf", "xbrl"]

    async def test_claim_di_un_altro_poller_rispettato(self):
        """Il follower o una lettura hanno appena preso il turno: il giro non
        chiama il provider e non tocca la riga."""
        riga = nuova_richiesta("in_lavorazione", minuti_fa=60 * 25, ultimo_poll_at=_iso(0))
        db, openapi = db_con(riga), FakeOpenapi()
        await sched.esegui_passo(db, openapi)
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        assert openapi.chiamate == []


class TestRunForever:
    async def test_sopravvive_a_un_errore_e_propaga_la_cancellazione(self, monkeypatch, caplog):
        giri: list[int] = []
        attese: list[float] = []

        async def passo(primary, openapi):
            giri.append(1)
            if len(giri) == 1:
                raise RuntimeError("inatteso")
            return 0

        async def attendi(secondi):
            attese.append(secondi)
            if len(attese) == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(sched, "esegui_passo", passo)
        monkeypatch.setattr(sched, "_attendi", attendi)
        with caplog.at_level(logging.ERROR, logger=sched.logger.name):
            with pytest.raises(asyncio.CancelledError):
                await sched.run_forever(object(), FakeOpenapi())
        assert len(giri) == 2
        assert attese == [sched.INTERVALLO_SECONDS] * 2 == [600, 600]
        assert "RuntimeError" in caplog.records[0].getMessage()

    async def test_openapi_spento_esce_con_un_warning(self, monkeypatch, caplog):
        """Senza provider il failsafe chiuderebbe come scadute richieste forse
        pronte e già pagate: non parte, con un solo WARNING."""
        giri: list[int] = []

        async def passo(primary, openapi):
            giri.append(1)
            return 0

        monkeypatch.setattr(sched, "esegui_passo", passo)
        with caplog.at_level(logging.INFO, logger=sched.logger.name):
            await sched.run_forever(object(), FakeOpenapi(enabled=False))
        assert giri == []
        [record] = caplog.records
        assert record.levelno == logging.WARNING and "openapi non configurato" in record.getMessage()
