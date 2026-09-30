"""Test funzionali della migration 0044 (ripristino della rimappatura dei bandi
fusi della 0043).

Coprono: fn_ripristina_rimappatura con la prova di default che non scrive e
conta come la scrittura; il ripristino completo di un passo reale della 0043
(righe eliminate reinserite con lo stesso id, aggiornate riportate al doppione,
convertite tornate scadenze del bando); le voci di audit_log del ripristino;
l'idempotenza; il filtro per doppione e per data; i conflitti, uno per regola,
con le righe in conflitto intatte; un inserimento concorrente; il lock advisory
condiviso con la 0043; le altre tabelle intatte; parametri, firma e revoche ai
client.
Ogni test riceve un database fresco clonato dal template.
"""

import itertools
import re
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SQL_0044 = (MIGRAZIONI / "0044_ripristino_rimappatura.sql").read_text(encoding="utf-8")

FIRMA = "fn_ripristina_rimappatura(integer,timestamp with time zone,boolean)"
ORIGINE = "catalogo.rimappatura_fuso"
AZIONE = "catalogo.ripristino_rimappatura"

DOPPIONE = 7001
MASTER = 7000
ALTRO_DOPPIONE = 7002
ALTRO_BANDO = 7999
SLUG_MASTER = "bando-master"

TABELLE = ("saved_bandi", "calendar_events", "partner_calls")

_ASSENTE = object()
_seq = itertools.count(1)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def conteggi(sb=(0, 0), ce=(0, 0), pc=(0, 0)) -> dict:
    """(ripristinate, in_conflitto) per tabella."""
    return {
        tabella: {"ripristinate": rip, "in_conflitto": con}
        for tabella, (rip, con) in zip(TABELLE, (sb, ce, pc), strict=True)
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
        (owner, f"ACME {i} Srl", f"{i + 44000000:011d}"),
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


def chiudi_call(db, call_id: str) -> None:
    db.execute("update public.partner_calls set stato = 'chiusa_annullata', chiusa_at = now(), "
               "motivo_chiusura = 'creatore_annullata' where id = %s", (call_id,))


def rimappa(db, doppione=DOPPIONE, master=MASTER, slug=SLUG_MASTER) -> dict:
    """Passo reale della 0043 (scrive)."""
    return db.execute(
        "select public.fn_rimappa_bando_fuso(p_doppione => %s::integer, "
        "p_master => %s::integer, p_master_slug => %s::text, p_prova => false)",
        (doppione, master, slug),
    ).fetchone()[0]


def ripristina(db, doppione=DOPPIONE, dal="-infinity", prova=_ASSENTE) -> dict:
    """Chiamata per nome; senza `prova` usa il default."""
    if prova is _ASSENTE:
        return db.execute(
            "select public.fn_ripristina_rimappatura(p_doppione => %s::integer, "
            "p_dal => %s::timestamptz)",
            (doppione, dal),
        ).fetchone()[0]
    return db.execute(
        "select public.fn_ripristina_rimappatura(p_doppione => %s::integer, "
        "p_dal => %s::timestamptz, p_prova => %s::boolean)",
        (doppione, dal, prova),
    ).fetchone()[0]


def riga(db, tabella: str, id_) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            sql.SQL("select * from public.{} where id = %s").format(sql.Identifier(tabella)),
            (id_,),
        ).fetchone()


def contenuto(db, tabella: str, escluse=("updated_at",)) -> dict:
    """Righe della tabella per id, senza le colonne escluse."""
    with db.cursor(row_factory=dict_row) as cur:
        righe_ = cur.execute(
            sql.SQL("select * from public.{}").format(sql.Identifier(tabella))
        ).fetchall()
    return {r["id"]: {k: v for k, v in r.items() if k not in escluse} for r in righe_}


