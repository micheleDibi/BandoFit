"""Test funzionali della migration 0014 (valore enum 'progettista').

Ogni test riceve un database fresco clonato dal template con le migration applicate.
"""

UTENTE = "a0000000-0000-0000-0000-000000000014"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


class TestRuoloProgettista:
    def test_enum_ha_i_tre_ruoli(self, db):
        labels = [
            row[0]
            for row in db.execute(
                """select enumlabel from pg_enum
                   join pg_type on pg_type.oid = pg_enum.enumtypid
                   where pg_type.typname = 'user_role'
                   order by enumsortorder"""
            ).fetchall()
        ]
        assert labels == ["admin", "cliente", "progettista"]

    def test_un_profilo_puo_assumere_il_ruolo(self, db):
        signup(db, UTENTE, "progettista14@test.it")
        db.execute(
            "update public.profiles set role = 'progettista' where id = %s", (UTENTE,)
        )
        role = db.execute(
            "select role from public.profiles where id = %s", (UTENTE,)
        ).fetchone()[0]
        assert role == "progettista"

    def test_il_default_resta_cliente(self, db):
        """La migration non deve toccare il provisioning esistente."""
        signup(db, UTENTE, "cliente14@test.it")
        role = db.execute(
            "select role from public.profiles where id = %s", (UTENTE,)
        ).fetchone()[0]
        assert role == "cliente"
