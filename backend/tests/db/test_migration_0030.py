"""Test funzionali della migration 0030 (motore entitlement).

Coprono: i vincoli di addons.risorsa e purchases.quantita, la canonizzazione
richiamabile dei due addon allocativi (ramo INSERT sul DB vuoto del harness e
ramo UPDATE su righe inscenate), la formula unica base+extra con dormienza e i
wrapper storici, lo snapshot, gli arbitri (inviti/aziende) col limite
effettivo, l'accredito a quantità di fn_complete_purchase, la guardia del
grant admin sugli allocativi ai membri attivi e la riduzione IMMEDIATA (B3)
alla revoca admin e al cambio piano.
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
        return str(db.execute(
            "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
            "values (%s, %s, %s) returning id",
            (parent_id, f"ACME {i}", piva(i)),
        ).fetchone()[0])
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva, created_at) "
        "values (%s, %s, %s, %s) returning id",
        (parent_id, f"ACME {i}", piva(i), created_at),
    ).fetchone()[0])


def make_addon(db, *, risorsa: str | None = None, prezzo: str = "49.00",
               tipo_fruizione: str = "consumabile") -> int:
    return db.execute(
        "insert into public.addons (nome, slug, prezzo, tipo_prezzo, tipo_fruizione, risorsa, is_active) "
        "values ('Addon T', %s, %s, 'importo', %s, %s, true) returning id",
        (f"addon-{uuid.uuid4().hex[:8]}", prezzo, tipo_fruizione, risorsa),
    ).fetchone()[0]


def grant(db, admin: str, user: str, addon_id: int, qty: int) -> None:
    db.execute("select public.fn_admin_grant_addon(%s, %s, %s, %s, 'test')",
               (admin, user, addon_id, qty))


def limit_of(db, user: str, risorsa: str) -> int:
    return db.execute("select public.fn_entitlement_limit(%s, %s)", (user, risorsa)).fetchone()[0]


def invite(db, parent: str, member: str) -> str:
    return str(db.execute(
        "select public.fn_create_family_member(%s, %s, 'Sede', %s, 'existing_user')",
        (parent, member, f"{member[:8]}@test.it"),
    ).fetchone()[0])


def activate_member(db, membership_id: str, joined_offset_days: int = 0) -> None:
    db.execute(
        "update public.family_members set status = 'active', "
        "joined_at = now() - make_interval(days => %s) where id = %s",
        (joined_offset_days, membership_id),
    )


def stati_famiglia(db, parent: str) -> dict[str, int]:
    rows = db.execute(
        "select status, count(*) from public.family_members where parent_id = %s group by status",
        (parent,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def saldo(db, user_id: str, addon_id: int) -> int:
    row = db.execute(
        "select quantita from public.user_addon_inventory where user_id = %s and addon_id = %s",
        (user_id, addon_id),
    ).fetchone()
    return row[0] if row else 0


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


SLUG_SEATS = "profilo-aziendale-aggiuntivo"
SLUG_COMPANIES = "azienda-aggiuntiva"


# ------------------------------------------------------------------- vincoli


class TestVincoli:
    def test_risorsa_check(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("insert into public.addons (nome, slug, risorsa) "
                       "values ('X', %s, 'ai_checks')", (f"a-{uuid.uuid4().hex[:6]}",))

    def test_risorsa_mai_permanente(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("insert into public.addons (nome, slug, risorsa, tipo_fruizione) "
                       "values ('X', %s, 'seats', 'permanente')", (f"a-{uuid.uuid4().hex[:6]}",))

    def test_purchases_quantita_default_e_bound(self, db):
        uid = new_user(db)
        aid = make_addon(db)
        pid = db.execute(
            "insert into public.purchases (user_id, kind, status, addon_id, oggetto_slug, "
            "oggetto_nome, descrizione, imponibile_cents, iva_cents, totale_cents, iva_aliquota) "
            "values (%s, 'addon', 'in_attesa', %s, 'x', 'X', 'd', 100, 25, 125, 25.00) returning id",
            (uid, aid),
        ).fetchone()[0]
        q = db.execute("select quantita from public.purchases where id = %s", (pid,)).fetchone()[0]
        assert q == 1
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("update public.purchases set quantita = 0 where id = %s", (pid,))
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("update public.purchases set quantita = 101 where id = %s", (pid,))


# ------------------------------------------------------------- canonizzazione


class TestCanonizzazione:
    def test_ramo_insert_su_db_vuoto(self, db):
        """Nel harness il catalogo parte vuoto: la migration ha creato le due
        righe INATTIVE (in produzione esistono già: ramo UPDATE)."""
        rows = db.execute(
            "select slug, risorsa, tipo_fruizione, is_active from public.addons "
            "where slug in (%s, %s) order by slug",
            (SLUG_COMPANIES, SLUG_SEATS),
        ).fetchall()
        assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
            (SLUG_COMPANIES, "companies", "consumabile", False),
            (SLUG_SEATS, "seats", "consumabile", False),
        ]

    def test_ramo_update_ripristina_testi(self, db):
        db.execute(
            "update public.addons set nome = 'Vecchio nome', descrizione = 'Vecchia', "
            "is_active = true where slug = %s", (SLUG_SEATS,))
        out = db.execute("select public.fn_canonizza_addon_0030()").fetchone()[0]
        assert out == {"aggiornati": 2, "creati": 0}
        nome, attivo, risorsa = db.execute(
            "select nome, is_active, risorsa from public.addons where slug = %s",
            (SLUG_SEATS,),
        ).fetchone()
        assert nome == "Account collegato aggiuntivo"
        assert attivo is True  # la fn non tocca is_active (lo gestisce l'admin)
        assert risorsa == "seats"


# ------------------------------------------------------------------- formula


class TestFormula:
    def test_seats_base_ed_extra(self, db):
        admin = new_user(db)
        pro = new_user(db, "pro")
        assert limit_of(db, pro, "seats") == 3
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, pro, aid, 2)
        assert limit_of(db, pro, "seats") == 5

    def test_seats_dormiente_se_base_1(self, db):
        admin = new_user(db)
        smart = new_user(db, "smart")
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, smart, aid, 3)
        assert limit_of(db, smart, "seats") == 1

    def test_seats_zero_senza_abbonamento(self, db):
        admin = new_user(db)
        uid = new_user(db)
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, uid, aid, 2)
        db.execute("update public.user_subscriptions set status = 'cancelled' "
                   "where user_id = %s", (uid,))
        assert limit_of(db, uid, "seats") == 0

    def test_companies_base_extra_e_override(self, db):
        admin = new_user(db)
        advisor = new_user(db, "advisor")
        assert limit_of(db, advisor, "companies") == 10
        aid = make_addon(db, risorsa="companies")
        grant(db, admin, advisor, aid, 2)
        assert limit_of(db, advisor, "companies") == 12
        # L'override SOSTITUISCE la base; l'extra resta (override > 1).
        db.execute("update public.profiles set max_aziende_override = 4 where id = %s", (advisor,))
        assert limit_of(db, advisor, "companies") == 6

    def test_companies_dormiente_su_piano_base(self, db):
        admin = new_user(db)
        uid = new_user(db)  # gratuito: max_aziende 1
        aid = make_addon(db, risorsa="companies")
        grant(db, admin, uid, aid, 5)
        assert limit_of(db, uid, "companies") == 1

    def test_ai_checks_base(self, db):
        pro = new_user(db, "pro")
        assert limit_of(db, pro, "ai_checks") == 20

    def test_risorsa_sconosciuta(self, db):
        uid = new_user(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_entitlement_limit(%s, 'boh')", (uid,))
        assert detail_of(exc) == "risorsa_sconosciuta"

    def test_wrapper_identici_alla_formula(self, db):
        admin = new_user(db)
        pro = new_user(db, "pro")
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, pro, aid, 1)
        fam = db.execute("select public.fn_family_limit(%s)", (pro,)).fetchone()[0]
        eff = db.execute("select public.fn_effective_max_aziende(%s)", (pro,)).fetchone()[0]
        assert fam == limit_of(db, pro, "seats") == 4
        assert eff == limit_of(db, pro, "companies") == 1


# ------------------------------------------------------------------ snapshot


class TestSnapshot:
    def test_forma_e_conteggi(self, db):
        admin = new_user(db)
        pro = new_user(db, "pro")
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, pro, aid, 2)
        invite(db, pro, new_user(db))  # pending: occupa un posto
        make_company(db, pro)

        snap = db.execute("select public.fn_entitlement_snapshot(%s)", (pro,)).fetchone()[0]
        assert snap["seats"] == {"base": 3, "extra": 2, "effettivo": 5, "usato": 2, "residuo": 3}
        assert snap["companies"] == {"base": 1, "extra": 0, "effettivo": 1, "usato": 1, "residuo": 0}
        ai = snap["ai_checks"]
        assert (ai["base"], ai["extra"], ai["effettivo"]) == (20, 0, 20)
        assert ai["periodo_inizio"] is not None and ai["periodo_fine"] is not None

    def test_ai_usato_finestra_e_stati(self, db):
        pro = new_user(db, "pro")
        cid = make_company(db, pro)
        base = ("insert into public.ai_checks (company_profile_id, family_parent_id, "
                "bando_id, bando_slug, bando_titolo, status, created_at) "
                "values (%s, %s, 1, 'b', 'B', %s, now() - make_interval(days => %s))")
        db.execute(base, (cid, pro, "pending", 0))
        db.execute(base, (cid, pro, "ready", 0))
        db.execute(base, (cid, pro, "error", 0))    # non conta
        db.execute(base, (cid, pro, "ready", 730))  # fuori finestra
        snap = db.execute("select public.fn_entitlement_snapshot(%s)", (pro,)).fetchone()[0]
        assert snap["ai_checks"]["usato"] == 2
        assert snap["ai_checks"]["residuo"] == 18

    def test_senza_abbonamento(self, db):
        uid = new_user(db)
        db.execute("update public.user_subscriptions set status = 'cancelled' "
                   "where user_id = %s", (uid,))
        snap = db.execute("select public.fn_entitlement_snapshot(%s)", (uid,)).fetchone()[0]
        assert snap["seats"]["effettivo"] == 0
        assert snap["ai_checks"] == {"base": 0, "extra": 0, "effettivo": 0, "usato": 0,
                                     "residuo": 0, "periodo_inizio": None, "periodo_fine": None}


# --------------------------------------------------------- arbitri con extra


class TestArbitriConExtra:
    def test_inviti_fino_all_effettivo(self, db):
        admin = new_user(db)
        pro = new_user(db, "pro")  # base 3 → 2 invitabili
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, pro, aid, 2)  # effettivo 5 → 4 invitabili
        for _ in range(4):
            invite(db, pro, new_user(db))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invite(db, pro, new_user(db))
        assert detail_of(exc) == "family_limit_reached"

    def test_aziende_fino_all_effettivo(self, db):
        admin = new_user(db)
        uid = new_user(db)
        db.execute("update public.profiles set max_aziende_override = 2 where id = %s", (uid,))
        aid = make_addon(db, risorsa="companies")
        grant(db, admin, uid, aid, 1)  # effettivo 3
        for i in range(1, 4):
            db.execute("select public.fn_create_company(%s, %s, %s)",
                       (uid, f"ACME {i}", piva(i)))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_create_company(%s, 'ACME 4', %s)", (uid, piva(4)))
        assert detail_of(exc) == "company_limit_reached"


# ------------------------------------------------- complete_purchase a quantità


class TestCompletePurchaseQuantita:
    def _purchase(self, db, uid: str, aid: int, qty: int) -> str:
        return str(db.execute(
            "insert into public.purchases (user_id, kind, status, addon_id, oggetto_slug, "
            "oggetto_nome, descrizione, imponibile_cents, iva_cents, totale_cents, "
            "iva_aliquota, quantita) "
            "values (%s, 'addon', 'in_attesa', %s, 'x', 'X', 'd', 100, 25, 125, 25.00, %s) "
            "returning id",
            (uid, aid, qty),
        ).fetchone()[0])

    def test_accredita_la_quantita(self, db):
        uid = new_user(db)
        aid = make_addon(db)
        pid = self._purchase(db, uid, aid, 3)
        out = db.execute("select public.fn_complete_purchase(%s, 'pay_1')", (pid,)).fetchone()[0]
        assert out["esito"] == "applicato"
        assert saldo(db, uid, aid) == 3
        rows = db.execute(
            "select tipo, delta from public.addon_ledger where purchase_id = %s", (pid,)
        ).fetchall()
        assert rows == [("purchase", 3)]

    def test_permanente_quantita_diversa_da_1_orfano(self, db):
        uid = new_user(db)
        aid = make_addon(db, tipo_fruizione="permanente")
        pid = self._purchase(db, uid, aid, 2)
        out = db.execute("select public.fn_complete_purchase(%s, 'pay_2')", (pid,)).fetchone()[0]
        assert out == {"esito": "pagamento_orfano", "motivo": "quantita_non_valida"}
        assert saldo(db, uid, aid) == 0
        status = db.execute("select status from public.purchases where id = %s", (pid,)).fetchone()[0]
        assert status == "pagato"  # i soldi restano registrati, il chiamante segnala


# --------------------------------------------------------------- grant admin


class TestAdminGrant:
    def test_persiste_quantita_in_colonna(self, db):
        admin, uid = new_user(db), new_user(db)
        aid = make_addon(db)
        grant(db, admin, uid, aid, 7)
        q = db.execute(
            "select quantita from public.purchases where user_id = %s and kind = 'addon_admin'",
            (uid,),
        ).fetchone()[0]
        assert q == 7

    def test_allocativo_vietato_a_membro_attivo(self, db):
        admin = new_user(db)
        pro = new_user(db, "pro")
        member = new_user(db)
        activate_member(db, invite(db, pro, member))
        aid = make_addon(db, risorsa="seats")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            grant(db, admin, member, aid, 1)
        assert detail_of(exc) == "addon_risorsa_solo_titolare"
        # Un addon NORMALE al membro attivo resta possibile (es. consulto).
        grant(db, admin, member, make_addon(db), 1)

    def test_allocativo_ok_a_membro_retrocesso(self, db):
        admin = new_user(db)
        pro = new_user(db, "pro")
        member = new_user(db)
        mid = invite(db, pro, member)
        activate_member(db, mid)
        db.execute("update public.family_members set status = 'demoted', demoted_at = now() "
                   "where id = %s", (mid,))
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, member, aid, 1)  # account indipendente: legittimo


# ------------------------------------------------- revoca admin → riduzione B3


class TestAdminRevokeReconcile:
    def test_seats_retrocede_i_piu_recenti(self, db):
        admin = new_user(db)
        pro = new_user(db, "pro")  # base 3
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, pro, aid, 2)  # effettivo 5
        vecchio = invite(db, pro, new_user(db))
        activate_member(db, vecchio, joined_offset_days=30)
        recenti = []
        for giorni in (2, 1, 0):
            mid = invite(db, pro, new_user(db))
            activate_member(db, mid, joined_offset_days=giorni)
            recenti.append(mid)

        db.execute("select public.fn_admin_revoke_addon(%s, %s, %s, 2, 'giù')",
                   (admin, pro, aid))

        assert limit_of(db, pro, "seats") == 3
        assert stati_famiglia(db, pro) == {"active": 2, "demoted": 2}
        demoted = {str(r[0]) for r in db.execute(
            "select id from public.family_members where parent_id = %s and status = 'demoted'",
            (pro,)).fetchall()}
        assert demoted == set(recenti[-2:])  # i due con joined_at più recente
        reason = db.execute(
            "select payload ->> 'reason' from public.audit_log "
            "where action = 'family.member_demoted' and family_parent_id = %s limit 1",
            (pro,)).fetchone()[0]
        assert reason == "addon_revoked"

    def test_companies_archivia_le_piu_recenti(self, db):
        admin = new_user(db)
        uid = new_user(db)
        db.execute("update public.profiles set max_aziende_override = 2 where id = %s", (uid,))
        aid = make_addon(db, risorsa="companies")
        grant(db, admin, uid, aid, 2)  # effettivo 4
        ids = [make_company(db, uid, i, created_at=f"2026-01-0{i}") for i in range(1, 5)]

        db.execute("select public.fn_admin_revoke_addon(%s, %s, %s, 2, 'giù')",
                   (admin, uid, aid))

        vive = [str(r[0]) for r in db.execute(
            "select id from public.company_profiles where parent_id = %s "
            "and archived_at is null order by created_at", (uid,)).fetchall()]
        assert vive == ids[:2]  # restano le due più vecchie

    def test_revoca_addon_normale_niente_reconcile(self, db):
        admin, uid = new_user(db), new_user(db)
        aid = make_addon(db)
        grant(db, admin, uid, aid, 2)
        out = db.execute("select public.fn_admin_revoke_addon(%s, %s, %s, 1, 'x')",
                         (admin, uid, aid)).fetchone()[0]
        assert out["quantita_revocata"] == 1
        assert out["quantita_residua"] == 1
        assert out["reconcile_family"] is None


# ------------------------------------------- cambio piano → limite effettivo


class TestApplyPlanChangeEffettivo:
    def test_downgrade_con_extra_seats(self, db):
        admin = new_user(db)
        advisor = new_user(db, "advisor")  # base 10
        aid = make_addon(db, risorsa="seats")
        grant(db, admin, advisor, aid, 2)
        for giorni in (40, 30, 20, 10, 0):
            activate_member(db, invite(db, advisor, new_user(db)), joined_offset_days=giorni)

        # pro: base 3 + 2 extra = 5 → 1 titolare + 4 figli; il 5° (più recente) scende.
        db.execute("select public.fn_switch_plan(%s, "
                   "(select id from public.subscription_plans where slug = 'pro'))", (advisor,))
        assert stati_famiglia(db, advisor) == {"active": 4, "demoted": 1}

    def test_downgrade_dormienza_companies(self, db):
        admin = new_user(db)
        advisor = new_user(db, "advisor")
        aid = make_addon(db, risorsa="companies")
        grant(db, admin, advisor, aid, 2)  # effettivo 12
        ids = [make_company(db, advisor, i, created_at=f"2026-02-0{i}") for i in range(1, 4)]

        # smart: base 1 → l'extra è DORMIENTE → resta viva solo la più vecchia.
        db.execute("select public.fn_switch_plan(%s, "
                   "(select id from public.subscription_plans where slug = 'smart'))", (advisor,))
        vive = [str(r[0]) for r in db.execute(
            "select id from public.company_profiles where parent_id = %s "
            "and archived_at is null order by created_at", (advisor,)).fetchall()]
        assert vive == ids[:1]
        # Le unità restano possedute (dormienti), pronte per un re-upgrade.
        assert saldo(db, advisor, aid) == 2

    def test_reupgrade_riattiva_le_aziende(self, db):
        admin = new_user(db)
        advisor = new_user(db, "advisor")
        aid = make_addon(db, risorsa="companies")
        grant(db, admin, advisor, aid, 2)
        ids = [make_company(db, advisor, i, created_at=f"2026-03-0{i}") for i in range(1, 4)]
        db.execute("select public.fn_switch_plan(%s, "
                   "(select id from public.subscription_plans where slug = 'smart'))", (advisor,))
        db.execute("select public.fn_switch_plan(%s, "
                   "(select id from public.subscription_plans where slug = 'advisor'))", (advisor,))
        vive = db.execute(
            "select count(*) from public.company_profiles where parent_id = %s "
            "and archived_at is null", (advisor,)).fetchone()[0]
        assert vive == 3
        assert set(ids) == {str(r[0]) for r in db.execute(
            "select id from public.company_profiles where parent_id = %s "
            "and archived_at is null", (advisor,)).fetchall()}
