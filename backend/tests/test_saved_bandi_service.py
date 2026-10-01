"""Test dei bandi salvati: snapshot, idempotenza, cap, merge vivo/sparito —
con fake di primario e catalogo secondario."""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from postgrest.exceptions import APIError

from app.api.deps import ActiveCompany
from app.core.errors import BadRequestError, BandoRitiratoError, NotFoundError
from app.services import saved_bandi_service

USER_ID = "a0000000-0000-0000-0000-000000000001"
COMPANY = "c0000000-0000-0000-0000-000000000001"


def errore_postgrest(codice: str) -> APIError:
    return APIError({"message": "errore del catalogo", "code": codice, "hint": None,
                     "details": None})


def _active(company_id: str | None = None, is_multi: bool = False) -> ActiveCompany:
    """Default = non-Advisor (is_multi False): overlay company NULL, comportamento
    legacy. Con is_multi True le righe sono scopate sulla company attiva."""
    return ActiveCompany(company_id=company_id, owner_id=USER_ID, editable=True, is_multi=is_multi)


# ------------------------------------------------------------------- finti

class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self._op = "select"
        self._payload = None
        self.filters: dict = {}

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def gte(self, column, value):
        self.filters[f"{column}__gte"] = value
        return self

    def lt(self, column, value):
        self.filters[f"{column}__lt"] = value
        return self

    def in_(self, column, values):
        self.filters[f"{column}__in"] = list(values)
        return self

    @property
    def not_(self):
        return self

    def is_(self, column, value):
        self.filters[f"{column}__not_is"] = value
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, start, end):
        self.filters["__range"] = (start, end)
        return self

    def limit(self, *args):
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._op, self._payload, dict(self.filters)))
        if self._op == "select":
            source = self._owner.selects.get(self._table, [])
            rows = source(self.filters) if callable(source) else source
            return SimpleNamespace(data=rows, count=len(rows))
        if self._op == "insert":
            if self._table in self._owner.insert_fail_unique:
                from postgrest.exceptions import APIError as PgError

                raise PgError({
                    "message": "duplicate key value violates unique constraint",
                    "code": "23505", "hint": None, "details": None,
                })
            now = datetime.now(timezone.utc).isoformat()
            return SimpleNamespace(
                data=[{
                    "id": f"gen-{self._table}-{len(self._owner.ops)}",
                    "created_at": now,
                    "updated_at": now,
                    **(self._payload or {}),
                }],
                count=None,
            )
        if self._op == "update":
            if self._table in self._owner.update_returns_empty:
                return SimpleNamespace(data=[], count=None)
            # Come PostgREST (return=representation): la riga COMPLETA
            # aggiornata, non il solo payload.
            source = self._owner.selects.get(self._table, [])
            rows = source(self.filters) if callable(source) else source
            base = rows[0] if rows else {}
            return SimpleNamespace(data=[{**base, **(self._payload or {})}], count=None)
        if self._op == "delete":
            if self._table in self._owner.delete_returns_empty:
                return SimpleNamespace(data=[], count=None)
            return SimpleNamespace(data=[{"id": "deleted"}], count=None)
        return SimpleNamespace(data=[], count=None)


class FakeDb:
    """Fa sia da primario che da secondario: risposte per tabella in
    `selects` (lista fissa o callable sui filtri registrati)."""

    def __init__(self, selects: dict | None = None):
        self.selects = selects or {}
        self.ops: list = []
        self.insert_fail_unique: set[str] = set()
        self.update_returns_empty: set[str] = set()
        self.delete_returns_empty: set[str] = set()

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def ops_for(self, table: str, op: str) -> list:
        return [(payload, filters) for t, o, payload, filters in self.ops if t == table and o == op]


BANDO_VIVO = {
    "id": 42,
    "slug": "bando-x",
    "titolo": "Titolo lungo del bando X",
    "titolo_breve": "Bando X",
    "descrizione_breve": "desc",
    "stato_bando": "in apertura prossimamente",
    "stato_effettivo": "aperto",
    "livello": "flash_bando",
    "data_pubblicazione": "2026-06-01",
    "data_apertura": None,
    "data_scadenza": "2026-09-15",
    "importo_totale_eur": 1000,
    "importo_max_per_progetto_eur": None,
    "ente_erogatore": "Regione",
    "tipologie_bando": {"id": 1, "nome": "Bandi regionali / locali"},
    "modalita_erogazione": {"id": 2, "nome": "Fondo perduto"},
    "bando_regioni": [{"regioni": {"id": 12, "nome": "Lazio"}}],
}


