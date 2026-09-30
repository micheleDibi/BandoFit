"""Test funzionali della migration 0043 (rimappatura dei bandi fusi: fase c del
contratto del DB bandi, §6.2).

Coprono: fn_bandi_in_uso (un solo array, mai NULL, con l'unione distinta e
ordinata di preferiti, scadenze in calendario e call negli stati attivi);
fn_rimappa_bando_fuso con le regole per tabella (saved_bandi aggiornata o
eliminata, calendar_events aggiornato, eliminato o convertito in evento
personale secondo le note, partner_calls aggiornata o lasciata in collisione),
l'ambito utente × azienda con NULL = NULL, le tabelle che non si toccano, le
voci di audit_log con i valori precedenti e il ripristino a mano che ne deriva,
la prova che non scrive, l'idempotenza, il lock advisory tra due passi,
l'atomicità con un inserimento concorrente, i parametri, le firme e le revoche
ai client.
Ogni test riceve un database fresco clonato dal template.
"""

import itertools
import re
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SQL_0043 = (MIGRAZIONI / "0043_rimappatura_fusi.sql").read_text(encoding="utf-8")

FIRME = {
    "fn_bandi_in_uso": "fn_bandi_in_uso()",
    "fn_rimappa_bando_fuso": "fn_rimappa_bando_fuso(integer,integer,text,boolean)",
}
RITORNI = {"fn_bandi_in_uso": "integer[]", "fn_rimappa_bando_fuso": "jsonb"}
AZIONE = "catalogo.rimappatura_fuso"

DOPPIONE = 7001
MASTER = 7000
ALTRO_BANDO = 7999
SLUG_DOPPIONE = "bando-doppione"
SLUG_MASTER = "bando-master"

TABELLE_RIMAPPATE = ("saved_bandi", "calendar_events", "partner_calls")
# Contratto §4: non si toccano MAI (più tutte le altre tabelle del primario).
TABELLE_ESCLUSE = ("bando_alert_sends", "ai_checks", "bando_requirements",
                   "consultation_requests", "partenariati_ai_esecuzioni",
                   "bando_partenariato", "api_usage_events", "purchases",
                   "partner_call_versioni")
STATI_ATTIVI = ("bozza", "pubblicata", "sospesa_moderazione")
STATI_CHIUSI = ("chiusa_completata", "chiusa_annullata", "scaduta")

_ASSENTE = object()
_seq = itertools.count(1)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def conteggi(sb_agg=0, sb_eli=0, ce_agg=0, ce_eli=0, ce_con=0, pc_agg=0, pc_col=0) -> dict:
    return {
        "saved_bandi": {"aggiornate": sb_agg, "eliminate": sb_eli},
        "calendar_events": {"aggiornati": ce_agg, "eliminati": ce_eli, "convertiti": ce_con},
        "partner_calls": {"aggiornate": pc_agg, "in_collisione": pc_col},
    }


def new_user(db, plan_slug: str | None = None) -> str:
    uid = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@test.it"))
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
        (owner, f"ACME {i} Srl", f"{i + 43000000:011d}"),
    ).fetchone()[0])


def azienda(db) -> tuple[str, str]:
    owner = new_user(db, "smart")
    return owner, make_company(db, owner)


def salva(db, user: str, bando_id: int, company: str | None = None) -> str:
    return str(db.execute(
        "insert into public.saved_bandi (user_id, company_profile_id, bando_id, bando_slug, "
        "bando_titolo, data_scadenza, stato_bando) "
        "values (%s, %s, %s, %s, %s, current_date + 20, 'aperto') returning id",
        (user, company, bando_id, f"bando-{bando_id}", f"Titolo del bando {bando_id}"),
    ).fetchone()[0])


def evento(db, user: str, bando_id: int, company: str | None = None,
           note: str | None = None, *, orari: bool = False) -> str:
    return str(db.execute(
        "insert into public.calendar_events (user_id, company_profile_id, titolo, data, "
        "tutto_il_giorno, ora_inizio, ora_fine, note, tipo, bando_id, bando_slug) "
        "values (%s, %s, %s, current_date + 20, %s, %s, %s, %s, 'bando', %s, %s) returning id",
        (user, company, f"Scadenza del bando {bando_id}", not orari,
         "09:00" if orari else None, "10:30" if orari else None, note, bando_id,
         f"bando-{bando_id}"),
    ).fetchone()[0])


def evento_personale(db, user: str, company: str | None = None) -> str:
    return str(db.execute(
        "insert into public.calendar_events (user_id, company_profile_id, titolo, data, note) "
        "values (%s, %s, 'Riunione', current_date + 3, 'Portare i bilanci') returning id",
        (user, company),
    ).fetchone()[0])


