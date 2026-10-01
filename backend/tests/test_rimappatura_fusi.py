"""Rimappatura dei bandi fusi (fase c del contratto DB bandi §6.2, migration
0043): un passo legge gli id in uso (`fn_bandi_in_uso`), cerca i doppioni su
`bando_fusione` a blocchi di 100 e chiama `fn_rimappa_bando_fuso` per ogni
coppia. Prova contro attiva, coppie scartate, errori isolati per coppia
(23505, 40P01), migration assente, call in collisione a livello INFO, e mai
un'eccezione al chiamante. Poi il conteggio del catalogo (`coppie_catalogo`)
e le separazioni (migration 0045 e 0046), trattate PRIMA della rimappatura:
doppione ancora fuso, separato e tornato nella vista (ripristino nel percorso
automatico con `p_prova` pari alla modalità), separato ma fuori dalla vista,
letture in errore mai trattate come assenze, conflitti ripetuti con un solo
WARNING al giorno, conflitti resi definitivi contati a parte e mai avvisati
come ripetuti. Primario e catalogo finti."""

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


def ripristino(**valori) -> dict:
    """Ritorno di fn_ripristina_rimappatura_voci nel percorso automatico (tutte
    le chiavi, come la 0046)."""
    esito = {t: {"ripristinate": 0, "in_conflitto": 0, "definitivi": 0}
             for t in rf._CHIAVI_CONTEGGI}
    for chiave, valore in valori.items():
        tabella, campo = chiave.split("__")
        esito[tabella][campo] = valore
    return esito


def errore(codice: str) -> APIError:
    return APIError({"message": "messaggio che non va nel log", "code": codice, "hint": None,
                     "details": None})


class FakePrimary:
    def __init__(self, in_uso=None, esiti=None, guasto_in_uso: Exception | None = None,
                 doppioni=None, ripristini=None, guasto_doppioni: Exception | None = None):
        self.in_uso = [] if in_uso is None else in_uso
        self.esiti = esiti or {}
        self.guasto_in_uso = guasto_in_uso
        self.doppioni = [] if doppioni is None else doppioni
        self.ripristini = ripristini or {}
        self.guasto_doppioni = guasto_doppioni
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
                if nome == "fn_doppioni_rimappati":
                    if db.guasto_doppioni is not None:
                        raise db.guasto_doppioni
                    return SimpleNamespace(data=db.doppioni)
                if nome == "fn_ripristina_rimappatura_voci":
                    esito = db.ripristini.get(params["p_doppione"], ripristino())
                    if isinstance(esito, Exception):
                        raise esito
                    return SimpleNamespace(data=esito)
                raise AssertionError(f"RPC inattesa: {nome}")

        return _Rpc()

    def rimappate(self) -> list[dict]:
        return [p for nome, p in self.chiamate if nome == "fn_rimappa_bando_fuso"]

    def ripristinate(self) -> list[dict]:
        return [p for nome, p in self.chiamate if nome == "fn_ripristina_rimappatura_voci"]

    def nomi(self) -> list[str]:
        return [nome for nome, _ in self.chiamate]


