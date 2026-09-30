"""Rimappatura dei bandi fusi (fase c del contratto DB bandi §6.2, migration
0043): un passo legge gli id in uso (`fn_bandi_in_uso`), cerca i doppioni su
`bando_fusione` a blocchi di 100 e chiama `fn_rimappa_bando_fuso` per ogni
coppia. Prova contro attiva, coppie scartate, errori isolati per coppia
(23505, 40P01), migration assente, call in collisione a livello INFO, e mai
un'eccezione al chiamante. Primario e catalogo finti."""

import logging
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.services import rimappatura_fusi as rf


def conteggi(**valori) -> dict:
    """Ritorno della RPC (sempre con tutte le chiavi, come la 0043)."""
    esito = rf.totali_vuoti()
    for chiave, valore in valori.items():
        tabella, campo = chiave.split("__")
        esito[tabella][campo] = valore
    return esito


def errore(codice: str) -> APIError:
    return APIError({"message": "messaggio che non va nel log", "code": codice, "hint": None,
                     "details": None})


class FakePrimary:
    def __init__(self, in_uso=None, esiti=None, guasto_in_uso: Exception | None = None):
        self.in_uso = [] if in_uso is None else in_uso
        self.esiti = esiti or {}
        self.guasto_in_uso = guasto_in_uso
        self.chiamate: list[tuple[str, dict]] = []

    def rpc(self, nome, params):
        self.chiamate.append((nome, dict(params)))
        db = self

        class _Rpc:
            async def execute(self):
                if nome == "fn_bandi_in_uso":
                    if db.guasto_in_uso is not None:
                        raise db.guasto_in_uso
                    return SimpleNamespace(data=db.in_uso)
                if nome == "fn_rimappa_bando_fuso":
                    esito = db.esiti.get(params["p_doppione"], conteggi())
                    if isinstance(esito, Exception):
                        raise esito
                    return SimpleNamespace(data=esito)
                raise AssertionError(f"RPC inattesa: {nome}")

        return _Rpc()

    def rimappate(self) -> list[dict]:
        return [p for nome, p in self.chiamate if nome == "fn_rimappa_bando_fuso"]


class FakeSecondary:
    """Solo `bando_fusione` per lista di `bando_id`; i blocchi in `guasti`
    (indice della richiesta) rispondono con un errore."""

    def __init__(self, fusioni=(), guasti=()):
        self.fusioni = list(fusioni)
        self.guasti = set(guasti)
        self.blocchi: list[list[int]] = []
        self.select: list[str] = []

    def table(self, nome):
        assert nome == "bando_fusione", f"tabella del catalogo inattesa: {nome}"
        sec = self

        class _Q:
            def select(self, colonne):
                sec.select.append(colonne)
                return self

            def in_(self, colonna, valori):
                assert colonna == "bando_id"
                self.ids = list(valori)
                return self

            async def execute(self):
                indice = len(sec.blocchi)
                sec.blocchi.append(self.ids)
                if indice in sec.guasti:
                    raise errore("57014")
                # le righe malformate passano sempre: il servizio le deve scartare
                return SimpleNamespace(data=[
                    r for r in sec.fusioni
                    if not isinstance(r, dict) or r.get("bando_id") in self.ids
                ])

        return _Q()


def fusione(doppione: int, master: int = 900, slug: str | None = "master-900") -> dict:
    return {"bando_id": doppione, "master_id": master, "master_slug": slug}