def inserisci_call(db, company: str, bando_id: int, *, stato: str = "pubblicata",
                   mancante_dal: bool = False) -> str:
    """Call scritta direttamente (come dopo le RPC del WP5) nello stato richiesto."""
    owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                           (company,)).fetchone()[0])
    pubblicata_at = stato in ("pubblicata", "chiusa_completata", "scaduta",
                              "sospesa_moderazione")
    motivo = {"chiusa_completata": "creatore_completata",
              "chiusa_annullata": "creatore_annullata",
              "scaduta": "scadenza_call"}.get(stato)
    return str(db.execute(
        """insert into public.partner_calls
             (company_profile_id, family_parent_id, creato_da, bando_id, bando_slug,
              bando_titolo, ruolo_creatore, titolo, descrizione_pubblica,
              scadenza_call, regole_partenariato, regole_confermate_at, visibilita, stato,
              pubblicata_at, chiusa_at, motivo_chiusura, sospesa_at, stato_prima_sospensione,
              bando_mancante_dal)
           values (%s, %s, %s, %s, %s, 'Bando di prova', 'capofila',
                   'Cerchiamo un organismo di ricerca', 'Progetto di ricerca industriale.',
                   current_date + 30, '{}'::jsonb, now(), 'pubblica', %s,
                   case when %s then now() end, case when %s::text is not null then now() end,
                   %s, case when %s then now() end, case when %s then 'pubblicata' end,
                   case when %s then current_date - 2 end)
           returning id""",
        (company, owner, owner, bando_id, f"bando-{bando_id}", stato, pubblicata_at, motivo,
         motivo, stato == "sospesa_moderazione", stato == "sospesa_moderazione",
         mancante_dal),
    ).fetchone()[0])


def rimappa(db, doppione=DOPPIONE, master=MASTER, slug=SLUG_MASTER, prova=_ASSENTE) -> dict:
    """Chiamata per nome come PostgREST; senza `prova` usa il default."""
    if prova is _ASSENTE:
        return db.execute(
            "select public.fn_rimappa_bando_fuso(p_doppione => %s::integer, "
            "p_master => %s::integer, p_master_slug => %s::text)",
            (doppione, master, slug),
        ).fetchone()[0]
    return db.execute(
        "select public.fn_rimappa_bando_fuso(p_doppione => %s::integer, "
        "p_master => %s::integer, p_master_slug => %s::text, p_prova => %s::boolean)",
        (doppione, master, slug, prova),
    ).fetchone()[0]


def bandi_in_uso(db) -> list[int]:
    """Un solo valore (array), come lo restituisce la RPC via PostgREST."""
    righe_ = db.execute("select public.fn_bandi_in_uso()").fetchall()
    assert len(righe_) == 1
    return righe_[0][0]


def riga(db, tabella: str, id_) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            sql.SQL("select * from public.{} where id = %s").format(sql.Identifier(tabella)),
            (id_,),
        ).fetchone()


def voci_audit(db) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select actor_id, target_user_id, family_parent_id, payload from public.audit_log "
            "where action = %s order by id", (AZIONE,),
        ).fetchall()


def impronta(db, tabelle) -> dict:
    """md5 del contenuto di ciascuna tabella (ordine canonico delle righe)."""
    out = {}
    for tabella in tabelle:
        out[tabella] = db.execute(
            sql.SQL("select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) "
                    "from public.{} t").format(sql.Identifier(tabella))
        ).fetchone()[0]
    return out


def tabelle_public(db) -> list[str]:
    return [r[0] for r in db.execute(
        "select table_name::text from information_schema.tables "
        "where table_schema = 'public' and table_type = 'BASE TABLE' order by 1"
    ).fetchall()]


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
    """Scenario completo sul doppione:
    - u1 (senza azienda): preferito ed evento del doppione, nessun master → aggiornati;
    - u2 (senza azienda): preferito ed evento sia del doppione sia del master → eliminati;
    - u3 (azienda A): preferito del doppione in A e del master fuori azienda → aggiornato;
      evento del doppione con note ed evento del master, entrambi in A → convertito;
    - call di A sul doppione (pubblicata, bando mancante) → aggiornata;
    - call di B sul doppione (bozza) con una call pubblicata di B sul master → in collisione;
    - call di C sul doppione chiusa → intatta;
    - righe di un altro bando e un evento personale → intatti.
    """
    u1, u2 = new_user(db), new_user(db)
    u3, a = azienda(db)
    b_owner, b = azienda(db)
    c_owner, c = azienda(db)
    ns = SimpleNamespace(u1=u1, u2=u2, u3=u3, a=a, b=b, c=c, b_owner=b_owner, c_owner=c_owner)
    ns.sb_u1 = salva(db, u1, DOPPIONE)
    ns.sb_u2 = salva(db, u2, DOPPIONE)
    ns.sb_u2_master = salva(db, u2, MASTER)
    ns.sb_u3 = salva(db, u3, DOPPIONE, a)
    ns.sb_u3_master = salva(db, u3, MASTER)
    ns.sb_altro = salva(db, u1, ALTRO_BANDO)
    ns.ce_u1 = evento(db, u1, DOPPIONE)
    ns.ce_u2 = evento(db, u2, DOPPIONE)
    ns.ce_u2_master = evento(db, u2, MASTER)
    ns.ce_u3 = evento(db, u3, DOPPIONE, a, note="Chiedere al commercialista", orari=True)
    ns.ce_u3_master = evento(db, u3, MASTER, a)
    ns.ce_altro = evento(db, u1, ALTRO_BANDO)
    ns.ce_personale = evento_personale(db, u1)
    ns.pc_a = inserisci_call(db, a, DOPPIONE, mancante_dal=True)
    ns.pc_b = inserisci_call(db, b, DOPPIONE, stato="bozza")
    ns.pc_b_master = inserisci_call(db, b, MASTER)
    ns.pc_c = inserisci_call(db, c, DOPPIONE, stato="chiusa_completata")
    ns.atteso = conteggi(sb_agg=2, sb_eli=1, ce_agg=1, ce_eli=1, ce_con=1, pc_agg=1, pc_col=1)
    return ns