def saved_row(bando_id: int = 42, **overrides) -> dict:
    row = {
        "id": f"s-{bando_id}",
        "bando_id": bando_id,
        "bando_slug": f"bando-{bando_id}",
        "bando_titolo": f"Bando {bando_id}",
        "data_scadenza": "2026-09-15",
        "stato_bando": "aperto",
        "created_at": "2026-07-07T10:00:00+00:00",
    }
    row.update(overrides)
    return row


def catalogo_per_id(*bandi: dict):
    """Catalogo in cui lo slug richiesto non c'è più (miss) ma i master si
    trovano per id: la seconda lettura di carica_per_slug (R0-b)."""

    def bando(filters):
        if "slug" in filters:
            return []
        return [b for b in bandi if b["id"] == filters.get("id")]

    return bando


STORICO_301 = [{"slug": "vecchio-slug", "bando_id": 42, "esito": "301"}]


# ---------------------------------------------------------------- salvataggio

class TestSaveBando:
    async def test_inserisce_lo_snapshot_corretto(self):
        primary = FakeDb({"saved_bandi": [], "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        item = await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "bando-x")

        [(inserted, _)] = primary.ops_for("saved_bandi", "insert")
        assert inserted == {
            "user_id": USER_ID,
            "company_profile_id": None,  # non-Advisor: overlay NULL (legacy)
            "bando_id": 42,
            "bando_slug": "bando-x",
            "bando_titolo": "Bando X",  # titolo_breve preferito
            "data_scadenza": "2026-09-15",
            # Lo stato dello snapshot è stato_effettivo, nella colonna storica.
            "stato_bando": "aperto",
        }
        assert item.disponibile is True
        assert item.in_calendario is False
        assert item.bando.slug == "bando-x"
        assert item.bando.stato_effettivo == "aperto"
        assert item.bando.stato_bando == "in apertura prossimamente"

    async def test_snapshot_con_stato_effettivo_nullo(self):
        primary = FakeDb({"saved_bandi": [], "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "stato_effettivo": None}]})
        await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "bando-x")
        [(inserted, _)] = primary.ops_for("saved_bandi", "insert")
        assert inserted["stato_bando"] is None

    async def test_fallback_del_titolo(self):
        senza_breve = {**BANDO_VIVO, "titolo_breve": None}
        secondary = FakeDb({"bando_pubblico": [senza_breve]})
        primary = FakeDb({"saved_bandi": [], "calendar_events": []})
        await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "bando-x")
        [(inserted, _)] = primary.ops_for("saved_bandi", "insert")
        assert inserted["bando_titolo"] == "Titolo lungo del bando X"

        senza_titoli = {**BANDO_VIVO, "titolo_breve": None, "titolo": None}
        secondary = FakeDb({"bando_pubblico": [senza_titoli]})
        primary = FakeDb({"saved_bandi": [], "calendar_events": []})
        await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "bando-x")
        [(inserted, _)] = primary.ops_for("saved_bandi", "insert")
        assert inserted["bando_titolo"] == "bando-x"

    async def test_bando_inesistente_niente_insert(self):
        primary = FakeDb({"saved_bandi": []})
        secondary = FakeDb({"bando_pubblico": []})
        with pytest.raises(NotFoundError):
            await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "sparito")
        assert not primary.ops_for("saved_bandi", "insert")

    async def test_idempotente_se_gia_salvato(self):
        def saved_bandi(filters):
            if "bando_id" in filters:  # lookup della riga esistente
                return [saved_row(42)]
            return []

        primary = FakeDb({"saved_bandi": saved_bandi, "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        item = await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "bando-x")
        assert not primary.ops_for("saved_bandi", "insert")
        assert item.disponibile is True

    async def test_cap_raggiunto(self):
        def saved_bandi(filters):
            if "bando_id" in filters:
                return []  # non ancora salvato
            return [{"id": i} for i in range(saved_bandi_service.MAX_SAVED)]

        primary = FakeDb({"saved_bandi": saved_bandi})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        with pytest.raises(BadRequestError):
            await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "bando-x")
        assert not primary.ops_for("saved_bandi", "insert")

    async def test_corsa_su_indice_unico_rilegge(self):
        calls = {"n": 0}

        def saved_bandi(filters):
            if "bando_id" in filters:
                calls["n"] += 1
                return [] if calls["n"] == 1 else [saved_row(42)]
            return []

        primary = FakeDb({"saved_bandi": saved_bandi, "calendar_events": []})
        primary.insert_fail_unique.add("saved_bandi")
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        item = await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "bando-x")
        assert item.bando.id == 42  # la riga vinta dalla corsa viene ritornata

    @pytest.mark.parametrize(
        "risoluzione",
        [
            {"bando_slug_storico": STORICO_301},
            {"bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "bando-x"}]},
        ],
        ids=["storico-301", "fusione"],
    )
    async def test_slug_spostato_salva_il_master(self, risoluzione):
        primary = FakeDb({"saved_bandi": [], "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": catalogo_per_id(BANDO_VIVO), **risoluzione})
        item = await saved_bandi_service.save_bando(
            primary, secondary, USER_ID, _active(), "vecchio-slug"
        )
        [(inserted, _)] = primary.ops_for("saved_bandi", "insert")
        assert inserted["bando_id"] == 42
        assert inserted["bando_slug"] == "bando-x"  # canonico, non quello richiesto
        assert item.disponibile is True
        assert item.bando.slug == "bando-x"

    async def test_master_gia_salvato_ritorna_la_riga_esistente(self):
        # L'idempotenza vale sul master: nessun secondo insert (l'indice unico
        # non viene mai sollecitato) e nemmeno il conteggio per il limite.
        def saved_bandi(filters):
            if filters.get("bando_id") == 42:
                return [saved_row(42, bando_slug="bando-x")]
            return []

        primary = FakeDb({"saved_bandi": saved_bandi, "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": catalogo_per_id(BANDO_VIVO), "bando_slug_storico": STORICO_301})
        item = await saved_bandi_service.save_bando(
            primary, secondary, USER_ID, _active(), "vecchio-slug"
        )
        assert not primary.ops_for("saved_bandi", "insert")
        [(_, filters)] = primary.ops_for("saved_bandi", "select")
        assert filters["bando_id"] == 42
        assert item.bando.id == 42 and item.bando.slug == "bando-x"

    async def test_slug_ritirato_410_primario_mai_interrogato(self):
        primary = FakeDb({"saved_bandi": []})
        secondary = FakeDb({
            "bando_pubblico": [],
            "bando_slug_storico": [{"slug": "ritirato", "bando_id": None, "esito": "410"}],
        })
        with pytest.raises(BandoRitiratoError):
            await saved_bandi_service.save_bando(primary, secondary, USER_ID, _active(), "ritirato")
        assert primary.ops == []


class TestRemoveBando:
    async def test_delete_con_entrambi_i_filtri(self):
        primary = FakeDb()
        await saved_bandi_service.remove_bando(primary, USER_ID, _active(), 42)
        [(_, filters)] = primary.ops_for("saved_bandi", "delete")
        assert filters["user_id"] == USER_ID
        assert filters["bando_id"] == 42


# --------------------------------------------------------------------- lista

class TestListSaved:
    async def test_merge_vivi_e_spariti_in_ordine_di_salvataggio(self):
        rows = [saved_row(42), saved_row(99, bando_titolo="Bando sparito")]
        primary = FakeDb({"saved_bandi": rows, "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})  # il 99 non c'è più

        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        assert page.total == 2
        vivo, sparito = page.items
        assert vivo.disponibile is True and vivo.bando.id == 42
        assert vivo.bando.ente_erogatore == "Regione"  # dati vivi dal catalogo
        assert vivo.bando.stato_effettivo == "aperto"
        assert sparito.disponibile is False
        assert sparito.bando.id == 99
        assert sparito.bando.titolo == "Bando sparito"  # fallback dallo snapshot
        assert str(sparito.bando.data_scadenza) == "2026-09-15"
        assert sparito.bando.regioni == []
        # snapshot: stato salvato in stato_bando, nessuno stato calcolato
        assert sparito.bando.stato_bando == "aperto"
        assert sparito.bando.stato_effettivo is None

        # una sola query alla vista del catalogo, con gli id della pagina
        assert [table for table, *_ in secondary.ops] == ["bando_pubblico", "bando_fusione"]
        [(_, filters)] = secondary.ops_for("bando_pubblico", "select")
        assert filters["id__in"] == [42, 99]
        assert filters["slug__not_is"] == "null"
        assert "stato_processing" not in filters

    async def test_stati_nuovi_e_miss_non_rompono_la_lista(self):
        # Contratto DB bandi §7 (R0-a): stati che oggi non esistono (revocato,
        # sospeso) sia dal catalogo sia dallo snapshot, più un miss.
        rows = [saved_row(42), saved_row(99, stato_bando="sospeso")]
        primary = FakeDb({"saved_bandi": rows, "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [
            {**BANDO_VIVO, "stato_bando": "revocato", "stato_effettivo": "revocato"}
        ]})

        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        vivo, mancante = page.items
        assert vivo.disponibile is True
        assert vivo.bando.stato_bando == "revocato"
        assert vivo.bando.stato_effettivo == "revocato"
        assert mancante.disponibile is False
        assert mancante.bando.titolo == "Bando 99"  # dallo snapshot
        assert mancante.bando.stato_bando == "sospeso"

    async def test_stato_da_verificare_dal_catalogo(self):
        # Contratto DB bandi §4.1: la colonna arriva per nome dalla select
        # dell'elenco; la card dallo snapshot non ha motivo.
        assert "stato_da_verificare" in saved_bandi_service.LIST_SELECT.split(",")
        rows = [saved_row(42), saved_row(99)]
        primary = FakeDb({"saved_bandi": rows, "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [
            {**BANDO_VIVO, "stato_da_verificare": "senza_conferma"}
        ]})

        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        vivo, sparito = page.items
        assert vivo.bando.stato_effettivo == "aperto"
        assert vivo.bando.stato_da_verificare == "senza_conferma"
        assert sparito.bando.stato_da_verificare is None

    async def test_stato_da_verificare_sconosciuto_non_rompe_la_lista(self):
        primary = FakeDb({"saved_bandi": [saved_row(42)], "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "stato_da_verificare": "nuovo"}]})

        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        [vivo] = page.items
        assert vivo.disponibile is True
        assert vivo.bando.stato_da_verificare is None

    async def test_spariti_per_fusione_rimandano_al_master(self):
        rows = [saved_row(42), saved_row(99), saved_row(100), saved_row(101)]
        primary = FakeDb({"saved_bandi": rows, "calendar_events": []})
        secondary = FakeDb({
            "bando_pubblico": [BANDO_VIVO],
            "bando_fusione": [
                {"bando_id": 99, "master_id": 42, "master_slug": "bando-x"},
                {"bando_id": 100, "master_id": 43, "master_slug": None},
            ],
        })

        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        vivo, fuso, fuso_senza_slug, sparito = page.items
        assert vivo.disponibile is True and vivo.slug_aggiornato is None
        assert fuso.disponibile is False
        assert fuso.slug_aggiornato == "bando-x"
        # la card resta quella dello snapshot: id salvato, rimozione per id intatta
        assert fuso.bando.id == 99 and fuso.bando.slug == "bando-99"
        assert fuso_senza_slug.disponibile is False and fuso_senza_slug.slug_aggiornato is None
        assert sparito.disponibile is False and sparito.slug_aggiornato is None

        # una sola risoluzione, per i soli id mancanti
        [(_, filters)] = secondary.ops_for("bando_fusione", "select")
        assert filters["bando_id__in"] == [99, 100, 101]
        # nessuna scrittura sul primario (la rimappatura è della fase c)
        assert {op for _, op, _, _ in primary.ops} == {"select"}

    async def test_tutti_vivi_nessuna_query_di_risoluzione(self):
        primary = FakeDb({"saved_bandi": [saved_row(42)], "calendar_events": []})
        secondary = FakeDb({
            "bando_pubblico": [BANDO_VIVO],
            "bando_fusione": [{"bando_id": 42, "master_id": 1, "master_slug": "altro"}],
        })
        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        assert page.items[0].slug_aggiornato is None
        assert [table for table, *_ in secondary.ops] == ["bando_pubblico"]

    async def test_pagina_vuota_salta_il_secondario(self):
        primary = FakeDb({"saved_bandi": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        assert page.items == [] and page.total == 0
        assert not secondary.ops  # mai interrogato

    async def test_scoping_utente_su_tutte_le_letture(self):
        # Il filtro di tenancy deve esserci DAVVERO: mai i salvati di altri.
        primary = FakeDb({"saved_bandi": [saved_row(42)], "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        await saved_bandi_service.saved_ids(primary, USER_ID, _active())
        for _, filters in primary.ops_for("saved_bandi", "select"):
            assert filters["user_id"] == USER_ID

    async def test_in_calendario(self):
        primary = FakeDb({
            "saved_bandi": [saved_row(42)],
            "calendar_events": [{"bando_id": 42}],
        })
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)
        assert page.items[0].in_calendario is True
        # il lookup filtra su tipo='bando' e utente
        [(_, filters)] = primary.ops_for("calendar_events", "select")
        assert filters["tipo"] == "bando"
        assert filters["user_id"] == USER_ID

    async def test_paginazione(self):
        primary = FakeDb({"saved_bandi": [saved_row(42)]})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 3, 10)
        [(_, filters)] = primary.ops_for("saved_bandi", "select")
        assert filters["__range"] == (20, 29)

    async def test_pagina_oltre_il_totale_vuota_con_il_totale_reale(self):
        # Il totale è calato fra due letture (un'altra scheda ha rimosso dei
        # preferiti): il primario rifiuta l'intervallo, l'elenco risponde
        # vuoto con il totale reale e rilegge il conteggio con una sola
        # richiesta senza offset. Il catalogo non si interroga.
        salvati = [saved_row(42), saved_row(43), saved_row(44)]

        def come_postgrest(filters):
            intervallo = filters.get("__range")
            if intervallo is not None and intervallo[0] > len(salvati):
                raise errore_postgrest("PGRST103")
            return salvati

        primary = FakeDb({"saved_bandi": come_postgrest})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        page = await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 5, 10)
        assert (page.items, page.total, page.total_pages, page.page) == ([], 3, 1, 5)
        assert [f.get("__range") for _, f in primary.ops_for("saved_bandi", "select")] == [
            (40, 49), None
        ]
        assert not secondary.ops

    @pytest.mark.parametrize("codice", ["42703", "42501", "PGRST205"])
    async def test_errore_di_contratto_del_catalogo_card_dagli_snapshot(self, codice, caplog):
        # Un cambio di contratto del catalogo (colonna o vista sparite,
        # permesso negato) non dà 5xx: le card vengono dallo snapshot, non
        # disponibili, e la risoluzione dei fusi non si interroga.
        rows = [saved_row(42), saved_row(99)]
        primary = FakeDb({"saved_bandi": rows, "calendar_events": [{"bando_id": 42}]})

        def catalogo_rotto(filters):
            raise errore_postgrest(codice)

        secondary = FakeDb({"bando_pubblico": catalogo_rotto, "bando_fusione": []})
        with caplog.at_level(logging.WARNING, logger="bandofit.saved_bandi"):
            page = await saved_bandi_service.list_saved(
                primary, secondary, USER_ID, _active(), 1, 20
            )
        assert page.total == 2 and [i.bando.id for i in page.items] == [42, 99]
        assert all(i.disponibile is False and i.slug_aggiornato is None for i in page.items)
        assert page.items[0].bando.titolo == "Bando 42" and page.items[0].in_calendario is True
        assert [table for table, *_ in secondary.ops] == ["bando_pubblico"]
        [record] = caplog.records
        assert codice in record.getMessage() and "errore del catalogo" not in caplog.text

    @pytest.mark.parametrize(
        "guasto", [errore_postgrest("57014"), errore_postgrest("PGRST000"),
                   httpx.ReadTimeout("timeout")],
        ids=["timeout_sql", "connessione", "timeout_http"],
    )
    async def test_guasto_vero_del_catalogo_si_propaga(self, guasto):
        primary = FakeDb({"saved_bandi": [saved_row(42)], "calendar_events": []})

        def catalogo_giu(filters):
            raise guasto

        secondary = FakeDb({"bando_pubblico": catalogo_giu})
        with pytest.raises(type(guasto)):
            await saved_bandi_service.list_saved(primary, secondary, USER_ID, _active(), 1, 20)


class TestSavedIds:
    async def test_ordinati(self):
        primary = FakeDb({"saved_bandi": [{"bando_id": 99}, {"bando_id": 7}, {"bando_id": 42}]})
        out = await saved_bandi_service.saved_ids(primary, USER_ID, _active())
        assert out.bando_ids == [7, 42, 99]


class TestOverlayAzienda:
    """Segregazione Gruppo A: un Advisor scopa i preferiti sulla company attiva;
    un non-Advisor resta a company_profile_id NULL (legacy)."""

    async def test_advisor_scrive_e_legge_per_company(self):
        primary = FakeDb({"saved_bandi": [], "calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        active = _active(company_id=COMPANY, is_multi=True)
        await saved_bandi_service.save_bando(primary, secondary, USER_ID, active, "bando-x")
        # scrittura: overlay = company attiva
        [(inserted, _)] = primary.ops_for("saved_bandi", "insert")
        assert inserted["company_profile_id"] == COMPANY
        # letture: filtro eq sulla company (non `is null`)
        await saved_bandi_service.saved_ids(primary, USER_ID, active)
        for _, filters in primary.ops_for("saved_bandi", "select"):
            assert filters.get("company_profile_id") == COMPANY
            assert "company_profile_id__not_is" not in filters

    async def test_non_advisor_resta_null(self):
        primary = FakeDb({"saved_bandi": [saved_row(42)], "calendar_events": []})
        await saved_bandi_service.saved_ids(primary, USER_ID, _active())
        [(_, filters)] = primary.ops_for("saved_bandi", "select")
        # legacy: filtro `is null`, mai un eq sulla company
        assert filters.get("company_profile_id__not_is") == "null"
        assert "company_profile_id" not in filters
