"""Test funzionali della migration 0005 (dati certificati openapi, preferenze,
registro consumi, lock di import, codice fiscale personale).

Ogni test riceve un database fresco clonato dal template con le migration applicate.
"""

import psycopg
import pytest

PADRE = "a0000000-0000-0000-0000-000000000005"
ALTRO = "a0000000-0000-0000-0000-000000000006"


def signup(db, user_id: str, email: str, metadata: str = "{}") -> None:
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s::jsonb)",
        (user_id, email, metadata),
    )


def create_company(db, parent: str) -> str:
    return db.execute(
        """insert into public.company_profiles (parent_id, ragione_sociale, partita_iva)
           values (%s, 'ACME Srl', '01234567890') returning id""",
        (parent,),
    ).fetchone()[0]


def acquire(db, parent: str, ttl: int = 120) -> bool:
    return db.execute(
        "select public.fn_acquire_import_lock(%s, %s)", (parent, ttl)
    ).fetchone()[0]


class TestCodiceFiscale:
    def test_formato_valido_e_invalido(self, db):
        signup(db, PADRE, "p@test.it")
        db.execute(
            "update public.profiles set codice_fiscale = 'RSSMRA80A01H501U' where id = %s",
            (PADRE,),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.profiles set codice_fiscale = 'troppo-corto' where id = %s",
                (PADRE,),
            )

    def test_cambio_cf_azzera_la_verifica(self, db):
        def verified_at(uid):
            return db.execute(
                "select cf_verified_at from public.profiles where id = %s", (uid,)
            ).fetchone()[0]

        signup(db, PADRE, "p@test.it")
        # flusso di verifica: CF + marca temporale scritti nello stesso UPDATE
        db.execute(
            "update public.profiles set codice_fiscale = 'RSSMRA80A01H501U', "
            "cf_verified_at = now() where id = %s",
            (PADRE,),
        )
        assert verified_at(PADRE) is not None
        # aggiornamento di un ALTRO campo: la verifica resta
        db.execute("update public.profiles set nome = 'Mario' where id = %s", (PADRE,))
        assert verified_at(PADRE) is not None
        # cambio del solo CF: la verifica decade
        db.execute(
            "update public.profiles set codice_fiscale = 'VRDLGI85B02F205X' where id = %s",
            (PADRE,),
        )
        assert verified_at(PADRE) is None


class TestCompanyData:
    def test_una_riga_per_azienda(self, db):
        signup(db, PADRE, "p@test.it")
        cid = create_company(db, PADRE)
        db.execute(
            "insert into public.company_data (company_profile_id, piva_fetched, raw) "
            "values (%s, '01234567890', '{}'::jsonb)",
            (cid,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                "insert into public.company_data (company_profile_id, piva_fetched, raw) "
                "values (%s, '01234567890', '{}'::jsonb)",
                (cid,),
            )

    def test_piva_malformata_rifiutata(self, db):
        signup(db, PADRE, "p@test.it")
        cid = create_company(db, PADRE)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.company_data (company_profile_id, piva_fetched, raw) "
                "values (%s, '12345', '{}'::jsonb)",
                (cid,),
            )

    def test_cascade_da_company_profile(self, db):
        signup(db, PADRE, "p@test.it")
        cid = create_company(db, PADRE)
        db.execute(
            "insert into public.company_data (company_profile_id, piva_fetched, raw) "
            "values (%s, '01234567890', '{}'::jsonb)",
            (cid,),
        )
        db.execute(
            "insert into public.company_people (company_profile_id, kind, nome, raw) "
            "values (%s, 'manager', 'Mario', '{}'::jsonb)",
            (cid,),
        )
        db.execute("delete from public.company_profiles where id = %s", (cid,))
        assert db.execute("select count(*) from public.company_data").fetchone()[0] == 0
        assert db.execute("select count(*) from public.company_people").fetchone()[0] == 0

    def test_kind_persona_vincolato(self, db):
        signup(db, PADRE, "p@test.it")
        cid = create_company(db, PADRE)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.company_people (company_profile_id, kind, raw) "
                "values (%s, 'alieno', '{}'::jsonb)",
                (cid,),
            )