# ----------------------------------------------------------------- fn_bandi_in_uso


class TestBandiInUso:
    def test_vuoto_e_array_vuoto_non_null(self, db):
        assert db.execute("select public.fn_bandi_in_uso() = '{}'::integer[]").fetchone()[0]
        assert bandi_in_uso(db) == []

    def test_oltre_mille_id_in_un_solo_valore(self, db):
        """Il max-rows di PostgREST tronca le righe, non un array: 1500 id restano
        in un unico valore."""
        u = new_user(db)
        db.execute(
            "insert into public.saved_bandi (user_id, bando_id, bando_slug, bando_titolo) "
            "select %s, g, 'bando-' || g, 'Titolo ' || g from generate_series(1500, 1, -1) g",
            (u,),
        )
        assert bandi_in_uso(db) == list(range(1, 1501))

    def test_unione_distinta_e_ordinata(self, db):
        u = new_user(db)
        owner, a = azienda(db)
        salva(db, u, 30)
        salva(db, u, 10)
        salva(db, owner, 10, a)
        evento(db, u, 20)
        evento(db, owner, 10, a)
        evento_personale(db, u)
        for bando_id, stato in ((40, "bozza"), (50, "pubblicata"), (60, "sospesa_moderazione"),
                                (70, "chiusa_completata"), (80, "chiusa_annullata"),
                                (90, "scaduta"), (30, "scaduta")):
            inserisci_call(db, a, bando_id, stato=stato)
        assert bandi_in_uso(db) == [10, 20, 30, 40, 50, 60]

    def test_scenario(self, db, sc):
        assert bandi_in_uso(db) == [MASTER, DOPPIONE, ALTRO_BANDO]
        rimappa(db)
        # Resta solo la call in collisione sul doppione.
        assert bandi_in_uso(db) == [MASTER, DOPPIONE, ALTRO_BANDO]
        db.execute("update public.partner_calls set stato = 'chiusa_annullata', "
                   "chiusa_at = now(), motivo_chiusura = 'creatore_annullata' where id = %s",
                   (sc.pc_b,))
        assert bandi_in_uso(db) == [MASTER, ALTRO_BANDO]


# ----------------------------------------------------------------- parametri e ritorno


class TestParametri:
    @pytest.mark.parametrize("doppione,master,slug,prova", [
        (DOPPIONE, DOPPIONE, SLUG_MASTER, False),
        (0, MASTER, SLUG_MASTER, False),
        (DOPPIONE, 0, SLUG_MASTER, False),
        (-1, MASTER, SLUG_MASTER, False),
        (DOPPIONE, -5, SLUG_MASTER, False),
        (None, MASTER, SLUG_MASTER, False),
        (DOPPIONE, None, SLUG_MASTER, False),
        (DOPPIONE, MASTER, None, False),
        (DOPPIONE, MASTER, "", False),
        (DOPPIONE, MASTER, "   ", False),
        (DOPPIONE, MASTER, "x" * 301, False),
        (DOPPIONE, MASTER, SLUG_MASTER, None),
    ])
    def test_parametri_non_validi(self, db, sc, doppione, master, slug, prova):
        prima = impronta(db, TABELLE_RIMAPPATE + ("audit_log",))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            rimappa(db, doppione, master, slug, prova)
        assert detail_of(exc) == "parametri_non_validi"
        assert impronta(db, TABELLE_RIMAPPATE + ("audit_log",)) == prima

    def test_slug_di_300_caratteri(self, db, sc):
        slug = "s" * 300
        assert rimappa(db, slug=slug) == sc.atteso
        assert riga(db, "partner_calls", sc.pc_a)["bando_slug"] == slug

    def test_forma_del_ritorno_e_default_scrive(self, db, sc):
        """Senza p_prova (default false) scrive; il jsonb ha esattamente le chiavi
        del contratto."""
        esito = rimappa(db)
        assert esito == sc.atteso
        assert set(esito) == {"saved_bandi", "calendar_events", "partner_calls"}
        assert riga(db, "saved_bandi", sc.sb_u1)["bando_id"] == MASTER

    def test_bando_senza_righe(self, db, sc):
        assert rimappa(db, doppione=12345) == conteggi()
        assert voci_audit(db) == []


