"""Test funzionali della migration 0042 (bozze AI dei documenti del
partenariato: WP10).

Coprono: la colonna subscription_plans.partner_bozze_mese (tipo, default 0,
CHECK, commento) e il seed per slug di Q7 con il DO di verifica; la tabella
partner_bozze_documento (vincoli, una sola pending per azienda × call × tipo,
cascade dalla call e dall'azienda); fn_partner_bozza_prenota (partecipazione
del creatore e dei membri non usciti, Advisor con un'altra azienda attiva,
parametri, limiti 0 / N / NULL, mese successivo e confine del mese su Roma,
pool dell'owner Advisor, errore pagato che conta ed errore senza LLM che non
conta, una pending, pending orfana, budget esaurito, nessuna scrittura in
ai_checks); la chiusura atomica; il failsafe con timeout_unknown; le
ridefinizioni di fn_partenariati_limiti e fn_partenariati_snapshot (campo
bozze_mese, stessa formula della RPC, copia fedele dei corpi); l'ordine dei
lock con più connessioni; RLS, privilegi, firme (una sola funzione per nome)
e mappa degli errori (test generici).
Ogni test riceve un database fresco clonato dal template.
"""

import difflib
import itertools
import re
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SQL_0042 = (MIGRAZIONI / "0042_partenariato_bozze.sql").read_text(encoding="utf-8")

TABELLE_NUOVE = {"partner_bozze_documento"}
FIRME = {
    # Nuove.
    "fn_seed_limiti_bozze_0042": "fn_seed_limiti_bozze_0042()",
    "fn_partner_bozze_usate": "fn_partner_bozze_usate(uuid)",
    "fn_partner_bozze_limite": "fn_partner_bozze_limite(uuid)",
    "fn_partner_bozza_esecuzione_interrotta": "fn_partner_bozza_esecuzione_interrotta(uuid)",
    "fn_partner_bozza_prenota":
        "fn_partner_bozza_prenota(uuid,uuid,uuid,uuid,text,jsonb,integer,integer,integer)",
    "fn_partner_bozza_concludi":
        "fn_partner_bozza_concludi(uuid,uuid,text,jsonb,text,text,integer,integer,integer,"
        "text,text)",
    "fn_partner_bozza_chiudi_stale": "fn_partner_bozza_chiudi_stale(integer)",
    # Ridefinite con la STESSA firma.
    "fn_partenariati_limiti": "fn_partenariati_limiti(uuid)",
    "fn_partenariati_snapshot": "fn_partenariati_snapshot(uuid)",
}
RIDEFINITE = {"fn_partenariati_limiti", "fn_partenariati_snapshot"}
FUNZIONI_NUOVE = set(FIRME) - RIDEFINITE
# Ultima definizione precedente di ciascuna ridefinita (copia fedele).
ORIGINE = {
    "fn_partenariati_limiti": "0036_piani_partenariato.sql",
    "fn_partenariati_snapshot": "0039_partenariato_candidature_chat.sql",
}

# Q7: slug → bozze al mese.
SEED_Q7 = {"gratuito": 0, "smart": 3, "pro": 10, "advisor": 30}
TIPI = ("lettera_intenti", "nda", "term_sheet")

INPUT = {
    "tipo": "nda",
    "bando": {"titolo": "Bando di prova", "programma": "PR FESR"},
    "forma": {"codice": "ats", "responsabilita": "solidale"},
    "membri": [{"segnaposto": "[Capofila]", "ruolo": "capofila", "quota": "60"},
               {"segnaposto": "[Partner 1]", "ruolo": "partner", "quota": "40"}],
}
CONTENUTO = {
    "titolo": "Accordo di riservatezza",
    "sezioni": [{"titolo": "Parti", "testo": "Tra [Capofila] e [Partner 1]."}],
    "note_per_l_utente": ["Sostituisci i segnaposto con i nomi delle aziende."],
}
_TITOLARE = object()
_ASSENTE = object()
_DEFAULT = object()

_seq = itertools.count(1)
_bandi = itertools.count(42000)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def vincolo_di(exc) -> str:
    return exc.value.diag.constraint_name or ""


def new_user(db, plan_slug: str | None = None, *, invitato: bool = False) -> str:
    """Utente registrato (profilo + Gratuito); con `invitato` nasce collegato,
    senza abbonamento proprio."""
    uid = str(uuid.uuid4())
    meta = '{"family_invite":"true","denominazione":"Invitato"}' if invitato else "{}"
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s::jsonb)",
        (uid, f"{uid[:8]}@test.it", meta),
    )
    if plan_slug:
        db.execute(
            "select public.fn_switch_plan(%s, "
            "(select id from public.subscription_plans where slug = %s))",
            (uid, plan_slug),
        )
    return uid


def make_company(db, owner: str) -> str:
    i = next(_seq)
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, %s, %s) returning id",
        (owner, f"ACME {i} Srl", f"{i + 42000000:011d}"),
    ).fetchone()[0])


def azienda(db, owner: str | None = None, plan_slug: str | None = "smart") -> tuple[str, str]:
    owner = owner or new_user(db, plan_slug)
    return owner, make_company(db, owner)


def inserisci_call(db, company: str, *, stato: str = "pubblicata") -> str:
    """Call scritta direttamente (come dopo le RPC del WP5) nello stato richiesto."""
    owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                           (company,)).fetchone()[0])
    pubblicata_at = stato in ("pubblicata", "chiusa_completata", "scaduta",
                              "sospesa_moderazione")
    motivo = {"chiusa_completata": "creatore_completata",
              "chiusa_annullata": "creatore_annullata",
              "scaduta": "scadenza_call"}.get(stato)
    bid = next(_bandi)
    return str(db.execute(
        """insert into public.partner_calls
             (company_profile_id, family_parent_id, creato_da, bando_id, bando_slug,
              bando_titolo, ruolo_creatore, titolo, descrizione_pubblica,
              scadenza_call, regole_partenariato, regole_confermate_at, visibilita, stato,
              pubblicata_at, chiusa_at, motivo_chiusura, sospesa_at, stato_prima_sospensione)
           values (%s, %s, %s, %s, %s, 'Bando di prova', 'capofila',
                   'Cerchiamo un organismo di ricerca', 'Progetto di ricerca industriale.',
                   current_date + 30, '{}'::jsonb, now(), 'pubblica', %s,
                   case when %s then now() end, case when %s::text is not null then now() end,
                   %s, case when %s then now() end, case when %s then 'pubblicata' end)
           returning id""",
        (company, owner, owner, bid, f"bando-{bid}", stato, pubblicata_at, motivo, motivo,
         stato == "sospesa_moderazione", stato == "sospesa_moderazione"),
    ).fetchone()[0])


def membro(db, call_id: str, company: str, stato: str = "proposto") -> str:
    """Riga del consorzio di un'azienda in piattaforma scritta direttamente."""
    owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                           (company,)).fetchone()[0])
    confermato = stato == "confermato"
    return str(db.execute(
        "insert into public.partner_call_membri (partner_call_id, company_profile_id, stato, "
        "quota_percentuale, confermato_at, confermato_da_user_id) "
        "values (%s, %s, %s, 30, case when %s then now() end, case when %s then %s::uuid end) "
        "returning id",
        (call_id, company, stato, confermato, confermato, owner),
    ).fetchone()[0])


def prenota(db, owner, company, call_id, tipo: str | None = "nda", *, richiedente=_TITOLARE,
            input_=_DEFAULT, budget: int | None = 1000, riserva: int | None = 5,
            prompt_version=_ASSENTE) -> dict:
    """Chiamata per nome come PostgREST; senza p_prompt_version usa il default."""
    argomenti = [
        ("p_owner", owner, "uuid"), ("p_company", company, "uuid"),
        ("p_call", call_id, "uuid"),
        ("p_richiedente", owner if richiedente is _TITOLARE else richiedente, "uuid"),
        ("p_tipo", tipo, "text"),
        ("p_input", Jsonb(INPUT) if input_ is _DEFAULT else input_, "jsonb"),
        ("p_budget_cents", budget, "integer"),
        ("p_costo_riservato_cents", riserva, "integer"),
    ]
    if prompt_version is not _ASSENTE:
        argomenti.append(("p_prompt_version", prompt_version, "integer"))
    sql = ", ".join(f"{nome} => %s::{tipo_}" for nome, _, tipo_ in argomenti)
    return db.execute(f"select public.fn_partner_bozza_prenota({sql})",
                      [v for _, v, _ in argomenti]).fetchone()[0]


def concludi(db, bozza_id, esecuzione_id, *, bozza_stato: str = "ready",
             contenuto=_DEFAULT, bozza_errore=_DEFAULT, stato: str | None = None,
             cost: int | None = 4, input_tokens: int | None = 120,
             output_tokens: int | None = 300, model: str | None = "claude-sonnet-5",
             errore: str | None = None) -> dict:
    if contenuto is _DEFAULT:
        contenuto = Jsonb(CONTENUTO) if bozza_stato == "ready" else None
    if bozza_errore is _DEFAULT:
        bozza_errore = "errore_modello" if bozza_stato == "error" else None
    if stato is None:
        stato = "conclusa" if bozza_stato == "ready" else "errore"
    return db.execute(
        "select public.fn_partner_bozza_concludi(%s::uuid, %s::uuid, %s::text, %s::jsonb, "
        "%s::text, %s::text, %s::integer, %s::integer, %s::integer, %s::text, %s::text)",
        (bozza_id, esecuzione_id, bozza_stato, contenuto, bozza_errore, stato, cost,
         input_tokens, output_tokens, model, errore),
    ).fetchone()[0]


def chiudi_stale(db, minuti: int | None = 10) -> int:
    return db.execute("select public.fn_partner_bozza_chiudi_stale(%s::integer)",
                      (minuti,)).fetchone()[0]