class TestPasso:
    @pytest.mark.parametrize("prova", [True, False])
    async def test_prova_e_attiva(self, prova):
        esito = conteggi(saved_bandi__aggiornate=2, saved_bandi__eliminate=1,
                         calendar_events__convertiti=1, partner_calls__aggiornate=1)
        primary = FakePrimary(in_uso=[1, 2, 3, 900], esiti={2: esito})
        secondary = FakeSecondary([fusione(2)])
        report = await rf.passo(primary, secondary, prova=prova)
        assert primary.rimappate() == [
            {"p_doppione": 2, "p_master": 900, "p_master_slug": "master-900", "p_prova": prova}]
        assert report == {
            "modalita": "prova" if prova else "attiva", "in_uso": 4, "fusi": 1, "scartate": 0,
            "errori": 0, "totali": esito,
            "coppie": [{"doppione": 2, "master": 900, "conteggi": esito}],
        }
        # colonne per nome, mai select=*
        assert secondary.select == ["bando_id,master_id,master_slug"]

    async def test_blocchi_da_cento(self):
        primary = FakePrimary(in_uso=list(range(1, 251)))
        secondary = FakeSecondary([fusione(7), fusione(180, 901, "master-901")])
        report = await rf.passo(primary, secondary, prova=True)
        assert [len(b) for b in secondary.blocchi] == [100, 100, 50]
        assert rf.BLOCCO_FUSIONI == 100
        assert [p["p_doppione"] for p in primary.rimappate()] == [7, 180]
        assert report["fusi"] == 2

    async def test_nessun_id_in_uso_nessuna_lettura_del_catalogo(self):
        primary = FakePrimary(in_uso=[])
        secondary = FakeSecondary()
        report = await rf.passo(primary, secondary, prova=False)
        assert secondary.blocchi == [] and primary.rimappate() == []
        assert (report["in_uso"], report["fusi"], report["errori"]) == (0, 0, 0)

    async def test_forme_dell_array_in_uso(self):
        assert rf._ids_in_uso([3, 1, 3, True, -1, "x", None]) == [3, 1]
        assert rf._ids_in_uso({"fn_bandi_in_uso": [5, 6]}) == [5, 6]
        assert rf._ids_in_uso([{"fn_bandi_in_uso": 8}]) == [8]
        assert rf._ids_in_uso(None) == []

    async def test_coppie_incomplete_scartate(self, caplog):
        primary = FakePrimary(in_uso=[1, 2, 3, 4, 5])
        secondary = FakeSecondary([
            fusione(1, master=None), fusione(2, master=2), fusione(3, slug=" "),
            fusione(4, master=True), fusione(5), {"bando_id": 99, "master_id": 900,
                                                  "master_slug": "fuori-dal-blocco"},
            "riga non valida",
        ])
        with caplog.at_level(logging.WARNING, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=True)
        assert (report["scartate"], report["fusi"]) == (4, 1)
        assert [p["p_doppione"] for p in primary.rimappate()] == [5]
        assert len([r for r in caplog.records if "incompleta" in r.getMessage()]) == 4

    @pytest.mark.parametrize("codice", ["23505", "40P01"])
    async def test_errore_su_una_coppia_non_ferma_le_altre(self, codice, caplog):
        esito = conteggi(saved_bandi__aggiornate=1)
        primary = FakePrimary(in_uso=[2, 3], esiti={2: errore(codice), 3: esito})
        secondary = FakeSecondary([fusione(2), fusione(3)])
        with caplog.at_level(logging.WARNING, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=False)
        assert report["errori"] == 1 and report["totali"] == esito
        assert report["coppie"] == [
            {"doppione": 2, "master": 900, "errore": codice},
            {"doppione": 3, "master": 900, "conteggi": esito},
        ]
        # transitorio: WARNING con id e codice, mai il messaggio di PostgREST
        [record] = caplog.records
        assert record.levelno == logging.WARNING and codice in record.getMessage()
        assert "rinviato" in record.getMessage()
        assert "messaggio che non va nel log" not in caplog.text

    async def test_errore_non_transitorio_a_livello_error(self, caplog):
        primary = FakePrimary(in_uso=[2], esiti={2: errore("P0001")})
        with caplog.at_level(logging.WARNING, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, FakeSecondary([fusione(2)]), prova=False)
        assert report["errori"] == 1
        [record] = caplog.records
        assert record.levelno == logging.ERROR and "P0001" in record.getMessage()

    @pytest.mark.parametrize("codice", ["PGRST202", "42883"])
    async def test_migration_non_applicata(self, codice, caplog):
        # Modalità accesa prima della 0043: un errore chiaro, nessuna lettura
        # del catalogo, nessuna eccezione.
        primary = FakePrimary(guasto_in_uso=errore(codice))
        secondary = FakeSecondary()
        with caplog.at_level(logging.ERROR, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=True)
        assert (report["errori"], report["in_uso"], report["coppie"]) == (1, 0, [])
        assert secondary.blocchi == []
        [record] = caplog.records
        assert "0043" in record.getMessage() and codice in record.getMessage()

    async def test_bandi_in_uso_non_leggibili(self, caplog):
        primary = FakePrimary(guasto_in_uso=RuntimeError("rete"))
        with caplog.at_level(logging.ERROR, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, FakeSecondary(), prova=False)
        assert report["errori"] == 1
        assert "RuntimeError" in caplog.text and "0043" not in caplog.text

    async def test_blocco_del_catalogo_in_errore_saltato(self):
        primary = FakePrimary(in_uso=list(range(1, 151)))
        secondary = FakeSecondary([fusione(5), fusione(120)], guasti={0})
        report = await rf.passo(primary, secondary, prova=False)
        assert report["errori"] == 1
        assert [p["p_doppione"] for p in primary.rimappate()] == [120]

    async def test_call_in_collisione_a_livello_info(self, caplog):
        # La coppia ricompare a ogni passo finché la call resta attiva: si
        # conta e si registra a INFO, nessun WARNING.
        esito = conteggi(partner_calls__in_collisione=1)
        primary = FakePrimary(in_uso=[2], esiti={2: esito})
        with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, FakeSecondary([fusione(2)]), prova=False)
        assert report["totali"]["partner_calls"]["in_collisione"] == 1
        assert report["errori"] == 0
        assert [r.levelno for r in caplog.records] == [logging.INFO]
        assert "collisione" in caplog.records[0].getMessage()

    async def test_conteggi_malformati_valgono_zero(self):
        primary = FakePrimary(in_uso=[2], esiti={2: {"saved_bandi": {"aggiornate": "2"},
                                                     "partner_calls": None}})
        report = await rf.passo(primary, FakeSecondary([fusione(2)]), prova=False)
        assert report["totali"] == rf.totali_vuoti() and report["errori"] == 0

    def test_riassunto_limita_le_coppie(self):
        report = {"modalita": "prova", "totali": rf.totali_vuoti(),
                  "coppie": [{"doppione": i, "master": 900} for i in range(1, 61)]}
        breve = rf.riassunto(report)
        assert len(breve["coppie"]) == rf.MAX_COPPIE_NEL_LOG == 50
        assert breve["coppie_omesse"] == 10 and breve["totali"] == report["totali"]
        assert len(report["coppie"]) == 60  # il report non si tocca
        assert "coppie_omesse" not in rf.riassunto({"coppie": []})
