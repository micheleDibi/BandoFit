"""Test funzionali della migration 0037 (call di partenariato).

Coprono: vincoli, macchina a stati, indici e cascade di partner_calls e delle
tabelle figlie (requisiti tipizzati con etichette stabili, posizioni, versioni
immutabili); il registro DSA partner_segnalazioni (vincoli, una aperta per
segnalante, niente FK); le RPC di creazione (piano, tetto di bozze, una call
non chiusa per azienda × bando), aggiornamento (whitelist e versioni dopo la
pubblicazione), conferma delle regole, sostituzione di requisiti (Q11 sulle
regole finanziarie, riallineamento delle posizioni) e posizioni, pubblicazione
(identità, rappresentante per la nominativa, bando, scadenza, completezza,
limiti sul pool dell'owner), chiusura manuale e automatica; i job AI della call
(prenotazione, limiti per call e per owner, budget, chiusura atomica,
failsafe); fn_partenariati_snapshot; l'equivalenza delle funzioni interne di
identità con fn_partner_consenso (0035); l'ordine dei lock con una seconda
connessione; RLS, privilegi e firme di tutto ciò che la migration crea.
Ogni test riceve un database fresco clonato dal template.
"""

import itertools
import re
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0037_call_partenariato.sql"
)
SQL_0037 = MIGRATION.read_text(encoding="utf-8")

TABELLE_NUOVE = {
    "partner_calls", "partner_call_requisiti", "partner_call_posizioni",
    "partner_call_versioni", "partner_segnalazioni",
}
FUNZIONI_NUOVE = {
    "fn_partner_call_versioni_immutabile", "fn_partenariato_identita_ok",
    "fn_partenariato_rappresentante_ok", "fn_partner_call_blocca_azienda",
    "fn_partner_call_blocca", "fn_partner_call_campi_editabili", "fn_partner_call_contenuto",
    "fn_partner_call_nuova_versione", "fn_partner_calls_attive_usate",
    "fn_partner_call_limite_attive", "fn_partner_call_regole_valide",
    "fn_partner_call_regola_ok", "fn_partner_call_crea_bozza", "fn_partner_call_aggiorna",
    "fn_partner_call_conferma_regole", "fn_partner_call_sostituisci_requisiti",
    "fn_partner_call_sostituisci_posizioni", "fn_partner_call_pubblica",
    "fn_partner_call_chiudi", "fn_partner_call_chiudi_auto",
    "fn_partner_call_ai_esecuzione_interrotta", "fn_partner_call_ai_prenota",
    "fn_partner_call_ai_concludi", "fn_partner_call_ai_chiudi_stale",
    "fn_partenariati_snapshot",
}

VERSIONE = "2026-10-bozza-1"
CF_TITOLARE = "RSSMRA80A01H501U"
_TITOLARE = object()  # sentinella: l'attore è il titolare
_DEFAULT = object()   # sentinella: valore di default del helper

REGOLA_F1 = {
    "id": "F1",
    "descrizione": "Costo della quota non oltre il 60% del fatturato medio",
    "ambito": "ciascun_partner",
    "numeratore": "costo_quota",
    "denominatore": "fatturato_medio_2",
    "operatore": "le",
    "soglia": "0.6",
    "soglia_variabile": None,
    "soglia_coefficiente": None,
    "unita": "rapporto",
}

REQ_A = {
    "testo": "Almeno un organismo di ricerca nel partenariato",
    "criterio": {"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]},
    "ambito": "consorzio",
    "cercato": True,
    "origine": "bando_partenariato",
}
REQ_B = {
    "testo": "Attività ATECO 62",
    "criterio": {"tipo": "ateco", "divisioni": ["62"]},
    "cercato": False,
    "origine": "precheck",
    "copertura_creatore": "coperto",
    "copertura_fonte": "registro",
    "copertura_nota": "Coperto dal Registro Imprese",
}
POS_1 = {
    "titolo": "Organismo di ricerca",
    "tipi_soggetto": ["organismo_ricerca"],
    "competenze": ["prototipazione_rapida"],
    "quota_ipotizzata_pct": 30,
}

_seq = itertools.count(1)
_bandi = itertools.count(5000)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def oggi(db) -> date:
    return db.execute("select (now() at time zone 'Europe/Rome')::date").fetchone()[0]


def new_user(db, plan_slug: str | None = None, *, invitato: bool = False) -> str:
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


def limite_piano(db, slug: str, valore: int | None) -> None:
    db.execute(
        "update public.subscription_plans set partner_calls_attive_max = %s where slug = %s",
        (valore, slug),
    )


def make_company(db, owner: str) -> str:
    i = next(_seq)
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, %s, %s) returning id",
        (owner, f"ACME {i} Srl", f"{i:011d}"),
    ).fetchone()[0])


def importa(db, company: str, *, piva: str | None = None, stato: str | None = "Attiva",
            sandbox: bool = False) -> None:
    """company_data come dopo un import IT-full (piva_fetched = P.IVA dell'azienda)."""
    db.execute(
        "insert into public.company_data (company_profile_id, piva_fetched, sandbox, raw, "
        "denominazione, stato_impresa) "
        "select id, coalesce(%s, partita_iva), %s, '{}'::jsonb, ragione_sociale, %s "
        "from public.company_profiles where id = %s",
        (piva, sandbox, stato, company),
    )


def legale(db, company: str, cf: str, *, rappresentante: bool = True) -> None:
    db.execute(
        "insert into public.company_people (company_profile_id, kind, nome, cognome, "
        "codice_fiscale, is_legale_rappresentante, raw) "
        "values (%s, 'manager', 'Mario', 'Rossi', %s, %s, '{}'::jsonb)",
        (company, cf, rappresentante),
    )


def imposta_cf(db, user: str, cf: str = CF_TITOLARE, *, verificato: bool = True) -> None:
    db.execute(
        "update public.profiles set codice_fiscale = %s, "
        "cf_verified_at = case when %s then now() end where id = %s",
        (cf, verificato, user),
    )


def verifica_identita_admin(db, owner: str, company: str) -> None:
    """Dalla 0041 il nominativo richiede l'identità verificata dall'admin
    (fn_partenariato_rappresentante_ok): richiesta del titolare e verifica."""
    admin = new_user(db)
    db.execute("update public.profiles set role = 'admin' where id = %s", (admin,))
    db.execute("select public.fn_identita_richiedi(%s, %s, %s, null)", (owner, company, owner))
    db.execute("select public.fn_identita_decidi(%s, %s, 'verificata', 'telefonata_sede', null)",
               (company, admin))


def azienda_pronta(db, owner: str | None = None, plan_slug: str = "smart"):
    """Titolare (Smart di default: 1 call attiva) + azienda con identità verificata."""
    owner = owner or new_user(db, plan_slug)
    company = make_company(db, owner)
    importa(db, company)
    return owner, company


def bando(db, bando_id: int | None = None, **extra) -> dict:
    bid = bando_id if bando_id is not None else next(_bandi)
    dati = {
        "id": bid, "slug": f"bando-{bid}", "titolo": f"Bando di prova {bid}",
        "scadenza": (oggi(db) + timedelta(days=90)).isoformat(),
        "programma_id": 7, "tipologia_id": 3, "stato_effettivo": "aperto",
    }
    dati.update(extra)
    return dati


def crea(db, owner, company, *, attore=_TITOLARE, bando_=None, dati=_DEFAULT,
         max_bozze=5) -> dict:
    if attore is _TITOLARE:
        attore = owner
    if dati is _DEFAULT:
        dati = {"ruolo_creatore": "capofila"}
    return db.execute(
        "select public.fn_partner_call_crea_bozza(%s::uuid, %s::uuid, %s::uuid, %s::jsonb, "
        "%s::jsonb, %s::integer)",
        (owner, company, attore, Jsonb(bando_ or bando(db)),
         None if dati is None else Jsonb(dati), max_bozze),
    ).fetchone()[0]


def aggiorna(db, owner, company, call_id, campi, *, attore=_TITOLARE) -> dict:
    if attore is _TITOLARE:
        attore = owner
    return db.execute(
        "select public.fn_partner_call_aggiorna(%s::uuid, %s::uuid, %s::uuid, %s::uuid, "
        "%s::jsonb)",
        (owner, company, attore, call_id, None if campi is None else Jsonb(campi)),
    ).fetchone()[0]


def regole(**sostituzioni) -> dict:
    snapshot = {
        "versione": 1,
        "fonte": {"estratta_at": "2026-09-01T10:00:00Z", "prompt_version": 1,
                  "modalita_effettiva": "ammesso"},
        "modalita": {"valore": "ammesso", "origine_voce": "confermata"},
        "forme_ammesse": [{"forma": "ats", "origine_voce": "confermata"}],
        "costituzione": {"valore": "da_costituire", "origine_voce": "modificata"},
        "partner_min": {"valore": 2, "origine_voce": "confermata"},
        "partner_max": None,
        "composizione": [{"id": "K1", "tipo_soggetto": "organismo_ricerca", "minimo": 1,
                          "origine_voce": "confermata"}],
        "quote": [],
        "vincoli": [{"id": "V1", "tipo": "territoriale", "origine_voce": "aggiunta"}],
        "regole_finanziarie": [dict(REGOLA_F1, origine_voce="confermata",
                                    citazione={"testo": "..."})],
        "documenti_richiesti": [],
    }
    snapshot.update(sostituzioni)
    return snapshot


def conferma(db, owner, company, call_id, snapshot=_DEFAULT, esclusivita=False, *,
             attore=_TITOLARE) -> dict:
    if attore is _TITOLARE:
        attore = owner
    if snapshot is _DEFAULT:
        snapshot = regole()
    return db.execute(
        "select public.fn_partner_call_conferma_regole(%s::uuid, %s::uuid, %s::uuid, "
        "%s::uuid, %s::jsonb, %s::boolean)",
        (owner, company, attore, call_id, None if snapshot is None else Jsonb(snapshot),
         esclusivita),
    ).fetchone()[0]


def requisiti(db, owner, company, call_id, lista, *, attore=_TITOLARE) -> list:
    if attore is _TITOLARE:
        attore = owner
    return db.execute(
        "select public.fn_partner_call_sostituisci_requisiti(%s::uuid, %s::uuid, %s::uuid, "
        "%s::uuid, %s::jsonb)",
        (owner, company, attore, call_id, None if lista is None else Jsonb(lista)),
    ).fetchone()[0]


def posizioni(db, owner, company, call_id, lista, *, attore=_TITOLARE) -> list:
    if attore is _TITOLARE:
        attore = owner
    return db.execute(
        "select public.fn_partner_call_sostituisci_posizioni(%s::uuid, %s::uuid, %s::uuid, "
        "%s::uuid, %s::jsonb)",
        (owner, company, attore, call_id, None if lista is None else Jsonb(lista)),
    ).fetchone()[0]


def pubblica(db, owner, company, call_id, *, attore=_TITOLARE, bando_stato="aperto",
             bando_scadenza=_DEFAULT, scadenza_call=_DEFAULT, non_sandbox=True) -> dict:
    if attore is _TITOLARE:
        attore = owner
    if bando_scadenza is _DEFAULT:
        bando_scadenza = oggi(db) + timedelta(days=90)
    if scadenza_call is _DEFAULT:
        scadenza_call = oggi(db) + timedelta(days=30)
    return db.execute(
        "select public.fn_partner_call_pubblica(%s::uuid, %s::uuid, %s::uuid, %s::uuid, "
        "%s::text, %s::date, %s::date, %s::boolean)",
        (owner, company, attore, call_id, bando_stato, bando_scadenza, scadenza_call,
         non_sandbox),
    ).fetchone()[0]


def chiudi(db, owner, company, call_id, esito, *, attore=_TITOLARE) -> dict:
    if attore is _TITOLARE:
        attore = owner
    return db.execute(
        "select public.fn_partner_call_chiudi(%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s::text)",
        (owner, company, attore, call_id, esito),
    ).fetchone()[0]


def chiudi_auto(db, call_id, stato, motivo) -> bool:
    return db.execute(
        "select public.fn_partner_call_chiudi_auto(%s::uuid, %s::text, %s::text)",
        (call_id, stato, motivo),
    ).fetchone()[0]


def call_pronta(db, owner, company, *, bando_=None, dati=None) -> str:
    """Bozza completa: testi, regole confermate, un requisito cercato, una posizione."""
    base = {"ruolo_creatore": "capofila", "titolo": "Cerchiamo un organismo di ricerca",
            "descrizione_pubblica": "Progetto di ricerca industriale su nuovi materiali."}
    base.update(dati or {})
    call_id = crea(db, owner, company, bando_=bando_, dati=base)["id"]
    conferma(db, owner, company, call_id)
    requisiti(db, owner, company, call_id, [REQ_A, REQ_B])
    posizioni(db, owner, company, call_id, [POS_1])
    return call_id


def pubblicata(db, owner=None, company=None, plan_slug="smart"):
    if company is None:
        owner, company = azienda_pronta(db, owner, plan_slug)
    call_id = call_pronta(db, owner, company)
    pubblica(db, owner, company, call_id)
    return owner, company, call_id


def call(db, call_id) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.partner_calls where id = %s", (call_id,)).fetchone()


def righe(db, tabella, call_id) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            f"select * from public.{tabella} where call_id = %s order by ordine",
            (call_id,)).fetchall()


def versioni(db, call_id) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.partner_call_versioni where call_id = %s order by versione",
            (call_id,)).fetchall()


def audit(db, action: str) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.audit_log where action = %s order by id", (action,)
        ).fetchall()


def conta(db, tabella: str) -> int:
    return db.execute(f"select count(*) from public.{tabella}").fetchone()[0]


def snapshot(db, owner) -> dict:
    return db.execute("select public.fn_partenariati_snapshot(%s::uuid)", (owner,)).fetchone()[0]


def prenota_ai(db, owner, company, call_id, *, servizio="partner_call_posizioni",
               richiedente=_TITOLARE, budget=1000, riserva=10, limite_call=10,
               limite_owner=None) -> str:
    if richiedente is _TITOLARE:
        richiedente = owner
    return str(db.execute(
        "select public.fn_partner_call_ai_prenota(%s::uuid, %s::uuid, %s::uuid, %s::uuid, "
        "%s::text, %s::integer, %s::integer, %s::integer, %s::integer)",
        (owner, company, call_id, richiedente, servizio, budget, riserva, limite_call,
         limite_owner),
    ).fetchone()[0])


def concludi_job(db, call_id, esecuzione_id, *, servizio="partner_call_posizioni",
                 job_stato="pronta", proposta=_DEFAULT, job_errore=None, stato="conclusa",
                 cost=5, input_tokens=100, output_tokens=50) -> dict:
    if proposta is _DEFAULT:
        proposta = {"posizioni": [{"titolo": "Proposta"}]} if job_stato == "pronta" else None
    return db.execute(
        "select public.fn_partner_call_ai_concludi(%s::uuid, %s::uuid, %s::text, %s::text, "
        "%s::jsonb, %s::text, %s::text, %s::integer, %s::integer, %s::integer, "
        "'claude-sonnet-5', null)",
        (call_id, esecuzione_id, servizio, job_stato,
         None if proposta is None else Jsonb(proposta), job_errore, stato, cost, input_tokens,
         output_tokens),
    ).fetchone()[0]


def concludi_ai(db, esecuzione_id, stato, cost=None, input_tokens=0, output_tokens=0) -> None:
    db.execute(
        "select public.fn_partenariati_ai_concludi(%s::uuid, %s::text, %s::integer, "
        "%s::integer, %s::integer, null::text, null::text)",
        (esecuzione_id, stato, cost, input_tokens, output_tokens),
    )


def chiudi_stale(db, minuti=10) -> int:
    return db.execute(
        "select public.fn_partner_call_ai_chiudi_stale(%s::integer)", (minuti,)).fetchone()[0]


def esecuzione(db, esecuzione_id) -> dict:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.partenariati_ai_esecuzioni where id = %s", (esecuzione_id,)
        ).fetchone()


def consumi(db) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.api_usage_events where service like 'partner_call_%%' "
            "order by id").fetchall()


def ai_check(db, owner, company, bando_id) -> str:
    return str(db.execute(
        "insert into public.ai_checks (company_profile_id, user_id, family_parent_id, "
        "bando_id, bando_slug, bando_titolo, status) "
        "values (%s, %s, %s, %s, 'slug', 'Titolo', 'ready') returning id",
        (company, owner, owner, bando_id),
    ).fetchone()[0])


def in_attesa(monitor, pid, thread, esito, scadenza_s=10) -> None:
    """Attende che il backend `pid` sia bloccato da un lock."""
    scadenza = time.monotonic() + scadenza_s
    while not monitor.execute(
        "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
    ).fetchone()[0]:
        assert time.monotonic() < scadenza, "la seconda transazione non attende"
        assert thread.is_alive(), f"la seconda transazione non ha atteso: {esito}"
        time.sleep(0.02)