def bozza_diretta(db, owner, company, call_id, *, stato: str = "ready", tipo: str = "nda",
                  llm: bool = True, cost: int | None = 3, created_at=None) -> str:
    """Bozza scritta direttamente (vincoli della tabella), per i conteggi."""
    return str(db.execute(
        "insert into public.partner_bozze_documento (partner_call_id, company_profile_id, "
        "family_parent_id, richiesta_da_user_id, tipo, stato, input_snapshot, contenuto, "
        "errore, esecuzione_id, llm_eseguito, cost_cents, ready_at, created_at) "
        "values (%s, %s, %s, %s, %s, %s, '{}'::jsonb, "
        "case when %s = 'ready' then '{}'::jsonb end, "
        "case when %s = 'error' then 'errore_modello' end, gen_random_uuid(), %s, %s, "
        "case when %s <> 'pending' then now() end, coalesce(%s::timestamptz, now())) "
        "returning id",
        (call_id, company, owner, owner, tipo, stato, stato, stato, llm, cost, stato,
         created_at),
    ).fetchone()[0])


def riga(db, tabella: str, id_) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(f"select * from public.{tabella} where id = %s", (id_,)).fetchone()


def bozza(db, id_) -> dict | None:
    return riga(db, "partner_bozze_documento", id_)


def esecuzione(db, id_) -> dict | None:
    return riga(db, "partenariati_ai_esecuzioni", id_)


def consumi(db) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.api_usage_events order by id").fetchall()


def conta(db, tabella: str) -> int:
    return db.execute(f"select count(*) from public.{tabella}").fetchone()[0]


def usate(db, owner) -> int:
    return db.execute("select public.fn_partner_bozze_usate(%s::uuid)", (owner,)).fetchone()[0]


def limiti(db, owner) -> dict:
    return db.execute("select public.fn_partenariati_limiti(%s::uuid)", (owner,)).fetchone()[0]


def snapshot(db, owner) -> dict:
    return db.execute("select public.fn_partenariati_snapshot(%s::uuid)",
                      (owner,)).fetchone()[0]


def limite_piano(db, slug: str, valore: int | None) -> None:
    db.execute("update public.subscription_plans set partner_bozze_mese = %s where slug = %s",
               (valore, slug))


def valore_piano(db, slug: str):
    return db.execute("select partner_bozze_mese from public.subscription_plans "
                      "where slug = %s", (slug,)).fetchone()[0]


def invecchia(db, bozza_id, minuti: int) -> None:
    """Bozza (e sua esecuzione) avviata `minuti` fa."""
    db.execute("update public.partner_bozze_documento set avviata_at = now() - "
               "make_interval(mins => %s) where id = %s", (minuti, bozza_id))
    db.execute("update public.partenariati_ai_esecuzioni set avviata_at = now() - "
               "make_interval(mins => %s) where id = (select esecuzione_id from "
               "public.partner_bozze_documento where id = %s)", (minuti, bozza_id))


def inizio_mese_roma(db):
    return db.execute("select date_trunc('month', now() at time zone 'Europe/Rome') "
                      "at time zone 'Europe/Rome'").fetchone()[0]


def blocco_do() -> str:
    """Il DO di verifica del seed, così com'è nel file."""
    [blocco] = re.findall(r"^do \$\$.*?^\$\$;", SQL_0042, re.M | re.S)
    return blocco


def in_attesa(monitor, pid, thread, esito, scadenza_s=10) -> None:
    """Attende che il backend `pid` sia bloccato da un lock."""
    scadenza = time.monotonic() + scadenza_s
    while not monitor.execute(
        "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
    ).fetchone()[0]:
        assert time.monotonic() < scadenza, "la transazione non attende"
        assert thread.is_alive(), f"la transazione non ha atteso: {esito}"
        time.sleep(0.02)


def in_thread(funzione) -> tuple[threading.Thread, dict]:
    esito: dict = {}

    def corpo():
        try:
            esito["out"] = funzione()
        except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
            esito["errore"] = exc

    return threading.Thread(target=corpo), esito


def chiudi_tutto(*conns) -> None:
    for c in conns:
        if c.closed:
            continue
        if c.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            c.rollback()
        c.close()


@pytest.fixture()
def sc(db):
    """X (Smart) ha una call pubblicata; Y (Smart, altro owner) è membro proposto
    del consorzio."""
    x_owner, x = azienda(db)
    y_owner, y = azienda(db)
    call_id = inserisci_call(db, x)
    my = membro(db, call_id, y)
    return SimpleNamespace(x_owner=x_owner, x=x, y_owner=y_owner, y=y, call=call_id, my=my)


# ----------------------------------------------------------------- colonna di piano


class TestColonnaPiano:
    def test_intero_nullable_default_zero(self, db):
        tipo, nullable, default = db.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'subscription_plans' "
            "and column_name = 'partner_bozze_mese'"
        ).fetchone()
        assert (tipo, nullable, default) == ("integer", "YES", "0")

    def test_check_rifiuta_i_negativi(self, db):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            limite_piano(db, "pro", -1)
        assert vincolo_di(exc) == "subscription_plans_partner_bozze_mese_check"

    @pytest.mark.parametrize("valore", [None, 0, 1, 1000])
    def test_null_zero_e_positivi_ammessi(self, db, valore):
        limite_piano(db, "pro", valore)
        assert valore_piano(db, "pro") == valore

    def test_piano_nuovo_nasce_escluso(self, db):
        db.execute("insert into public.subscription_plans (nome, slug) "
                   "values ('Enterprise', 'enterprise')")
        assert valore_piano(db, "enterprise") == 0

    def test_commento_con_la_semantica(self, db):
        commento = db.execute(
            "select col_description('public.subscription_plans'::regclass, attnum) "
            "from pg_attribute where attrelid = 'public.subscription_plans'::regclass "
            "and attname = 'partner_bozze_mese'"
        ).fetchone()[0]
        assert "NULL = illimitato, 0 = esclusa" in commento
        assert "OPPOSTA ad alert_ritardo_giorni" in commento


class TestSeed:
    @pytest.mark.parametrize("slug", sorted(SEED_Q7))
    def test_valori_per_slug(self, db, slug):
        assert valore_piano(db, slug) == SEED_Q7[slug]

    def test_idempotente(self, db):
        esito = db.execute("select public.fn_seed_limiti_bozze_0042()").fetchone()[0]
        assert esito == {"aggiornati": 4, "attesi": 4}
        for slug, atteso in SEED_Q7.items():
            assert valore_piano(db, slug) == atteso

    def test_non_tocca_gli_altri_piani_ne_gli_altri_limiti(self, db):
        db.execute("insert into public.subscription_plans (nome, slug, partner_bozze_mese) "
                   "values ('Su misura', 'su-misura', 7)")
        prima = db.execute("select partner_calls_attive_max, partner_candidature_mese "
                           "from public.subscription_plans where slug = 'pro'").fetchone()
        db.execute("select public.fn_seed_limiti_bozze_0042()")
        assert valore_piano(db, "su-misura") == 7
        assert db.execute("select partner_calls_attive_max, partner_candidature_mese "
                          "from public.subscription_plans where slug = 'pro'").fetchone() == prima

    def test_slug_mancante_contato(self, db):
        db.execute("update public.subscription_plans set slug = 'pro-2024' where slug = 'pro'")
        esito = db.execute("select public.fn_seed_limiti_bozze_0042()").fetchone()[0]
        assert esito == {"aggiornati": 3, "attesi": 4}

    def test_riporta_ai_valori_di_q7(self, db):
        limite_piano(db, "smart", None)
        db.execute("select public.fn_seed_limiti_bozze_0042()")
        assert valore_piano(db, "smart") == 3


class TestVerificaDelSeed:
    """Il DO in coda al seed: WARNING (mai abort) se i quattro slug non hanno i
    valori di Q7, NOTICE con i piani che restano esclusi."""

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

    def test_valore_diverso_da_q7_avvisa(self, db):
        limite_piano(db, "advisor", None)
        avvisi = [a for a in self._esegui(db) if a[0] == "WARNING"]
        assert len(avvisi) == 1 and "3 piani dei 4 attesi" in avvisi[0][1]

    def test_notice_sui_piani_esclusi(self, db):
        db.execute("insert into public.subscription_plans (nome, slug) "
                   "values ('Tailored', 'tailored')")
        assert ("NOTICE", "seed 0042: i piani tailored restano senza bozze dei documenti (0) "
                "finché non li imposti da AdminPiani") in self._esegui(db)


# ----------------------------------------------------------------- tabella


