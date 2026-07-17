"""Test funzionali della migration 0026 (modulo pagamenti, fase 1).

Coprono le invarianti che il piano dichiara «per costruzione»: vincoli di
coerenza monetaria e di stato su purchases, unicità (un in_attesa per utente,
un ciclo/tentativo, un cambio programmato), dedup differenziato dei webhook,
il refactor fn_apply_plan_change/fn_switch_plan, le RPC di transizione
(complete/fail/execute/cambio_admin) con la loro idempotenza, la blindatura di
handle_new_user (niente piani a pagamento alla registrazione) e la protezione
RLS/revoke delle tabelle nuove.
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
    # Il trigger non assegna piani a pagamento (è il punto della 0026): i test
    # che partono da un piano pagato lo ottengono via fn_switch_plan.
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


def plan_id(db, slug: str) -> int:
    return db.execute(
        "select id from public.subscription_plans where slug = %s", (slug,)
    ).fetchone()[0]


def active_sub(db, user_id: str):
    """(slug, data_scadenza, auto_renew, grace_until) dell'abbonamento attivo."""
    return db.execute(
        """select p.slug, s.data_scadenza, s.auto_renew, s.grace_until
           from public.user_subscriptions s
           join public.subscription_plans p on p.id = s.plan_id
           where s.user_id = %s and s.status = 'active'""",
        (user_id,),
    ).fetchone()


