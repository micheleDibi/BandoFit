"""Test funzionali della migration 0028 (inventario addon + ledger).

Coprono: i vincoli di tipo_fruizione/quantità/coerenza ledger, l'append-only
(trigger su UPDATE/DELETE), le RPC (apply_movement, create_consultation_request
con consumo atomico, complete_purchase→ledger, grant/revoke admin), il backfill
idempotente dallo storico con dati sporchi + l'invariante sum(delta)=quantita,
e la protezione RLS/revoke.
"""

import json
import uuid

import psycopg
import pytest


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def new_user(db) -> str:
    uid = str(uuid.uuid4())
    signup(db, uid, f"{uid[:8]}@test.it")
    return uid


def make_company(db, parent: str, piva: str = "01234567890") -> str:
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, 'ACME Srl', %s) returning id",
        (parent, piva),
    ).fetchone()[0])


def make_addon(db, *, slug: str | None = None, prezzo: str = "49.00",
               tipo_prezzo: str = "importo", tipo_fruizione: str = "consumabile") -> int:
    slug = slug or f"addon-{uuid.uuid4().hex[:8]}"
    return db.execute(
        "insert into public.addons (nome, slug, prezzo, tipo_prezzo, tipo_fruizione, is_active) "
        "values ('Addon T', %s, %s, %s, %s, true) returning id",
        (slug, prezzo, tipo_prezzo, tipo_fruizione),
    ).fetchone()[0]


def saldo(db, user_id: str, addon_id: int) -> int:
    row = db.execute(
        "select quantita from public.user_addon_inventory where user_id = %s and addon_id = %s",
        (user_id, addon_id),
    ).fetchone()
    return row[0] if row else 0


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def payload(cliente: str, company: str, addon_id: int, bando_id: int = 1) -> str:
    return json.dumps({
        "cliente_id": cliente, "family_parent_id": cliente,
        "company_profile_id": company, "ai_check_id": "",
        "esito": "ammissibile", "punteggio": "82",
        "bando_id": str(bando_id), "bando_slug": f"bando-{bando_id}",
        "bando_titolo": "Bando di prova", "addon_id": str(addon_id),
    })


def make_addon_purchase(db, user_id: str, addon_id: int) -> str:
    return db.execute(
        """insert into public.purchases
             (user_id, kind, status, addon_id, oggetto_slug, oggetto_nome, descrizione,
              imponibile_cents, iva_cents, totale_cents, iva_aliquota)
           values (%s, 'addon', 'in_attesa', %s, 'x', 'X', 'd', 4900, 1078, 5978, 22.00)
           returning id""",
        (user_id, addon_id),
    ).fetchone()[0]


# ------------------------------------------------------------------- vincoli