class FakeSecondary:
    """`bando_fusione` (per lista di `bando_id`, per singolo `eq`, o il
    conteggio esatto con `limit`) e `bando_pubblico` per lista di `id`.
    `guasti`: indici delle richieste per blocco (`in_`) che rispondono con
    un errore; `guasto_conteggio` (o `conteggio_nullo`, risposta senza
    conteggio) e `guasto_vista` per le altre letture."""

    def __init__(self, fusioni=(), guasti=(), *, pubblici=(), guasto_conteggio=False,
                 conteggio_nullo=False, guasto_vista=False, guasto_riletta=False):
        self.fusioni = list(fusioni)
        self.guasti = set(guasti)
        self.pubblici = set(pubblici)
        self.guasto_conteggio = guasto_conteggio
        self.conteggio_nullo = conteggio_nullo
        self.guasto_vista = guasto_vista
        self.guasto_riletta = guasto_riletta
        self.blocchi: list[list[int]] = []
        self.blocchi_vista: list[list[int]] = []
        self.rilette: list[int] = []
        self.conteggi: list[int | None] = []
        self.select: list[str] = []

    def table(self, nome):
        assert nome in ("bando_fusione", "bando_pubblico"), f"tabella inattesa: {nome}"
        sec = self

        class _Q:
            def __init__(self):
                self.ids = None
                self.eq_id = None
                self.conta = None
                self.massimo = None

            def select(self, colonne, count=None):
                sec.select.append(colonne)
                self.conta = count
                return self

            def in_(self, colonna, valori):
                assert colonna == ("bando_id" if nome == "bando_fusione" else "id")
                self.ids = list(valori)
                return self

            def eq(self, colonna, valore):
                assert nome == "bando_fusione" and colonna == "bando_id"
                self.eq_id = valore
                return self

            def limit(self, n):
                self.massimo = n
                return self

            async def execute(self):
                if nome == "bando_pubblico":
                    sec.blocchi_vista.append(self.ids)
                    if sec.guasto_vista:
                        raise errore("57014")
                    return SimpleNamespace(data=[{"id": i} for i in self.ids
                                                 if i in sec.pubblici])
                if self.eq_id is not None:
                    sec.rilette.append(self.eq_id)
                    if sec.guasto_riletta:
                        raise errore("57014")
                    return SimpleNamespace(data=[
                        r for r in sec.fusioni
                        if isinstance(r, dict) and r.get("bando_id") == self.eq_id][:1])
                if self.conta == "exact" and self.ids is None:
                    sec.conteggi.append(self.massimo)
                    if sec.guasto_conteggio:
                        raise errore("57014")
                    righe = [{"bando_id": r["bando_id"]} for r in sec.fusioni
                             if isinstance(r, dict) and isinstance(r.get("bando_id"), int)]
                    return SimpleNamespace(data=righe[:self.massimo],
                                           count=None if sec.conteggio_nullo else len(righe))
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


@pytest.fixture(autouse=True)
def stato_pulito(monkeypatch):
    monkeypatch.setattr(rf, "_conflitti", {})
    monkeypatch.setattr(rf, "_avvisi_conflitto", {})


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
            "modalita": "prova" if prova else "attiva", "in_uso": 4, "fusi": 1,
            "coppie_catalogo": 1, "scartate": 0, "errori": 0,
            "separazioni_rilevate": 0, "ripristinate": 0, "in_conflitto": 0,
            "conflitti_definitivi": 0, "totali": esito,
            "coppie": [{"doppione": 2, "master": 900, "conteggi": esito}],
        }
        # colonne per nome, mai select=*
        assert secondary.select == ["bando_id,master_id,master_slug", "bando_id"]
        assert primary.ripristinate() == []

    async def test_blocchi_da_cento(self):
        primary = FakePrimary(in_uso=list(range(1, 251)))
        secondary = FakeSecondary([fusione(7), fusione(180, 901, "master-901")])
        report = await rf.passo(primary, secondary, prova=True)
        assert [len(b) for b in secondary.blocchi] == [100, 100, 50]
        assert rf.BLOCCO_FUSIONI == 100
        assert [p["p_doppione"] for p in primary.rimappate()] == [7, 180]
        assert report["fusi"] == 2

    async def test_nessun_id_in_uso_nessuna_lettura_per_id(self):
        # Senza id in uso non si cerca nessun doppione (nessun blocco `in_`);
        # il conteggio del catalogo si legge comunque.
        primary = FakePrimary(in_uso=[])
        secondary = FakeSecondary([fusione(2), fusione(3)])
        report = await rf.passo(primary, secondary, prova=False)
        assert secondary.blocchi == [] and primary.rimappate() == []
        assert (report["in_uso"], report["fusi"], report["errori"]) == (0, 0, 0)
        assert report["coppie_catalogo"] == 2

    async def test_forme_dell_array_in_uso(self):
        assert rf._ids_in_uso([3, 1, 3, True, -1, "x", None]) == [3, 1]
        assert rf._ids_in_uso({"fn_bandi_in_uso": [5, 6]}) == [5, 6]
        assert rf._ids_in_uso([{"fn_bandi_in_uso": 8}]) == [8]
        assert rf._ids_in_uso(None) == []
        assert rf._ids_array({"fn_doppioni_rimappati": [9, 9]}, "fn_doppioni_rimappati") == [9]

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
        # per id del catalogo, nessuna eccezione; il conteggio del catalogo e
        # le separazioni si fanno lo stesso.
        primary = FakePrimary(guasto_in_uso=errore(codice))
        secondary = FakeSecondary([fusione(2)])
        with caplog.at_level(logging.ERROR, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=True)
        assert (report["errori"], report["in_uso"], report["coppie"]) == (1, 0, [])
        assert secondary.blocchi == [] and secondary.conteggi == [1]
        assert report["coppie_catalogo"] == 1
        assert primary.nomi() == ["fn_doppioni_rimappati", "fn_bandi_in_uso"]
        [record] = caplog.records
        assert "0043" in record.getMessage() and codice in record.getMessage()

    async def test_bandi_in_uso_in_errore_non_ferma_catalogo_e_separazioni(self):
        primary = FakePrimary(guasto_in_uso=RuntimeError("rete"), doppioni=[2],
                              ripristini={2: ripristino(saved_bandi__ripristinate=1)})
        secondary = FakeSecondary([fusione(5)], pubblici={2})
        report = await rf.passo(primary, secondary, prova=False)
        assert (report["errori"], report["in_uso"], report["fusi"]) == (1, 0, 0)
        assert report["coppie_catalogo"] == 1
        assert (report["separazioni_rilevate"], report["ripristinate"]) == (1, 1)
        assert [p["p_doppione"] for p in primary.ripristinate()] == [2]

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


