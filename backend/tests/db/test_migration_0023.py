"""Test funzionali della migration 0023 (gestione multi-azienda).

Coprono: le nuove colonne (max_aziende di piano, override profilo, ciclo di
vita azienda), gli indici unici parziali dell'overlay company_profile_id
(legacy vs per-azienda, con la correttezza NULLS DISTINCT), le RPC
(fn_effective_max_aziende, fn_create_company col limite race-free e le
validazioni, fn_soft_delete_company), il backfill Advisor e il pattern di
sicurezza del repo (revoke sulle funzioni).

Nota: a livello 0023 `company_profiles.parent_id` è ancora UNIQUE, quindi un
owner ha fisicamente una sola azienda; il limite oltre 1 e l'idempotenza
per-azienda si esercitano con aziende di owner distinti (gli indici non
validano la proprietà) e col caso a limite 1. Il vero multi-azienda per un
singolo owner arriva con la 0024.
"""

import uuid

import psycopg
import pytest

PIVA_A = "12345678901"
PIVA_B = "98765432109"


def signup(db, user_id: str, email: str, plan_slug: str | None = None) -> None:
    meta = {"plan_slug": plan_slug} if plan_slug else {}
    import json

    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
        (user_id, email, json.dumps(meta)),
    )


def new_user(db, plan_slug: str | None = None) -> str:
    uid = str(uuid.uuid4())
    signup(db, uid, f"{uid[:8]}@test.it", plan_slug)
    return uid


def make_company(db, parent_id: str, piva: str = PIVA_A, ragione: str = "ACME Srl") -> str:
    """Inserimento diretto (bypassa fn_create_company) per allestire fixture
    con owner specifici; parent_id resta UNIQUE a livello 0023."""
    return db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, %s, %s) returning id",
        (parent_id, ragione, piva),
    ).fetchone()[0]


class TestColonnePiano:
    def test_advisor_ha_max_aziende_10(self, db):
        val = db.execute(
            "select max_aziende from public.subscription_plans where slug = 'advisor'"
        ).fetchone()[0]
        assert val == 10

    def test_altri_piani_default_1(self, db):
        rows = db.execute(
            "select slug, max_aziende from public.subscription_plans where slug <> 'advisor'"
        ).fetchall()
        assert rows and all(v == 1 for _, v in rows)

    def test_override_profilo_nullable_e_positivo(self, db):
        uid = new_user(db, "gratuito")
        assert db.execute(
            "select max_aziende_override from public.profiles where id = %s", (uid,)
        ).fetchone()[0] is None
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.profiles set max_aziende_override = 0 where id = %s", (uid,)
            )


class TestCicloVitaAzienda:
    def test_colonne_default_null(self, db):
        owner = new_user(db, "advisor")
        cid = make_company(db, owner)
        deleted, archived = db.execute(
            "select deleted_at, archived_at from public.company_profiles where id = %s", (cid,)
        ).fetchone()
        assert deleted is None and archived is None


class TestEffectiveMax:
    def _eff(self, db, uid: str) -> int:
        return db.execute("select public.fn_effective_max_aziende(%s)", (uid,)).fetchone()[0]

    def test_advisor_10(self, db):
        assert self._eff(db, new_user(db, "advisor")) == 10

    def test_non_advisor_1(self, db):
        assert self._eff(db, new_user(db, "gratuito")) == 1

    def test_override_vince_sul_piano(self, db):
        uid = new_user(db, "advisor")
        db.execute(
            "update public.profiles set max_aziende_override = 3 where id = %s", (uid,)
        )
        assert self._eff(db, uid) == 3

    def test_senza_abbonamento_attivo_1(self, db):
        uid = new_user(db, "advisor")
        db.execute(
            "update public.user_subscriptions set status = 'cancelled' where user_id = %s",
            (uid,),
        )
        assert self._eff(db, uid) == 1


