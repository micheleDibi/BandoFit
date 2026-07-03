"""Test funzionali delle migration del DB primario (famiglia, provisioning, piani).

Ogni test riceve un database fresco clonato dal template con le migration applicate.
"""

import psycopg
import pytest

PADRE = "a0000000-0000-0000-0000-000000000001"
FIGLIO1 = "b0000000-0000-0000-0000-000000000001"
FIGLIO2 = "b0000000-0000-0000-0000-000000000002"
FIGLIO3 = "b0000000-0000-0000-0000-000000000003"
ELENA = "c0000000-0000-0000-0000-000000000001"
GRATUITO = "d0000000-0000-0000-0000-000000000001"


def signup(db, user_id: str, email: str, metadata: str = "{}") -> None:
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s::jsonb)",
        (user_id, email, metadata),
    )


def invite(db, user_id: str, email: str, denominazione: str = "Invitato") -> None:
    signup(db, user_id, email,
           f'{{"family_invite":"true","denominazione":"{denominazione}"}}')


def active_plan_slug(db, user_id: str) -> str | None:
    row = db.execute(
        """select sp.slug from public.user_subscriptions us
           join public.subscription_plans sp on sp.id = us.plan_id
           where us.user_id = %s and us.status = 'active'""",
        (user_id,),
    ).fetchone()
    return row[0] if row else None


def member_status(db, member_id: str) -> str | None:
    """Stato della membership CORRENTE (None se solo righe terminali: removed/declined)."""
    row = db.execute(
        "select status from public.family_members where member_id = %s "
        "and status in ('pending', 'active', 'demoted')",
        (member_id,),
    ).fetchone()
    return row[0] if row else None


def create_member(db, parent: str, member: str, email: str, kind: str = "new_user") -> str:
    return db.execute(
        "select public.fn_create_family_member(%s, %s, %s, %s, %s)",
        (parent, member, "Denominazione", email, kind),
    ).fetchone()[0]


def membership_id(db, member: str) -> str:
    return db.execute(
        "select id from public.family_members where member_id = %s "
        "and status in ('pending','active','demoted')",
        (member,),
    ).fetchone()[0]


def detail_of(excinfo) -> str:
    return excinfo.value.diag.message_detail or ""


def plan_id(db, slug: str) -> int:
    return db.execute(
        "select id from public.subscription_plans where slug = %s", (slug,)
    ).fetchone()[0]


class TestProvisioning:
    def test_signup_normale_crea_profilo_e_abbonamento(self, db):
        signup(db, PADRE, "padre@test.it", '{"nome":"Paolo","plan_slug":"advisor"}')
        assert active_plan_slug(db, PADRE) == "advisor"

    def test_signup_invitato_senza_abbonamento(self, db):
        invite(db, FIGLIO1, "f1@test.it", "Figlio Uno")
        nome = db.execute(
            "select nome from public.profiles where id = %s", (FIGLIO1,)
        ).fetchone()[0]
        assert nome == "Figlio Uno"
        assert active_plan_slug(db, FIGLIO1) is None

    def test_metadata_malformati_non_bloccano_il_signup(self, db):
        signup(db, PADRE, "x@test.it", '{"plan_slug": ["array","non","valido"]}')
        assert db.execute(
            "select 1 from public.profiles where id = %s", (PADRE,)
        ).fetchone() is not None


