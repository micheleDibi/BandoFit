"""Test funzionali della migration 0027 (fatturazione SDI): vincoli su
invoices, numerazione atomica per (anno, serie), protezioni RLS/revoke."""

import uuid

import psycopg
import pytest


def _piano(db, slug="pro"):
    return db.execute(
        "select id from public.subscription_plans where slug = %s", (slug,)
    ).fetchone()[0]


def _purchase(db, *, totale=36478) -> str:
    uid = str(uuid.uuid4())
    return db.execute(
        """insert into public.purchases
             (user_id, kind, status, oggetto_slug, oggetto_nome, descrizione,
              imponibile_cents, iva_cents, totale_cents, iva_aliquota)
           values (%s, 'piano', 'pagato', 'pro', 'Pro', 'Abbonamento Pro',
                   29900, 6578, %s, 22.00)
           returning id""",
        (uid, totale),
    ).fetchone()[0]


def _invoice(db, purchase_id, *, anno=2027, serie="", numero=None) -> str:
    return db.execute(
        """insert into public.invoices
             (purchase_id, anno, serie, numero, data_documento,
              imponibile_cents, iva_cents, totale_cents, cliente_snapshot)
           values (%s, %s, %s, %s, '2027-03-01', 29900, 6578, 36478, '{}'::jsonb)
           returning id""",
        (purchase_id, anno, serie, numero),
    ).fetchone()[0]


class TestVincoli:
    def test_una_fattura_per_purchase(self, db):
        p = _purchase(db)
        _invoice(db, p)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _invoice(db, p)

    def test_stato_di_default(self, db):
        p = _purchase(db)
        i = _invoice(db, p)
        stato = db.execute(
            "select stato from public.invoices where id = %s", (i,)
        ).fetchone()[0]
        assert stato == "da_emettere"

    def test_stato_invalido_rifiutato(self, db):
        p = _purchase(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.invoices "
                "(purchase_id, anno, data_documento, stato, imponibile_cents, "
                "iva_cents, totale_cents, cliente_snapshot) "
                "values (%s, 2027, '2027-03-01', 'boh', 1, 0, 1, '{}'::jsonb)",
                (p,),
            )

    def test_numero_progressivo_univoco(self, db):
        p1, p2 = _purchase(db), _purchase(db)
        _invoice(db, p1, numero=1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _invoice(db, p2, anno=2027, serie="", numero=1)

    def test_stesso_numero_serie_diversa_ok(self, db):
        p1, p2 = _purchase(db), _purchase(db)
        _invoice(db, p1, serie="", numero=1)
        _invoice(db, p2, serie="A", numero=1)  # serie diversa: nessun conflitto


class TestNumerazione:
    def test_progressivo_atomico(self, db):
        n1 = db.execute("select public.fn_next_invoice_number(2027, '')").fetchone()[0]
        n2 = db.execute("select public.fn_next_invoice_number(2027, '')").fetchone()[0]
        n3 = db.execute("select public.fn_next_invoice_number(2027, '')").fetchone()[0]
        assert [n1, n2, n3] == [1, 2, 3]

    def test_serie_indipendenti(self, db):
        db.execute("select public.fn_next_invoice_number(2027, '')")
        a = db.execute("select public.fn_next_invoice_number(2027, 'A')").fetchone()[0]
        assert a == 1  # la serie A parte da capo

    def test_anni_indipendenti(self, db):
        db.execute("select public.fn_next_invoice_number(2027, '')")
        assert db.execute(
            "select public.fn_next_invoice_number(2028, '')"
        ).fetchone()[0] == 1


class TestProtezioni:
    def test_rls_e_revoche(self, db):
        for t in ("invoices", "invoice_counters"):
            assert db.execute(
                "select relrowsecurity from pg_class where oid = %s::regclass",
                (f"public.{t}",),
            ).fetchone()[0]
            assert not db.execute(
                "select has_table_privilege('anon', %s, 'select')", (f"public.{t}",)
            ).fetchone()[0]

    def test_rpc_non_eseguibile_dai_client(self, db):
        assert not db.execute(
            "select has_function_privilege('anon', "
            "'public.fn_next_invoice_number(integer, text)', 'execute')"
        ).fetchone()[0]
