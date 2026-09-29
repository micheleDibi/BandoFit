"""Test funzionali della migration 0040 (consorzio della call, WP8).

Coprono: vincoli e indici di partner_call_membri (quota, ruoli, stati, esterni,
conferma con quota, una riga per azienda, capofila unico tra i non usciti),
partner_call_documenti e le colonne della validazione su partner_calls; le
righe del consorzio create dall'accettazione (creatore se manca e Y, ruolo e
quota dalla posizione, riammissione di chi era uscito) e dalla pubblicazione;
l'esclusività estesa ai membri (accettazione e pubblicazione, membro uscito
libero, candidatura accettata senza riga, creatore di una call scaduta);
«azienda viva» con il titolare attivo in fn_partner_call_aperta; le RPC dei
membri (aggiorna, conferma, esci, esterno) con i controlli di chi agisce, lo
stato della call, la quota e il ritorno a proposto; i documenti; il
salvataggio della validazione; il backfill idempotente e la sua verifica a
WARNING; cascade; concorrenza (capofila unico con due accettazioni, modifica
contro conferma, esclusività contro pubblicazione); non regressione dei casi
0037/0039 toccati; RLS, privilegi e firme (test generico sulle funzioni).
Ogni test riceve un database fresco clonato dal template.
"""

import base64
import itertools
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0040_partenariato_consorzio.sql"
)
SQL_0040 = MIGRATION.read_text(encoding="utf-8")

TABELLE_NUOVE = {"partner_call_membri", "partner_call_documenti"}
FIRME = {
    "fn_partner_membro_inserisci":
        "fn_partner_membro_inserisci(uuid,uuid,uuid,uuid,text,numeric,uuid,boolean)",
    "fn_partner_membro_creatore": "fn_partner_membro_creatore(uuid,uuid)",
    "fn_partner_membro_aggiorna":
        "fn_partner_membro_aggiorna(uuid,uuid,uuid,uuid,uuid,text,numeric)",
    "fn_partner_membro_conferma":
        "fn_partner_membro_conferma(uuid,uuid,uuid,uuid,text,uuid,numeric)",
    "fn_partner_membro_esci": "fn_partner_membro_esci(uuid,uuid,uuid,uuid)",
    "fn_partner_membro_esterno": "fn_partner_membro_esterno(uuid,uuid,uuid,uuid,jsonb)",
    "fn_partner_documento_stato":
        "fn_partner_documento_stato(uuid,uuid,uuid,uuid,text,text,text)",
    "fn_partner_call_validazione_salva": "fn_partner_call_validazione_salva(uuid,text,numeric)",
    "fn_partner_backfill_membri": "fn_partner_backfill_membri()",
    # Ridefinite con la STESSA firma della 0039.
    "fn_partner_call_aperta": "fn_partner_call_aperta(text,date,date,uuid)",
    "fn_partner_esclusivita_violata":
        "fn_partner_esclusivita_violata(uuid,uuid,integer,boolean)",
    "fn_partner_decidi": "fn_partner_decidi(uuid,uuid,uuid,uuid,text,text,boolean,boolean)",
    "fn_partner_call_pubblica":
        "fn_partner_call_pubblica(uuid,uuid,uuid,uuid,text,date,date,boolean)",
    "fn_partner_call_sostituisci_posizioni":
        "fn_partner_call_sostituisci_posizioni(uuid,uuid,uuid,uuid,jsonb)",
}
RIDEFINITE = {"fn_partner_call_aperta", "fn_partner_esclusivita_violata", "fn_partner_decidi",
              "fn_partner_call_pubblica", "fn_partner_call_sostituisci_posizioni"}
FUNZIONI_NUOVE = set(FIRME) - RIDEFINITE

VERSIONE = "2026-10-bozza-1"
MSG = ("Siamo un organismo di ricerca con esperienza nella prototipazione rapida "
       "di componenti meccanici.")
_TITOLARE = object()  # sentinella: l'attore è il titolare
_ATTUALE = object()   # sentinella: valore attuale della riga del membro

_seq = itertools.count(1)
_bandi = itertools.count(40000)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def vincolo_di(exc) -> str:
    return exc.value.diag.constraint_name or ""


def pseudo() -> str:
    return base64.b32encode(uuid.uuid4().bytes).decode("ascii")[:16]


def new_user(db, plan_slug: str | None = None) -> str:
    uid = str(uuid.uuid4())
    db.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, '{}'::jsonb)",
        (uid, f"{uid[:8]}@test.it"),
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
        (owner, f"ACME {i} Srl", f"{i + 80000000:011d}"),
    ).fetchone()[0])


def importa(db, company: str) -> None:
    """company_data come dopo un import IT-full (identità verificata, T5)."""
    db.execute(
        "insert into public.company_data (company_profile_id, piva_fetched, sandbox, raw, "
        "denominazione, stato_impresa) "
        "select id, partita_iva, false, '{}'::jsonb, ragione_sociale, 'Attiva' "
        "from public.company_profiles where id = %s",
        (company,),
    )


def consenso(db, owner: str, company: str) -> dict:
    return db.execute(
        "select public.fn_partner_consenso(%s::uuid, %s::uuid, %s::uuid, 'concedi', %s::text, "
        "'pagina_azienda', true, true)",
        (owner, company, owner, VERSIONE),
    ).fetchone()[0]


def azienda(db, owner: str | None = None, plan_slug: str | None = "smart", *,
            opt_in: bool = True) -> tuple[str, str]:
    """Titolare + azienda con identità verificata; con opt_in il profilo partner è visibile."""
    owner = owner or new_user(db, plan_slug)
    company = make_company(db, owner)
    importa(db, company)
    if opt_in:
        consenso(db, owner, company)
    return owner, company


def inserisci_call(db, company: str, *, stato: str = "pubblicata",
                   visibilita: str = "pubblica", bando_id: int | None = None,
                   esclusivita: bool = False, scadenza_giorni: int = 30,
                   ruolo_creatore: str = "capofila", quota_creatore=None,
                   pubblicata_at: bool | None = None) -> str:
    """Call scritta direttamente (come dopo le RPC del WP5) nello stato richiesto,
    senza righe del consorzio (come prima della 0040)."""
    owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                           (company,)).fetchone()[0])
    if pubblicata_at is None:
        pubblicata_at = stato in ("pubblicata", "chiusa_completata", "scaduta",
                                  "sospesa_moderazione")
    motivo = {"chiusa_completata": "creatore_completata",
              "chiusa_annullata": "creatore_annullata",
              "scaduta": "scadenza_call"}.get(stato)
    bid = bando_id if bando_id is not None else next(_bandi)
    return str(db.execute(
        """insert into public.partner_calls
             (company_profile_id, family_parent_id, creato_da, bando_id, bando_slug,
              bando_titolo, ruolo_creatore, quota_creatore_pct, titolo, descrizione_pubblica,
              scadenza_call, regole_partenariato, regole_confermate_at, visibilita, stato,
              pubblicata_at, chiusa_at, motivo_chiusura, sospesa_at, stato_prima_sospensione,
              esclusivita)
           values (%s, %s, %s, %s, %s, 'Bando di prova', %s, %s,
                   'Cerchiamo un organismo di ricerca', 'Progetto di ricerca industriale.',
                   current_date + %s, '{}'::jsonb, now(), %s, %s,
                   case when %s then now() end, case when %s::text is not null then now() end,
                   %s, case when %s then now() end, case when %s then 'pubblicata' end, %s)
           returning id""",
        (company, owner, owner, bid, f"bando-{bid}", ruolo_creatore, quota_creatore,
         scadenza_giorni, visibilita, stato, pubblicata_at, motivo, motivo,
         stato == "sospesa_moderazione", stato == "sospesa_moderazione", esclusivita),
    ).fetchone()[0])


def posizione(db, call_id: str, titolo: str = "Organismo di ricerca", *,
              ruolo: str = "partner", quota=None) -> str:
    ordine = db.execute("select count(*) from public.partner_call_posizioni where call_id = %s",
                        (call_id,)).fetchone()[0]
    return str(db.execute(
        "insert into public.partner_call_posizioni (call_id, titolo, ruolo, "
        "quota_ipotizzata_pct, ordine) values (%s, %s, %s, %s, %s) returning id",
        (call_id, titolo, ruolo, quota, ordine)).fetchone()[0])


def requisito(db, call_id: str, etichetta: str = "A") -> str:
    return str(db.execute(
        "insert into public.partner_call_requisiti (call_id, origine, etichetta, testo, cercato) "
        "values (%s, 'manuale', %s, 'Requisito di prova', true) returning id",
        (call_id, etichetta)).fetchone()[0])


def call_di(db, company: str, **kw) -> tuple[str, str]:
    c = inserisci_call(db, company, **kw)
    return c, posizione(db, c)


def porta_in_stato(db, call_id: str, stato: str) -> None:
    """Stato della call scritto direttamente (i trigger della 0039 scattano)."""
    motivo = {"chiusa_completata": "creatore_completata",
              "chiusa_annullata": "creatore_annullata",
              "scaduta": "scadenza_call"}.get(stato)
    sospesa = stato == "sospesa_moderazione"
    db.execute(
        "update public.partner_calls set stato = %s, "
        "chiusa_at = case when %s::text is not null then now() end, motivo_chiusura = %s, "
        "sospesa_at = case when %s then now() end, "
        "stato_prima_sospensione = case when %s then 'pubblicata' end where id = %s",
        (stato, motivo, motivo, sospesa, sospesa, call_id))


def candida(db, owner, company, call_id, pos) -> str:
    payload = {
        "owner_id": owner, "company_id": company, "attore_id": owner, "call_id": call_id,
        "posizione_id": pos, "messaggio": MSG, "requisiti_dichiarati": [],
        "valutazione": {"requisiti_coperti": ["A"]}, "pseudonimo": pseudo(),
    }
    return db.execute("select public.fn_partner_invia_candidatura(%s::jsonb)",
                      (Jsonb(payload),)).fetchone()[0]["candidatura"]["id"]


def invita(db, owner, company, call_id, invitato, *, pos=None) -> str:
    payload = {
        "owner_id": owner, "company_id": company, "attore_id": owner, "call_id": call_id,
        "invitato_company_id": invitato, "posizione_id": pos, "messaggio": None,
        "valutazione": {"requisiti_coperti": ["A"]}, "pseudonimo": pseudo(),
        "max_inviti": 30, "ttl_giorni": 14,
    }
    return db.execute("select public.fn_partner_invita(%s::jsonb)",
                      (Jsonb(payload),)).fetchone()[0]["candidatura"]["id"]


def decidi(db, owner, company, cand_id, decisione="accetta") -> dict:
    """Chiamata per nome come PostgREST, con il default di p_richiedi_non_sandbox."""
    return db.execute(
        "select public.fn_partner_decidi(p_candidatura => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid, p_decisione => %s::text, "
        "p_motivo => null, p_rivela => false)",
        (cand_id, owner, owner, company, decisione),
    ).fetchone()[0]


def pubblica_call(db, owner, company, call_id, bando_stato="aperto") -> dict:
    return db.execute(
        "select public.fn_partner_call_pubblica(%s::uuid, %s::uuid, %s::uuid, %s::uuid, "
        "%s, current_date + 90, current_date + 30, true)",
        (owner, company, owner, call_id, bando_stato),
    ).fetchone()[0]


def bozza_completa(db, company: str, *, bando_id: int, esclusivita: bool = False,
                   ruolo_creatore: str = "capofila", quota_creatore=None) -> str:
    """Bozza pronta per la pubblicazione: testi, regole confermate, una posizione e
    un requisito cercato."""
    c, _ = call_di(db, company, stato="bozza", bando_id=bando_id, esclusivita=esclusivita,
                   ruolo_creatore=ruolo_creatore, quota_creatore=quota_creatore)
    requisito(db, c)
    return c


