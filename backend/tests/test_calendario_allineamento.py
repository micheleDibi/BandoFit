"""Riallineamento delle scadenze in calendario al catalogo (migration 0048):
data, ora e stato del bando con le regole della creazione, solo le righe
cambiate, mai titolo/note/ora_fine, bandi assenti non toccati, letture a
pagine (keyset) e a blocchi, prima si legge tutto e poi si scrive, errori
confinati e mai rilanciati."""

import logging
from types import SimpleNamespace

from postgrest.exceptions import APIError

from app.services import calendario_allineamento as ca


def evento(n: int, bando_id: int = 42, **campi) -> dict:
    riga = {
        "id": f"e0000000-0000-0000-0000-{n:012d}",
        "bando_id": bando_id,
        "data": "2026-09-15",
        "tutto_il_giorno": True,
        "ora_inizio": None,
        "bando_stato": "aperto",
    }
    riga.update(campi)
    return riga


def bando(bando_id: int = 42, **campi) -> dict:
    riga = {"id": bando_id, "data_scadenza": "2026-09-15", "ora_scadenza": None,
            "stato_effettivo": "aperto"}
    riga.update(campi)
    return riga


class QueryEventi:
    def __init__(self, db):
        self.db = db
        self.op = "select"
        self.payload = None
        self.filtri: dict = {}
        self.colonne = None
        self.dopo = None
        self.quante = None

    def select(self, colonne):
        self.colonne = colonne
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, colonna, valore):
        self.filtri[colonna] = valore
        return self

    def gt(self, colonna, valore):
        assert colonna == "id"
        self.dopo = valore
        return self

    def order(self, colonna):
        assert colonna == "id"
        return self

    def limit(self, n):
        self.quante = n
        return self

    async def execute(self):
        if self.op == "select":
            self.db.letture.append((self.colonne, dict(self.filtri), self.dopo, self.quante))
            if self.db.errore_lettura:
                raise APIError({"message": "boom", "code": "57014", "hint": None,
                                "details": None})
            righe = sorted((r for r in self.db.righe if r.get("tipo", "bando") == "bando"
                            and (self.dopo is None or r["id"] > self.dopo)),
                           key=lambda r: r["id"])
            pagina = [{k: v for k, v in r.items() if k != "tipo"} for r in righe[: self.quante]]
            if self.db.pagine_senza_id:
                pagina = [{k: v for k, v in r.items() if k != "id"} for r in pagina]
            return SimpleNamespace(data=pagina)
        self.db.update.append((self.payload, dict(self.filtri)))
        if self.filtri.get("id") in self.db.errori_update:
            raise APIError({"message": "violazione: dettagli del db", "code": "23514",
                            "hint": None, "details": "riga segreta"})
        if self.filtri.get("id") in self.db.spostate:
            return SimpleNamespace(data=[])
        aggiornate = []
        for riga in self.db.righe:
            if all(riga.get(c, "bando" if c == "tipo" else None) == v
                   for c, v in self.filtri.items()):
                riga.update(self.payload)
                aggiornate.append(dict(riga))
        return SimpleNamespace(data=aggiornate)


class Primario:
    def __init__(self, righe: list[dict]):
        self.righe = righe
        self.letture: list = []
        self.update: list = []
        self.errore_lettura = False
        self.pagine_senza_id = False
        self.errori_update: set[str] = set()
        self.spostate: set[str] = set()

    def table(self, nome):
        assert nome == "calendar_events"
        return QueryEventi(self)


class QueryCatalogo:
    def __init__(self, db, nome):
        self.db = db
        self.nome = nome
        self.ids: list = []
        self.colonne = None

    def select(self, colonne):
        self.colonne = colonne
        return self

    def in_(self, colonna, valori):
        assert colonna == "id"
        self.ids = list(valori)
        return self

    async def execute(self):
        self.db.letture.append((self.nome, self.colonne, self.ids))
        if len(self.db.letture) in self.db.blocchi_in_errore:
            raise APIError({"message": "timeout", "code": "57014", "hint": None, "details": None})
        return SimpleNamespace(data=[b for b in self.db.bandi if b["id"] in self.ids])


class Secondario:
    def __init__(self, bandi: list[dict]):
        self.bandi = bandi
        self.letture: list = []
        self.blocchi_in_errore: set[int] = set()

    def table(self, nome):
        return QueryCatalogo(self, nome)


def conteggi(**valori) -> dict:
    base = {"eventi": 1, "bandi": 1, "date_aggiornate": 0, "stati_aggiornati": 0,
            "assenti": 0, "errori": 0}
    base.update(valori)
    return base


# ----------------------------------------------------------------- regole