def in_thread(funzione) -> tuple[threading.Thread, dict]:
    esito: dict = {}

    def corpo():
        try:
            esito["out"] = funzione()
        except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
            esito["errore"] = exc

    return threading.Thread(target=corpo), esito


# ------------------------------------------------------------ partner_calls


class TestTabellaCall:
    def test_default_e_snapshot_del_bando(self, db):
        owner, company = azienda_pronta(db)
        b = bando(db, 4242)
        c = crea(db, owner, company, bando_=b)
        riga = call(db, c["id"])
        assert riga["stato"] == "bozza" and riga["anonima"] is True
        assert riga["visibilita"] == "pubblica" and riga["wizard_passo"] == 1
        assert riga["versione"] == 0 and riga["esclusivita"] is False
        assert riga["regole_partenariato"] is None and riga["pubblicata_at"] is None
        assert riga["budget_progetto_eur"] is None and riga["ai_posizioni_stato"] is None
        assert str(riga["creato_da"]) == owner and str(riga["family_parent_id"]) == owner
        assert str(riga["company_profile_id"]) == company
        assert (riga["bando_id"], riga["bando_slug"], riga["bando_titolo"]) == (
            4242, "bando-4242", "Bando di prova 4242")
        assert riga["bando_scadenza"] == oggi(db) + timedelta(days=90)
        assert (riga["bando_programma_id"], riga["bando_tipologia_id"]) == (7, 3)
        assert riga["bando_stato_effettivo"] == "aperto"
        assert riga["bando_verificato_at"] is not None and riga["bando_mancante_dal"] is None

    @pytest.mark.parametrize(("colonna", "valore", "vincolo"), [
        ("titolo", "Corto", "pcall_titolo_check"),
        ("titolo", "x" * 141, "pcall_titolo_check"),
        ("descrizione_pubblica", "x" * 3001, "pcall_descrizione_pubblica_check"),
        ("dettagli_riservati", "x" * 5001, "pcall_dettagli_riservati_check"),
        ("profilo_partner_ideale", "x" * 2001, "pcall_profilo_partner_ideale_check"),
        ("budget_fascia", "1m_5m", "pcall_budget_fascia_check"),
        ("budget_progetto_eur", 0, "pcall_budget_progetto_eur_check"),
        ("budget_progetto_eur", -1, "pcall_budget_progetto_eur_check"),
        ("quota_creatore_pct", 0, "pcall_quota_creatore_pct_check"),
        ("quota_creatore_pct", 100.01, "pcall_quota_creatore_pct_check"),
        ("wizard_passo", 0, "pcall_wizard_passo_check"),
        ("wizard_passo", 8, "pcall_wizard_passo_check"),
        ("visibilita", "privata", "pcall_visibilita_check"),
        ("motivo_chiusura", "altro", "pcall_motivo_chiusura_check"),
        ("override_non_ammesso_motivo", "troppo corto", "pcall_override_non_ammesso_motivo_check"),
        ("override_non_ammesso_motivo", "x" * 1001, "pcall_override_non_ammesso_motivo_check"),
        ("sospeso_motivo", "x" * 501, "pcall_sospeso_motivo_check"),
        ("ruolo_creatore", "partner", "pcall_ruolo_creatore_check"),
        ("forma_aggregazione_prevista", "joint_venture", "pcall_forma_aggregazione_check"),
        ("ai_posizioni_stato", "boh", "pcall_ai_posizioni_stato_check"),
        ("ai_testi_stato", "boh", "pcall_ai_testi_stato_check"),
        ("stato_prima_sospensione", "scaduta", "pcall_stato_prima_sospensione_check"),
        ("partenariato_ref", Jsonb([]), "pcall_partenariato_ref_check"),
        ("regole_partenariato", Jsonb("x"), "pcall_regole_partenariato_check"),
        ("ai_posizioni_proposta", Jsonb([1]), "pcall_ai_posizioni_proposta_check"),
        ("ai_testi_proposta", Jsonb(1), "pcall_ai_testi_proposta_check"),
        ("versione", -1, "pcall_versione_check"),
        ("bando_slug", "", "pcall_bando_slug_check"),
        ("bando_titolo", "", "pcall_bando_titolo_check"),
    ])
    def test_vincoli(self, db, colonna, valore, vincolo):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(f"update public.partner_calls set {colonna} = %s where id = %s",
                       (valore, c["id"]))
        assert exc.value.diag.constraint_name == vincolo

    def test_stato_non_valido(self, db):
        """Uno stato ignoto viola sia il dominio sia la macchina a stati."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_calls set stato = 'archiviata' where id = %s",
                       (c["id"],))
        assert exc.value.diag.constraint_name in ("pcall_stato_check",
                                                  "pcall_pubblicata_coerente")

    def test_valori_al_limite_ammessi(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        for colonna, valore in [
            ("titolo", "x" * 10), ("titolo", "x" * 140), ("descrizione_pubblica", "x" * 3000),
            ("dettagli_riservati", "x" * 5000), ("profilo_partner_ideale", "x" * 2000),
            ("budget_progetto_eur", 0.01), ("budget_progetto_eur", 999999999999.99),
            ("quota_creatore_pct", 100), ("quota_creatore_pct", 0.01), ("wizard_passo", 7),
            ("override_non_ammesso_motivo", "x" * 20), ("sospeso_motivo", "x" * 500),
            ("forma_aggregazione_prevista", "altra"), ("ruolo_creatore", "cerco_capofila"),
        ]:
            db.execute(f"update public.partner_calls set {colonna} = %s where id = %s",
                       (valore, c["id"]))

    @pytest.mark.parametrize("fascia", ["fino_50k", "50k_150k", "150k_300k", "300k_500k",
                                        "500k_1m", "1m_2m", "2m_5m", "oltre_5m"])
    def test_le_otto_fasce_di_budget(self, db, fascia):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        assert aggiorna(db, owner, company, c["id"], {"budget_fascia": fascia})[
            "budget_fascia"] == fascia

    def test_completezza_della_pubblicata(self, db):
        """Una call pubblicata (anche se poi annullata) deve essere completa."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        chiudi(db, owner, company, c["id"], "annullata")
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_calls set pubblicata_at = now() where id = %s",
                       (c["id"],))
        assert exc.value.diag.constraint_name == "pcall_pubblicata_completa"

    def test_bozza_vuota_annullabile(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        assert call(db, c["id"])["titolo"] is None
        out = chiudi(db, owner, company, c["id"], "annullata")
        assert out["stato"] == "chiusa_annullata" and out["chiusa_at"] is not None

    @pytest.mark.parametrize(("assegnazione", "vincolo"), [
        ("stato = 'pubblicata'", "pcall_pubblicata_coerente"),
        ("stato = 'scaduta', chiusa_at = now(), motivo_chiusura = 'scadenza_call'",
         "pcall_pubblicata_coerente"),
        ("stato = 'chiusa_completata', chiusa_at = now(), motivo_chiusura = 'creatore_completata'",
         "pcall_pubblicata_coerente"),
        ("stato = 'chiusa_annullata'", "pcall_chiusa_coerente"),
        ("stato = 'chiusa_annullata', chiusa_at = now()", "pcall_chiusa_coerente"),
        ("chiusa_at = now(), motivo_chiusura = 'scadenza_call'", "pcall_chiusa_coerente"),
        ("stato = 'sospesa_moderazione'", "pcall_sospesa_coerente"),
        ("stato = 'sospesa_moderazione', sospesa_at = now()", "pcall_sospesa_coerente"),
        ("regole_confermate_at = now()", "pcall_regole_confermate_coerente"),
        ("ai_posizioni_stato = 'in_corso', ai_posizioni_avviata_at = now()",
         "pcall_ai_posizioni_in_corso_coerente"),
        ("ai_testi_stato = 'in_corso', ai_testi_esecuzione_id = gen_random_uuid()",
         "pcall_ai_testi_in_corso_coerente"),
    ])
    def test_macchina_a_stati(self, db, assegnazione, vincolo):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(f"update public.partner_calls set {assegnazione} where id = %s",
                       (c["id"],))
        assert exc.value.diag.constraint_name == vincolo

    def test_bozza_mai_pubblicata(self, db):
        """Una bozza completa con pubblicata_at viola solo la macchina a stati."""
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        aggiorna(db, owner, company, c,
                 {"scadenza_call": (oggi(db) + timedelta(days=10)).isoformat()})
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_calls set pubblicata_at = now() where id = %s",
                       (c,))
        assert exc.value.diag.constraint_name == "pcall_pubblicata_coerente"

    def test_stati_coerenti_ammessi(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                   "sospesa_at = now(), stato_prima_sospensione = 'bozza' where id = %s",
                   (c["id"],))
        db.execute("update public.partner_calls set ai_posizioni_stato = 'in_corso', "
                   "ai_posizioni_avviata_at = now(), ai_posizioni_esecuzione_id = "
                   "gen_random_uuid() where id = %s", (c["id"],))

    def test_azienda_inesistente(self, db):
        owner = new_user(db, "smart")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute(
                "insert into public.partner_calls (company_profile_id, family_parent_id, "
                "creato_da, bando_id, bando_slug, bando_titolo, ruolo_creatore) "
                "values (gen_random_uuid(), %s, %s, 1, 's', 't', 'capofila')", (owner, owner))

    def test_trigger_updated_at(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        db.execute("update public.partner_calls set updated_at = now() - interval '1 day' "
                   "where id = %s", (c["id"],))
        prima = call(db, c["id"])["updated_at"]
        aggiorna(db, owner, company, c["id"], {"wizard_passo": 2})
        assert call(db, c["id"])["updated_at"] > prima

    def test_indici(self, db):
        indici = {r[0]: r[1] for r in db.execute(
            "select indexname, indexdef from pg_indexes where schemaname = 'public' "
            "and tablename in ('partner_calls', 'partner_call_requisiti', "
            "'partner_call_posizioni', 'partner_call_versioni', 'partner_segnalazioni')"
        ).fetchall()}
        for nome in ("partner_calls_una_attiva", "partner_calls_stato_scadenza_idx",
                     "partner_calls_bando_pubblicate_idx", "partner_calls_family_idx",
                     "partner_calls_company_idx", "partner_calls_ai_check_idx",
                     "partner_calls_ai_posizioni_in_corso_idx",
                     "partner_calls_ai_testi_in_corso_idx", "pcr_call_idx", "pcp_call_idx",
                     "pcr_etichetta_unica", "pcv_call_versione_key", "ps_una_aperta",
                     "ps_coda_idx", "ps_oggetto_idx"):
            assert nome in indici, nome
        una = indici["partner_calls_una_attiva"]
        assert "UNIQUE" in una and "(company_profile_id, bando_id)" in una
        for stato in ("bozza", "pubblicata", "sospesa_moderazione"):
            assert stato in una
        assert "chiusa" not in una and "scaduta" not in una
        assert "(stato, scadenza_call)" in indici["partner_calls_stato_scadenza_idx"]
        assert "pubblicata" in indici["partner_calls_bando_pubblicate_idx"]
        assert "created_at DESC" in indici["partner_calls_family_idx"]
        aperta = indici["ps_una_aperta"]
        assert "UNIQUE" in aperta and "ricevuta" in aperta and "in_esame" in aperta

    def test_ai_check_cancellato_set_null(self, db):
        owner, company = azienda_pronta(db)
        b = bando(db)
        check = ai_check(db, owner, company, b["id"])
        c = crea(db, owner, company, bando_=b,
                 dati={"ruolo_creatore": "capofila", "ai_check_id": check})
        assert str(call(db, c["id"])["ai_check_id"]) == check
        db.execute("delete from public.ai_checks where id = %s", (check,))
        assert call(db, c["id"])["ai_check_id"] is None


# ---------------------------------------------------------- tabelle figlie


class TestRequisitiTabella:
    def _inserisci(self, db, call_id, **colonne):
        base = {"call_id": call_id, "origine": "manuale", "etichetta": "A",
                "testo": "Testo del requisito"}
        base.update(colonne)
        db.execute(
            f"insert into public.partner_call_requisiti ({', '.join(base)}) "
            f"values ({', '.join(['%s'] * len(base))})",
            [Jsonb(v) if isinstance(v, (dict, list)) else v for v in base.values()],
        )

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"etichetta": ""}, "pcr_etichetta_check"),
        ({"etichetta": "x" * 61}, "pcr_etichetta_check"),
        ({"testo": "ab"}, "pcr_testo_check"),
        ({"testo": "x" * 501}, "pcr_testo_check"),
        ({"origine": "bando_requirements"}, "pcr_origine_check"),
        ({"ambito": "tutti"}, "pcr_ambito_check"),
        ({"criterio": ["tag"]}, "pcr_criterio_check"),
        ({"criterio": {"tipo": "boh"}}, "pcr_criterio_check"),
        ({"criterio": {"valori": ["micro"]}}, "pcr_criterio_check"),
        ({"copertura_creatore": "parziale"}, "pcr_copertura_creatore_check"),
        ({"copertura_fonte": "precheck"}, "pcr_copertura_fonte_check"),
        ({"copertura_nota": "x" * 301}, "pcr_copertura_nota_check"),
        ({"rif_origine": "x" * 41}, "pcr_rif_origine_check"),
        ({"citazione": ["x"]}, "pcr_citazione_check"),
    ])
    def test_vincoli(self, db, colonne, vincolo):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._inserisci(db, c["id"], **colonne)
        assert exc.value.diag.constraint_name == vincolo

    @pytest.mark.parametrize("tipo", ["tipo_soggetto", "tag", "regione", "paese", "ateco",
                                      "settore", "dimensione", "certificazione", "esperienza",
                                      "regola_finanziaria", "manuale"])
    def test_tipi_di_criterio(self, db, tipo):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        self._inserisci(db, c["id"], criterio={"tipo": tipo})

    def test_default_ed_etichetta_unica_per_call(self, db):
        owner, company = azienda_pronta(db)
        c1 = crea(db, owner, company)
        c2 = crea(db, owner, company)
        self._inserisci(db, c1["id"])
        self._inserisci(db, c2["id"])  # stessa etichetta su un'altra call: ammessa
        r = righe(db, "partner_call_requisiti", c1["id"])[0]
        assert (r["ambito"], r["cercato"], r["ordine"]) == ("consorzio", False, 0)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            self._inserisci(db, c1["id"])
        assert exc.value.diag.constraint_name == "pcr_etichetta_unica"