class TestTabella:
    def test_default(self, db, sc):
        bid = bozza_diretta(db, sc.x_owner, sc.x, sc.call, stato="pending", llm=False,
                            cost=None)
        b = bozza(db, bid)
        assert (b["stato"], b["llm_eseguito"], b["cost_cents"], b["input_tokens"],
                b["output_tokens"], b["contenuto"], b["errore"], b["ready_at"]) == (
            "pending", False, None, 0, 0, None, None, None)
        assert b["avviata_at"] is not None and b["prompt_version"] is None

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"tipo": "contratto"}, "pbd_tipo_check"),
        ({"stato": "pronta", "ready_at": "now()"}, "pbd_stato_check"),
        ({"input_snapshot": "[]"}, "pbd_input_snapshot_check"),
        ({"stato": "ready", "ready_at": "now()"}, "pbd_ready_ha_contenuto"),
        ({"stato": "error", "ready_at": "now()"}, "pbd_error_ha_codice"),
        ({"errore": "x"}, "pbd_error_ha_codice"),
        ({"contenuto": "{}"}, "pbd_ready_ha_contenuto"),
        ({"stato": "ready", "contenuto": "[]", "ready_at": "now()"}, "pbd_contenuto_check"),
        ({"ready_at": "now()"}, "pbd_chiusa_coerente"),
        ({"stato": "error", "errore": "x"}, "pbd_chiusa_coerente"),
        ({"stato": "error", "errore": "", "ready_at": "now()"}, "pbd_errore_check"),
        ({"cost_cents": "-1"}, "pbd_cost_cents_check"),
        ({"input_tokens": "-1"}, "pbd_input_tokens_check"),
        ({"prompt_version": "0"}, "pbd_prompt_version_check"),
        ({"cost_cents": "0"}, "pbd_pending_senza_esito"),
        ({"llm_eseguito": "true"}, "pbd_pending_senza_esito"),
        ({"output_tokens": "5"}, "pbd_pending_senza_esito"),
    ])
    def test_vincoli(self, db, sc, colonne, vincolo):
        dati = {"partner_call_id": f"'{sc.call}'", "company_profile_id": f"'{sc.x}'",
                "family_parent_id": f"'{sc.x_owner}'",
                "richiesta_da_user_id": f"'{sc.x_owner}'", "tipo": "'nda'",
                "input_snapshot": "'{}'", "esecuzione_id": "gen_random_uuid()"}
        for nome, valore in colonne.items():
            dati[nome] = valore if valore == "now()" or valore.lstrip("-").isdigit() \
                else f"'{valore}'"
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(f"insert into public.partner_bozze_documento ({', '.join(dati)}) "
                       f"values ({', '.join(dati.values())})")
        assert vincolo_di(exc) == vincolo

    def test_una_sola_pending_per_azienda_call_tipo(self, db, sc):
        prima = bozza_diretta(db, sc.x_owner, sc.x, sc.call, stato="pending", llm=False,
                              cost=None)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            bozza_diretta(db, sc.x_owner, sc.x, sc.call, stato="pending", llm=False, cost=None)
        assert vincolo_di(exc) == "partner_bozze_one_pending"
        # Altro tipo, altra azienda sulla stessa call, righe chiuse: ammessi.
        bozza_diretta(db, sc.x_owner, sc.x, sc.call, stato="pending", tipo="term_sheet",
                      llm=False, cost=None)
        bozza_diretta(db, sc.y_owner, sc.y, sc.call, stato="pending", llm=False, cost=None)
        bozza_diretta(db, sc.x_owner, sc.x, sc.call, stato="ready")
        bozza_diretta(db, sc.x_owner, sc.x, sc.call, stato="error")
        db.execute("update public.partner_bozze_documento set stato = 'error', "
                   "errore = 'interrotta', ready_at = now() where id = %s", (prima,))
        bozza_diretta(db, sc.x_owner, sc.x, sc.call, stato="pending", llm=False, cost=None)

    def test_esecuzione_unica(self, db, sc):
        eid = str(uuid.uuid4())
        db.execute("insert into public.partner_bozze_documento (partner_call_id, "
                   "company_profile_id, family_parent_id, richiesta_da_user_id, tipo, "
                   "input_snapshot, esecuzione_id) values (%s, %s, %s, %s, 'nda', '{}', %s)",
                   (sc.call, sc.x, sc.x_owner, sc.x_owner, eid))
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            db.execute("insert into public.partner_bozze_documento (partner_call_id, "
                       "company_profile_id, family_parent_id, richiesta_da_user_id, tipo, "
                       "input_snapshot, esecuzione_id) values (%s, %s, %s, %s, 'lettera_intenti',"
                       " '{}', %s)", (sc.call, sc.x, sc.x_owner, sc.x_owner, eid))
        assert vincolo_di(exc) == "pbd_esecuzione_key"


# ----------------------------------------------------------------- prenotazione


class TestPrenota:
    def test_creatore(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call, "lettera_intenti", riserva=17,
                      prompt_version=1)
        assert set(out) == {"bozza_id", "esecuzione_id"}
        b = bozza(db, out["bozza_id"])
        assert (str(b["partner_call_id"]), str(b["company_profile_id"]),
                str(b["family_parent_id"]), str(b["richiesta_da_user_id"])) == (
            sc.call, sc.x, sc.x_owner, sc.x_owner)
        assert (b["tipo"], b["stato"], b["input_snapshot"], b["prompt_version"]) == (
            "lettera_intenti", "pending", INPUT, 1)
        assert str(b["esecuzione_id"]) == out["esecuzione_id"]
        assert (b["cost_cents"], b["llm_eseguito"], b["ready_at"]) == (None, False, None)
        e = esecuzione(db, out["esecuzione_id"])
        assert (e["servizio"], e["origine"], e["gruppo"], e["stato"]) == (
            "partner_bozza", "utente", "altri", "in_corso")
        bando_id = db.execute("select bando_id from public.partner_calls where id = %s",
                              (sc.call,)).fetchone()[0]
        assert (e["bando_id"], str(e["company_profile_id"]), str(e["owner_id"]),
                str(e["richiedente_user_id"]), e["costo_riservato_cents"]) == (
            bando_id, sc.x, sc.x_owner, sc.x_owner, 17)

    def test_versione_del_prompt_facoltativa(self, db, sc):
        """Senza p_prompt_version (la chiamata con i soli parametri del contratto)
        la colonna resta NULL."""
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        assert bozza(db, out["bozza_id"])["prompt_version"] is None

    @pytest.mark.parametrize("stato", ["proposto", "confermato"])
    def test_membro_non_uscito(self, db, sc, stato):
        db.execute("delete from public.partner_call_membri where id = %s", (sc.my,))
        membro(db, sc.call, sc.y, stato)
        out = prenota(db, sc.y_owner, sc.y, sc.call)
        b = bozza(db, out["bozza_id"])
        assert (str(b["company_profile_id"]), str(b["family_parent_id"])) == (sc.y, sc.y_owner)
        assert str(esecuzione(db, out["esecuzione_id"])["owner_id"]) == sc.y_owner

    def test_creatore_e_membro_sulla_stessa_call(self, db, sc):
        prenota(db, sc.x_owner, sc.x, sc.call)
        prenota(db, sc.y_owner, sc.y, sc.call)
        assert conta(db, "partner_bozze_documento") == 2

    @pytest.mark.parametrize("stato", ["bozza", "pubblicata", "scaduta", "chiusa_completata",
                                       "chiusa_annullata", "sospesa_moderazione"])
    def test_creatore_in_ogni_stato(self, db, stato):
        owner, company = azienda(db)
        c = inserisci_call(db, company, stato=stato)
        prenota(db, owner, company, c)

    def _nessuna_scrittura(self, db) -> None:
        assert conta(db, "partner_bozze_documento") == 0
        assert conta(db, "partenariati_ai_esecuzioni") == 0

    def test_membro_uscito(self, db, sc):
        db.execute("update public.partner_call_membri set stato = 'uscito' where id = %s",
                   (sc.my,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "call_non_trovata"
        self._nessuna_scrittura(db)

    def test_estraneo(self, db, sc):
        z_owner, z = azienda(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, z_owner, z, sc.call)
        assert detail_of(exc) == "call_non_trovata"
        self._nessuna_scrittura(db)

    def test_call_inesistente(self, db, sc):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, str(uuid.uuid4()))
        assert detail_of(exc) == "call_non_trovata"

    def test_membro_di_una_call_sospesa(self, db, sc):
        db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                   "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' where id = %s",
                   (sc.call,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "call_non_trovata"
        prenota(db, sc.x_owner, sc.x, sc.call)  # il creatore sì

    def test_advisor_con_un_altra_azienda_attiva(self, db):
        """L'Advisor con B attiva non chiede bozze sulla call di A: né come
        creatore (azienda diversa) né come membro."""
        owner = new_user(db, "advisor")
        a = make_company(db, owner)
        b = make_company(db, owner)
        ca = inserisci_call(db, a)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, owner, b, ca)
        assert detail_of(exc) == "call_non_trovata"
        self._nessuna_scrittura(db)
        prenota(db, owner, a, ca)

    def test_azienda_di_un_altro_owner(self, db, sc):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.x, sc.call)
        assert detail_of(exc) == "company_not_found"

    @pytest.mark.parametrize("colonna", ["archived_at", "deleted_at"])
    def test_azienda_non_viva(self, db, sc, colonna):
        db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                   (sc.y,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "company_not_found"
        self._nessuna_scrittura(db)

    def test_owner_inesistente(self, db, sc):
        altro = str(uuid.uuid4())
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, altro, sc.x, sc.call)
        assert detail_of(exc) == "owner_not_found"

    @pytest.mark.parametrize("chi", ["collegato", "estraneo", "nessuno"])
    def test_solo_il_titolare(self, db, sc, chi):
        richiedente = None if chi == "nessuno" else new_user(db, invitato=chi == "collegato")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call, richiedente=richiedente)
        assert detail_of(exc) == "attore_non_titolare"
        self._nessuna_scrittura(db)

    @pytest.mark.parametrize("campi", [
        {"tipo": "contratto"}, {"tipo": None}, {"tipo": "NDA"},
        {"input_": Jsonb([1, 2])}, {"input_": Jsonb("testo")}, {"input_": None},
        {"input_": Jsonb({"testo": "x" * 70000})},
        {"riserva": None}, {"riserva": -1},
        {"prompt_version": 0},
    ])
    def test_parametri_non_validi(self, db, sc, campi):
        campi = dict(campi)
        tipo = campi.pop("tipo", "nda")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call, tipo, **campi)
        assert detail_of(exc) == "parametri_non_validi"
        self._nessuna_scrittura(db)

    @pytest.mark.parametrize("nullo", ["owner", "company", "call"])
    def test_identificativi_nulli(self, db, sc, nullo):
        valori = {"owner": sc.x_owner, "company": sc.x, "call": sc.call, nullo: None}
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, valori["owner"], valori["company"], valori["call"],
                    richiedente=sc.x_owner)
        assert detail_of(exc) == "parametri_non_validi"

    def test_input_al_limite_accettato(self, db, sc):
        # 64 KB di testo JSON (con apici, graffe e chiave) passano.
        pieno = {"t": "x" * (65536 - len('{"t": ""}'))}
        out = prenota(db, sc.x_owner, sc.x, sc.call, input_=Jsonb(pieno))
        assert bozza(db, out["bozza_id"])["input_snapshot"] == pieno

    def test_nessuna_scrittura_in_ai_checks(self, db, sc):
        prima = conta(db, "ai_checks")
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        concludi(db, out["bozza_id"], out["esecuzione_id"])
        assert conta(db, "ai_checks") == prima


