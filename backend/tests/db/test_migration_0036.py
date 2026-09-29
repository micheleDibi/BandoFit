"""Test funzionali della migration 0036 (limiti di piano del modulo partenariati).

Coprono: le due colonne di subscription_plans (tipo, nullable, default 0,
CHECK, commenti con la semantica NULL = illimitato); il seed per slug di Q1
(valori sui piani della 0002, idempotenza, piani fuori dal seed intatti) e il
DO di verifica che avvisa senza abortire; un piano nuovo che nasce escluso;
fn_partenariati_limiti (gratuito, dopo un cambio di piano, NULL = illimitato,
limiti cambiati dall'admin, senza abbonamento, collegato, owner inesistente);
privilegi e firme di tutto ciò che la migration crea.
Ogni test riceve un database fresco clonato dal template.
"""

import json
import re
import uuid
from pathlib import Path

import psycopg
import pytest

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0036_piani_partenariato.sql"
)
SQL_0036 = MIGRATION.read_text(encoding="utf-8")

COLONNE = ("partner_calls_attive_max", "partner_candidature_mese")
FUNZIONI_NUOVE = {"fn_seed_limiti_partenariato_0036", "fn_partenariati_limiti"}
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0036, re.M))

# Q1: slug → (call attive, candidature al mese).
SEED_Q1 = {
    "gratuito": (0, 0),
    "smart": (1, 5),
    "pro": (3, 20),
    "advisor": (10, 50),
}


# ----------------------------------------------------------------- helper


def new_user(db, plan_slug: str | None = None, *, invitato: bool = False) -> str:
    """Utente registrato (trigger handle_new_user: profilo + Gratuito). Con
    `invitato` nasce come collegato, senza abbonamento proprio."""
    uid = str(uuid.uuid4())
    meta = '{"family_invite":"true","denominazione":"Invitato"}' if invitato else "{}"
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s::jsonb)",
        (uid, f"{uid[:8]}@test.it", meta),
    )
    if plan_slug:
        switch_plan(db, uid, plan_slug)
    return uid


def switch_plan(db, user: str, slug: str) -> None:
    db.execute(
        "select public.fn_switch_plan(%s, "
        "(select id from public.subscription_plans where slug = %s))",
        (user, slug),
    )


def crea_piano(db, slug: str, **campi) -> None:
    colonne = ["nome", "slug", *campi]
    valori = [slug.title(), slug, *campi.values()]
    db.execute(
        f"insert into public.subscription_plans ({', '.join(colonne)}) "
        f"values ({', '.join(['%s'] * len(valori))})",
        valori,
    )


def limiti_piano(db, slug: str) -> tuple:
    return db.execute(
        "select partner_calls_attive_max, partner_candidature_mese "
        "from public.subscription_plans where slug = %s",
        (slug,),
    ).fetchone()


def limiti(db, owner: str | None) -> dict:
    return db.execute("select public.fn_partenariati_limiti(%s::uuid)", (owner,)).fetchone()[0]


def blocco_do() -> str:
    """Il DO di verifica del seed, così com'è nel file."""
    [blocco] = re.findall(r"^do \$\$.*?^\$\$;", SQL_0036, re.M | re.S)
    return blocco


# ----------------------------------------------------------------- colonne


class TestColonne:
    @pytest.mark.parametrize("colonna", COLONNE)
    def test_intero_nullable_default_zero(self, db, colonna):
        tipo, nullable, default = db.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'subscription_plans' "
            "and column_name = %s",
            (colonna,),
        ).fetchone()
        assert (tipo, nullable, default) == ("integer", "YES", "0")

    @pytest.mark.parametrize("colonna", COLONNE)
    def test_check_rifiuta_i_negativi(self, db, colonna):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(f"update public.subscription_plans set {colonna} = -1 where slug = 'pro'")
        assert exc.value.diag.constraint_name == f"subscription_plans_{colonna}_check"

    @pytest.mark.parametrize("colonna", COLONNE)
    @pytest.mark.parametrize("valore", [None, 0, 1, 1000])
    def test_null_zero_e_positivi_ammessi(self, db, colonna, valore):
        db.execute(
            f"update public.subscription_plans set {colonna} = %s where slug = 'pro'", (valore,)
        )
        riga = dict(zip(COLONNE, limiti_piano(db, "pro"), strict=True))
        assert riga[colonna] == valore

    def test_piano_nuovo_nasce_escluso(self, db):
        crea_piano(db, "enterprise", ai_check=100, num_account_aziendali=5)
        assert limiti_piano(db, "enterprise") == (0, 0)

    def test_piano_nuovo_illimitato_se_esplicito(self, db):
        crea_piano(db, "illimitato", partner_calls_attive_max=None,
                   partner_candidature_mese=None)
        assert limiti_piano(db, "illimitato") == (None, None)

    @pytest.mark.parametrize("colonna", COLONNE)
    def test_commento_con_la_semantica(self, db, colonna):
        commento = db.execute(
            "select col_description('public.subscription_plans'::regclass, attnum) "
            "from pg_attribute where attrelid = 'public.subscription_plans'::regclass "
            "and attname = %s",
            (colonna,),
        ).fetchone()[0]
        assert "NULL = illimitato, 0 = esclusa" in commento
        assert "OPPOSTA ad alert_ritardo_giorni" in commento