# ----------------------------------------------------------------- saved_bandi


class TestSavedBandi:
    def test_aggiornata_senza_collisione_con_snapshot_invariato(self, db):
        u = new_user(db)
        sid = salva(db, u, DOPPIONE)
        prima = riga(db, "saved_bandi", sid)
        assert rimappa(db) == conteggi(sb_agg=1)
        dopo = riga(db, "saved_bandi", sid)
        assert (dopo["bando_id"], dopo["bando_slug"]) == (MASTER, SLUG_MASTER)
        for campo in ("id", "user_id", "company_profile_id", "bando_titolo", "data_scadenza",
                      "stato_bando", "created_at"):
            assert dopo[campo] == prima[campo], campo

    @pytest.mark.parametrize("con_azienda", [False, True])
    def test_eliminata_con_il_master_nello_stesso_ambito(self, db, con_azienda):
        """NULLS NOT DISTINCT: senza azienda l'ambito è l'utente."""
        owner, a = azienda(db)
        company = a if con_azienda else None
        sid = salva(db, owner, DOPPIONE, company)
        master = salva(db, owner, MASTER, company)
        prima_master = riga(db, "saved_bandi", master)
        assert rimappa(db) == conteggi(sb_eli=1)
        assert riga(db, "saved_bandi", sid) is None
        assert riga(db, "saved_bandi", master) == prima_master

    @pytest.mark.parametrize("caso", [
        "master_senza_azienda", "doppione_senza_azienda", "altra_azienda", "altro_utente",
    ])
    def test_aggiornata_se_il_master_e_in_un_altro_ambito(self, db, caso):
        owner, a = azienda(db)
        b = make_company(db, owner)
        altro = new_user(db)
        doppione_in, master_di, master_in = {
            "master_senza_azienda": (a, owner, None),
            "doppione_senza_azienda": (None, owner, a),
            "altra_azienda": (a, owner, b),
            "altro_utente": (None, altro, None),
        }[caso]
        sid = salva(db, owner, DOPPIONE, doppione_in)
        master = salva(db, master_di, MASTER, master_in)
        assert rimappa(db) == conteggi(sb_agg=1)
        assert riga(db, "saved_bandi", sid)["bando_id"] == MASTER
        assert riga(db, "saved_bandi", master)["bando_id"] == MASTER


# ----------------------------------------------------------------- calendar_events


class TestCalendario:
    def test_aggiornato_senza_collisione(self, db):
        u = new_user(db)
        eid = evento(db, u, DOPPIONE, note="Promemoria", orari=True)
        prima = riga(db, "calendar_events", eid)
        assert rimappa(db) == conteggi(ce_agg=1)
        dopo = riga(db, "calendar_events", eid)
        assert (dopo["tipo"], dopo["bando_id"], dopo["bando_slug"]) == (
            "bando", MASTER, SLUG_MASTER)
        for campo in ("user_id", "company_profile_id", "titolo", "data", "tutto_il_giorno",
                      "ora_inizio", "ora_fine", "note", "created_at"):
            assert dopo[campo] == prima[campo], campo

    @pytest.mark.parametrize("note", [None, "", "  \n\t "])
    def test_eliminato_in_collisione_senza_note(self, db, note):
        u = new_user(db)
        eid = evento(db, u, DOPPIONE, note=note)
        master = evento(db, u, MASTER)
        prima_master = riga(db, "calendar_events", master)
        assert rimappa(db) == conteggi(ce_eli=1)
        assert riga(db, "calendar_events", eid) is None
        assert riga(db, "calendar_events", master) == prima_master

    @pytest.mark.parametrize("con_azienda", [False, True])
    def test_convertito_in_personale_con_note(self, db, con_azienda):
        owner, a = azienda(db)
        company = a if con_azienda else None
        eid = evento(db, owner, DOPPIONE, company, note="Chiamare l'ente", orari=True)
        master = evento(db, owner, MASTER, company)
        prima = riga(db, "calendar_events", eid)
        prima_master = riga(db, "calendar_events", master)
        assert rimappa(db) == conteggi(ce_con=1)
        dopo = riga(db, "calendar_events", eid)
        assert (dopo["tipo"], dopo["bando_id"], dopo["bando_slug"]) == ("personale", None, None)
        for campo in ("id", "user_id", "company_profile_id", "titolo", "data",
                      "tutto_il_giorno", "ora_inizio", "ora_fine", "note", "created_at"):
            assert dopo[campo] == prima[campo], campo
        assert riga(db, "calendar_events", master) == prima_master

    @pytest.mark.parametrize("caso", ["master_senza_azienda", "altra_azienda", "altro_utente"])
    def test_collisione_solo_nello_stesso_ambito(self, db, caso):
        owner, a = azienda(db)
        b = make_company(db, owner)
        altro = new_user(db)
        master_di, master_in = {
            "master_senza_azienda": (owner, None),
            "altra_azienda": (owner, b),
            "altro_utente": (altro, None),
        }[caso]
        eid = evento(db, owner, DOPPIONE, a)
        evento(db, master_di, MASTER, master_in)
        assert rimappa(db) == conteggi(ce_agg=1)
        assert riga(db, "calendar_events", eid)["bando_id"] == MASTER

    def test_eventi_personali_intatti(self, db):
        u = new_user(db)
        pid = evento_personale(db, u)
        prima = riga(db, "calendar_events", pid)
        assert rimappa(db) == conteggi()
        assert riga(db, "calendar_events", pid) == prima