class TestPosizioniTabella:
    def _inserisci(self, db, call_id, **colonne):
        base = {"call_id": call_id, "titolo": "Partner tecnologico"}
        base.update(colonne)
        db.execute(
            f"insert into public.partner_call_posizioni ({', '.join(base)}) "
            f"values ({', '.join(['%s'] * len(base))})",
            list(base.values()),
        )

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"titolo": "ab"}, "pcp_titolo_check"),
        ({"titolo": "x" * 121}, "pcp_titolo_check"),
        ({"ruolo": "socio"}, "pcp_ruolo_check"),
        ({"tipi_soggetto": ["a"] * 6}, "pcp_tipi_soggetto_check"),
        ({"tipi_soggetto": ["a", None]}, "pcp_tipi_soggetto_check"),
        ({"competenze": ["c"] * 11}, "pcp_competenze_check"),
        ({"competenze": [None]}, "pcp_competenze_check"),
        ({"ateco_divisioni": ["6"]}, "pcp_ateco_divisioni_check"),
        ({"ateco_divisioni": ["620"]}, "pcp_ateco_divisioni_check"),
        ({"ateco_divisioni": ["62", None]}, "pcp_ateco_divisioni_check"),
        ({"ateco_divisioni": ["62"] * 11}, "pcp_ateco_divisioni_check"),
        ({"regioni": list(range(1, 23))}, "pcp_regioni_check"),
        ({"regioni": [1, None]}, "pcp_regioni_check"),
        ({"territorio_modalita": "ovunque"}, "pcp_territorio_modalita_check"),
        ({"paesi": ["it"]}, "pcp_paesi_check"),
        ({"paesi": ["ITA"]}, "pcp_paesi_check"),
        ({"paesi": ["IT", None]}, "pcp_paesi_check"),
        ({"paesi": ["IT"] * 31}, "pcp_paesi_check"),
        ({"dimensioni": ["enorme"]}, "pcp_dimensioni_check"),
        ({"quota_ipotizzata_pct": 0}, "pcp_quota_ipotizzata_pct_check"),
        ({"quota_ipotizzata_pct": 100.5}, "pcp_quota_ipotizzata_pct_check"),
        ({"numero": 0}, "pcp_numero_check"),
        ({"numero": 11}, "pcp_numero_check"),
        ({"requisiti_ids": [uuid.uuid4() for _ in range(21)]}, "pcp_requisiti_ids_check"),
        ({"requisiti_ids": [uuid.uuid4(), None]}, "pcp_requisiti_ids_check"),
        ({"note": "x" * 501}, "pcp_note_check"),
    ])
    def test_vincoli(self, db, colonne, vincolo):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._inserisci(db, c["id"], **colonne)
        assert exc.value.diag.constraint_name == vincolo

    def test_default_e_valori_al_limite(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        self._inserisci(db, c["id"], tipi_soggetto=["a"] * 5, competenze=["c"] * 10,
                        ateco_divisioni=["62"] * 10, regioni=list(range(1, 22)),
                        paesi=["IT"] * 30, dimensioni=["micro", "piccola", "media", "grande"],
                        quota_ipotizzata_pct=100, numero=10, note="x" * 500)
        self._inserisci(db, c["id"], ordine=1)
        r = righe(db, "partner_call_posizioni", c["id"])[-1]
        assert (r["ruolo"], r["territorio_modalita"], r["numero"]) == (
            "partner", "qualsiasi", 1)
        assert r["tipi_soggetto"] == [] and r["requisiti_ids"] == [] and r["regioni"] == []


class TestVersioniTabella:
    def test_immutabili_ma_cancellabili(self, db):
        owner, company, c = pubblicata(db)
        (v,) = versioni(db, c)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("update public.partner_call_versioni set snapshot = '{}'::jsonb "
                       "where id = %s", (v["id"],))
        assert detail_of(exc) == "versione_immutabile"
        db.execute("delete from public.partner_call_versioni where id = %s", (v["id"],))
        assert versioni(db, c) == []

    @pytest.mark.parametrize(("versione", "snapshot_", "vincolo"), [
        (0, {}, "pcv_versione_check"),
        (2, [], "pcv_snapshot_check"),
    ])
    def test_vincoli(self, db, versione, snapshot_, vincolo):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("insert into public.partner_call_versioni (call_id, versione, snapshot, "
                       "modificato_da) values (%s, %s, %s, %s)",
                       (c, versione, Jsonb(snapshot_), owner))
        assert exc.value.diag.constraint_name == vincolo

    def test_versione_unica_per_call(self, db):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("insert into public.partner_call_versioni (call_id, versione, snapshot, "
                       "modificato_da) values (%s, 1, '{}'::jsonb, %s)", (c, owner))


class TestSegnalazioni:
    def _segnala(self, db, **colonne):
        base = {"oggetto_tipo": "call", "oggetto_id": str(uuid.uuid4()),
                "segnalante_user_id": str(uuid.uuid4()), "motivo": "contatti_nel_testo",
                "descrizione": "Il testo contiene un numero di telefono", "buona_fede": True,
                "contenuto_snapshot": {"titolo": "Call"}}
        base.update(colonne)
        return str(db.execute(
            f"insert into public.partner_segnalazioni ({', '.join(base)}) "
            f"values ({', '.join(['%s'] * len(base))}) returning id",
            [Jsonb(v) if isinstance(v, (dict, list)) else v for v in base.values()],
        ).fetchone()[0])

    def test_default_e_nessuna_fk(self, db):
        """Nessuna FK: oggetto e segnalante possono non esistere (registro DSA)."""
        sid = self._segnala(db, oggetto_tipo="profilo", segnalante_company_id=str(uuid.uuid4()))
        with db.cursor(row_factory=dict_row) as cur:
            r = cur.execute("select * from public.partner_segnalazioni where id = %s",
                            (sid,)).fetchone()
        assert r["stato"] == "ricevuta" and r["created_at"] is not None
        assert db.execute(
            "select count(*) from pg_constraint where conrelid = "
            "'public.partner_segnalazioni'::regclass and contype = 'f'").fetchone()[0] == 0

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"oggetto_tipo": "conversazione"}, "ps_oggetto_tipo_check"),  # «messaggio» dalla 0039
        ({"oggetto_id": ""}, "ps_oggetto_id_check"),
        ({"oggetto_id": "x" * 101}, "ps_oggetto_id_check"),
        ({"motivo": "offensivo"}, "ps_motivo_check"),
        ({"descrizione": "corta"}, "ps_descrizione_check"),
        ({"descrizione": "x" * 2001}, "ps_descrizione_check"),
        ({"buona_fede": False}, "ps_buona_fede_check"),
        ({"contenuto_snapshot": ["x"]}, "ps_contenuto_snapshot_check"),
        ({"stato": "accolta"}, "ps_stato_check"),
    ])
    def test_vincoli(self, db, colonne, vincolo):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._segnala(db, **colonne)
        assert exc.value.diag.constraint_name == vincolo

    @pytest.mark.parametrize("colonna", ["segnalante_user_id", "buona_fede",
                                         "contenuto_snapshot", "oggetto_id"])
    def test_obbligatori(self, db, colonna):
        with pytest.raises(psycopg.errors.NotNullViolation):
            self._segnala(db, **{colonna: None})

    @pytest.mark.parametrize("motivo", ["contenuto_illecito", "dati_personali",
                                        "spam_pubblicita", "contatti_nel_testo",
                                        "discriminatorio", "impersonificazione", "altro"])
    def test_motivi(self, db, motivo):
        self._segnala(db, motivo=motivo)

    def test_una_aperta_per_segnalante_e_oggetto(self, db):
        oggetto, utente = str(uuid.uuid4()), str(uuid.uuid4())
        sid = self._segnala(db, oggetto_id=oggetto, segnalante_user_id=utente)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            self._segnala(db, oggetto_id=oggetto, segnalante_user_id=utente)
        assert exc.value.diag.constraint_name == "ps_una_aperta"
        # Altro segnalante, altro oggetto o altro tipo: ammessi.
        self._segnala(db, oggetto_id=oggetto)
        self._segnala(db, segnalante_user_id=utente)
        self._segnala(db, oggetto_id=oggetto, segnalante_user_id=utente, oggetto_tipo="profilo")
        db.execute("update public.partner_segnalazioni set stato = 'in_esame' where id = %s",
                   (sid,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            self._segnala(db, oggetto_id=oggetto, segnalante_user_id=utente)
        # Dalla 0041 una segnalazione decisa ha decisione e motivazione.
        db.execute("update public.partner_segnalazioni set stato = 'decisa', "
                   "decisione = 'nessuna_azione', motivazione = 'Nessuna violazione riscontrata', "
                   "deciso_da = %s, deciso_at = now() where id = %s", (utente, sid))
        self._segnala(db, oggetto_id=oggetto, segnalante_user_id=utente)

    def test_trigger_updated_at(self, db):
        sid = self._segnala(db)
        db.execute("update public.partner_segnalazioni set updated_at = now() - "
                   "interval '1 day' where id = %s", (sid,))
        db.execute("update public.partner_segnalazioni set stato = 'in_esame' where id = %s",
                   (sid,))
        assert db.execute("select updated_at > now() - interval '1 minute' from "
                          "public.partner_segnalazioni where id = %s", (sid,)).fetchone()[0]


# ------------------------------------------------------------- crea bozza


class TestCreaBozza:
    def test_crea(self, db):
        owner, company = azienda_pronta(db)
        dati = {"ruolo_creatore": "cerco_capofila", "forma_aggregazione_prevista": "ats",
                "override_non_ammesso_motivo": "Il bando lo consente in un allegato tecnico",
                "partenariato_ref": {"prompt_version": 1, "modalita_effettiva": "non_ammesso"},
                "budget_progetto_eur": 1_100_000, "wizard_passo": 2}
        out = crea(db, owner, company, dati=dati)
        riga = call(db, out["id"])
        assert out["stato"] == "bozza" and riga["ruolo_creatore"] == "cerco_capofila"
        assert riga["forma_aggregazione_prevista"] == "ats" and riga["wizard_passo"] == 2
        assert riga["partenariato_ref"]["modalita_effettiva"] == "non_ammesso"
        assert riga["budget_progetto_eur"] == 1_100_000
        (a,) = audit(db, "partenariato.call_creata")
        assert str(a["actor_id"]) == owner and str(a["family_parent_id"]) == owner
        assert a["payload"] == {"call_id": out["id"], "company_profile_id": company,
                                "bando_id": riga["bando_id"]}

    def test_senza_campi_facoltativi_del_bando(self, db):
        owner, company = azienda_pronta(db)
        out = crea(db, owner, company, bando_={"id": 9, "slug": " s ", "titolo": " T "})
        riga = call(db, out["id"])
        assert (riga["bando_slug"], riga["bando_titolo"]) == ("s", "T")
        assert riga["bando_scadenza"] is None and riga["bando_programma_id"] is None

    @pytest.mark.parametrize("piano", ["gratuito", "senza_abbonamento", "piano_nuovo"])
    def test_piano_non_include_call(self, db, piano):
        if piano == "gratuito":
            owner = new_user(db)
        elif piano == "senza_abbonamento":
            owner = new_user(db, invitato=True)
        else:
            owner = new_user(db, "smart")
            limite_piano(db, "smart", 0)
        company = make_company(db, owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company)
        assert detail_of(exc) == "piano_non_include_call"
        assert conta(db, "partner_calls") == 0

    def test_limite_illimitato(self, db):
        limite_piano(db, "advisor", None)
        owner, company = azienda_pronta(db, plan_slug="advisor")
        assert crea(db, owner, company)["stato"] == "bozza"

    def test_sesta_bozza(self, db):
        owner, company = azienda_pronta(db)
        for _ in range(5):
            crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company)
        assert detail_of(exc) == "troppe_bozze"
        # Il tetto è per azienda e conta solo le bozze.
        _, altra = azienda_pronta(db, owner)
        crea(db, owner, altra)
        c = call(db, db.execute("select id from public.partner_calls where "
                                "company_profile_id = %s limit 1", (company,)).fetchone()[0])
        chiudi(db, owner, company, c["id"], "annullata")
        crea(db, owner, company)

    def test_pubblicate_non_contano_come_bozze(self, db):
        limite_piano(db, "pro", None)
        owner, company = azienda_pronta(db, plan_slug="pro")
        call_id = call_pronta(db, owner, company)
        pubblica(db, owner, company, call_id)
        for _ in range(4):
            crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException):
            crea(db, owner, company, max_bozze=4)
        crea(db, owner, company)  # 5a bozza: la pubblicata non conta

    def test_tetto_nullo_nessun_limite(self, db):
        owner, company = azienda_pronta(db)
        for _ in range(7):
            crea(db, owner, company, max_bozze=None)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company, max_bozze=0)
        assert detail_of(exc) == "troppe_bozze"

    def test_una_call_non_chiusa_per_bando(self, db):
        owner, company = azienda_pronta(db)
        b = bando(db)
        prima = crea(db, owner, company, bando_=b)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company, bando_=b)
        assert detail_of(exc) == "call_gia_presente"
        # Un'altra azienda dello stesso owner sullo stesso bando: ammessa.
        _, altra = azienda_pronta(db, owner)
        crea(db, owner, altra, bando_=b)
        # Dopo la chiusura la coppia torna libera.
        chiudi(db, owner, company, prima["id"], "annullata")
        assert crea(db, owner, company, bando_=b)["stato"] == "bozza"

    @pytest.mark.parametrize("stato", ["pubblicata", "sospesa_moderazione"])
    def test_pubblicata_o_sospesa_bloccano_il_bando(self, db, stato):
        owner, company, c = pubblicata(db)
        if stato == "sospesa_moderazione":
            db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                       "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' "
                       "where id = %s", (c,))
        riga = call(db, c)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company, bando_=bando(db, riga["bando_id"]))
        assert detail_of(exc) == "call_gia_presente"

    @pytest.mark.parametrize("stato", ["scaduta", "chiusa_completata"])
    def test_chiusa_o_scaduta_liberano_il_bando(self, db, stato):
        owner, company, c = pubblicata(db)
        if stato == "scaduta":
            assert chiudi_auto(db, c, "scaduta", "scadenza_call")
        else:
            chiudi(db, owner, company, c, "completata")
        crea(db, owner, company, bando_=bando(db, call(db, c)["bando_id"]))

    @pytest.mark.parametrize("caso", ["altro_owner", "soft_deleted", "archiviata", "inesistente"])
    def test_company_not_found(self, db, caso):
        owner, company = azienda_pronta(db)
        if caso == "altro_owner":
            owner = new_user(db, "smart")
        elif caso == "soft_deleted":
            db.execute("update public.company_profiles set deleted_at = now() where id = %s",
                       (company,))
        elif caso == "archiviata":
            db.execute("update public.company_profiles set archived_at = now() where id = %s",
                       (company,))
        else:
            company = str(uuid.uuid4())
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company)
        assert detail_of(exc) == "company_not_found"

    def test_owner_not_found(self, db):
        owner, company = azienda_pronta(db)
        fantasma = str(uuid.uuid4())
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, fantasma, company)
        assert detail_of(exc) == "owner_not_found"

    @pytest.mark.parametrize("attore", ["membro", None])
    def test_attore_non_titolare(self, db, attore):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company, attore=new_user(db) if attore else None)
        assert detail_of(exc) == "attore_non_titolare"

    @pytest.mark.parametrize("b", [
        None, [], {"slug": "s", "titolo": "t"}, {"id": "12a", "slug": "s", "titolo": "t"},
        {"id": 1.5, "slug": "s", "titolo": "t"}, {"id": 1, "titolo": "t"},
        {"id": 1, "slug": " ", "titolo": "t"}, {"id": 1, "slug": "s", "titolo": ""},
        {"id": 1, "slug": 3, "titolo": "t"}, {"id": 1, "slug": "s", "titolo": "t", "extra": 1},
        {"id": 1, "slug": "s", "titolo": "t", "scadenza": "domani"},
        {"id": 1, "slug": "s", "titolo": "t", "programma_id": "x"},
        {"id": 1234567890, "slug": "s", "titolo": "t"},
    ])
    def test_bando_non_valido(self, db, b):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "select public.fn_partner_call_crea_bozza(%s::uuid, %s::uuid, %s::uuid, "
                "%s::jsonb, '{\"ruolo_creatore\": \"capofila\"}'::jsonb, 5)",
                (owner, company, owner, None if b is None else Jsonb(b)))
        assert detail_of(exc) == "parametri_non_validi"

    @pytest.mark.parametrize("chiave", ["stato", "regole_partenariato", "company_profile_id",
                                        "family_parent_id", "versione", "pubblicata_at",
                                        "ai_posizioni_stato", "esclusivita", "boh"])
    def test_campo_non_modificabile(self, db, chiave):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company, dati={"ruolo_creatore": "capofila", chiave: None})
        assert detail_of(exc) == "campo_non_modificabile"

    @pytest.mark.parametrize("dati", [
        None, {}, {"ruolo_creatore": None}, {"ruolo_creatore": "partner"},
        {"ruolo_creatore": "capofila", "titolo": "Corto"},
        {"ruolo_creatore": "capofila", "scadenza_call": "domani"},
        {"ruolo_creatore": "capofila", "partenariato_ref": [1]},
        {"ruolo_creatore": "capofila", "ai_check_id": str(uuid.uuid4())},
    ])
    def test_dati_non_validi(self, db, dati):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company, dati=dati)
        assert detail_of(exc) == "dati_non_validi"

    def test_dati_non_oggetto(self, db):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company, dati=["capofila"])
        assert detail_of(exc) == "parametri_non_validi"

    def test_ai_check_di_un_altra_azienda_o_bando(self, db):
        owner, company = azienda_pronta(db)
        _, altra = azienda_pronta(db, owner)
        b = bando(db)
        for check in (ai_check(db, owner, altra, b["id"]),
                      ai_check(db, owner, company, b["id"] + 1)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                crea(db, owner, company, bando_=b,
                     dati={"ruolo_creatore": "capofila", "ai_check_id": check})
            assert detail_of(exc) == "dati_non_validi"

    def test_nominativa_ammessa_in_bozza(self, db):
        """Il divieto del nominativo è nel backend (NOMINATIVO_DISPONIBILE): la RPC
        conserva il dato e alla pubblicazione chiede il legale rappresentante."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company, dati={"ruolo_creatore": "capofila", "anonima": False})
        assert call(db, c["id"])["anonima"] is False


# ---------------------------------------------------------------- aggiorna


class TestAggiorna:
    def test_bozza_tutti_i_campi_editabili(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        campi = {"titolo": "Cerchiamo partner per la ricerca",
                 "descrizione_pubblica": "Descrizione", "dettagli_riservati": "Riservato",
                 "profilo_partner_ideale": "Ideale", "scadenza_call": "2020-01-01",
                 "visibilita": "solo_invitati", "budget_fascia": "1m_2m",
                 "budget_progetto_eur": 1_150_000.5, "quota_creatore_pct": 70,
                 "ruolo_creatore": "cerco_capofila", "forma_aggregazione_prevista": "consorzio",
                 "anonima": False, "override_non_ammesso_motivo": "x" * 25,
                 "partenariato_ref": {"a": 1}, "wizard_passo": 5}
        out = aggiorna(db, owner, company, c["id"], campi)
        riga = call(db, c["id"])
        assert riga["scadenza_call"] == date(2020, 1, 1)  # in bozza si valida alla pubblicazione
        assert float(riga["budget_progetto_eur"]) == 1_150_000.5
        assert riga["visibilita"] == "solo_invitati" and riga["anonima"] is False
        assert out["versione"] == 0 and versioni(db, c["id"]) == []
        assert audit(db, "partenariato.call_modificata") == []
        # JSON null svuota.
        aggiorna(db, owner, company, c["id"], {"titolo": None, "budget_fascia": None})
        assert call(db, c["id"])["titolo"] is None and call(db, c["id"])["budget_fascia"] is None

    def test_nessun_cambio_nessuna_scrittura(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        db.execute("update public.partner_calls set updated_at = now() - interval '1 day' "
                   "where id = %s", (c["id"],))
        prima = call(db, c["id"])["updated_at"]
        aggiorna(db, owner, company, c["id"], {})
        aggiorna(db, owner, company, c["id"], {"ruolo_creatore": "capofila", "wizard_passo": 1})
        assert call(db, c["id"])["updated_at"] == prima

    @pytest.mark.parametrize("chiave", ["stato", "regole_partenariato", "regole_confermate_at",
                                        "esclusivita", "company_profile_id", "creato_da",
                                        "bando_id", "bando_scadenza", "versione", "id",
                                        "ai_testi_proposta", "sospesa_at", "boh"])
    def test_campo_mai_modificabile(self, db, chiave):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c["id"], {chiave: None})
        assert detail_of(exc) == "campo_non_modificabile"

    def test_pubblicata_whitelist_crea_una_versione(self, db):
        owner, company, c = pubblicata(db)
        nuova = oggi(db) + timedelta(days=45)
        out = aggiorna(db, owner, company, c, {
            "descrizione_pubblica": "Nuova descrizione", "dettagli_riservati": "Nuovi",
            "profilo_partner_ideale": "Nuovo", "scadenza_call": nuova.isoformat(),
            "visibilita": "solo_invitati", "budget_fascia": "oltre_5m",
            "budget_progetto_eur": 6_000_000, "quota_creatore_pct": 55})
        assert out["versione"] == 2
        v = versioni(db, c)
        assert [x["versione"] for x in v] == [1, 2]
        assert v[1]["snapshot"]["call"]["descrizione_pubblica"] == "Nuova descrizione"
        assert v[0]["snapshot"]["call"]["descrizione_pubblica"] != "Nuova descrizione"
        assert str(v[1]["modificato_da"]) == owner
        assert "requisiti" in v[1]["snapshot"] and "posizioni" in v[1]["snapshot"]
        assert "versione" not in v[1]["snapshot"]["call"]
        assert "ai_posizioni_stato" not in v[1]["snapshot"]["call"]
        (a,) = audit(db, "partenariato.call_modificata")
        assert a["payload"]["versione"] == 2
        assert a["payload"]["campi"] == sorted([
            "budget_fascia", "budget_progetto_eur", "descrizione_pubblica", "dettagli_riservati",
            "profilo_partner_ideale", "quota_creatore_pct", "scadenza_call", "visibilita"])

    @pytest.mark.parametrize(("chiave", "valore"), [
        ("titolo", "Un altro titolo per la call"), ("ruolo_creatore", "cerco_capofila"),
        ("anonima", False), ("wizard_passo", 3), ("forma_aggregazione_prevista", "ats"),
        ("override_non_ammesso_motivo", "x" * 30), ("partenariato_ref", {"a": 2}),
    ])
    def test_pubblicata_fuori_whitelist(self, db, chiave, valore):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c, {chiave: valore})
        assert detail_of(exc) == "campo_non_modificabile"
        assert call(db, c)["versione"] == 1

    def test_pubblicata_stesso_valore_fuori_whitelist_ammesso(self, db):
        owner, company, c = pubblicata(db)
        riga = call(db, c)
        out = aggiorna(db, owner, company, c, {"titolo": riga["titolo"],
                                               "ruolo_creatore": riga["ruolo_creatore"]})
        assert out["versione"] == 1 and len(versioni(db, c)) == 1
        out = aggiorna(db, owner, company, c, {"titolo": riga["titolo"],
                                               "descrizione_pubblica": "Cambiata"})
        assert out["versione"] == 2
        # L'audit elenca solo i campi cambiati.
        (a,) = audit(db, "partenariato.call_modificata")
        assert a["payload"]["campi"] == ["descrizione_pubblica"]

    def test_pubblicata_nessun_cambio_nessuna_versione(self, db):
        owner, company, c = pubblicata(db)
        riga = call(db, c)
        aggiorna(db, owner, company, c, {"descrizione_pubblica": riga["descrizione_pubblica"],
                                         "quota_creatore_pct": None})
        assert call(db, c)["versione"] == 1 and len(versioni(db, c)) == 1

    @pytest.mark.parametrize("giorni", [-1, 91])
    def test_pubblicata_scadenza_non_valida(self, db, giorni):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c,
                     {"scadenza_call": (oggi(db) + timedelta(days=giorni)).isoformat()})
        assert detail_of(exc) == "scadenza_call_non_valida"

    @pytest.mark.parametrize("giorni", [0, 90])
    def test_pubblicata_scadenza_ai_bordi(self, db, giorni):
        owner, company, c = pubblicata(db)
        aggiorna(db, owner, company, c,
                 {"scadenza_call": (oggi(db) + timedelta(days=giorni)).isoformat()})
        assert call(db, c)["scadenza_call"] == oggi(db) + timedelta(days=giorni)

    def test_pubblicata_scadenza_invariata_non_rivalidata(self, db):
        """Una scadenza già passata (in attesa dello scheduler) non blocca le altre
        modifiche."""
        owner, company, c = pubblicata(db)
        db.execute("update public.partner_calls set scadenza_call = %s where id = %s",
                   (oggi(db) - timedelta(days=2), c))
        aggiorna(db, owner, company, c, {"descrizione_pubblica": "Aggiornata"})

    @pytest.mark.parametrize("campi", [{"descrizione_pubblica": None},
                                       {"descrizione_pubblica": "   "},
                                       {"scadenza_call": None}])
    def test_pubblicata_non_svuotabile(self, db, campi):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c, campi)
        assert detail_of(exc) == "call_incompleta"

    @pytest.mark.parametrize("stato", ["chiusa_annullata", "chiusa_completata", "scaduta",
                                       "sospesa_moderazione"])
    def test_stato_non_valido(self, db, stato):
        owner, company, c = pubblicata(db)
        if stato == "chiusa_annullata":
            chiudi(db, owner, company, c, "annullata")
        elif stato == "chiusa_completata":
            chiudi(db, owner, company, c, "completata")
        elif stato == "scaduta":
            chiudi_auto(db, c, "scaduta", "scadenza_call")
        else:
            db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                       "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' "
                       "where id = %s", (c,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c, {"descrizione_pubblica": "Nuova"})
        assert detail_of(exc) == "stato_call_non_valido"

    @pytest.mark.parametrize("campi", [
        {"titolo": "Corto"}, {"scadenza_call": "2026-13-45"}, {"quota_creatore_pct": "tanto"},
        {"anonima": None}, {"ruolo_creatore": None}, {"visibilita": "privata"},
        {"budget_progetto_eur": -5}, {"wizard_passo": 9},
    ])
    def test_dati_non_validi(self, db, campi):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c["id"], campi)
        assert detail_of(exc) == "dati_non_validi"

    @pytest.mark.parametrize("campi", [None, [], "x"])
    def test_campi_non_oggetto(self, db, campi):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c["id"], campi)
        assert detail_of(exc) == "parametri_non_validi"

    def test_ai_check_della_stessa_azienda_e_bando(self, db):
        owner, company = azienda_pronta(db)
        b = bando(db)
        c = crea(db, owner, company, bando_=b)
        buono = ai_check(db, owner, company, b["id"])
        aggiorna(db, owner, company, c["id"], {"ai_check_id": buono})
        _, altra = azienda_pronta(db, owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c["id"],
                     {"ai_check_id": ai_check(db, owner, altra, b["id"])})
        assert detail_of(exc) == "dati_non_validi"
        aggiorna(db, owner, company, c["id"], {"ai_check_id": None})

    def test_advisor_non_tocca_la_call_di_un_altra_azienda(self, db):
        owner, a = azienda_pronta(db, plan_slug="advisor")
        _, b = azienda_pronta(db, owner)
        call_b = crea(db, owner, b)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, a, call_b, {"wizard_passo": 3})
        assert detail_of(exc) == "call_not_found"
        altro = new_user(db, "smart")
        _, azienda_altro = azienda_pronta(db, altro)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, altro, azienda_altro, call_b, {"wizard_passo": 3})
        assert detail_of(exc) == "call_not_found"
        assert call(db, call_b)["wizard_passo"] == 1

    def test_attore_non_titolare(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c["id"], {"wizard_passo": 2}, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"

    def test_azienda_non_viva(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, owner, company, c["id"], {"wizard_passo": 2})
        assert detail_of(exc) == "company_not_found"


# -------------------------------------------------------- conferma regole


class TestConfermaRegole:
    def test_conferma(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        out = conferma(db, owner, company, c["id"], esclusivita=True)
        riga = call(db, c["id"])
        assert riga["regole_partenariato"] == regole()
        assert riga["esclusivita"] is True and riga["regole_confermate_at"] is not None
        assert out["esclusivita"] is True
        # Riconferma: sostituisce lo snapshot.
        conferma(db, owner, company, c["id"], regole(partner_min=None), esclusivita=False)
        riga = call(db, c["id"])
        assert riga["regole_partenariato"]["partner_min"] is None
        assert riga["esclusivita"] is False

    def test_liste_assenti_o_nulle_ammesse(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        conferma(db, owner, company, c["id"],
                 {"versione": 1, "modalita": {"valore": "ammesso", "origine_voce": "aggiunta"},
                  "quote": None, "fonte": None})

    @pytest.mark.parametrize("snapshot_", [
        None, [], "x", regole(versione=2), regole(versione="1"), {"versione": 1},
        regole(modalita=None), regole(modalita={"valore": "ammesso"}),
        regole(modalita={"valore": "ammesso", "origine_voce": "estratta"}),
        regole(fonte="WP3"), regole(costituzione="da_costituire"),
        regole(partner_max={"valore": 3}),
        regole(composizione={"id": "K1"}), regole(composizione=["K1"]),
        regole(vincoli=[{"id": "V1"}]),
        regole(quote=[{"id": "Q1", "origine_voce": "da_verificare"}]),
        regole(documenti_richiesti=[{"id": "D1", "origine_voce": None}]),
        regole(forme_ammesse=[{"forma": "ats", "origine_voce": "confermata"}] * 101),
        regole(regole_finanziarie=[dict(REGOLA_F1, origine_voce="aggiunta")]),
        regole(note="x" * 262144),
    ])
    def test_regole_non_valide(self, db, snapshot_):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, owner, company, c["id"], snapshot_)
        assert detail_of(exc) == "regole_non_valide"
        assert call(db, c["id"])["regole_confermate_at"] is None

    def test_esclusivita_obbligatoria(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, owner, company, c["id"], esclusivita=None)
        assert detail_of(exc) == "regole_non_valide"

    def test_solo_in_bozza(self, db):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, owner, company, c)
        assert detail_of(exc) == "stato_call_non_valido"

    def test_guardie(self, db):
        owner, a = azienda_pronta(db, plan_slug="advisor")
        _, b = azienda_pronta(db, owner)
        call_b = crea(db, owner, b)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, owner, a, call_b)
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, owner, b, call_b, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"


# --------------------------------------------------------------- requisiti


class TestRequisiti:
    def test_sostituzione_con_etichette_in_ordine(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        out = requisiti(db, owner, company, c, [REQ_A, REQ_B, {**REQ_A, "testo": "Terzo"}])
        assert [r["etichetta"] for r in out] == ["A", "B", "C"]
        assert [r["ordine"] for r in out] == [0, 1, 2]
        r = righe(db, "partner_call_requisiti", c)
        assert r[0]["criterio"] == REQ_A["criterio"] and r[0]["cercato"] is True
        assert r[0]["ambito"] == "consorzio" and r[1]["ambito"] == "consorzio"
        assert r[1]["cercato"] is False and r[1]["copertura_creatore"] == "coperto"
        assert r[1]["copertura_nota"] == "Coperto dal Registro Imprese"
        # Una nuova generazione senza id riparte da «A».
        out = requisiti(db, owner, company, c, [REQ_B])
        assert [x["etichetta"] for x in out] == ["A"]
        assert len(righe(db, "partner_call_requisiti", c)) == 1

    def test_lista_vuota(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        requisiti(db, owner, company, c, [REQ_A])
        assert requisiti(db, owner, company, c, []) == []
        assert conta(db, "partner_call_requisiti") == 0

    def test_id_ed_etichetta_conservati(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        a, b = requisiti(db, owner, company, c, [REQ_A, REQ_B])
        db.execute("update public.partner_call_requisiti set created_at = now() - "
                   "interval '1 day' where id = %s", (b["id"],))
        creato = righe(db, "partner_call_requisiti", c)[1]["created_at"]
        out = requisiti(db, owner, company, c, [
            {**REQ_B, "id": b["id"], "testo": "ATECO 62 aggiornato"},
            {**REQ_A, "testo": "Nuovo requisito"},
        ])
        assert out[0]["id"] == b["id"] and out[0]["etichetta"] == "B"
        assert out[0]["testo"] == "ATECO 62 aggiornato" and out[0]["ordine"] == 0
        # Il nuovo prende la prima etichetta libera: «A» non è più usata.
        assert out[1]["etichetta"] == "A" and out[1]["id"] != a["id"]
        assert righe(db, "partner_call_requisiti", c)[0]["created_at"] == creato

    def test_etichette_esplicite_e_automatiche(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        out = requisiti(db, owner, company, c, [
            REQ_A, {**REQ_B, "etichetta": " A "}, {**REQ_A, "etichetta": "Regione"}, REQ_B])
        assert [r["etichetta"] for r in out] == ["B", "A", "Regione", "C"]

    def test_etichette_oltre_la_z(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        lista = [{**REQ_B, "testo": f"Requisito {i}"} for i in range(40)]
        out = requisiti(db, owner, company, c, lista)
        assert [r["etichetta"] for r in out[:3]] == ["A", "B", "C"]
        assert [r["etichetta"] for r in out[25:29]] == ["Z", "AA", "AB", "AC"]
        assert out[-1]["etichetta"] == "AN"

    @pytest.mark.parametrize("lista", [
        None, {"testo": "x"}, [REQ_A] * 41, ["testo"], [[REQ_A]],
        [{**REQ_A, "boh": 1}], [{**REQ_A, "ordine": 3}], [{**REQ_A, "call_id": None}],
        [{**REQ_A, "id": "non-un-uuid"}], [{**REQ_A, "id": str(uuid.uuid4())}],
        [{**REQ_A, "etichetta": "X"}, {**REQ_B, "etichetta": "X"}],
        [{**REQ_A, "testo": "ab"}], [{**REQ_A, "testo": None}], [{**REQ_A, "origine": None}],
        [{**REQ_A, "origine": "altro"}], [{**REQ_A, "ambito": "tutti"}],
        [{**REQ_A, "criterio": {"tipo": "boh"}}], [{**REQ_A, "cercato": "forse"}],
        [{**REQ_A, "etichetta": "x" * 61}], [{**REQ_A, "copertura_creatore": "parziale"}],
    ])
    def test_requisiti_non_validi(self, db, lista):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        requisiti(db, owner, company, c, [REQ_B])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            requisiti(db, owner, company, c, lista)
        assert detail_of(exc) == "requisiti_non_validi"
        assert [r["testo"] for r in righe(db, "partner_call_requisiti", c)] == [REQ_B["testo"]]

    def test_id_duplicato_o_di_un_altra_call(self, db):
        owner, company = azienda_pronta(db)
        c1 = crea(db, owner, company)["id"]
        c2 = crea(db, owner, company)["id"]
        (a,) = requisiti(db, owner, company, c1, [REQ_A])
        (x,) = requisiti(db, owner, company, c2, [REQ_A])
        for lista in ([{**REQ_A, "id": a["id"]}, {**REQ_B, "id": a["id"]}],
                      [{**REQ_A, "id": x["id"]}]):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                requisiti(db, owner, company, c1, lista)
            assert detail_of(exc) == "requisiti_non_validi"

    def test_etichetta_esplicita_uguale_a_una_conservata(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        a, _ = requisiti(db, owner, company, c, [REQ_A, REQ_B])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            requisiti(db, owner, company, c, [{**REQ_A, "id": a["id"]},
                                              {**REQ_B, "etichetta": "A"}])
        assert detail_of(exc) == "requisiti_non_validi"

    def test_regola_finanziaria_dallo_snapshot(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        conferma(db, owner, company, c)
        req = {"testo": "Costo quota / fatturato medio ≤ 0,6", "ambito": "ogni_membro",
               "origine": "regola_finanziaria", "rif_origine": "F1",
               "criterio": {"tipo": "regola_finanziaria", "regola": REGOLA_F1}}
        (r,) = requisiti(db, owner, company, c, [req])
        assert r["criterio"]["regola"] == REGOLA_F1
        # Chiavi a null omesse: stessa regola.
        regola = {k: v for k, v in REGOLA_F1.items() if v is not None}
        requisiti(db, owner, company, c, [{**req, "criterio": {"tipo": "regola_finanziaria",
                                                               "regola": regola}}])

    @pytest.mark.parametrize("caso", ["origine_manuale", "origine_ai_check", "soglia_ritoccata",
                                      "regola_inventata", "senza_id", "senza_regole",
                                      "regola_non_oggetto", "operatore_cambiato"])
    def test_regola_finanziaria_non_ammessa(self, db, caso):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        if caso != "senza_regole":
            conferma(db, owner, company, c)
        regola = dict(REGOLA_F1)
        origine = "regola_finanziaria"
        if caso == "origine_manuale":
            origine = "manuale"
        elif caso == "origine_ai_check":
            origine = "ai_check"
        elif caso == "soglia_ritoccata":
            regola["soglia"] = "0.61"
        elif caso == "regola_inventata":
            regola["id"] = "F9"
        elif caso == "senza_id":
            regola.pop("id")
        elif caso == "operatore_cambiato":
            regola["operatore"] = "lt"
        criterio = {"tipo": "regola_finanziaria",
                    "regola": "F1" if caso == "regola_non_oggetto" else regola}
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            requisiti(db, owner, company, c, [{"testo": "Regola finanziaria",
                                               "origine": origine, "criterio": criterio}])
        assert detail_of(exc) == "requisiti_non_validi"

    def test_regola_finanziaria_da_voce_modificata(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        conferma(db, owner, company, c, regole(regole_finanziarie=[
            dict(REGOLA_F1, soglia="0.5", origine_voce="modificata")]))
        requisiti(db, owner, company, c, [{
            "testo": "Regola", "origine": "regola_finanziaria",
            "criterio": {"tipo": "regola_finanziaria", "regola": dict(REGOLA_F1, soglia="0.5")}}])

    def test_posizioni_riallineate(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        a, b, x = requisiti(db, owner, company, c, [REQ_A, REQ_B, {**REQ_A, "testo": "Terzo"}])
        posizioni(db, owner, company, c, [
            {**POS_1, "requisiti_ids": [a["id"], b["id"], x["id"]]},
            {**POS_1, "titolo": "Seconda", "requisiti_ids": [x["id"]]}])
        # A conservato per id; B sostituito da un nuovo requisito con la stessa
        # etichetta ESPLICITA; C rimosso (una nuova etichetta automatica non eredita).
        out = requisiti(db, owner, company, c, [
            {**REQ_A, "id": a["id"]}, {**REQ_B, "etichetta": "B", "testo": "Nuovo B"},
            {**REQ_B, "testo": "Senza etichetta"}])
        nuovo_b = next(r for r in out if r["etichetta"] == "B")["id"]
        assert [r["etichetta"] for r in out] == ["A", "B", "C"]
        p1, p2 = righe(db, "partner_call_posizioni", c)
        assert [str(i) for i in p1["requisiti_ids"]] == [a["id"], nuovo_b]
        assert p2["requisiti_ids"] == []

    def test_pubblicata_crea_una_versione(self, db):
        owner, company, c = pubblicata(db)
        esistenti = righe(db, "partner_call_requisiti", c)
        stessi = [{k: r[k] for k in ("testo", "criterio", "ambito", "cercato", "origine")}
                  | {"id": str(r["id"])} for r in esistenti]
        stessi[1]["copertura_creatore"] = "coperto"
        stessi[1]["copertura_fonte"] = "registro"
        stessi[1]["copertura_nota"] = "Coperto dal Registro Imprese"
        requisiti(db, owner, company, c, stessi)
        assert call(db, c)["versione"] == 1  # contenuto identico: nessuna versione
        requisiti(db, owner, company, c, stessi[:1])
        assert call(db, c)["versione"] == 2
        v = versioni(db, c)[-1]
        assert len(v["snapshot"]["requisiti"]) == 1
        (a,) = audit(db, "partenariato.call_modificata")
        assert a["payload"]["campi"] == ["requisiti"]

    def test_pubblicata_almeno_un_cercato(self, db):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            requisiti(db, owner, company, c, [REQ_B])
        assert detail_of(exc) == "call_incompleta"
        assert len(righe(db, "partner_call_requisiti", c)) == 2

    def test_stato_non_valido_e_guardie(self, db):
        owner, company, c = pubblicata(db)
        chiudi(db, owner, company, c, "completata")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            requisiti(db, owner, company, c, [REQ_A])
        assert detail_of(exc) == "stato_call_non_valido"
        _, altra = azienda_pronta(db, owner)
        c2 = crea(db, owner, altra)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            requisiti(db, owner, company, c2, [REQ_A])
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            requisiti(db, owner, altra, c2, [REQ_A], attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"


# ---------------------------------------------------------------- posizioni


class TestPosizioni:
    def test_sostituzione_con_default(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        (a,) = requisiti(db, owner, company, c, [REQ_A])
        out = posizioni(db, owner, company, c, [
            {**POS_1, "requisiti_ids": [a["id"]], "regioni": [18], "territorio_modalita":
             "sede_attuale", "paesi": ["IT"], "dimensioni": ["micro"], "ateco_divisioni": ["72"],
             "numero": 2, "ruolo": "partner", "note": "Nota"},
            {"titolo": "Capofila cercato", "ruolo": "capofila"}])
        assert [p["ordine"] for p in out] == [0, 1]
        p1, p2 = righe(db, "partner_call_posizioni", c)
        assert p1["requisiti_ids"] == [uuid.UUID(a["id"])] and p1["regioni"] == [18]
        assert float(p1["quota_ipotizzata_pct"]) == 30 and p1["numero"] == 2
        assert (p2["ruolo"], p2["territorio_modalita"], p2["numero"]) == (
            "capofila", "qualsiasi", 1)
        assert p2["tipi_soggetto"] == [] and p2["quota_ipotizzata_pct"] is None

    def test_id_conservati(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        p1, p2 = posizioni(db, owner, company, c, [POS_1, {**POS_1, "titolo": "Seconda"}])
        db.execute("update public.partner_call_posizioni set created_at = now() - "
                   "interval '1 day' where id = %s", (p2["id"],))
        out = posizioni(db, owner, company, c, [{**POS_1, "id": p2["id"], "titolo": "Modificata"},
                                                {**POS_1, "titolo": "Nuova"}])
        assert out[0]["id"] == p2["id"] and out[0]["titolo"] == "Modificata"
        assert out[1]["id"] not in (p1["id"], p2["id"])
        assert righe(db, "partner_call_posizioni", c)[0]["created_at"] < \
            righe(db, "partner_call_posizioni", c)[1]["created_at"]

    @pytest.mark.parametrize("lista", [
        None, {"titolo": "x"}, [POS_1] * 11, ["titolo"], [{**POS_1, "boh": 1}],
        [{**POS_1, "ordine": 1}], [{**POS_1, "call_id": None}], [{**POS_1, "id": "x"}],
        [{**POS_1, "id": str(uuid.uuid4())}], [{**POS_1, "titolo": "ab"}], [{"ruolo": "partner"}],
        [{**POS_1, "ruolo": "socio"}], [{**POS_1, "paesi": ["ita"]}],
        [{**POS_1, "dimensioni": ["enorme"]}], [{**POS_1, "numero": 0}],
        [{**POS_1, "quota_ipotizzata_pct": 0}], [{**POS_1, "regioni": ["Calabria"]}],
        [{**POS_1, "requisiti_ids": [str(uuid.uuid4())]}], [{**POS_1, "requisiti_ids": "x"}],
        [{**POS_1, "competenze": "prototipazione"}], [{**POS_1, "ateco_divisioni": ["7"]}],
        [{**POS_1, "territorio_modalita": "ovunque"}],
    ])
    def test_posizioni_non_valide(self, db, lista):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        posizioni(db, owner, company, c, [POS_1])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            posizioni(db, owner, company, c, lista)
        assert detail_of(exc) == "posizioni_non_valide"
        assert [p["titolo"] for p in righe(db, "partner_call_posizioni", c)] == [POS_1["titolo"]]

    def test_id_e_requisiti_di_un_altra_call(self, db):
        owner, company = azienda_pronta(db)
        c1 = crea(db, owner, company)["id"]
        c2 = crea(db, owner, company)["id"]
        (p,) = posizioni(db, owner, company, c2, [POS_1])
        (r,) = requisiti(db, owner, company, c2, [REQ_A])
        (mia,) = posizioni(db, owner, company, c1, [POS_1])
        for lista in ([{**POS_1, "id": p["id"]}], [{**POS_1, "requisiti_ids": [r["id"]]}],
                      [{**POS_1, "id": mia["id"]}, {**POS_1, "id": mia["id"]}]):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                posizioni(db, owner, company, c1, lista)
            assert detail_of(exc) == "posizioni_non_valide"

    def test_pubblicata_versione_e_almeno_una(self, db):
        owner, company, c = pubblicata(db)
        (p,) = righe(db, "partner_call_posizioni", c)
        stessa = {k: p[k] for k in ("titolo", "tipi_soggetto", "competenze")} | {
            "id": str(p["id"]), "quota_ipotizzata_pct": 30}
        posizioni(db, owner, company, c, [stessa])
        assert call(db, c)["versione"] == 1
        posizioni(db, owner, company, c, [stessa, {**POS_1, "titolo": "Seconda posizione"}])
        assert call(db, c)["versione"] == 2
        assert len(versioni(db, c)[-1]["snapshot"]["posizioni"]) == 2
        (a,) = audit(db, "partenariato.call_modificata")
        assert a["payload"]["campi"] == ["posizioni"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            posizioni(db, owner, company, c, [])
        assert detail_of(exc) == "call_incompleta"

    def test_stato_non_valido_e_guardie(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        chiudi(db, owner, company, c, "annullata")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            posizioni(db, owner, company, c, [POS_1])
        assert detail_of(exc) == "stato_call_non_valido"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            posizioni(db, owner, company, str(uuid.uuid4()), [POS_1])
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            posizioni(db, owner, company, c, [POS_1], attore=None)
        assert detail_of(exc) == "attore_non_titolare"


# ---------------------------------------------------------------- pubblica


class TestPubblica:
    def test_pubblica(self, db):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        scadenza_bando = oggi(db) + timedelta(days=60)
        out = pubblica(db, owner, company, c, bando_stato="in apertura prossimamente",
                       bando_scadenza=scadenza_bando, scadenza_call=oggi(db) + timedelta(days=20))
        riga = call(db, c)
        assert out["stato"] == "pubblicata" == riga["stato"] and riga["pubblicata_at"]
        assert riga["versione"] == 1 and riga["scadenza_call"] == oggi(db) + timedelta(days=20)
        assert riga["bando_stato_effettivo"] == "in apertura prossimamente"
        assert riga["bando_scadenza"] == scadenza_bando
        (v,) = versioni(db, c)
        assert v["versione"] == 1 and v["snapshot"]["call"]["stato"] == "pubblicata"
        assert len(v["snapshot"]["requisiti"]) == 2 and len(v["snapshot"]["posizioni"]) == 1
        (a,) = audit(db, "partenariato.call_pubblicata")
        assert a["payload"] == {"call_id": c, "company_profile_id": company,
                                "bando_id": riga["bando_id"], "versione": 1}

    def test_aggiorna_lo_snapshot_del_bando(self, db):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        db.execute("update public.partner_calls set bando_mancante_dal = current_date, "
                   "bando_verificato_at = now() - interval '3 days' where id = %s", (c,))
        pubblica(db, owner, company, c, bando_scadenza=None)
        riga = call(db, c)
        assert riga["bando_mancante_dal"] is None and riga["bando_scadenza"] is None
        assert riga["bando_verificato_at"] > riga["created_at"]

    def test_scadenza_salvata_se_non_indicata(self, db):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        aggiorna(db, owner, company, c,
                 {"scadenza_call": (oggi(db) + timedelta(days=10)).isoformat()})
        pubblica(db, owner, company, c, scadenza_call=None)
        assert call(db, c)["scadenza_call"] == oggi(db) + timedelta(days=10)

    @pytest.mark.parametrize("stato", ["pubblicata", "chiusa_annullata", "sospesa_moderazione"])
    def test_solo_una_bozza(self, db, stato):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        if stato == "pubblicata":
            pubblica(db, owner, company, c)
        elif stato == "chiusa_annullata":
            chiudi(db, owner, company, c, "annullata")
        else:
            db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                       "sospesa_at = now(), stato_prima_sospensione = 'bozza' where id = %s",
                       (c,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c)
        assert detail_of(exc) == "stato_call_non_valido"

    @pytest.mark.parametrize("caso", ["senza_dati", "piva_diversa", "cessata", "sandbox",
                                      "piva_cambiata"])
    def test_identita_non_verificata(self, db, caso):
        owner = new_user(db, "smart")
        company = make_company(db, owner)
        if caso == "piva_diversa":
            importa(db, company, piva="99999999999")
        elif caso == "cessata":
            importa(db, company, stato="Cessata")
        elif caso == "sandbox":
            importa(db, company, sandbox=True)
        elif caso == "piva_cambiata":
            importa(db, company)
        c = call_pronta(db, owner, company)
        if caso == "piva_cambiata":
            db.execute("update public.company_profiles set partita_iva = '99999999998' "
                       "where id = %s", (company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c)
        assert detail_of(exc) == "identita_non_verificata"
        assert call(db, c)["stato"] == "bozza"

    def test_sandbox_ammessa_solo_se_non_richiesto_il_contrario(self, db):
        owner = new_user(db, "smart")
        company = make_company(db, owner)
        importa(db, company, sandbox=True)
        c = call_pronta(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c, non_sandbox=None)  # NULL vale true
        assert detail_of(exc) == "identita_non_verificata"
        assert pubblica(db, owner, company, c, non_sandbox=False)["stato"] == "pubblicata"

    def test_nominativa_richiede_il_rappresentante(self, db):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company, dati={"anonima": False})
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c)
        assert detail_of(exc) == "rappresentante_non_verificato"
        imposta_cf(db, owner)
        legale(db, company, " rssmra80a01h501u ")
        # Dalla 0041 il CF tra i legali rappresentanti non basta più.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c)
        assert detail_of(exc) == "rappresentante_non_verificato"
        verifica_identita_admin(db, owner, company)
        assert pubblica(db, owner, company, c)["stato"] == "pubblicata"

    def test_anonima_senza_rappresentante(self, db):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        assert call(db, c)["anonima"] is True
        assert pubblica(db, owner, company, c)["stato"] == "pubblicata"

    @pytest.mark.parametrize("stato", ["chiuso", "sospeso", "revocato", None, "", "Chiuso",
                                       "in apertura"])
    def test_bando_non_disponibile(self, db, stato):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c, bando_stato=stato)
        assert detail_of(exc) == "bando_non_disponibile"

    @pytest.mark.parametrize("stato", ["aperto", " Aperto ", "IN APERTURA PROSSIMAMENTE"])
    def test_bando_aperto_normalizzato(self, db, stato):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        assert pubblica(db, owner, company, c, bando_stato=stato)["stato"] == "pubblicata"

    @pytest.mark.parametrize(("scadenza", "bando_scadenza"), [
        (-1, 90), (91, 90), (None, 90), (5, 4), (1, -1),
    ])
    def test_scadenza_call_non_valida(self, db, scadenza, bando_scadenza):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c,
                     scadenza_call=None if scadenza is None else oggi(db) + timedelta(scadenza),
                     bando_scadenza=oggi(db) + timedelta(bando_scadenza))
        assert detail_of(exc) == "scadenza_call_non_valida"

    @pytest.mark.parametrize(("scadenza", "bando_scadenza"), [(0, 90), (90, 90), (365, None)])
    def test_scadenza_call_ai_bordi(self, db, scadenza, bando_scadenza):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        pubblica(db, owner, company, c, scadenza_call=oggi(db) + timedelta(scadenza),
                 bando_scadenza=None if bando_scadenza is None
                 else oggi(db) + timedelta(bando_scadenza))

    @pytest.mark.parametrize("manca", ["titolo", "descrizione", "descrizione_vuota", "regole",
                                       "posizioni", "requisiti", "cercati"])
    def test_call_incompleta(self, db, manca):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        if manca == "titolo":
            aggiorna(db, owner, company, c, {"titolo": None})
        elif manca == "descrizione":
            aggiorna(db, owner, company, c, {"descrizione_pubblica": None})
        elif manca == "descrizione_vuota":
            aggiorna(db, owner, company, c, {"descrizione_pubblica": "  "})
        elif manca == "regole":
            db.execute("update public.partner_calls set regole_confermate_at = null "
                       "where id = %s", (c,))
        elif manca == "posizioni":
            posizioni(db, owner, company, c, [])
        elif manca == "requisiti":
            requisiti(db, owner, company, c, [])
        else:
            requisiti(db, owner, company, c, [REQ_B])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c)
        assert detail_of(exc) == "call_incompleta"

    def test_regola_finanziaria_non_piu_nello_snapshot(self, db):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        requisiti(db, owner, company, c, [REQ_A, {
            "testo": "Regola", "origine": "regola_finanziaria",
            "criterio": {"tipo": "regola_finanziaria", "regola": REGOLA_F1}}])
        conferma(db, owner, company, c, regole(regole_finanziarie=[]))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c)
        assert detail_of(exc) == "requisiti_non_validi"

    def test_limite_smart_con_pool_su_due_aziende(self, db):
        owner, a = azienda_pronta(db, plan_slug="smart")
        _, b = azienda_pronta(db, owner)
        ca = call_pronta(db, owner, a)
        cb = call_pronta(db, owner, b)
        pubblica(db, owner, a, ca)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, b, cb)
        assert detail_of(exc) == "limite_call_raggiunto"
        assert call(db, cb)["stato"] == "bozza"
        # Chiusa la prima, il posto si libera.
        chiudi(db, owner, a, ca, "completata")
        assert pubblica(db, owner, b, cb)["stato"] == "pubblicata"

    def test_sospesa_conta_nel_pool(self, db):
        owner, a, ca = pubblicata(db)
        db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                   "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' where id = %s",
                   (ca,))
        _, b = azienda_pronta(db, owner)
        cb = call_pronta(db, owner, b)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, b, cb)
        assert detail_of(exc) == "limite_call_raggiunto"

    @pytest.mark.parametrize("stato_azienda", ["deleted_at", "archived_at"])
    def test_le_aziende_non_vive_non_contano(self, db, stato_azienda):
        owner, a, _ = pubblicata(db)
        db.execute(f"update public.company_profiles set {stato_azienda} = now() where id = %s",
                   (a,))
        _, b = azienda_pronta(db, owner)
        cb = call_pronta(db, owner, b)
        assert pubblica(db, owner, b, cb)["stato"] == "pubblicata"

    def test_il_pool_e_dell_owner(self, db):
        pubblicata(db)  # un altro owner con la sua call
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        assert pubblica(db, owner, company, c)["stato"] == "pubblicata"

    def test_advisor_illimitato(self, db):
        limite_piano(db, "advisor", None)
        owner, company = azienda_pronta(db, plan_slug="advisor")
        for _ in range(12):
            _, azienda = azienda_pronta(db, owner)
            c = call_pronta(db, owner, azienda)
            pubblica(db, owner, azienda, c)
        assert snapshot(db, owner)["call_attive"]["usate"] == 12

    def test_downgrade(self, db):
        """Q18: dopo un downgrade le call pubblicate restano; le nuove si bloccano."""
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        c2 = call_pronta(db, owner, company)  # stessa azienda, altro bando
        pubblica(db, owner, company, c)
        switch_plan(db, owner, "gratuito")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, company, c2)
        assert detail_of(exc) == "piano_non_include_call"
        assert call(db, c)["stato"] == "pubblicata"
        aggiorna(db, owner, company, c, {"descrizione_pubblica": "Si modifica ancora"})

    def test_ordine_dei_controlli(self, db):
        """Identità prima del bando, bando prima della scadenza, scadenza prima
        della completezza, completezza prima dei limiti."""
        owner = new_user(db)  # gratuito: limite 0
        company = make_company(db, owner)
        switch_plan(db, owner, "smart")
        c = crea(db, owner, company)["id"]
        switch_plan(db, owner, "gratuito")
        attesi = [
            (dict(bando_stato="chiuso", scadenza_call=oggi(db) - timedelta(1)),
             "identita_non_verificata"),
        ]
        for kwargs, detail in attesi:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                pubblica(db, owner, company, c, **kwargs)
            assert detail_of(exc) == detail
        importa(db, company)
        for kwargs, detail in [
            (dict(bando_stato="chiuso", scadenza_call=oggi(db) - timedelta(1)),
             "bando_non_disponibile"),
            (dict(scadenza_call=oggi(db) - timedelta(1)), "scadenza_call_non_valida"),
            ({}, "call_incompleta"),
        ]:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                pubblica(db, owner, company, c, **kwargs)
            assert detail_of(exc) == detail

    def test_guardie(self, db):
        owner, a = azienda_pronta(db, plan_slug="advisor")
        _, b = azienda_pronta(db, owner)
        cb = call_pronta(db, owner, b)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, a, cb)
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, b, cb, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        db.execute("update public.company_profiles set deleted_at = now() where id = %s", (b,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica(db, owner, b, cb)
        assert detail_of(exc) == "company_not_found"

    def test_concorrenza_sul_pool(self, db):
        """Due pubblicazioni concorrenti su due aziende dello stesso owner Smart: la
        seconda attende il lock dell'owner e poi trova il limite raggiunto."""
        owner, a = azienda_pronta(db)
        _, b = azienda_pronta(db, owner)
        ca = call_pronta(db, owner, a)
        cb = call_pronta(db, owner, b)
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: pubblica(db, owner, b, cb))
        try:
            pubblica(altra, owner, a, ca)
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
            assert not thread.is_alive()
        finally:
            if altra.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                altra.rollback()
            altra.close()
            monitor.close()
            if thread.is_alive():
                thread.join(timeout=10)
        assert "out" not in esito, esito
        assert esito["errore"].diag.message_detail == "limite_call_raggiunto"
        assert call(db, cb)["stato"] == "bozza"