# ----------------------------------------------------------------- seed


class TestSeed:
    @pytest.mark.parametrize("slug", sorted(SEED_Q1))
    def test_valori_per_slug(self, db, slug):
        assert limiti_piano(db, slug) == SEED_Q1[slug]

    def test_idempotente(self, db):
        esito = db.execute("select public.fn_seed_limiti_partenariato_0036()").fetchone()[0]
        assert esito == {"aggiornati": 4, "attesi": 4}
        for slug, attesi in SEED_Q1.items():
            assert limiti_piano(db, slug) == attesi

    def test_non_tocca_gli_altri_piani(self, db):
        crea_piano(db, "tailored")
        crea_piano(db, "su-misura", partner_calls_attive_max=None, partner_candidature_mese=7)
        db.execute("select public.fn_seed_limiti_partenariato_0036()")
        assert limiti_piano(db, "tailored") == (0, 0)
        assert limiti_piano(db, "su-misura") == (None, 7)

    def test_slug_mancante_contato(self, db):
        db.execute("update public.subscription_plans set slug = 'pro-2024' where slug = 'pro'")
        esito = db.execute("select public.fn_seed_limiti_partenariato_0036()").fetchone()[0]
        assert esito == {"aggiornati": 3, "attesi": 4}

    def test_riporta_ai_valori_di_q1(self, db):
        # Per questo è revocato ai client: un richiamo cancella le scelte dell'admin.
        db.execute("update public.subscription_plans set partner_calls_attive_max = null "
                   "where slug = 'smart'")
        db.execute("select public.fn_seed_limiti_partenariato_0036()")
        assert limiti_piano(db, "smart") == SEED_Q1["smart"]


class TestVerificaDelSeed:
    """Il DO in coda al seed: WARNING (mai abort) se i quattro slug non hanno i
    valori di Q1, NOTICE con i piani che restano esclusi."""

    @staticmethod
    def _esegui(db) -> list[tuple[str, str]]:
        avvisi: list[tuple[str, str]] = []
        db.add_notice_handler(lambda diag: avvisi.append((diag.severity_nonlocalized,
                                                          diag.message_primary)))
        db.execute(blocco_do())
        return avvisi

    def test_nessun_avviso_sui_piani_standard(self, db):
        assert [a for a in self._esegui(db) if a[0] == "WARNING"] == []

    def test_slug_mancante_avvisa_senza_abortire(self, db):
        db.execute("update public.subscription_plans set slug = 'pro-2024' where slug = 'pro'")
        avvisi = [a for a in self._esegui(db) if a[0] == "WARNING"]
        assert len(avvisi) == 1 and "3 piani dei 4 attesi" in avvisi[0][1]

    def test_valore_diverso_da_q1_avvisa(self, db):
        db.execute("update public.subscription_plans set partner_candidature_mese = null "
                   "where slug = 'advisor'")
        avvisi = [a for a in self._esegui(db) if a[0] == "WARNING"]
        assert len(avvisi) == 1 and "3 piani dei 4 attesi" in avvisi[0][1]

    def test_notice_sui_piani_esclusi(self, db):
        crea_piano(db, "tailored")
        avvisi = self._esegui(db)
        assert ("NOTICE", "seed 0036: i piani tailored restano senza partenariati (0) "
                "finché non li imposti da AdminPiani") in avvisi


# ----------------------------------------------------------------- fn_partenariati_limiti