def make_purchase(db, user_id: str, kind: str, *, slug: str = "pro",
                  status: str = "in_attesa", ciclo=None, tentativo=None,
                  auto_renew=None, order_id=None) -> str:
    pid = plan_id(db, slug) if kind in ("piano", "rinnovo") else None
    aid = None
    if kind == "addon":
        aid = db.execute(
            "insert into public.addons (nome, slug, prezzo) values ('Addon T', %s, 50) "
            "on conflict (slug) do update set nome = excluded.nome returning id",
            (f"addon-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0]
    return db.execute(
        """insert into public.purchases
             (user_id, kind, status, plan_id, addon_id, oggetto_slug, oggetto_nome,
              descrizione, imponibile_cents, iva_cents, totale_cents, iva_aliquota,
              ciclo_rinnovo, tentativo, auto_renew_scelto, revolut_order_id)
           values (%s, %s, %s, %s, %s, %s, 'Oggetto', 'Descrizione',
                   29900, 6578, 36478, 22.00, %s, %s, %s, %s)
           returning id""",
        (user_id, kind, status, pid, aid, slug, ciclo, tentativo, auto_renew, order_id),
    ).fetchone()[0]


def schedule_change(db, user_id: str, to_slug: str, effective, motivo="downgrade") -> str:
    return db.execute(
        """insert into public.scheduled_plan_changes
             (user_id, to_plan_id, effective_date, motivo)
           values (%s, %s, %s, %s) returning id""",
        (user_id, plan_id(db, to_slug), effective, motivo),
    ).fetchone()[0]


def detail_of(excinfo) -> str:
    return excinfo.value.diag.message_detail or ""


# --------------------------------------------------------------------- vincoli


class TestVincoli:
    def test_totale_deve_essere_imponibile_piu_iva(self, db):
        uid = new_user(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """insert into public.purchases
                     (user_id, kind, status, oggetto_slug, oggetto_nome, descrizione,
                      imponibile_cents, iva_cents, totale_cents, iva_aliquota)
                   values (%s, 'addon', 'in_attesa', 'x', 'X', 'd', 100, 22, 999, 22)""",
                (uid,),
            )

    def test_un_solo_in_attesa_per_utente(self, db):
        uid = new_user(db)
        make_purchase(db, uid, "piano")
        with pytest.raises(psycopg.errors.UniqueViolation):
            make_purchase(db, uid, "piano")

    def test_ciclo_e_tentativo_solo_sui_rinnovi(self, db):
        uid = new_user(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            make_purchase(db, uid, "piano", ciclo="2027-01-01", tentativo=1)
        with pytest.raises(psycopg.errors.CheckViolation):
            make_purchase(db, uid, "rinnovo")  # senza ciclo/tentativo

    def test_un_tentativo_per_ciclo(self, db):
        uid = new_user(db)
        make_purchase(db, uid, "rinnovo", status="fallito",
                      ciclo="2027-01-01", tentativo=1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            make_purchase(db, uid, "rinnovo", status="fallito",
                          ciclo="2027-01-01", tentativo=1)

    def test_un_solo_cambio_programmato(self, db):
        uid = new_user(db, "pro")
        schedule_change(db, uid, "smart", "2027-06-01")
        with pytest.raises(psycopg.errors.UniqueViolation):
            schedule_change(db, uid, "gratuito", "2027-06-01")

    def test_webhook_dedup_solo_eventi_order(self, db):
        ins = ("insert into public.webhook_events (provider, event, resource_id, payload) "
               "values ('revolut', %s, 'ord-1', '{}')")
        db.execute(ins, ("ORDER_COMPLETED",))
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(ins, ("ORDER_COMPLETED",))

    def test_webhook_declini_multipli_ammessi(self, db):
        ins = ("insert into public.webhook_events (provider, event, resource_id, payload) "
               "values ('revolut', 'ORDER_PAYMENT_DECLINED', 'ord-2', '{}')")
        db.execute(ins)
        db.execute(ins)  # il secondo declino è un evento legittimo nuovo
        n = db.execute(
            "select count(*) from public.webhook_events where resource_id = 'ord-2'"
        ).fetchone()[0]
        assert n == 2


# ------------------------------------------------------ trigger di provisioning


class TestHandleNewUser:
    def test_piano_a_pagamento_ripiega_su_gratuito(self, db):
        uid = str(uuid.uuid4())
        db.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
            (uid, "leak@test.it", json.dumps({"plan_slug": "pro"})),
        )
        assert active_sub(db, uid)[0] == "gratuito"

    def test_gratuito_resta_assegnabile(self, db):
        uid = str(uuid.uuid4())
        db.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
            (uid, "free26@test.it", json.dumps({"plan_slug": "gratuito"})),
        )
        assert active_sub(db, uid)[0] == "gratuito"


# ----------------------------------------------------------- refactor wrapper


class TestSwitchPlanWrapper:
    def test_comportamento_identico_alla_0024(self, db):
        uid = new_user(db)
        db.execute("select public.fn_switch_plan(%s, %s)", (uid, plan_id(db, "pro")))
        slug, scadenza, auto_renew, _ = active_sub(db, uid)
        assert slug == "pro"
        assert auto_renew is False
        resto = db.execute(
            "select count(*) from public.user_subscriptions "
            "where user_id = %s and status = 'active'", (uid,)
        ).fetchone()[0]
        assert resto == 1
        action = db.execute(
            "select actor_id::text from public.audit_log "
            "where action = 'plan.switched' and target_user_id = %s "
            "order by created_at desc limit 1", (uid,)
        ).fetchone()
        assert action and action[0] == uid


# --------------------------------------------------------- fn_complete_purchase


class TestCompletePurchase:
    def test_piano_applicato_e_idempotente(self, db):
        uid = new_user(db)  # gratuito
        schedule_change(db, uid, "gratuito", "2027-06-01", motivo="disdetta")
        pid = make_purchase(db, uid, "piano", slug="pro", auto_renew=True,
                            order_id="rev-1")
        esito = db.execute(
            "select public.fn_complete_purchase(%s, 'pay-1')", (pid,)
        ).fetchone()[0]
        assert esito["esito"] == "applicato"
        slug, _, auto_renew, _ = active_sub(db, uid)
        assert slug == "pro" and auto_renew is True
        # il cambio programmato è stato annullato dall'upgrade
        stato = db.execute(
            "select status from public.scheduled_plan_changes where user_id = %s", (uid,)
        ).fetchone()[0]
        assert stato == "annullato"
        # idempotenza: secondo webhook → no-op
        esito2 = db.execute(
            "select public.fn_complete_purchase(%s, 'pay-1')", (pid,)
        ).fetchone()[0]
        assert esito2["esito"] == "gia_pagato"

    def test_rinnovo_non_viola_one_active_e_prolunga_dalla_scadenza(self, db):
        uid = new_user(db, "pro")
        db.execute(
            "update public.user_subscriptions set data_scadenza = '2027-03-01', "
            "auto_renew = true, grace_until = '2027-03-10' "
            "where user_id = %s and status = 'active'", (uid,)
        )
        pid = make_purchase(db, uid, "rinnovo", slug="pro",
                            ciclo="2027-03-01", tentativo=1)
        esito = db.execute(
            "select public.fn_complete_purchase(%s, 'pay-r1')", (pid,)
        ).fetchone()[0]
        assert esito["esito"] == "applicato"
        slug, scadenza, auto_renew, grace = active_sub(db, uid)
        assert slug == "pro"
        assert str(scadenza) == "2028-03-01"  # dalla VECCHIA scadenza, non da oggi
        assert auto_renew is True             # sopravvive al rinnovo
        assert grace is None                  # la grazia si azzera

    def test_rinnovo_applica_il_cambio_programmato_coerente(self, db):
        uid = new_user(db, "pro")
        db.execute(
            "update public.user_subscriptions set data_scadenza = '2027-03-01' "
            "where user_id = %s and status = 'active'", (uid,)
        )
        sid = schedule_change(db, uid, "smart", "2027-03-01")
        # il purchase di rinnovo snapshotta il piano di DESTINAZIONE
        pid = make_purchase(db, uid, "rinnovo", slug="smart",
                            ciclo="2027-03-01", tentativo=1)
        db.execute("select public.fn_complete_purchase(%s, 'pay-r2')", (pid,))
        assert active_sub(db, uid)[0] == "smart"
        stato = db.execute(
            "select status from public.scheduled_plan_changes where id = %s", (sid,)
        ).fetchone()[0]
        assert stato == "eseguito"

    def test_rinnovo_non_marca_un_programmato_incoerente(self, db):
        uid = new_user(db, "pro")
        db.execute(
            "update public.user_subscriptions set data_scadenza = '2027-03-01' "
            "where user_id = %s and status = 'active'", (uid,)
        )
        pid = make_purchase(db, uid, "rinnovo", slug="pro",
                            ciclo="2027-03-01", tentativo=1)
        # il downgrade arriva DOPO la creazione del rinnovo del piano corrente
        sid = schedule_change(db, uid, "smart", "2027-03-01")
        db.execute("select public.fn_complete_purchase(%s, 'pay-r3')", (pid,))
        assert active_sub(db, uid)[0] == "pro"
        stato = db.execute(
            "select status from public.scheduled_plan_changes where id = %s", (sid,)
        ).fetchone()[0]
        assert stato == "programmato"  # maturerà al ciclo successivo

    def test_ciclo_gia_coperto_e_pagamento_orfano(self, db):
        uid = new_user(db, "pro")
        p1 = make_purchase(db, uid, "rinnovo", slug="pro",
                           ciclo="2027-03-01", tentativo=1, order_id="rev-a")
        db.execute("select public.fn_complete_purchase(%s, 'pay-a')", (p1,))
        scadenza_dopo_p1 = active_sub(db, uid)[1]
        p2 = make_purchase(db, uid, "rinnovo", slug="pro",
                           ciclo="2027-03-01", tentativo=2, order_id="rev-b")
        esito = db.execute(
            "select public.fn_complete_purchase(%s, 'pay-b')", (p2,)
        ).fetchone()[0]
        assert esito["esito"] == "pagamento_orfano"
        assert esito["motivo"] == "ciclo_gia_coperto"
        # il denaro è registrato ma il piano NON è esteso due volte
        assert db.execute(
            "select status from public.purchases where id = %s", (p2,)
        ).fetchone()[0] == "pagato"
        assert active_sub(db, uid)[1] == scadenza_dopo_p1

    def test_complete_su_annullato_e_pagamento_orfano(self, db):
        uid = new_user(db)
        pid = make_purchase(db, uid, "piano", slug="pro", status="annullato")
        esito = db.execute(
            "select public.fn_complete_purchase(%s, 'pay-x')", (pid,)
        ).fetchone()[0]
        assert esito["esito"] == "pagamento_orfano"
        assert active_sub(db, uid)[0] == "gratuito"  # nulla applicato

    def test_addon_crea_il_credito(self, db):
        uid = new_user(db)
        pid = make_purchase(db, uid, "addon")
        db.execute("select public.fn_complete_purchase(%s, 'pay-ad')", (pid,))
        stato = db.execute(
            "select stato from public.user_addons where purchase_id = %s", (pid,)
        ).fetchone()[0]
        assert stato == "disponibile"


# ------------------------------------------------------------- fn_fail_purchase


class TestFailPurchase:
    def test_primo_fallimento_di_rinnovo_arma_la_grazia(self, db):
        uid = new_user(db, "pro")
        pid = make_purchase(db, uid, "rinnovo", slug="pro",
                            ciclo="2027-03-01", tentativo=1)
        db.execute(
            "select public.fn_fail_purchase(%s, 'fallito', 'insufficient_funds')", (pid,)
        )
        assert str(active_sub(db, uid)[3]) == "2027-03-15"  # ciclo + 14
        reason = db.execute(
            "select decline_reason from public.purchases where id = %s", (pid,)
        ).fetchone()[0]
        assert reason == "insufficient_funds"

    def test_su_pagato_non_fa_nulla(self, db):
        uid = new_user(db)
        pid = make_purchase(db, uid, "piano", slug="pro")
        db.execute("select public.fn_complete_purchase(%s, 'pay-1')", (pid,))
        esito = db.execute(
            "select public.fn_fail_purchase(%s, 'scaduto', null)", (pid,)
        ).fetchone()[0]
        assert esito["esito"] == "gia_pagato"
        assert db.execute(
            "select status from public.purchases where id = %s", (pid,)
        ).fetchone()[0] == "pagato"


# -------------------------------------------------- fn_execute_scheduled_change


class TestExecuteScheduledChange:
    def test_destinazione_gratuita_applicata(self, db):
        uid = new_user(db, "pro")
        sid = schedule_change(db, uid, "gratuito", "2020-01-01", motivo="disdetta")
        esito = db.execute(
            "select public.fn_execute_scheduled_change(%s)", (sid,)
        ).fetchone()[0]
        assert esito["esito"] == "eseguito" and esito["fallback_gratuito"] is False
        assert active_sub(db, uid)[0] == "gratuito"

    def test_destinazione_a_pagamento_non_pagata_degrada_a_gratuito(self, db):
        uid = new_user(db, "pro")
        sid = schedule_change(db, uid, "smart", "2020-01-01")
        esito = db.execute(
            "select public.fn_execute_scheduled_change(%s)", (sid,)
        ).fetchone()[0]
        assert esito["fallback_gratuito"] is True
        assert active_sub(db, uid)[0] == "gratuito"  # MAI smart regalato

    def test_non_maturato_rifiutato(self, db):
        uid = new_user(db, "pro")
        sid = schedule_change(db, uid, "gratuito", "2099-01-01")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_execute_scheduled_change(%s)", (sid,))
        assert detail_of(exc) == "not_due"

    def test_gia_eseguito_e_un_no_op(self, db):
        uid = new_user(db, "pro")
        sid = schedule_change(db, uid, "gratuito", "2020-01-01")
        db.execute("select public.fn_execute_scheduled_change(%s)", (sid,))
        esito = db.execute(
            "select public.fn_execute_scheduled_change(%s)", (sid,)
        ).fetchone()[0]
        assert esito["esito"] == "non_programmato"


# --------------------------------------------------------- fn_registra_cambio_admin


class TestCambioAdmin:
    def test_cambio_con_audit_dell_attore_vero(self, db):
        admin = new_user(db)
        uid = new_user(db)
        make_purchase(db, uid, "piano", slug="pro", order_id="rev-open")
        schedule_change(db, uid, "gratuito", "2027-06-01", motivo="disdetta")
        esito = db.execute(
            "select public.fn_registra_cambio_admin(%s, %s, %s, 'Cliente convenzionato')",
            (admin, uid, plan_id(db, "advisor")),
        ).fetchone()[0]
        assert active_sub(db, uid)[0] == "advisor"
        # il checkout in corso è annullato e ritornato per il cancel provider
        assert esito["annullati"][0]["revolut_order_id"] == "rev-open"
        assert db.execute(
            "select status from public.purchases where revolut_order_id = 'rev-open'"
        ).fetchone()[0] == "annullato"
        # storico: riga cambio_admin gratuita con attore e motivazione
        kind, status, actor, motiv = db.execute(
            "select kind, status, actor_admin_id::text, motivazione "
            "from public.purchases where id = %s", (esito["purchase_id"],)
        ).fetchone()
        assert (kind, status, actor, motiv) == (
            "cambio_admin", "gratuito", admin, "Cliente convenzionato")
        # audit con l'ADMIN come attore (il gap della 0024)
        actor_id = db.execute(
            "select actor_id::text from public.audit_log "
            "where action = 'plan.admin_changed' and target_user_id = %s", (uid,)
        ).fetchone()[0]
        assert actor_id == admin

    def test_motivazione_obbligatoria(self, db):
        admin = new_user(db)
        uid = new_user(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "select public.fn_registra_cambio_admin(%s, %s, %s, '   ')",
                (admin, uid, plan_id(db, "pro")),
            )
        assert detail_of(exc) == "motivation_required"


# ------------------------------------------------------------------- protezioni


class TestProtezioni:
    TABELLE = ["billing_profiles", "revolut_customers", "purchases", "user_addons",
               "scheduled_plan_changes", "webhook_events", "payment_runs"]

    def test_rls_attiva_e_ruoli_client_esclusi(self, db):
        for t in self.TABELLE:
            assert db.execute(
                "select relrowsecurity from pg_class where oid = %s::regclass",
                (f"public.{t}",),
            ).fetchone()[0], f"RLS spenta su {t}"
            assert not db.execute(
                "select has_table_privilege('anon', %s, 'select')",
                (f"public.{t}",),
            ).fetchone()[0], f"anon legge {t}"

    def test_rpc_non_eseguibili_dai_ruoli_client(self, db):
        firme = [
            "public.fn_apply_plan_change(uuid, bigint, date, uuid, text)",
            "public.fn_complete_purchase(uuid, text)",
            "public.fn_fail_purchase(uuid, text, text)",
            "public.fn_execute_scheduled_change(uuid)",
            "public.fn_registra_cambio_admin(uuid, uuid, bigint, text)",
        ]
        for f in firme:
            assert not db.execute(
                "select has_function_privilege('anon', %s, 'execute')", (f,)
            ).fetchone()[0], f"anon esegue {f}"