def chiudi_call(db, owner, company, call_id, esito="completata") -> dict:
    return db.execute(
        "select public.fn_partner_call_chiudi(%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s::text)",
        (owner, company, owner, call_id, esito),
    ).fetchone()[0]


def riga_membro(db, membro_id) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.partner_call_membri where id = %s",
                           (membro_id,)).fetchone()


def per_azienda(db, call_id) -> dict[str, dict]:
    with db.cursor(row_factory=dict_row) as cur:
        righe = cur.execute("select * from public.partner_call_membri where partner_call_id = %s "
                            "and company_profile_id is not null", (call_id,)).fetchall()
    return {str(r["company_profile_id"]): r for r in righe}


def tutte_le_righe(db) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.partner_call_membri order by id").fetchall()


def membro_diretto(db, call_id, **colonne) -> str:
    """Riga del consorzio scritta direttamente (vincoli della tabella). Default: esterno."""
    dati = {"partner_call_id": call_id, "esterno_denominazione": "Ente esterno GmbH",
            "esterno_paese": "DE", "esterno_tipi_soggetto": ["organismo_ricerca"]}
    if colonne.get("company_profile_id"):
        dati = {"partner_call_id": call_id}
    dati.update(colonne)
    nomi = list(dati)
    return str(db.execute(
        f"insert into public.partner_call_membri ({', '.join(nomi)}) "
        f"values ({', '.join(['%s'] * len(nomi))}) returning id",
        list(dati.values()),
    ).fetchone()[0])


def aggiorna(db, owner, company, membro_id, *, attore=_TITOLARE, posizione_=_ATTUALE,
             ruolo=_ATTUALE, quota=_ATTUALE) -> dict:
    attuale = {}
    if _ATTUALE in (posizione_, ruolo, quota):
        attuale = riga_membro(db, membro_id) or {}
    valori = [
        attuale.get("posizione_id") if posizione_ is _ATTUALE else posizione_,
        attuale.get("ruolo") if ruolo is _ATTUALE else ruolo,
        attuale.get("quota_percentuale") if quota is _ATTUALE else quota,
    ]
    return db.execute(
        "select public.fn_partner_membro_aggiorna(p_membro => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid, p_posizione => %s::uuid, "
        "p_ruolo => %s::text, p_quota => %s::numeric)",
        [membro_id, owner if attore is _TITOLARE else attore, owner, company, *valori],
    ).fetchone()[0]


def conferma(db, owner, company, membro_id, *, attore=_TITOLARE, posizione_=_ATTUALE,
             ruolo=_ATTUALE, quota=_ATTUALE) -> dict:
    """Conferma con i termini visti: di default quelli attuali della riga
    (letti prima della chiamata, come li mostra la pagina)."""
    attuale = {}
    if _ATTUALE in (posizione_, ruolo, quota):
        attuale = riga_membro(db, membro_id) or {}
    valori = [
        attuale.get("ruolo") if ruolo is _ATTUALE else ruolo,
        attuale.get("posizione_id") if posizione_ is _ATTUALE else posizione_,
        attuale.get("quota_percentuale") if quota is _ATTUALE else quota,
    ]
    return db.execute(
        "select public.fn_partner_membro_conferma(p_membro => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid, p_ruolo => %s::text, "
        "p_posizione => %s::uuid, p_quota => %s::numeric)",
        [membro_id, owner if attore is _TITOLARE else attore, owner, company,
         valori[0] or "partner", *valori[1:]],
    ).fetchone()[0]


def esci(db, owner, company, membro_id, *, attore=_TITOLARE) -> dict:
    return db.execute(
        "select public.fn_partner_membro_esci(p_membro => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid)",
        (membro_id, owner if attore is _TITOLARE else attore, owner, company),
    ).fetchone()[0]


ESTERNO = {"denominazione": "Fraunhofer Institut", "paese": "DE",
           "tipi_soggetto": ["organismo_ricerca"], "ruolo": "partner", "quota": 15}


def esterno(db, owner, company, call_id, payload=None, *, attore=_TITOLARE, **extra) -> dict:
    dati = dict(ESTERNO if payload is None else payload)
    dati.update(extra)
    return db.execute(
        "select public.fn_partner_membro_esterno(p_call => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid, p_payload => %s::jsonb)",
        (call_id, owner if attore is _TITOLARE else attore, owner, company, Jsonb(dati)),
    ).fetchone()[0]


def documento(db, owner, company, call_id, codice="nda", stato="in_corso", note=None, *,
              attore=_TITOLARE) -> dict:
    return db.execute(
        "select public.fn_partner_documento_stato(p_call => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid, p_codice => %s::text, "
        "p_stato => %s::text, p_note => %s::text)",
        (call_id, owner if attore is _TITOLARE else attore, owner, company, codice, stato,
         note),
    ).fetchone()[0]


def salva_validazione(db, call_id, esito="verde", copertura=None) -> bool:
    return db.execute(
        "select public.fn_partner_call_validazione_salva(p_call => %s::uuid, "
        "p_esito => %s::text, p_copertura => %s::numeric)",
        (call_id, esito, copertura),
    ).fetchone()[0]


def backfill(db) -> int:
    return db.execute("select public.fn_partner_backfill_membri()").fetchone()[0]


def esclusivita(db, company, call_id, bando, esclusiva) -> bool:
    return db.execute("select public.fn_partner_esclusivita_violata(%s, %s, %s, %s)",
                      (company, call_id, bando, esclusiva)).fetchone()[0]


def audit(db, action) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.audit_log where action = %s order by id",
                           (action,)).fetchall()


def conta(db, tabella: str) -> int:
    return db.execute(f"select count(*) from public.{tabella}").fetchone()[0]


def accettata_diretta(db, call_id, company, owner, creatore, pos=None) -> str:
    """Candidatura accettata scritta direttamente (dati precedenti alla 0040)."""
    return str(db.execute(
        "insert into public.partner_candidature (partner_call_id, tipo, company_profile_id, "
        "family_parent_id, creatore_company_profile_id, posizione_id, messaggio, pseudonimo, "
        "inviata_da_user_id, stato, decisa_at, decisa_da_user_id) "
        "values (%s, 'candidatura', %s, %s, %s, %s, %s, %s, %s, 'accettata', %s, %s) "
        "returning id",
        (call_id, company, owner, creatore, pos, MSG, pseudo(), owner,
         datetime.now(timezone.utc), owner),
    ).fetchone()[0])


def in_attesa(monitor, pid, thread, esito, scadenza_s=10) -> None:
    """Attende che il backend `pid` sia bloccato da un lock."""
    scadenza = time.monotonic() + scadenza_s
    while not monitor.execute(
        "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
    ).fetchone()[0]:
        assert time.monotonic() < scadenza, "la transazione non attende"
        assert thread.is_alive(), f"la transazione non ha atteso: {esito}"
        time.sleep(0.02)


def bloccanti(monitor, pid) -> set[int]:
    return set(monitor.execute("select pg_blocking_pids(%s)", (pid,)).fetchone()[0])


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


def errore_detail(esito) -> str:
    exc = esito.get("errore")
    assert exc is not None, esito
    return exc.diag.message_detail or ""


@pytest.fixture()
def sc(db):
    """X (Smart, capofila con quota 40) ha una call pubblicata (senza righe del
    consorzio) con una posizione da 30 e un requisito; Y (Smart, altro owner) ha
    l'opt-in visibile."""
    x_owner, x = azienda(db, opt_in=False)
    y_owner, y = azienda(db)
    call_id = inserisci_call(db, x, quota_creatore=40)
    pos = posizione(db, call_id, quota=30)
    requisito(db, call_id)
    return SimpleNamespace(x_owner=x_owner, x=x, y_owner=y_owner, y=y, call=call_id, pos=pos)


@pytest.fixture()
def cons(db, sc):
    """sc + Y accettata: righe del creatore (mx) e di Y (my) nel consorzio."""
    k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
    out = decidi(db, sc.x_owner, sc.x, k)
    righe = per_azienda(db, sc.call)
    sc.k, sc.my, sc.mx = k, out["membro_id"], str(righe[sc.x]["id"])
    return sc


# ------------------------------------------------------------------ tabelle


class TestMembriTabella:
    def test_default(self, db, sc):
        m = riga_membro(db, membro_diretto(db, sc.call))
        assert (m["stato"], m["ruolo"], m["quota_percentuale"], m["confermato_at"]) == (
            "proposto", "partner", None, None)
        m = riga_membro(db, membro_diretto(db, sc.call, company_profile_id=sc.y))
        assert m["esterno_denominazione"] is None and m["esterno_tipi_soggetto"] is None

    @pytest.mark.parametrize("colonne,vincolo", [
        ({"quota_percentuale": 0}, "pcm_quota_check"),
        ({"quota_percentuale": Decimal("100.01")}, "pcm_quota_check"),
        ({"quota_percentuale": -5}, "pcm_quota_check"),
        ({"ruolo": "osservatore"}, "pcm_ruolo_check"),
        ({"stato": "sospeso"}, "pcm_stato_check"),
        ({"esterno_denominazione": "A"}, "pcm_esterno_denominazione_check"),
        ({"esterno_denominazione": "A" * 201}, "pcm_esterno_denominazione_check"),
        ({"esterno_paese": "it"}, "pcm_esterno_paese_check"),
        ({"esterno_paese": "ITA"}, "pcm_esterno_paese_check"),
        ({"esterno_tipi_soggetto": ["pmi"] * 6}, "pcm_esterno_tipi_soggetto_check"),
        ({"esterno_tipi_soggetto": ["Pmi"]}, "pcm_esterno_tipi_soggetto_check"),
        ({"esterno_tipi_soggetto": ["p"]}, "pcm_esterno_tipi_soggetto_check"),
        ({"esterno_tipi_soggetto": ["pmi", None]}, "pcm_esterno_tipi_soggetto_check"),
        ({"esterno_paese": None}, "pcm_esterno_coerente"),
        ({"esterno_tipi_soggetto": None}, "pcm_esterno_coerente"),
        ({"esterno_denominazione": None}, "pcm_esterno_coerente"),
        ({"stato": "confermato", "quota_percentuale": 10}, "pcm_confermato_coerente"),
        ({"quota_percentuale": 10, "confermato_at": datetime.now(timezone.utc),
          "confermato_da_user_id": str(uuid.uuid4())}, "pcm_confermato_coerente"),
        ({"stato": "confermato", "quota_percentuale": 10,
          "confermato_at": datetime.now(timezone.utc)}, "pcm_confermato_coerente"),
        ({"stato": "confermato", "confermato_at": datetime.now(timezone.utc),
          "confermato_da_user_id": str(uuid.uuid4())}, "pcm_confermato_con_quota"),
    ])
    def test_vincoli_esterno(self, db, sc, colonne, vincolo):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            membro_diretto(db, sc.call, **colonne)
        assert vincolo_di(exc) == vincolo

    @pytest.mark.parametrize("colonne", [
        {"esterno_denominazione": "Ente"}, {"esterno_paese": "IT"},
        {"esterno_tipi_soggetto": []},
    ])
    def test_azienda_senza_campi_esterni(self, db, sc, colonne):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            membro_diretto(db, sc.call, company_profile_id=sc.y, **colonne)
        assert vincolo_di(exc) == "pcm_esterno_coerente"

    def test_esterno_senza_candidatura(self, db, sc):
        k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            membro_diretto(db, sc.call, candidatura_id=k)
        assert vincolo_di(exc) == "pcm_esterno_coerente"

    def test_esterno_confermato_valido(self, db, sc):
        m = membro_diretto(db, sc.call, stato="confermato", quota_percentuale=10,
                           confermato_at=datetime.now(timezone.utc),
                           confermato_da_user_id=sc.x_owner, esterno_tipi_soggetto=[])
        assert riga_membro(db, m)["stato"] == "confermato"

    def test_partner_associato_confermato_senza_quota(self, db, sc):
        """Il partner associato non riceve budget: confermato anche senza quota."""
        m = membro_diretto(db, sc.call, stato="confermato", ruolo="associated_partner",
                           confermato_at=datetime.now(timezone.utc),
                           confermato_da_user_id=sc.x_owner)
        assert riga_membro(db, m)["quota_percentuale"] is None

    def test_una_riga_per_azienda(self, db, sc):
        membro_diretto(db, sc.call, company_profile_id=sc.y)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            membro_diretto(db, sc.call, company_profile_id=sc.y, stato="uscito")
        assert vincolo_di(exc) == "partner_membri_azienda_uq"
        # Esterni con lo stesso nome: ammessi; la stessa azienda su un'altra call pure.
        membro_diretto(db, sc.call)
        membro_diretto(db, sc.call)
        membro_diretto(db, inserisci_call(db, sc.x), company_profile_id=sc.y)

    def test_capofila_unico_tra_i_non_usciti(self, db, sc):
        primo = membro_diretto(db, sc.call, ruolo="capofila")
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            membro_diretto(db, sc.call, company_profile_id=sc.y, ruolo="capofila")
        assert vincolo_di(exc) == "partner_membri_capofila_uq"
        db.execute("update public.partner_call_membri set stato = 'uscito' where id = %s",
                   (primo,))
        membro_diretto(db, sc.call, company_profile_id=sc.y, ruolo="capofila")
        membro_diretto(db, sc.call, ruolo="capofila", stato="uscito")  # uscito: ammesso

    def test_updated_at(self, db, sc):
        m = membro_diretto(db, sc.call)
        prima = riga_membro(db, m)["updated_at"]
        with db.transaction():
            db.execute("update public.partner_call_membri set quota_percentuale = 5 "
                       "where id = %s", (m,))
        assert riga_membro(db, m)["updated_at"] >= prima


