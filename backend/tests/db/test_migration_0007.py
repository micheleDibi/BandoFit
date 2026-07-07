"""Test funzionali della migration 0007 (AI-check: report + cache estrazioni)."""

import psycopg
import pytest

PADRE = "a0000000-0000-0000-0000-000000000007"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def create_company(db, parent: str) -> str:
    return db.execute(
        """insert into public.company_profiles (parent_id, ragione_sociale, partita_iva)
           values (%s, 'ACME Srl', '01234567890') returning id""",
        (parent,),
    ).fetchone()[0]


def insert_check(db, cid: str, status: str = "pending", bando_id: int = 42) -> str:
    return db.execute(
        """insert into public.ai_checks
           (company_profile_id, family_parent_id, bando_id, bando_slug, bando_titolo, status)
           values (%s, %s, %s, 'bando-x', 'Bando X', %s) returning id""",
        (cid, PADRE, bando_id, status),
    ).fetchone()[0]


class TestAiChecks:
    def test_vincoli_status_esito_punteggio(self, db):
        signup(db, PADRE, "p7@test.it")
        cid = create_company(db, PADRE)
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_check(db, cid, status="boh")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """insert into public.ai_checks
                   (company_profile_id, family_parent_id, bando_id, bando_slug, bando_titolo, esito)
                   values (%s, %s, 1, 's', 't', 'forse')""",
                (cid, PADRE),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """insert into public.ai_checks
                   (company_profile_id, family_parent_id, bando_id, bando_slug, bando_titolo, punteggio)
                   values (%s, %s, 1, 's', 't', 101)""",
                (cid, PADRE),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """insert into public.ai_checks
                   (company_profile_id, family_parent_id, bando_id, bando_slug, bando_titolo, tipo_punteggio)
                   values (%s, %s, 1, 's', 't', 'ufficiale')""",
                (cid, PADRE),
            )

    def test_una_sola_analisi_pending_per_coppia(self, db):
        signup(db, PADRE, "p7@test.it")
        cid = create_company(db, PADRE)
        insert_check(db, cid, "pending", bando_id=42)
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_check(db, cid, "pending", bando_id=42)
        # bando diverso: consentita
        insert_check(db, cid, "pending", bando_id=43)
        # lo storico (ready/error) non è limitato: versioning nativo
        insert_check(db, cid, "ready", bando_id=42)
        insert_check(db, cid, "ready", bando_id=42)
        insert_check(db, cid, "error", bando_id=42)

    def test_cascade_da_company_profile(self, db):
        signup(db, PADRE, "p7@test.it")
        cid = create_company(db, PADRE)
        insert_check(db, cid, "ready")
        db.execute("delete from public.company_profiles where id = %s", (cid,))
        assert db.execute("select count(*) from public.ai_checks").fetchone()[0] == 0

    def test_updated_at_trigger(self, db):
        signup(db, PADRE, "p7@test.it")
        cid = create_company(db, PADRE)
        check_id = insert_check(db, cid, "pending")
        before = db.execute(
            "select updated_at from public.ai_checks where id = %s", (check_id,)
        ).fetchone()[0]
        db.execute("select pg_sleep(0.01)")
        db.execute(
            "update public.ai_checks set status = 'error' where id = %s", (check_id,)
        )
        after = db.execute(
            "select updated_at from public.ai_checks where id = %s", (check_id,)
        ).fetchone()[0]
        assert after > before


class TestBandoRequirements:
    def test_una_riga_per_bando(self, db):
        db.execute(
            """insert into public.bando_requirements
               (bando_id, bando_slug, content_hash, prompt_version, model, extraction)
               values (7, 'bando-7', 'abc', 1, 'claude-sonnet-5', '{}')"""
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                """insert into public.bando_requirements
                   (bando_id, bando_slug, content_hash, prompt_version, model, extraction)
                   values (7, 'bando-7', 'def', 1, 'claude-sonnet-5', '{}')"""
            )

    def test_privilegi_revocati_e_rls(self, db):
        for table in ("ai_checks", "bando_requirements"):
            checks = db.execute(
                f"""select
                     has_table_privilege('anon', 'public.{table}', 'select'),
                     has_table_privilege('authenticated', 'public.{table}', 'select')"""
            ).fetchone()
            assert not any(checks), table
            assert db.execute(
                "select relrowsecurity from pg_class where relname = %s", (table,)
            ).fetchone()[0], table