# ----------------------------------------------------------------- limiti di piano


class TestLimite:
    @pytest.mark.parametrize("caso", ["gratuito", "senza_abbonamento", "piano_a_zero"])
    def test_esclusa(self, db, sc, caso):
        if caso == "gratuito":
            db.execute("select public.fn_switch_plan(%s, (select id from "
                       "public.subscription_plans where slug = 'gratuito'))", (sc.y_owner,))
        elif caso == "senza_abbonamento":
            db.execute("update public.user_subscriptions set status = 'cancelled' "
                       "where user_id = %s", (sc.y_owner,))
        else:
            limite_piano(db, "smart", 0)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "funzione_non_inclusa"
        assert conta(db, "partner_bozze_documento") == 0
        assert conta(db, "partenariati_ai_esecuzioni") == 0

    def test_limite_n(self, db, sc):
        """Smart: 3 al mese (Q7). La quarta → bozze_esaurite, senza righe né spesa."""
        for tipo in TIPI:
            prenota(db, sc.y_owner, sc.y, sc.call, tipo)
        c2 = inserisci_call(db, sc.x)
        membro(db, c2, sc.y)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, c2)
        assert detail_of(exc) == "bozze_esaurite"
        assert conta(db, "partner_bozze_documento") == 3
        assert conta(db, "partenariati_ai_esecuzioni") == 3
        # Il limite è di Y: X (creatore, altro owner) prenota sulla stessa call.
        prenota(db, sc.x_owner, sc.x, c2)

    def test_null_illimitato(self, db, sc):
        limite_piano(db, "smart", None)
        for _ in range(2):
            for tipo in TIPI:
                out = prenota(db, sc.y_owner, sc.y, sc.call, tipo)
                concludi(db, out["bozza_id"], out["esecuzione_id"])
        assert usate(db, sc.y_owner) == 6

    def test_letto_dal_vivo(self, db, sc):
        limite_piano(db, "smart", 1)
        prenota(db, sc.y_owner, sc.y, sc.call)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call, "term_sheet")
        assert detail_of(exc) == "bozze_esaurite"
        limite_piano(db, "smart", 2)
        prenota(db, sc.y_owner, sc.y, sc.call, "term_sheet")

    def test_cambio_di_piano(self, db, sc):
        """Pro (10) → Gratuito: esclusa; → Smart (3) con 3 già usate: esaurite."""
        def passa_a(slug: str) -> None:
            db.execute("select public.fn_switch_plan(%s, (select id from "
                       "public.subscription_plans where slug = %s))", (sc.y_owner, slug))

        passa_a("pro")
        for tipo in TIPI:
            prenota(db, sc.y_owner, sc.y, sc.call, tipo)
        c2 = inserisci_call(db, sc.x)
        membro(db, c2, sc.y)
        passa_a("gratuito")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, c2)
        assert detail_of(exc) == "funzione_non_inclusa"
        passa_a("smart")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, c2)
        assert detail_of(exc) == "bozze_esaurite"
        passa_a("pro")
        prenota(db, sc.y_owner, sc.y, c2)

    def test_mese_successivo(self, db, sc):
        limite_piano(db, "smart", 1)
        out = prenota(db, sc.y_owner, sc.y, sc.call)
        concludi(db, out["bozza_id"], out["esecuzione_id"])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "bozze_esaurite"
        db.execute("update public.partner_bozze_documento set created_at = now() - "
                   "interval '40 days'")
        prenota(db, sc.y_owner, sc.y, sc.call)

    @pytest.mark.parametrize("fuso", ["UTC", "America/New_York", "Asia/Tokyo"])
    def test_confine_del_mese_su_roma(self, db, sc, fuso):
        """Il mese è quello di Roma qualunque sia il fuso della sessione (PostgREST
        non lo fissa a Europe/Rome)."""
        db.execute(f"set timezone = '{fuso}'")
        inizio = inizio_mese_roma(db)
        dentro = bozza_diretta(db, sc.y_owner, sc.y, sc.call, created_at=inizio)
        bozza_diretta(db, sc.y_owner, sc.y, sc.call,
                      created_at=db.execute("select %s::timestamptz - interval '1 second'",
                                            (inizio,)).fetchone()[0])
        assert usate(db, sc.y_owner) == 1
        db.execute("delete from public.partner_bozze_documento where id = %s", (dentro,))
        assert usate(db, sc.y_owner) == 0

    def test_pool_dell_owner_advisor(self, db, sc):
        """Il limite è per titolare, su tutte le sue aziende; un altro owner ha il
        suo."""
        limite_piano(db, "advisor", 2)
        owner = new_user(db, "advisor")
        a = make_company(db, owner)
        b = make_company(db, owner)
        ca = inserisci_call(db, a)
        membro(db, sc.call, b)
        prenota(db, owner, a, ca)
        prenota(db, owner, b, sc.call)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, owner, a, ca, "term_sheet")
        assert detail_of(exc) == "bozze_esaurite"
        assert usate(db, owner) == 2 and usate(db, sc.y_owner) == 0
        prenota(db, sc.y_owner, sc.y, sc.call)  # Y ha il suo pool

    @pytest.mark.parametrize(
        ("stato", "codice", "cost", "input_tokens", "output_tokens", "conta"), [
            # Modello non chiamato (AI non configurata, 4xx non transitorio
            # senza token): 0 esplicito → non conta.
            ("errore", "ai_richiesta_rifiutata", 0, 0, 0, False),
            # Chiamata pagata: costo reale o riserva.
            ("errore", "ai_risposta_non_valida", 3, 100, 0, True),
            ("timeout", "timeout", 5, 0, 0, True),
            # Costo ignoto (nessun usage: rete, guasto dopo l'invio): conta.
            ("errore", "ai_rete", None, 0, 0, True),
            ("errore", "errore_interno", None, 0, 0, True),
            # Errore transitorio del provider (429, 5xx, 529) senza
            # generazione: costo ignoto per il budget, ma NON conta.
            ("errore", "ai_non_disponibile", None, 0, 0, False),
            # Token senza costo (prezzo ignoto): il modello è stato chiamato.
            ("errore", "ai_risposta_non_valida", 0, 0, 50, True),
        ])
    def test_errori_pagati_contano(self, db, sc, stato, codice, cost, input_tokens,
                                   output_tokens, conta):
        limite_piano(db, "smart", 1)
        out = prenota(db, sc.y_owner, sc.y, sc.call)
        concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato="error",
                 bozza_errore=codice, stato=stato, cost=cost, input_tokens=input_tokens,
                 output_tokens=output_tokens)
        assert usate(db, sc.y_owner) == (1 if conta else 0)
        if conta:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                prenota(db, sc.y_owner, sc.y, sc.call)
            assert detail_of(exc) == "bozze_esaurite"
        else:
            prenota(db, sc.y_owner, sc.y, sc.call)

    def test_pending_e_ready_contano(self, db, sc):
        prenota(db, sc.y_owner, sc.y, sc.call)
        out = prenota(db, sc.y_owner, sc.y, sc.call, "term_sheet")
        concludi(db, out["bozza_id"], out["esecuzione_id"])
        assert usate(db, sc.y_owner) == 2

    def test_interrotta_dal_failsafe_conta(self, db, sc):
        limite_piano(db, "smart", 1)
        out = prenota(db, sc.y_owner, sc.y, sc.call)
        invecchia(db, out["bozza_id"], 11)
        assert chiudi_stale(db) == 1
        assert usate(db, sc.y_owner) == 1
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "bozze_esaurite"

    def test_cancellazione_dell_azienda_libera_il_posto(self, db):
        """Come le candidature: le righe di un'azienda cancellata (cascade) non
        contano più."""
        limite_piano(db, "advisor", 1)
        owner = new_user(db, "advisor")
        a = make_company(db, owner)
        b = make_company(db, owner)
        prenota(db, owner, a, inserisci_call(db, a))
        db.execute("delete from public.company_profiles where id = %s", (a,))
        prenota(db, owner, b, inserisci_call(db, b))


# ----------------------------------------------------------------- una pending


