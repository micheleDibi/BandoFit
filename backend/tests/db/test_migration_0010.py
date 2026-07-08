"""Test funzionali della migration 0010 (modalità di visualizzazione prezzo)."""

import psycopg
import pytest

TABELLE = ("subscription_plans", "addons")


def insert_plan(db, slug: str = "piano-test", **overrides) -> int:
    row = {"nome": "Piano test", "slug": slug}
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join(f"%({c})s" for c in row)
    return db.execute(
        f"insert into public.subscription_plans ({columns}) "
        f"values ({placeholders}) returning id",
        row,
    ).fetchone()[0]


def insert_addon(db, slug: str = "addon-test", **overrides) -> int:
    row = {"nome": "Addon test", "slug": slug, "prezzo": "49.00"}
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join(f"%({c})s" for c in row)
    return db.execute(
        f"insert into public.addons ({columns}) "
        f"values ({placeholders}) returning id",
        row,
    ).fetchone()[0]


class TestTipoPrezzo:
    def test_default_importo(self, db):
        plan_id = insert_plan(db)
        addon_id = insert_addon(db)
        for table, row_id in (("subscription_plans", plan_id), ("addons", addon_id)):
            row = db.execute(
                f"select tipo_prezzo, etichetta_prezzo from public.{table} where id = %s",
                (row_id,),
            ).fetchone()
            assert row == ("importo", None)

    def test_valori_ammessi(self, db):
        for tipo in ("importo", "gratis", "su_richiesta"):
            insert_plan(db, f"piano-{tipo}", tipo_prezzo=tipo)
            insert_addon(db, f"addon-{tipo}", tipo_prezzo=tipo)

    def test_valore_non_valido_respinto(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_plan(db, tipo_prezzo="a_pagamento")
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_addon(db, tipo_prezzo="a_pagamento")

    def test_nessun_check_cross_campo(self, db):
        # Decisione di design: l'etichetta è libera. Con su_richiesta e
        # etichetta NULL la UI mostra il fallback «Su richiesta»; un'etichetta
        # residua con altro tipo è semplicemente ignorata dalla UI.
        insert_plan(db, "su-richiesta-senza-etichetta", tipo_prezzo="su_richiesta")
        insert_addon(db, "importo-con-etichetta", etichetta_prezzo="Parliamone")

    def test_backfill_seed(self, db):
        rows = dict(
            db.execute(
                "select slug, tipo_prezzo from public.subscription_plans "
                "where slug in ('gratuito', 'smart', 'pro', 'advisor')"
            ).fetchall()
        )
        assert rows["gratuito"] == "gratis"
        assert rows["smart"] == rows["pro"] == rows["advisor"] == "importo"