class TestCreateCompany:
    def _create(self, db, owner: str, ragione="ACME Srl", piva=PIVA_A):
        return db.execute(
            "select public.fn_create_company(%s, %s, %s)", (owner, ragione, piva)
        ).fetchone()[0]

    def test_advisor_crea_prima_azienda(self, db):
        owner = new_user(db, "advisor")
        cid = self._create(db, owner)
        parent, deleted = db.execute(
            "select parent_id, deleted_at from public.company_profiles where id = %s", (cid,)
        ).fetchone()
        assert str(parent) == owner and deleted is None

    def test_limite_raggiunto_blocca(self, db):
        # Piano gratuito: limite 1. La seconda creazione è respinta PRIMA
        # dell'insert (stesso ramo del limite advisor a 10).
        owner = new_user(db, "gratuito")
        self._create(db, owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            self._create(db, owner, piva=PIVA_B)
        assert exc.value.diag.message_detail == "company_limit_reached"

    def test_piva_invalida_respinta(self, db):
        owner = new_user(db, "advisor")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            self._create(db, owner, piva="123")
        assert exc.value.diag.message_detail == "partita_iva_invalid"

    def test_ragione_vuota_respinta(self, db):
        owner = new_user(db, "advisor")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            self._create(db, owner, ragione="   ")
        assert exc.value.diag.message_detail == "ragione_sociale_required"


class TestSoftDelete:
    def test_soft_delete_imposta_deleted_at(self, db):
        owner = new_user(db, "advisor")
        cid = make_company(db, owner)
        db.execute("select public.fn_soft_delete_company(%s, %s)", (owner, cid))
        assert db.execute(
            "select deleted_at from public.company_profiles where id = %s", (cid,)
        ).fetchone()[0] is not None

    def test_azienda_altrui_respinta(self, db):
        owner = new_user(db, "advisor")
        altro = new_user(db, "advisor")
        cid = make_company(db, owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_soft_delete_company(%s, %s)", (altro, cid))
        assert exc.value.diag.message_detail == "company_not_found"

    def test_gia_cancellata_respinta(self, db):
        owner = new_user(db, "advisor")
        cid = make_company(db, owner)
        db.execute("select public.fn_soft_delete_company(%s, %s)", (owner, cid))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_soft_delete_company(%s, %s)", (owner, cid))
        assert exc.value.diag.message_detail == "company_not_found"


class TestIndiciUniciParziali:
    """La correttezza NULLS DISTINCT: due indici parziali per tabella, uno
    legacy (company_profile_id NULL) e uno per-azienda."""

    def _save(self, db, user_id, company_id, bando_id=1):
        db.execute(
            "insert into public.saved_bandi (user_id, company_profile_id, bando_id, "
            "bando_slug, bando_titolo) values (%s, %s, %s, 'b', 'B')",
            (user_id, company_id, bando_id),
        )

    def test_legacy_duplicato_respinto(self, db):
        u = new_user(db, "gratuito")
        self._save(db, u, None)
        with pytest.raises(psycopg.errors.UniqueViolation):
            self._save(db, u, None)

    def test_stessa_azienda_duplicato_respinto(self, db):
        u = new_user(db, "advisor")
        cid = make_company(db, u)
        self._save(db, u, cid)
        with pytest.raises(psycopg.errors.UniqueViolation):
            self._save(db, u, cid)

    def test_aziende_diverse_stesso_bando_convivono(self, db):
        # Overlay: stesso utente e stesso bando ma aziende diverse → due righe.
        u = new_user(db, "advisor")
        altro = new_user(db, "advisor")
        cid_a = make_company(db, u, piva=PIVA_A)
        cid_b = make_company(db, altro, piva=PIVA_B)  # owner distinto: parent_id UNIQUE a 0023
        self._save(db, u, cid_a)
        self._save(db, u, cid_b)  # l'indice non valida la proprietà, solo la tupla
        n = db.execute(
            "select count(*) from public.saved_bandi where user_id = %s", (u,)
        ).fetchone()[0]
        assert n == 2

    def test_legacy_e_azienda_indipendenti(self, db):
        u = new_user(db, "advisor")
        cid = make_company(db, u)
        self._save(db, u, None)  # legacy
        self._save(db, u, cid)   # azienda: stesso (user, bando) ma scope diverso
        assert db.execute(
            "select count(*) from public.saved_bandi where user_id = %s", (u,)
        ).fetchone()[0] == 2

    def test_calendario_una_scadenza_per_bando_per_azienda(self, db):
        u = new_user(db, "advisor")
        cid = make_company(db, u)

        def add(company_id):
            db.execute(
                "insert into public.calendar_events (user_id, company_profile_id, titolo, "
                "data, tipo, bando_id, bando_slug) values "
                "(%s, %s, 'Scadenza', '2026-09-01', 'bando', 1, 'b')",
                (u, company_id),
            )

        add(cid)
        with pytest.raises(psycopg.errors.UniqueViolation):
            add(cid)


class TestBackfill:
    """Il backfill nella migration gira su DB vuoto (no-op nel template): qui si
    riesegue la STESSA UPDATE su fixture per bloccarne il comportamento
    (Advisor stampato sulla sua azienda, non-Advisor lasciato NULL)."""

    BACKFILL = """
        update public.saved_bandi x
        set company_profile_id = c.id
        from public.company_profiles c
        where c.parent_id = x.user_id
          and c.deleted_at is null
          and x.company_profile_id is null
          and exists (
            select 1 from public.user_subscriptions us
            join public.subscription_plans sp on sp.id = us.plan_id
            where us.user_id = x.user_id and us.status = 'active' and sp.max_aziende > 1
          )
    """

    def _save_legacy(self, db, user_id, bando_id=1):
        db.execute(
            "insert into public.saved_bandi (user_id, bando_id, bando_slug, bando_titolo) "
            "values (%s, %s, 'b', 'B')",
            (user_id, bando_id),
        )

    def test_advisor_stampato_non_advisor_null(self, db):
        advisor = new_user(db, "advisor")
        normale = new_user(db, "gratuito")
        cid = make_company(db, advisor)
        make_company(db, normale, piva=PIVA_B)  # anche il gratuito ha un'azienda
        self._save_legacy(db, advisor)
        self._save_legacy(db, normale)

        db.execute(self.BACKFILL)

        adv_scope = db.execute(
            "select company_profile_id from public.saved_bandi where user_id = %s", (advisor,)
        ).fetchone()[0]
        norm_scope = db.execute(
            "select company_profile_id from public.saved_bandi where user_id = %s", (normale,)
        ).fetchone()[0]
        assert str(adv_scope) == str(cid)
        assert norm_scope is None

    def test_idempotente(self, db):
        advisor = new_user(db, "advisor")
        make_company(db, advisor)
        self._save_legacy(db, advisor)
        db.execute(self.BACKFILL)
        db.execute(self.BACKFILL)  # seconda passata: nessun effetto (già valorizzato)
        n = db.execute(
            "select count(*) from public.saved_bandi where user_id = %s "
            "and company_profile_id is not null",
            (advisor,),
        ).fetchone()[0]
        assert n == 1


class TestSicurezza0023:
    def test_funzioni_revocate(self, db):
        checks = db.execute(
            """select
                 has_function_privilege('anon', 'public.fn_create_company(uuid, text, text)', 'execute'),
                 has_function_privilege('authenticated', 'public.fn_create_company(uuid, text, text)', 'execute'),
                 has_function_privilege('authenticated', 'public.fn_soft_delete_company(uuid, uuid)', 'execute'),
                 has_function_privilege('authenticated', 'public.fn_effective_max_aziende(uuid)', 'execute')"""
        ).fetchone()
        assert not any(checks)