class TestUnaPending:
    def test_seconda_prenotazione_stesso_tipo(self, db, sc):
        prenota(db, sc.x_owner, sc.x, sc.call)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call)
        assert detail_of(exc) == "bozza_in_corso"
        assert conta(db, "partner_bozze_documento") == 1
        assert conta(db, "partenariati_ai_esecuzioni") == 1

    def test_altro_tipo_e_altra_call(self, db, sc):
        prenota(db, sc.x_owner, sc.x, sc.call)
        prenota(db, sc.x_owner, sc.x, sc.call, "term_sheet")
        prenota(db, sc.x_owner, sc.x, inserisci_call(db, sc.x))
        assert conta(db, "partner_bozze_documento") == 3

    @pytest.mark.parametrize("esito", ["ready", "error"])
    def test_dopo_la_chiusura_si_riprenota(self, db, sc, esito):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato=esito)
        nuova = prenota(db, sc.x_owner, sc.x, sc.call)
        assert nuova["bozza_id"] != out["bozza_id"]
        assert bozza(db, out["bozza_id"])["stato"] == esito

    def test_pending_orfana_si_chiude_e_si_riprenota(self, db, sc):
        vecchia = prenota(db, sc.x_owner, sc.x, sc.call, riserva=13)
        invecchia(db, vecchia["bozza_id"], 11)
        nuova = prenota(db, sc.x_owner, sc.x, sc.call)
        assert nuova["bozza_id"] != vecchia["bozza_id"]
        b = bozza(db, vecchia["bozza_id"])
        assert (b["stato"], b["errore"], b["cost_cents"], b["llm_eseguito"]) == (
            "error", "interrotta", None, False)
        assert b["ready_at"] is not None
        e = esecuzione(db, vecchia["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["errore_codice"]) == (
            "interrotta", None, "interrotta")
        (uso,) = consumi(db)
        assert (uso["provider"], uso["service"], uso["outcome"], uso["cost_cents"]) == (
            "anthropic", "partner_bozza", "timeout_unknown", 13)
        assert (str(uso["user_id"]), str(uso["family_parent_id"])) == (sc.x_owner, sc.x_owner)
        assert uso["request_meta"]["esecuzione_id"] == vecchia["esecuzione_id"]
        assert uso["request_meta"]["failsafe"] is True
        assert bozza(db, nuova["bozza_id"])["stato"] == "pending"

    def test_pending_di_9_minuti_non_e_orfana(self, db, sc):
        vecchia = prenota(db, sc.x_owner, sc.x, sc.call)
        invecchia(db, vecchia["bozza_id"], 9)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call)
        assert detail_of(exc) == "bozza_in_corso"

    def test_orfana_con_il_limite_esaurito_resta_al_failsafe(self, db, sc):
        """Con il limite esaurito (l'orfana conta) la RPC fallisce prima di
        chiuderla: la transazione non lascia effetti."""
        limite_piano(db, "smart", 1)
        vecchia = prenota(db, sc.y_owner, sc.y, sc.call)
        invecchia(db, vecchia["bozza_id"], 11)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "bozze_esaurite"
        assert bozza(db, vecchia["bozza_id"])["stato"] == "pending"
        assert consumi(db) == []

    def test_orfana_con_il_budget_esaurito_resta_al_failsafe(self, db, sc):
        vecchia = prenota(db, sc.x_owner, sc.x, sc.call, budget=10, riserva=8)
        invecchia(db, vecchia["bozza_id"], 11)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call, budget=10, riserva=8)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert bozza(db, vecchia["bozza_id"])["stato"] == "pending"
        assert esecuzione(db, vecchia["esecuzione_id"])["stato"] == "in_corso"
        assert consumi(db) == []


# ----------------------------------------------------------------- budget


class TestBudget:
    def test_budget_esaurito(self, db, sc):
        prenota(db, sc.x_owner, sc.x, sc.call, budget=15, riserva=10)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call, budget=15, riserva=10)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert conta(db, "partner_bozze_documento") == 1
        assert conta(db, "partenariati_ai_esecuzioni") == 1

    @pytest.mark.parametrize("budget", [None, 0, -5])
    def test_budget_nullo_nega(self, db, sc, budget):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call, budget=budget)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert conta(db, "partner_bozze_documento") == 0

    def test_gruppo_altri_condiviso(self, db, sc):
        """Il budget del gruppo altri comprende la spesa degli altri servizi
        (bozza del profilo, job della call); il gruppo bando no."""
        for servizio, gruppo, costo in (("partner_profilo_ai", "altri", 6),
                                        ("partenariato_estrazione", "bando", 50)):
            db.execute(
                "insert into public.partenariati_ai_esecuzioni (servizio, origine, gruppo, "
                "giorno, costo_riservato_cents, stato, llm_eseguito, cost_cents, conclusa_at) "
                "values (%s, 'utente', %s, (now() at time zone 'Europe/Rome')::date, 1, "
                "'conclusa', true, %s, now())", (servizio, gruppo, costo))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call, budget=10, riserva=5)
        assert detail_of(exc) == "ai_budget_esaurito"
        prenota(db, sc.x_owner, sc.x, sc.call, budget=10, riserva=4)

    def test_la_riserva_resta_finche_il_costo_e_ignoto(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call, budget=10, riserva=6)
        concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato="error",
                 stato="errore", cost=None, input_tokens=0, output_tokens=0)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.x_owner, sc.x, sc.call, budget=10, riserva=6)
        assert detail_of(exc) == "ai_budget_esaurito"


# ----------------------------------------------------------------- chiusura