class TestCoppieCatalogo:
    async def test_conta_tutte_le_righe_anche_fuori_dagli_id_in_uso(self):
        # Le 6 coppie del catalogo con nessun id in uso: fusi 0, coppie_catalogo 6.
        secondary = FakeSecondary([fusione(i) for i in range(1, 7)])
        report = await rf.passo(FakePrimary(in_uso=[50]), secondary, prova=True)
        assert (report["fusi"], report["coppie_catalogo"]) == (0, 6)
        # una sola richiesta a conteggio esatto, con limit 1 e colonna per nome
        assert secondary.conteggi == [1] and secondary.select[-1] == "bando_id"

    async def test_conteggio_assente_conta_e_lascia_none(self, caplog):
        secondary = FakeSecondary([fusione(2)], conteggio_nullo=True)
        with caplog.at_level(logging.WARNING, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(FakePrimary(), secondary, prova=True)
        assert report["coppie_catalogo"] is None and report["errori"] == 1
        assert "assente" in caplog.text

    async def test_errore_conta_e_lascia_none(self, caplog):
        primary = FakePrimary(in_uso=[2])
        secondary = FakeSecondary([fusione(2)], guasto_conteggio=True)
        with caplog.at_level(logging.WARNING, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=False)
        assert report["coppie_catalogo"] is None and report["errori"] == 1
        # la rimappatura e i totali non ne risentono
        assert report["fusi"] == 1 and [p["p_doppione"] for p in primary.rimappate()] == [2]
        assert "conteggio" in caplog.text and "messaggio che non va nel log" not in caplog.text


class TestSeparazioni:
    @pytest.mark.parametrize("prova", [True, False])
    async def test_doppione_ancora_fuso_nessun_ripristino(self, prova):
        primary = FakePrimary(doppioni=[2])
        secondary = FakeSecondary([fusione(2)])
        report = await rf.passo(primary, secondary, prova=prova)
        assert primary.ripristinate() == [] and secondary.blocchi_vista == []
        assert (report["separazioni_rilevate"], report["ripristinate"],
                report["in_conflitto"], report["errori"]) == (0, 0, 0, 0)
        # per le separazioni si legge solo bando_id
        assert secondary.blocchi == [[2]] and secondary.select[-1] == "bando_id"

    @pytest.mark.parametrize("prova", [True, False])
    async def test_separato_e_tornato_nella_vista_ripristinato(self, prova, caplog):
        esito = ripristino(saved_bandi__ripristinate=2, calendar_events__ripristinate=1,
                           partner_calls__in_conflitto=1)
        primary = FakePrimary(doppioni=[2, 3], ripristini={2: esito})
        secondary = FakeSecondary([fusione(3)], pubblici={2})
        with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=prova)
        assert primary.ripristinate() == [
            {"p_doppione": 2, "p_dal": "-infinity", "p_prova": prova, "p_automatico": True}]
        assert (report["separazioni_rilevate"], report["ripristinate"],
                report["in_conflitto"], report["errori"]) == (1, 3, 1, 0)
        assert secondary.blocchi_vista == [[2]]
        # in attiva una riletta del solo doppione prima di scrivere; in prova no
        assert secondary.rilette == ([] if prova else [2])
        [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert "bando 2" in record.getMessage()
        assert ("da ripristinare" if prova else "ripristinate") in record.getMessage()

    async def test_separato_ma_fuori_dalla_vista_resta_sul_master(self, caplog):
        primary = FakePrimary(doppioni=[2])
        secondary = FakeSecondary([], pubblici=set())
        with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=False)
        assert primary.ripristinate() == [] and secondary.rilette == []
        assert (report["separazioni_rilevate"], report["ripristinate"], report["errori"]) == (
            1, 0, 0)
        assert [r.levelno for r in caplog.records] == [logging.INFO]
        assert "non ancora nella vista" in caplog.records[0].getMessage()

    async def test_errore_su_bando_fusione_non_vale_come_assenza(self):
        # Il primo blocco fallisce: i suoi doppioni non sono separati; il
        # secondo blocco risponde e il suo doppione assente si ripristina.
        primary = FakePrimary(doppioni=list(range(1, 102)))
        secondary = FakeSecondary([], guasti={0}, pubblici={101})
        report = await rf.passo(primary, secondary, prova=False)
        assert [p["p_doppione"] for p in primary.ripristinate()] == [101]
        assert (report["separazioni_rilevate"], report["errori"]) == (1, 1)

    async def test_errore_su_bando_pubblico_nessun_ripristino(self):
        primary = FakePrimary(doppioni=[2])
        secondary = FakeSecondary([], guasto_vista=True)
        report = await rf.passo(primary, secondary, prova=False)
        assert primary.ripristinate() == []
        assert (report["separazioni_rilevate"], report["errori"]) == (1, 1)

    async def test_riletta_che_ricompare_o_fallisce_nessuna_scrittura(self):
        # Il doppione manca dal blocco ma ricompare alla riletta (replica in
        # ritardo): niente ripristino, nessun errore.
        primary = FakePrimary(doppioni=[2])

        class Ricompare(FakeSecondary):
            def table(self, nome):
                q = super().table(nome)
                sec = self

                class _Q(type(q)):
                    async def execute(inner):
                        if inner.eq_id is not None:
                            sec.rilette.append(inner.eq_id)
                            return SimpleNamespace(data=[{"bando_id": inner.eq_id}])
                        return await super().execute()

                nuova = _Q()
                return nuova

        secondary = Ricompare([], pubblici={2})
        report = await rf.passo(primary, secondary, prova=False)
        assert primary.ripristinate() == [] and secondary.rilette == [2]
        assert (report["separazioni_rilevate"], report["errori"]) == (1, 0)

        primary = FakePrimary(doppioni=[2])
        secondary = FakeSecondary([], pubblici={2}, guasto_riletta=True)
        report = await rf.passo(primary, secondary, prova=False)
        assert primary.ripristinate() == [] and report["errori"] == 1

    @pytest.mark.parametrize("codice", ["PGRST202", "42883"])
    async def test_migration_0045_non_applicata_il_resto_gira(self, codice, caplog):
        esito = conteggi(saved_bandi__aggiornate=1)
        primary = FakePrimary(in_uso=[2], esiti={2: esito}, guasto_doppioni=errore(codice))
        with caplog.at_level(logging.ERROR, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, FakeSecondary([fusione(2)]), prova=False)
        assert report["totali"] == esito and report["fusi"] == 1
        assert report["coppie_catalogo"] == 1
        assert (report["errori"], report["separazioni_rilevate"]) == (1, 0)
        [record] = caplog.records
        assert "0045" in record.getMessage() and codice in record.getMessage()

    @pytest.mark.parametrize("codice", ["23505", "P0001", "PGRST202"])
    async def test_errore_del_ripristino_isolato(self, codice, caplog):
        primary = FakePrimary(doppioni=[2, 3], ripristini={
            2: errore(codice), 3: ripristino(saved_bandi__ripristinate=1)})
        secondary = FakeSecondary([], pubblici={2, 3})
        with caplog.at_level(logging.WARNING, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, secondary, prova=False)
        assert [p["p_doppione"] for p in primary.ripristinate()] == [2, 3]
        assert (report["errori"], report["ripristinate"]) == (1, 1)
        record = next(r for r in caplog.records if codice in r.getMessage())
        assert record.levelno == (logging.WARNING if codice == "23505" else logging.ERROR)
        # funzione assente: è la 0046 a doverla aggiungere
        assert ("0046" in record.getMessage()) is (codice == "PGRST202")
        assert "messaggio che non va nel log" not in caplog.text

    async def test_conflitto_ripetuto_info_poi_un_warning_al_giorno(self, monkeypatch, caplog):
        esito = ripristino(saved_bandi__in_conflitto=1)
        primary = FakePrimary(doppioni=[2], ripristini={2: esito})
        secondary = FakeSecondary([], pubblici={2})
        orologio = [1000.0]
        monkeypatch.setattr(rf, "_orologio", lambda: orologio[0])

        async def un_passo() -> list[int]:
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
                report = await rf.passo(primary, secondary, prova=False)
            assert report["in_conflitto"] == 1 and report["ripristinate"] == 0
            assert report["errori"] == 0
            return [r.levelno for r in caplog.records if "conflitto" in r.getMessage()]

        for _ in range(rf.PASSI_CONFLITTO_TOLLERATI):
            assert await un_passo() == [logging.INFO]
        # oltre la soglia: un solo WARNING, poi silenzio per un giorno
        assert await un_passo() == [logging.WARNING]
        orologio[0] += 3600
        assert await un_passo() == []
        orologio[0] += rf.SECONDI_FRA_AVVISI_CONFLITTO
        assert await un_passo() == [logging.WARNING]
        # tornato fuso: il conteggio riparte da zero
        secondary.fusioni.append(fusione(2))
        report = await rf.passo(primary, secondary, prova=False)
        assert report["separazioni_rilevate"] == 0 and rf._conflitti == {}

    async def test_conflitto_azzerato_dal_ripristino(self):
        primary = FakePrimary(doppioni=[2], ripristini={2: ripristino(saved_bandi__in_conflitto=1)})
        secondary = FakeSecondary([], pubblici={2})
        await rf.passo(primary, secondary, prova=False)
        assert rf._conflitti == {2: 1}
        primary.ripristini[2] = ripristino(saved_bandi__ripristinate=1)
        await rf.passo(primary, secondary, prova=False)
        assert rf._conflitti == {}
        primary.doppioni = []
        await rf.passo(primary, secondary, prova=False)
        assert secondary.blocchi == [[2], [2]]  # nessuna lettura senza doppioni

    async def test_conteggi_del_ripristino_malformati_valgono_zero(self, caplog):
        primary = FakePrimary(doppioni=[2], ripristini={2: {"saved_bandi": {"ripristinate": "1"},
                                                            "partner_calls": None}})
        with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, FakeSecondary([], pubblici={2}), prova=False)
        assert (report["ripristinate"], report["in_conflitto"], report["errori"]) == (0, 0, 0)
        assert report["conflitti_definitivi"] == 0
        assert any("nessuna riga" in r.getMessage() for r in caplog.records)


