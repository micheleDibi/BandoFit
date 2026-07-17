"""Test funzionali della migration 0024 (multi-azienda, scritture).

Coprono: la rimozione del vincolo UNIQUE su company_profiles.parent_id (un
owner può avere N aziende), fn_create_company sul multi-azienda reale (limite
del piano applicato per un SINGOLO owner), fn_reconcile_companies (archivia le
eccedenti / riattiva le archiviate, tenendo vive le più vecchie) e l'aggancio
del reconcile in fn_switch_plan (downgrade Advisor → archivia; upgrade →
riattiva). I soft-deleted non vengono mai riattivati.
"""

import uuid

import psycopg
import pytest


def signup(db, user_id: str, email: str, plan_slug: str | None = None) -> None:
    import json

    meta = {"plan_slug": plan_slug} if plan_slug else {}
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
        (user_id, email, json.dumps(meta)),
    )
    # Dalla 0026 il trigger non assegna piani a pagamento: il piano richiesto
    # si applica via fn_switch_plan (percorso server legittimo).
    if plan_slug:
        db.execute(
            "select public.fn_switch_plan(%s, "
            "(select id from public.subscription_plans where slug = %s))",
            (user_id, plan_slug),
        )


def new_user(db, plan_slug: str | None = None) -> str:
    uid = str(uuid.uuid4())
    signup(db, uid, f"{uid[:8]}@test.it", plan_slug)
    return uid


def piva(i: int) -> str:
    return f"{i:011d}"


def make_company(db, parent_id: str, i: int = 1, created_at: str | None = None) -> str:
    """Insert diretto (bypassa fn_create_company). `created_at` esplicito per
    controllare l'ordine di anzianità nei test di reconcile."""
    if created_at is None:
        return db.execute(
            "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
            "values (%s, %s, %s) returning id",
            (parent_id, f"ACME {i}", piva(i)),
        ).fetchone()[0]
    return db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva, created_at) "
        "values (%s, %s, %s, %s) returning id",
        (parent_id, f"ACME {i}", piva(i), created_at),
    ).fetchone()[0]


def plan_id(db, slug: str) -> int:
    return db.execute(
        "select id from public.subscription_plans where slug = %s", (slug,)
    ).fetchone()[0]


def live(db, owner: str) -> list[str]:
    """Id delle aziende VIVE (non cancellate né archiviate), dalla più vecchia."""
    return [
        r[0]
        for r in db.execute(
            "select id from public.company_profiles "
            "where parent_id = %s and deleted_at is null and archived_at is null "
            "order by created_at",
            (owner,),
        ).fetchall()
    ]


class TestDropUnique:
    def test_owner_puo_avere_due_aziende(self, db):
        owner = new_user(db, "advisor")
        make_company(db, owner, 1)
        make_company(db, owner, 2)  # stesso parent_id: non deve più fallire
        count = db.execute(
            "select count(*) from public.company_profiles where parent_id = %s", (owner,)
        ).fetchone()[0]
        assert count == 2

    def test_vincolo_unique_assente(self, db):
        found = db.execute(
            "select 1 from pg_constraint where conname = 'company_profiles_parent_id_key'"
        ).fetchone()
        assert found is None


