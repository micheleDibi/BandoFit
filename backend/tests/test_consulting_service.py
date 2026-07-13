"""Consulenze: slot (validazioni, mappatura errori), flusso richiesta →
proposta → assegnazione → prenotazione (guardie, eventi, audit) e visibilità
partial/full del progettista."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.schemas.consulting import MAX_OCCORRENZE_SERIE, SerieIn, SlotIn
from app.schemas.openapi_data import DossierResponse
from app.services import consulting_service

PROGETTISTA = "aaaaaaaa-0000-0000-0000-000000000020"
SLOT_ID = "bbbbbbbb-0000-0000-0000-000000000021"
LIBERO_ID = "cccccccc-0000-0000-0000-000000000022"
OCCUPATO_ID = "dddddddd-0000-0000-0000-000000000023"
TITOLARE = "eeeeeeee-0000-0000-0000-000000000024"
REQUEST_ID = "ffffffff-0000-0000-0000-000000000025"
PROPOSAL_ID = "99999999-0000-0000-0000-000000000026"
AI_CHECK_ID = "88888888-0000-0000-0000-000000000027"
COMPANY_ID = "77777777-0000-0000-0000-000000000028"
BOOKING_ID = "66666666-0000-0000-0000-000000000029"
SERIE_ID = "55555555-0000-0000-0000-000000000030"

USER = {"id": TITOLARE, "role": "cliente"}
PROG_USER = {"id": PROGETTISTA, "role": "progettista"}


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

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def gte(self, column, value):
        self.filters.append(("gte", column, value))
        return self

    def gt(self, column, value):
        self.filters.append(("gt", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def order(self, column, desc=False):
        return self

    def limit(self, n):
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._action, self._payload, list(self.filters)))
        error = self._owner.errors.get((self._table, self._action))
        if error is not None:
            raise error
        if self._action == "insert":
            row = {"id": self._owner.insert_id, "created_at": tra(0).isoformat(), **self._payload}
            return SimpleNamespace(data=[row])
        if self._action == "update":
            return SimpleNamespace(data=self._owner.updates.get(self._table, [self._payload]))
        queue = self._owner.select_queues.get(self._table)
        if queue:
            return SimpleNamespace(data=queue.pop(0))
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
        return SimpleNamespace(data=self._owner.rpc_results.get(self._fn))


class FakePrimary:
    def __init__(self, selects: dict | None = None, updates: dict | None = None):
        self.selects = selects or {}
        self.select_queues: dict = {}
        self.updates = updates or {}
        self.insert_id = "id-inserito"
        self.ops: list = []
        self.rpc_calls: list = []
        self.errors: dict = {}
        self.rpc_errors: dict = {}
        self.rpc_results: dict = {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, fn: str, params: dict) -> FakeRpc:
        return FakeRpc(self, fn, params)


def api_error(code: str = "P0001", details: str | None = None) -> APIError:
    return APIError({"message": "dal db", "code": code, "details": details, "hint": None})


def request_row(**overrides) -> dict:
    row = {
        "id": REQUEST_ID,
        "cliente_id": TITOLARE,
        "family_parent_id": TITOLARE,
        "company_profile_id": COMPANY_ID,
        "ai_check_id": AI_CHECK_ID,
        "esito": "ammissibile",
        "punteggio": 82,
        "bando_id": 1,
        "bando_slug": "bando-di-prova",
        "bando_titolo": "Bando di prova",
        "stato": "nuova",
        "assigned_progettista_id": None,
        "assigned_at": None,
        "accepted_proposal_id": None,
        "created_at": tra(0).isoformat(),
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def as_titolare(monkeypatch):
    async def fake(primary, user):
        return (str(user["id"]), True)

    monkeypatch.setattr(consulting_service.family_service, "owner_and_editable", fake)


@pytest.fixture
def as_collegato(monkeypatch):
    """Account collegato attivo: vede i dati del titolare, non agisce."""

    async def fake(primary, user):
        return (TITOLARE, False)

    monkeypatch.setattr(consulting_service.family_service, "owner_and_editable", fake)


@pytest.fixture
def notify_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_notify(primary, user_ids, **kwargs):
        calls.append({"user_ids": [str(u) for u in user_ids], **kwargs})

    monkeypatch.setattr(consulting_service.notification_service, "notify", fake_notify)
    return calls


@pytest.fixture
def spawn_calls(monkeypatch):
    calls: list = []

    def fake_spawn(coro):
        calls.append(coro)
        coro.close()

    monkeypatch.setattr(consulting_service, "_spawn", fake_spawn)
    return calls


@pytest.fixture
def detail_stub(monkeypatch):
    """Le azioni ritornano il dettaglio ricaricato: fuori scope nei test."""
    sentinel = SimpleNamespace(kind="consulenza")

    async def fake_get(primary, user, request_id):
        return sentinel

    monkeypatch.setattr(consulting_service, "get_my_request", fake_get)
    return sentinel


# ---------------------------------------------------------------------------
# Slot
# ---------------------------------------------------------------------------


class TestValidazioniSlot:
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


class TestSlotCrud:
    async def test_inserimento(self):
        primary = FakePrimary()
        primary.insert_id = SLOT_ID
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
        bookings_op = [op for op in primary.ops if op[0] == "consultation_bookings"][0]
        assert ("in", "slot_id", [LIBERO_ID, OCCUPATO_ID]) in bookings_op[3]
        assert ("eq", "stato", "confermata") in bookings_op[3]

    async def test_senza_slot_non_interroga_i_booking(self):
        primary = FakePrimary(selects={"availability_slots": []})
        assert await consulting_service.list_slots(primary, PROGETTISTA) == []
        assert [op[0] for op in primary.ops] == ["availability_slots"]

    async def test_update_passa_dalla_rpc(self):
        primary = FakePrimary()
        data = slot_in()
        out = await consulting_service.update_slot(primary, PROGETTISTA, SLOT_ID, data)
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_update_slot"
        assert params["p_slot_id"] == SLOT_ID
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

    async def test_serie_id_propagato_in_lista(self):
        primary = FakePrimary(
            selects={
                "availability_slots": [
                    {
                        "id": LIBERO_ID,
                        "inizio": tra(60).isoformat(),
                        "fine": tra(90).isoformat(),
                        "serie_id": SERIE_ID,
                    },
                ],
            }
        )
        [slot] = await consulting_service.list_slots(primary, PROGETTISTA)
        assert str(slot.serie_id) == SERIE_ID


class TestSerieSlot:
    def _rpc_result(self) -> dict:
        return {
            "serie_id": SERIE_ID,
            "creati": [
                {
                    "id": LIBERO_ID,
                    "inizio": tra(60).isoformat(),
                    "fine": tra(90).isoformat(),
                    "serie_id": SERIE_ID,
                },
                {
                    "id": OCCUPATO_ID,
                    "inizio": tra(24 * 60 + 60).isoformat(),
                    "fine": tra(24 * 60 + 90).isoformat(),
                    "serie_id": SERIE_ID,
                },
            ],
            "saltati": 1,
        }

    async def test_crea_serie_passa_dalla_rpc(self):
        primary = FakePrimary()
        primary.rpc_results["fn_create_slot_serie"] = self._rpc_result()
        data = SerieIn(occorrenze=[slot_in(60), slot_in(24 * 60), slot_in(48 * 60)])
        out = await consulting_service.create_slot_serie(primary, PROGETTISTA, data)
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_create_slot_serie"
        assert params["p_progettista_id"] == PROGETTISTA
        assert len(params["p_occorrenze"]) == 3
        assert set(params["p_occorrenze"][0]) == {"inizio", "fine"}
        assert out.saltati == 1
        assert str(out.serie_id) == SERIE_ID
        assert [str(s.serie_id) for s in out.creati] == [SERIE_ID, SERIE_ID]
        assert all(s.prenotato is False for s in out.creati)

    async def test_occorrenza_nel_passato_niente_rpc(self):
        """La validazione è PRIMA della RPC: 400 senza alcuna scrittura."""
        primary = FakePrimary()
        data = SerieIn(occorrenze=[slot_in(60), slot_in(start_min=-120)])
        with pytest.raises(BadRequestError):
            await consulting_service.create_slot_serie(primary, PROGETTISTA, data)
        assert primary.rpc_calls == []

    async def test_serie_tutta_sovrapposta_mappata(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_create_slot_serie"] = api_error(
            details="serie_tutta_sovrapposta"
        )
        with pytest.raises(ConflictError):
            await consulting_service.create_slot_serie(
                primary, PROGETTISTA, SerieIn(occorrenze=[slot_in()])
            )

    def test_serie_oltre_il_tetto_rifiutata_dallo_schema(self):
        with pytest.raises(ValidationError):
            SerieIn(occorrenze=[slot_in()] * (MAX_OCCORRENZE_SERIE + 1))

    async def test_delete_serie_passa_dalla_rpc(self):
        primary = FakePrimary()
        primary.rpc_results["fn_delete_slot_serie"] = {"eliminati": 2, "mantenuti": 1}
        out = await consulting_service.delete_slot_serie(primary, PROGETTISTA, SERIE_ID)
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_delete_slot_serie"
        assert params == {"p_serie_id": SERIE_ID, "p_progettista_id": PROGETTISTA}
        assert (out.eliminati, out.mantenuti) == (2, 1)

    async def test_delete_serie_not_found(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_delete_slot_serie"] = api_error(details="serie_not_found")
        with pytest.raises(NotFoundError):
            await consulting_service.delete_slot_serie(primary, PROGETTISTA, SERIE_ID)


# ---------------------------------------------------------------------------
# Creazione richiesta (attivazione addon)
# ---------------------------------------------------------------------------


def create_selects(check_status: str = "ready", addon: bool = True) -> dict:
    return {
        "ai_checks": [
            {
                "id": AI_CHECK_ID,
                "status": check_status,
                "esito": "ammissibile",
                "punteggio": 82,
                "bando_id": 1,
                "bando_slug": "bando-di-prova",
                "bando_titolo": "Bando di prova",
                "company_profile_id": COMPANY_ID,
                "family_parent_id": TITOLARE,
            }
        ],
        "addons": (
            [{"id": 1, "slug": "consulto-esperto", "prezzo": 0, "is_active": True}]
            if addon
            else []
        ),
        "profiles": [
            {"id": PROGETTISTA, "email": "prog@test.it"},
            {"id": "11111111-0000-0000-0000-000000000030", "email": "prog2@test.it"},
        ],
    }


class TestCreateRequest:
    async def test_account_collegato_respinto(self, as_collegato):
        primary = FakePrimary()
        with pytest.raises(ForbiddenError):
            await consulting_service.create_request(primary, USER, AI_CHECK_ID)
        assert primary.ops == []

    async def test_ai_check_non_trovato(self, as_titolare):
        primary = FakePrimary(selects={"ai_checks": []})
        with pytest.raises(NotFoundError):
            await consulting_service.create_request(primary, USER, AI_CHECK_ID)

    async def test_ai_check_non_completato(self, as_titolare):
        primary = FakePrimary(selects=create_selects(check_status="pending"))
        with pytest.raises(ConflictError):
            await consulting_service.create_request(primary, USER, AI_CHECK_ID)

    async def test_addon_non_disponibile(self, as_titolare):
        primary = FakePrimary(selects=create_selects(addon=False))
        with pytest.raises(NotFoundError):
            await consulting_service.create_request(primary, USER, AI_CHECK_ID)

    async def test_richiesta_duplicata(self, as_titolare):
        primary = FakePrimary(selects=create_selects())
        primary.errors[("consultation_requests", "insert")] = api_error(code="23505")
        with pytest.raises(ConflictError):
            await consulting_service.create_request(primary, USER, AI_CHECK_ID)

    async def test_creazione_con_eventi(
        self, as_titolare, notify_calls, spawn_calls, detail_stub
    ):
        primary = FakePrimary(selects=create_selects())
        primary.insert_id = REQUEST_ID
        out = await consulting_service.create_request(primary, USER, AI_CHECK_ID)
        assert out is detail_stub

        [(_, _, payload, _)] = [
            op for op in primary.ops if op[0] == "consultation_requests"
        ]
        assert payload["cliente_id"] == TITOLARE
        assert payload["esito"] == "ammissibile"
        assert payload["addon_slug"] == "consulto-esperto"

        [audit] = [op for op in primary.ops if op[0] == "audit_log"]
        assert audit[2]["action"] == "consulenza.created"

        # Evento 1: in-app a TUTTI i progettisti e admin attivi (parità 0019),
        # email in background.
        [notifica] = notify_calls
        assert notifica["tipo"] == "consulenza.nuova_richiesta"
        assert len(notifica["user_ids"]) == 2
        assert notifica["dedup_key"] == f"richiesta:{REQUEST_ID}"
        # Minimizzazione: nel corpo solo il bando, nessun dato del cliente.
        assert "Bando di prova" in notifica["corpo"]
        assert len(spawn_calls) == 1
        [destinatari] = [op for op in primary.ops if op[0] == "profiles"]
        assert ("in", "role", ["progettista", "admin"]) in destinatari[3]
        assert ("eq", "is_active", True) in destinatari[3]


class TestDettaglioCliente:
    async def test_proposte_con_nome_e_codice(self, as_titolare):
        """Il cliente vede l'autore per nome e cognome (il codice resta nel
        payload per usi interni, la UI non lo mostra)."""
        primary = FakePrimary(
            selects={
                "consultation_requests": [request_row()],
                "consultation_proposals": [
                    {
                        "id": PROPOSAL_ID,
                        "request_id": REQUEST_ID,
                        "progettista_id": PROGETTISTA,
                        "messaggio": "Posso aiutarti",
                        "stato": "inviata",
                        "created_at": tra(0).isoformat(),
                    }
                ],
                "progettisti": [{"user_id": PROGETTISTA, "codice": "PRG-00001"}],
                "profiles": [{"id": PROGETTISTA, "nome": "Paola", "cognome": "Verdi"}],
            }
        )
        out = await consulting_service.get_my_request(primary, USER, REQUEST_ID)
        [proposta] = out.proposte
        assert proposta.nome_progettista == "Paola Verdi"
        assert proposta.codice_progettista == "PRG-00001"


# ---------------------------------------------------------------------------
# Azioni del titolare: accetta / rifiuta / annulla / prenota
# ---------------------------------------------------------------------------


class TestAcceptProposal:
    async def test_rpc_e_evento_assegnazione(
        self, notify_calls, spawn_calls, detail_stub
    ):
        primary = FakePrimary(
            selects={
                "consultation_requests": [request_row(stato="assegnata")],
                "profiles": [{"id": PROGETTISTA, "email": "prog@test.it"}],
                "company_profiles": [{"id": COMPANY_ID, "ragione_sociale": "ACME Srl"}],
            }
        )
        primary.rpc_results["fn_accept_proposal"] = {
            "progettista_id": PROGETTISTA,
            "proposal_id": PROPOSAL_ID,
            "booking_id": None,
        }
        out = await consulting_service.accept_proposal(
            primary, USER, REQUEST_ID, PROPOSAL_ID, None
        )
        assert out is detail_stub
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_accept_proposal"
        assert params["p_cliente_id"] == TITOLARE
        assert params["p_slot_id"] is None
        [notifica] = notify_calls
        assert notifica["tipo"] == "consulenza.assegnazione"
        assert notifica["user_ids"] == [PROGETTISTA]
        assert notifica["dedup_key"] == f"assegnazione:{REQUEST_ID}"

    async def test_con_slot_scatena_anche_levento_prenotazione(
        self, notify_calls, spawn_calls, detail_stub
    ):
        primary = FakePrimary(
            selects={
                "consultation_requests": [request_row(stato="assegnata")],
                "profiles": [{"id": PROGETTISTA, "email": "prog@test.it"}],
                "company_profiles": [{"id": COMPANY_ID, "ragione_sociale": "ACME Srl"}],
                "consultation_bookings": [
                    {
                        "id": BOOKING_ID,
                        "request_id": REQUEST_ID,
                        "slot_id": SLOT_ID,
                        "cliente_id": TITOLARE,
                        "progettista_id": PROGETTISTA,
                        "inizio": tra(60).isoformat(),
                        "fine": tra(90).isoformat(),
                        "stato": "confermata",
                    }
                ],
            }
        )
        primary.rpc_results["fn_accept_proposal"] = {
            "progettista_id": PROGETTISTA,
            "proposal_id": PROPOSAL_ID,
            "booking_id": BOOKING_ID,
        }
        await consulting_service.accept_proposal(
            primary, USER, REQUEST_ID, PROPOSAL_ID, SLOT_ID
        )
        assert [n["tipo"] for n in notify_calls] == [
            "consulenza.assegnazione",
            "consulenza.prenotazione",
        ]
        prenotazione = notify_calls[1]
        assert prenotazione["dedup_key"] == f"booking:{BOOKING_ID}"
        assert "ora italiana" in prenotazione["corpo"]

    async def test_slot_occupato_mappato_su_conflict(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_accept_proposal"] = api_error(details="slot_taken")
        with pytest.raises(ConflictError):
            await consulting_service.accept_proposal(
                primary, USER, REQUEST_ID, PROPOSAL_ID, SLOT_ID
            )


class TestRejectCancel:
    async def test_rifiuto_condizionale(self, as_titolare, detail_stub):
        primary = FakePrimary(
            selects={"consultation_requests": [request_row()]},
            updates={"consultation_proposals": [{"id": PROPOSAL_ID}]},
        )
        await consulting_service.reject_proposal(primary, USER, REQUEST_ID, PROPOSAL_ID)
        [(_, _, payload, filters)] = [
            op for op in primary.ops if op[0] == "consultation_proposals"
        ]
        assert payload == {"stato": "rifiutata"}
        assert ("eq", "stato", "inviata") in filters

    async def test_rifiuto_su_proposta_gia_chiusa(self, as_titolare, detail_stub):
        primary = FakePrimary(
            selects={"consultation_requests": [request_row()]},
            updates={"consultation_proposals": []},
        )
        with pytest.raises(ConflictError):
            await consulting_service.reject_proposal(primary, USER, REQUEST_ID, PROPOSAL_ID)

    async def test_richiesta_di_unaltra_azienda_non_esiste(self, as_titolare, detail_stub):
        altro = {"id": "22222222-0000-0000-0000-000000000031", "role": "cliente"}
        primary = FakePrimary(selects={"consultation_requests": [request_row()]})
        # La richiesta è dell'azienda di TITOLARE: per un estraneo non esiste.
        with pytest.raises(NotFoundError):
            await consulting_service.reject_proposal(primary, altro, REQUEST_ID, PROPOSAL_ID)

    async def test_annullo_chiude_le_proposte_e_avvisa(
        self, as_titolare, notify_calls, detail_stub
    ):
        altra_prog = "33333333-0000-0000-0000-000000000032"
        primary = FakePrimary(
            selects={
                "consultation_requests": [request_row()],
                "consultation_proposals": [
                    {"progettista_id": PROGETTISTA},
                    {"progettista_id": altra_prog},
                ],
            },
            updates={
                "consultation_requests": [{"id": REQUEST_ID}],
                "consultation_proposals": [{}],
            },
        )
        await consulting_service.cancel_request(primary, USER, REQUEST_ID)
        proposte_update = [
            op
            for op in primary.ops
            if op[0] == "consultation_proposals" and op[1] == "update"
        ]
        assert proposte_update[0][2] == {"stato": "superata"}
        [notifica] = notify_calls
        assert notifica["tipo"] == "consulenza.richiesta_annullata"
        assert set(notifica["user_ids"]) == {PROGETTISTA, altra_prog}
        [audit] = [op for op in primary.ops if op[0] == "audit_log"]
        assert audit[2]["action"] == "consulenza.cancelled"

    async def test_annullo_su_richiesta_non_aperta(self, as_titolare, detail_stub):
        primary = FakePrimary(
            selects={"consultation_requests": [request_row(stato="assegnata",
                                                           assigned_progettista_id=PROGETTISTA,
                                                           accepted_proposal_id=PROPOSAL_ID,
                                                           assigned_at=tra(0).isoformat())]},
            updates={"consultation_requests": []},
        )
        with pytest.raises(ConflictError):
            await consulting_service.cancel_request(primary, USER, REQUEST_ID)


class TestBookableSlots:
    async def test_serve_la_proposta_se_non_assegnata(self, as_titolare):
        primary = FakePrimary(selects={"consultation_requests": [request_row()]})
        with pytest.raises(BadRequestError):
            await consulting_service.list_bookable_slots(primary, USER, REQUEST_ID, None)

    async def test_slot_liberi_della_proposta(self, as_titolare):
        primary = FakePrimary(
            selects={
                "consultation_requests": [request_row()],
                "consultation_proposals": [
                    {"progettista_id": PROGETTISTA, "stato": "inviata"}
                ],
                "availability_slots": [
                    {"id": LIBERO_ID, "inizio": tra(60).isoformat(), "fine": tra(90).isoformat()},
                    {"id": OCCUPATO_ID, "inizio": tra(120).isoformat(), "fine": tra(150).isoformat()},
                ],
                "consultation_bookings": [{"slot_id": OCCUPATO_ID}],
            }
        )
        slots = await consulting_service.list_bookable_slots(
            primary, USER, REQUEST_ID, PROPOSAL_ID
        )
        assert [str(s.id) for s in slots] == [LIBERO_ID]


# ---------------------------------------------------------------------------
# Lato progettista: pool parziale, proposte, dossier full
# ---------------------------------------------------------------------------


class TestPool:
    async def test_dati_parziali_e_denominazione(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [
                    {"id": COMPANY_ID, "ragione_sociale": "ACME Srl", "partita_iva": "01234567890"}
                ],
                "profiles": [
                    {
                        "id": TITOLARE,
                        "nome": "Paola",
                        "cognome": "Bianchi",
                        "email": "paola@acme.it",
                        "azienda": None,
                    }
                ],
                "consultation_proposals": [],
                "consultation_bookings": [],
            }
        )
        primary.select_queues["consultation_requests"] = [
            [request_row()],  # aperte
            [],  # assegnate a me
        ]
        pool = await consulting_service.list_pool(primary, PROG_USER)
        assert len(pool.aperte) == 1 and pool.assegnate == []
        richiesta = pool.aperte[0]
        # Esattamente i campi del requisito punto 3.
        assert richiesta.ragione_sociale == "ACME Srl"
        assert richiesta.partita_iva == "01234567890"
        assert richiesta.denominazione_utente == "ACME Srl"
        assert richiesta.email == "paola@acme.it"
        assert richiesta.esito == "ammissibile" and richiesta.punteggio == 82
        assert richiesta.assegnata_a_me is False

    async def test_richiesta_altrui_assegnata_non_visibile(self):
        primary = FakePrimary(
            selects={
                "consultation_requests": [
                    request_row(
                        stato="assegnata",
                        assigned_progettista_id="44444444-0000-0000-0000-000000000033",
                        accepted_proposal_id=PROPOSAL_ID,
                        assigned_at=tra(0).isoformat(),
                    )
                ]
            }
        )
        with pytest.raises(NotFoundError):
            await consulting_service.get_pool_request(primary, PROG_USER, REQUEST_ID)

    async def test_richiesta_annullata_non_visibile(self):
        primary = FakePrimary(
            selects={"consultation_requests": [request_row(stato="annullata")]}
        )
        with pytest.raises(NotFoundError):
            await consulting_service.get_pool_request(primary, PROG_USER, REQUEST_ID)


class TestProposte:
    async def test_invio_con_evento_al_titolare(self, notify_calls, spawn_calls, monkeypatch):
        async def fake_detail(primary, progettista, request_id):
            return SimpleNamespace(kind="dettaglio")

        monkeypatch.setattr(consulting_service, "get_pool_request", fake_detail)
        primary = FakePrimary(
            selects={
                "consultation_requests": [request_row()],
                "progettisti": [{"user_id": PROGETTISTA, "codice": "PRG-00001"}],
                "profiles": [{"id": TITOLARE, "email": "paola@acme.it"}],
            }
        )
        primary.insert_id = PROPOSAL_ID
        await consulting_service.create_proposal(
            primary, PROG_USER, REQUEST_ID, "Posso aiutarti su questo bando"
        )
        [notifica] = notify_calls
        assert notifica["tipo"] == "consulenza.proposta"
        assert notifica["user_ids"] == [TITOLARE]
        assert notifica["dedup_key"] == f"proposta:{PROPOSAL_ID}"
        # Minimizzazione: la notifica CONSERVATA cita solo il bando (il nome
        # dell'autore viaggia nell'email, effimera).
        assert notifica["corpo"] == "Bando: Bando di prova"
        [audit] = [op for op in primary.ops if op[0] == "audit_log"]
        assert audit[2]["action"] == "consulenza.proposal_sent"
        # Il progettista ha già il codice: nessuna RPC di ensure.
        assert primary.rpc_calls == []

    async def test_proposta_da_admin_garantisce_il_codice(
        self, notify_calls, monkeypatch
    ):
        """Parità admin: la prima proposta assegna pigramente il codice PRG
        (RPC 0019) prima dell'insert, così l'evento 2 lo mostra da subito."""

        async def fake_detail(primary, progettista, request_id):
            return SimpleNamespace(kind="dettaglio")

        monkeypatch.setattr(consulting_service, "get_pool_request", fake_detail)
        primary = FakePrimary(
            selects={
                "consultation_requests": [request_row()],
                "progettisti": [{"user_id": PROGETTISTA, "codice": "PRG-00009"}],
                "profiles": [{"id": TITOLARE, "email": "paola@acme.it"}],
            }
        )
        primary.insert_id = PROPOSAL_ID
        await consulting_service.create_proposal(
            primary, {"id": PROGETTISTA, "role": "admin"}, REQUEST_ID, "Posso aiutarti"
        )
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_ensure_progettista_codice"
        assert params == {"p_user_id": PROGETTISTA}
        [notifica] = notify_calls
        assert notifica["corpo"] == "Bando: Bando di prova"

    async def test_ensure_codice_in_errore_blocca_prima_dellinsert(self, notify_calls):
        primary = FakePrimary(selects={"consultation_requests": [request_row()]})
        primary.rpc_errors["fn_ensure_progettista_codice"] = api_error(
            details="user_not_found"
        )
        with pytest.raises(NotFoundError):
            await consulting_service.create_proposal(
                primary, {"id": PROGETTISTA, "role": "admin"}, REQUEST_ID, "Ciao"
            )
        inserts = [op for op in primary.ops if op[0] == "consultation_proposals"]
        assert inserts == []
        assert notify_calls == []

    async def test_doppia_proposta(self):
        primary = FakePrimary(selects={"consultation_requests": [request_row()]})
        primary.errors[("consultation_proposals", "insert")] = api_error(code="23505")
        with pytest.raises(ConflictError):
            await consulting_service.create_proposal(primary, PROG_USER, REQUEST_ID, "Ciao")

    async def test_richiesta_chiusa_in_gara_compensata(self, notify_calls):
        """TOCTOU guardia→insert: se la richiesta viene assegnata nel mezzo,
        la proposta appena inserita si chiude come «superata», il titolare
        NON viene notificato e il progettista riceve un 409 chiaro."""
        primary = FakePrimary()
        primary.insert_id = PROPOSAL_ID
        primary.select_queues["consultation_requests"] = [
            [request_row()],  # guardia: ancora nuova
            [
                request_row(
                    stato="assegnata",
                    assigned_progettista_id="44444444-0000-0000-0000-000000000033",
                    accepted_proposal_id="55555555-0000-0000-0000-000000000034",
                    assigned_at=tra(0).isoformat(),
                )
            ],  # ricontrollo post-insert: assegnata a un altro
        ]
        with pytest.raises(ConflictError):
            await consulting_service.create_proposal(
                primary, PROG_USER, REQUEST_ID, "Arrivo tardi"
            )
        [(_, _, payload, filters)] = [
            op
            for op in primary.ops
            if op[0] == "consultation_proposals" and op[1] == "update"
        ]
        assert payload == {"stato": "superata"}
        assert ("eq", "id", PROPOSAL_ID) in filters
        assert ("eq", "stato", "inviata") in filters
        assert notify_calls == []

    async def test_ritiro_condizionale(self):
        primary = FakePrimary(updates={"consultation_proposals": []})
        with pytest.raises(ConflictError):
            await consulting_service.withdraw_proposal(primary, PROG_USER, PROPOSAL_ID)


class TestAnnulloAppuntamentoProgettista:
    def booking_row(self) -> dict:
        return {
            "id": BOOKING_ID,
            "request_id": REQUEST_ID,
            "slot_id": SLOT_ID,
            "cliente_id": TITOLARE,
            "progettista_id": PROGETTISTA,
            "inizio": tra(60).isoformat(),
            "fine": tra(90).isoformat(),
            "stato": "annullata",
        }

    async def test_annullo_atomico_sul_booking_indicato(self, notify_calls):
        primary = FakePrimary(
            selects={"consultation_requests": [request_row(stato="assegnata",
                                                           assigned_progettista_id=PROGETTISTA,
                                                           accepted_proposal_id=PROPOSAL_ID,
                                                           assigned_at=tra(0).isoformat())]},
            updates={"consultation_bookings": [self.booking_row()]},
        )
        await consulting_service.progettista_cancel_booking(primary, PROG_USER, BOOKING_ID)
        [(_, _, payload, filters)] = [
            op for op in primary.ops if op[0] == "consultation_bookings"
        ]
        # L'update deve puntare al booking INDICATO e solo se confermato:
        # senza il filtro su stato, l'id di un appuntamento già annullato
        # finirebbe per colpire la prenotazione confermata della richiesta.
        assert payload == {"stato": "annullata"}
        assert ("eq", "id", BOOKING_ID) in filters
        assert ("eq", "progettista_id", PROGETTISTA) in filters
        assert ("eq", "stato", "confermata") in filters
        [notifica] = notify_calls
        assert notifica["user_ids"] == [TITOLARE]
        assert notifica["dedup_key"] == f"appuntamento-annullato:{BOOKING_ID}"

    async def test_booking_gia_annullato_o_altrui(self):
        primary = FakePrimary(updates={"consultation_bookings": []})
        with pytest.raises(NotFoundError):
            await consulting_service.progettista_cancel_booking(
                primary, PROG_USER, BOOKING_ID
            )


class TestDossierFull:
    async def test_non_assegnato_respinto(self):
        primary = FakePrimary(
            selects={
                "consultation_requests": [
                    request_row(
                        stato="assegnata",
                        assigned_progettista_id="44444444-0000-0000-0000-000000000033",
                        accepted_proposal_id=PROPOSAL_ID,
                        assigned_at=tra(0).isoformat(),
                    )
                ]
            }
        )
        with pytest.raises(ForbiddenError):
            await consulting_service.get_full_company(primary, PROG_USER, REQUEST_ID)
        assert [op for op in primary.ops if op[0] == "audit_log"] == []

    async def test_assegnato_con_audit(self, monkeypatch):
        async def fake_company(primary, owner_id):
            assert owner_id == TITOLARE
            return None

        async def fake_dossier(primary, owner_id, *, editable=False):
            assert owner_id == TITOLARE
            return DossierResponse(editable=False, imported=False)

        monkeypatch.setattr(
            consulting_service.company_service, "get_company_for_owner", fake_company
        )
        monkeypatch.setattr(
            consulting_service.openapi_service, "get_dossier_for_owner", fake_dossier
        )
        primary = FakePrimary(
            selects={
                "consultation_requests": [
                    request_row(
                        stato="assegnata",
                        assigned_progettista_id=PROGETTISTA,
                        accepted_proposal_id=PROPOSAL_ID,
                        assigned_at=tra(0).isoformat(),
                    )
                ]
            }
        )
        out = await consulting_service.get_full_company(primary, PROG_USER, REQUEST_ID)
        assert out.dossier.imported is False
        # OGNI accesso ai dati full finisce in audit_log.
        [audit] = [op for op in primary.ops if op[0] == "audit_log"]
        assert audit[2]["action"] == "consulenza.dossier_accessed"
        assert audit[2]["payload"] == {"request_id": REQUEST_ID}
