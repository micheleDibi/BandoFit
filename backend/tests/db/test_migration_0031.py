"""Test funzionali della migration 0031 (appartenenza + visibilità aziende
per i membri della famiglia, budget AI-check per membro).

Coprono: la firma nuova di fn_create_family_member (drop della vecchia:
niente overload), la risoluzione dell'appartenenza all'invito, l'invariante
visibilità ⊇ appartenenza (RPC + trigger), le RPC di modifica membro, la
riparazione della membership alla riattivazione, il guard company_has_members
sulla soft-delete, la deroga «prima azienda» di fn_create_company e il
backfill richiamabile su dati inscenati.
"""

import json
import uuid

import psycopg
import pytest


def signup(db, user_id: str, email: str, plan_slug: str | None = None) -> None:
    meta = {"plan_slug": plan_slug} if plan_slug else {}
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
        (user_id, email, json.dumps(meta)),
    )
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
    if created_at is None:
        created_at = f"2026-01-{i:02d}"
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva, created_at) "
        "values (%s, %s, %s, %s) returning id",
        (parent_id, f"ACME {i}", piva(i), created_at),
    ).fetchone()[0])


def invite(db, parent: str, member: str, company: str | None = None,
           budget: int | None = None) -> str:
    return str(db.execute(
        "select public.fn_create_family_member(%s, %s, 'Sede', %s, 'existing_user', %s, %s)",
        (parent, member, f"{member[:8]}@test.it", company, budget),
    ).fetchone()[0])


def membership_row(db, membership_id: str) -> dict:
    row = db.execute(
        "select company_profile_id, ai_check_budget, status from public.family_members "
        "where id = %s", (membership_id,),
    ).fetchone()
    return {"company": str(row[0]) if row[0] else None, "budget": row[1], "status": row[2]}


def access_of(db, membership_id: str) -> set[str]:
    return {str(r[0]) for r in db.execute(
        "select company_profile_id from public.family_member_company_access "
        "where family_member_id = %s", (membership_id,),
    ).fetchall()}


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


class TestFirma:
    def test_una_sola_funzione(self, db):
        # La 0031 DROPPA la firma a 5 argomenti: niente overload/bypass.
        n = db.execute(
            "select count(*) from pg_proc where proname = 'fn_create_family_member'"
        ).fetchone()[0]
        assert n == 1