class TestCreateCompanyMulti:
    def _create(self, db, owner: str, i: int):
        return db.execute(
            "select public.fn_create_company(%s, %s, %s)",
            (owner, f"ACME {i}", piva(i)),
        ).fetchone()[0]

    def test_advisor_crea_fino_a_dieci(self, db):
        owner = new_user(db, "advisor")
        for i in range(1, 11):
            self._create(db, owner, i)
        assert len(live(db, owner)) == 10
        # l'undicesima sfora il limite del piano
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            self._create(db, owner, 11)
        assert "company_limit_reached" in str(exc.value)

    def test_non_advisor_seconda_bloccata(self, db):
        owner = new_user(db, "gratuito")
        self._create(db, owner, 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            self._create(db, owner, 2)
        assert "company_limit_reached" in str(exc.value)

    def test_azienda_cancellata_non_conta_nel_limite(self, db):
        owner = new_user(db, "gratuito")  # limite 1
        cid = self._create(db, owner, 1)
        db.execute("select public.fn_soft_delete_company(%s, %s)", (owner, cid))
        # la cancellata non occupa il posto: se ne può creare un'altra
        self._create(db, owner, 2)
        assert len(live(db, owner)) == 1


class TestReconcile:
    def _setup_tre(self, db, owner: str) -> list[str]:
        a = make_company(db, owner, 1, created_at="2026-01-01T00:00:00+00:00")
        b = make_company(db, owner, 2, created_at="2026-02-01T00:00:00+00:00")
        c = make_company(db, owner, 3, created_at="2026-03-01T00:00:00+00:00")
        return [a, b, c]

    def test_downgrade_archivia_le_piu_recenti(self, db):
        owner = new_user(db, "advisor")
        a, b, c = self._setup_tre(db, owner)
        db.execute("update public.profiles set max_aziende_override = 1 where id = %s", (owner,))
        db.execute("select public.fn_reconcile_companies(%s)", (owner,))
        assert live(db, owner) == [a]  # resta la più vecchia
        # b e c archiviate (non cancellate)
        for cid in (b, c):
            deleted, archived = db.execute(
                "select deleted_at, archived_at from public.company_profiles where id = %s", (cid,)
            ).fetchone()
            assert deleted is None and archived is not None

    def test_upgrade_riattiva_le_piu_vecchie(self, db):
        owner = new_user(db, "advisor")
        a, b, c = self._setup_tre(db, owner)
        db.execute("update public.profiles set max_aziende_override = 1 where id = %s", (owner,))
        db.execute("select public.fn_reconcile_companies(%s)", (owner,))
        # sale a 2: si riattiva la più vecchia tra le archiviate (b), non c
        db.execute("update public.profiles set max_aziende_override = 2 where id = %s", (owner,))
        db.execute("select public.fn_reconcile_companies(%s)", (owner,))
        assert live(db, owner) == [a, b]

    def test_idempotente(self, db):
        owner = new_user(db, "advisor")
        a, b, c = self._setup_tre(db, owner)
        db.execute("update public.profiles set max_aziende_override = 2 where id = %s", (owner,))
        db.execute("select public.fn_reconcile_companies(%s)", (owner,))
        prima = live(db, owner)
        db.execute("select public.fn_reconcile_companies(%s)", (owner,))
        assert live(db, owner) == prima == [a, b]

    def test_soft_deleted_non_riattivata(self, db):
        owner = new_user(db, "advisor")
        a, b = make_company(db, owner, 1), make_company(db, owner, 2)
        db.execute("select public.fn_soft_delete_company(%s, %s)", (owner, a))
        # limite alto: nulla da archiviare, e la cancellata NON va riattivata
        db.execute("select public.fn_reconcile_companies(%s)", (owner,))
        assert live(db, owner) == [b]
        assert db.execute(
            "select deleted_at from public.company_profiles where id = %s", (a,)
        ).fetchone()[0] is not None


class TestSwitchPlanDowngrade:
    def test_switch_a_gratuito_archivia(self, db):
        owner = new_user(db, "advisor")
        a = make_company(db, owner, 1, created_at="2026-01-01T00:00:00+00:00")
        make_company(db, owner, 2, created_at="2026-02-01T00:00:00+00:00")
        make_company(db, owner, 3, created_at="2026-03-01T00:00:00+00:00")
        db.execute(
            "select public.fn_switch_plan(%s, %s)", (owner, plan_id(db, "gratuito"))
        )
        assert live(db, owner) == [a]  # limite 1: resta la più vecchia
        # e il piano è davvero cambiato
        slug = db.execute(
            "select sp.slug from public.user_subscriptions us "
            "join public.subscription_plans sp on sp.id = us.plan_id "
            "where us.user_id = %s and us.status = 'active'",
            (owner,),
        ).fetchone()[0]
        assert slug == "gratuito"

    def test_switch_indietro_ad_advisor_riattiva(self, db):
        owner = new_user(db, "advisor")
        ids = [
            make_company(db, owner, i, created_at=f"2026-0{i}-01T00:00:00+00:00")
            for i in (1, 2, 3)
        ]
        db.execute("select public.fn_switch_plan(%s, %s)", (owner, plan_id(db, "gratuito")))
        assert live(db, owner) == ids[:1]
        db.execute("select public.fn_switch_plan(%s, %s)", (owner, plan_id(db, "advisor")))
        assert live(db, owner) == ids  # tutte e tre di nuovo vive


class TestSicurezza:
    def test_reconcile_revocata(self, db):
        for role in ("anon", "authenticated"):
            has = db.execute(
                "select has_function_privilege(%s, 'public.fn_reconcile_companies(uuid)', 'execute')",
                (role,),
            ).fetchone()[0]
            assert has is False
