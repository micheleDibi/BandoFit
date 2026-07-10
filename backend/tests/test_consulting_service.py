"""Slot di disponibilità: validazioni, mappatura errori RPC/constraint,
flag `prenotato` derivato dai booking confermati."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.consulting import SlotIn
from app.services import consulting_service

PROGETTISTA = "aaaaaaaa-0000-0000-0000-000000000020"
SLOT_ID = "bbbbbbbb-0000-0000-0000-000000000021"


def tra(minuti: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minuti)


def slot_in(start_min: int = 60, durata_min: int = 30) -> SlotIn:
    return SlotIn(inizio=tra(start_min), fine=tra(start_min + durata_min))


class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self._action = "select"
        self._payload = None
        self.filters: list = []

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self._action = "insert"
        self._payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def gte(self, column, value):
        self.filters.append(("gte", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def order(self, column, desc=False):
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._action, self._payload, list(self.filters)))
        error = self._owner.errors.get((self._table, self._action))
        if error is not None:
            raise error
        if self._action == "insert":
            return SimpleNamespace(data=[{**self._payload, "id": SLOT_ID}])
        return SimpleNamespace(data=self._owner.selects.get(self._table, []))


class FakeRpc:
    def __init__(self, owner, fn: str, params: dict):
        self._owner = owner
        self._fn = fn
        self._params = params

    async def execute(self):
        self._owner.rpc_calls.append((self._fn, self._params))
        error = self._owner.rpc_errors.get(self._fn)
        if error is not None:
            raise error
        return SimpleNamespace(data=None)


class FakePrimary:
    def __init__(self, selects: dict | None = None):
        self.selects = selects or {}
        self.ops: list = []
        self.rpc_calls: list = []
        self.errors: dict = {}
        self.rpc_errors: dict = {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, fn: str, params: dict) -> FakeRpc:
        return FakeRpc(self, fn, params)


def api_error(code: str = "P0001", details: str | None = None) -> APIError:
    return APIError({"message": "dal db", "code": code, "details": details, "hint": None})


class TestValidazioni:
    async def test_fine_prima_dellinizio(self):
        data = SlotIn(inizio=tra(120), fine=tra(60))
        with pytest.raises(BadRequestError):
            await consulting_service.create_slot(FakePrimary(), PROGETTISTA, data)

    async def test_durata_minima(self):
        with pytest.raises(BadRequestError):
            await consulting_service.create_slot(
                FakePrimary(), PROGETTISTA, slot_in(durata_min=10)
            )

    async def test_durata_massima(self):
        with pytest.raises(BadRequestError):
            await consulting_service.create_slot(
                FakePrimary(), PROGETTISTA, slot_in(durata_min=13 * 60)
            )

    async def test_slot_nel_passato(self):
        with pytest.raises(BadRequestError):
            await consulting_service.create_slot(
                FakePrimary(), PROGETTISTA, slot_in(start_min=-60)
            )

    def test_orario_senza_fuso_rifiutato_dallo_schema(self):
        with pytest.raises(ValueError):
            SlotIn(inizio=datetime(2026, 8, 1, 10, 0), fine=datetime(2026, 8, 1, 11, 0))


class TestCreate:
    async def test_inserimento(self):
        primary = FakePrimary()
        out = await consulting_service.create_slot(primary, PROGETTISTA, slot_in())
        [(table, action, payload, _)] = primary.ops
        assert (table, action) == ("availability_slots", "insert")
        assert payload["progettista_id"] == PROGETTISTA
        assert out.prenotato is False

    async def test_sovrapposizione_mappata_su_conflict(self):
        primary = FakePrimary()
        primary.errors[("availability_slots", "insert")] = api_error(code="23P01")
        with pytest.raises(ConflictError):
            await consulting_service.create_slot(primary, PROGETTISTA, slot_in())


LIBERO_ID = "cccccccc-0000-0000-0000-000000000022"
OCCUPATO_ID = "dddddddd-0000-0000-0000-000000000023"


class TestList:
    async def test_flag_prenotato_derivato(self):
        primary = FakePrimary(
            selects={
                "availability_slots": [
                    {"id": LIBERO_ID, "inizio": tra(60).isoformat(), "fine": tra(90).isoformat()},
                    {"id": OCCUPATO_ID, "inizio": tra(120).isoformat(), "fine": tra(150).isoformat()},
                ],
                "consultation_bookings": [{"slot_id": OCCUPATO_ID}],
            }
        )
        slots = await consulting_service.list_slots(primary, PROGETTISTA)
        assert [(str(s.id), s.prenotato) for s in slots] == [
            (LIBERO_ID, False),
            (OCCUPATO_ID, True),
        ]
        # I booking si filtrano sui soli slot in pagina e sullo stato confermata.
        bookings_op = [op for op in primary.ops if op[0] == "consultation_bookings"][0]
        assert ("in", "slot_id", [LIBERO_ID, OCCUPATO_ID]) in bookings_op[3]
        assert ("eq", "stato", "confermata") in bookings_op[3]

    async def test_senza_slot_non_interroga_i_booking(self):
        primary = FakePrimary(selects={"availability_slots": []})
        assert await consulting_service.list_slots(primary, PROGETTISTA) == []
        assert [op[0] for op in primary.ops] == ["availability_slots"]


class TestUpdateDelete:
    async def test_update_passa_dalla_rpc(self):
        primary = FakePrimary()
        data = slot_in()
        out = await consulting_service.update_slot(primary, PROGETTISTA, SLOT_ID, data)
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_update_slot"
        assert params["p_slot_id"] == SLOT_ID
        assert params["p_progettista_id"] == PROGETTISTA
        assert out.inizio == data.inizio

    async def test_update_slot_prenotato(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_update_slot"] = api_error(details="slot_booked")
        with pytest.raises(ConflictError):
            await consulting_service.update_slot(primary, PROGETTISTA, SLOT_ID, slot_in())

    async def test_update_slot_altrui(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_update_slot"] = api_error(details="slot_not_found")
        with pytest.raises(NotFoundError):
            await consulting_service.update_slot(primary, PROGETTISTA, SLOT_ID, slot_in())

    async def test_delete_passa_dalla_rpc(self):
        primary = FakePrimary()
        await consulting_service.delete_slot(primary, PROGETTISTA, SLOT_ID)
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_delete_slot"
        assert params == {"p_slot_id": SLOT_ID, "p_progettista_id": PROGETTISTA}

    async def test_delete_slot_prenotato(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_delete_slot"] = api_error(details="slot_booked")
        with pytest.raises(ConflictError):
            await consulting_service.delete_slot(primary, PROGETTISTA, SLOT_ID)