class TestLimiti:
    def test_gratuito(self, db):
        owner = new_user(db)
        assert limiti(db, owner) == {
            "calls_attive_max": 0, "candidature_mese": 0, "piano_attivo": True}

    @pytest.mark.parametrize("slug", ["smart", "pro", "advisor"])
    def test_dopo_il_cambio_di_piano(self, db, slug):
        owner = new_user(db)
        switch_plan(db, owner, slug)
        calls, candidature = SEED_Q1[slug]
        assert limiti(db, owner) == {
            "calls_attive_max": calls, "candidature_mese": candidature, "piano_attivo": True}

    def test_downgrade(self, db):
        owner = new_user(db, "pro")
        switch_plan(db, owner, "gratuito")
        assert limiti(db, owner)["calls_attive_max"] == 0

    def test_null_vuol_dire_illimitato(self, db):
        db.execute("update public.subscription_plans set partner_calls_attive_max = null, "
                   "partner_candidature_mese = null where slug = 'advisor'")
        owner = new_user(db, "advisor")
        esito = limiti(db, owner)
        assert esito == {"calls_attive_max": None, "candidature_mese": None, "piano_attivo": True}
        # null JSON, non assente: il chiamante distingue illimitato da mancante.
        testo = db.execute("select public.fn_partenariati_limiti(%s::uuid)::text",
                           (owner,)).fetchone()[0]
        assert json.loads(testo) == esito and '"calls_attive_max": null' in testo

    def test_legge_il_piano_dal_vivo(self, db):
        owner = new_user(db, "smart")
        db.execute("update public.subscription_plans set partner_calls_attive_max = 2 "
                   "where slug = 'smart'")
        assert limiti(db, owner)["calls_attive_max"] == 2

    @pytest.mark.parametrize("stato", ["cancelled", "expired"])
    def test_abbonamento_non_attivo(self, db, stato):
        owner = new_user(db, "pro")
        db.execute("update public.user_subscriptions set status = %s::subscription_status "
                   "where user_id = %s", (stato, owner))
        assert limiti(db, owner) == {
            "calls_attive_max": 0, "candidature_mese": 0, "piano_attivo": False}

    def test_collegato_senza_abbonamento_proprio(self, db):
        membro = new_user(db, invitato=True)
        assert db.execute("select count(*) from public.user_subscriptions where user_id = %s",
                          (membro,)).fetchone()[0] == 0
        assert limiti(db, membro)["piano_attivo"] is False

    @pytest.mark.parametrize("owner", [str(uuid.uuid4()), None])
    def test_owner_inesistente_o_nullo(self, db, owner):
        assert limiti(db, owner) == {
            "calls_attive_max": 0, "candidature_mese": 0, "piano_attivo": False}

    def test_non_modifica_nulla(self, db):
        volatilita = db.execute(
            "select provolatile from pg_proc where oid = "
            "'public.fn_partenariati_limiti(uuid)'::regprocedure"
        ).fetchone()[0]
        assert volatilita == "s"  # stable


# ----------------------------------------------------------------- sicurezza


class TestSicurezza0036:
    def test_inventario_del_file(self):
        # Se la migration crea altro, i test sotto devono coprirlo.
        assert FUNZIONI_NEL_FILE == FUNZIONI_NUOVE
        assert not re.search(r"^create (table|function|trigger|index)", SQL_0036, re.M | re.I)

    def test_additiva(self):
        """Nessuna funzione o tabella esistente ridefinita o eliminata: si
        aggiungono solo le due colonne di subscription_plans."""
        eseguibile = "\n".join(r for r in SQL_0036.splitlines()
                               if not r.lstrip().startswith("--"))
        assert not re.search(r"^\s*(drop|alter function)", eseguibile, re.M | re.I)
        assert re.findall(r"^alter table public\.(\w+)", eseguibile, re.M) == [
            "subscription_plans"]
        assert re.findall(r"add column (\w+)", eseguibile) == list(COLONNE)

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration è SECURITY DEFINER con
        search_path fissato, non eseguibile dai client (PUBLIC compreso) ed esiste
        in una sola firma."""
        for nome in sorted(FUNZIONI_NEL_FILE | FUNZIONI_NUOVE):
            righe = db.execute(
                """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                          coalesce(p.proacl::text, '')
                   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and p.proname = %s""",
                (nome,),
            ).fetchall()
            assert len(righe) == 1, f"{nome}: {len(righe)} firme"
            oid, secdef, config, acl = righe[0]
            assert secdef is True, f"{nome} non è security definer"
            assert "search_path=public" in config, f"{nome}: search_path non fissato"
            # ACL esplicita senza la voce di PUBLIC («=X/…»).
            assert acl and not re.search(r"[{,]=X", acl), f"{nome}: PUBLIC esegue ({acl})"
            for ruolo in ("anon", "authenticated"):
                assert not db.execute(
                    "select has_function_privilege(%s, %s::oid, 'execute')", (ruolo, oid)
                ).fetchone()[0], f"{ruolo} esegue {nome}"

    def test_revoche_scritte_nel_file(self):
        for nome in FUNZIONI_NUOVE:
            assert re.search(
                rf"^revoke execute on function public\.{nome}\([^)]*\)\s+"
                r"from public, anon, authenticated;",
                SQL_0036, re.M,
            ), nome

    @pytest.mark.parametrize("chiamata", [
        "select public.fn_seed_limiti_partenariato_0036()",
        "select public.fn_partenariati_limiti(gen_random_uuid())",
    ])
    def test_i_client_non_eseguono(self, db, chiamata):
        """Prova diretta: con il ruolo authenticated la chiamata fallisce."""
        db.execute("set role authenticated")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    @pytest.mark.parametrize("colonna", COLONNE)
    def test_colonne_invisibili_ai_client(self, db, colonna):
        for ruolo in ("anon", "authenticated"):
            for privilegio in ("select", "insert", "update"):
                assert not db.execute(
                    "select has_column_privilege(%s, 'public.subscription_plans', %s, %s)",
                    (ruolo, colonna, privilegio),
                ).fetchone()[0], f"{ruolo} ha {privilegio} su {colonna}"
