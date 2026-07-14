"""Test funzionali della migration 0022 (posizioni aziendali + telefono E.164).

Coprono il seed della lookup, la risoluzione slug→id nel trigger di signup
(con i fallback difensivi: slug ignoto o disattivato → NULL senza mai
bloccare la registrazione), il testo libero legato a «Altro», la FK sui
profili e il pattern di sicurezza del repo.
"""

import json

import psycopg
import pytest

UTENTE = "a0000000-0000-0000-0000-000000000022"


def signup(db, user_id: str, email: str, metadata: dict | None = None) -> None:
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
        (user_id, email, json.dumps(metadata or {})),
    )


def profilo(db, user_id: str) -> tuple:
    return db.execute(
        """select telefono, job_position_id, job_position_altro
           from public.profiles where id = %s""",
        (user_id,),
    ).fetchone()


class TestSeedPosizioni:
    def test_29_voci_con_slug_unici(self, db):
        totale, distinti = db.execute(
            "select count(*), count(distinct slug) from public.job_positions"
        ).fetchone()
        assert totale == 29
        assert distinti == 29

    def test_tutte_attive_e_altro_in_coda(self, db):
        rows = db.execute(
            "select slug, is_active from public.job_positions order by ordering"
        ).fetchall()
        assert all(attiva for _, attiva in rows)
        assert rows[0][0] == "ceo-ad"
        assert rows[-1][0] == "altro"


class TestTriggerRegistrazione:
    def test_metadata_completi(self, db):
        signup(db, UTENTE, "u22@test.it", {
            "nome": "Mario", "cognome": "Rossi",
            "telefono": " +393471234567 ",
            "job_position_slug": "cto",
        })
        telefono, position_id, altro = profilo(db, UTENTE)
        assert telefono == "+393471234567"  # il trigger fa solo trim
        slug = db.execute(
            "select slug from public.job_positions where id = %s", (position_id,)
        ).fetchone()[0]
        assert slug == "cto"
        assert altro is None

    def test_altro_con_testo_libero(self, db):
        signup(db, UTENTE, "u22@test.it", {
            "job_position_slug": "altro",
            "job_position_altro": " Responsabile qualità ",
        })
        _, position_id, altro = profilo(db, UTENTE)
        assert position_id is not None
        assert altro == "Responsabile qualità"

    def test_testo_libero_ignorato_se_non_altro(self, db):
        signup(db, UTENTE, "u22@test.it", {
            "job_position_slug": "cfo",
            "job_position_altro": "non deve essere salvato",
        })
        _, _, altro = profilo(db, UTENTE)
        assert altro is None

    def test_slug_ignoto_non_blocca_il_signup(self, db):
        signup(db, UTENTE, "u22@test.it", {
            "nome": "Mario", "job_position_slug": "astronauta",
        })
        telefono, position_id, altro = profilo(db, UTENTE)
        assert (telefono, position_id, altro) == (None, None, None)

    def test_posizione_disattivata_come_ignota(self, db):
        db.execute("update public.job_positions set is_active = false where slug = 'cto'")
        signup(db, UTENTE, "u22@test.it", {"job_position_slug": "cto"})
        assert profilo(db, UTENTE)[1] is None

    def test_invito_azienda_senza_nuovi_campi(self, db):
        # I metadata dell'invito non hanno telefono/posizione: profilo creato
        # con NULL e nessun abbonamento (regola family_invite della 0003).
        signup(db, UTENTE, "u22@test.it", {
            "family_invite": "true", "denominazione": "Invitato",
        })
        assert profilo(db, UTENTE) == (None, None, None)
        abbonamenti = db.execute(
            "select count(*) from public.user_subscriptions where user_id = %s",
            (UTENTE,),
        ).fetchone()[0]
        assert abbonamenti == 0

    def test_non_regressione_assegnazione_piano(self, db):
        signup(db, UTENTE, "u22@test.it", {
            "plan_slug": "pro", "job_position_slug": "titolare",
        })
        slug = db.execute(
            """select p.slug from public.user_subscriptions s
               join public.subscription_plans p on p.id = s.plan_id
               where s.user_id = %s""",
            (UTENTE,),
        ).fetchone()[0]
        assert slug == "pro"


