"""Test funzionali della migration 0015 (identità del progettista).

Ogni test riceve un database fresco clonato dal template con le migration applicate.
"""

import psycopg
import pytest

UTENTE = "a0000000-0000-0000-0000-000000000015"
COLLEGA = "b0000000-0000-0000-0000-000000000015"
ADMIN = "c0000000-0000-0000-0000-000000000015"
INESISTENTE = "d0000000-0000-0000-0000-000000000015"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def promote(db, user_id: str, actor: str = ADMIN) -> str:
    return db.execute(
        "select public.fn_promote_progettista(%s, %s)", (user_id, actor)
    ).fetchone()[0]


def detail_of(excinfo) -> str:
    return excinfo.value.diag.message_detail or ""


class TestPromozione:
    def test_assegna_codice_e_ruolo(self, db):
        signup(db, UTENTE, "p15@test.it")
        codice = promote(db, UTENTE)
        assert codice == "PRG-00001"
        role = db.execute(
            "select role from public.profiles where id = %s", (UTENTE,)
        ).fetchone()[0]
        assert role == "progettista"

    def test_codici_progressivi(self, db):
        signup(db, UTENTE, "p15@test.it")
        signup(db, COLLEGA, "p15b@test.it")
        assert promote(db, UTENTE) == "PRG-00001"
        assert promote(db, COLLEGA) == "PRG-00002"

    def test_ripromozione_riusa_il_codice(self, db):
        """Demozione e ri-promozione: stesso codice, una sola riga."""
        signup(db, UTENTE, "p15@test.it")
        primo = promote(db, UTENTE)
        db.execute("update public.profiles set role = 'cliente' where id = %s", (UTENTE,))
        secondo = promote(db, UTENTE)
        assert secondo == primo
        righe = db.execute("select count(*) from public.progettisti").fetchone()[0]
        assert righe == 1

    def test_azione_in_audit_log(self, db):
        signup(db, UTENTE, "p15@test.it")
        signup(db, ADMIN, "admin15@test.it")
        promote(db, UTENTE)
        actor, action, target, payload = db.execute(
            """select actor_id, action, target_user_id, payload from public.audit_log
               where action = 'admin.progettista_promoted'"""
        ).fetchone()
        assert str(actor) == ADMIN
        assert str(target) == UTENTE
        assert payload["codice"] == "PRG-00001"
        assert payload["ruolo_precedente"] == "cliente"

    def test_utente_inesistente(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            promote(db, INESISTENTE)
        assert detail_of(excinfo) == "user_not_found"


class TestCodiceImmutabile:
    def test_update_del_codice_rifiutato(self, db):
        signup(db, UTENTE, "p15@test.it")
        promote(db, UTENTE)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute(
                "update public.progettisti set codice = 'PRG-99999' where user_id = %s",
                (UTENTE,),
            )
        assert detail_of(excinfo) == "codice_immutabile"

    def test_gli_altri_campi_restano_modificabili(self, db):
        signup(db, UTENTE, "p15@test.it")
        promote(db, UTENTE)
        db.execute(
            "update public.progettisti set bio = 'Esperto di bandi PNRR' where user_id = %s",
            (UTENTE,),
        )
        bio, codice = db.execute(
            "select bio, codice from public.progettisti where user_id = %s", (UTENTE,)
        ).fetchone()
        assert bio == "Esperto di bandi PNRR"
        assert codice == "PRG-00001"

    def test_codice_unico(self, db):
        signup(db, UTENTE, "p15@test.it")
        promote(db, UTENTE)
        signup(db, COLLEGA, "p15b@test.it")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                "insert into public.progettisti (user_id, codice) values (%s, 'PRG-00001')",
                (COLLEGA,),
            )


class TestCascade0015:
    def test_la_riga_muore_col_profilo(self, db):
        signup(db, UTENTE, "p15@test.it")
        promote(db, UTENTE)
        db.execute("delete from auth.users where id = %s", (UTENTE,))
        rimasti = db.execute("select count(*) from public.progettisti").fetchone()[0]
        assert rimasti == 0


class TestSicurezza0015:
    def test_privilegi_revocati(self, db):
        checks = db.execute(
            """select
                 has_table_privilege('anon', 'public.progettisti', 'select'),
                 has_table_privilege('authenticated', 'public.progettisti', 'select'),
                 has_table_privilege('authenticated', 'public.progettisti', 'insert')"""
        ).fetchone()
        assert not any(checks)

    def test_rls_abilitata(self, db):
        attiva = db.execute(
            "select relrowsecurity from pg_class where relname = 'progettisti'"
        ).fetchone()[0]
        assert attiva is True

    def test_rpc_non_eseguibile_dai_client(self, db):
        """PostgREST esporrebbe la funzione come RPC pubblica: senza la revoca
        chiunque potrebbe auto-promuoversi progettista."""
        checks = db.execute(
            """select
                 has_function_privilege('anon', 'public.fn_promote_progettista(uuid, uuid)', 'execute'),
                 has_function_privilege('authenticated', 'public.fn_promote_progettista(uuid, uuid)', 'execute')"""
        ).fetchone()
        assert not any(checks)