class TestConcludi:
    def test_ready_ed_esecuzione_insieme(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        esito = concludi(db, out["bozza_id"], out["esecuzione_id"], cost=9, input_tokens=1000,
                         output_tokens=2000)
        assert esito == {"bozza_scritta": True, "esecuzione_chiusa": True}
        b = bozza(db, out["bozza_id"])
        assert (b["stato"], b["contenuto"], b["errore"], b["cost_cents"], b["input_tokens"],
                b["output_tokens"], b["model"], b["llm_eseguito"]) == (
            "ready", CONTENUTO, None, 9, 1000, 2000, "claude-sonnet-5", True)
        assert b["ready_at"] is not None
        e = esecuzione(db, out["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["input_tokens"], e["llm_eseguito"],
                e["model"]) == ("conclusa", 9, 1000, True, "claude-sonnet-5")
        # Seconda chiusura: nessun effetto.
        assert concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato="error") == {
            "bozza_scritta": False, "esecuzione_chiusa": False}
        assert bozza(db, out["bozza_id"])["stato"] == "ready"

    def test_error(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        esito = concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato="error",
                         bozza_errore="output_non_valido", cost=5, errore="output_non_valido")
        assert esito == {"bozza_scritta": True, "esecuzione_chiusa": True}
        b = bozza(db, out["bozza_id"])
        assert (b["stato"], b["errore"], b["contenuto"], b["cost_cents"]) == (
            "error", "output_non_valido", None, 5)
        e = esecuzione(db, out["esecuzione_id"])
        assert (e["stato"], e["errore_codice"], e["cost_cents"]) == (
            "errore", "output_non_valido", 5)

    def test_costo_ignoto(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato="error", cost=None,
                 input_tokens=None, output_tokens=None, model=None)
        b = bozza(db, out["bozza_id"])
        assert (b["cost_cents"], b["input_tokens"], b["output_tokens"], b["llm_eseguito"],
                b["model"]) == (None, 0, 0, False, None)
        assert esecuzione(db, out["esecuzione_id"])["cost_cents"] is None

    def test_il_failsafe_vince(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        invecchia(db, out["bozza_id"], 11)
        chiudi_stale(db)
        assert concludi(db, out["bozza_id"], out["esecuzione_id"]) == {
            "bozza_scritta": False, "esecuzione_chiusa": False}
        b = bozza(db, out["bozza_id"])
        assert (b["stato"], b["errore"], b["contenuto"]) == ("error", "interrotta", None)
        assert len(consumi(db)) == 1  # solo la riga del failsafe

    def test_una_nuova_prenotazione_vince(self, db, sc):
        vecchia = prenota(db, sc.x_owner, sc.x, sc.call)
        invecchia(db, vecchia["bozza_id"], 11)
        nuova = prenota(db, sc.x_owner, sc.x, sc.call)
        assert concludi(db, vecchia["bozza_id"], vecchia["esecuzione_id"]) == {
            "bozza_scritta": False, "esecuzione_chiusa": False}
        assert bozza(db, nuova["bozza_id"])["stato"] == "pending"

    def test_esecuzione_di_un_altra_bozza(self, db, sc):
        a = prenota(db, sc.x_owner, sc.x, sc.call)
        b = prenota(db, sc.x_owner, sc.x, sc.call, "term_sheet")
        assert concludi(db, a["bozza_id"], b["esecuzione_id"]) == {
            "bozza_scritta": False, "esecuzione_chiusa": True}
        assert bozza(db, a["bozza_id"])["stato"] == "pending"

    def test_bozza_cancellata_esecuzione_chiusa(self, db, sc):
        """Call cancellata durante il job (cascade): l'esecuzione si chiude
        comunque, così il job registra il consumo."""
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        db.execute("delete from public.partner_calls where id = %s", (sc.call,))
        assert concludi(db, out["bozza_id"], out["esecuzione_id"]) == {
            "bozza_scritta": False, "esecuzione_chiusa": True}
        assert esecuzione(db, out["esecuzione_id"])["stato"] == "conclusa"

    def test_esecuzione_di_un_altro_servizio_non_si_chiude(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        altra = db.execute(
            "select public.fn_partenariati_ai_prenota('partner_profilo_ai', 'utente', 'altri', "
            "1000, 1, %s::uuid, null, %s::uuid, null, %s::uuid, null)",
            (sc.x_owner, sc.x_owner, sc.x)).fetchone()[0]
        assert concludi(db, out["bozza_id"], str(altra))["esecuzione_chiusa"] is False
        assert esecuzione(db, altra)["stato"] == "in_corso"

    @pytest.mark.parametrize("campi", [
        {"bozza_stato": "pronta"},
        {"bozza_stato": None},
        {"bozza_stato": "pending"},
        {"bozza_stato": "ready", "contenuto": None},
        {"bozza_stato": "ready", "contenuto": Jsonb([CONTENUTO])},
        {"bozza_stato": "error", "bozza_errore": None},
        {"bozza_stato": "error", "bozza_errore": ""},
        {"bozza_stato": "error", "bozza_errore": "x" * 201},
        {"stato": "riusata"},
        {"stato": "in_corso"},
        {"cost": -1},
        {"input_tokens": -1},
    ])
    def test_parametri_non_validi(self, db, sc, campi):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi(db, out["bozza_id"], out["esecuzione_id"], **campi)
        assert detail_of(exc) == "parametri_non_validi"
        assert bozza(db, out["bozza_id"])["stato"] == "pending"
        assert esecuzione(db, out["esecuzione_id"])["stato"] == "in_corso"

    @pytest.mark.parametrize("nullo", ["bozza", "esecuzione"])
    def test_identificativi_nulli(self, db, sc, nullo):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi(db, None if nullo == "bozza" else out["bozza_id"],
                     None if nullo == "esecuzione" else out["esecuzione_id"])
        assert detail_of(exc) == "parametri_non_validi"


# ----------------------------------------------------------------- failsafe


class TestStale:
    def test_pending_oltre_la_soglia(self, db, sc):
        vecchia = prenota(db, sc.x_owner, sc.x, sc.call, riserva=11)
        fresca = prenota(db, sc.y_owner, sc.y, sc.call)
        invecchia(db, vecchia["bozza_id"], 11)
        assert chiudi_stale(db, 10) == 1
        b = bozza(db, vecchia["bozza_id"])
        assert (b["stato"], b["errore"], b["cost_cents"], b["llm_eseguito"]) == (
            "error", "interrotta", None, False)
        assert b["ready_at"] is not None
        e = esecuzione(db, vecchia["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["errore_codice"]) == (
            "interrotta", None, "interrotta")
        (uso,) = consumi(db)
        assert (uso["service"], uso["outcome"], uso["cost_cents"]) == (
            "partner_bozza", "timeout_unknown", 11)
        assert uso["request_meta"] == {
            "company_profile_id": sc.x, "bando_id": e["bando_id"],
            "esecuzione_id": vecchia["esecuzione_id"], "esito": "interrotta", "failsafe": True}
        assert bozza(db, fresca["bozza_id"])["stato"] == "pending"
        assert esecuzione(db, fresca["esecuzione_id"])["stato"] == "in_corso"

    def test_idempotente(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        invecchia(db, out["bozza_id"], 30)
        assert chiudi_stale(db) == 1
        assert chiudi_stale(db) == 0
        assert len(consumi(db)) == 1

    @pytest.mark.parametrize(("minuti", "eta", "chiusa"), [
        (None, 11, True), (None, 9, False), (0, 2, True), (-5, 2, True), (30, 20, False),
        (0, 0, False), (-5, 0, False),
    ])
    def test_soglia(self, db, sc, minuti, eta, chiusa):
        """NULL = 10 minuti, minimo 1."""
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        invecchia(db, out["bozza_id"], eta)
        assert chiudi_stale(db, minuti) == (1 if chiusa else 0)

    def test_esecuzioni_orfane(self, db, sc):
        """Esecuzione rimasta in_corso senza bozza pending (call cancellata a metà
        del job): chiusa con timeout_unknown."""
        out = prenota(db, sc.x_owner, sc.x, sc.call, riserva=7)
        invecchia(db, out["bozza_id"], 11)
        db.execute("delete from public.partner_calls where id = %s", (sc.call,))
        assert chiudi_stale(db) == 1
        assert esecuzione(db, out["esecuzione_id"])["stato"] == "interrotta"
        (uso,) = consumi(db)
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", 7)

    def test_esecuzione_orfana_recente_resta(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        db.execute("delete from public.partner_calls where id = %s", (sc.call,))
        assert chiudi_stale(db) == 0
        assert esecuzione(db, out["esecuzione_id"])["stato"] == "in_corso"

    def test_non_tocca_gli_altri_servizi(self, db, sc):
        altra = db.execute(
            "select public.fn_partenariati_ai_prenota('partner_profilo_ai', 'utente', 'altri', "
            "1000, 1, %s::uuid, null, %s::uuid, null, %s::uuid, null)",
            (sc.x_owner, sc.x_owner, sc.x)).fetchone()[0]
        db.execute("update public.partenariati_ai_esecuzioni set avviata_at = now() - "
                   "interval '1 hour' where id = %s", (altra,))
        assert chiudi_stale(db) == 0
        assert esecuzione(db, altra)["stato"] == "in_corso"

    def test_salta_le_righe_bloccate(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        invecchia(db, out["bozza_id"], 11)
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select 1 from public.partner_bozze_documento where id = %s "
                          "for update", (out["bozza_id"],))
            db.execute("set lock_timeout = '300ms'")
            # La bozza bloccata si salta; la sua esecuzione è referenziata da una
            # pending e non entra nel secondo giro.
            assert chiudi_stale(db) == 0
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        assert chiudi_stale(db) == 1


# ----------------------------------------------------------------- cascade


class TestCascade:
    def test_dalla_call(self, db, sc):
        out = prenota(db, sc.x_owner, sc.x, sc.call)
        prenota(db, sc.y_owner, sc.y, sc.call)
        altra = inserisci_call(db, sc.x)
        rimasta = prenota(db, sc.x_owner, sc.x, altra)
        db.execute("delete from public.partner_calls where id = %s", (sc.call,))
        assert conta(db, "partner_bozze_documento") == 1
        assert bozza(db, rimasta["bozza_id"]) is not None
        # Il registro della spesa (senza FK) resta.
        assert esecuzione(db, out["esecuzione_id"]) is not None

    def test_dall_azienda(self, db, sc):
        prenota(db, sc.x_owner, sc.x, sc.call)
        out_y = prenota(db, sc.y_owner, sc.y, sc.call)
        db.execute("delete from public.company_profiles where id = %s", (sc.y,))
        assert conta(db, "partner_bozze_documento") == 1
        assert bozza(db, out_y["bozza_id"]) is None
        assert esecuzione(db, out_y["esecuzione_id"]) is not None


# ----------------------------------------------------------------- limiti e snapshot


class TestLimitiESnapshot:
    @pytest.mark.parametrize("slug", sorted(SEED_Q7))
    def test_limiti_per_slug(self, db, slug):
        owner = new_user(db, None if slug == "gratuito" else slug)
        esito = limiti(db, owner)
        assert esito["bozze_mese"] == SEED_Q7[slug] and esito["piano_attivo"] is True
        assert set(esito) == {"calls_attive_max", "candidature_mese", "bozze_mese",
                              "piano_attivo"}

    def test_limiti_null_illimitato(self, db):
        limite_piano(db, "pro", None)
        owner = new_user(db, "pro")
        testo = db.execute("select public.fn_partenariati_limiti(%s::uuid)::text",
                           (owner,)).fetchone()[0]
        assert '"bozze_mese": null' in testo

    @pytest.mark.parametrize("caso", ["collegato", "inesistente", "nullo", "disdetto"])
    def test_limiti_senza_abbonamento(self, db, caso):
        if caso == "collegato":
            owner = new_user(db, invitato=True)
        elif caso == "inesistente":
            owner = str(uuid.uuid4())
        elif caso == "nullo":
            owner = None
        else:
            owner = new_user(db, "pro")
            db.execute("update public.user_subscriptions set status = 'expired' "
                       "where user_id = %s", (owner,))
        assert limiti(db, owner) == {"calls_attive_max": 0, "candidature_mese": 0,
                                     "bozze_mese": 0, "piano_attivo": False}

    def test_limiti_invariati_per_gli_altri_campi(self, db):
        owner = new_user(db, "pro")
        esito = limiti(db, owner)
        assert (esito["calls_attive_max"], esito["candidature_mese"]) == db.execute(
            "select partner_calls_attive_max, partner_candidature_mese "
            "from public.subscription_plans where slug = 'pro'").fetchone()

    def test_snapshot_forma(self, db, sc):
        inizio, fine = db.execute(
            "select date_trunc('month', now() at time zone 'Europe/Rome')::date, "
            "(date_trunc('month', now() at time zone 'Europe/Rome') + interval '1 month' "
            "- interval '1 day')::date").fetchone()
        prenota(db, sc.y_owner, sc.y, sc.call)
        s = snapshot(db, sc.y_owner)
        periodo = {"periodo_inizio": inizio.isoformat(), "periodo_fine": fine.isoformat()}
        assert s == {
            "call_attive": {"limite": 1, "usate": 0, "residuo": 1},
            "candidature_mese": {"limite": 5, "usate": 0, "residuo": 5, **periodo},
            "bozze_mese": {"limite": 3, "usate": 1, "residuo": 2, **periodo},
        }

    def test_snapshot_stessa_formula_della_rpc(self, db, sc):
        out = prenota(db, sc.y_owner, sc.y, sc.call)
        concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato="error", cost=0,
                 input_tokens=0, output_tokens=0)  # non conta
        out = prenota(db, sc.y_owner, sc.y, sc.call)
        concludi(db, out["bozza_id"], out["esecuzione_id"], bozza_stato="error", cost=2)
        prenota(db, sc.y_owner, sc.y, sc.call, "term_sheet")
        bozza_diretta(db, sc.y_owner, sc.y, sc.call,
                      created_at=db.execute("select now() - interval '40 days'").fetchone()[0])
        s = snapshot(db, sc.y_owner)["bozze_mese"]
        assert s["usate"] == usate(db, sc.y_owner) == 2
        assert s["residuo"] == 1

    def test_snapshot_residuo_mai_negativo_e_illimitato(self, db, sc):
        for tipo in TIPI:
            prenota(db, sc.y_owner, sc.y, sc.call, tipo)
        limite_piano(db, "smart", 1)
        s = snapshot(db, sc.y_owner)["bozze_mese"]
        assert (s["limite"], s["usate"], s["residuo"]) == (1, 3, 0)
        limite_piano(db, "smart", None)
        s = snapshot(db, sc.y_owner)["bozze_mese"]
        assert (s["limite"], s["usate"], s["residuo"]) == (None, 3, None)

    def test_snapshot_pool_dell_owner(self, db, sc):
        owner = new_user(db, "advisor")
        a = make_company(db, owner)
        b = make_company(db, owner)
        prenota(db, owner, a, inserisci_call(db, a))
        membro(db, sc.call, b)
        prenota(db, owner, b, sc.call)
        assert snapshot(db, owner)["bozze_mese"]["usate"] == 2
        assert snapshot(db, sc.y_owner)["bozze_mese"]["usate"] == 0

    def test_snapshot_senza_abbonamento(self, db):
        s = snapshot(db, str(uuid.uuid4()))
        assert (s["bozze_mese"]["limite"], s["bozze_mese"]["usate"],
                s["bozze_mese"]["residuo"]) == (0, 0, 0)

    def test_limiti_senza_la_chiave_fail_closed(self, db, sc):
        """Una risposta di fn_partenariati_limiti senza bozze_mese vale 0 (esclusa),
        una con la chiave a null vale illimitato."""
        db.execute("create or replace function public.fn_partenariati_limiti(p_owner uuid) "
                   "returns jsonb language sql stable security definer "
                   "set search_path = public as $$ select '{}'::jsonb $$")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "funzione_non_inclusa"
        s = snapshot(db, sc.y_owner)["bozze_mese"]
        assert (s["limite"], s["residuo"]) == (0, 0)
        db.execute("create or replace function public.fn_partenariati_limiti(p_owner uuid) "
                   "returns jsonb language sql stable security definer set search_path = "
                   "public as $$ select '{\"bozze_mese\": null}'::jsonb $$")
        for tipo in TIPI:
            prenota(db, sc.y_owner, sc.y, sc.call, tipo)
        assert snapshot(db, sc.y_owner)["bozze_mese"]["limite"] is None


# ----------------------------------------------------------------- lock


class TestLock:
    def test_lock_owner_con_seconda_connessione(self, db, sc):
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select 1 from public.profiles where id = %s for update",
                          (sc.y_owner,))
            db.execute("set lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                prenota(db, sc.y_owner, sc.y, sc.call)
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        prenota(db, sc.y_owner, sc.y, sc.call)

    @pytest.mark.parametrize("chi", ["creatore", "membro"])
    def test_righe_prima_del_budget(self, db, sc, chi):
        """Con il lock del budget occupato la prenotazione attende tenendo già owner,
        azienda, call (FOR SHARE) e, per un membro, la sua riga del consorzio."""
        owner, company = (sc.x_owner, sc.x) if chi == "creatore" else (sc.y_owner, sc.y)
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: prenota(db, owner, company, sc.call))
        try:
            altra.execute("select pg_advisory_xact_lock(hashtext('partenariati_ai_budget'))")
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            bloccate = [
                ("select 1 from public.profiles where id = %s for update nowait", owner),
                ("select 1 from public.company_profiles where id = %s "
                 "for no key update nowait", company),
                ("select 1 from public.partner_calls where id = %s for update nowait", sc.call),
            ]
            if chi == "membro":
                bloccate.append(("select 1 from public.partner_call_membri where id = %s "
                                 "for update nowait", sc.my))
            for sql, valore in bloccate:
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    terza.execute(sql, (valore,))
            # FOR SHARE: un'altra lettura condivisa della call passa.
            terza.execute("select 1 from public.partner_calls where id = %s "
                          "for share nowait", (sc.call,))
            altra.rollback()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert "out" in esito, esito

    def test_pending_prima_del_budget(self, db, sc):
        """La pending orfana della stessa chiave è bloccata prima del lock del
        budget; la sua esecuzione (registro della spesa) solo dopo."""
        vecchia = prenota(db, sc.x_owner, sc.x, sc.call)
        invecchia(db, vecchia["bozza_id"], 11)
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: prenota(db, sc.x_owner, sc.x, sc.call))
        try:
            altra.execute("select pg_advisory_xact_lock(hashtext('partenariati_ai_budget'))")
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                terza.execute("select 1 from public.partner_bozze_documento where id = %s "
                              "for update nowait", (vecchia["bozza_id"],))
            terza.execute("select 1 from public.partenariati_ai_esecuzioni where id = %s "
                          "for update nowait", (vecchia["esecuzione_id"],))
            altra.rollback()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert "out" in esito, esito
        assert bozza(db, vecchia["bozza_id"])["stato"] == "error"

    def test_due_prenotazioni_concorrenti_stessa_chiave(self, db, sc):
        """La seconda attende il lock dell'owner e poi trova la pending: una sola
        bozza e una sola esecuzione."""
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: prenota(db, sc.y_owner, sc.y, sc.call))
        try:
            prenota(altra, sc.y_owner, sc.y, sc.call)
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, monitor)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito["errore"].diag.message_detail == "bozza_in_corso", esito
        assert conta(db, "partner_bozze_documento") == 1
        assert conta(db, "partenariati_ai_esecuzioni") == 1

    def test_pool_concorrente_tra_aziende_advisor(self, db, sc):
        """Due aziende dello stesso Advisor con una sola bozza residua: la seconda
        prenotazione attende l'owner e poi trova il limite esaurito."""
        limite_piano(db, "advisor", 1)
        owner = new_user(db, "advisor")
        a = make_company(db, owner)
        b = make_company(db, owner)
        ca = inserisci_call(db, a)
        cb = inserisci_call(db, b)
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: prenota(db, owner, b, cb))
        try:
            prenota(altra, owner, a, ca)
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, monitor)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito["errore"].diag.message_detail == "bozze_esaurite", esito
        assert usate(db, owner) == 1

    def test_l_uscita_dal_consorzio_attende_la_prenotazione(self, db, sc):
        """Il creatore che toglie Y dal consorzio attende la prenotazione di Y
        (call FOR SHARE); dopo il commit Y non è più membro e non riprenota."""
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esci = ("select public.fn_partner_membro_esci(%s::uuid, %s::uuid, %s::uuid, %s::uuid)",
                (sc.my, sc.x_owner, sc.x_owner, sc.x))
        thread, esito = in_thread(lambda: db.execute(*esci).fetchone()[0])
        try:
            prenota(altra, sc.y_owner, sc.y, sc.call)
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, monitor)
            if thread.is_alive():
                thread.join(timeout=10)
        assert "out" in esito, esito
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, sc.y_owner, sc.y, sc.call, "term_sheet")
        assert detail_of(exc) == "call_non_trovata"