class TestCreateMember:
    def _setup_famiglia(self, db, slug="advisor"):
        signup(db, PADRE, "padre@test.it", f'{{"plan_slug":"{slug}"}}')

    def test_creazione_ok(self, db):
        self._setup_famiglia(db)
        invite(db, FIGLIO1, "f1@test.it")
        assert create_member(db, PADRE, FIGLIO1, "f1@test.it")
        assert member_status(db, FIGLIO1) == "pending"

    def test_non_invitabile_se_stesso(self, db):
        self._setup_famiglia(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            create_member(db, PADRE, PADRE, "padre@test.it")
        assert detail_of(exc) == "cannot_invite_self"

    def test_padre_gratuito_bloccato(self, db):
        signup(db, GRATUITO, "free@test.it")
        signup(db, ELENA, "e@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            create_member(db, GRATUITO, ELENA, "e@test.it", "existing_user")
        assert detail_of(exc) == "not_family_parent"

    def test_target_admin_bloccato(self, db):
        self._setup_famiglia(db)
        signup(db, ELENA, "admin@test.it")
        db.execute("select public.promote_to_admin('admin@test.it')")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            create_member(db, PADRE, ELENA, "admin@test.it", "existing_user")
        assert detail_of(exc) == "target_is_admin"

    def test_doppio_invito_stessa_famiglia(self, db):
        self._setup_famiglia(db)
        invite(db, FIGLIO1, "f1@test.it")
        create_member(db, PADRE, FIGLIO1, "f1@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            create_member(db, PADRE, FIGLIO1, "f1@test.it")
        assert detail_of(exc) == "invite_already_pending"

    def test_target_gia_in_altra_famiglia(self, db):
        self._setup_famiglia(db)
        signup(db, ELENA, "padre2@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        create_member(db, ELENA, FIGLIO1, "f1@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            create_member(db, PADRE, FIGLIO1, "f1@test.it")
        assert detail_of(exc) == "already_in_family"

    def test_target_padre_di_famiglia_bloccato(self, db):
        self._setup_famiglia(db)
        signup(db, ELENA, "padre2@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        create_member(db, ELENA, FIGLIO1, "f1@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            create_member(db, PADRE, ELENA, "padre2@test.it", "existing_user")
        assert detail_of(exc) == "target_is_parent"

    def test_limite_include_padre_e_pending(self, db):
        # pro = 3 account inclusi il padre -> 2 inviti ok, il terzo fallisce
        self._setup_famiglia(db, slug="pro")
        invite(db, FIGLIO1, "f1@test.it")
        invite(db, FIGLIO2, "f2@test.it")
        invite(db, FIGLIO3, "f3@test.it")
        create_member(db, PADRE, FIGLIO1, "f1@test.it")
        create_member(db, PADRE, FIGLIO2, "f2@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            create_member(db, PADRE, FIGLIO3, "f3@test.it")
        assert detail_of(exc) == "family_limit_reached"


class TestAcceptDecline:
    def test_accept_cancella_sub_propria_e_attiva(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        signup(db, ELENA, "e@test.it", '{"plan_slug":"smart"}')
        mid = create_member(db, PADRE, ELENA, "e@test.it", "existing_user")
        db.execute("select public.fn_accept_invitation(%s, %s)", (mid, ELENA))
        assert member_status(db, ELENA) == "active"
        assert active_plan_slug(db, ELENA) is None

    def test_accept_di_altro_utente_rifiutato(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        mid = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_accept_invitation(%s, %s)", (mid, ELENA))
        assert detail_of(exc) == "invitation_not_found"

    def test_decline(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        mid = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        db.execute("select public.fn_decline_invitation(%s, %s)", (mid, FIGLIO1))
        assert member_status(db, FIGLIO1) is None  # declined è terminale

    def test_family_full_se_limite_abbassato_dopo_invito(self, db):
        # advisor: padre + 2 inviti; l'admin abbassa il limite a 2 -> il secondo accept fallisce
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"advisor"}')
        invite(db, FIGLIO1, "f1@test.it")
        invite(db, FIGLIO2, "f2@test.it")
        m1 = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        m2 = create_member(db, PADRE, FIGLIO2, "f2@test.it")
        db.execute(
            "update public.subscription_plans set num_account_aziendali = 2 "
            "where slug = 'advisor'"
        )
        db.execute("select public.fn_accept_invitation(%s, %s)", (m1, FIGLIO1))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_accept_invitation(%s, %s)", (m2, FIGLIO2))
        assert detail_of(exc) == "family_full"


class TestSwitchPlan:
    def _famiglia_advisor_completa(self, db):
        """padre advisor + figlio1, figlio2 attivi (in quest'ordine) + elena pending."""
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"advisor"}')
        invite(db, FIGLIO1, "f1@test.it")
        invite(db, FIGLIO2, "f2@test.it")
        signup(db, ELENA, "e@test.it", '{"plan_slug":"smart"}')
        m1 = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        m2 = create_member(db, PADRE, FIGLIO2, "f2@test.it")
        create_member(db, PADRE, ELENA, "e@test.it", "existing_user")
        db.execute("select public.fn_accept_invitation(%s, %s)", (m1, FIGLIO1))
        # joined_at successivo: figlio2 è il più recente
        db.execute("select pg_sleep(0.02)")
        db.execute("select public.fn_accept_invitation(%s, %s)", (m2, FIGLIO2))

    def test_downgrade_revoca_pending_poi_retrocede_i_piu_recenti(self, db):
        self._famiglia_advisor_completa(db)
        result = db.execute(
            "select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "smart"))
        ).fetchone()[0]
        # smart = 1 account: pending revocato, entrambi i figli retrocessi
        assert member_status(db, ELENA) is None  # removed è terminale
        assert member_status(db, FIGLIO1) == "demoted"
        assert member_status(db, FIGLIO2) == "demoted"
        # ordine: il più recente (figlio2) retrocesso per primo
        demoted_ids = [d["member_id"] for d in result["demoted"]]
        assert demoted_ids == [FIGLIO2, FIGLIO1]
        assert [r["member_id"] for r in result["revoked_pending"]] == [ELENA]
        # i retrocessi hanno un gratuito fresco
        assert active_plan_slug(db, FIGLIO1) == "gratuito"
        assert active_plan_slug(db, FIGLIO2) == "gratuito"

    def test_downgrade_parziale_mantiene_i_piu_vecchi(self, db):
        self._famiglia_advisor_completa(db)
        db.execute("select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "pro")))
        # pro = 3: resta il padre + figlio1 (più vecchio) + figlio2? 1+2=3 ok, ma
        # c'è anche il pending: 1+3=4 > 3 -> revoca pending, i due attivi restano.
        assert member_status(db, ELENA) is None
        assert member_status(db, FIGLIO1) == "active"
        assert member_status(db, FIGLIO2) == "active"

    def test_upgrade_non_riattiva(self, db):
        self._famiglia_advisor_completa(db)
        db.execute("select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "smart")))
        db.execute("select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "advisor")))
        assert member_status(db, FIGLIO1) == "demoted"
        assert member_status(db, FIGLIO2) == "demoted"

    def test_figlio_attivo_non_cambia_piano(self, db):
        self._famiglia_advisor_completa(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_switch_plan(%s, %s)", (FIGLIO1, plan_id(db, "pro")))
        assert detail_of(exc) == "child_plan_locked"

    def test_figlio_retrocesso_puo_cambiare_il_proprio_piano(self, db):
        self._famiglia_advisor_completa(db)
        db.execute("select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "smart")))
        db.execute("select public.fn_switch_plan(%s, %s)", (FIGLIO1, plan_id(db, "pro")))
        assert active_plan_slug(db, FIGLIO1) == "pro"


class TestRemoveReactivate:
    def _famiglia_pro(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        invite(db, FIGLIO2, "f2@test.it")
        m1 = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        m2 = create_member(db, PADRE, FIGLIO2, "f2@test.it")
        db.execute("select public.fn_accept_invitation(%s, %s)", (m1, FIGLIO1))
        db.execute("select pg_sleep(0.02)")
        db.execute("select public.fn_accept_invitation(%s, %s)", (m2, FIGLIO2))

    def test_rimozione_attivo_da_gratuito(self, db):
        self._famiglia_pro(db)
        result = db.execute(
            "select public.fn_remove_family_member(%s, %s)",
            (PADRE, membership_id(db, FIGLIO1)),
        ).fetchone()[0]
        assert result["prior_status"] == "active"
        assert active_plan_slug(db, FIGLIO1) == "gratuito"
        assert member_status(db, FIGLIO1) is None

    def test_riattivazione_solo_con_posto_libero(self, db):
        self._famiglia_pro(db)
        # downgrade a smart (1): entrambi retrocessi
        db.execute("select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "pro")))
        db.execute("select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "smart")))
        # riattivazione impossibile su smart
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "select public.fn_reactivate_family_member(%s, %s)",
                (PADRE, membership_id(db, FIGLIO1)),
            )
        assert detail_of(exc) == "family_limit_reached"
        # upgrade a pro: ora c'è posto; la riattivazione cancella il gratuito proprio
        db.execute("select public.fn_switch_plan(%s, %s)", (PADRE, plan_id(db, "pro")))
        db.execute(
            "select public.fn_reactivate_family_member(%s, %s)",
            (PADRE, membership_id(db, FIGLIO1)),
        )
        assert member_status(db, FIGLIO1) == "active"
        assert active_plan_slug(db, FIGLIO1) is None


class TestGuards:
    def test_delete_padre_bloccato_finche_ha_membri(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        mid = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        with pytest.raises(psycopg.errors.RaiseException):
            db.execute("delete from public.profiles where id = %s", (PADRE,))
        # dopo la rimozione del membro, la cancellazione riesce
        db.execute("select public.fn_remove_family_member(%s, %s)", (PADRE, mid))
        db.execute("delete from public.profiles where id = %s", (PADRE,))

    def test_delete_figlio_cascata_membership(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        mid = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        db.execute("select public.fn_accept_invitation(%s, %s)", (mid, FIGLIO1))
        db.execute("delete from public.profiles where id = %s", (FIGLIO1,))
        assert member_status(db, FIGLIO1) is None

    def test_privilegi_revocati(self, db):
        checks = db.execute(
            """select
                 has_function_privilege('anon', 'public.fn_switch_plan(uuid,bigint)', 'execute'),
                 has_function_privilege('authenticated',
                   'public.fn_create_family_member(uuid,uuid,text,text,text)', 'execute'),
                 has_table_privilege('anon', 'public.family_members', 'select'),
                 has_table_privilege('authenticated', 'public.company_profiles', 'select'),
                 has_table_privilege('anon', 'public.audit_log', 'select')"""
        ).fetchone()
        assert not any(checks)

    def test_indice_unico_una_famiglia_corrente(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        signup(db, ELENA, "padre2@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        create_member(db, PADRE, FIGLIO1, "f1@test.it")
        # inserimento diretto (bypass della funzione): l'indice unico deve bloccare
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                """insert into public.family_members
                   (parent_id, member_id, denominazione, invited_email, invite_kind)
                   values (%s, %s, 'X', 'f1@test.it', 'new_user')""",
                (ELENA, FIGLIO1),
            )

    def test_audit_registrato(self, db):
        signup(db, PADRE, "padre@test.it", '{"plan_slug":"pro"}')
        invite(db, FIGLIO1, "f1@test.it")
        mid = create_member(db, PADRE, FIGLIO1, "f1@test.it")
        db.execute("select public.fn_accept_invitation(%s, %s)", (mid, FIGLIO1))
        actions = {
            row[0]
            for row in db.execute("select action from public.audit_log").fetchall()
        }
        assert {"family.invite_created", "family.invite_accepted"} <= actions
