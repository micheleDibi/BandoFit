"""Test del calendario: bounds mensili, CRUD, evento scadenza-bando con data
in sola lettura, guardie su UUID e coerenza orari."""

from datetime import date, time

import pytest

from app.core.errors import BadRequestError, BandoRitiratoError, NotFoundError
from app.schemas.calendar import CalendarEventIn, CalendarEventUpdate
from app.api.deps import ActiveCompany
from app.services import calendar_service
from tests.test_saved_bandi_service import BANDO_VIVO, STORICO_301, FakeDb, catalogo_per_id

USER_ID = "a0000000-0000-0000-0000-000000000001"
EVENT_ID = "e0000000-0000-0000-0000-0000000000e1"
COMPANY = "c0000000-0000-0000-0000-000000000001"


def _active(company_id=None, is_multi=False):
    return ActiveCompany(company_id=company_id, owner_id=USER_ID, editable=True, is_multi=is_multi)


def event_row(**overrides) -> dict:
    row = {
        "id": EVENT_ID,
        "titolo": "Riunione",
        "data": "2026-07-15",
        "tutto_il_giorno": True,
        "ora_inizio": None,
        "ora_fine": None,
        "note": None,
        "tipo": "personale",
        "bando_id": None,
        "bando_slug": None,
        "created_at": "2026-07-07T10:00:00+00:00",
        "updated_at": "2026-07-07T10:00:00+00:00",
    }
    row.update(overrides)
    return row


# -------------------------------------------------------------- validazione

class TestCalendarEventIn:
    def test_tutto_il_giorno_azzera_gli_orari(self):
        event = CalendarEventIn(
            titolo="X", data=date(2026, 7, 15), tutto_il_giorno=True,
            ora_inizio=time(9, 0), ora_fine=time(10, 0),
        )
        assert event.ora_inizio is None and event.ora_fine is None

    def test_con_orari_serve_l_inizio(self):
        with pytest.raises(ValueError):
            CalendarEventIn(titolo="X", data=date(2026, 7, 15), tutto_il_giorno=False)

    def test_fine_dopo_inizio(self):
        with pytest.raises(ValueError):
            CalendarEventIn(
                titolo="X", data=date(2026, 7, 15), tutto_il_giorno=False,
                ora_inizio=time(10, 0), ora_fine=time(10, 0),
            )

    def test_titolo_di_soli_spazi_respinto(self):
        # min_length conterebbe gli spazi: senza lo strip il CHECK del DB
        # esploderebbe in un 502.
        with pytest.raises(ValueError):
            CalendarEventIn(titolo="   ", data=date(2026, 7, 15))
        event = CalendarEventIn(titolo="  Ok  ", data=date(2026, 7, 15))
        assert event.titolo == "Ok"

    def test_data_fuori_intervallo_respinta(self):
        # Il calendario mostra il 2000-2100: una data fuori sarebbe un evento
        # invisibile che consuma il limite.
        with pytest.raises(ValueError):
            CalendarEventIn(titolo="X", data=date(1999, 12, 31))
        with pytest.raises(ValueError):
            CalendarEventIn(titolo="X", data=date(2101, 1, 1))


# ------------------------------------------------------- regole della scadenza