class TestPreferenze:
    def test_unique_e_facet_vincolati(self, db):
        signup(db, PADRE, "p@test.it")
        db.execute(
            "insert into public.user_preferences (user_id, facet, ref_id, label) "
            "values (%s, 'regioni', 9, 'Lazio')",
            (PADRE,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                "insert into public.user_preferences (user_id, facet, ref_id, label) "
                "values (%s, 'regioni', 9, 'Lazio bis')",
                (PADRE,),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.user_preferences (user_id, facet, ref_id, label) "
                "values (%s, 'faccetta_inesistente', 1, 'X')",
                (PADRE,),
            )

    def test_cascade_da_profilo(self, db):
        signup(db, PADRE, "p@test.it")
        db.execute(
            "insert into public.user_preferences (user_id, facet, ref_id, label) "
            "values (%s, 'codici_ateco', 45, '02')",
            (PADRE,),
        )
        db.execute("delete from public.profiles where id = %s", (PADRE,))
        assert db.execute("select count(*) from public.user_preferences").fetchone()[0] == 0


class TestRegistroConsumi:
    def test_outcome_vincolato_e_sopravvive_alle_cancellazioni(self, db):
        signup(db, PADRE, "p@test.it")
        db.execute(
            "insert into public.api_usage_events "
            "(user_id, family_parent_id, provider, service, outcome, cost_cents) "
            "values (%s, %s, 'openapi', 'IT-full', 'success', 30)",
            (PADRE, PADRE),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.api_usage_events (provider, service, outcome) "
                "values ('openapi', 'IT-full', 'boh')"
            )
        # nessuna FK: il registro resta anche dopo la cancellazione dell'utente
        db.execute("delete from public.profiles where id = %s", (PADRE,))
        assert db.execute("select count(*) from public.api_usage_events").fetchone()[0] == 1


class TestImportLock:
    def test_acquisizione_e_rifiuto_concorrente(self, db):
        signup(db, PADRE, "p@test.it")
        assert acquire(db, PADRE) is True
        assert acquire(db, PADRE) is False  # lock ancora valido

    def test_furto_dopo_scadenza(self, db):
        signup(db, PADRE, "p@test.it")
        db.execute(
            "insert into public.company_import_locks (parent_id, expires_at) "
            "values (%s, now() - interval '1 minute')",
            (PADRE,),
        )
        assert acquire(db, PADRE) is True

    def test_release_libera_il_lock(self, db):
        signup(db, PADRE, "p@test.it")
        assert acquire(db, PADRE) is True
        db.execute("select public.fn_release_import_lock(%s)", (PADRE,))
        assert acquire(db, PADRE) is True

    def test_ttl_estremi_clampati(self, db):
        signup(db, PADRE, "p@test.it")
        assert acquire(db, PADRE, ttl=99999) is True
        exp = db.execute(
            "select expires_at <= now() + interval '600 seconds' "
            "from public.company_import_locks where parent_id = %s",
            (PADRE,),
        ).fetchone()[0]
        assert exp is True


class TestSicurezza0005:
    def test_privilegi_revocati(self, db):
        checks = db.execute(
            """select
                 has_table_privilege('anon', 'public.company_data', 'select'),
                 has_table_privilege('authenticated', 'public.company_people', 'select'),
                 has_table_privilege('anon', 'public.user_preferences', 'select'),
                 has_table_privilege('authenticated', 'public.api_usage_events', 'select'),
                 has_table_privilege('anon', 'public.company_import_locks', 'select'),
                 has_function_privilege('anon', 'public.fn_acquire_import_lock(uuid,integer)', 'execute'),
                 has_function_privilege('authenticated', 'public.fn_release_import_lock(uuid)', 'execute')"""
        ).fetchone()
        assert not any(checks)

    def test_rls_abilitata(self, db):
        rows = db.execute(
            """select relname from pg_class
               where relname in ('company_data', 'company_people', 'user_preferences',
                                 'api_usage_events', 'company_import_locks')
                 and relrowsecurity"""
        ).fetchall()
        assert len(rows) == 5
