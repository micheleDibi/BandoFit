"""Test funzionali della migration 0021 (alert email sui nuovi bandi).

Coprono i seed per piano, le guardie di idempotenza (ledger e registro run),
la suppression list case-insensitive e la RPC ponte su auth.users.
"""

import psycopg
import pytest

UTENTE = "a0000000-0000-0000-0000-000000000021"
UTENTE2 = "b0000000-0000-0000-0000-000000000021"


def signup(db, user_id: str, email: str, confermata: bool = True) -> None:
    db.execute(
        """insert into auth.users (id, email, email_confirmed_at)
           values (%s, %s, case when %s then now() end)""",
        (user_id, email, confermata),
    )


class TestColonnaRitardo:
    def test_seed_per_piano(self, db):
        rows = dict(
            db.execute(
                "select slug, alert_ritardo_giorni from public.subscription_plans"
            ).fetchall()
        )
        assert rows["advisor"] == 1
        assert rows["pro"] == 7
        assert rows["smart"] == 14
        assert rows["gratuito"] is None

    def test_negativo_rifiutato_zero_accettato(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.subscription_plans set alert_ritardo_giorni = -1 where slug = 'pro'"
            )
        db.execute(
            "update public.subscription_plans set alert_ritardo_giorni = 0 where slug = 'advisor'"
        )


class TestAlertSettings:
    def test_default_e_token(self, db):
        signup(db, UTENTE, "u21@test.it")
        signup(db, UTENTE2, "u21b@test.it")
        db.execute(
            "insert into public.bando_alert_settings (user_id) values (%s), (%s)",
            (UTENTE, UTENTE2),
        )
        rows = db.execute(
            "select abilitati, unsubscribe_token from public.bando_alert_settings"
        ).fetchall()
        assert all(abilitati for abilitati, _ in rows)
        assert rows[0][1] != rows[1][1]  # token unici e generati dal default

    def test_cascade_dal_profilo(self, db):
        signup(db, UTENTE, "u21@test.it")
        db.execute(
            "insert into public.bando_alert_settings (user_id) values (%s)", (UTENTE,)
        )
        db.execute("delete from auth.users where id = %s", (UTENTE,))
        conteggio = db.execute(
            "select count(*) from public.bando_alert_settings"
        ).fetchone()[0]
        assert conteggio == 0


class TestLedgerInvii:
    def test_claim_unico_per_coppia(self, db):
        signup(db, UTENTE, "u21@test.it")
        db.execute(
            """insert into public.bando_alert_sends (user_id, bando_id, bando_slug)
               values (%s, 42, 'bando-di-prova')""",
            (UTENTE,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                "insert into public.bando_alert_sends (user_id, bando_id) values (%s, 42)",
                (UTENTE,),
            )
        # L'arbiter dell'upsert PostgREST: on conflict do nothing non duplica.
        # Dal 0023 il vincolo include company_profile_id (NULLS NOT DISTINCT):
        # per il ledger legacy la company è NULL e continua a deduplicare.
        db.execute(
            """insert into public.bando_alert_sends (user_id, bando_id)
               values (%s, 42)
               on conflict (user_id, company_profile_id, bando_id) do nothing""",
            (UTENTE,),
        )
        conteggio = db.execute(
            "select count(*) from public.bando_alert_sends"
        ).fetchone()[0]
        assert conteggio == 1

    def test_stato_invalido_rifiutato(self, db):
        signup(db, UTENTE, "u21@test.it")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """insert into public.bando_alert_sends (user_id, bando_id, stato)
                   values (%s, 1, 'spedita')""",
                (UTENTE,),
            )


class TestRegistroRun:
    def test_claim_per_giorno(self, db):
        db.execute("insert into public.bando_alert_runs (giorno) values ('2026-07-14')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                "insert into public.bando_alert_runs (giorno) values ('2026-07-14')"
            )


class TestSuppressions:
    def test_unicita_case_insensitive(self, db):
        db.execute(
            """insert into public.email_suppressions (email, motivo)
               values ('Persona@Test.it', 'hard_bounce')"""
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                """insert into public.email_suppressions (email, motivo)
                   values ('persona@test.it', 'manuale')"""
            )

    def test_motivo_invalido(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """insert into public.email_suppressions (email, motivo)
                   values ('x@test.it', 'soft_bounce')"""
            )


class TestEmailVerificate:
    def test_ritorna_solo_i_confermati(self, db):
        signup(db, UTENTE, "confermato@test.it", confermata=True)
        signup(db, UTENTE2, "sospeso@test.it", confermata=False)
        rows = db.execute(
            "select * from public.fn_email_verificate(%s::uuid[])",
            ([UTENTE, UTENTE2],),
        ).fetchall()
        assert [str(r[0]) for r in rows] == [UTENTE]

    def test_rpc_non_eseguibile_dai_client(self, db):
        checks = db.execute(
            """select
                 has_function_privilege('anon', 'public.fn_email_verificate(uuid[])', 'execute'),
                 has_function_privilege('authenticated', 'public.fn_email_verificate(uuid[])', 'execute')"""
        ).fetchone()
        assert not any(checks)


class TestSicurezza0021:
    TABELLE = [
        "bando_alert_settings",
        "bando_alert_sends",
        "bando_alert_runs",
        "email_suppressions",
    ]

    def test_privilegi_revocati(self, db):
        for tabella in self.TABELLE:
            checks = db.execute(
                f"""select
                     has_table_privilege('anon', 'public.{tabella}', 'select'),
                     has_table_privilege('authenticated', 'public.{tabella}', 'select'),
                     has_table_privilege('authenticated', 'public.{tabella}', 'insert')"""
            ).fetchone()
            assert not any(checks), tabella

    def test_rls_abilitata(self, db):
        for tabella in self.TABELLE:
            attiva = db.execute(
                "select relrowsecurity from pg_class where relname = %s", (tabella,)
            ).fetchone()[0]
            assert attiva is True, tabella