# ------------------------------------------------------------------ chiudi


class TestChiudi:
    def test_completata_da_pubblicata(self, db):
        owner, company, c = pubblicata(db)
        out = chiudi(db, owner, company, c, "completata")
        assert out["stato"] == "chiusa_completata"
        assert out["motivo_chiusura"] == "creatore_completata" and out["chiusa_at"]
        (a,) = audit(db, "partenariato.call_chiusa")
        assert str(a["actor_id"]) == owner
        assert a["payload"] == {"call_id": c, "company_profile_id": company,
                                "stato": "chiusa_completata", "motivo": "creatore_completata",
                                "origine": "creatore"}

    @pytest.mark.parametrize("stato", ["bozza", "pubblicata", "sospesa_moderazione"])
    def test_annullata(self, db, stato):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        if stato != "bozza":
            pubblica(db, owner, company, c)
        if stato == "sospesa_moderazione":
            db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                       "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' "
                       "where id = %s", (c,))
        out = chiudi(db, owner, company, c, "annullata")
        assert (out["stato"], out["motivo_chiusura"]) == ("chiusa_annullata",
                                                          "creatore_annullata")

    @pytest.mark.parametrize(("stato", "esito"), [
        ("bozza", "completata"), ("sospesa_moderazione", "completata"),
        ("chiusa_completata", "annullata"), ("chiusa_annullata", "annullata"),
        ("scaduta", "annullata"), ("scaduta", "completata"),
    ])
    def test_stato_non_valido(self, db, stato, esito):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        if stato != "bozza":
            pubblica(db, owner, company, c)
        if stato == "sospesa_moderazione":
            db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                       "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' "
                       "where id = %s", (c,))
        elif stato == "chiusa_completata":
            chiudi(db, owner, company, c, "completata")
        elif stato == "chiusa_annullata":
            chiudi(db, owner, company, c, "annullata")
        elif stato == "scaduta":
            chiudi_auto(db, c, "scaduta", "scadenza_call")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, owner, company, c, esito)
        assert detail_of(exc) == "stato_call_non_valido"

    @pytest.mark.parametrize("esito", ["chiusa", None, "scaduta"])
    def test_esito_non_valido(self, db, esito):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, owner, company, c, esito)
        assert detail_of(exc) == "parametri_non_validi"

    def test_azienda_non_piu_viva(self, db):
        owner, company, c = pubblicata(db)
        db.execute("update public.company_profiles set deleted_at = now() where id = %s",
                   (company,))
        assert chiudi(db, owner, company, c, "annullata")["stato"] == "chiusa_annullata"

    def test_guardie(self, db):
        owner, a = azienda_pronta(db, plan_slug="advisor")
        _, b = azienda_pronta(db, owner)
        cb = crea(db, owner, b)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, owner, a, cb, "annullata")
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, owner, b, cb, "annullata", attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, new_user(db), b, cb, "annullata")
        assert detail_of(exc) == "company_not_found"
        assert call(db, cb)["stato"] == "bozza"