class TestDocumentiTabella:
    @pytest.mark.parametrize("colonne,vincolo", [
        ({"codice": "Nda"}, "pcd_codice_check"),
        ({"codice": "n"}, "pcd_codice_check"),
        ({"codice": "a" * 41}, "pcd_codice_check"),
        ({"codice": "nda-1"}, "pcd_codice_check"),
        ({"stato": "boh"}, "pcd_stato_check"),
        ({"note": "x" * 501}, "pcd_note_check"),
    ])
    def test_vincoli(self, db, sc, colonne, vincolo):
        dati = {"partner_call_id": sc.call, "codice": "nda"}
        dati.update(colonne)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(f"insert into public.partner_call_documenti ({', '.join(dati)}) "
                       f"values ({', '.join(['%s'] * len(dati))})", list(dati.values()))
        assert vincolo_di(exc) == vincolo

    def test_chiave_e_default(self, db, sc):
        db.execute("insert into public.partner_call_documenti (partner_call_id, codice) "
                   "values (%s, 'dichiarazioni_affiliated_entities')", (sc.call,))
        assert db.execute("select stato from public.partner_call_documenti").fetchone()[0] == (
            "da_fare")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("insert into public.partner_call_documenti (partner_call_id, codice) "
                       "values (%s, 'dichiarazioni_affiliated_entities')", (sc.call,))


class TestCallColonne:
    @pytest.mark.parametrize("colonne,vincolo", [
        ("validazione_esito = 'giallo', validazione_at = now()",
         "pcall_validazione_esito_check"),
        ("validazione_esito = 'verde'", "pcall_validazione_coerente"),
        ("validazione_at = now()", "pcall_validazione_coerente"),
        ("copertura_gap_ratio = 1.5", "pcall_copertura_gap_ratio_check"),
        ("copertura_gap_ratio = -0.1", "pcall_copertura_gap_ratio_check"),
    ])
    def test_vincoli(self, db, sc, colonne, vincolo):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(f"update public.partner_calls set {colonne} where id = %s", (sc.call,))
        assert vincolo_di(exc) == vincolo

    def test_colonne_fuori_dalla_whitelist_delle_rpc(self, db, sc):
        """crea_bozza e aggiorna non le scrivono (fn_partner_call_campi_editabili)."""
        for pubblicata in (True, False):
            campi = db.execute("select public.fn_partner_call_campi_editabili(%s)",
                               (pubblicata,)).fetchone()[0]
            assert not {"validazione_esito", "validazione_at", "copertura_gap_ratio"} & set(campi)


# ------------------------------------------------------ accettazione (decidi)


class TestAccettazione:
    def test_crea_il_creatore_e_il_membro(self, db, sc):
        k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert per_azienda(db, sc.call) == {}
        out = decidi(db, sc.x_owner, sc.x, k)
        righe = per_azienda(db, sc.call)
        assert set(righe) == {sc.x, sc.y}
        x, y = righe[sc.x], righe[sc.y]
        assert (x["ruolo"], x["quota_percentuale"], x["stato"], x["candidatura_id"],
                x["posizione_id"], x["aggiornato_da_user_id"]) == (
            "capofila", Decimal("40.00"), "proposto", None, None, None)
        assert (y["ruolo"], y["quota_percentuale"], y["stato"], str(y["candidatura_id"]),
                str(y["posizione_id"]), str(y["aggiornato_da_user_id"])) == (
            "partner", Decimal("30.00"), "proposto", k, sc.pos, sc.x_owner)
        assert out["membro_id"] == str(y["id"])
        assert out["candidatura"]["stato"] == "accettata" and out["conversazione_id"]

    def test_il_creatore_non_si_duplica(self, db, sc):
        k1 = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        decidi(db, sc.x_owner, sc.x, k1)
        z_owner, z = azienda(db)
        decidi(db, sc.x_owner, sc.x, candida(db, z_owner, z, sc.call, sc.pos))
        assert set(per_azienda(db, sc.call)) == {sc.x, sc.y, z}
        assert conta(db, "partner_call_membri") == 3

    def test_cerco_capofila(self, db):
        """Creatore che cerca il capofila: entra come partner; Y su una posizione da
        capofila entra come capofila."""
        x_owner, x = azienda(db, opt_in=False)
        y_owner, y = azienda(db)
        c = inserisci_call(db, x, ruolo_creatore="cerco_capofila", quota_creatore=20)
        p = posizione(db, c, "Capofila cercato", ruolo="capofila", quota=60)
        decidi(db, x_owner, x, candida(db, y_owner, y, c, p))
        righe = per_azienda(db, c)
        assert (righe[x]["ruolo"], righe[y]["ruolo"], righe[y]["quota_percentuale"]) == (
            "partner", "capofila", Decimal("60.00"))

    def test_posizione_da_capofila_con_capofila_presente(self, db, sc):
        p = posizione(db, sc.call, "Altro capofila", ruolo="capofila")
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, sc.call, p))
        righe = per_azienda(db, sc.call)
        assert (righe[sc.x]["ruolo"], righe[sc.y]["ruolo"]) == ("capofila", "partner")
        assert righe[sc.y]["quota_percentuale"] is None

    def test_invito_accettato_da_y(self, db, sc):
        i = invita(db, sc.x_owner, sc.x, sc.call, sc.y, pos=sc.pos)
        out = decidi(db, sc.y_owner, sc.y, i)
        righe = per_azienda(db, sc.call)
        assert str(righe[sc.y]["aggiornato_da_user_id"]) == sc.y_owner
        assert righe[sc.x]["aggiornato_da_user_id"] is None  # riga del creatore: sistema
        assert out["membro_id"] == str(righe[sc.y]["id"])
        assert righe[sc.y]["quota_percentuale"] == Decimal("30.00")

    def test_invito_senza_posizione(self, db, sc):
        i = invita(db, sc.x_owner, sc.x, sc.call, sc.y)
        decidi(db, sc.y_owner, sc.y, i)
        y = per_azienda(db, sc.call)[sc.y]
        assert (y["posizione_id"], y["quota_percentuale"], y["ruolo"]) == (None, None, "partner")

    def test_il_rifiuto_non_crea_membri(self, db, sc):
        k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        out = decidi(db, sc.x_owner, sc.x, k, "rifiuta")
        assert conta(db, "partner_call_membri") == 0
        assert "membro_id" not in out

    def test_nella_stessa_transazione(self, db, sc):
        k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        with pytest.raises(psycopg.errors.DivisionByZero):
            with db.transaction():
                decidi(db, sc.x_owner, sc.x, k)
                db.execute("select 1 / 0")
        assert conta(db, "partner_call_membri") == 0

    def test_errore_non_lascia_membri(self, db, sc):
        k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        porta_in_stato(db, sc.call, "sospesa_moderazione")
        db.execute("update public.partner_candidature set stato = 'inviata', chiusa_at = null, "
                   "motivo_chiusura = null where id = %s", (k,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "call_non_attiva"
        assert conta(db, "partner_call_membri") == 0

    def test_riammissione_di_chi_era_uscito(self, db, cons):
        sc = cons
        conferma(db, sc.y_owner, sc.y, sc.my)
        aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo="affiliated_entity", quota=12)
        conferma(db, sc.y_owner, sc.y, sc.my)
        esci(db, sc.y_owner, sc.y, sc.my)
        assert riga_membro(db, sc.my)["stato"] == "uscito"
        # La candidatura accettata non si ritira: per una nuova accettazione sulla
        # stessa call deve sparire (qui: cancellata, candidatura_id → NULL).
        db.execute("delete from public.partner_candidature where id = %s", (sc.k,))
        assert riga_membro(db, sc.my)["candidatura_id"] is None
        p2 = posizione(db, sc.call, "Seconda posizione", quota=25)
        k2 = candida(db, sc.y_owner, sc.y, sc.call, p2)
        out = decidi(db, sc.x_owner, sc.x, k2)
        assert out["membro_id"] == sc.my
        m = riga_membro(db, sc.my)
        assert (m["stato"], str(m["candidatura_id"]), str(m["posizione_id"]), m["ruolo"],
                m["quota_percentuale"], m["confermato_at"], m["confermato_da_user_id"]) == (
            "proposto", k2, p2, "partner", Decimal("25.00"), None, None)
        assert conta(db, "partner_call_membri") == 2

    def test_riga_non_uscita_non_cambia(self, db, sc):
        """Riga già presente e non uscita (per esempio candidatura cancellata): la
        nuova accettazione non la tocca e ne restituisce l'id."""
        m = membro_diretto(db, sc.call, company_profile_id=sc.y, ruolo="affiliated_entity",
                           quota_percentuale=5)
        out = decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, sc.call, sc.pos))
        assert out["membro_id"] == m
        r = riga_membro(db, m)
        assert (r["ruolo"], r["quota_percentuale"], r["candidatura_id"]) == (
            "affiliated_entity", Decimal("5.00"), None)


# -------------------------------------------------------------- pubblicazione