# ----------------------------------------------------------------- partner_calls


class TestCall:
    @pytest.mark.parametrize("stato", STATI_ATTIVI)
    def test_aggiornata_negli_stati_attivi(self, db, stato):
        _, a = azienda(db)
        cid = inserisci_call(db, a, DOPPIONE, stato=stato, mancante_dal=True)
        prima = riga(db, "partner_calls", cid)
        assert prima["bando_mancante_dal"] is not None
        assert rimappa(db) == conteggi(pc_agg=1)
        dopo = riga(db, "partner_calls", cid)
        assert (dopo["bando_id"], dopo["bando_slug"], dopo["bando_mancante_dal"]) == (
            MASTER, SLUG_MASTER, None)
        diversi = {k for k in prima if prima[k] != dopo[k]}
        assert diversi == {"bando_id", "bando_slug", "bando_mancante_dal", "updated_at"}

    @pytest.mark.parametrize("stato_master", STATI_ATTIVI)
    def test_in_collisione_nessuna_modifica(self, db, stato_master):
        owner, a = azienda(db)
        cid = inserisci_call(db, a, DOPPIONE, mancante_dal=True)
        master = inserisci_call(db, a, MASTER, stato=stato_master)
        prima = riga(db, "partner_calls", cid)
        prima_master = riga(db, "partner_calls", master)
        assert rimappa(db) == conteggi(pc_col=1)
        assert riga(db, "partner_calls", cid) == prima
        assert riga(db, "partner_calls", master) == prima_master
        assert voci_audit(db) == []

    @pytest.mark.parametrize("stato_master", STATI_CHIUSI)
    def test_una_call_chiusa_sul_master_non_collide(self, db, stato_master):
        _, a = azienda(db)
        cid = inserisci_call(db, a, DOPPIONE)
        inserisci_call(db, a, MASTER, stato=stato_master)
        assert rimappa(db) == conteggi(pc_agg=1)
        assert riga(db, "partner_calls", cid)["bando_id"] == MASTER

    def test_la_call_di_un_altra_azienda_non_collide(self, db):
        _, a = azienda(db)
        _, b = azienda(db)
        cid = inserisci_call(db, a, DOPPIONE)
        inserisci_call(db, b, MASTER)
        assert rimappa(db) == conteggi(pc_agg=1)
        assert riga(db, "partner_calls", cid)["bando_id"] == MASTER

    @pytest.mark.parametrize("stato", STATI_CHIUSI)
    def test_call_chiuse_intatte_e_non_contate(self, db, stato):
        _, a = azienda(db)
        cid = inserisci_call(db, a, DOPPIONE, stato=stato, mancante_dal=True)
        prima = riga(db, "partner_calls", cid)
        assert rimappa(db) == conteggi()
        assert riga(db, "partner_calls", cid) == prima

    def test_versioni_intatte(self, db):
        owner, a = azienda(db)
        cid = inserisci_call(db, a, DOPPIONE)
        db.execute(
            "insert into public.partner_call_versioni (call_id, versione, snapshot, "
            "modificato_da) values (%s, 1, %s::jsonb, %s)",
            (cid, '{"bando_id": %d}' % DOPPIONE, owner),
        )
        prima = impronta(db, ["partner_call_versioni"])
        versione = riga(db, "partner_calls", cid)["versione"]
        assert rimappa(db) == conteggi(pc_agg=1)
        assert impronta(db, ["partner_call_versioni"]) == prima
        assert riga(db, "partner_calls", cid)["versione"] == versione


# ----------------------------------------------------------------- scenario e tabelle escluse