class TestChiudiAuto:
    @pytest.mark.parametrize(("nuovo", "motivo"), [
        ("scaduta", "scadenza_call"), ("scaduta", "bando_chiuso"), ("scaduta", "bando_sospeso"),
        ("chiusa_annullata", "bando_revocato"), ("chiusa_annullata", "bando_non_disponibile"),
        ("chiusa_annullata", "azienda_non_disponibile"),
    ])
    def test_da_pubblicata(self, db, nuovo, motivo):
        owner, company, c = pubblicata(db)
        assert chiudi_auto(db, c, nuovo, motivo) is True
        riga = call(db, c)
        assert (riga["stato"], riga["motivo_chiusura"]) == (nuovo, motivo)
        assert riga["chiusa_at"] is not None
        (a,) = audit(db, "partenariato.call_chiusa")
        assert a["actor_id"] is None and str(a["family_parent_id"]) == owner
        assert a["payload"] == {"call_id": c, "company_profile_id": company, "stato": nuovo,
                                "motivo": motivo, "origine": "sistema"}

    @pytest.mark.parametrize("nuovo", ["scaduta", "chiusa_annullata"])
    def test_da_bozza_sempre_annullata(self, db, nuovo):
        """Una bozza non «scade» (§7): anche vuota, si chiude annullata."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        assert chiudi_auto(db, c, nuovo, "bando_chiuso") is True
        riga = call(db, c)
        assert (riga["stato"], riga["motivo_chiusura"]) == ("chiusa_annullata", "bando_chiuso")
        assert audit(db, "partenariato.call_chiusa")[0]["payload"]["stato"] == "chiusa_annullata"

    @pytest.mark.parametrize("stato", ["chiusa_completata", "chiusa_annullata", "scaduta",
                                       "sospesa_moderazione"])
    def test_condizionata(self, db, stato):
        owner, company, c = pubblicata(db)
        if stato == "chiusa_completata":
            chiudi(db, owner, company, c, "completata")
        elif stato == "chiusa_annullata":
            chiudi(db, owner, company, c, "annullata")
        elif stato == "scaduta":
            chiudi_auto(db, c, "scaduta", "scadenza_call")
        else:
            db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                       "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' "
                       "where id = %s", (c,))
        prima = call(db, c)
        n_audit = len(audit(db, "partenariato.call_chiusa"))
        assert chiudi_auto(db, c, "chiusa_annullata", "bando_revocato") is False
        assert call(db, c) == prima
        assert len(audit(db, "partenariato.call_chiusa")) == n_audit

    def test_call_inesistente(self, db):
        assert chiudi_auto(db, str(uuid.uuid4()), "scaduta", "scadenza_call") is False

    @pytest.mark.parametrize(("nuovo", "motivo"), [
        ("chiusa_completata", "scadenza_call"), ("pubblicata", "scadenza_call"),
        (None, "scadenza_call"), ("scaduta", "creatore_annullata"), ("scaduta", "moderazione"),
        ("scaduta", None), ("scaduta", "boh"),
    ])
    def test_parametri_non_validi(self, db, nuovo, motivo):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi_auto(db, c, nuovo, motivo)
        assert detail_of(exc) == "parametri_non_validi"
        assert call(db, c)["stato"] == "pubblicata"

    def test_azienda_non_viva(self, db):
        owner, company, c = pubblicata(db)
        db.execute("update public.company_profiles set deleted_at = now() where id = %s",
                   (company,))
        assert chiudi_auto(db, c, "chiusa_annullata", "azienda_non_disponibile") is True


# ------------------------------------------------------------------ job AI


class TestAiPrenota:
    def test_prenotazione(self, db):
        owner, company = azienda_pronta(db)
        b = bando(db)
        c = crea(db, owner, company, bando_=b)["id"]
        eid = prenota_ai(db, owner, company, c, riserva=17)
        riga = call(db, c)
        assert riga["ai_posizioni_stato"] == "in_corso"
        assert str(riga["ai_posizioni_esecuzione_id"]) == eid
        assert riga["ai_posizioni_avviata_at"] is not None and riga["ai_testi_stato"] is None
        e = esecuzione(db, eid)
        assert (e["servizio"], e["origine"], e["gruppo"], e["stato"]) == (
            "partner_call_posizioni", "call", "altri", "in_corso")
        assert (e["bando_id"], str(e["company_profile_id"]), str(e["owner_id"])) == (
            b["id"], company, owner)
        assert str(e["richiedente_user_id"]) == owner and e["costo_riservato_cents"] == 17

    def test_servizi_indipendenti(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        e1 = prenota_ai(db, owner, company, c)
        e2 = prenota_ai(db, owner, company, c, servizio="partner_call_testi")
        riga = call(db, c)
        assert str(riga["ai_posizioni_esecuzione_id"]) == e1
        assert str(riga["ai_testi_esecuzione_id"]) == e2 and riga["ai_testi_stato"] == "in_corso"

    @pytest.mark.parametrize("servizio", ["partner_call_posizioni", "partner_call_testi"])
    def test_in_corso(self, db, servizio):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        prenota_ai(db, owner, company, c, servizio=servizio)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c, servizio=servizio)
        assert detail_of(exc) == "ai_in_corso"
        assert conta(db, "partenariati_ai_esecuzioni") == 1

    def test_job_orfano_si_chiude_e_si_riprenota(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        vecchia = prenota_ai(db, owner, company, c, riserva=13)
        db.execute("update public.partner_calls set ai_posizioni_avviata_at = now() - "
                   "interval '11 minutes' where id = %s", (c,))
        nuova = prenota_ai(db, owner, company, c)
        assert nuova != vecchia
        assert str(call(db, c)["ai_posizioni_esecuzione_id"]) == nuova
        e = esecuzione(db, vecchia)
        assert (e["stato"], e["cost_cents"], e["errore_codice"]) == (
            "interrotta", None, "interrotta")
        (uso,) = consumi(db)
        assert (uso["provider"], uso["service"], uso["outcome"], uso["cost_cents"]) == (
            "anthropic", "partner_call_posizioni", "timeout_unknown", 13)
        assert uso["request_meta"]["esecuzione_id"] == vecchia
        assert uso["request_meta"]["failsafe"] is True

    def test_azzera_l_errore_precedente(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c)
        concludi_job(db, c, eid, job_stato="errore", job_errore="output_non_valido",
                     stato="errore", cost=3)
        assert call(db, c)["ai_posizioni_errore"] == "output_non_valido"
        prenota_ai(db, owner, company, c)
        assert call(db, c)["ai_posizioni_errore"] is None

    def test_limite_per_call(self, db):
        owner, company = azienda_pronta(db)
        b = bando(db)
        c = crea(db, owner, company, bando_=b)["id"]
        for _ in range(2):
            eid = prenota_ai(db, owner, company, c, limite_call=2)
            concludi_job(db, c, eid)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c, limite_call=2)
        assert detail_of(exc) == "ai_limite_call"
        # L'altro servizio ha il suo conteggio; NULL = nessun limite.
        prenota_ai(db, owner, company, c, servizio="partner_call_testi", limite_call=2)
        prenota_ai(db, owner, company, c, limite_call=None)

    def test_limite_per_call_cosa_conta(self, db):
        """Non contano le esecuzioni chiuse senza LLM a costo 0; contano quelle con
        costo o token e quelle a costo ignoto."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c, limite_call=1)
        concludi_job(db, c, eid, job_stato="errore", job_errore="rete", stato="errore", cost=0,
                     input_tokens=0, output_tokens=0)
        eid = prenota_ai(db, owner, company, c, limite_call=1)  # la prima non conta
        concludi_job(db, c, eid, job_stato="errore", job_errore="timeout", stato="timeout",
                     cost=None, input_tokens=0, output_tokens=0)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c, limite_call=1)
        assert detail_of(exc) == "ai_limite_call"

    def test_limite_per_call_non_si_azzera_ricreando_la_bozza(self, db):
        owner, company = azienda_pronta(db)
        b = bando(db)
        c1 = crea(db, owner, company, bando_=b)["id"]
        concludi_job(db, c1, prenota_ai(db, owner, company, c1, limite_call=1))
        chiudi(db, owner, company, c1, "annullata")
        c2 = crea(db, owner, company, bando_=b)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c2, limite_call=1)
        assert detail_of(exc) == "ai_limite_call"
        # Un altro bando o un'altra azienda hanno il loro conteggio.
        c3 = crea(db, owner, company)["id"]
        prenota_ai(db, owner, company, c3, limite_call=1)
        _, altra = azienda_pronta(db, owner)
        c4 = crea(db, owner, altra, bando_=b)["id"]
        prenota_ai(db, owner, altra, c4, limite_call=1)

    def test_limite_per_owner(self, db):
        owner, company = azienda_pronta(db)
        _, altra = azienda_pronta(db, owner)
        c1 = crea(db, owner, company)["id"]
        c2 = crea(db, owner, altra)["id"]
        prenota_ai(db, owner, company, c1, limite_owner=1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, altra, c2, limite_owner=1)
        assert detail_of(exc) == "ai_limite_owner"
        assert call(db, c2)["ai_posizioni_stato"] is None

    def test_limite_per_owner_unico_su_tutti_i_servizi_call(self, db):
        """Il tetto per titolare vale su posizioni e testi INSIEME (non uno per
        servizio); non contano le esecuzioni senza LLM a costo 0 né quelle di
        altri servizi (bozza del profilo, WP4)."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c, limite_owner=2)
        concludi_job(db, c, eid, job_stato="errore", job_errore="rete", stato="errore", cost=0,
                     input_tokens=0, output_tokens=0)  # non conta
        concludi_job(db, c, prenota_ai(db, owner, company, c, limite_owner=2))
        db.execute(
            "insert into public.partenariati_ai_esecuzioni (servizio, origine, gruppo, owner_id,"
            " richiedente_user_id, giorno, costo_riservato_cents, stato, llm_eseguito,"
            " cost_cents, conclusa_at) values ('partner_profilo_ai', 'utente', 'altri', %s, %s,"
            " (now() at time zone 'Europe/Rome')::date, 1, 'conclusa', true, 1, now())",
            (owner, owner),
        )
        prenota_ai(db, owner, company, c, servizio="partner_call_testi", limite_owner=2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c, limite_owner=2)
        assert detail_of(exc) == "ai_limite_owner"
        _, altra = azienda_pronta(db, owner)
        c2 = crea(db, owner, altra)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, altra, c2, servizio="partner_call_testi", limite_owner=2)
        assert detail_of(exc) == "ai_limite_owner"
        prenota_ai(db, owner, altra, c2, limite_owner=None)  # NULL = nessun limite

    def test_budget_del_gruppo_altri(self, db):
        owner, company = azienda_pronta(db)
        c1 = crea(db, owner, company)["id"]
        c2 = crea(db, owner, company)["id"]
        prenota_ai(db, owner, company, c1, budget=15, riserva=10)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c2, budget=15, riserva=10)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert call(db, c2)["ai_posizioni_stato"] is None

    @pytest.mark.parametrize("servizio", ["partner_profilo_ai", "partenariato_estrazione",
                                          None, "partner_call"])
    def test_servizio_non_valido(self, db, servizio):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c, servizio=servizio)
        assert detail_of(exc) == "parametri_non_validi"

    def test_riserva_non_valida(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c, riserva=None)
        assert detail_of(exc) == "parametri_non_validi"
        assert call(db, c)["ai_posizioni_stato"] is None

    def test_solo_in_bozza(self, db):
        owner, company, c = pubblicata(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, company, c)
        assert detail_of(exc) == "stato_call_non_valido"
        assert conta(db, "partenariati_ai_esecuzioni") == 0

    def test_guardie(self, db):
        owner, a = azienda_pronta(db, plan_slug="advisor")
        _, b = azienda_pronta(db, owner)
        cb = crea(db, owner, b)["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, a, cb)
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner, b, cb, richiedente=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        assert conta(db, "partenariati_ai_esecuzioni") == 0

    def test_concorrenza_stessa_call(self, db):
        """Due prenotazioni concorrenti: la seconda attende il lock della call e poi
        trova il job in corso. Una sola esecuzione."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: prenota_ai(db, owner, company, c))
        try:
            prenota_ai(altra, owner, company, c)
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            if altra.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                altra.rollback()
            altra.close()
            monitor.close()
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito["errore"].diag.message_detail == "ai_in_corso", esito
        assert conta(db, "partenariati_ai_esecuzioni") == 1