def voci(db, azione: str = AZIONE) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select id, actor_id, target_user_id, family_parent_id, payload, created_at "
            "from public.audit_log where action = %s order by id", (azione,),
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
    """Lo scenario della 0043 dopo un passo reale della rimappatura:
    - u1: preferito ed evento del doppione passati al master (aggiornati);
    - u2: preferito ed evento del doppione eliminati (c'era già il master);
    - u3 (azienda A): preferito aggiornato; evento con note convertito in personale;
    - call di A aggiornata (aveva bando_mancante_dal); call di B in collisione e
      call chiusa di C mai toccate;
    - righe di un altro bando e un evento personale intatti.
    `prima` è il contenuto delle tre tabelle prima della rimappatura.
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
    ns.prima = {t: contenuto(db, t) for t in TABELLE}
    ns.prima_intere = {t: contenuto(db, t, escluse=()) for t in TABELLE}
    esito = rimappa(db)
    assert esito["saved_bandi"] == {"aggiornate": 2, "eliminate": 1}
    assert esito["calendar_events"] == {"aggiornati": 1, "eliminati": 1, "convertiti": 1}
    assert esito["partner_calls"] == {"aggiornate": 1, "in_collisione": 1}
    ns.atteso = conteggi(sb=(3, 0), ce=(3, 0), pc=(1, 0))
    return ns


def ripristinata_come_prima(db, sc) -> None:
    """Le tre tabelle tornano al contenuto precedente alla rimappatura, salvo
    updated_at (il trigger lo avanza) e bando_mancante_dal della call
    ripristinata (NULL: lo scheduler lo rivaluta)."""
    for tabella in TABELLE:
        ora = contenuto(db, tabella)
        atteso = {k: dict(v) for k, v in sc.prima[tabella].items()}
        if tabella == "partner_calls":
            atteso[uuid.UUID(sc.pc_a)]["bando_mancante_dal"] = None
        assert ora == atteso, tabella


# ----------------------------------------------------------------- parametri e ritorno


class TestParametri:
    @pytest.mark.parametrize("doppione,dal,prova", [
        (None, "-infinity", False),
        (0, "-infinity", False),
        (-3, "-infinity", False),
        (DOPPIONE, None, False),
        (DOPPIONE, "-infinity", None),
    ])
    def test_parametri_non_validi(self, db, sc, doppione, dal, prova):
        prima = impronta(db, TABELLE + ("audit_log",))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ripristina(db, doppione, dal, prova)
        assert detail_of(exc) == "parametri_non_validi"
        assert impronta(db, TABELLE + ("audit_log",)) == prima

    def test_forma_del_ritorno(self, db, sc):
        esito = ripristina(db, prova=False)
        assert esito == sc.atteso
        assert set(esito) == set(TABELLE)

    def test_doppione_senza_voci(self, db, sc):
        assert ripristina(db, doppione=12345, prova=False) == conteggi()
        assert voci(db) == []


# ----------------------------------------------------------------- prova


class TestProva:
    def test_default_e_prova_senza_scritture(self, db, sc):
        tabelle = TABELLE + ("audit_log",)
        prima = impronta(db, tabelle)
        assert ripristina(db) == sc.atteso
        assert ripristina(db, prova=True) == sc.atteso
        assert impronta(db, tabelle) == prima

    def test_la_prova_conta_come_la_scrittura(self, db, sc):
        """Voci che dipendono l'una dall'altra: la prova vede l'effetto dei
        ripristini più recenti della stessa chiamata, come la scrittura.
        - u1 salva di nuovo il doppione e un secondo passo lo elimina: il suo
          reinserimento occupa l'ambito del preferito del primo passo;
        - il preferito di u torna a mano sul doppione e un terzo passo lo
          rimappa di nuovo: la voce più recente lo ripristina, quella del
          secondo passo lo trova già sul doppione."""
        salva(db, sc.u1, DOPPIONE)
        u = new_user(db)
        sid = salva(db, u, DOPPIONE)
        rimappa(db)
        db.execute("update public.saved_bandi set bando_id = %s, bando_slug = %s "
                   "where id = %s", (DOPPIONE, f"bando-{DOPPIONE}", sid))
        rimappa(db)
        attesi = conteggi(sb=(4, 2), ce=(3, 0), pc=(1, 0))
        assert ripristina(db, prova=True) == attesi
        assert ripristina(db, prova=False) == attesi
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE
        assert riga(db, "saved_bandi", sc.sb_u1)["bando_id"] == MASTER


# ----------------------------------------------------------------- ripristino completo


class TestRipristinoCompleto:
    def test_passo_reale_della_0043_ripristinato(self, db, sc):
        assert ripristina(db, prova=False) == sc.atteso
        ripristinata_come_prima(db, sc)
        assert db.execute("select public.fn_bandi_in_uso()").fetchone()[0] == [
            MASTER, DOPPIONE, ALTRO_BANDO]

    def test_righe_reinserite_intere(self, db, sc):
        """Stesso id e stessi valori, created_at e updated_at compresi."""
        ripristina(db, prova=False)
        for tabella, id_ in (("saved_bandi", sc.sb_u2), ("calendar_events", sc.ce_u2)):
            assert riga(db, tabella, id_) == sc.prima_intere[tabella][uuid.UUID(id_)], tabella

    def test_evento_convertito_torna_scadenza(self, db, sc):
        ripristina(db, prova=False)
        dopo = riga(db, "calendar_events", sc.ce_u3)
        assert (dopo["tipo"], dopo["bando_id"], dopo["bando_slug"]) == (
            "bando", DOPPIONE, f"bando-{DOPPIONE}")
        assert dopo["note"] == "Chiedere al commercialista"

    def test_call_torna_al_doppione_con_mancante_azzerato(self, db, sc):
        ripristina(db, prova=False)
        dopo = riga(db, "partner_calls", sc.pc_a)
        assert (dopo["bando_id"], dopo["bando_slug"], dopo["bando_mancante_dal"]) == (
            DOPPIONE, f"bando-{DOPPIONE}", None)

    def test_call_chiusa_dopo_la_rimappatura_si_ripristina(self, db, sc):
        chiudi_call(db, sc.pc_a)
        assert ripristina(db, prova=False) == sc.atteso
        dopo = riga(db, "partner_calls", sc.pc_a)
        assert (dopo["bando_id"], dopo["stato"]) == (DOPPIONE, "chiusa_annullata")

    def test_le_altre_tabelle_non_cambiano(self, db, sc):
        altre = [t for t in tabelle_public(db) if t not in TABELLE and t != "audit_log"]
        prima = impronta(db, altre)
        ripristina(db, prova=False)
        assert impronta(db, altre) == prima

    def test_modifiche_dell_utente_su_altre_colonne_restano(self, db, sc):
        """Note cambiate su una scadenza ancora del bando: il ripristino riporta
        solo il bando e lascia le note."""
        db.execute("update public.calendar_events set note = 'Nuova nota' where id = %s",
                   (sc.ce_u1,))
        assert ripristina(db, prova=False) == sc.atteso
        dopo = riga(db, "calendar_events", sc.ce_u1)
        assert (dopo["bando_id"], dopo["note"]) == (DOPPIONE, "Nuova nota")


# ----------------------------------------------------------------- audit


class TestAudit:
    def test_una_voce_per_riga_ripristinata(self, db, sc):
        origine = {v["payload"]["id"]: v for v in voci(db, ORIGINE)}
        ripristina(db, prova=False)
        scritte = voci(db)
        assert len(scritte) == 7
        assert {v["payload"]["id"] for v in scritte} == set(origine)
        for v in scritte:
            p = v["payload"]
            o = origine[p["id"]]
            assert v["actor_id"] is None
            assert (v["target_user_id"], v["family_parent_id"]) == (
                o["target_user_id"], o["family_parent_id"])
            assert p["voce"] == o["id"]
            assert (p["tabella"], p["operazione"], p["company_profile_id"]) == (
                o["payload"]["tabella"], o["payload"]["operazione"],
                o["payload"]["company_profile_id"])
            assert (p["doppione"], p["master"]) == (DOPPIONE, MASTER)

    def test_stato_sovrascritto(self, db, sc):
        mancante = riga(db, "partner_calls", sc.pc_a)["bando_mancante_dal"]
        ripristina(db, prova=False)
        per_id = {v["payload"]["id"]: v["payload"]["prima"] for v in voci(db)}
        assert per_id[sc.sb_u2] is None
        assert per_id[sc.ce_u2] is None
        assert per_id[sc.sb_u1] == {"bando_id": MASTER, "bando_slug": SLUG_MASTER}
        assert per_id[sc.ce_u1] == {"tipo": "bando", "bando_id": MASTER,
                                    "bando_slug": SLUG_MASTER}
        assert per_id[sc.ce_u3] == {"tipo": "personale", "bando_id": None, "bando_slug": None}
        assert per_id[sc.pc_a] == {"bando_id": MASTER, "bando_slug": SLUG_MASTER,
                                   "bando_mancante_dal": mancante}


# ----------------------------------------------------------------- idempotenza e filtri


class TestIdempotenzaEFiltri:
    def test_idempotente(self, db, sc):
        assert ripristina(db, prova=False) == sc.atteso
        tabelle = TABELLE + ("audit_log",)
        prima = impronta(db, tabelle)
        assert ripristina(db, prova=False) == conteggi()
        assert ripristina(db) == conteggi()
        assert impronta(db, tabelle) == prima

    def test_i_conflitti_restano_da_vedere_senza_scritture(self, db, sc):
        salva(db, sc.u1, DOPPIONE)
        esito = ripristina(db, prova=False)
        assert esito == conteggi(sb=(2, 1), ce=(3, 0), pc=(1, 0))
        prima = impronta(db, TABELLE + ("audit_log",))
        assert ripristina(db, prova=False) == conteggi(sb=(0, 1))
        assert impronta(db, TABELLE + ("audit_log",)) == prima

    def test_nuova_rimappatura_dopo_un_ripristino(self, db, sc):
        """Fusione, ripristino, nuova fusione: il secondo ripristino applica solo
        le voci nuove."""
        ripristina(db, prova=False)
        rimappa(db)
        assert ripristina(db, prova=False) == sc.atteso
        ripristinata_come_prima(db, sc)

    def test_voci_prima_di_p_dal_ignorate(self, db, sc):
        istante = voci(db, ORIGINE)[-1]["created_at"]
        assert ripristina(db, dal=istante + timedelta(microseconds=1), prova=False) == conteggi()
        assert ripristina(db, dal=istante, prova=True) == sc.atteso
        assert ripristina(db, dal=istante, prova=False) == sc.atteso

    def test_solo_il_doppione_indicato(self, db, sc):
        u = new_user(db)
        altro = salva(db, u, ALTRO_DOPPIONE)
        rimappa(db, doppione=ALTRO_DOPPIONE)
        assert ripristina(db, prova=False) == sc.atteso
        assert riga(db, "saved_bandi", altro)["bando_id"] == MASTER
        assert ripristina(db, doppione=ALTRO_DOPPIONE, prova=False) == conteggi(sb=(1, 0))
        assert riga(db, "saved_bandi", altro)["bando_id"] == ALTRO_DOPPIONE


# ----------------------------------------------------------------- conflitti


class TestConflitti:
    def test_preferito_eliminato_con_il_doppione_di_nuovo_salvato(self, db, sc):
        nuovo = salva(db, sc.u2, DOPPIONE)
        assert ripristina(db, prova=False) == conteggi(sb=(2, 1), ce=(3, 0), pc=(1, 0))
        assert riga(db, "saved_bandi", sc.sb_u2) is None
        assert riga(db, "saved_bandi", nuovo)["bando_id"] == DOPPIONE

    def test_preferito_aggiornato_con_il_doppione_di_nuovo_salvato(self, db, sc):
        salva(db, sc.u3, DOPPIONE, sc.a)
        assert ripristina(db, prova=False) == conteggi(sb=(2, 1), ce=(3, 0), pc=(1, 0))
        assert riga(db, "saved_bandi", sc.sb_u3)["bando_id"] == MASTER

    def test_preferito_non_piu_sul_master(self, db, sc):
        db.execute("update public.saved_bandi set bando_id = %s where id = %s",
                   (ALTRO_BANDO + 1, sc.sb_u1))
        assert ripristina(db, prova=False) == conteggi(sb=(2, 1), ce=(3, 0), pc=(1, 0))
        assert riga(db, "saved_bandi", sc.sb_u1)["bando_id"] == ALTRO_BANDO + 1

    def test_preferito_eliminato_di_un_utente_che_non_esiste_piu(self, db, sc):
        db.execute("delete from auth.users where id = %s", (sc.u2,))
        assert riga(db, "saved_bandi", sc.sb_u2_master) is None
        assert ripristina(db, prova=False) == conteggi(sb=(2, 1), ce=(2, 1), pc=(1, 0))
        assert riga(db, "saved_bandi", sc.sb_u2) is None

    def test_evento_eliminato_con_la_scadenza_di_nuovo_in_calendario(self, db, sc):
        nuovo = evento(db, sc.u2, DOPPIONE)
        assert ripristina(db, prova=False) == conteggi(sb=(3, 0), ce=(2, 1), pc=(1, 0))
        assert riga(db, "calendar_events", sc.ce_u2) is None
        assert riga(db, "calendar_events", nuovo)["bando_id"] == DOPPIONE

    def test_evento_aggiornato_eliminato_dall_utente(self, db, sc):
        db.execute("delete from public.calendar_events where id = %s", (sc.ce_u1,))
        assert ripristina(db, prova=False) == conteggi(sb=(3, 0), ce=(2, 1), pc=(1, 0))
        assert riga(db, "calendar_events", sc.ce_u1) is None

    def test_evento_convertito_modificato_dopo(self, db, sc):
        db.execute("select pg_sleep(0.01)")
        db.execute("update public.calendar_events set data = data + 1 where id = %s",
                   (sc.ce_u3,))
        prima = riga(db, "calendar_events", sc.ce_u3)
        assert ripristina(db, prova=False) == conteggi(sb=(3, 0), ce=(2, 1), pc=(1, 0))
        assert riga(db, "calendar_events", sc.ce_u3) == prima

    def test_call_con_un_altra_call_attiva_sul_doppione(self, db, sc):
        nuova = inserisci_call(db, sc.a, DOPPIONE, stato="bozza")
        assert ripristina(db, prova=False) == conteggi(sb=(3, 0), ce=(3, 0), pc=(0, 1))
        assert riga(db, "partner_calls", sc.pc_a)["bando_id"] == MASTER
        assert riga(db, "partner_calls", nuova)["bando_id"] == DOPPIONE

    def test_call_chiusa_non_collide_con_una_call_attiva(self, db, sc):
        chiudi_call(db, sc.pc_a)
        inserisci_call(db, sc.a, DOPPIONE, stato="bozza")
        assert ripristina(db, prova=False) == sc.atteso
        assert riga(db, "partner_calls", sc.pc_a)["bando_id"] == DOPPIONE

    def test_call_non_piu_sul_master(self, db, sc):
        db.execute("update public.partner_calls set bando_id = %s where id = %s",
                   (ALTRO_BANDO, sc.pc_a))
        assert ripristina(db, prova=False) == conteggi(sb=(3, 0), ce=(3, 0), pc=(0, 1))
        assert riga(db, "partner_calls", sc.pc_a)["bando_id"] == ALTRO_BANDO


# ----------------------------------------------------------------- due doppioni, un master


class TestDueDoppioniDelloStessoMaster:
    """DOPPIONE e ALTRO_DOPPIONE fusi entrambi in MASTER."""

    def test_riga_sul_master_che_ha_fatto_eliminare_l_altro_doppione(self, db):
        """u aveva salvato e messo in calendario entrambi i doppioni: il primo passo
        porta sul master le righe del primo, il secondo elimina quelle dell'altro.
        Riportare indietro solo il primo lascerebbe l'ambito senza nessuno dei due
        bandi dell'altro: finché l'altro non è ripristinato, restano sul master."""
        u = new_user(db)
        sid, eid = salva(db, u, DOPPIONE), evento(db, u, DOPPIONE)
        altro_sid, altro_eid = salva(db, u, ALTRO_DOPPIONE), evento(db, u, ALTRO_DOPPIONE)
        rimappa(db)
        esito = rimappa(db, doppione=ALTRO_DOPPIONE)
        assert (esito["saved_bandi"]["eliminate"], esito["calendar_events"]["eliminati"]) == (1, 1)
        attesi = conteggi(sb=(0, 1), ce=(0, 1))
        assert ripristina(db) == attesi
        assert ripristina(db, prova=False) == attesi
        assert riga(db, "saved_bandi", sid)["bando_id"] == MASTER
        assert riga(db, "calendar_events", eid)["bando_id"] == MASTER
        assert voci(db) == []
        # Ripristinato l'altro doppione, il primo si ripristina.
        assert ripristina(db, doppione=ALTRO_DOPPIONE, prova=False) == conteggi(
            sb=(1, 0), ce=(1, 0))
        assert ripristina(db, prova=False) == conteggi(sb=(1, 0), ce=(1, 0))
        for tabella, id_, bando in (("saved_bandi", sid, DOPPIONE),
                                    ("saved_bandi", altro_sid, ALTRO_DOPPIONE),
                                    ("calendar_events", eid, DOPPIONE),
                                    ("calendar_events", altro_eid, ALTRO_DOPPIONE)):
            assert riga(db, tabella, id_)["bando_id"] == bando, (tabella, bando)

    def test_eliminazioni_precedenti_o_di_altri_ambiti_non_bloccano(self, db):
        u, a = azienda(db)
        w = new_user(db)
        salva(db, u, ALTRO_DOPPIONE)
        master_u = salva(db, u, MASTER)
        # Eliminato perché c'era già il master salvato da u, poi tolto da u.
        rimappa(db, doppione=ALTRO_DOPPIONE)
        db.execute("delete from public.saved_bandi where id = %s", (master_u,))
        sid = salva(db, u, DOPPIONE)
        rimappa(db)
        # Dopo, eliminazioni di un altro utente e di un'altra azienda di u.
        for utente, company in ((w, None), (u, a)):
            salva(db, utente, ALTRO_DOPPIONE, company)
            salva(db, utente, MASTER, company)
        assert rimappa(db, doppione=ALTRO_DOPPIONE)["saved_bandi"]["eliminate"] == 2
        assert ripristina(db) == conteggi(sb=(1, 0))
        assert ripristina(db, prova=False) == conteggi(sb=(1, 0))
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE

    def test_conversioni_e_altre_tabelle_non_bloccano(self, db):
        """Dopo che la scadenza del primo doppione è passata al master, l'altro
        doppione ha un evento convertito (note conservate) e un preferito
        eliminato: nessuno dei due dipende dalla scadenza sul master."""
        u = new_user(db)
        eid = evento(db, u, DOPPIONE)
        rimappa(db)
        evento(db, u, ALTRO_DOPPIONE, note="Portare i documenti")
        salva(db, u, ALTRO_DOPPIONE)
        salva(db, u, MASTER)
        esito = rimappa(db, doppione=ALTRO_DOPPIONE)
        assert (esito["saved_bandi"]["eliminate"], esito["calendar_events"]["convertiti"]) == (
            1, 1)
        assert ripristina(db) == conteggi(ce=(1, 0))
        assert ripristina(db, prova=False) == conteggi(ce=(1, 0))
        assert riga(db, "calendar_events", eid)["bando_id"] == DOPPIONE


# ----------------------------------------------------------------- concorrenza


class TestConcorrenza:
    def test_attende_il_lock_della_rimappatura_la_prova_no(self, db, sc):
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select pg_advisory_xact_lock(hashtext('catalogo_rimappatura_fusi'))")
            db.execute("set lock_timeout = '300ms'")
            assert ripristina(db) == sc.atteso
            with pytest.raises(psycopg.errors.LockNotAvailable):
                ripristina(db, prova=False)
        finally:
            db.execute("set lock_timeout = 0")
            chiudi_tutto(altra)
        assert ripristina(db, prova=False) == sc.atteso

    def test_inserimento_concorrente_conta_in_conflitto(self, db, sc):
        """L'utente salva di nuovo il doppione mentre il ripristino gira: quella
        riga va in conflitto e le altre si ripristinano."""
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: ripristina(db, prova=False))
        try:
            nuovo = salva(altra, sc.u1, DOPPIONE)  # non ancora confermato
            thread.start()
            in_attesa(monitor, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, monitor)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") == conteggi(sb=(2, 1), ce=(3, 0), pc=(1, 0)), esito
        assert riga(db, "saved_bandi", sc.sb_u1)["bando_id"] == MASTER
        assert riga(db, "saved_bandi", nuovo)["bando_id"] == DOPPIONE
        assert len(voci(db)) == 6


# ----------------------------------------------------------------- sicurezza


class TestSicurezza0044:
    def test_firma_e_protezioni(self, db):
        righe_ = db.execute(
            """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                      coalesce(p.proacl::text, ''), p.oid::regprocedure::text,
                      pg_get_function_result(p.oid)
               from pg_proc p join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'public' and p.proname = 'fn_ripristina_rimappatura'""",
        ).fetchall()
        assert len(righe_) == 1
        oid, secdef, config, acl, firma, ritorno = righe_[0]
        assert firma == FIRMA
        assert ritorno == "jsonb"
        assert secdef is True
        assert "search_path=public" in config
        assert acl and not re.search(r"[{,]=X", acl), f"PUBLIC esegue ({acl})"
        for ruolo in ("anon", "authenticated"):
            assert not db.execute(
                "select has_function_privilege(%s, %s::oid, 'execute')", (ruolo, oid)
            ).fetchone()[0], ruolo

    def test_default_di_p_prova_true(self, db):
        argomenti = db.execute(
            "select pg_get_function_arguments('public.fn_ripristina_rimappatura"
            "(integer,timestamptz,boolean)'::regprocedure)"
        ).fetchone()[0]
        assert argomenti == ("p_doppione integer, p_dal timestamp with time zone, "
                             "p_prova boolean DEFAULT true")

    def test_revoca_scritta_nel_file(self):
        assert re.search(
            r"^revoke execute on function public\.fn_ripristina_rimappatura\([^)]*\)\s+"
            r"from public, anon, authenticated;",
            SQL_0044, re.M,
        )

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_ripristina_rimappatura(7001, '-infinity')",
        "select public.fn_ripristina_rimappatura(7001, '-infinity', false)",
    ])
    def test_i_client_non_eseguono_la_rpc(self, db, ruolo, chiamata):
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")