class TestCampiScadenza:
    """La funzione condivisa da creazione e riallineamento."""

    @pytest.mark.parametrize(("data", "ora", "attesa"), [
        ("2026-09-15", None, {"data": "2026-09-15", "tutto_il_giorno": True, "ora_inizio": None}),
        ("2026-09-15", "12:00:00",
         {"data": "2026-09-15", "tutto_il_giorno": False, "ora_inizio": "12:00:00"}),
        (" 2026-09-15 ", "18:30",
         {"data": "2026-09-15", "tutto_il_giorno": False, "ora_inizio": "18:30:00"}),
        ("2026-09-15", "12:00:00+02",
         {"data": "2026-09-15", "tutto_il_giorno": False, "ora_inizio": "12:00:00"}),
        ("2026-09-15", "24:00",
         {"data": "2026-09-15", "tutto_il_giorno": True, "ora_inizio": None}),
        ("2026-09-15", "boh", {"data": "2026-09-15", "tutto_il_giorno": True, "ora_inizio": None}),
        (date(2026, 9, 15), time(9, 0),
         {"data": "2026-09-15", "tutto_il_giorno": False, "ora_inizio": "09:00:00"}),
    ])
    def test_data_e_ora(self, data, ora, attesa):
        assert calendar_service.campi_scadenza(data, ora) == attesa

    @pytest.mark.parametrize("data", [None, "", "boh", "2026-13-01", 20260915, True])
    def test_senza_data_valida(self, data):
        assert calendar_service.campi_scadenza(data, "12:00") is None

    @pytest.mark.parametrize(("valore", "atteso"), [
        ("aperto", "aperto"), ("stato_nuovo", "stato_nuovo"), (None, None), ("", None),
        (" ", None), (1, None),
    ])
    def test_stato_del_bando(self, valore, atteso):
        assert calendar_service.stato_del_bando(valore) == atteso


# --------------------------------------------------------------------- lista

class TestListEvents:
    async def test_bounds_del_mese(self):
        primary = FakeDb({"calendar_events": [event_row()]})
        out = await calendar_service.list_events(primary, USER_ID, _active(), 2026, 7)
        [(_, filters)] = primary.ops_for("calendar_events", "select")
        assert filters["data__gte"] == "2026-07-01"
        assert filters["data__lt"] == "2026-08-01"
        assert filters["user_id"] == USER_ID
        assert out.items[0].titolo == "Riunione"
        assert out.items[0].bando_stato is None  # evento personale

    async def test_scadenza_con_lo_stato_del_bando(self):
        assert "bando_stato" in calendar_service.EVENT_SELECT.split(",")
        riga = event_row(tipo="bando", bando_id=42, bando_slug="bando-x", bando_stato="revocato")
        primary = FakeDb({"calendar_events": [riga]})
        out = await calendar_service.list_events(primary, USER_ID, _active(), 2026, 7)
        assert out.items[0].bando_stato == "revocato"

    async def test_rollover_dicembre(self):
        primary = FakeDb({"calendar_events": []})
        await calendar_service.list_events(primary, USER_ID, _active(), 2026, 12)
        [(_, filters)] = primary.ops_for("calendar_events", "select")
        assert filters["data__gte"] == "2026-12-01"
        assert filters["data__lt"] == "2027-01-01"


# -------------------------------------------------------------------- create

class TestCreateEvent:
    async def test_personale_con_orari(self):
        primary = FakeDb({"calendar_events": []})
        payload = CalendarEventIn(
            titolo="  Colloquio  ", data=date(2026, 7, 20), tutto_il_giorno=False,
            ora_inizio=time(9, 0), ora_fine=time(10, 30), note="portare documenti",
        )
        out = await calendar_service.create_event(primary, USER_ID, _active(), payload)
        [(inserted, _)] = primary.ops_for("calendar_events", "insert")
        assert inserted["tipo"] == "personale"  # il tipo non arriva mai dal client
        assert inserted["titolo"] == "Colloquio"
        assert inserted["data"] == "2026-07-20"
        assert inserted["ora_inizio"] == "09:00:00"
        assert inserted["ora_fine"] == "10:30:00"
        assert out.tipo == "personale"

    async def test_cap_raggiunto(self):
        primary = FakeDb(
            {"calendar_events": [{"id": i} for i in range(calendar_service.MAX_EVENTS)]}
        )
        with pytest.raises(BadRequestError):
            await calendar_service.create_event(
                primary, USER_ID, _active(), CalendarEventIn(titolo="X", data=date(2026, 7, 1))
            )
        assert not primary.ops_for("calendar_events", "insert")