class TestOrdineNelPasso:
    async def test_separazioni_prima_della_rimappatura(self):
        primary = FakePrimary(in_uso=[3], doppioni=[2], esiti={3: conteggi()})
        secondary = FakeSecondary([fusione(3)], pubblici={2})
        await rf.passo(primary, secondary, prova=False)
        assert primary.nomi() == ["fn_doppioni_rimappati", "fn_ripristina_rimappatura_voci",
                                  "fn_bandi_in_uso", "fn_rimappa_bando_fuso"]

    async def test_doppione_separato_e_master_fuso_nello_stesso_aggiornamento(self):
        """D (2) era fuso in M (900): le sue righe stanno su M. Il catalogo
        separa D e fonde M in M2 (901) insieme. Il ripristino gira prima: le
        righe tornano a D e M non è più in uso, quindi non si sposta nulla su
        M2 (nell'ordine precedente sarebbero passate su M2 prima del
        ripristino)."""
        class Primario(FakePrimary):
            def rpc(self, nome, params):
                if nome == "fn_ripristina_rimappatura_voci":
                    self.in_uso = [2]  # le righe tornano da M a D
                return super().rpc(nome, params)

        primary = Primario(in_uso=[900], doppioni=[2],
                           ripristini={2: ripristino(saved_bandi__ripristinate=1)})
        secondary = FakeSecondary([fusione(900, 901, "master-901")], pubblici={2})
        report = await rf.passo(primary, secondary, prova=False)
        assert (report["ripristinate"], report["in_uso"], report["fusi"]) == (1, 1, 0)
        assert primary.rimappate() == []