# ----------------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0042, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0042, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")
ESEGUIBILE = "\n".join(r for r in SQL_0042.splitlines() if not r.lstrip().startswith("--"))
DETAIL_NEL_FILE = set(re.findall(r"detail = '([a-z_]+)'", ESEGUIBILE))
# Detail NON mappati di proposito (bug del backend → 502, come nei WP5-WP9).
DETAIL_NON_MAPPATI = {"parametri_non_validi"}


def _corpo(sql: str, nome: str) -> list[str]:
    m = re.search(rf"^create or replace function public\.{nome}\(.*?^\$\$;\n", sql, re.M | re.S)
    assert m, nome
    return m.group(0).splitlines()


class TestSicurezza0042:
    def test_inventario_del_file(self):
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == set(FIRME)
        assert not re.search(r"^create (function|trigger|table(?! public\.))", ESEGUIBILE,
                             re.M)

    def test_additiva(self):
        """Nessun DROP; l'unica tabella esistente alterata è subscription_plans (una
        colonna in più); le sole funzioni esistenti toccate sono le due
        ridefinite."""
        assert not re.search(r"^\s*(drop|alter function|create or replace view)\b",
                             ESEGUIBILE, re.M | re.I)
        alterate = set(re.findall(r"^alter table public\.(\w+)", ESEGUIBILE, re.M))
        assert alterate == TABELLE_NUOVE | {"subscription_plans"}
        blocco = re.search(r"^alter table public\.subscription_plans\n(.*?);\n", ESEGUIBILE,
                           re.M | re.S).group(1)
        assert re.findall(r"add column (\w+)", blocco) == ["partner_bozze_mese"]
        assert FUNZIONI_NEL_FILE - FUNZIONI_NUOVE == RIDEFINITE
        # Le funzioni nuove non esistevano prima della 0042.
        precedenti = "\n".join(p.read_text(encoding="utf-8")
                               for p in sorted(MIGRAZIONI.glob("*.sql"))
                               if p.name < "0042")
        for nome in FUNZIONI_NUOVE:
            assert f"function public.{nome}(" not in precedenti, nome
        for nome in RIDEFINITE:
            assert f"function public.{nome}(" in precedenti, nome

    def test_ridefinite_con_la_stessa_firma(self, db):
        for nome in RIDEFINITE:
            firme = [r[0] for r in db.execute(
                "select p.oid::regprocedure::text from pg_proc p join pg_namespace n "
                "on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = %s",
                (nome,)).fetchall()]
            assert firme == [FIRME[nome]]
            assert db.execute(
                "select pg_get_function_result(%s::regprocedure)", (f"public.{FIRME[nome]}",)
            ).fetchone()[0] == "jsonb"

    def test_ultima_definizione_precedente(self):
        """La copia di riferimento è davvero l'ULTIMA definizione prima della 0042."""
        for nome, origine in ORIGINE.items():
            ultime = [p.name for p in sorted(MIGRAZIONI.glob("*.sql")) if p.name < "0042"
                      and re.search(rf"^create or replace function public\.{nome}\(",
                                    p.read_text(encoding="utf-8"), re.M)]
            assert ultime[-1] == origine, (nome, ultime)

    def test_copia_fedele_dei_corpi(self):
        """Ogni corpo ridefinito è quello dell'ultima definizione con le sole
        modifiche previste (righe tolte e aggiunte esatte)."""
        attese = {
            "fn_partenariati_limiti": (
                ["select sp.partner_calls_attive_max, sp.partner_candidature_mese",
                 "into v_calls, v_cand",
                 "'calls_attive_max', 0, 'candidature_mese', 0, 'piano_attivo', false);",
                 "'calls_attive_max', v_calls, 'candidature_mese', v_cand, 'piano_attivo', "
                 "true);"],
                ["v_bozze integer;  -- 0042",
                 "select sp.partner_calls_attive_max, sp.partner_candidature_mese, "
                 "sp.partner_bozze_mese",
                 "into v_calls, v_cand, v_bozze",
                 "'calls_attive_max', 0, 'candidature_mese', 0, 'bozze_mese', 0, "
                 "'piano_attivo', false);",
                 "'calls_attive_max', v_calls, 'candidature_mese', v_cand, 'bozze_mese', "
                 "v_bozze,",
                 "'piano_attivo', true);"]),
            # Solo aggiunte: la chiusura «::date));» della 0039 resta come ultima
            # riga, dopo il nuovo blocco bozze_mese.
            "fn_partenariati_snapshot": (
                [],
                ["v_bozze       integer;  -- 0042",
                 "v_bozze_usate integer;  -- 0042",
                 "v_bozze := public.fn_partner_bozze_limite(p_owner);",
                 "v_bozze_usate := public.fn_partner_bozze_usate(p_owner);",
                 "'periodo_fine', (v_inizio + interval '1 month' - interval '1 day')::date),",
                 "'bozze_mese', jsonb_build_object(",
                 "'limite', v_bozze,",
                 "'usate', v_bozze_usate,",
                 "'residuo', case when v_bozze is null then null",
                 "else greatest(v_bozze - v_bozze_usate, 0) end,",
                 "'periodo_inizio', v_inizio,"]),
        }
        for nome, (tolte_attese, aggiunte_attese) in attese.items():
            orig = _corpo((MIGRAZIONI / ORIGINE[nome]).read_text(encoding="utf-8"), nome)
            nuovo = _corpo(SQL_0042, nome)
            diff = list(difflib.ndiff(orig, nuovo))
            tolte = [r[2:].strip() for r in diff if r.startswith("- ")]
            aggiunte = [r[2:].strip() for r in diff if r.startswith("+ ")]
            assert tolte == tolte_attese, nome
            assert aggiunte == aggiunte_attese, nome

    def test_volatilita(self, db):
        for nome in ("fn_partenariati_limiti", "fn_partenariati_snapshot",
                     "fn_partner_bozze_usate", "fn_partner_bozze_limite"):
            assert db.execute("select provolatile from pg_proc where proname = %s",
                              (nome,)).fetchone()[0] == "s", nome
        for nome in ("fn_partner_bozza_prenota", "fn_partner_bozza_concludi",
                     "fn_partner_bozza_chiudi_stale", "fn_partner_bozza_esecuzione_interrotta",
                     "fn_seed_limiti_bozze_0042"):
            assert db.execute("select provolatile from pg_proc where proname = %s",
                              (nome,)).fetchone()[0] == "v", nome

    def test_nessun_trigger_sulla_tabella(self, db):
        assert db.execute(
            "select count(*) from pg_trigger where not tgisinternal "
            "and tgrelid = 'public.partner_bozze_documento'::regclass").fetchone()[0] == 0

    @pytest.mark.parametrize("tabella", sorted(TABELLE_NUOVE))
    def test_rls_attiva_senza_policy(self, db, tabella):
        assert db.execute(
            "select relrowsecurity from pg_class where oid = %s::regclass",
            (f"public.{tabella}",),
        ).fetchone()[0] is True
        assert db.execute(
            "select count(*) from pg_policies where schemaname = 'public' and tablename = %s",
            (tabella,),
        ).fetchone()[0] == 0

    @pytest.mark.parametrize("tabella", sorted(TABELLE_NUOVE))
    def test_nessun_privilegio_ai_client(self, db, tabella):
        for ruolo in ("anon", "authenticated"):
            for privilegio in PRIVILEGI_TABELLA:
                assert not db.execute(
                    "select has_table_privilege(%s, %s, %s)",
                    (ruolo, f"public.{tabella}", privilegio),
                ).fetchone()[0], f"{ruolo} ha {privilegio} su {tabella}"

    @pytest.mark.parametrize("tabella", sorted(TABELLE_NUOVE))
    def test_revoche_e_rls_scritte_nel_file(self, tabella):
        assert re.search(
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0042, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0042, re.M
        ), tabella

    def test_colonna_di_piano_invisibile_ai_client(self, db):
        for ruolo in ("anon", "authenticated"):
            for privilegio in ("select", "insert", "update"):
                assert not db.execute(
                    "select has_column_privilege(%s, 'public.subscription_plans', "
                    "'partner_bozze_mese', %s)", (ruolo, privilegio),
                ).fetchone()[0], f"{ruolo} ha {privilegio} su partner_bozze_mese"

    def test_funzioni_protette_e_una_sola_per_nome(self, db):
        """Generico: ogni funzione della migration e ogni fn_partner_% /
        fn_partenariat% / fn_seed_limiti_% presente nel DB è SECURITY DEFINER con
        search_path fissato, non eseguibile dai client (PUBLIC compreso) ed
        esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public'
                  and (p.proname like 'fn\_partner\_%%' or p.proname like 'fn\_partenariat%%'
                       or p.proname like 'fn\_seed\_limiti\_%%')"""
        ).fetchall()}
        assert set(FIRME) <= dal_db
        for nome in sorted(FUNZIONI_NEL_FILE | set(FIRME) | dal_db):
            righe_ = db.execute(
                """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                          coalesce(p.proacl::text, ''), p.oid::regprocedure::text
                   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and p.proname = %s""",
                (nome,),
            ).fetchall()
            assert len(righe_) == 1, f"{nome}: {len(righe_)} firme"
            oid, secdef, config, acl, firma = righe_[0]
            if nome in FIRME:
                assert firma == FIRME[nome]
            assert secdef is True, f"{nome} non è security definer"
            assert "search_path=public" in config, f"{nome}: search_path non fissato"
            assert acl and not re.search(r"[{,]=X", acl), f"{nome}: PUBLIC esegue ({acl})"
            for ruolo in ("anon", "authenticated"):
                assert not db.execute(
                    "select has_function_privilege(%s, %s::oid, 'execute')", (ruolo, oid)
                ).fetchone()[0], f"{ruolo} esegue {nome}"

    def test_revoche_scritte_nel_file(self):
        for nome, firma in FIRME.items():
            assert re.search(
                rf"^revoke execute on function public\.{nome}\([^)]*\)\s+"
                r"from public, anon, authenticated;",
                SQL_0042, re.M,
            ), nome
            assert firma.startswith(nome)

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_seed_limiti_bozze_0042()",
        "select public.fn_partner_bozze_usate(gen_random_uuid())",
        "select public.fn_partner_bozze_limite(gen_random_uuid())",
        "select public.fn_partner_bozza_esecuzione_interrotta(gen_random_uuid())",
        "select public.fn_partner_bozza_prenota(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), 'nda', '{}'::jsonb, 100, 5)",
        "select public.fn_partner_bozza_concludi(gen_random_uuid(), gen_random_uuid(), 'ready', "
        "'{}'::jsonb, null, 'conclusa', 1, 1, 1, null, null)",
        "select public.fn_partner_bozza_chiudi_stale(10)",
        "select public.fn_partenariati_limiti(gen_random_uuid())",
        "select public.fn_partenariati_snapshot(gen_random_uuid())",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, ruolo, chiamata):
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_esegue_il_flusso(self, db, sc):
        """Come il service_role: un grant esplicito sulle RPC basta e le funzioni
        scrivono senza privilegi di tabella."""
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        rpc = ("fn_partner_bozza_prenota", "fn_partner_bozza_concludi",
               "fn_partner_bozza_chiudi_stale", "fn_partenariati_snapshot")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            for nome in rpc:
                db.execute(f"grant execute on function public.{FIRME[nome]} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                a = prenota(db, sc.y_owner, sc.y, sc.call)
                concludi(db, a["bozza_id"], a["esecuzione_id"])
                b = prenota(db, sc.x_owner, sc.x, sc.call)
                assert chiudi_stale(db, 10) == 0
                assert snapshot(db, sc.y_owner)["bozze_mese"]["usate"] == 1
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    db.execute("select 1 from public.partner_bozze_documento")
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        assert bozza(db, a["bozza_id"])["stato"] == "ready"
        assert bozza(db, b["bozza_id"])["stato"] == "pending"

    def test_nessun_identificativo_in_chiaro_nelle_colonne(self, db):
        """La tabella nuova non ha colonne per P.IVA, CF, email o nomi (T8)."""
        colonne = {r[0] for r in db.execute(
            "select column_name::text from information_schema.columns "
            "where table_schema = 'public' and table_name::text = any (%s)",
            (sorted(TABELLE_NUOVE),)).fetchall()}
        assert len(colonne) > 12
        for vietata in ("partita_iva", "piva", "codice_fiscale", "cf", "email", "nome",
                        "cognome", "ragione_sociale", "denominazione", "telefono"):
            assert vietata not in colonne, vietata

    def test_ogni_detail_nuovo_e_mappato(self):
        """Ogni detail della 0042 ha la sua voce in partenariato_errori.RPC_ERRORS
        (status, code, messaggio), salvo quelli lasciati a 502 di proposito."""
        errori = pytest.importorskip("app.services.partenariato_errori")
        assert DETAIL_NEL_FILE == {
            "parametri_non_validi", "attore_non_titolare", "call_non_trovata",
            "funzione_non_inclusa", "bozza_in_corso", "bozze_esaurite"}
        mancanti = DETAIL_NEL_FILE - set(errori.RPC_ERRORS) - DETAIL_NON_MAPPATI
        assert not mancanti, sorted(mancanti)
        assert not DETAIL_NON_MAPPATI & set(errori.RPC_ERRORS)
        # Detail delle funzioni chiamate (lock dell'azienda, budget).
        for detail in ("owner_not_found", "company_not_found", "ai_budget_esaurito"):
            assert detail in errori.RPC_ERRORS, detail
        status, code, messaggio = errori.RPC_ERRORS["bozze_esaurite"]
        assert (status, code) == (409, "bozze_esaurite") and "bozze" in messaggio
        for detail in ("call_non_trovata", "funzione_non_inclusa", "bozza_in_corso"):
            status, code, messaggio = errori.RPC_ERRORS[detail]
            assert 400 <= status < 500 and code and messaggio
