"""Notifiche in-app: fan-out idempotente che non solleva mai, lista con
conteggio non lette, letture puntuali e globali."""

from types import SimpleNamespace

import pytest

from app.core.errors import BadRequestError
from app.schemas.notification import MarkReadIn
from app.services import notification_service

UTENTE = "aaaaaaaa-0000-0000-0000-000000000010"


class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self._action = "select"
        self._payload = None
        self._kwargs: dict = {}
        self.filters: list = []

    def select(self, columns="*", count=None):
        self._kwargs["count"] = count
        return self

    def upsert(self, payload, *, on_conflict="", ignore_duplicates=False):
        self._action = "upsert"
        self._payload = payload
        self._kwargs.update(on_conflict=on_conflict, ignore_duplicates=ignore_duplicates)
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def order(self, column, desc=False):
        return self

    def range(self, start, end):
        self._kwargs["range"] = (start, end)
        return self

    def limit(self, n):
        return self

    async def execute(self):
        self._owner.ops.append(
            (self._table, self._action, self._payload, list(self.filters), dict(self._kwargs))
        )
        if self._owner.raise_on_execute:
            raise RuntimeError("db KO")
        if self._action == "select":
            has_unread_filter = ("is", "read_at", "null") in self.filters
            if has_unread_filter:
                return SimpleNamespace(data=[], count=self._owner.unread_count)
            return SimpleNamespace(data=self._owner.rows, count=self._owner.total)
        return SimpleNamespace(data=[])


class FakePrimary:
    def __init__(self, rows=None, total=0, unread_count=0):
        self.rows = rows or []
        self.total = total
        self.unread_count = unread_count
        self.ops: list = []
        self.raise_on_execute = False

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)


def notifica(i: int, read: bool = False) -> dict:
    return {
        "id": i,
        "tipo": "consulenza.nuova_richiesta",
        "titolo": f"Notifica {i}",
        "corpo": None,
        "url": f"/app/consulenze/{i}",
        "read_at": "2026-07-10T10:00:00+00:00" if read else None,
        "created_at": "2026-07-10T09:00:00+00:00",
    }


class TestNotify:
    async def test_fanout_con_dedup(self):
        primary = FakePrimary()
        await notification_service.notify(
            primary,
            [UTENTE, "bbbbbbbb-0000-0000-0000-000000000011"],
            tipo="consulenza.nuova_richiesta",
            titolo="Nuova richiesta di consulto",
            url="/app/progettista/richieste",
            dedup_key="richiesta:42",
        )
        [(table, action, payload, _, kwargs)] = primary.ops
        assert (table, action) == ("notifications", "upsert")
        assert len(payload) == 2
        assert all(row["dedup_key"] == "richiesta:42" for row in payload)
        # Il constraint pieno fa da arbiter: senza queste opzioni il retry
        # del fan-out solleverebbe invece di ignorare i già recapitati.
        assert kwargs["on_conflict"] == "user_id,dedup_key"
        assert kwargs["ignore_duplicates"] is True

    async def test_senza_destinatari_non_tocca_il_db(self):
        primary = FakePrimary()
        await notification_service.notify(
            primary, [], tipo="x", titolo="y", dedup_key="z"
        )
        assert primary.ops == []

    async def test_guasto_db_non_solleva(self):
        primary = FakePrimary()
        primary.raise_on_execute = True
        await notification_service.notify(
            primary, [UTENTE], tipo="x", titolo="y", dedup_key="z"
        )


class TestList:
    async def test_pagina_con_conteggio_non_lette(self):
        primary = FakePrimary(
            rows=[notifica(2), notifica(1, read=True)], total=42, unread_count=7
        )
        page = await notification_service.list_notifications(primary, UTENTE, 1, 20)
        assert page.total == 42
        assert page.total_pages == 3
        assert page.non_lette == 7
        assert [n.id for n in page.items] == [2, 1]
        assert page.items[0].read_at is None
        assert page.items[1].read_at is not None

    async def test_offset_della_pagina(self):
        primary = FakePrimary()
        await notification_service.list_notifications(primary, UTENTE, 3, 10)
        list_op = primary.ops[0]
        assert list_op[4]["range"] == (20, 29)


class TestMarkRead:
    async def test_tutte(self):
        primary = FakePrimary()
        await notification_service.mark_read(primary, UTENTE, MarkReadIn(all=True))
        [(table, action, payload, filters, _)] = primary.ops
        assert (table, action) == ("notifications", "update")
        assert payload.keys() == {"read_at"}
        assert ("eq", "user_id", UTENTE) in filters
        assert ("is", "read_at", "null") in filters
        assert not any(f[0] == "in" for f in filters)

    async def test_puntuale(self):
        primary = FakePrimary()
        await notification_service.mark_read(primary, UTENTE, MarkReadIn(ids=[3, 5]))
        [(_, _, _, filters, _)] = primary.ops
        assert ("in", "id", [3, 5]) in filters

    async def test_input_vuoto_rifiutato(self):
        with pytest.raises(ValueError):
            MarkReadIn()

    async def test_guardia_anche_nel_service(self):
        primary = FakePrimary()
        data = MarkReadIn.model_construct(all=False, ids=None)
        with pytest.raises(BadRequestError):
            await notification_service.mark_read(primary, UTENTE, data)
        assert primary.ops == []
