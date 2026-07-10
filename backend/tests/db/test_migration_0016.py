"""Test funzionali della migration 0016 (notifiche in-app).

Ogni test riceve un database fresco clonato dal template con le migration applicate.
"""

import psycopg
import pytest

UTENTE = "a0000000-0000-0000-0000-000000000016"
COLLEGA = "b0000000-0000-0000-0000-000000000016"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def store(db, user_id: str, dedup: str = "richiesta:1", titolo: str = "Nuova richiesta") -> None:
    db.execute(
        """insert into public.notifications (user_id, tipo, titolo, dedup_key)
           values (%s, 'consulenza.nuova_richiesta', %s, %s)""",
        (user_id, titolo, dedup),
    )


class TestDedup:
    def test_stessa_chiave_stesso_utente_rifiutata(self, db):
        signup(db, UTENTE, "n16@test.it")
        store(db, UTENTE)
        with pytest.raises(psycopg.errors.UniqueViolation):
            store(db, UTENTE, titolo="Doppione")

    def test_il_constraint_fa_da_arbiter_di_upsert(self, db):
        """`on conflict on constraint` = ciò che PostgREST usa per
        l'ignore-duplicates del fan-out: il retry non duplica."""
        signup(db, UTENTE, "n16@test.it")
        store(db, UTENTE)
        db.execute(
            """insert into public.notifications (user_id, tipo, titolo, dedup_key)
               values (%s, 'consulenza.nuova_richiesta', 'Retry', 'richiesta:1')
               on conflict (user_id, dedup_key) do nothing""",
            (UTENTE,),
        )
        totale, titolo = db.execute(
            "select count(*), min(titolo) from public.notifications where user_id = %s",
            (UTENTE,),
        ).fetchone()
        assert totale == 1
        assert titolo == "Nuova richiesta"

    def test_stessa_chiave_utenti_diversi_ok(self, db):
        signup(db, UTENTE, "n16@test.it")
        signup(db, COLLEGA, "n16b@test.it")
        store(db, UTENTE)
        store(db, COLLEGA)
        totale = db.execute("select count(*) from public.notifications").fetchone()[0]
        assert totale == 2


class TestCascade0016:
    def test_le_notifiche_muoiono_con_lutente(self, db):
        signup(db, UTENTE, "n16@test.it")
        store(db, UTENTE)
        db.execute("delete from auth.users where id = %s", (UTENTE,))
        rimaste = db.execute("select count(*) from public.notifications").fetchone()[0]
        assert rimaste == 0


class TestSicurezza0016:
    def test_privilegi_revocati(self, db):
        checks = db.execute(
            """select
                 has_table_privilege('anon', 'public.notifications', 'select'),
                 has_table_privilege('authenticated', 'public.notifications', 'select'),
                 has_table_privilege('authenticated', 'public.notifications', 'insert')"""
        ).fetchone()
        assert not any(checks)

    def test_rls_abilitata(self, db):
        attiva = db.execute(
            "select relrowsecurity from pg_class where relname = 'notifications'"
        ).fetchone()[0]
        assert attiva is True