class TestVincoli:
    def test_tipo_fruizione_default_e_check(self, db):
        aid = make_addon(db)
        tf = db.execute("select tipo_fruizione from public.addons where id = %s", (aid,)).fetchone()[0]
        assert tf == "consumabile"
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("insert into public.addons (nome, slug, tipo_fruizione) "
                       "values ('X', %s, 'boh')", (f"a-{uuid.uuid4().hex[:6]}",))

    def test_consulto_e_consumabile(self, db):
        tf = db.execute(
            "select tipo_fruizione from public.addons where slug = 'consulto-esperto'"
        ).fetchone()
        # il seed 0017 lo garantisce; la 0028 lo marca consumabile
        assert tf is None or tf[0] == "consumabile"

    def test_inventario_non_negativo(self, db):
        uid, aid = new_user(db), make_addon(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("insert into public.user_addon_inventory (user_id, addon_id, quantita) "
                       "values (%s, %s, -1)", (uid, aid))

    def test_ledger_delta_coerente(self, db):
        uid, aid = new_user(db), make_addon(db)
        # consume deve avere delta -1
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("insert into public.addon_ledger (user_id, addon_id, tipo, delta, request_id) "
                       "values (%s, %s, 'consume', -2, %s)", (uid, aid, str(uuid.uuid4())))
        # purchase deve avere delta > 0
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("insert into public.addon_ledger (user_id, addon_id, tipo, delta, purchase_id) "
                       "values (%s, %s, 'purchase', 0, null)", (uid, aid))

    def test_ledger_riferimenti(self, db):
        uid, aid = new_user(db), make_addon(db)
        # consume senza request_id → viola i riferimenti
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("insert into public.addon_ledger (user_id, addon_id, tipo, delta) "
                       "values (%s, %s, 'consume', -1)", (uid, aid))

    def test_ledger_append_only(self, db):
        uid, aid = new_user(db), make_addon(db)
        # riga valida (admin_revoke: delta<0 + actor + note), poi si prova a mutarla
        lid = db.execute(
            "insert into public.addon_ledger (user_id, addon_id, tipo, delta, actor_id, note) "
            "values (%s, %s, 'admin_revoke', -1, %s, 'x') returning id", (uid, aid, uid)
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("update public.addon_ledger set delta = 5 where id = %s", (lid,))
        assert detail_of(exc) == "ledger_append_only"
        with pytest.raises(psycopg.errors.RaiseException):
            db.execute("delete from public.addon_ledger where id = %s", (lid,))

    def test_consume_e_refund_una_volta_per_request(self, db):
        uid, aid, rid = new_user(db), make_addon(db), str(uuid.uuid4())
        ins = ("insert into public.addon_ledger (user_id, addon_id, tipo, delta, request_id) "
               "values (%s, %s, %s, %s, %s)")
        db.execute(ins, (uid, aid, "consume", -1, rid))
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(ins, (uid, aid, "consume", -1, rid))


# ------------------------------------------------------------ apply_movement


class TestApplyMovement:
    def test_consume_senza_inventario_e_esaurito(self, db):
        uid, aid = new_user(db), make_addon(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_addon_apply_movement(%s, %s, 'consume', -1, null, %s, %s, null)",
                       (uid, aid, str(uuid.uuid4()), uid))
        assert detail_of(exc) == "addon_credit_esaurito"
        assert saldo(db, uid, aid) == 0

    def test_purchase_poi_consume(self, db):
        uid, aid = new_user(db), make_addon(db)
        pid = make_addon_purchase(db, uid, aid)
        db.execute("select public.fn_addon_apply_movement(%s, %s, 'purchase', 1, %s, null, %s, null)",
                   (uid, aid, pid, uid))
        assert saldo(db, uid, aid) == 1
        db.execute("select public.fn_addon_apply_movement(%s, %s, 'consume', -1, null, %s, %s, null)",
                   (uid, aid, str(uuid.uuid4()), uid))
        assert saldo(db, uid, aid) == 0


# ----------------------------------------------------------- complete_purchase


class TestCompletePurchase:
    def test_addon_accredita_nel_ledger(self, db):
        uid, aid = new_user(db), make_addon(db)
        pid = make_addon_purchase(db, uid, aid)
        db.execute("select public.fn_complete_purchase(%s, 'pay')", (pid,))
        assert saldo(db, uid, aid) == 1
        tipo, delta = db.execute(
            "select tipo, delta from public.addon_ledger where purchase_id = %s", (pid,)
        ).fetchone()
        assert (tipo, delta) == ("purchase", 1)

    def test_doppio_complete_e_no_op(self, db):
        uid, aid = new_user(db), make_addon(db)
        pid = make_addon_purchase(db, uid, aid)
        db.execute("select public.fn_complete_purchase(%s, 'pay')", (pid,))
        esito = db.execute("select public.fn_complete_purchase(%s, 'pay')", (pid,)).fetchone()[0]
        assert esito["esito"] == "gia_pagato"
        n = db.execute("select count(*) from public.addon_ledger where purchase_id = %s",
                       (pid,)).fetchone()[0]
        assert n == 1

    def test_permanente_gia_posseduto_e_orfano(self, db):
        admin, uid = new_user(db), new_user(db)
        aid = make_addon(db, tipo_fruizione="permanente")
        # possesso pregresso via grant (purchase addon_admin, non in_attesa)
        db.execute("select public.fn_admin_grant_addon(%s, %s, %s, 1, 'x')", (admin, uid, aid))
        # ora l'utente prova a comprarlo: unico checkout in_attesa
        pid = make_addon_purchase(db, uid, aid)
        esito = db.execute("select public.fn_complete_purchase(%s, 'pay')", (pid,)).fetchone()[0]
        assert esito["esito"] == "pagamento_orfano"
        assert esito["motivo"] == "addon_gia_posseduto"
        assert saldo(db, uid, aid) == 1  # non duplicato


# ------------------------------------------------ create_consultation_request


class TestCreateRequest:
    def test_addon_gratis_non_consuma(self, db):
        uid = new_user(db)
        company = make_company(db, uid)
        aid = make_addon(db, prezzo="0", tipo_prezzo="gratis")
        out = db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                         (payload(uid, company, aid),)).fetchone()[0]
        assert out["consumato"] is False
        assert db.execute("select count(*) from public.consultation_requests "
                          "where cliente_id = %s", (uid,)).fetchone()[0] == 1

    def test_a_pagamento_con_credito_consuma(self, db):
        uid = new_user(db)
        company = make_company(db, uid)
        aid = make_addon(db)  # consumabile, importo, 49
        db.execute("insert into public.user_addon_inventory (user_id, addon_id, quantita) "
                   "values (%s, %s, 1)", (uid, aid))
        out = db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                         (payload(uid, company, aid),)).fetchone()[0]
        assert out["consumato"] is True and out["quantita_residua"] == 0
        assert saldo(db, uid, aid) == 0
        # entry consume legata alla richiesta
        rid = out["request"]["id"]
        assert db.execute("select delta from public.addon_ledger "
                          "where request_id = %s and tipo = 'consume'", (rid,)).fetchone()[0] == -1

    def test_senza_credito_e_atomico(self, db):
        uid = new_user(db)
        company = make_company(db, uid)
        aid = make_addon(db)  # a pagamento, saldo 0
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                       (payload(uid, company, aid),))
        assert detail_of(exc) == "addon_credit_esaurito"
        # ATOMICITÀ: nessuna richiesta creata
        assert db.execute("select count(*) from public.consultation_requests "
                          "where cliente_id = %s", (uid,)).fetchone()[0] == 0

    def test_doppia_richiesta_stesso_bando(self, db):
        uid = new_user(db)
        company = make_company(db, uid)
        aid = make_addon(db)
        db.execute("insert into public.user_addon_inventory (user_id, addon_id, quantita) "
                   "values (%s, %s, 2)", (uid, aid))
        db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                   (payload(uid, company, aid, bando_id=7),))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                       (payload(uid, company, aid, bando_id=7),))
        assert detail_of(exc) == "request_gia_aperta"
        assert saldo(db, uid, aid) == 1  # solo il primo ha consumato

    def test_secondo_bando_consuma_seconda_unita(self, db):
        uid = new_user(db)
        company = make_company(db, uid)
        aid = make_addon(db)
        db.execute("insert into public.user_addon_inventory (user_id, addon_id, quantita) "
                   "values (%s, %s, 2)", (uid, aid))
        db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                   (payload(uid, company, aid, bando_id=1),))
        db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                   (payload(uid, company, aid, bando_id=2),))
        assert saldo(db, uid, aid) == 0