class TestPubblicazione:
    def test_crea_la_riga_del_creatore(self, db):
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        c = bozza_completa(db, company, bando_id=next(_bandi), quota_creatore=55)
        assert pubblica_call(db, owner, company, c)["stato"] == "pubblicata"
        (r,) = per_azienda(db, c).values()
        assert (str(r["company_profile_id"]), r["ruolo"], r["quota_percentuale"], r["stato"],
                str(r["aggiornato_da_user_id"])) == (
            company, "capofila", Decimal("55.00"), "proposto", owner)

    def test_cerco_capofila_entra_come_partner(self, db):
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        c = bozza_completa(db, company, bando_id=next(_bandi), ruolo_creatore="cerco_capofila")
        pubblica_call(db, owner, company, c)
        assert per_azienda(db, c)[company]["ruolo"] == "partner"

    def test_pubblicazione_fallita_non_scrive(self, db):
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        c = bozza_completa(db, company, bando_id=next(_bandi))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica_call(db, owner, company, c, bando_stato="chiuso")
        assert detail_of(exc) == "bando_non_disponibile"
        assert conta(db, "partner_call_membri") == 0

    def test_accettazione_dopo_la_pubblicazione(self, db):
        """Con la riga del creatore già scritta alla pubblicazione, l'accettazione
        aggiunge solo Y."""
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        c = bozza_completa(db, company, bando_id=next(_bandi))
        pubblica_call(db, owner, company, c)
        pos = str(db.execute("select id from public.partner_call_posizioni where call_id = %s",
                             (c,)).fetchone()[0])
        y_owner, y = azienda(db)
        decidi(db, owner, company, candida(db, y_owner, y, c, pos))
        righe = per_azienda(db, c)
        assert set(righe) == {company, y}
        assert str(righe[company]["aggiornato_da_user_id"]) == owner


# ----------------------------------------------------- esclusività sui membri


class TestEsclusivitaMembri:
    def _impegno(self, db, sc, *, esclusiva_altra=False, esclusiva_a=True):
        """Y accettata (con la sua riga) sulla call C di Z; A di X sullo stesso bando."""
        bando = next(_bandi)
        a, pa = call_di(db, sc.x, bando_id=bando, esclusivita=esclusiva_a)
        z_owner, z = azienda(db)
        c, pc = call_di(db, z, bando_id=bando, esclusivita=esclusiva_altra)
        k1 = candida(db, sc.y_owner, sc.y, c, pc)
        m = decidi(db, z_owner, z, k1)["membro_id"]
        return SimpleNamespace(bando=bando, a=a, pa=pa, z_owner=z_owner, z=z, c=c, k1=k1, m=m)

    def test_membro_non_uscito_blocca_l_accettazione(self, db, sc):
        e = self._impegno(db, sc)
        # Anche senza più la candidatura: conta la riga del consorzio.
        db.execute("delete from public.partner_candidature where id = %s", (e.k1,))
        k2 = candida(db, sc.y_owner, sc.y, e.a, e.pa)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k2)
        assert detail_of(exc) == "esclusivita_violata"
        assert per_azienda(db, e.a) == {}

    def test_membro_uscito_e_libero(self, db, sc):
        e = self._impegno(db, sc)
        k2 = candida(db, sc.y_owner, sc.y, e.a, e.pa)
        with pytest.raises(psycopg.errors.RaiseException):
            decidi(db, sc.x_owner, sc.x, k2)
        esci(db, sc.y_owner, sc.y, e.m)
        # La candidatura su C resta accettata, ma la riga uscita prevale.
        assert db.execute("select stato from public.partner_candidature where id = %s",
                          (e.k1,)).fetchone()[0] == "accettata"
        decidi(db, sc.x_owner, sc.x, k2)
        assert per_azienda(db, e.a)[sc.y]["stato"] == "proposto"

    def test_simmetrica_e_p_esclusiva(self, db, sc):
        e = self._impegno(db, sc, esclusiva_altra=True, esclusiva_a=False)
        assert esclusivita(db, sc.y, e.a, e.bando, False) is True   # l'altra è esclusiva
        porta_in_stato(db, e.c, "chiusa_completata")
        assert esclusivita(db, sc.y, e.a, e.bando, False) is True   # completata: impegna
        porta_in_stato(db, e.c, "scaduta")
        assert esclusivita(db, sc.y, e.a, e.bando, False) is True   # scaduta: impegna
        db.execute("update public.partner_calls set stato = 'chiusa_annullata', "
                   "motivo_chiusura = 'creatore_annullata' where id = %s", (e.c,))
        assert esclusivita(db, sc.y, e.a, e.bando, True) is False   # annullata: no
        assert esclusivita(db, sc.y, e.c, e.bando, True) is False   # la stessa call: no

    def test_nessuna_call_esclusiva(self, db, sc):
        e = self._impegno(db, sc, esclusiva_a=False)
        assert esclusivita(db, sc.y, e.a, e.bando, False) is False
        assert esclusivita(db, sc.y, e.a, e.bando, None) is True    # NULL = prudente
        assert esclusivita(db, sc.y, e.a, next(_bandi), True) is False  # altro bando
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, e.a, e.pa))

    def test_candidatura_accettata_senza_riga(self, db, sc):
        """Dati precedenti alla 0040: la candidatura accettata senza riga impegna;
        con una riga uscita no, con una riga non uscita sì."""
        bando = next(_bandi)
        z_owner, z = azienda(db)
        c, pc = call_di(db, z, bando_id=bando, esclusivita=True)
        accettata_diretta(db, c, sc.y, sc.y_owner, z, pc)
        a, _ = call_di(db, sc.x, bando_id=bando)
        assert esclusivita(db, sc.y, a, bando, False) is True
        m = membro_diretto(db, c, company_profile_id=sc.y, stato="uscito")
        assert esclusivita(db, sc.y, a, bando, False) is False
        db.execute("update public.partner_call_membri set stato = 'proposto' where id = %s",
                   (m,))
        assert esclusivita(db, sc.y, a, bando, False) is True

    def test_pubblicazione_con_membro_sullo_stesso_bando(self, db, sc):
        e = self._impegno(db, sc, esclusiva_altra=True)
        db.execute("delete from public.partner_candidature where id = %s", (e.k1,))
        d = bozza_completa(db, sc.y, bando_id=e.bando)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica_call(db, sc.y_owner, sc.y, d)
        assert detail_of(exc) == "esclusivita_violata"
        assert per_azienda(db, d) == {}
        esci(db, sc.y_owner, sc.y, e.m)
        assert pubblica_call(db, sc.y_owner, sc.y, d)["stato"] == "pubblicata"
        assert sc.y in per_azienda(db, d)

    def test_la_riga_del_creatore_non_impegna_il_creatore(self, db):
        """Decisione della 0040: per il creatore vale la regola della 0039 (solo una
        call pubblicata). Una call esclusiva scaduta, con la sua riga nel consorzio,
        non gli blocca per sempre il bando."""
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        bando = next(_bandi)
        vecchia = inserisci_call(db, company, bando_id=bando, esclusivita=True, stato="scaduta")
        backfill(db)
        assert company in per_azienda(db, vecchia)
        nuova = bozza_completa(db, company, bando_id=bando, esclusivita=True)
        assert pubblica_call(db, owner, company, nuova)["stato"] == "pubblicata"
        # Mentre la call è pubblicata, invece, il creatore è impegnato (ramo a).
        assert esclusivita(db, company, str(uuid.uuid4()), bando, True) is True

    @pytest.mark.parametrize("stato", ["chiusa_completata", "scaduta", "sospesa_moderazione"])
    def test_creatore_di_una_call_con_partner_resta_impegnato(self, db, sc, stato):
        """Il partenariato di X è partito (call completata, scaduta o sospesa con
        Y nel consorzio): X non pubblica un'altra call sullo stesso bando
        esclusivo, come Y non vi si candida. Se Y esce, o la call è annullata,
        X torna libero; un esterno non uscito basta a impegnarlo."""
        porta_in_stato(db, sc.call, "chiusa_annullata")  # libera il limite del piano
        bando = next(_bandi)
        a, pa = call_di(db, sc.x, bando_id=bando, esclusivita=True)
        m = decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa))["membro_id"]
        porta_in_stato(db, a, stato)
        altra = str(uuid.uuid4())  # un'altra call qualunque sullo stesso bando
        assert esclusivita(db, sc.x, altra, bando, False) is True
        assert esclusivita(db, sc.y, altra, bando, False) is True  # come Y
        esci(db, sc.y_owner, sc.y, m)
        assert esclusivita(db, sc.x, altra, bando, False) is False
        membro_diretto(db, a)  # un membro esterno non uscito
        assert esclusivita(db, sc.x, altra, bando, False) is True
        # Una sospesa occupa ancora il posto della call attiva sul bando.
        nuova = bozza_completa(db, sc.x, bando_id=bando) if stato != "sospesa_moderazione" \
            else None
        if nuova is not None:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                pubblica_call(db, sc.x_owner, sc.x, nuova)
            assert detail_of(exc) == "esclusivita_violata"
        db.execute("update public.partner_calls set stato = 'chiusa_annullata', "
                   "motivo_chiusura = 'creatore_annullata', sospesa_at = null, "
                   "stato_prima_sospensione = null, "
                   "chiusa_at = coalesce(chiusa_at, now()) where id = %s", (a,))
        assert esclusivita(db, sc.x, altra, bando, False) is False
        if nuova is not None:
            assert pubblica_call(db, sc.x_owner, sc.x, nuova)["stato"] == "pubblicata"


# ------------------------------------------------- «azienda viva» (0040)


class TestCallAperta:
    def _aperta(self, db, call_id) -> bool:
        return db.execute(
            "select public.fn_partner_call_aperta(stato, scadenza_call, bando_scadenza, "
            "company_profile_id) from public.partner_calls where id = %s", (call_id,)
        ).fetchone()[0]

    def test_titolare_disattivato_chiude_la_call(self, db, sc):
        assert self._aperta(db, sc.call) is True
        i = invita(db, sc.x_owner, sc.x, sc.call, sc.y, pos=sc.pos)
        db.execute("update public.profiles set is_active = false where id = %s", (sc.x_owner,))
        assert self._aperta(db, sc.call) is False
        z_owner, z = azienda(db)
        for chiamata in (lambda: candida(db, z_owner, z, sc.call, sc.pos),
                         lambda: invita(db, sc.x_owner, sc.x, sc.call, z),
                         lambda: decidi(db, sc.y_owner, sc.y, i)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                chiamata()
            assert detail_of(exc) == "call_non_attiva"
        db.execute("update public.profiles set is_active = true where id = %s", (sc.x_owner,))
        assert self._aperta(db, sc.call) is True
        decidi(db, sc.y_owner, sc.y, i)

    @pytest.mark.parametrize("colonna", ["archived_at", "deleted_at"])
    def test_azienda_non_viva(self, db, sc, colonna):
        db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                   (sc.x,))
        assert self._aperta(db, sc.call) is False

    def test_senza_titolare(self, db, sc):
        assert db.execute("select public.fn_partner_call_aperta('pubblicata', current_date, "
                          "null, gen_random_uuid())").fetchone()[0] is False


# ------------------------------------------------------------- aggiorna