class TestScenario:
    def test_scenario_completo(self, db, sc):
        altri = {
            ("saved_bandi", sc.sb_u2_master), ("saved_bandi", sc.sb_u3_master),
            ("saved_bandi", sc.sb_altro), ("calendar_events", sc.ce_u2_master),
            ("calendar_events", sc.ce_u3_master), ("calendar_events", sc.ce_altro),
            ("calendar_events", sc.ce_personale), ("partner_calls", sc.pc_b),
            ("partner_calls", sc.pc_b_master), ("partner_calls", sc.pc_c),
        }
        prima = {k: riga(db, *k) for k in altri}
        assert rimappa(db) == sc.atteso
        assert riga(db, "saved_bandi", sc.sb_u1)["bando_id"] == MASTER
        assert riga(db, "saved_bandi", sc.sb_u2) is None
        assert riga(db, "saved_bandi", sc.sb_u3)["bando_id"] == MASTER
        assert riga(db, "calendar_events", sc.ce_u1)["bando_id"] == MASTER
        assert riga(db, "calendar_events", sc.ce_u2) is None
        assert riga(db, "calendar_events", sc.ce_u3)["tipo"] == "personale"
        assert riga(db, "partner_calls", sc.pc_a)["bando_id"] == MASTER
        # Righe del master, di altri bandi, personali, in collisione e chiuse: intatte.
        for k, valore in prima.items():
            assert riga(db, *k) == valore, k

    def test_le_altre_tabelle_non_cambiano(self, db, sc):
        """Righe col doppione nelle tabelle escluse dal contratto; l'impronta di
        OGNI tabella public diversa dalle tre rimappate e da audit_log non cambia."""
        owner = sc.b_owner
        db.execute(
            "insert into public.bando_requirements (bando_id, bando_slug, content_hash, "
            "prompt_version, model, extraction) values (%s, %s, 'h', 1, 'm', '{}'::jsonb)",
            (DOPPIONE, SLUG_DOPPIONE))
        db.execute(
            "insert into public.ai_checks (company_profile_id, user_id, family_parent_id, "
            "bando_id, bando_slug, bando_titolo) values (%s, %s, %s, %s, %s, 'Doppione')",
            (sc.b, owner, owner, DOPPIONE, SLUG_DOPPIONE))
        db.execute(
            "insert into public.bando_alert_sends (user_id, bando_id, bando_slug, stato) "
            "values (%s, %s, %s, 'inviata')", (sc.u1, DOPPIONE, SLUG_DOPPIONE))
        db.execute(
            "insert into public.bando_partenariato (bando_id, bando_slug, bando_titolo, stato) "
            "values (%s, %s, 'Doppione', 'errore')", (DOPPIONE, SLUG_DOPPIONE))
        altre = [t for t in tabelle_public(db)
                 if t not in TABELLE_RIMAPPATE and t != "audit_log"]
        assert set(TABELLE_ESCLUSE) <= set(altre)
        prima = impronta(db, altre)
        assert rimappa(db) == sc.atteso
        assert impronta(db, altre) == prima
        for tabella in ("bando_requirements", "ai_checks", "bando_alert_sends",
                        "bando_partenariato"):
            assert db.execute(
                sql.SQL("select count(*) from public.{} where bando_id = %s").format(
                    sql.Identifier(tabella)), (DOPPIONE,),
            ).fetchone()[0] == 1, tabella

    def test_il_corpo_non_nomina_le_tabelle_escluse(self, db):
        corpo = db.execute(
            "select prosrc from pg_proc where proname = 'fn_rimappa_bando_fuso'"
        ).fetchone()[0]
        for tabella in TABELLE_ESCLUSE:
            assert tabella not in corpo, tabella


# ----------------------------------------------------------------- audit