class TestConflittiDefinitivi:
    @pytest.mark.parametrize("prova", [True, False])
    async def test_contati_a_parte_e_mai_avvisati_come_ripetuti(self, prova, caplog):
        # Tutti i conflitti del doppione diventano definitivi: si contano, un
        # INFO per passo e nessun conteggio verso il WARNING dei conflitti
        # ripetuti (in attiva il doppione non torna più nell'elenco).
        esito = ripristino(saved_bandi__in_conflitto=1, saved_bandi__definitivi=1,
                           calendar_events__in_conflitto=1, calendar_events__definitivi=1)
        primary = FakePrimary(doppioni=[2], ripristini={2: esito})
        with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
            for _ in range(rf.PASSI_CONFLITTO_TOLLERATI + 2):
                report = await rf.passo(primary, FakeSecondary([], pubblici={2}), prova=prova)
        assert (report["in_conflitto"], report["conflitti_definitivi"], report["errori"]) == (
            2, 2, 0)
        assert rf._conflitti == {}
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        messaggi = [r.getMessage() for r in caplog.records]
        attesa = "2 conflitti da rendere definitivi" if prova else "2 conflitti resi definitivi"
        assert all(attesa in m for m in messaggi)
        assert len(messaggi) == rf.PASSI_CONFLITTO_TOLLERATI + 2

    async def test_solo_i_conflitti_aperti_contano_per_l_avviso(self, caplog):
        esito = ripristino(saved_bandi__in_conflitto=2, saved_bandi__definitivi=1,
                           partner_calls__in_conflitto=1)
        primary = FakePrimary(doppioni=[2], ripristini={2: esito})
        with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, FakeSecondary([], pubblici={2}), prova=False)
        assert (report["in_conflitto"], report["conflitti_definitivi"]) == (3, 1)
        assert rf._conflitti == {2: 1}
        assert any("2 righe in conflitto" in r.getMessage() for r in caplog.records)
        assert any("1 conflitti resi definitivi" in r.getMessage() for r in caplog.records)

    async def test_ripristino_con_definitivi_resta_a_warning(self, caplog):
        esito = ripristino(saved_bandi__ripristinate=1, calendar_events__in_conflitto=1,
                           calendar_events__definitivi=1)
        primary = FakePrimary(doppioni=[2], ripristini={2: esito})
        with caplog.at_level(logging.INFO, logger="bandofit.rimappatura_fusi"):
            report = await rf.passo(primary, FakeSecondary([], pubblici={2}), prova=False)
        assert (report["ripristinate"], report["conflitti_definitivi"]) == (1, 1)
        [avviso] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert "1 righe ripristinate" in avviso.getMessage()
        assert rf._conflitti == {}