class TestAggiorna:
    def test_quota_modificata_azzera_la_conferma(self, db, cons):
        sc = cons
        conferma(db, sc.y_owner, sc.y, sc.my)
        out = aggiorna(db, sc.x_owner, sc.x, sc.my, quota=35)
        assert (out["stato_precedente"], out["modificato"]) == ("confermato", True)
        m = riga_membro(db, sc.my)
        assert (m["stato"], m["quota_percentuale"], m["confermato_at"],
                m["confermato_da_user_id"], str(m["aggiornato_da_user_id"])) == (
            "proposto", Decimal("35.00"), None, None, sc.x_owner)
        (a,) = audit(db, "partenariato.membro_aggiornato")
        assert (str(a["actor_id"]), str(a["target_user_id"]), str(a["family_parent_id"])) == (
            sc.x_owner, sc.y_owner, sc.x_owner)
        assert a["payload"]["stato_precedente"] == "confermato"

    @pytest.mark.parametrize("campo", ["ruolo", "posizione", "quota_tolta"])
    def test_ruolo_o_posizione_riportano_a_proposto(self, db, cons, campo):
        sc = cons
        conferma(db, sc.y_owner, sc.y, sc.my)
        if campo == "ruolo":
            aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo="associated_partner")
        elif campo == "posizione":
            aggiorna(db, sc.x_owner, sc.x, sc.my, posizione_=None)
        else:
            aggiorna(db, sc.x_owner, sc.x, sc.my, quota=None)
        assert riga_membro(db, sc.my)["stato"] == "proposto"

    def test_nulla_cambia_nessuna_scrittura(self, db, cons):
        sc = cons
        conferma(db, sc.y_owner, sc.y, sc.my)
        prima = riga_membro(db, sc.my)
        out = aggiorna(db, sc.x_owner, sc.x, sc.my, quota=Decimal("30.001"))  # arrotonda a 30
        assert (out["modificato"], out["membro"]["stato"]) == (False, "confermato")
        assert riga_membro(db, sc.my) == prima
        assert audit(db, "partenariato.membro_aggiornato") == []

    def test_riga_del_creatore_resta_confermata(self, db, cons):
        sc = cons
        conferma(db, sc.x_owner, sc.x, sc.mx)
        aggiorna(db, sc.x_owner, sc.x, sc.mx, quota=45, ruolo="partner")
        m = riga_membro(db, sc.mx)
        assert (m["stato"], m["quota_percentuale"], m["ruolo"]) == (
            "confermato", Decimal("45.00"), "partner")
        assert m["confermato_at"] is not None
        aggiorna(db, sc.x_owner, sc.x, sc.mx, quota=None)  # senza quota non resta confermata
        assert riga_membro(db, sc.mx)["stato"] == "proposto"

    def test_ruolo_non_ammesso_per_il_creatore(self, db, cons):
        sc = cons
        for ruolo in ("affiliated_entity", "associated_partner"):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                aggiorna(db, sc.x_owner, sc.x, sc.mx, ruolo=ruolo)
            assert detail_of(exc) == "ruolo_non_ammesso"
            aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo=ruolo)  # per gli altri sì

    def test_capofila_unico(self, db, cons):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo="capofila")
        assert detail_of(exc) == "capofila_gia_presente"
        aggiorna(db, sc.x_owner, sc.x, sc.mx, ruolo="partner")
        aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo="capofila")
        aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo="capofila", quota=31)  # sé stesso: ok
        e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, e, ruolo="capofila")
        assert detail_of(exc) == "capofila_gia_presente"

    def test_posizione_di_un_altra_call(self, db, cons):
        sc = cons
        _, altra = call_di(db, sc.x)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, sc.my, posizione_=altra)
        assert detail_of(exc) == "posizione_non_valida"
        p2 = posizione(db, sc.call, "Seconda")
        aggiorna(db, sc.x_owner, sc.x, sc.my, posizione_=p2)
        assert str(riga_membro(db, sc.my)["posizione_id"]) == p2

    @pytest.mark.parametrize("campi", [
        {"ruolo": None}, {"ruolo": "osservatore"}, {"quota": 0}, {"quota": Decimal("100.5")},
        {"quota": -1}, {"quota": Decimal("0.004")},
    ])
    def test_parametri_non_validi(self, db, cons, campi):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, sc.my, **campi)
        assert detail_of(exc) == "parametri_non_validi"

    def test_solo_il_creatore(self, db, cons):
        sc = cons
        for owner, company in ((sc.y_owner, sc.y), azienda(db)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                aggiorna(db, owner, company, sc.my, quota=20)
            assert detail_of(exc) == "membro_non_trovato"
        # Advisor: un'altra azienda dello stesso titolare non tocca la call di X.
        altra = make_company(db, sc.x_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, altra, sc.my, quota=20)
        assert detail_of(exc) == "membro_non_trovato"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, sc.my, quota=20, attore=sc.y_owner)
        assert detail_of(exc) == "attore_non_titolare"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, str(uuid.uuid4()), ruolo="partner", quota=20,
                     posizione_=None)
        assert detail_of(exc) == "membro_non_trovato"
        assert riga_membro(db, sc.my)["quota_percentuale"] == Decimal("30.00")

    @pytest.mark.parametrize("stato", ["chiusa_annullata", "sospesa_moderazione"])
    def test_call_non_modificabile(self, db, cons, stato):
        sc = cons
        if stato == "chiusa_annullata":
            chiudi_call(db, sc.x_owner, sc.x, sc.call, "annullata")
        else:
            porta_in_stato(db, sc.call, stato)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, sc.my, quota=20)
        assert detail_of(exc) == "call_non_modificabile"

    def test_chiusa_completata_modificabile(self, db, cons):
        sc = cons
        chiudi_call(db, sc.x_owner, sc.x, sc.call, "completata")
        assert aggiorna(db, sc.x_owner, sc.x, sc.my, quota=20)["modificato"] is True

    def test_scaduta_modificabile(self, db, cons):
        """Alla scadenza della ricerca di partner (scheduler, stato terminale) il
        partenariato può andare avanti: il consorzio resta modificabile come
        per una call completata (membri, conferme, esterni, documenti)."""
        sc = cons
        porta_in_stato(db, sc.call, "scaduta")
        assert aggiorna(db, sc.x_owner, sc.x, sc.my, quota=20)["modificato"] is True
        assert conferma(db, sc.y_owner, sc.y, sc.my)["membro"]["stato"] == "confermato"
        assert conferma(db, sc.x_owner, sc.x, sc.mx)["membro"]["stato"] == "confermato"
        e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        assert documento(db, sc.x_owner, sc.x, sc.call, "nda", "fatto")["stato"] == "fatto"
        assert esci(db, sc.x_owner, sc.x, e)["origine"] == "creatore"

    def test_membro_uscito(self, db, cons):
        sc = cons
        esci(db, sc.y_owner, sc.y, sc.my)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, sc.my, quota=20)
        assert detail_of(exc) == "membro_uscito"

    def test_azienda_del_creatore_non_viva(self, db, cons):
        sc = cons
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (sc.x,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            aggiorna(db, sc.x_owner, sc.x, sc.my, quota=20)
        assert detail_of(exc) == "company_not_found"

    def test_esterno_torna_proposto(self, db, cons):
        sc = cons
        e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        conferma(db, sc.x_owner, sc.x, e)
        aggiorna(db, sc.x_owner, sc.x, e, quota=16)
        assert riga_membro(db, e)["stato"] == "proposto"


# ------------------------------------------------------------- posizioni


def sostituisci_posizioni(db, owner, company, call_id, lista) -> list:
    return db.execute(
        "select public.fn_partner_call_sostituisci_posizioni(%s::uuid, %s::uuid, %s::uuid, "
        "%s::uuid, %s::jsonb)",
        (owner, company, owner, call_id, Jsonb(lista)),
    ).fetchone()[0]


class TestPosizioniConMembri:
    def test_posizione_con_membro_non_si_rimuove(self, db, cons):
        """Y (confermata) spostata su una posizione senza candidature, e un
        esterno su un'altra: nessuna delle due si toglie (prima la
        cancellazione lasciava Y confermata senza posizione). Da uscito il
        membro non blocca più."""
        sc = cons
        p2 = posizione(db, sc.call, "Seconda posizione")
        p3 = posizione(db, sc.call, "Terza posizione")
        aggiorna(db, sc.x_owner, sc.x, sc.my, posizione_=p2)
        conferma(db, sc.y_owner, sc.y, sc.my)
        e = esterno(db, sc.x_owner, sc.x, sc.call, posizione_id=p3)["membro"]["id"]
        p1 = {"id": sc.pos, "titolo": "Organismo di ricerca"}
        for tenute in ([p1, {"id": p3, "titolo": "Terza posizione"}],
                       [p1, {"id": p2, "titolo": "Seconda posizione"}]):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call, tenute)
            assert detail_of(exc) == "posizione_con_membri"
        m = riga_membro(db, sc.my)
        assert (str(m["posizione_id"]), m["stato"]) == (p2, "confermato")
        esci(db, sc.y_owner, sc.y, sc.my)
        esci(db, sc.x_owner, sc.x, e)
        out = sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call, [p1])
        assert [str(p["id"]) for p in out] == [sc.pos]
        assert riga_membro(db, sc.my)["posizione_id"] is None  # ON DELETE SET NULL


# ------------------------------------------------------------- conferma