class TestAudit:
    def test_una_voce_per_riga_toccata(self, db, sc):
        rimappa(db)
        voci = voci_audit(db)
        attese = {
            ("saved_bandi", sc.sb_u1, "aggiornata", sc.u1),
            ("saved_bandi", sc.sb_u2, "eliminata", sc.u2),
            ("saved_bandi", sc.sb_u3, "aggiornata", sc.u3),
            ("calendar_events", sc.ce_u1, "aggiornata", sc.u1),
            ("calendar_events", sc.ce_u2, "eliminata", sc.u2),
            ("calendar_events", sc.ce_u3, "convertita", sc.u3),
            ("partner_calls", sc.pc_a, "aggiornata", sc.u3),
        }
        assert len(voci) == len(attese)
        assert {(v["payload"]["tabella"], v["payload"]["id"], v["payload"]["operazione"],
                 str(v["target_user_id"])) for v in voci} == attese
        for v in voci:
            p = v["payload"]
            assert v["actor_id"] is None
            assert (p["doppione"], p["master"], p["master_slug"]) == (
                DOPPIONE, MASTER, SLUG_MASTER)
            if p["tabella"] == "partner_calls":
                assert str(v["family_parent_id"]) == sc.u3
                assert p["company_profile_id"] == sc.a
            else:
                assert v["family_parent_id"] is None

    def test_valori_precedenti(self, db, sc):
        """Riga intera per le eliminate; solo le colonne cambiate per le altre (per
        le call niente colonne riservate)."""
        intere = {
            id_: db.execute(
                sql.SQL("select to_jsonb(t) from public.{} t where id = %s").format(
                    sql.Identifier(tabella)), (id_,),
            ).fetchone()[0]
            for tabella, id_ in (("saved_bandi", sc.sb_u2), ("calendar_events", sc.ce_u2))
        }
        mancante_dal = riga(db, "partner_calls", sc.pc_a)["bando_mancante_dal"]
        rimappa(db)
        per_id = {v["payload"]["id"]: v["payload"]["prima"] for v in voci_audit(db)}
        for id_, intera in intere.items():
            assert per_id[id_] == intera
            assert intera["bando_id"] == DOPPIONE
        assert per_id[sc.sb_u1] == {"bando_id": DOPPIONE, "bando_slug": f"bando-{DOPPIONE}"}
        assert per_id[sc.ce_u1] == {"bando_id": DOPPIONE, "bando_slug": f"bando-{DOPPIONE}"}
        assert per_id[sc.ce_u3] == {"tipo": "bando", "bando_id": DOPPIONE,
                                    "bando_slug": f"bando-{DOPPIONE}"}
        assert per_id[sc.pc_a] == {"bando_id": DOPPIONE, "bando_slug": f"bando-{DOPPIONE}",
                                   "bando_mancante_dal": mancante_dal.isoformat()}

    def test_ripristino_a_mano_dall_audit(self, db, sc):
        """Le query del rollback in coda al file riportano le righe allo stato
        precedente (salvo updated_at, che il trigger avanza)."""
        chiavi = [("saved_bandi", sc.sb_u1), ("saved_bandi", sc.sb_u2),
                  ("saved_bandi", sc.sb_u3), ("calendar_events", sc.ce_u1),
                  ("calendar_events", sc.ce_u2), ("calendar_events", sc.ce_u3),
                  ("partner_calls", sc.pc_a)]
        prima = {k: riga(db, *k) for k in chiavi}
        rimappa(db)
        voci = db.execute(
            "select payload from public.audit_log where action = %s "
            "and payload->>'doppione' = %s order by id desc", (AZIONE, str(DOPPIONE)),
        ).fetchall()
        for (payload,) in voci:
            tabella, operazione = payload["tabella"], payload["operazione"]
            valore = Jsonb(payload)
            if operazione == "eliminata":
                db.execute(
                    sql.SQL("insert into public.{t} select * from jsonb_populate_record("
                            "null::public.{t}, %s::jsonb->'prima')").format(
                        t=sql.Identifier(tabella)), (valore,))
            elif operazione == "convertita":
                db.execute(
                    "update public.calendar_events set tipo = 'bando', "
                    "bando_id = (%s::jsonb->'prima'->>'bando_id')::integer, "
                    "bando_slug = %s::jsonb->'prima'->>'bando_slug' "
                    "where id = (%s::jsonb->>'id')::uuid", (valore, valore, valore))
            elif tabella == "partner_calls":
                db.execute(
                    "update public.partner_calls "
                    "set bando_id = (%s::jsonb->'prima'->>'bando_id')::integer, "
                    "bando_slug = %s::jsonb->'prima'->>'bando_slug', "
                    "bando_mancante_dal = (%s::jsonb->'prima'->>'bando_mancante_dal')::date "
                    "where id = (%s::jsonb->>'id')::uuid "
                    "and bando_id = (%s::jsonb->>'master')::integer",
                    (valore,) * 5)
            else:
                db.execute(
                    sql.SQL("update public.{} "
                            "set bando_id = (%s::jsonb->'prima'->>'bando_id')::integer, "
                            "bando_slug = %s::jsonb->'prima'->>'bando_slug' "
                            "where id = (%s::jsonb->>'id')::uuid "
                            "and bando_id = (%s::jsonb->>'master')::integer").format(
                        sql.Identifier(tabella)), (valore,) * 4)
        for k in chiavi:
            ora = riga(db, *k)
            atteso = prima[k]
            if "updated_at" in atteso:
                ora.pop("updated_at")
                atteso = {c: v for c, v in atteso.items() if c != "updated_at"}
            assert ora == atteso, k


# ----------------------------------------------------------------- prova e idempotenza


