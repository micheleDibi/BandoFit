"""Test funzionali della migration 0006 (documenti ufficiali dell'azienda)."""

import psycopg
import pytest

PADRE = "a0000000-0000-0000-0000-000000000006"


def signup(db, user_id: str, email: str) -> None:
    db.execute(
        "insert into auth.users (id, email) values (%s, %s)", (user_id, email)
    )


def create_company(db, parent: str) -> str:
    return db.execute(
        """insert into public.company_profiles (parent_id, ragione_sociale, partita_iva)
           values (%s, 'ACME Srl', '01234567890') returning id""",
        (parent,),
    ).fetchone()[0]


def insert_doc(db, cid: str, status: str = "pending") -> str:
    return db.execute(
        """insert into public.company_documents
           (company_profile_id, kind, endpoint, status)
           values (%s, 'visura', 'ordinaria-societa-capitale', %s) returning id""",
        (cid, status),
    ).fetchone()[0]


class TestCompanyDocuments:
    def test_vincoli_kind_e_status(self, db):
        signup(db, PADRE, "p@test.it")
        cid = create_company(db, PADRE)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.company_documents (company_profile_id, kind, endpoint) "
                "values (%s, 'contratto', 'x')",
                (cid,),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.company_documents (company_profile_id, kind, endpoint, status) "
                "values (%s, 'visura', 'x', 'boh')",
                (cid,),
            )

    def test_una_sola_richiesta_pending_per_azienda(self, db):
        signup(db, PADRE, "p@test.it")
        cid = create_company(db, PADRE)
        insert_doc(db, cid, "pending")
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_doc(db, cid, "pending")
        # ma lo storico (ready/error) non è limitato
        insert_doc(db, cid, "ready")
        insert_doc(db, cid, "error")

    def test_cascade_da_company_profile(self, db):
        signup(db, PADRE, "p@test.it")
        cid = create_company(db, PADRE)
        insert_doc(db, cid, "ready")
        db.execute("delete from public.company_profiles where id = %s", (cid,))
        assert db.execute(
            "select count(*) from public.company_documents"
        ).fetchone()[0] == 0

    def test_privilegi_revocati_e_rls(self, db):
        checks = db.execute(
            """select
                 has_table_privilege('anon', 'public.company_documents', 'select'),
                 has_table_privilege('authenticated', 'public.company_documents', 'select')"""
        ).fetchone()
        assert not any(checks)
        assert db.execute(
            "select relrowsecurity from pg_class where relname = 'company_documents'"
        ).fetchone()[0]