class TestAiConcludi:
    def test_pronta_ed_esecuzione_insieme(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c)
        out = concludi_job(db, c, eid, proposta={"posizioni": [{"titolo": "P1"}]}, cost=9)
        assert out == {"job_scritto": True, "esecuzione_chiusa": True}
        riga = call(db, c)
        assert riga["ai_posizioni_stato"] == "pronta"
        assert riga["ai_posizioni_proposta"] == {"posizioni": [{"titolo": "P1"}]}
        e = esecuzione(db, eid)
        assert (e["stato"], e["cost_cents"], e["llm_eseguito"]) == ("conclusa", 9, True)
        # Seconda chiusura: nessun effetto.
        assert concludi_job(db, c, eid) == {"job_scritto": False, "esecuzione_chiusa": False}

    def test_testi(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c, servizio="partner_call_testi")
        concludi_job(db, c, eid, servizio="partner_call_testi", proposta={"titolo": "T"})
        riga = call(db, c)
        assert riga["ai_testi_stato"] == "pronta" and riga["ai_testi_proposta"] == {"titolo": "T"}
        assert riga["ai_posizioni_stato"] is None

    def test_errore_svuota_la_proposta(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        concludi_job(db, c, prenota_ai(db, owner, company, c))
        eid = prenota_ai(db, owner, company, c)
        concludi_job(db, c, eid, job_stato="errore", job_errore="output_non_valido",
                     stato="errore")
        riga = call(db, c)
        assert (riga["ai_posizioni_stato"], riga["ai_posizioni_proposta"],
                riga["ai_posizioni_errore"]) == ("errore", None, "output_non_valido")

    def test_job_superato_chiude_comunque_l_esecuzione(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        vecchia = prenota_ai(db, owner, company, c)
        db.execute("update public.partner_calls set ai_posizioni_avviata_at = now() - "
                   "interval '11 minutes' where id = %s", (c,))
        nuova = prenota_ai(db, owner, company, c)
        # La vecchia è già stata chiusa come interrotta dalla nuova prenotazione.
        assert concludi_job(db, c, vecchia) == {"job_scritto": False,
                                                "esecuzione_chiusa": False}
        assert str(call(db, c)["ai_posizioni_esecuzione_id"]) == nuova
        assert call(db, c)["ai_posizioni_stato"] == "in_corso"

    def test_servizio_diverso_nessun_effetto(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c)
        out = concludi_job(db, c, eid, servizio="partner_call_testi", proposta={"t": 1})
        assert out == {"job_scritto": False, "esecuzione_chiusa": False}
        assert esecuzione(db, eid)["stato"] == "in_corso"

    def test_call_cancellata_chiude_l_esecuzione(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c)
        db.execute("delete from public.company_profiles where id = %s", (company,))
        assert concludi_job(db, c, eid) == {"job_scritto": False, "esecuzione_chiusa": True}
        assert esecuzione(db, eid)["stato"] == "conclusa"

    def test_atomica(self, db):
        """Uno stato di chiusura non valido annulla anche la scrittura del job."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi_job(db, c, eid, stato="in_corso")
        assert detail_of(exc) == "stato_non_valido"
        assert call(db, c)["ai_posizioni_stato"] == "in_corso"
        assert esecuzione(db, eid)["stato"] == "in_corso"

    @pytest.mark.parametrize("campo", ["call", "esecuzione", "servizio", "job_stato",
                                       "proposta_non_oggetto", "proposta_mancante"])
    def test_parametri_non_validi(self, db, campo):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c)
        kwargs: dict = {}
        if campo == "call":
            c = None
        elif campo == "esecuzione":
            eid = None
        elif campo == "servizio":
            kwargs["servizio"] = "partner_profilo_ai"
        elif campo == "job_stato":
            kwargs["job_stato"] = "in_corso"
        elif campo == "proposta_non_oggetto":
            kwargs["proposta"] = ["x"]
        else:
            kwargs["proposta"] = None
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi_job(db, c, eid, **kwargs)
        assert detail_of(exc) == "parametri_non_validi"


class TestAiChiudiStale:
    def _in_corso(self, db, owner, company, call_id, minuti_fa, *,
                  servizio="partner_call_posizioni", riserva=10) -> str:
        eid = prenota_ai(db, owner, company, call_id, servizio=servizio, riserva=riserva)
        colonna = ("ai_posizioni_avviata_at" if servizio == "partner_call_posizioni"
                   else "ai_testi_avviata_at")
        db.execute(f"update public.partner_calls set {colonna} = now() - "
                   "make_interval(mins => %s) where id = %s", (minuti_fa, call_id))
        db.execute("update public.partenariati_ai_esecuzioni set avviata_at = now() - "
                   "make_interval(mins => %s) where id = %s", (minuti_fa, eid))
        return eid

    def test_chiude_solo_i_job_orfani(self, db):
        owner, company = azienda_pronta(db)
        vecchia = crea(db, owner, company)["id"]
        fresca = crea(db, owner, company)["id"]
        e_vecchia = self._in_corso(db, owner, company, vecchia, 11, riserva=23)
        e_testi = self._in_corso(db, owner, company, vecchia, 12,
                                 servizio="partner_call_testi", riserva=7)
        e_fresca = self._in_corso(db, owner, company, fresca, 2)
        db.execute("update public.partner_calls set ai_posizioni_proposta = '{\"a\": 1}' "
                   "where id = %s", (vecchia,))
        assert chiudi_stale(db, 10) == 2
        riga = call(db, vecchia)
        assert (riga["ai_posizioni_stato"], riga["ai_posizioni_errore"],
                riga["ai_posizioni_proposta"]) == ("errore", "interrotta", None)
        assert (riga["ai_testi_stato"], riga["ai_testi_errore"]) == ("errore", "interrotta")
        for eid in (e_vecchia, e_testi):
            e = esecuzione(db, eid)
            assert (e["stato"], e["cost_cents"]) == ("interrotta", None)
        assert call(db, fresca)["ai_posizioni_stato"] == "in_corso"
        assert esecuzione(db, e_fresca)["stato"] == "in_corso"
        usi = consumi(db)
        assert sorted((u["service"], u["outcome"], u["cost_cents"]) for u in usi) == [
            ("partner_call_posizioni", "timeout_unknown", 23),
            ("partner_call_testi", "timeout_unknown", 7)]
        assert all(str(u["user_id"]) == owner and str(u["family_parent_id"]) == owner
                   for u in usi)
        assert {u["request_meta"]["esecuzione_id"] for u in usi} == {e_vecchia, e_testi}
        assert chiudi_stale(db, 10) == 0
        assert len(consumi(db)) == 2

    def test_esecuzione_gia_chiusa_non_si_registra(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = self._in_corso(db, owner, company, c, 30)
        concludi_ai(db, eid, "conclusa", cost=7, input_tokens=100)
        assert chiudi_stale(db, 10) == 1
        assert call(db, c)["ai_posizioni_stato"] == "errore"
        assert esecuzione(db, eid)["cost_cents"] == 7
        assert consumi(db) == []

    def test_esecuzione_senza_job_in_corso(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = self._in_corso(db, owner, company, c, 30, riserva=12)
        db.execute("update public.partner_calls set ai_posizioni_stato = 'pronta' "
                   "where id = %s", (c,))
        assert chiudi_stale(db, 10) == 1
        assert esecuzione(db, eid)["stato"] == "interrotta"
        (uso,) = consumi(db)
        assert uso["cost_cents"] == 12
        assert call(db, c)["ai_posizioni_stato"] == "pronta"

    def test_esecuzione_orfana_dopo_la_cancellazione(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = self._in_corso(db, owner, company, c, 30)
        db.execute("delete from public.company_profiles where id = %s", (company,))
        assert chiudi_stale(db, 10) == 1
        assert esecuzione(db, eid)["stato"] == "interrotta"

    def test_esecuzione_orfana_recente_resta(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        eid = prenota_ai(db, owner, company, c)
        db.execute("delete from public.company_profiles where id = %s", (company,))
        assert chiudi_stale(db, 10) == 0
        assert esecuzione(db, eid)["stato"] == "in_corso"

    def test_altri_servizi_non_si_toccano(self, db):
        eid = str(db.execute(
            "select public.fn_partenariati_ai_prenota('partner_profilo_ai', 'utente', 'altri', "
            "1000, 10, null, null, null, null, null, null)").fetchone()[0])
        db.execute("update public.partenariati_ai_esecuzioni set avviata_at = now() - "
                   "interval '1 hour' where id = %s", (eid,))
        assert chiudi_stale(db, 10) == 0
        assert esecuzione(db, eid)["stato"] == "in_corso"
        assert db.execute("select public.fn_partner_call_ai_esecuzione_interrotta(%s)",
                          (eid,)).fetchone()[0] is False

    @pytest.mark.parametrize(("minuti", "chiusi"), [(None, 1), (5, 1), (20, 0), (0, 1)])
    def test_soglia(self, db, minuti, chiusi):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        self._in_corso(db, owner, company, c, 15)
        assert chiudi_stale(db, minuti) == chiusi

    def test_soglia_minima_protegge_le_appena_avviate(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        prenota_ai(db, owner, company, c)
        assert chiudi_stale(db, 0) == 0
        assert chiudi_stale(db, -5) == 0

    def test_salta_le_righe_bloccate(self, db):
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        self._in_corso(db, owner, company, c, 30)
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select 1 from public.partner_calls where id = %s for update", (c,))
            altra.execute("select 1 from public.partenariati_ai_esecuzioni for update")
            db.execute("set lock_timeout = '2s'")  # senza SKIP LOCKED: errore, non attesa
            assert chiudi_stale(db, 10) == 0
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        assert chiudi_stale(db, 10) == 1


# ---------------------------------------------------------------- snapshot


class TestSnapshot:
    def test_forma_e_periodo(self, db):
        owner, _ = azienda_pronta(db)
        s = snapshot(db, owner)
        inizio, fine = db.execute(
            "select date_trunc('month', now() at time zone 'Europe/Rome')::date, "
            "(date_trunc('month', now() at time zone 'Europe/Rome') + interval '1 month' "
            "- interval '1 day')::date").fetchone()
        assert s == {
            "call_attive": {"limite": 1, "usate": 0, "residuo": 1},
            "candidature_mese": {"limite": 5, "usate": 0, "residuo": 5,
                                 "periodo_inizio": inizio.isoformat(),
                                 "periodo_fine": fine.isoformat()},
        }

    def test_usate_contano_pubblicate_e_sospese_delle_aziende_vive(self, db):
        limite_piano(db, "pro", 3)
        owner, a = azienda_pronta(db, plan_slug="pro")
        pubblicata(db, owner, a)
        assert snapshot(db, owner)["call_attive"] == {"limite": 3, "usate": 1, "residuo": 2}
        _, b = azienda_pronta(db, owner)
        _, _, cb = pubblicata(db, owner, b)
        db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                   "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' where id = %s",
                   (cb,))
        crea(db, owner, a)  # le bozze non contano
        assert snapshot(db, owner)["call_attive"]["usate"] == 2
        _, c, cc = pubblicata(db, owner, azienda_pronta(db, owner)[1])
        chiudi(db, owner, c, cc, "completata")  # le chiuse non contano
        assert snapshot(db, owner)["call_attive"]["usate"] == 2
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (b,))
        assert snapshot(db, owner)["call_attive"] == {"limite": 3, "usate": 1, "residuo": 2}
        # Un altro owner non conta.
        pubblicata(db)
        assert snapshot(db, owner)["call_attive"]["usate"] == 1

    def test_illimitato(self, db):
        limite_piano(db, "advisor", None)
        db.execute("update public.subscription_plans set partner_candidature_mese = null "
                   "where slug = 'advisor'")
        owner, company = azienda_pronta(db, plan_slug="advisor")
        pubblicata(db, owner, company)
        s = snapshot(db, owner)
        assert s["call_attive"] == {"limite": None, "usate": 1, "residuo": None}
        assert (s["candidature_mese"]["limite"], s["candidature_mese"]["residuo"]) == (None, None)

    @pytest.mark.parametrize("caso", ["gratuito", "senza_abbonamento", "inesistente"])
    def test_senza_partenariati(self, db, caso):
        if caso == "gratuito":
            owner = new_user(db)
        elif caso == "senza_abbonamento":
            owner = new_user(db, invitato=True)
        else:
            owner = str(uuid.uuid4())
        s = snapshot(db, owner)
        assert s["call_attive"] == {"limite": 0, "usate": 0, "residuo": 0}
        assert (s["candidature_mese"]["limite"], s["candidature_mese"]["residuo"]) == (0, 0)

    def test_residuo_mai_negativo_dopo_un_downgrade(self, db):
        limite_piano(db, "pro", 3)
        owner, a = azienda_pronta(db, plan_slug="pro")
        pubblicata(db, owner, a)
        pubblicata(db, owner, a)  # due call della stessa azienda su bandi diversi
        switch_plan(db, owner, "smart")
        assert snapshot(db, owner)["call_attive"] == {"limite": 1, "usate": 2, "residuo": 0}

    def test_coerente_con_la_pubblicazione(self, db):
        owner, a, _ = pubblicata(db)
        assert snapshot(db, owner)["call_attive"]["residuo"] == 0
        _, b = azienda_pronta(db, owner)
        cb = call_pronta(db, owner, b)
        with pytest.raises(psycopg.errors.RaiseException):
            pubblica(db, owner, b, cb)

    def test_limiti_senza_la_chiave_fail_closed(self, db):
        """Se fn_partenariati_limiti non riportasse la chiave, niente call."""
        owner, company = azienda_pronta(db)
        db.execute(
            "create or replace function public.fn_partenariati_limiti(p_owner uuid) "
            "returns jsonb language sql stable security definer set search_path = public "
            "as $$ select '{\"piano_attivo\": true}'::jsonb $$")
        assert snapshot(db, owner)["call_attive"]["limite"] == 0
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, owner, company)
        assert detail_of(exc) == "piano_non_include_call"


# --------------------------------------------------- identità: equivalenza


def _esito_consenso(db, owner, company, *, anonimo, non_sandbox) -> str:
    """Esito di fn_partner_consenso (concedi) senza lasciare tracce."""
    try:
        with db.transaction(force_rollback=True):
            db.execute(
                "select public.fn_partner_consenso(%s::uuid, %s::uuid, %s::uuid, 'concedi', "
                "%s, 'pagina_azienda', %s::boolean, %s::boolean)",
                (owner, company, owner, VERSIONE, anonimo, non_sandbox))
        return "ok"
    except psycopg.errors.RaiseException as exc:
        return exc.diag.message_detail


SCENARI_IDENTITA = ["ok", "maiuscolo", "spazi", "cessata", "stato_nullo", "piva_diversa",
                    "senza_dati", "sandbox", "piva_cambiata", "in_liquidazione"]
SCENARI_RAPPRESENTANTE = ["ok", "cf_visura_minuscolo", "senza_cf",
                          "cf_non_verificato", "non_rappresentante", "cf_diverso",
                          "rappresentante_altra_azienda", "due_righe_una_buona"]


class TestIdentitaEquivalente:
    def _identita(self, db, caso):
        owner = new_user(db, "smart")
        company = make_company(db, owner)
        stato = {"maiuscolo": "ATTIVA", "spazi": "  attiva ", "cessata": "Cessata",
                 "stato_nullo": None, "in_liquidazione": "In liquidazione"}.get(caso, "Attiva")
        if caso != "senza_dati":
            importa(db, company, stato=stato, sandbox=caso == "sandbox",
                    piva="99999999999" if caso == "piva_diversa" else None)
        if caso == "piva_cambiata":
            db.execute("update public.company_profiles set partita_iva = '99999999998' "
                       "where id = %s", (company,))
        return owner, company

    @pytest.mark.parametrize("caso", SCENARI_IDENTITA)
    @pytest.mark.parametrize("non_sandbox", [True, False, None])
    def test_identita_come_fn_partner_consenso(self, db, caso, non_sandbox):
        owner, company = self._identita(db, caso)
        mia = db.execute("select public.fn_partenariato_identita_ok(%s, %s::boolean)",
                         (company, non_sandbox)).fetchone()[0]
        esito = _esito_consenso(db, owner, company, anonimo=True, non_sandbox=non_sandbox)
        assert esito in ("ok", "identita_non_verificata")
        assert mia is (esito == "ok")

    def test_casi_attesi(self, db):
        attese = {"ok": True, "maiuscolo": True, "spazi": True, "cessata": False,
                  "stato_nullo": False, "piva_diversa": False, "senza_dati": False,
                  "sandbox": False, "piva_cambiata": False, "in_liquidazione": False}
        for caso, atteso in attese.items():
            _, company = self._identita(db, caso)
            assert db.execute("select public.fn_partenariato_identita_ok(%s, null)",
                              (company,)).fetchone()[0] is atteso, caso

    def _rappresentante(self, db, caso):
        owner, company = azienda_pronta(db, plan_slug="advisor")
        if caso not in ("senza_cf",):
            imposta_cf(db, owner, verificato=caso != "cf_non_verificato")
        if caso == "cf_visura_minuscolo":
            legale(db, company, " rssmra80a01h501u ")
        elif caso == "non_rappresentante":
            legale(db, company, CF_TITOLARE, rappresentante=False)
        elif caso == "cf_diverso":
            legale(db, company, "BNCLGU70B02F205X")
        elif caso == "rappresentante_altra_azienda":
            legale(db, make_company(db, owner), CF_TITOLARE)
        elif caso == "due_righe_una_buona":
            legale(db, company, "BNCLGU70B02F205X")
            legale(db, company, CF_TITOLARE)
        else:
            legale(db, company, CF_TITOLARE)
        return owner, company

    @pytest.mark.parametrize("caso", SCENARI_RAPPRESENTANTE)
    def test_rappresentante_come_fn_partner_consenso(self, db, caso):
        owner, company = self._rappresentante(db, caso)
        mia = db.execute("select public.fn_partenariato_rappresentante_ok(%s, %s)",
                         (owner, company)).fetchone()[0]
        esito = _esito_consenso(db, owner, company, anonimo=False, non_sandbox=True)
        assert esito in ("ok", "rappresentante_non_verificato")
        assert mia is (esito == "ok")
        # Dalla 0041 il CF non basta più in nessun caso: serve l'identità
        # verificata dall'admin (l'equivalenza con fn_partner_consenso resta).
        assert mia is False

    def test_rappresentante_di_un_altro_owner(self, db):
        owner, company = self._rappresentante(db, "ok")
        verifica_identita_admin(db, owner, company)  # dalla 0041
        altro = new_user(db)
        imposta_cf(db, altro)  # stesso CF, ma non è il titolare indicato
        assert db.execute("select public.fn_partenariato_rappresentante_ok(%s, %s)",
                          (owner, company)).fetchone()[0] is True
        assert db.execute("select public.fn_partenariato_rappresentante_ok(%s, %s)",
                          (str(uuid.uuid4()), company)).fetchone()[0] is False


# ----------------------------------------------------------------- cascade


class TestCascade:
    def test_hard_delete_dell_azienda(self, db):
        owner, company, c = pubblicata(db)
        aggiorna(db, owner, company, c, {"descrizione_pubblica": "Seconda versione"})
        eid = str(db.execute(
            "select public.fn_partenariati_ai_prenota('partner_call_testi', 'call', 'altri', "
            "1000, 10, null, null, null, null, %s, null)", (company,)).fetchone()[0])
        db.execute("insert into public.partner_segnalazioni (oggetto_tipo, oggetto_id, "
                   "segnalante_user_id, motivo, descrizione, buona_fede, contenuto_snapshot) "
                   "values ('call', %s, gen_random_uuid(), 'altro', 'Segnalazione di prova', "
                   "true, '{}'::jsonb)", (c,))
        assert conta(db, "partner_call_versioni") == 2
        db.execute("delete from public.company_profiles where id = %s", (company,))
        for tabella in ("partner_calls", "partner_call_requisiti", "partner_call_posizioni",
                        "partner_call_versioni"):
            assert conta(db, tabella) == 0, tabella
        assert conta(db, "partner_segnalazioni") == 1
        assert esecuzione(db, eid) is not None
        assert len(audit(db, "partenariato.call_pubblicata")) == 1

    def test_delete_della_call(self, db):
        owner, company, c = pubblicata(db)
        db.execute("delete from public.partner_calls where id = %s", (c,))
        for tabella in ("partner_call_requisiti", "partner_call_posizioni",
                        "partner_call_versioni"):
            assert conta(db, tabella) == 0, tabella

    def test_delete_dell_owner(self, db):
        owner, company, c = pubblicata(db)
        db.execute("delete from auth.users where id = %s", (owner,))
        db.execute("delete from public.profiles where id = %s", (owner,))
        assert conta(db, "partner_calls") == 0


# -------------------------------------------------------------------- lock


def _chiamate(db, owner, company, call_id):
    """Una chiamata per ogni RPC che agisce su una call (tutte bloccano l'owner)."""
    return {
        "aggiorna": lambda: aggiorna(db, owner, company, call_id, {"wizard_passo": 3}),
        "conferma": lambda: conferma(db, owner, company, call_id),
        "requisiti": lambda: requisiti(db, owner, company, call_id, [REQ_A]),
        "posizioni": lambda: posizioni(db, owner, company, call_id, [POS_1]),
        "pubblica": lambda: pubblica(db, owner, company, call_id),
        "chiudi": lambda: chiudi(db, owner, company, call_id, "annullata"),
        "chiudi_auto": lambda: chiudi_auto(db, call_id, "chiusa_annullata", "bando_revocato"),
        "ai_prenota": lambda: prenota_ai(db, owner, company, call_id),
        "crea": lambda: crea(db, owner, company),
    }


class TestLock:
    @pytest.mark.parametrize("rpc", ["aggiorna", "conferma", "requisiti", "posizioni",
                                     "pubblica", "chiudi", "chiudi_auto", "ai_prenota", "crea"])
    def test_lock_owner_con_seconda_connessione(self, db, rpc):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select 1 from public.profiles where id = %s for update", (owner,))
            db.execute("set lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                _chiamate(db, owner, company, c)[rpc]()
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        _chiamate(db, owner, company, c)[rpc]()

    @pytest.mark.parametrize("rpc", ["aggiorna", "pubblica", "chiudi", "chiudi_auto",
                                     "ai_prenota", "crea"])
    def test_owner_prima_dell_azienda(self, db, rpc):
        """Con l'azienda bloccata da un'altra transazione la RPC attende tenendo già
        il lock dell'owner (ordine owner → azienda)."""
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(_chiamate(db, owner, company, c)[rpc])
        try:
            altra.execute("select 1 from public.company_profiles where id = %s for update",
                          (company,))
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                terza.execute("select 1 from public.profiles where id = %s for update nowait",
                              (owner,))
            altra.rollback()
            thread.join(timeout=10)
        finally:
            if altra.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                altra.rollback()
            altra.close()
            terza.close()
            if thread.is_alive():
                thread.join(timeout=10)
        assert "errore" not in esito, esito

    @pytest.mark.parametrize("rpc", ["aggiorna", "requisiti", "pubblica", "chiudi",
                                     "chiudi_auto", "ai_prenota"])
    def test_azienda_prima_della_call(self, db, rpc):
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(_chiamate(db, owner, company, c)[rpc])
        try:
            altra.execute("select 1 from public.partner_calls where id = %s for update", (c,))
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                terza.execute("select 1 from public.company_profiles where id = %s "
                              "for no key update nowait", (company,))
            altra.rollback()
            thread.join(timeout=10)
        finally:
            if altra.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                altra.rollback()
            altra.close()
            terza.close()
            if thread.is_alive():
                thread.join(timeout=10)
        assert "errore" not in esito, esito

    def test_budget_per_ultimo(self, db):
        """Con il lock del budget occupato la prenotazione attende tenendo già il lock
        della call (ordine call → budget)."""
        owner, company = azienda_pronta(db)
        c = crea(db, owner, company)["id"]
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: prenota_ai(db, owner, company, c))
        try:
            altra.execute("select pg_advisory_xact_lock(hashtext('partenariati_ai_budget'))")
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                terza.execute("select 1 from public.partner_calls where id = %s "
                              "for update nowait", (c,))
            altra.rollback()
            thread.join(timeout=10)
        finally:
            if altra.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                altra.rollback()
            altra.close()
            terza.close()
            if thread.is_alive():
                thread.join(timeout=10)
        assert "out" in esito, esito

    def test_lock_azienda_non_blocca_gli_insert_con_fk(self, db):
        """FOR NO KEY UPDATE sulla riga azienda: serializza con gli UPDATE
        dell'azienda ma non blocca gli insert delle tabelle figlie (FK)."""
        owner, company = azienda_pronta(db)
        c = call_pronta(db, owner, company)
        altra = psycopg.connect(db.info.dsn)
        try:
            pubblica(altra, owner, company, c)
            db.execute("set lock_timeout = '500ms'")
            legale(db, company, "BNCLGU70B02F205X")  # FK verso company_profiles: passa
            with pytest.raises(psycopg.errors.LockNotAvailable):
                db.execute("update public.company_profiles set ragione_sociale = 'Altra Srl' "
                           "where id = %s", (company,))
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()


# ---------------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0037, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0037, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")


class TestSicurezza0037:
    def test_inventario_del_file(self):
        # Se la migration crea altro, i test sotto devono coprirlo.
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == FUNZIONI_NUOVE
        assert not re.search(r"^create function", SQL_0037, re.M)

    def test_additiva(self):
        """Nessuna funzione o tabella esistente ridefinita, modificata o eliminata."""
        eseguibile = "\n".join(r for r in SQL_0037.splitlines()
                               if not r.lstrip().startswith("--"))
        assert not re.search(r"^\s*(drop|alter function)\b", eseguibile, re.M | re.I)
        assert set(re.findall(r"^alter table public\.(\w+)", eseguibile, re.M)) == TABELLE_NUOVE
        esistenti = {"fn_partner_consenso", "fn_partenariati_ai_prenota",
                     "fn_partenariati_ai_concludi", "fn_partenariati_limiti",
                     "fn_entitlement_detail", "fn_create_company", "set_updated_at"}
        assert not (FUNZIONI_NEL_FILE & esistenti)

    def test_trigger(self, db):
        trigger = {(r[0], r[1]) for r in db.execute(
            "select tgname, tgrelid::regclass::text from pg_trigger where not tgisinternal "
            "and tgrelid in ('public.partner_calls'::regclass, "
            "'public.partner_call_requisiti'::regclass, 'public.partner_call_posizioni'::regclass, "
            "'public.partner_call_versioni'::regclass, 'public.partner_segnalazioni'::regclass)"
        ).fetchall()}
        assert trigger == {
            ("trg_partner_calls_updated_at", "partner_calls"),
            ("trg_partner_calls_chiudi_candidature", "partner_calls"),  # della 0039
            ("trg_pcr_updated_at", "partner_call_requisiti"),
            ("trg_pcp_updated_at", "partner_call_posizioni"),
            ("trg_pcv_immutabile", "partner_call_versioni"),
            ("trg_partner_segnalazioni_updated_at", "partner_segnalazioni"),
        }

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
        """Nel harness anon/authenticated non hanno grant di default sulle
        tabelle (in Supabase sì): la revoca esplicita va verificata sul file."""
        assert re.search(
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0037, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0037, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration e ogni fn_partner_call% /
        fn_partenariat% presente nel DB è SECURITY DEFINER con search_path
        fissato, non eseguibile dai client (PUBLIC compreso) ed esiste in una sola
        firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public'
                  and (p.proname like 'fn\_partner\_call%%'
                       or p.proname like 'fn\_partenariat%%')"""
        ).fetchall()}
        assert FUNZIONI_NUOVE <= dal_db
        nomi = FUNZIONI_NEL_FILE | FUNZIONI_NUOVE | dal_db
        for nome in sorted(nomi):
            righe_ = db.execute(
                """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                          coalesce(p.proacl::text, '')
                   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and p.proname = %s""",
                (nome,),
            ).fetchall()
            assert len(righe_) == 1, f"{nome}: {len(righe_)} firme"
            oid, secdef, config, acl = righe_[0]
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
                SQL_0037, re.M,
            ), nome

    @pytest.mark.parametrize("chiamata", [
        "select public.fn_partner_call_ai_chiudi_stale(10)",
        "select public.fn_partenariati_snapshot(gen_random_uuid())",
        "select public.fn_partenariato_identita_ok(gen_random_uuid(), true)",
        "select public.fn_partner_call_chiudi_auto(gen_random_uuid(), 'scaduta', "
        "'scadenza_call')",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, chiamata):
        """Prova diretta: con il ruolo authenticated la chiamata fallisce."""
        db.execute("set role authenticated")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_i_trigger_scattano_senza_execute(self, db):
        """Le revoche sulle funzioni trigger non fermano i trigger: un ruolo con i
        privilegi di tabella ma senza EXECUTE li attiva comunque."""
        owner, company, c = pubblicata(db)
        ruolo = f"scrittore_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin bypassrls")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            db.execute(f"grant select, update on public.partner_call_versioni to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                with pytest.raises(psycopg.errors.RaiseException) as exc:
                    db.execute("update public.partner_call_versioni set versione = 9")
                assert detail_of(exc) == "versione_immutabile"
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