class TestConferma:
    def test_y_conferma_la_sua_riga(self, db, cons):
        sc = cons
        out = conferma(db, sc.y_owner, sc.y, sc.my)
        assert (out["stato_precedente"], out["modificato"]) == ("proposto", True)
        m = riga_membro(db, sc.my)
        assert (m["stato"], str(m["confermato_da_user_id"])) == ("confermato", sc.y_owner)
        assert m["confermato_at"] is not None
        (a,) = audit(db, "partenariato.membro_confermato")
        assert (str(a["actor_id"]), str(a["target_user_id"]), str(a["family_parent_id"])) == (
            sc.y_owner, sc.x_owner, sc.y_owner)

    def test_quota_mancante(self, db, sc):
        p = posizione(db, sc.call, "Senza quota")
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, sc.call, p))
        m = str(per_azienda(db, sc.call)[sc.y]["id"])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, sc.y_owner, sc.y, m)
        assert detail_of(exc) == "quota_mancante"
        aggiorna(db, sc.x_owner, sc.x, m, quota=10)
        assert conferma(db, sc.y_owner, sc.y, m)["membro"]["stato"] == "confermato"

    def test_il_creatore_conferma_sé_e_gli_esterni(self, db, cons):
        sc = cons
        e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        conferma(db, sc.x_owner, sc.x, sc.mx)
        conferma(db, sc.x_owner, sc.x, e)
        assert (riga_membro(db, sc.mx)["stato"], riga_membro(db, e)["stato"]) == (
            "confermato", "confermato")
        assert str(riga_membro(db, e)["confermato_da_user_id"]) == sc.x_owner

    def test_termini_cambiati_membro_modificato(self, db, cons):
        """Si conferma solo ciò che si è visto: ruolo, posizione o quota diversi
        da quelli della riga → membro_modificato, nessuna scrittura."""
        sc = cons
        vista = riga_membro(db, sc.my)
        aggiorna(db, sc.x_owner, sc.x, sc.my, quota=35)
        altra = posizione(db, sc.call, "Altra posizione")
        for campi in ({"quota": vista["quota_percentuale"]}, {"ruolo": "affiliated_entity"},
                      {"posizione_": altra}, {"posizione_": None}):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                conferma(db, sc.y_owner, sc.y, sc.my, **campi)
            assert detail_of(exc) == "membro_modificato", campi
        assert riga_membro(db, sc.my)["stato"] == "proposto"
        assert audit(db, "partenariato.membro_confermato") == []
        assert conferma(db, sc.y_owner, sc.y, sc.my, quota=Decimal("35.004"))["modificato"]

    def test_partner_associato_conferma_senza_quota(self, db, cons):
        sc = cons
        aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo="associated_partner", quota=None)
        assert conferma(db, sc.y_owner, sc.y, sc.my)["membro"]["stato"] == "confermato"
        # Tornato partner senza quota: di nuovo proposto, e senza quota non conferma.
        aggiorna(db, sc.x_owner, sc.x, sc.my, ruolo="partner", quota=None)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, sc.y_owner, sc.y, sc.my)
        assert detail_of(exc) == "quota_mancante"

    def test_nessuno_conferma_per_altri(self, db, cons):
        sc = cons
        e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        z_owner, z = azienda(db)
        casi = [(sc.x_owner, sc.x, sc.my),   # il creatore non conferma per Y
                (sc.y_owner, sc.y, sc.mx),   # Y non conferma il creatore
                (sc.y_owner, sc.y, e),       # né un esterno
                (z_owner, z, sc.my),
                (sc.x_owner, make_company(db, sc.x_owner), e),  # altra azienda del titolare
                (sc.x_owner, sc.x, str(uuid.uuid4()))]
        for owner, company, membro_id in casi:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                conferma(db, owner, company, membro_id)
            assert detail_of(exc) == "membro_non_trovato"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, sc.y_owner, sc.y, sc.my, attore=sc.x_owner)
        assert detail_of(exc) == "attore_non_titolare"
        assert riga_membro(db, sc.my)["stato"] == "proposto"

    def test_idempotente(self, db, cons):
        sc = cons
        conferma(db, sc.y_owner, sc.y, sc.my)
        prima = riga_membro(db, sc.my)
        out = conferma(db, sc.y_owner, sc.y, sc.my)
        assert (out["stato_precedente"], out["modificato"]) == ("confermato", False)
        assert riga_membro(db, sc.my) == prima
        assert len(audit(db, "partenariato.membro_confermato")) == 1

    def test_membro_uscito(self, db, cons):
        sc = cons
        esci(db, sc.y_owner, sc.y, sc.my)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, sc.y_owner, sc.y, sc.my)
        assert detail_of(exc) == "membro_uscito"

    @pytest.mark.parametrize("stato", ["chiusa_annullata", "sospesa_moderazione"])
    def test_call_non_modificabile(self, db, cons, stato):
        sc = cons
        porta_in_stato(db, sc.call, stato)
        for owner, company, m in ((sc.y_owner, sc.y, sc.my), (sc.x_owner, sc.x, sc.mx)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                conferma(db, owner, company, m)
            assert detail_of(exc) == "call_non_modificabile"

    def test_chiusa_completata(self, db, cons):
        sc = cons
        chiudi_call(db, sc.x_owner, sc.x, sc.call, "completata")
        assert conferma(db, sc.y_owner, sc.y, sc.my)["membro"]["stato"] == "confermato"

    def test_azienda_del_membro_non_viva(self, db, cons):
        sc = cons
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (sc.y,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conferma(db, sc.y_owner, sc.y, sc.my)
        assert detail_of(exc) == "azienda_non_disponibile"


# ------------------------------------------------------------------ esci


class TestEsci:
    def test_il_creatore_rimuove(self, db, cons):
        sc = cons
        conferma(db, sc.y_owner, sc.y, sc.my)
        out = esci(db, sc.x_owner, sc.x, sc.my)
        assert (out["origine"], out["stato_precedente"], out["modificato"]) == (
            "creatore", "confermato", True)
        m = riga_membro(db, sc.my)
        assert (m["stato"], m["confermato_at"], m["confermato_da_user_id"],
                str(m["aggiornato_da_user_id"])) == ("uscito", None, None, sc.x_owner)
        (a,) = audit(db, "partenariato.membro_uscito")
        assert (str(a["target_user_id"]), a["payload"]["origine"]) == (sc.y_owner, "creatore")

    def test_il_membro_esce_da_se(self, db, cons):
        sc = cons
        out = esci(db, sc.y_owner, sc.y, sc.my)
        assert (out["origine"], riga_membro(db, sc.my)["stato"]) == ("membro", "uscito")
        (a,) = audit(db, "partenariato.membro_uscito")
        assert (str(a["actor_id"]), str(a["target_user_id"])) == (sc.y_owner, sc.x_owner)

    def test_la_riga_del_creatore_non_si_tocca(self, db, cons):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esci(db, sc.x_owner, sc.x, sc.mx)
        assert detail_of(exc) == "membro_non_rimovibile"
        z_owner, z = azienda(db)
        for owner, company, m in ((sc.y_owner, sc.y, sc.mx), (z_owner, z, sc.my),
                                  (sc.y_owner, sc.y, str(uuid.uuid4()))):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                esci(db, owner, company, m)
            assert detail_of(exc) == "membro_non_trovato"
        e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esci(db, sc.y_owner, sc.y, e)
        assert detail_of(exc) == "membro_non_trovato"
        assert esci(db, sc.x_owner, sc.x, e)["origine"] == "creatore"
        assert riga_membro(db, sc.mx)["stato"] == "proposto"

    @pytest.mark.parametrize("stato", ["chiusa_annullata", "sospesa_moderazione"])
    def test_uscire_e_sempre_possibile_rimuovere_no(self, db, cons, stato):
        sc = cons
        porta_in_stato(db, sc.call, stato)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esci(db, sc.x_owner, sc.x, sc.my)
        assert detail_of(exc) == "call_non_modificabile"
        assert esci(db, sc.y_owner, sc.y, sc.my)["modificato"] is True

    def test_esce_anche_con_l_azienda_archiviata(self, db, cons):
        sc = cons
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (sc.y,))
        assert esci(db, sc.y_owner, sc.y, sc.my)["membro"]["stato"] == "uscito"

    def test_idempotente(self, db, cons):
        sc = cons
        esci(db, sc.y_owner, sc.y, sc.my)
        out = esci(db, sc.x_owner, sc.x, sc.my)
        assert (out["modificato"], out["stato_precedente"]) == (False, "uscito")
        assert len(audit(db, "partenariato.membro_uscito")) == 1

    def test_attore_non_titolare(self, db, cons):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esci(db, sc.y_owner, sc.y, sc.my, attore=str(uuid.uuid4()))
        assert detail_of(exc) == "attore_non_titolare"


# ------------------------------------------------------------ esterni


class TestEsterno:
    def test_aggiunge(self, db, cons):
        sc = cons
        out = esterno(db, sc.x_owner, sc.x, sc.call, posizione_id=sc.pos)
        assert (out["creato"], out["modificato"], out["stato_precedente"]) == (True, True, None)
        m = riga_membro(db, out["membro"]["id"])
        assert (m["company_profile_id"], m["esterno_denominazione"], m["esterno_paese"],
                m["esterno_tipi_soggetto"], m["ruolo"], m["quota_percentuale"], m["stato"],
                str(m["posizione_id"]), str(m["aggiornato_da_user_id"])) == (
            None, "Fraunhofer Institut", "DE", ["organismo_ricerca"], "partner",
            Decimal("15.00"), "proposto", sc.pos, sc.x_owner)
        (a,) = audit(db, "partenariato.membro_aggiunto")
        assert "Fraunhofer" not in str(a["payload"]) and a["payload"]["esterno"] is True

    def test_default(self, db, cons):
        sc = cons
        out = esterno(db, sc.x_owner, sc.x, sc.call,
                      {"denominazione": "  Università di Graz  ", "paese": "AT"})
        m = riga_membro(db, out["membro"]["id"])
        assert (m["esterno_denominazione"], m["ruolo"], m["esterno_tipi_soggetto"],
                m["quota_percentuale"]) == ("Università di Graz", "partner", [], None)

    @pytest.mark.parametrize("payload", [
        {**ESTERNO, "sconosciuta": 1},
        {**ESTERNO, "denominazione": "A"},
        {**ESTERNO, "denominazione": "   "},
        {**ESTERNO, "denominazione": "A" * 201},
        {k: v for k, v in ESTERNO.items() if k != "denominazione"},
        {**ESTERNO, "paese": "de"},
        {**ESTERNO, "paese": "DEU"},
        {k: v for k, v in ESTERNO.items() if k != "paese"},
        {**ESTERNO, "tipi_soggetto": ["pmi"] * 6},
        {**ESTERNO, "tipi_soggetto": ["pmi", "startup_innovativa", "pmi"]},
        {**ESTERNO, "tipi_soggetto": ["Pmi"]},
        {**ESTERNO, "tipi_soggetto": [1]},
        {**ESTERNO, "tipi_soggetto": "pmi"},
        {**ESTERNO, "quota": 0},
        {**ESTERNO, "quota": 100.5},
        {**ESTERNO, "quota": "tanta"},
        {**ESTERNO, "ruolo": "osservatore"},
        {**ESTERNO, "membro_id": "non-un-uuid"},
        {**ESTERNO, "posizione_id": 12},
    ])
    def test_parametri_non_validi(self, db, cons, payload):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call, payload)
        assert detail_of(exc) == "parametri_non_validi"

    def test_payload_non_oggetto(self, db, cons):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_partner_membro_esterno(%s, %s, %s, %s, '[]'::jsonb)",
                       (sc.call, sc.x_owner, sc.x_owner, sc.x))
        assert detail_of(exc) == "parametri_non_validi"

    def test_posizione_di_un_altra_call(self, db, cons):
        sc = cons
        _, altra = call_di(db, sc.x)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call, posizione_id=altra)
        assert detail_of(exc) == "posizione_non_valida"

    def test_solo_il_creatore(self, db, cons):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call, attore=sc.y_owner)
        assert detail_of(exc) == "attore_non_titolare"

    @pytest.mark.parametrize("stato", ["chiusa_annullata", "sospesa_moderazione"])
    def test_call_non_modificabile(self, db, cons, stato):
        sc = cons
        porta_in_stato(db, sc.call, stato)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call)
        assert detail_of(exc) == "call_non_modificabile"

    def test_capofila_gia_presente(self, db, cons):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call, ruolo="capofila")
        assert detail_of(exc) == "capofila_gia_presente"

    def test_tetto_dei_membri(self, db, cons):
        sc = cons  # X e Y: 2 membri non usciti
        ids = [esterno(db, sc.x_owner, sc.x, sc.call, denominazione=f"Ente {i}")["membro"]["id"]
               for i in range(28)]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call)
        assert detail_of(exc) == "limite_membri"
        esci(db, sc.x_owner, sc.x, ids[0])
        nuovo = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        # Riproporre un esterno uscito conta nel tetto.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call, membro_id=ids[0])
        assert detail_of(exc) == "limite_membri"
        esci(db, sc.x_owner, sc.x, nuovo)
        assert esterno(db, sc.x_owner, sc.x, sc.call, membro_id=ids[0])["membro"]["stato"] == (
            "proposto")

    def test_tetto_delle_righe_esterne(self, db, cons):
        sc = cons
        for i in range(60):
            membro_diretto(db, sc.call, esterno_denominazione=f"Uscito {i}", stato="uscito")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            esterno(db, sc.x_owner, sc.x, sc.call)
        assert detail_of(exc) == "limite_membri"
        # Un esterno uscito si ripropone modificandolo.
        uscito = str(db.execute("select id from public.partner_call_membri "
                                "where company_profile_id is null limit 1").fetchone()[0])
        out = esterno(db, sc.x_owner, sc.x, sc.call, membro_id=uscito)
        assert (out["creato"], out["stato_precedente"], out["membro"]["stato"]) == (
            False, "uscito", "proposto")

    def test_modifica(self, db, cons):
        sc = cons
        e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
        conferma(db, sc.x_owner, sc.x, e)
        stesso = esterno(db, sc.x_owner, sc.x, sc.call, membro_id=e)
        assert (stesso["modificato"], stesso["membro"]["stato"]) == (False, "confermato")
        assert audit(db, "partenariato.membro_aggiornato") == []
        out = esterno(db, sc.x_owner, sc.x, sc.call, membro_id=e, paese="AT",
                      tipi_soggetto=["universita", "organismo_ricerca"])
        assert (out["creato"], out["modificato"], out["stato_precedente"]) == (
            False, True, "confermato")
        m = riga_membro(db, e)
        assert (m["esterno_paese"], m["esterno_tipi_soggetto"], m["stato"],
                m["confermato_at"]) == ("AT", ["universita", "organismo_ricerca"],
                                        "proposto", None)
        assert len(audit(db, "partenariato.membro_aggiornato")) == 1
        assert conta(db, "partner_call_membri") == 3

    def test_membro_id_non_esterno(self, db, cons):
        sc = cons
        altra = inserisci_call(db, sc.x)
        e_altra = membro_diretto(db, altra)
        for m in (sc.my, sc.mx, e_altra, str(uuid.uuid4())):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                esterno(db, sc.x_owner, sc.x, sc.call, membro_id=m)
            assert detail_of(exc) == "membro_non_trovato"


