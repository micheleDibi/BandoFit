"""Test funzionali della migration 0013 (staging dell'import P.IVA).

Ogni test riceve un database fresco clonato dal template con le migration applicate.
"""

import psycopg
import pytest

PADRE = "a0000000-0000-0000-0000-000000000013"

PAYLOAD = '{"companyDetails": {"companyName": "ACME Srl"}}'


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def store_draft(db, parent: str, piva: str = "01234567890", minuti: int = 30) -> None:
    db.execute(
        """insert into public.company_import_drafts (parent_id, partita_iva, raw, expires_at)
           values (%s, %s, %s::jsonb, now() + make_interval(mins => %s))""",
        (parent, piva, PAYLOAD, minuti),
    )


class TestImportDrafts:
    def test_una_riga_per_titolare(self, db):
        signup(db, PADRE, "padre13@test.it")
        store_draft(db, PADRE)
        with pytest.raises(psycopg.errors.UniqueViolation):
            store_draft(db, PADRE, piva="09876543210")

    def test_upsert_sostituisce_lanteprima_precedente(self, db):
        signup(db, PADRE, "padre13@test.it")
        store_draft(db, PADRE)
        db.execute(
            """insert into public.company_import_drafts (parent_id, partita_iva, raw, expires_at)
               values (%s, '09876543210', %s::jsonb, now() + interval '30 minutes')
               on conflict (parent_id) do update
                 set partita_iva = excluded.partita_iva, raw = excluded.raw,
                     expires_at = excluded.expires_at, fetched_at = now()""",
            (PADRE, PAYLOAD),
        )
        piva = db.execute(
            "select partita_iva from public.company_import_drafts where parent_id = %s",
            (PADRE,),
        ).fetchone()[0]
        assert piva == "09876543210"

    def test_piva_malformata_rifiutata(self, db):
        signup(db, PADRE, "padre13@test.it")
        with pytest.raises(psycopg.errors.CheckViolation):
            store_draft(db, PADRE, piva="IT01234567890")

    def test_cascade_dal_profilo(self, db):
        """Un utente cancellato non lascia in giro il payload della sua azienda."""
        signup(db, PADRE, "padre13@test.it")
        store_draft(db, PADRE)
        db.execute("delete from auth.users where id = %s", (PADRE,))
        rimasti = db.execute("select count(*) from public.company_import_drafts").fetchone()[0]
        assert rimasti == 0

    def test_draft_scaduto_resta_leggibile_solo_a_chi_non_filtra(self, db):
        """Nessun job di pulizia: la scadenza è un filtro, non una cancellazione.
        Il backend legge sempre con `expires_at > now()`."""
        signup(db, PADRE, "padre13@test.it")
        store_draft(db, PADRE, minuti=-1)
        validi = db.execute(
            "select count(*) from public.company_import_drafts where expires_at > now()"
        ).fetchone()[0]
        assert validi == 0


class TestSicurezza0013:
    def test_privilegi_revocati(self, db):
        checks = db.execute(
            """select
                 has_table_privilege('anon', 'public.company_import_drafts', 'select'),
                 has_table_privilege('authenticated', 'public.company_import_drafts', 'select'),
                 has_table_privilege('authenticated', 'public.company_import_drafts', 'insert')"""
        ).fetchone()
        assert not any(checks)

    def test_rls_abilitata(self, db):
        attiva = db.execute(
            "select relrowsecurity from pg_class where relname = 'company_import_drafts'"
        ).fetchone()[0]
        assert attiva is True