class TestInvito:
    def test_owner_multi_azienda_richiede_la_scelta(self, db):
        padre = new_user(db, "advisor")
        make_company(db, padre, 1)
        make_company(db, padre, 2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invite(db, padre, new_user(db))
        assert detail_of(exc) == "company_required"

    def test_azienda_altrui_o_archiviata_rifiutata(self, db):
        padre = new_user(db, "advisor")
        make_company(db, padre, 1)
        altrui = make_company(db, new_user(db, "advisor"), 2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invite(db, padre, new_user(db), company=altrui)
        assert detail_of(exc) == "company_not_found"

    def test_unica_azienda_auto_assegnata(self, db):
        padre = new_user(db, "pro")
        cid = make_company(db, padre, 1)
        mid = invite(db, padre, new_user(db), budget=5)
        row = membership_row(db, mid)
        assert row["company"] == cid and row["budget"] == 5
        assert access_of(db, mid) == {cid}

    def test_senza_aziende_appartenenza_null(self, db):
        padre = new_user(db, "pro")
        mid = invite(db, padre, new_user(db))
        row = membership_row(db, mid)
        assert row["company"] is None and row["budget"] is None
        assert access_of(db, mid) == set()

    def test_scelta_esplicita_su_multi(self, db):
        padre = new_user(db, "advisor")
        make_company(db, padre, 1)
        c2 = make_company(db, padre, 2)
        mid = invite(db, padre, new_user(db), company=c2)
        assert membership_row(db, mid)["company"] == c2
        assert access_of(db, mid) == {c2}  # N7: la 1 NON è visibile finché concessa


class TestInvarianteVisibilita:
    def test_trigger_blocca_delete_appartenenza(self, db):
        padre = new_user(db, "pro")
        cid = make_company(db, padre, 1)
        mid = invite(db, padre, new_user(db))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "delete from public.family_member_company_access "
                "where family_member_id = %s and company_profile_id = %s", (mid, cid))
        assert detail_of(exc) == "membership_access_required"

    def test_set_access_deve_includere_appartenenza(self, db):
        padre = new_user(db, "advisor")
        c1 = make_company(db, padre, 1)
        c2 = make_company(db, padre, 2)
        mid = invite(db, padre, new_user(db), company=c1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_set_member_access(%s, %s, %s)",
                       (padre, mid, [c2]))
        assert detail_of(exc) == "membership_access_required"

    def test_set_access_aggiunge_e_toglie(self, db):
        padre = new_user(db, "advisor")
        c1 = make_company(db, padre, 1)
        c2 = make_company(db, padre, 2)
        c3 = make_company(db, padre, 3)
        mid = invite(db, padre, new_user(db), company=c1)
        db.execute("select public.fn_set_member_access(%s, %s, %s)",
                   (padre, mid, [c1, c2, c3]))
        assert access_of(db, mid) == {c1, c2, c3}
        db.execute("select public.fn_set_member_access(%s, %s, %s)",
                   (padre, mid, [c1, c3]))
        assert access_of(db, mid) == {c1, c3}

    def test_cascade_dalla_membership(self, db):
        # Il DELETE in cascata (riga membership rimossa) NON è bloccato dal
        # trigger: a quel punto la membership non esiste più.
        padre = new_user(db, "pro")
        make_company(db, padre, 1)
        mid = invite(db, padre, new_user(db))
        db.execute("delete from public.family_members where id = %s", (mid,))
        assert access_of(db, mid) == set()


class TestModificaMembro:
    def test_cambia_appartenenza_conserva_vecchia_visibilita(self, db):
        padre = new_user(db, "advisor")
        c1 = make_company(db, padre, 1)
        c2 = make_company(db, padre, 2)
        mid = invite(db, padre, new_user(db), company=c1)
        db.execute("select public.fn_set_member_company(%s, %s, %s)", (padre, mid, c2))
        assert membership_row(db, mid)["company"] == c2
        assert access_of(db, mid) == {c1, c2}

    def test_budget(self, db):
        padre = new_user(db, "pro")
        make_company(db, padre, 1)
        mid = invite(db, padre, new_user(db))
        db.execute("select public.fn_set_member_budget(%s, %s, 7)", (padre, mid))
        assert membership_row(db, mid)["budget"] == 7
        db.execute("select public.fn_set_member_budget(%s, %s, null)", (padre, mid))
        assert membership_row(db, mid)["budget"] is None  # illimitato

    def test_membro_di_altro_padre_rifiutato(self, db):
        padre = new_user(db, "pro")
        altro = new_user(db, "pro")
        make_company(db, padre, 1)
        mid = invite(db, padre, new_user(db))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_set_member_budget(%s, %s, 1)", (altro, mid))
        assert detail_of(exc) == "member_not_found"


class TestRiattivazione:
    def test_ripara_appartenenza_archiviata(self, db):
        padre = new_user(db, "advisor")
        c1 = make_company(db, padre, 1)
        c2 = make_company(db, padre, 2)
        mid = invite(db, padre, new_user(db), company=c2)
        db.execute("update public.family_members set status='active', joined_at=now() "
                   "where id = %s", (mid,))
        # la sua azienda viene archiviata (es. reconcile da downgrade)
        db.execute("update public.company_profiles set archived_at = now() where id = %s", (c2,))
        db.execute("update public.family_members set status='demoted', demoted_at=now() "
                   "where id = %s", (mid,))
        db.execute("select public.fn_reactivate_family_member(%s, %s)", (padre, mid))
        row = membership_row(db, mid)
        assert row["status"] == "active"
        assert row["company"] == c1  # riassegnata alla più vecchia viva
        assert c1 in access_of(db, mid)


class TestSoftDelete:
    def test_bloccata_con_membri(self, db):
        padre = new_user(db, "pro")
        cid = make_company(db, padre, 1)
        invite(db, padre, new_user(db))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_soft_delete_company(%s, %s)", (padre, cid))
        assert detail_of(exc) == "company_has_members"

    def test_ok_dopo_riassegnazione(self, db):
        padre = new_user(db, "advisor")
        c1 = make_company(db, padre, 1)
        c2 = make_company(db, padre, 2)
        mid = invite(db, padre, new_user(db), company=c1)
        db.execute("select public.fn_set_member_company(%s, %s, %s)", (padre, mid, c2))
        # la visibilità residua su c1 non blocca la cancellazione (solo
        # l'appartenenza conta); la riga access cade in cascade con l'azienda.
        db.execute("select public.fn_soft_delete_company(%s, %s)", (padre, c1))
        deleted = db.execute(
            "select deleted_at is not null from public.company_profiles where id = %s",
            (c1,)).fetchone()[0]
        assert deleted


class TestPrimaAzienda:
    def test_assegnata_ai_membri_senza_appartenenza(self, db):
        padre = new_user(db, "pro")
        mid = invite(db, padre, new_user(db))  # owner senza aziende → NULL
        cid = str(db.execute(
            "select public.fn_create_company(%s, 'Prima Srl', %s)", (padre, piva(41)),
        ).fetchone()[0])
        row = membership_row(db, mid)
        assert row["company"] == cid
        assert access_of(db, mid) == {cid}

    def test_seconda_azienda_invisibile(self, db):
        padre = new_user(db, "advisor")
        c1 = make_company(db, padre, 1)
        mid = invite(db, padre, new_user(db))
        c2 = str(db.execute(
            "select public.fn_create_company(%s, 'Seconda Srl', %s)", (padre, piva(42)),
        ).fetchone()[0])
        assert membership_row(db, mid)["company"] == c1
        assert c2 not in access_of(db, mid)  # N7


class TestBackfill:
    def test_su_dati_inscenati(self, db):
        padre = new_user(db, "advisor")
        c1 = make_company(db, padre, 1)
        c2 = make_company(db, padre, 2)
        # membership pre-0031 simulate: insert diretto senza azienda/budget
        mids = []
        for status in ("pending", "active", "demoted"):
            member = new_user(db)
            mid = str(db.execute(
                "insert into public.family_members (parent_id, member_id, denominazione, "
                "invited_email, invite_kind, status) values (%s, %s, 'X', %s, 'existing_user', %s) "
                "returning id",
                (padre, member, f"{member[:8]}@test.it", status),
            ).fetchone()[0])
            mids.append(mid)

        out = db.execute("select public.fn_backfill_famiglia_0031()").fetchone()[0]
        assert out["membership_assegnate"] == 3
        assert out["budget_azzerati"] == 3
        for mid in mids:
            row = membership_row(db, mid)
            assert row["company"] == c1  # la più vecchia viva
            assert row["budget"] == 0
            assert access_of(db, mid) == {c1, c2}  # TUTTE le vive: zero regressioni

    def test_idempotente_e_non_tocca_i_valorizzati(self, db):
        padre = new_user(db, "pro")
        make_company(db, padre, 1)
        mid = invite(db, padre, new_user(db), budget=9)
        out = db.execute("select public.fn_backfill_famiglia_0031()").fetchone()[0]
        assert out["membership_assegnate"] == 0
        assert membership_row(db, mid)["budget"] == 9