class TestCreateBandoEvent:
    async def test_deriva_data_e_titolo_dal_catalogo(self):
        primary = FakeDb({"calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        out = await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "bando-x")
        [(inserted, _)] = primary.ops_for("calendar_events", "insert")
        assert inserted["tipo"] == "bando"
        assert inserted["titolo"] == "Scadenza: Bando X"
        assert inserted["data"] == "2026-09-15"  # la scadenza del catalogo
        assert inserted["tutto_il_giorno"] is True
        assert inserted["bando_id"] == 42
        assert inserted["bando_slug"] == "bando-x"
        assert out.tipo == "bando"

    async def test_legge_la_vista_del_catalogo(self):
        # Fase c: una sola lettura su `bando_pubblico`, senza predicato di
        # pubblicazione (la vista è già filtrata).
        primary = FakeDb({"calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "bando-x")
        assert [table for table, *_ in secondary.ops] == ["bando_pubblico"]
        [(_, filters)] = secondary.ops_for("bando_pubblico", "select")
        assert filters["slug"] == "bando-x"
        assert "stato_processing" not in filters

    def test_snapshot_legge_ora_e_verifica_della_scadenza(self):
        # Contratto DB bandi §12, R6 «Versione (c)». Il FakeQuery non registra
        # la select: si verifica la costante.
        colonne = calendar_service.SNAPSHOT_SELECT.split(",")
        assert colonne == ["id", "slug", "titolo", "titolo_breve", "data_scadenza",
                           "ora_scadenza", "data_scadenza_verificata", "stato_effettivo"]

    async def test_salva_lo_stato_del_bando(self):
        # Migration 0048: lo stato del catalogo entra in `bando_stato` e torna
        # nella risposta.
        primary = FakeDb({"calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "stato_effettivo": "sospeso"}]})
        out = await calendar_service.create_bando_event(
            primary, secondary, USER_ID, _active(), "bando-x"
        )
        [(inserted, _)] = primary.ops_for("calendar_events", "insert")
        assert inserted["bando_stato"] == "sospeso"
        assert out.bando_stato == "sospeso"

    @pytest.mark.parametrize("stato", [None, "", "  ", 3])
    async def test_stato_assente_o_strano_resta_null(self, stato):
        primary = FakeDb({"calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "stato_effettivo": stato}]})
        await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "bando-x")
        [(inserted, _)] = primary.ops_for("calendar_events", "insert")
        assert inserted["bando_stato"] is None

    @pytest.mark.parametrize("data_scadenza", ["", "boh", "2026-02-30", 20260915])
    async def test_scadenza_non_valida_400(self, data_scadenza):
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "data_scadenza": data_scadenza}]})
        with pytest.raises(BadRequestError):
            await calendar_service.create_bando_event(
                FakeDb(), secondary, USER_ID, _active(), "bando-x"
            )

    @pytest.mark.parametrize(
        ("ora_scadenza", "attesa"),
        [("12:00:00", "12:00:00"), ("18:30", "18:30:00"), ("12:00:00+02", "12:00:00")],
    )
    async def test_scadenza_con_ora_evento_con_ora_di_inizio(self, ora_scadenza, attesa):
        primary = FakeDb({"calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "ora_scadenza": ora_scadenza,
                                                 "data_scadenza_verificata": True}]})
        await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "bando-x")
        [(inserted, _)] = primary.ops_for("calendar_events", "insert")
        assert inserted["tutto_il_giorno"] is False
        assert inserted["ora_inizio"] == attesa
        assert "ora_fine" not in inserted
        assert inserted["data"] == "2026-09-15"
        # Letta ma senza colonna: mai nelle note, che sono dell'utente.
        assert "data_scadenza_verificata" not in inserted
        assert "note" not in inserted

    @pytest.mark.parametrize("ora_scadenza", [None, "", "24:00", "24:00:00", "boh"])
    async def test_scadenza_senza_ora_evento_tutto_il_giorno(self, ora_scadenza):
        primary = FakeDb({"calendar_events": []})
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "ora_scadenza": ora_scadenza}]})
        await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "bando-x")
        [(inserted, _)] = primary.ops_for("calendar_events", "insert")
        assert inserted["tutto_il_giorno"] is True
        assert "ora_inizio" not in inserted

    async def test_bando_senza_scadenza(self):
        secondary = FakeDb({"bando_pubblico": [{**BANDO_VIVO, "data_scadenza": None}]})
        with pytest.raises(BadRequestError):
            await calendar_service.create_bando_event(FakeDb(), secondary, USER_ID, _active(), "bando-x")

    async def test_bando_sparito(self):
        secondary = FakeDb({"bando_pubblico": []})
        with pytest.raises(NotFoundError):
            await calendar_service.create_bando_event(FakeDb(), secondary, USER_ID, _active(), "x")

    async def test_slug_spostato_evento_sul_master(self):
        primary = FakeDb({"calendar_events": []})
        secondary = FakeDb({"bando_pubblico": catalogo_per_id(BANDO_VIVO), "bando_slug_storico": STORICO_301})
        out = await calendar_service.create_bando_event(
            primary, secondary, USER_ID, _active(), "vecchio-slug"
        )
        [(inserted, _)] = primary.ops_for("calendar_events", "insert")
        assert inserted["bando_id"] == 42
        assert inserted["bando_slug"] == "bando-x"  # canonico, non quello richiesto
        assert inserted["titolo"] == "Scadenza: Bando X"
        assert inserted["data"] == "2026-09-15"
        assert out.bando_slug == "bando-x"

    async def test_slug_ritirato_410_primario_mai_interrogato(self):
        primary = FakeDb()
        secondary = FakeDb({
            "bando_pubblico": [],
            "bando_slug_storico": [{"slug": "ritirato", "bando_id": None, "esito": "410"}],
        })
        with pytest.raises(BandoRitiratoError):
            await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "ritirato")
        assert primary.ops == []

    async def test_master_senza_scadenza_resta_400(self):
        # Il controllo sulla scadenza viene dopo la risoluzione: vale sul master.
        secondary = FakeDb({
            "bando_pubblico": catalogo_per_id({**BANDO_VIVO, "data_scadenza": None}),
            "bando_slug_storico": STORICO_301,
        })
        with pytest.raises(BadRequestError):
            await calendar_service.create_bando_event(
                FakeDb(), secondary, USER_ID, _active(), "vecchio-slug"
            )

    async def test_idempotente_se_gia_in_calendario(self):
        esistente = event_row(tipo="bando", bando_id=42, bando_slug="bando-x",
                              titolo="Scadenza: Bando X", data="2026-09-15")

        def calendar_events(filters):
            if filters.get("tipo") == "bando" and "bando_id" in filters:
                return [esistente]
            return []

        primary = FakeDb({"calendar_events": calendar_events})
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        out = await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "bando-x")
        assert not primary.ops_for("calendar_events", "insert")
        assert out.id == EVENT_ID

    async def test_corsa_su_indice_unico_rilegge(self):
        calls = {"n": 0}
        esistente = event_row(tipo="bando", bando_id=42, bando_slug="bando-x")

        def calendar_events(filters):
            if filters.get("tipo") == "bando" and "bando_id" in filters:
                calls["n"] += 1
                return [] if calls["n"] == 1 else [esistente]
            return []

        primary = FakeDb({"calendar_events": calendar_events})
        primary.insert_fail_unique.add("calendar_events")
        secondary = FakeDb({"bando_pubblico": [BANDO_VIVO]})
        out = await calendar_service.create_bando_event(primary, secondary, USER_ID, _active(), "bando-x")
        assert out.id == EVENT_ID