# ------------------------------------------------------------ documenti


class TestDocumenti:
    def test_upsert(self, db, cons):
        sc = cons
        r = documento(db, sc.x_owner, sc.x, sc.call, "mandato_collettivo", "in_corso",
                      "  Bozza dal notaio  ")
        assert (r["stato"], r["note"], r["aggiornato_da_user_id"]) == (
            "in_corso", "Bozza dal notaio", sc.x_owner)
        r = documento(db, sc.x_owner, sc.x, sc.call, "mandato_collettivo", "fatto", "   ")
        assert (r["stato"], r["note"]) == ("fatto", None)
        documento(db, sc.x_owner, sc.x, sc.call, "nda", "non_applicabile")
        assert conta(db, "partner_call_documenti") == 2

    @pytest.mark.parametrize("codice", ["Nda", "n", "a" * 41, "nda 1", None, "nda\n"])
    def test_codice_non_valido(self, db, cons, codice):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            documento(db, sc.x_owner, sc.x, sc.call, codice)
        assert detail_of(exc) == "documento_non_valido"

    @pytest.mark.parametrize("stato,note", [("boh", None), (None, None),
                                            ("fatto", "x" * 501)])
    def test_parametri_non_validi(self, db, cons, stato, note):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            documento(db, sc.x_owner, sc.x, sc.call, "nda", stato, note)
        assert detail_of(exc) == "parametri_non_validi"

    def test_solo_il_creatore(self, db, cons):
        sc = cons
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            documento(db, sc.y_owner, sc.y, sc.call)
        assert detail_of(exc) == "call_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            documento(db, sc.x_owner, sc.x, sc.call, attore=sc.y_owner)
        assert detail_of(exc) == "attore_non_titolare"
        assert conta(db, "partner_call_documenti") == 0

    def test_call_non_modificabile(self, db, cons):
        sc = cons
        documento(db, sc.x_owner, sc.x, sc.call)
        chiudi_call(db, sc.x_owner, sc.x, sc.call, "annullata")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            documento(db, sc.x_owner, sc.x, sc.call, "nda", "fatto")
        assert detail_of(exc) == "call_non_modificabile"


# ------------------------------------------------------------ validazione


class TestValidazioneSalva:
    def test_salva(self, db, sc):
        assert salva_validazione(db, sc.call, "grigio", Decimal("0.66666")) is True
        r = db.execute("select validazione_esito, validazione_at, copertura_gap_ratio "
                       "from public.partner_calls where id = %s", (sc.call,)).fetchone()
        assert (r[0], r[2]) == ("grigio", Decimal("0.667")) and r[1] is not None
        assert salva_validazione(db, sc.call, "verde", None) is True
        assert db.execute("select copertura_gap_ratio from public.partner_calls where id = %s",
                          (sc.call,)).fetchone()[0] is None

    def test_call_inesistente(self, db):
        assert salva_validazione(db, str(uuid.uuid4())) is False

    @pytest.mark.parametrize("esito,copertura", [(None, None), ("giallo", None),
                                                 ("verde", Decimal("1.2")),
                                                 ("rosso", Decimal("-0.1"))])
    def test_parametri_non_validi(self, db, sc, esito, copertura):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            salva_validazione(db, sc.call, esito, copertura)
        assert detail_of(exc) == "parametri_non_validi"

    def test_non_cambia_versione_ne_stato(self, db, sc):
        prima = db.execute("select stato, versione from public.partner_calls where id = %s",
                           (sc.call,)).fetchone()
        salva_validazione(db, sc.call, "rosso", Decimal("0.5"))
        assert db.execute("select stato, versione from public.partner_calls where id = %s",
                          (sc.call,)).fetchone() == prima


# ------------------------------------------------------------ backfill


class TestBackfill:
    def _dati_precedenti(self, db):
        """Call e candidature come prima della 0040 (senza righe del consorzio)."""
        x_owner, x = azienda(db, plan_slug="pro", opt_in=False)
        y_owner, y = azienda(db)
        w_owner, w = azienda(db)
        calls = {
            "pubblicata": inserisci_call(db, x, quota_creatore=40),
            "completata": inserisci_call(db, x, stato="chiusa_completata"),
            "scaduta": inserisci_call(db, x, stato="scaduta", ruolo_creatore="cerco_capofila"),
            "annullata_dopo": inserisci_call(db, x, stato="chiusa_annullata",
                                             pubblicata_at=True),
            "annullata_bozza": inserisci_call(db, x, stato="chiusa_annullata"),
            "bozza": inserisci_call(db, x, stato="bozza"),
        }
        p_cap = posizione(db, calls["scaduta"], "Capofila", ruolo="capofila", quota=50)
        p_pub = posizione(db, calls["pubblicata"], quota=30)
        k_y = accettata_diretta(db, calls["pubblicata"], y, y_owner, x, p_pub)
        k_cap = accettata_diretta(db, calls["scaduta"], y, y_owner, x, p_cap)
        k_w = accettata_diretta(db, calls["annullata_dopo"], w, w_owner, x)
        # Non accettate: nessuna riga.
        db.execute("update public.partner_candidature set stato = 'rifiutata' where id = %s",
                   (accettata_diretta(db, calls["completata"], w, w_owner, x),))
        return SimpleNamespace(x=x, y=y, w=w, calls=calls, k_y=k_y, k_cap=k_cap, k_w=k_w,
                               p_pub=p_pub, p_cap=p_cap)

    def test_idempotente(self, db):
        d = self._dati_precedenti(db)
        assert backfill(db) == 4 + 3
        prima = tutte_le_righe(db)
        assert backfill(db) == 0
        assert tutte_le_righe(db) == prima
        for nome in ("bozza", "annullata_bozza"):
            assert per_azienda(db, d.calls[nome]) == {}
        assert set(per_azienda(db, d.calls["completata"])) == {d.x}

    def test_ruoli_quote_e_posizioni(self, db):
        d = self._dati_precedenti(db)
        backfill(db)
        pub = per_azienda(db, d.calls["pubblicata"])
        assert (pub[d.x]["ruolo"], pub[d.x]["quota_percentuale"], pub[d.x]["stato"]) == (
            "capofila", Decimal("40.00"), "proposto")
        assert (str(pub[d.y]["candidatura_id"]), str(pub[d.y]["posizione_id"]),
                pub[d.y]["quota_percentuale"], pub[d.y]["ruolo"],
                pub[d.y]["aggiornato_da_user_id"]) == (
            d.k_y, d.p_pub, Decimal("30.00"), "partner", None)
        scad = per_azienda(db, d.calls["scaduta"])
        assert (scad[d.x]["ruolo"], scad[d.y]["ruolo"], scad[d.y]["quota_percentuale"]) == (
            "partner", "capofila", Decimal("50.00"))
        ann = per_azienda(db, d.calls["annullata_dopo"])
        assert set(ann) == {d.x, d.w} and ann[d.w]["posizione_id"] is None

    def test_non_tocca_le_righe_esistenti(self, db):
        d = self._dati_precedenti(db)
        m = membro_diretto(db, d.calls["pubblicata"], company_profile_id=d.y, stato="uscito")
        backfill(db)
        r = riga_membro(db, m)
        assert (r["stato"], r["candidatura_id"]) == ("uscito", None)

    def test_eseguito_nella_migration_con_verifica(self, db):
        """Il file lancia il backfill e verifica a WARNING (il DB del harness è vuoto)."""
        assert re.search(r"^select public\.fn_partner_backfill_membri\(\);", SQL_0040, re.M)
        blocco = re.search(r"^do \$\$\n.*?^\$\$;", SQL_0040, re.M | re.S).group(0)
        assert "raise warning" in blocco
        avvisi: list[str] = []
        db.add_notice_handler(
            lambda d: avvisi.append(f"{d.severity_nonlocalized}: {d.message_primary}"))
        db.execute(blocco)
        assert avvisi == []
        self._dati_precedenti(db)
        db.execute(blocco)
        assert len(avvisi) == 1 and avvisi[0].startswith("WARNING: backfill 0040: 4 call")
        backfill(db)
        db.execute(blocco)
        assert len(avvisi) == 1


# ------------------------------------------------------------ cascade


class TestCascade:
    def test_azienda_del_membro(self, db, cons):
        sc = cons
        documento(db, sc.x_owner, sc.x, sc.call)
        db.execute("delete from public.company_profiles where id = %s", (sc.y,))
        assert riga_membro(db, sc.my) is None and riga_membro(db, sc.mx) is not None
        assert conta(db, "partner_call_documenti") == 1

    def test_owner_del_membro(self, db, cons):
        sc = cons
        db.execute("delete from auth.users where id = %s", (sc.y_owner,))
        assert riga_membro(db, sc.my) is None and riga_membro(db, sc.mx) is not None

    def test_call_e_azienda_creatrice(self, db, cons):
        sc = cons
        esterno(db, sc.x_owner, sc.x, sc.call)
        documento(db, sc.x_owner, sc.x, sc.call)
        altra = inserisci_call(db, sc.x)
        membro_diretto(db, altra)
        db.execute("delete from public.partner_calls where id = %s", (sc.call,))
        assert conta(db, "partner_call_membri") == 1 and conta(db, "partner_call_documenti") == 0
        db.execute("delete from public.company_profiles where id = %s", (sc.x,))
        assert conta(db, "partner_call_membri") == 0

    def test_candidatura_e_posizione_set_null(self, db, cons):
        sc = cons
        db.execute("delete from public.partner_candidature where id = %s", (sc.k,))
        m = riga_membro(db, sc.my)
        assert (m["candidatura_id"], m["stato"]) == (None, "proposto")
        db.execute("delete from public.partner_call_posizioni where id = %s", (sc.pos,))
        assert riga_membro(db, sc.my)["posizione_id"] is None


# ------------------------------------------------------------ concorrenza