class TestProvaEIdempotenza:
    def test_prova_non_scrive_e_conta_come_la_scrittura(self, db, sc):
        tabelle = TABELLE_RIMAPPATE + ("audit_log",)
        prima = impronta(db, tabelle)
        prova = rimappa(db, prova=True)
        assert prova == sc.atteso
        assert impronta(db, tabelle) == prima
        assert rimappa(db, prova=False) == prova

    def test_prova_senza_lock_advisory(self, db, sc):
        """La prova non attende il lock dei passi che scrivono; la scrittura sì."""
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select pg_advisory_xact_lock(hashtext('catalogo_rimappatura_fusi'))")
            db.execute("set lock_timeout = '300ms'")
            assert rimappa(db, prova=True) == sc.atteso
            with pytest.raises(psycopg.errors.LockNotAvailable):
                rimappa(db, prova=False)
        finally:
            db.execute("set lock_timeout = 0")
            chiudi_tutto(altra)
        assert rimappa(db) == sc.atteso

    def test_idempotente(self, db, sc):
        assert rimappa(db) == sc.atteso
        tabelle = TABELLE_RIMAPPATE + ("audit_log",)
        prima = impronta(db, tabelle)
        # Resta solo la call in collisione, che non si tocca.
        assert rimappa(db) == conteggi(pc_col=1)
        assert rimappa(db, prova=True) == conteggi(pc_col=1)
        assert impronta(db, tabelle) == prima


# ----------------------------------------------------------------- concorrenza


class TestConcorrenza:
    def test_due_passi_si_serializzano(self, db, sc):
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: rimappa(db))
        try:
            assert rimappa(altra) == sc.atteso  # transazione aperta
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, monitor)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") == conteggi(pc_col=1), esito
        assert len(voci_audit(db)) == 7

    def test_inserimento_concorrente_del_master_annulla_tutta_la_chiamata(self, db):
        """L'utente aggiunge in calendario il master mentre la rimappatura gira: il
        vincolo di unicità fa fallire la chiamata intera (anche il preferito già
        aggiornato torna indietro) e il passo successivo la ripete."""
        u = new_user(db)
        sid = salva(db, u, DOPPIONE)
        eid = evento(db, u, DOPPIONE)
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: rimappa(db))
        try:
            evento(altra, u, MASTER)  # non ancora confermato: invisibile al passo
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, monitor)
            if thread.is_alive():
                thread.join(timeout=10)
        assert isinstance(esito.get("errore"), psycopg.errors.UniqueViolation), esito
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE
        assert riga(db, "calendar_events", eid)["bando_id"] == DOPPIONE
        assert voci_audit(db) == []
        assert rimappa(db) == conteggi(sb_agg=1, ce_eli=1)


# ----------------------------------------------------------------- sicurezza


class TestSicurezza0043:
    def test_firme_e_protezioni(self, db):
        """Una sola firma per nome, SECURITY DEFINER con search_path fissato, non
        eseguibili dai client (PUBLIC compreso)."""
        for nome, firma in FIRME.items():
            righe_ = db.execute(
                """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                          coalesce(p.proacl::text, ''), p.oid::regprocedure::text,
                          pg_get_function_result(p.oid), p.provolatile
                   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and p.proname = %s""",
                (nome,),
            ).fetchall()
            assert len(righe_) == 1, f"{nome}: {len(righe_)} firme"
            oid, secdef, config, acl, firma_db, ritorno, volatilita = righe_[0]
            assert firma_db == firma
            assert ritorno == RITORNI[nome]
            assert secdef is True, f"{nome} non è security definer"
            assert "search_path=public" in config, f"{nome}: search_path non fissato"
            assert acl and not re.search(r"[{,]=X", acl), f"{nome}: PUBLIC esegue ({acl})"
            for ruolo in ("anon", "authenticated"):
                assert not db.execute(
                    "select has_function_privilege(%s, %s::oid, 'execute')", (ruolo, oid)
                ).fetchone()[0], f"{ruolo} esegue {nome}"
            if nome == "fn_bandi_in_uso":
                assert volatilita == "s"

    def test_default_di_p_prova(self, db):
        argomenti = db.execute(
            "select pg_get_function_arguments('public.fn_rimappa_bando_fuso"
            "(integer,integer,text,boolean)'::regprocedure)"
        ).fetchone()[0]
        assert argomenti == ("p_doppione integer, p_master integer, p_master_slug text, "
                             "p_prova boolean DEFAULT false")

    def test_revoche_scritte_nel_file(self):
        for nome in FIRME:
            assert re.search(
                rf"^revoke execute on function public\.{nome}\([^)]*\)\s+"
                r"from public, anon, authenticated;",
                SQL_0043, re.M,
            ), nome

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_bandi_in_uso()",
        "select public.fn_rimappa_bando_fuso(7001, 7000, 'bando-master')",
        "select public.fn_rimappa_bando_fuso(7001, 7000, 'bando-master', true)",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, ruolo, chiamata):
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_esegue_il_flusso(self, db, sc):
        """Come il service_role: un grant esplicito sulle RPC basta e la
        rimappatura scrive senza privilegi di tabella."""
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            for firma in FIRME.values():
                db.execute(f"grant execute on function public.{firma} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                assert bandi_in_uso(db) == [MASTER, DOPPIONE, ALTRO_BANDO]
                assert rimappa(db) == sc.atteso
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    db.execute("select 1 from public.saved_bandi")
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        assert riga(db, "saved_bandi", sc.sb_u1)["bando_id"] == MASTER
        assert len(voci_audit(db)) == 7