class TestRegole:
    async def test_data_e_ora_cambiate(self):
        primario = Primario([evento(1)])
        secondario = Secondario([bando(data_scadenza="2026-10-01", ora_scadenza="12:00:00")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(date_aggiornate=1)
        [(payload, filtri)] = primario.update
        # ora_fine non si tocca con un'ora di inizio; mai titolo e note
        assert payload == {"data": "2026-10-01", "tutto_il_giorno": False,
                           "ora_inizio": "12:00:00"}
        assert filtri == {"id": evento(1)["id"], "tipo": "bando", "bando_id": 42}

    async def test_ora_sparita_tutto_il_giorno_azzera_gli_orari(self):
        primario = Primario([evento(1, tutto_il_giorno=False, ora_inizio="10:00:00")])
        secondario = Secondario([bando(ora_scadenza="24:00")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(date_aggiornate=1)
        [(payload, _)] = primario.update
        assert payload == {"data": "2026-09-15", "tutto_il_giorno": True,
                           "ora_inizio": None, "ora_fine": None}

    async def test_solo_lo_stato(self):
        primario = Primario([evento(1)])
        secondario = Secondario([bando(stato_effettivo="sospeso")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(stati_aggiornati=1)
        assert [p for p, _ in primario.update] == [{"bando_stato": "sospeso"}]

    async def test_data_e_stato_insieme_un_solo_update(self):
        primario = Primario([evento(1, bando_stato=None)])
        secondario = Secondario([bando(data_scadenza="2026-11-30", stato_effettivo="aperto")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(date_aggiornate=1, stati_aggiornati=1)
        [(payload, _)] = primario.update
        assert payload == {"data": "2026-11-30", "tutto_il_giorno": True, "ora_inizio": None,
                           "ora_fine": None, "bando_stato": "aperto"}

    async def test_scadenza_null_solo_lo_stato(self):
        primario = Primario([evento(1, data="2026-01-01")])
        secondario = Secondario([bando(data_scadenza=None, ora_scadenza="12:00",
                                       stato_effettivo="chiuso")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(stati_aggiornati=1)
        assert [p for p, _ in primario.update] == [{"bando_stato": "chiuso"}]

    async def test_stato_null_nella_vista(self):
        primario = Primario([evento(1), evento(2, bando_id=43, bando_stato=None)])
        secondario = Secondario([bando(stato_effettivo=None), bando(43, stato_effettivo=None)])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=2, bandi=2, stati_aggiornati=1)
        assert primario.update == [({"bando_stato": None},
                                    {"id": evento(1)["id"], "tipo": "bando", "bando_id": 42})]

    async def test_invariata_confronto_normalizzato(self):
        # Stesso istante scritto in altro modo: nessuna scrittura.
        primario = Primario([evento(1, tutto_il_giorno=False, ora_inizio="12:00:00"),
                             evento(2, bando_id=43)])
        secondario = Secondario([bando(ora_scadenza="12:00:00+02"),
                                 bando(43, data_scadenza=" 2026-09-15 ", ora_scadenza="boh")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=2, bandi=2)
        assert primario.update == []

    async def test_bando_assente_non_toccato(self):
        primario = Primario([evento(1), evento(2, bando_id=99)])
        secondario = Secondario([bando(stato_effettivo="chiuso")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=2, bandi=2, stati_aggiornati=1, assenti=1)
        assert [f["bando_id"] for _, f in primario.update] == [42]

    async def test_stesso_bando_piu_scadenze(self):
        primario = Primario([evento(1), evento(2), evento(3, bando_stato="chiuso")])
        secondario = Secondario([bando(stato_effettivo="chiuso")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=3, bandi=1, stati_aggiornati=2)
        assert [ids for _, _, ids in secondario.letture] == [[42]]


# ----------------------------------------------------------------- modalità


class TestProva:
    async def test_prova_conta_senza_scrivere(self):
        def scenario():
            return (Primario([evento(1), evento(2, bando_id=43), evento(3, bando_id=99)]),
                    Secondario([bando(data_scadenza="2026-10-01"),
                                bando(43, stato_effettivo="revocato")]))

        primario, secondario = scenario()
        prova = await ca.passo(primario, secondario, scrivi=False)
        assert primario.update == []
        primario, secondario = scenario()
        scrittura = await ca.passo(primario, secondario, scrivi=True)
        assert prova == scrittura == conteggi(eventi=3, bandi=3, date_aggiornate=1,
                                              stati_aggiornati=1, assenti=1)


# ----------------------------------------------------------------- letture


class TestLetture:
    async def test_pagine_per_id_e_blocchi_di_100(self):
        # 2500 scadenze su 250 bandi: tre pagine (keyset), tre blocchi.
        eventi = [evento(n, bando_id=1000 + n % 250) for n in range(2500)]
        primario = Primario(eventi)
        secondario = Secondario([bando(1000 + n) for n in range(250)])
        report = await ca.passo(primario, secondario, scrivi=False)
        assert report == conteggi(eventi=2500, bandi=250)

        assert [(dopo, quante) for _, _, dopo, quante in primario.letture] == [
            (None, 1000), (evento(999)["id"], 1000), (evento(1999)["id"], 1000)]
        for colonne, filtri, _, _ in primario.letture:
            assert colonne == "id,bando_id,data,tutto_il_giorno,ora_inizio,bando_stato"
            assert filtri == {"tipo": "bando"}
        assert [len(ids) for _, _, ids in secondario.letture] == [100, 100, 50]
        for nome, colonne, _ in secondario.letture:
            assert nome == "bando_pubblico"
            assert colonne == "id,data_scadenza,ora_scadenza,stato_effettivo"

    async def test_pagina_piena_esatta_ne_legge_un_altra(self):
        primario = Primario([evento(n) for n in range(1000)])
        report = await ca.passo(primario, Secondario([bando()]), scrivi=False)
        assert report["eventi"] == 1000 and len(primario.letture) == 2

    async def test_nessuna_scadenza_nessuna_lettura_del_catalogo(self):
        secondario = Secondario([])
        report = await ca.passo(Primario([]), secondario, scrivi=True)
        assert report == conteggi(eventi=0, bandi=0)
        assert secondario.letture == []

    async def test_errore_sulle_scadenze_passo_interrotto(self, caplog):
        primario = Primario([evento(1)])
        primario.errore_lettura = True
        secondario = Secondario([bando()])
        with caplog.at_level(logging.WARNING, logger="bandofit.calendario_allineamento"):
            report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=0, bandi=0, errori=1)
        assert secondario.letture == [] and primario.update == []
        assert "57014" in caplog.text and "boom" not in caplog.text

    async def test_pagina_senza_id_passo_interrotto(self):
        primario = Primario([evento(n) for n in range(1000)])
        primario.pagine_senza_id = True
        report = await ca.passo(primario, Secondario([bando()]), scrivi=True)
        assert report["errori"] == 1 and primario.update == []

    async def test_errore_sul_catalogo_nessuna_scrittura(self, caplog):
        # Il primo blocco è leggibile e cambierebbe tutto, il secondo no: il
        # passo si chiude senza scrivere nulla e nessun bando vale come assente.
        eventi = [evento(n, bando_id=1000 + n) for n in range(150)]
        primario = Primario(eventi)
        secondario = Secondario([bando(1000 + n, stato_effettivo="chiuso") for n in range(150)])
        secondario.blocchi_in_errore = {2}
        with caplog.at_level(logging.WARNING, logger="bandofit.calendario_allineamento"):
            report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=150, bandi=150, errori=1)
        assert primario.update == []
        assert "senza scritture" in caplog.text and "timeout" not in caplog.text


# ----------------------------------------------------------------- scritture


class TestScritture:
    async def test_errore_su_una_riga_confinato(self, caplog):
        primario = Primario([evento(1), evento(2)])
        primario.errori_update = {evento(1)["id"]}
        secondario = Secondario([bando(stato_effettivo="sospeso")])
        with caplog.at_level(logging.WARNING, logger="bandofit.calendario_allineamento"):
            report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=2, stati_aggiornati=1, errori=1)
        assert [f["id"] for _, f in primario.update] == [evento(1)["id"], evento(2)["id"]]
        assert "23514" in caplog.text
        assert "dettagli del db" not in caplog.text and "riga segreta" not in caplog.text

    async def test_riga_spostata_nel_frattempo_non_contata(self):
        primario = Primario([evento(1), evento(2)])
        primario.spostate = {evento(2)["id"]}
        secondario = Secondario([bando(stato_effettivo="sospeso")])
        report = await ca.passo(primario, secondario, scrivi=True)
        assert report == conteggi(eventi=2, stati_aggiornati=1)
        assert len(primario.update) == 2

    async def test_idempotente(self):
        primario = Primario([evento(1, tutto_il_giorno=False, ora_inizio="09:00:00")])
        secondario = Secondario([bando(data_scadenza="2026-12-01", stato_effettivo="sospeso")])
        primo = await ca.passo(primario, secondario, scrivi=True)
        assert primo == conteggi(date_aggiornate=1, stati_aggiornati=1)
        secondo = await ca.passo(primario, secondario, scrivi=True)
        assert secondo == conteggi()
        assert len(primario.update) == 1