class TestCoerenzaAltro:
    """Il trigger di riga trg_profiles_job_position_altro impone la coerenza
    posizione/testo libero su OGNI scrittura: è la difesa race-free che il
    backend non potrebbe dare con un check read-then-write."""

    def _id_di(self, db, slug: str) -> int:
        return db.execute(
            "select id from public.job_positions where slug = %s", (slug,)
        ).fetchone()[0]

    def test_cambio_posizione_azzera_il_testo(self, db):
        signup(db, UTENTE, "u22@test.it", {
            "job_position_slug": "altro", "job_position_altro": "Testo",
        })
        db.execute(
            "update public.profiles set job_position_id = %s where id = %s",
            (self._id_di(db, "cto"), UTENTE),
        )
        assert profilo(db, UTENTE)[2] is None

    def test_testo_su_posizione_non_altro_azzerato(self, db):
        # Lo scenario della race: UPDATE del solo testo mentre la posizione
        # (riga) non è più «Altro» → il trigger lo azzera comunque.
        signup(db, UTENTE, "u22@test.it", {"job_position_slug": "cto"})
        db.execute(
            "update public.profiles set job_position_altro = 'orfano' where id = %s",
            (UTENTE,),
        )
        assert profilo(db, UTENTE)[2] is None

    def test_testo_su_posizione_altro_sopravvive(self, db):
        signup(db, UTENTE, "u22@test.it", {"job_position_slug": "altro"})
        db.execute(
            "update public.profiles set job_position_altro = 'Responsabile qualità' "
            "where id = %s",
            (UTENTE,),
        )
        assert profilo(db, UTENTE)[2] == "Responsabile qualità"

    def test_azzeramento_posizione_azzera_il_testo(self, db):
        signup(db, UTENTE, "u22@test.it", {
            "job_position_slug": "altro", "job_position_altro": "Testo",
        })
        db.execute(
            "update public.profiles set job_position_id = null where id = %s",
            (UTENTE,),
        )
        assert profilo(db, UTENTE) == (None, None, None)


class TestFkProfili:
    def test_id_inesistente_rifiutato(self, db):
        signup(db, UTENTE, "u22@test.it")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute(
                "update public.profiles set job_position_id = 999999 where id = %s",
                (UTENTE,),
            )

    def test_azzeramento_consentito(self, db):
        signup(db, UTENTE, "u22@test.it", {"job_position_slug": "designer"})
        db.execute(
            "update public.profiles set job_position_id = null where id = %s",
            (UTENTE,),
        )
        assert profilo(db, UTENTE)[1] is None


class TestSicurezza0022:
    def test_privilegi_revocati(self, db):
        checks = db.execute(
            """select
                 has_table_privilege('anon', 'public.job_positions', 'select'),
                 has_table_privilege('authenticated', 'public.job_positions', 'select'),
                 has_table_privilege('authenticated', 'public.job_positions', 'insert')"""
        ).fetchone()
        assert not any(checks)

    def test_rls_abilitata_senza_policy(self, db):
        attiva = db.execute(
            "select relrowsecurity from pg_class where relname = 'job_positions'"
        ).fetchone()[0]
        assert attiva is True
        policy = db.execute(
            "select count(*) from pg_policies where tablename = 'job_positions'"
        ).fetchone()[0]
        assert policy == 0

    def test_updated_at_si_aggiorna(self, db):
        prima = db.execute(
            "select updated_at from public.job_positions where slug = 'altro'"
        ).fetchone()[0]
        db.execute(
            "update public.job_positions set ordering = 991 where slug = 'altro'"
        )
        dopo = db.execute(
            "select updated_at from public.job_positions where slug = 'altro'"
        ).fetchone()[0]
        assert dopo >= prima