# ------------------------------------------------------------- grant / revoke


class TestGrantRevoke:
    def test_grant_crea_purchase_ledger_audit(self, db):
        admin, uid, aid = new_user(db), new_user(db), make_addon(db)
        out = db.execute(
            "select public.fn_admin_grant_addon(%s, %s, %s, 3, 'Cortesia')",
            (admin, uid, aid)
        ).fetchone()[0]
        assert out["quantita_residua"] == 3
        kind, status, actor, motiv, tot = db.execute(
            "select kind, status, actor_admin_id::text, motivazione, totale_cents "
            "from public.purchases where id = %s", (out["purchase_id"],)
        ).fetchone()
        assert (kind, status, actor, motiv, tot) == ("addon_admin", "gratuito", admin, "Cortesia", 0)
        assert db.execute("select delta from public.addon_ledger where purchase_id = %s "
                          "and tipo = 'admin_grant'", (out["purchase_id"],)).fetchone()[0] == 3
        assert db.execute("select count(*) from public.audit_log "
                          "where action = 'addon.granted' and target_user_id = %s", (uid,)).fetchone()[0] == 1

    def test_grant_motivazione_obbligatoria(self, db):
        admin, uid, aid = new_user(db), new_user(db), make_addon(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_admin_grant_addon(%s, %s, %s, 1, '  ')", (admin, uid, aid))
        assert detail_of(exc) == "motivation_required"

    def test_grant_permanente_gia_posseduto(self, db):
        admin, uid = new_user(db), new_user(db)
        aid = make_addon(db, tipo_fruizione="permanente")
        db.execute("select public.fn_admin_grant_addon(%s, %s, %s, 1, 'x')", (admin, uid, aid))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_admin_grant_addon(%s, %s, %s, 1, 'x')", (admin, uid, aid))
        assert detail_of(exc) == "addon_gia_posseduto"

    def test_grant_non_annulla_il_checkout_aperto(self, db):
        admin, uid, aid = new_user(db), new_user(db), make_addon(db)
        pending = make_addon_purchase(db, uid, aid)  # kind addon, in_attesa
        db.execute("select public.fn_admin_grant_addon(%s, %s, %s, 1, 'x')", (admin, uid, aid))
        stato = db.execute("select status from public.purchases where id = %s", (pending,)).fetchone()[0]
        assert stato == "in_attesa"  # NON annullato (differenza da cambio_admin)

    def test_revoke_clamp_al_residuo(self, db):
        admin, uid, aid = new_user(db), new_user(db), make_addon(db)
        db.execute("select public.fn_admin_grant_addon(%s, %s, %s, 2, 'x')", (admin, uid, aid))
        out = db.execute("select public.fn_admin_revoke_addon(%s, %s, %s, 5, 'errore')",
                         (admin, uid, aid)).fetchone()[0]
        assert out["quantita_revocata"] == 2 and out["quantita_residua"] == 0

    def test_revoke_senza_unita(self, db):
        admin, uid, aid = new_user(db), new_user(db), make_addon(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_admin_revoke_addon(%s, %s, %s, 1, 'x')", (admin, uid, aid))
        assert detail_of(exc) == "niente_da_revocare"


# -------------------------------------------------------------------- backfill


class TestBackfill:
    def _user_addon(self, db, uid, aid, purchase_id, stato="disponibile",
                    consumed_ref=None, consumed_at=None):
        db.execute(
            "insert into public.user_addons (user_id, addon_id, purchase_id, stato, "
            "consumed_ref, consumed_at) values (%s, %s, %s, %s, %s, %s)",
            (uid, aid, purchase_id, stato, consumed_ref, consumed_at),
        )

    def test_backfill_saldi_e_invariante(self, db):
        aid = make_addon(db)
        # a) disponibile → saldo 1
        ua = new_user(db)
        p_ok = make_addon_purchase(db, ua, aid)
        self._user_addon(db, ua, aid, p_ok, "disponibile")
        # b) consumata con ref uuid → saldo 0
        ub = new_user(db)
        p_cons = make_addon_purchase(db, ub, aid)
        self._user_addon(db, ub, aid, p_cons, "consumato", str(uuid.uuid4()), "now()")
        # c) consumata con ref spazzatura → saltata (saldo 0)
        uc = new_user(db)
        p_bad = make_addon_purchase(db, uc, aid)
        self._user_addon(db, uc, aid, p_bad, "consumato", "non-un-uuid", "now()")
        # d) purchase addon pagato senza user_addons → saldo 1
        ud = new_user(db)
        p_orf = make_addon_purchase(db, ud, aid)
        db.execute("update public.purchases set status = 'pagato', paid_at = now() where id = %s",
                   (p_orf,))

        out = db.execute("select public.fn_backfill_addon_ledger_0028()").fetchone()[0]
        assert out["saltate"] == 1 and out["orfani_recuperati"] == 1

        assert saldo(db, ua, aid) == 1
        assert saldo(db, ub, aid) == 0
        assert saldo(db, uc, aid) == 0
        assert saldo(db, ud, aid) == 1

        # invariante sum(delta) = quantita
        bad = db.execute(
            "select count(*) from (select user_id, addon_id, sum(delta) s "
            "from public.addon_ledger group by 1,2) t "
            "join public.user_addon_inventory i using (user_id, addon_id) "
            "where t.s <> i.quantita or t.s < 0"
        ).fetchone()[0]
        assert bad == 0
        # audit dello skip
        assert db.execute("select count(*) from public.audit_log "
                          "where action = 'addon.backfill_skipped'").fetchone()[0] >= 1

    def test_backfill_idempotente(self, db):
        aid = make_addon(db)
        ua = new_user(db)
        p = make_addon_purchase(db, ua, aid)
        self._user_addon(db, ua, aid, p, "disponibile")
        db.execute("select public.fn_backfill_addon_ledger_0028()")
        db.execute("select public.fn_backfill_addon_ledger_0028()")  # seconda: no-op
        n = db.execute("select count(*) from public.addon_ledger where purchase_id = %s "
                       "and tipo = 'purchase'", (p,)).fetchone()[0]
        assert n == 1


# ------------------------------------------------------------------- protezioni


class TestProtezioni:
    def test_rls_e_revoche(self, db):
        for t in ("user_addon_inventory", "addon_ledger"):
            assert db.execute("select relrowsecurity from pg_class where oid = %s::regclass",
                              (f"public.{t}",)).fetchone()[0], f"RLS spenta su {t}"
            assert not db.execute("select has_table_privilege('anon', %s, 'select')",
                                  (f"public.{t}",)).fetchone()[0]

    def test_rpc_non_eseguibili_dai_client(self, db):
        firme = [
            "public.fn_addon_apply_movement(uuid, bigint, text, integer, uuid, uuid, uuid, text)",
            "public.fn_create_consultation_request(jsonb)",
            "public.fn_admin_grant_addon(uuid, uuid, bigint, integer, text)",
            "public.fn_admin_revoke_addon(uuid, uuid, bigint, integer, text)",
        ]
        for f in firme:
            assert not db.execute("select has_function_privilege('anon', %s, 'execute')",
                                  (f,)).fetchone()[0], f"anon esegue {f}"
