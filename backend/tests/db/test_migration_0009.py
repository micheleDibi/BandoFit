"""Test funzionali della migration 0009 (catalogo add-on)."""

import psycopg
import pytest


def insert_addon(db, slug: str = "pacchetto-ai", **overrides) -> int:
    row = {"nome": "Pacchetto AI", "slug": slug, "prezzo": "49.00"}
    row.update(overrides)
    return db.execute(
        """insert into public.addons (nome, slug, prezzo)
           values (%(nome)s, %(slug)s, %(prezzo)s) returning id""",
        row,
    ).fetchone()[0]


class TestAddons:
    def test_slug_unico(self, db):
        insert_addon(db, "pacchetto-ai")
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_addon(db, "pacchetto-ai")
        insert_addon(db, "alert-plus")  # slug diverso: ok

    def test_prezzo_non_negativo(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_addon(db, prezzo="-1.00")

    def test_default(self, db):
        addon_id = insert_addon(db)
        row = db.execute(
            "select prezzo, ordering, is_active from public.addons where id = %s",
            (addon_id,),
        ).fetchone()
        assert str(row[0]) == "49.00"
        assert row[1] == 0
        assert row[2] is True

    def test_updated_at_trigger(self, db):
        addon_id = insert_addon(db)
        before = db.execute(
            "select updated_at from public.addons where id = %s", (addon_id,)
        ).fetchone()[0]
        db.execute("select pg_sleep(0.01)")
        db.execute("update public.addons set nome = 'Nuovo' where id = %s", (addon_id,))
        after = db.execute(
            "select updated_at from public.addons where id = %s", (addon_id,)
        ).fetchone()[0]
        assert after > before

    def test_privilegi_revocati_e_rls(self, db):
        checks = db.execute(
            """select
                 has_table_privilege('anon', 'public.addons', 'select'),
                 has_table_privilege('authenticated', 'public.addons', 'select')"""
        ).fetchone()
        assert not any(checks)
        assert db.execute(
            "select relrowsecurity from pg_class where relname = 'addons'"
        ).fetchone()[0]
