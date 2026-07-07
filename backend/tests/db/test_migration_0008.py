"""Test funzionali della migration 0008 (bandi salvati + calendario)."""

import psycopg
import pytest

UTENTE = "a0000000-0000-0000-0000-000000000008"
UTENTE2 = "a0000000-0000-0000-0000-000000000009"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def save_bando(db, user_id: str, bando_id: int = 42, slug: str = "bando-x") -> str:
    return db.execute(
        """insert into public.saved_bandi (user_id, bando_id, bando_slug, bando_titolo)
           values (%s, %s, %s, 'Bando X') returning id""",
        (user_id, bando_id, slug),
    ).fetchone()[0]


def insert_event(db, user_id: str, **overrides) -> str:
    row = {
        "titolo": "Evento",
        "data": "2026-07-15",
        "tutto_il_giorno": True,
        "ora_inizio": None,
        "ora_fine": None,
        "tipo": "personale",
        "bando_id": None,
        "bando_slug": None,
    }
    row.update(overrides)
    return db.execute(
        """insert into public.calendar_events
           (user_id, titolo, data, tutto_il_giorno, ora_inizio, ora_fine, tipo, bando_id, bando_slug)
           values (%(user_id)s, %(titolo)s, %(data)s, %(tutto_il_giorno)s,
                   %(ora_inizio)s, %(ora_fine)s, %(tipo)s, %(bando_id)s, %(bando_slug)s)
           returning id""",
        {"user_id": user_id, **row},
    ).fetchone()[0]


class TestSavedBandi:
    def test_unico_per_utente_e_bando(self, db):
        signup(db, UTENTE, "u8@test.it")
        save_bando(db, UTENTE, bando_id=42)
        with pytest.raises(psycopg.errors.UniqueViolation):
            save_bando(db, UTENTE, bando_id=42)
        # bando diverso: consentito
        save_bando(db, UTENTE, bando_id=43)
        # STESSO bando per un ALTRO utente: consentito (il vincolo è per coppia)
        signup(db, UTENTE2, "u9@test.it")
        save_bando(db, UTENTE2, bando_id=42)

    def test_cascade_da_profiles(self, db):
        signup(db, UTENTE, "u8@test.it")
        save_bando(db, UTENTE)
        db.execute("delete from public.profiles where id = %s", (UTENTE,))
        assert db.execute("select count(*) from public.saved_bandi").fetchone()[0] == 0


class TestCalendarEvents:
    def test_vincolo_tipo(self, db):
        signup(db, UTENTE, "u8@test.it")
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tipo="riunione")

    def test_coerenza_bando_link(self, db):
        signup(db, UTENTE, "u8@test.it")
        # tipo bando senza riferimento
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tipo="bando")
        # tipo bando con UNO SOLO dei due riferimenti
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tipo="bando", bando_id=42)
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tipo="bando", bando_slug="s")
        # personale con riferimento
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tipo="personale", bando_id=42, bando_slug="s")
        # bando completo: ok
        insert_event(db, UTENTE, tipo="bando", bando_id=42, bando_slug="s")

    def test_coerenza_orari(self, db):
        signup(db, UTENTE, "u8@test.it")
        # tutto il giorno con orario
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tutto_il_giorno=True, ora_inizio="09:00")
        # tutto il giorno con la SOLA ora di fine
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tutto_il_giorno=True, ora_fine="10:00")
        # con orari ma senza inizio
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tutto_il_giorno=False)
        # fine non dopo l'inizio
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, tutto_il_giorno=False, ora_inizio="10:00", ora_fine="10:00")
        # forma valida
        insert_event(db, UTENTE, tutto_il_giorno=False, ora_inizio="09:00", ora_fine="10:30")

    def test_lunghezza_titolo(self, db):
        signup(db, UTENTE, "u8@test.it")
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, titolo="")
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_event(db, UTENTE, titolo="x" * 201)

    def test_una_sola_scadenza_per_bando(self, db):
        signup(db, UTENTE, "u8@test.it")
        insert_event(db, UTENTE, tipo="bando", bando_id=42, bando_slug="s")
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_event(db, UTENTE, tipo="bando", bando_id=42, bando_slug="s")
        # gli eventi personali non sono limitati
        insert_event(db, UTENTE, titolo="A")
        insert_event(db, UTENTE, titolo="B")

    def test_updated_at_trigger(self, db):
        signup(db, UTENTE, "u8@test.it")
        event_id = insert_event(db, UTENTE)
        before = db.execute(
            "select updated_at from public.calendar_events where id = %s", (event_id,)
        ).fetchone()[0]
        db.execute("select pg_sleep(0.01)")
        db.execute(
            "update public.calendar_events set titolo = 'Nuovo' where id = %s", (event_id,)
        )
        after = db.execute(
            "select updated_at from public.calendar_events where id = %s", (event_id,)
        ).fetchone()[0]
        assert after > before

    def test_cascade_da_profiles(self, db):
        signup(db, UTENTE, "u8@test.it")
        insert_event(db, UTENTE)
        db.execute("delete from public.profiles where id = %s", (UTENTE,))
        assert db.execute("select count(*) from public.calendar_events").fetchone()[0] == 0


def test_privilegi_revocati_e_rls(db):
    for table in ("saved_bandi", "calendar_events"):
        checks = db.execute(
            f"""select
                 has_table_privilege('anon', 'public.{table}', 'select'),
                 has_table_privilege('authenticated', 'public.{table}', 'select')"""
        ).fetchone()
        assert not any(checks), table
        assert db.execute(
            "select relrowsecurity from pg_class where relname = %s", (table,)
        ).fetchone()[0], table