class TestConcorrenza:
    def test_due_accettazioni_un_solo_capofila(self, db):
        """Y1 e Y2 accettano insieme un invito sulla posizione da capofila: la seconda
        attende l'indice del capofila e, dopo il commit della prima, entra come
        partner senza errori."""
        x_owner, x = azienda(db, opt_in=False)
        c = inserisci_call(db, x, ruolo_creatore="cerco_capofila")
        p = posizione(db, c, "Capofila cercato", ruolo="capofila", quota=50)
        backfill(db)  # riga del creatore (partner) già presente
        y1_owner, y1 = azienda(db)
        y2_owner, y2 = azienda(db)
        i1 = invita(db, x_owner, x, c, y1, pos=p)
        i2 = invita(db, x_owner, x, c, y2, pos=p)
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: decidi(conn_b, y2_owner, y2, i2))
        try:
            decidi(conn_a, y1_owner, y1, i1)
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            assert conn_a.info.backend_pid in bloccanti(monitor, conn_b.info.backend_pid)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert "errore" not in eb, eb
        righe = per_azienda(db, c)
        assert (righe[y1]["ruolo"], righe[y2]["ruolo"], righe[x]["ruolo"]) == (
            "capofila", "partner", "partner")

    def test_la_modifica_attende_la_conferma(self, db, cons):
        """La conferma di Y tiene la call FOR SHARE: la modifica del creatore (call
        FOR UPDATE) attende, poi riporta Y a proposto."""
        sc = cons
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: aggiorna(conn_b, sc.x_owner, sc.x, sc.my,
                                            posizione_=sc.pos, ruolo="partner", quota=35))
        try:
            conferma(conn_a, sc.y_owner, sc.y, sc.my)
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            assert conn_a.info.backend_pid in bloccanti(monitor, conn_b.info.backend_pid)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert "errore" not in eb, eb
        assert eb["out"]["stato_precedente"] == "confermato"
        m = riga_membro(db, sc.my)
        assert (m["stato"], m["quota_percentuale"]) == ("proposto", Decimal("35.00"))

    def test_la_conferma_attende_la_modifica_e_non_conferma_termini_nuovi(self, db, cons):
        """Il creatore porta Y dal 30% al 20% (transazione aperta: call e riga
        bloccate); Y conferma dalla pagina col 30%: attende la modifica e poi
        risponde membro_modificato, senza confermare il 20%."""
        sc = cons
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: conferma(conn_b, sc.y_owner, sc.y, sc.my, ruolo="partner",
                                            posizione_=sc.pos, quota=Decimal("30")))
        try:
            aggiorna(conn_a, sc.x_owner, sc.x, sc.my, quota=20)  # non committata
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            assert conn_a.info.backend_pid in bloccanti(monitor, conn_b.info.backend_pid)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "membro_modificato"
        m = riga_membro(db, sc.my)
        assert (m["stato"], m["quota_percentuale"]) == ("proposto", Decimal("20.00"))

    def test_la_conferma_attende_la_chiusura_della_call(self, db, cons):
        """Il membro prende la call FOR SHARE prima della sua riga: con una chiusura
        in corso attende e poi trova la call annullata."""
        sc = cons
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: conferma(conn_b, sc.y_owner, sc.y, sc.my))
        try:
            chiudi_call(conn_a, sc.x_owner, sc.x, sc.call, "annullata")  # non committata
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            assert conn_a.info.backend_pid in bloccanti(monitor, conn_b.info.backend_pid)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "call_non_modificabile"
        assert riga_membro(db, sc.my)["stato"] == "proposto"

    def test_esclusivita_accettazione_contro_pubblicazione(self, db, sc):
        """X accetta Y su una call esclusiva mentre Y pubblica la propria sullo stesso
        bando: il lock advisory le serializza e la pubblicazione vede la riga di Y."""
        bando = next(_bandi)
        a, pa = call_di(db, sc.x, bando_id=bando, esclusivita=True)
        k = candida(db, sc.y_owner, sc.y, a, pa)
        d = bozza_completa(db, sc.y, bando_id=bando)
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: pubblica_call(conn_b, sc.y_owner, sc.y, d))
        try:
            decidi(conn_a, sc.x_owner, sc.x, k)
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "esclusivita_violata"
        assert sc.y in per_azienda(db, a) and per_azienda(db, d) == {}


# ------------------------------------------- non regressione 0037 / 0039


class TestNonRegressione:
    def test_decidi_errori_invariati(self, db, sc):
        k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        decidi(db, sc.x_owner, sc.x, k, "rifiuta")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "candidatura_gia_decisa"
        z_owner, z = azienda(db)
        k2 = candida(db, z_owner, z, sc.call, sc.pos)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, z_owner, z, k2)
        assert detail_of(exc) == "candidatura_non_trovata"
        assert conta(db, "partner_call_membri") == 0

    def test_decidi_accettazione_invariata(self, db, sc):
        k = candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        out = decidi(db, sc.x_owner, sc.x, k)
        assert set(out) == {"candidatura", "conversazione_id", "membro_id"}
        assert len(audit(db, "partenariato.candidatura_accettata")) == 1
        assert audit(db, "partenariato.identita_rivelata") == []
        assert conta(db, "partner_conversazioni") == 1

    def test_pubblica_invariata(self, db):
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        c = bozza_completa(db, company, bando_id=next(_bandi))
        out = pubblica_call(db, owner, company, c)
        assert (out["stato"], out["versione"]) == ("pubblicata", 1)
        assert len(audit(db, "partenariato.call_pubblicata")) == 1
        # Lo snapshot della versione non contiene il consorzio.
        snap = db.execute("select snapshot from public.partner_call_versioni where call_id = %s",
                          (c,)).fetchone()[0]
        assert set(snap) == {"call", "requisiti", "posizioni"}
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica_call(db, owner, company, c)
        assert detail_of(exc) == "stato_call_non_valido"
        assert conta(db, "partner_call_membri") == 1

    def test_esclusivita_0039_invariata(self, db, sc):
        """Creatrice di un'altra call pubblicata: impegnata come nella 0039."""
        bando = next(_bandi)
        a, pa = call_di(db, sc.x, bando_id=bando, esclusivita=True)
        inserisci_call(db, sc.y, bando_id=bando)
        k = candida(db, sc.y_owner, sc.y, a, pa)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "esclusivita_violata"


# ---------------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0040, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0040, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")
ESEGUIBILE = "\n".join(r for r in SQL_0040.splitlines() if not r.lstrip().startswith("--"))


class TestSicurezza0040:
    def test_inventario_del_file(self):
        # Se la migration crea altro, i test sotto devono coprirlo.
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == set(FIRME)
        assert not re.search(r"^create (function|table(?! public\.))", ESEGUIBILE, re.M)

    def test_additiva(self):
        """Nessun oggetto eliminato; le sole tabelle esistenti toccate sono
        partner_calls (colonne e vincoli nuovi); le sole funzioni esistenti
        ridefinite sono le cinque previste."""
        assert not re.findall(r"^\s*drop\b.*$", ESEGUIBILE, re.M | re.I)
        assert not re.search(r"^\s*(alter function|create or replace view)\b",
                             ESEGUIBILE, re.M | re.I)
        alterate = set(re.findall(r"^alter table public\.(\w+)", ESEGUIBILE, re.M))
        assert alterate == TABELLE_NUOVE | {"partner_calls"}
        blocco = re.search(r"^alter table public\.partner_calls\n(.*?);", ESEGUIBILE,
                           re.M | re.S).group(1)
        assert [c.split()[:2] for c in re.split(r",\n", blocco.strip())] == [
            ["add", "column"]] * 3 + [["add", "constraint"]] * 3
        assert FUNZIONI_NEL_FILE - FUNZIONI_NUOVE == RIDEFINITE
        assert set(re.findall(r"^create trigger \w+\n\s+(?:before|after) [\w ]+? on "
                              r"public\.(\w+)", ESEGUIBILE, re.M)) == TABELLE_NUOVE

    def test_ridefinite_con_la_stessa_firma(self, db):
        """Una sola funzione per nome, con la firma della 0039."""
        for nome in RIDEFINITE:
            firme = [r[0] for r in db.execute(
                "select p.oid::regprocedure::text from pg_proc p join pg_namespace n "
                "on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = %s",
                (nome,)).fetchall()]
            assert firme == [FIRME[nome]]
            assert re.search(rf"^create or replace function public\.{nome}\(", SQL_0040, re.M)
        # p_richiedi_non_sandbox di fn_partner_decidi conserva il default true.
        assert db.execute(
            "select pg_get_function_arguments('public.fn_partner_decidi'::regproc)"
        ).fetchone()[0].endswith("p_richiedi_non_sandbox boolean DEFAULT true")

    def test_trigger(self, db):
        trigger = {(r[0], r[1]) for r in db.execute(
            "select tgname, tgrelid::regclass::text from pg_trigger where not tgisinternal "
            "and tgrelid = any (%s::regclass[])",
            ([f"public.{t}" for t in sorted(TABELLE_NUOVE | {"partner_calls"})],),
        ).fetchall()}
        assert trigger == {
            ("trg_partner_call_membri_updated_at", "partner_call_membri"),
            ("trg_partner_call_documenti_updated_at", "partner_call_documenti"),
            ("trg_partner_calls_updated_at", "partner_calls"),  # della 0037, invariato
            ("trg_partner_calls_chiudi_candidature", "partner_calls"),  # della 0039
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

    @pytest.mark.parametrize("tabella", sorted(TABELLE_NUOVE | {"partner_calls"}))
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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0040, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0040, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration (nuove e ridefinite) e ogni
        fn_partner_% / fn_partenariat% presente nel DB è SECURITY DEFINER con
        search_path fissato, non eseguibile dai client (PUBLIC compreso) ed esiste
        in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public'
                  and (p.proname like 'fn\_partner\_%%' or p.proname like 'fn\_partenariat%%')"""
        ).fetchall()}
        assert FUNZIONI_NUOVE <= dal_db
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
            # ACL esplicita senza la voce di PUBLIC («=X/…»).
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
                SQL_0040, re.M,
            ), nome
            assert firma.startswith(nome)

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_partner_membro_aggiorna(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), null, 'partner', 10)",
        "select public.fn_partner_membro_conferma(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), 'partner', null, 10)",
        "select public.fn_partner_membro_esci(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid())",
        "select public.fn_partner_membro_esterno(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), '{}'::jsonb)",
        "select public.fn_partner_documento_stato(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), 'nda', 'fatto', null)",
        "select public.fn_partner_call_validazione_salva(gen_random_uuid(), 'verde', 1)",
        "select public.fn_partner_backfill_membri()",
        "select public.fn_partner_membro_creatore(gen_random_uuid(), null)",
        "select public.fn_partner_membro_inserisci(gen_random_uuid(), gen_random_uuid(), "
        "null, null, 'partner', null, null, false)",
        "select public.fn_partner_esclusivita_violata(gen_random_uuid(), gen_random_uuid(), 1, "
        "true)",
        "select public.fn_partner_call_aperta('pubblicata', current_date, null, "
        "gen_random_uuid())",
        "select public.fn_partner_decidi(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), 'accetta', null, false)",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, ruolo, chiamata):
        """Prova diretta: con i ruoli esposti la chiamata fallisce."""
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_esegue_il_flusso(self, db, cons):
        """Come il service_role: un grant esplicito sulle RPC basta (la revoca è ai
        soli ruoli esposti) e le funzioni scrivono senza privilegi di tabella."""
        sc = cons
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        rpc = ("fn_partner_membro_aggiorna", "fn_partner_membro_conferma",
               "fn_partner_membro_esci", "fn_partner_membro_esterno",
               "fn_partner_documento_stato", "fn_partner_call_validazione_salva")
        # Valori letti prima di cambiare ruolo (il ruolo non legge le tabelle).
        pos, quota = sc.pos, Decimal("30.00")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            for nome in rpc:
                db.execute(f"grant execute on function public.{FIRME[nome]} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                e = esterno(db, sc.x_owner, sc.x, sc.call)["membro"]["id"]
                conferma(db, sc.x_owner, sc.x, e, ruolo=ESTERNO["ruolo"], posizione_=None,
                         quota=ESTERNO["quota"])
                conferma(db, sc.y_owner, sc.y, sc.my, ruolo="partner", posizione_=pos,
                         quota=quota)
                aggiorna(db, sc.x_owner, sc.x, sc.my, posizione_=pos, ruolo="partner",
                         quota=quota + 1)
                documento(db, sc.x_owner, sc.x, sc.call)
                assert salva_validazione(db, sc.call, "grigio", Decimal("0.5")) is True
                esci(db, sc.x_owner, sc.x, e)
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        assert riga_membro(db, sc.my)["stato"] == "proposto"
        assert conta(db, "partner_call_documenti") == 1

    def test_nessun_identificativo_in_chiaro_nelle_colonne(self, db):
        """Le tabelle nuove non hanno colonne per P.IVA, CF o email (T8). L'unico
        nome è quello dichiarato dell'ente esterno (Q20)."""
        colonne = {r[0] for r in db.execute(
            "select column_name::text from information_schema.columns "
            "where table_schema = 'public' and table_name::text = any (%s)",
            (sorted(TABELLE_NUOVE),)).fetchall()}
        assert len(colonne) > 15
        for vietata in ("partita_iva", "piva", "codice_fiscale", "cf", "email", "nome",
                        "ragione_sociale", "denominazione", "codice_pubblico", "punteggio",
                        "fatturato"):
            assert vietata not in colonne, vietata
        assert {c for c in colonne if "denominazione" in c} == {"esterno_denominazione"}