# -------------------------------------------------------------------- update

class TestUpdateEvent:
    async def test_merge_dei_campi_permessi(self):
        primary = FakeDb({"calendar_events": [event_row()]})
        await calendar_service.update_event(
            primary, USER_ID, _active(), EVENT_ID, CalendarEventUpdate(titolo="Nuovo titolo")
        )
        [(updated, filters)] = primary.ops_for("calendar_events", "update")
        assert updated["titolo"] == "Nuovo titolo"
        assert updated["data"] == "2026-07-15"  # invariata
        assert filters["id"] == EVENT_ID and filters["user_id"] == USER_ID

    async def test_passaggio_a_tutto_il_giorno_azzera_gli_orari(self):
        primary = FakeDb({
            "calendar_events": [event_row(tutto_il_giorno=False, ora_inizio="09:00:00",
                                          ora_fine="10:00:00")]
        })
        await calendar_service.update_event(
            primary, USER_ID, _active(), EVENT_ID, CalendarEventUpdate(tutto_il_giorno=True)
        )
        [(updated, _)] = primary.ops_for("calendar_events", "update")
        assert updated["tutto_il_giorno"] is True
        assert updated["ora_inizio"] is None and updated["ora_fine"] is None

    async def test_orari_incoerenti(self):
        primary = FakeDb({
            "calendar_events": [event_row(tutto_il_giorno=False, ora_inizio="09:00:00")]
        })
        with pytest.raises(BadRequestError):
            await calendar_service.update_event(
                primary, USER_ID, _active(), EVENT_ID, CalendarEventUpdate(ora_fine=time(8, 0))
            )

    async def test_orari_su_evento_tutto_il_giorno_respinti_esplicitamente(self):
        # La rivalidazione li azzererebbe in silenzio (200 senza effetto):
        # meglio un 400 che spiega di togliere prima la spunta.
        primary = FakeDb({"calendar_events": [event_row(tutto_il_giorno=True)]})
        with pytest.raises(BadRequestError):
            await calendar_service.update_event(
                primary, USER_ID, _active(), EVENT_ID, CalendarEventUpdate(ora_inizio=time(10, 0))
            )
        assert not primary.ops_for("calendar_events", "update")

    async def test_evento_bando_data_bloccata(self):
        primary = FakeDb({
            "calendar_events": [event_row(tipo="bando", bando_id=42, bando_slug="s")]
        })
        with pytest.raises(BadRequestError):
            await calendar_service.update_event(
                primary, USER_ID, _active(), EVENT_ID, CalendarEventUpdate(data=date(2026, 12, 25))
            )
        assert not primary.ops_for("calendar_events", "update")

    async def test_evento_bando_titolo_e_note_modificabili(self):
        primary = FakeDb({
            "calendar_events": [event_row(tipo="bando", bando_id=42, bando_slug="s")]
        })
        await calendar_service.update_event(
            primary, USER_ID, _active(), EVENT_ID,
            CalendarEventUpdate(titolo="Scadenza importante", note="preparare i documenti"),
        )
        [(updated, _)] = primary.ops_for("calendar_events", "update")
        assert updated["titolo"] == "Scadenza importante"
        assert updated["note"] == "preparare i documenti"

    async def test_non_trovato(self):
        primary = FakeDb({"calendar_events": []})
        with pytest.raises(NotFoundError):
            await calendar_service.update_event(
                primary, USER_ID, _active(), EVENT_ID, CalendarEventUpdate(titolo="X")
            )

    async def test_uuid_malformato(self):
        primary = FakeDb()
        with pytest.raises(NotFoundError):
            await calendar_service.update_event(
                primary, USER_ID, _active(), "non-un-uuid", CalendarEventUpdate(titolo="X")
            )
        assert not primary.ops  # mai arrivati al DB


# -------------------------------------------------------------------- delete

class TestDeleteEvent:
    async def test_delete(self):
        primary = FakeDb()
        await calendar_service.delete_event(primary, USER_ID, _active(), EVENT_ID)
        [(_, filters)] = primary.ops_for("calendar_events", "delete")
        assert filters["id"] == EVENT_ID and filters["user_id"] == USER_ID

    async def test_non_trovato(self):
        primary = FakeDb()
        primary.delete_returns_empty.add("calendar_events")
        with pytest.raises(NotFoundError):
            await calendar_service.delete_event(primary, USER_ID, _active(), EVENT_ID)

    async def test_uuid_malformato(self):
        primary = FakeDb()
        with pytest.raises(NotFoundError):
            await calendar_service.delete_event(primary, USER_ID, _active(), "x")
        assert not primary.ops
