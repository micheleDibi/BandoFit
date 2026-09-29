"""Test funzionali della migration 0039 (candidature, inviti e chat, WP7).

Coprono: vincoli e indici di partner_candidature (un solo attivo per call ×
azienda, messaggio, scadenza solo per l'invito, coerenza degli stati),
conversazioni, messaggi (immutabili, idempotenti, oscuramento) e letture; la
candidatura spontanea (piano e pool mensile Europe/Rome, opt-in, identità,
call aperta, gruppo, solo su invito, posizione e requisiti, un solo attivo);
l'invito (controparte neutra, accetta_inviti, TTL, tetto per call); la
decisione (lato giusto, stato, invito scaduto, accettazione con conversazione e
audit nella stessa transazione, p_rivela false/true, esclusività, identità e
controparte); ritiro e scadenze; trigger di chiusura della call e di revoca
dell'opt-in (utente e sistema); posizioni con candidature (ridefinizione di
fn_partner_call_sostituisci_posizioni: aggiorna per id); chat (invio, letture
per utente, claim delle email «una per raffica» con utenti estranei ignorati,
riepilogo, chiusura); fn_partenariati_snapshot con le candidature usate;
concorrenza e ordine dei lock con più connessioni (chiusura della call contro
decisione senza deadlock, pool dell'owner, esclusività, revoca, posizioni);
cascade; RLS, privilegi e firme di tutto ciò che la migration crea o
ridefinisce (test generico sulle funzioni).
Ogni test riceve un database fresco clonato dal template.
"""

import base64
import itertools
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0039_partenariato_candidature_chat.sql"
)
SQL_0039 = MIGRATION.read_text(encoding="utf-8")

TABELLE_NUOVE = {
    "partner_candidature", "partner_conversazioni", "partner_messaggi",
    "partner_conversazione_letture",
}
FIRME = {
    "fn_partner_utente_di_azienda": "fn_partner_utente_di_azienda(uuid,uuid)",
    "fn_partner_cand_blocca_azienda": "fn_partner_cand_blocca_azienda(uuid,uuid)",
    "fn_partner_candidature_usate": "fn_partner_candidature_usate(uuid)",
    "fn_partner_candidature_limite": "fn_partner_candidature_limite(uuid)",
    "fn_partner_call_aperta": "fn_partner_call_aperta(text,date,date,uuid)",
    "fn_partner_esclusivita_violata":
        "fn_partner_esclusivita_violata(uuid,uuid,integer,boolean)",
    "fn_partner_messaggi_immutabili": "fn_partner_messaggi_immutabili()",
    "fn_partner_calls_chiudi_candidature": "fn_partner_calls_chiudi_candidature()",
    "fn_partner_consents_chiudi_pendenti": "fn_partner_consents_chiudi_pendenti()",
    "fn_partner_scadi_per_opt_out": "fn_partner_scadi_per_opt_out(uuid)",
    "fn_partner_invia_candidatura": "fn_partner_invia_candidatura(jsonb)",
    "fn_partner_invita": "fn_partner_invita(jsonb)",
    "fn_partner_decidi":
        "fn_partner_decidi(uuid,uuid,uuid,uuid,text,text,boolean,boolean)",
    "fn_partner_ritira": "fn_partner_ritira(uuid,uuid,uuid,uuid)",
    "fn_partner_scadi_inviti": "fn_partner_scadi_inviti(integer)",
    "fn_partner_invia_messaggio": "fn_partner_invia_messaggio(uuid,uuid,uuid,uuid,text,uuid)",
    "fn_partner_segna_letto": "fn_partner_segna_letto(uuid,uuid,uuid,bigint)",
    "fn_partner_claim_email_chat": "fn_partner_claim_email_chat(uuid,uuid,uuid[],bigint)",
    "fn_partner_conversazioni_riepilogo": "fn_partner_conversazioni_riepilogo(uuid,uuid)",
    "fn_partner_chiudi_conversazione": "fn_partner_chiudi_conversazione(uuid,uuid,uuid,uuid)",
    # Ridefinite con la STESSA firma della 0037.
    "fn_partner_call_sostituisci_posizioni":
        "fn_partner_call_sostituisci_posizioni(uuid,uuid,uuid,uuid,jsonb)",
    "fn_partenariati_snapshot": "fn_partenariati_snapshot(uuid)",
    "fn_partner_call_pubblica":
        "fn_partner_call_pubblica(uuid,uuid,uuid,uuid,text,date,date,boolean)",
}
RIDEFINITE = {"fn_partner_call_sostituisci_posizioni", "fn_partenariati_snapshot",
              "fn_partner_call_pubblica"}
FUNZIONI_NUOVE = set(FIRME) - RIDEFINITE

VERSIONE = "2026-10-bozza-1"
MSG = ("Siamo un organismo di ricerca con esperienza nella prototipazione rapida "
       "di componenti meccanici.")
_TITOLARE = object()  # sentinella: l'attore è il titolare
_ASSENTE = object()   # sentinella: parametro non passato

_seq = itertools.count(1)
_bandi = itertools.count(9000)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def vincolo_di(exc) -> str:
    return exc.value.diag.constraint_name or ""


def oggi(db) -> date:
    return db.execute("select (now() at time zone 'Europe/Rome')::date").fetchone()[0]


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


def limite_candidature(db, slug: str, valore: int | None) -> None:
    db.execute("update public.subscription_plans set partner_candidature_mese = %s "
               "where slug = %s", (valore, slug))


def make_company(db, owner: str) -> str:
    i = next(_seq)
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, %s, %s) returning id",
        (owner, f"ACME {i} Srl", f"{i + 70000000:011d}"),
    ).fetchone()[0])


def importa(db, company: str, *, sandbox: bool = False) -> None:
    """company_data come dopo un import IT-full (identità verificata, T5)."""
    db.execute(
        "insert into public.company_data (company_profile_id, piva_fetched, sandbox, raw, "
        "denominazione, stato_impresa) "
        "select id, partita_iva, %s, '{}'::jsonb, ragione_sociale, 'Attiva' "
        "from public.company_profiles where id = %s",
        (sandbox, company),
    )


def consenso(db, owner: str, company: str, azione: str = "concedi", *,
             non_sandbox: bool = True) -> dict:
    return db.execute(
        "select public.fn_partner_consenso(%s::uuid, %s::uuid, %s::uuid, %s::text, %s::text, "
        "'pagina_azienda', true, %s)",
        (owner, company, owner, azione, VERSIONE, non_sandbox),
    ).fetchone()[0]


def azienda(db, owner: str | None = None, plan_slug: str | None = "smart", *,
            opt_in: bool = True, sandbox: bool = False) -> tuple[str, str]:
    """Titolare + azienda con identità verificata; con opt_in il profilo partner è visibile."""
    owner = owner or new_user(db, plan_slug)
    company = make_company(db, owner)
    importa(db, company, sandbox=sandbox)
    if opt_in:
        consenso(db, owner, company, non_sandbox=not sandbox)
    return owner, company


def membro(db, owner, company, *, attivo: bool = True) -> str:
    """Membro della famiglia dell'owner con appartenenza (e visibilità) su company."""
    uid = new_user(db)
    mid = db.execute(
        "select public.fn_create_family_member(%s, %s, 'Sede', %s, 'existing_user', %s, null)",
        (owner, uid, f"{uid[:8]}@test.it", company),
    ).fetchone()[0]
    if attivo:
        db.execute("select public.fn_accept_invitation(%s, %s)", (mid, uid))
    return uid


def con_guc(db, sql: str, args: tuple) -> None:
    """Scrittura con la GUC dei campi protetti (come dentro una RPC della 0035)."""
    with db.transaction():
        db.execute("select set_config('app.partner_consenso', 'on', true)")
        db.execute(sql, args)


def sospendi(db, company: str) -> None:
    con_guc(db, "update public.company_partner_profiles set sospeso_at = now(), "
                "sospeso_motivo = 'moderazione', sospeso_da = gen_random_uuid() "
                "where company_profile_id = %s", (company,))


def inserisci_call(db, company: str, *, stato: str = "pubblicata",
                   visibilita: str = "pubblica", bando_id: int | None = None,
                   esclusivita: bool = False, scadenza_giorni: int = 30,
                   motivo: str | None = None) -> str:
    """Call scritta direttamente (come dopo le RPC del WP5) nello stato richiesto."""
    owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                           (company,)).fetchone()[0])
    pubblicata = stato in ("pubblicata", "chiusa_completata", "scaduta", "sospesa_moderazione")
    motivo = motivo or {"chiusa_completata": "creatore_completata",
                        "chiusa_annullata": "creatore_annullata",
                        "scaduta": "scadenza_call"}.get(stato)
    bid = bando_id if bando_id is not None else next(_bandi)
    return str(db.execute(
        """insert into public.partner_calls
             (company_profile_id, family_parent_id, creato_da, bando_id, bando_slug,
              bando_titolo, ruolo_creatore, titolo, descrizione_pubblica, scadenza_call,
              regole_partenariato, regole_confermate_at, visibilita, stato, pubblicata_at,
              chiusa_at, motivo_chiusura, sospesa_at, stato_prima_sospensione, esclusivita)
           values (%s, %s, %s, %s, %s, 'Bando di prova', 'capofila',
                   'Cerchiamo un organismo di ricerca', 'Progetto di ricerca industriale.',
                   current_date + %s, '{}'::jsonb, now(), %s, %s,
                   case when %s then now() end, case when %s::text is not null then now() end,
                   %s, case when %s then now() end, case when %s then 'pubblicata' end, %s)
           returning id""",
        (company, owner, owner, bid, f"bando-{bid}", scadenza_giorni, visibilita, stato,
         pubblicata, motivo, motivo, stato == "sospesa_moderazione",
         stato == "sospesa_moderazione", esclusivita),
    ).fetchone()[0])


def posizione(db, call_id: str, titolo: str = "Organismo di ricerca") -> str:
    ordine = db.execute("select count(*) from public.partner_call_posizioni where call_id = %s",
                        (call_id,)).fetchone()[0]
    return str(db.execute(
        "insert into public.partner_call_posizioni (call_id, titolo, ordine) "
        "values (%s, %s, %s) returning id", (call_id, titolo, ordine)).fetchone()[0])


def requisito(db, call_id: str, etichetta: str = "A") -> str:
    return str(db.execute(
        "insert into public.partner_call_requisiti (call_id, origine, etichetta, testo, cercato) "
        "values (%s, 'manuale', %s, 'Requisito di prova', true) returning id",
        (call_id, etichetta)).fetchone()[0])


def call_con_posizione(db, company: str, **kw) -> tuple[str, str]:
    c = inserisci_call(db, company, **kw)
    return c, posizione(db, c)


def candida(db, owner, company, call_id, pos, *, messaggio=MSG, requisiti=None,
            attore=_TITOLARE, non_sandbox=_ASSENTE, extra=None, togli=()) -> dict:
    payload = {
        "owner_id": owner, "company_id": company,
        "attore_id": owner if attore is _TITOLARE else attore,
        "call_id": call_id, "posizione_id": pos, "messaggio": messaggio,
        "requisiti_dichiarati": requisiti if requisiti is not None else [],
        "valutazione": {"requisiti_coperti": ["A"], "fasce": {"fatturato": "1m_2m"}},
        "pseudonimo": pseudo(),
    }
    if non_sandbox is not _ASSENTE:
        payload["richiedi_non_sandbox"] = non_sandbox
    payload.update(extra or {})
    for chiave in togli:
        payload.pop(chiave)
    return db.execute("select public.fn_partner_invia_candidatura(%s::jsonb)",
                      (Jsonb(payload),)).fetchone()[0]


def invita(db, owner, company, call_id, invitato, *, max_inviti=30, ttl=14, pos=None,
           messaggio=None, attore=_TITOLARE, extra=None) -> dict:
    payload = {
        "owner_id": owner, "company_id": company,
        "attore_id": owner if attore is _TITOLARE else attore,
        "call_id": call_id, "invitato_company_id": invitato, "posizione_id": pos,
        "messaggio": messaggio, "valutazione": {"requisiti_coperti": ["A"]},
        "pseudonimo": pseudo(), "max_inviti": max_inviti, "ttl_giorni": ttl,
    }
    payload.update(extra or {})
    return db.execute("select public.fn_partner_invita(%s::jsonb)",
                      (Jsonb(payload),)).fetchone()[0]


def decidi(db, owner, company, cand_id, decisione="accetta", *, motivo=None, rivela=False,
           attore=_TITOLARE, non_sandbox=_ASSENTE) -> dict:
    """Chiamata per nome come PostgREST; senza non_sandbox vale il default (true)."""
    args = [cand_id, owner if attore is _TITOLARE else attore, owner, company, decisione,
            motivo, rivela]
    extra = ""
    if non_sandbox is not _ASSENTE:
        extra = ", p_richiedi_non_sandbox => %s::boolean"
        args.append(non_sandbox)
    return db.execute(
        "select public.fn_partner_decidi(p_candidatura => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid, p_decisione => %s::text, "
        f"p_motivo => %s::text, p_rivela => %s::boolean{extra})",
        args,
    ).fetchone()[0]


def ritira(db, owner, company, cand_id, *, attore=_TITOLARE) -> dict:
    return db.execute(
        "select public.fn_partner_ritira(p_candidatura => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid)",
        (cand_id, owner if attore is _TITOLARE else attore, owner, company),
    ).fetchone()[0]


def scadi_inviti(db, limite=_ASSENTE) -> int:
    if limite is _ASSENTE:
        return db.execute("select public.fn_partner_scadi_inviti()").fetchone()[0]
    return db.execute("select public.fn_partner_scadi_inviti(p_limite => %s::integer)",
                      (limite,)).fetchone()[0]


def scrivi(db, owner, company, conv, testo="Buongiorno, ecco i nostri riferimenti.", *,
           client=None, attore=_TITOLARE) -> dict:
    return db.execute(
        "select public.fn_partner_invia_messaggio(p_conversazione => %s::uuid, "
        "p_attore => %s::uuid, p_owner => %s::uuid, p_company => %s::uuid, "
        "p_testo => %s::text, p_client_msg_id => %s::uuid)",
        (conv, owner if attore is _TITOLARE else attore, owner, company, testo,
         client or str(uuid.uuid4())),
    ).fetchone()[0]


def segna_letto(db, conv, user, company, fino_a) -> int:
    return db.execute(
        "select public.fn_partner_segna_letto(p_conversazione => %s::uuid, p_user => %s::uuid, "
        "p_company => %s::uuid, p_fino_a => %s::bigint)",
        (conv, user, company, fino_a),
    ).fetchone()[0]


def claim(db, conv, company, users, ultimo) -> set[str]:
    return {str(r[0]) for r in db.execute(
        "select * from public.fn_partner_claim_email_chat(p_conversazione => %s::uuid, "
        "p_company => %s::uuid, p_user_ids => %s::uuid[], p_ultimo_id => %s::bigint)",
        (conv, company, users, ultimo),
    ).fetchall()}


def riepilogo(db, user, company) -> list[tuple]:
    return [(str(r[0]), r[1]) for r in db.execute(
        "select conversazione_id, non_letti from public.fn_partner_conversazioni_riepilogo("
        "p_user => %s::uuid, p_company => %s::uuid)", (user, company)).fetchall()]


def chiudi_conv(db, owner, company, conv, *, attore=_TITOLARE) -> dict:
    return db.execute(
        "select public.fn_partner_chiudi_conversazione(p_conversazione => %s::uuid, "
        "p_attore => %s::uuid, p_owner => %s::uuid, p_company => %s::uuid)",
        (conv, owner if attore is _TITOLARE else attore, owner, company),
    ).fetchone()[0]


def pubblica_call(db, owner, company, call_id) -> dict:
    """fn_partner_call_pubblica (0037, ridefinita dalla 0039) con bando aperto."""
    return db.execute(
        "select public.fn_partner_call_pubblica(%s::uuid, %s::uuid, %s::uuid, %s::uuid, "
        "'aperto', current_date + 90, current_date + 30, true)",
        (owner, company, owner, call_id),
    ).fetchone()[0]


def bozza_completa(db, company: str, *, bando_id: int, esclusivita: bool = False) -> str:
    """Bozza pronta per la pubblicazione: testi, regole confermate, una posizione e
    un requisito cercato."""
    c, _ = call_con_posizione(db, company, stato="bozza", bando_id=bando_id,
                              esclusivita=esclusivita)
    requisito(db, c)
    return c


def chiudi_call(db, owner, company, call_id, esito="completata") -> dict:
    return db.execute(
        "select public.fn_partner_call_chiudi(%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s::text)",
        (owner, company, owner, call_id, esito),
    ).fetchone()[0]


def sostituisci_posizioni(db, owner, company, call_id, lista) -> list:
    return db.execute(
        "select public.fn_partner_call_sostituisci_posizioni(%s::uuid, %s::uuid, %s::uuid, "
        "%s::uuid, %s::jsonb)",
        (owner, company, owner, call_id, Jsonb(lista)),
    ).fetchone()[0]


def snapshot(db, owner) -> dict:
    return db.execute("select public.fn_partenariati_snapshot(%s::uuid)", (owner,)).fetchone()[0]


def usate(db, owner) -> int:
    return db.execute("select public.fn_partner_candidature_usate(%s::uuid)",
                      (owner,)).fetchone()[0]


def cand(db, cand_id) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.partner_candidature where id = %s",
                           (cand_id,)).fetchone()


def conv(db, conv_id) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.partner_conversazioni where id = %s",
                           (conv_id,)).fetchone()


def lettura(db, conv_id, user) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.partner_conversazione_letture "
                           "where conversazione_id = %s and user_id = %s",
                           (conv_id, user)).fetchone()


def audit(db, action) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.audit_log where action = %s order by id",
                           (action,)).fetchall()


def conta(db, tabella: str) -> int:
    return db.execute(f"select count(*) from public.{tabella}").fetchone()[0]


def riga(db, sc, **colonne) -> str:
    """Candidatura scritta direttamente (vincoli della tabella)."""
    dati = {
        "partner_call_id": sc.call, "tipo": "candidatura", "company_profile_id": sc.y,
        "family_parent_id": sc.y_owner, "creatore_company_profile_id": sc.x,
        "posizione_id": sc.pos, "messaggio": MSG, "pseudonimo": pseudo(),
        "inviata_da_user_id": sc.y_owner,
    }
    dati.update(colonne)
    nomi = list(dati)
    return str(db.execute(
        f"insert into public.partner_candidature ({', '.join(nomi)}) "
        f"values ({', '.join(['%s'] * len(nomi))}) returning id",
        [Jsonb(v) if isinstance(v, dict) else v for v in dati.values()],
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
    """X (Smart) ha una call pubblicata con una posizione e un requisito; Y (Smart,
    altro owner) ha l'opt-in visibile."""
    x_owner, x = azienda(db, opt_in=False)
    y_owner, y = azienda(db)
    call_id, pos = call_con_posizione(db, x)
    return SimpleNamespace(x_owner=x_owner, x=x, y_owner=y_owner, y=y, call=call_id, pos=pos,
                           req=requisito(db, call_id, "A"))


def candidatura_pendente(db, sc) -> str:
    return candida(db, sc.y_owner, sc.y, sc.call, sc.pos)["candidatura"]["id"]


def accettata(db, sc, *, rivela=False) -> tuple[str, str]:
    """Candidatura di Y accettata da X → (candidatura, conversazione)."""
    k = candidatura_pendente(db, sc)
    out = decidi(db, sc.x_owner, sc.x, k, rivela=rivela)
    return k, out["conversazione_id"]


# ------------------------------------------------------------------ tabelle


class TestCandidatureTabella:
    def test_default(self, db, sc):
        k = cand(db, riga(db, sc))
        assert (k["stato"], k["valutazione"], k["requisiti_dichiarati"]) == ("inviata", {}, [])
        assert k["decisa_at"] is None and k["chiusa_at"] is None and k["scade_at"] is None
        assert k["created_at"] is not None and k["updated_at"] is not None

    def test_un_solo_attivo_per_call_e_azienda(self, db, sc):
        now = datetime.now(timezone.utc)
        for _ in range(2):
            riga(db, sc, stato="rifiutata", decisa_at=now, decisa_da_user_id=sc.x_owner)
        riga(db, sc, stato="ritirata", chiusa_at=now)
        riga(db, sc)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            riga(db, sc, tipo="invito", messaggio=None, scade_at=now + timedelta(days=1))
        assert vincolo_di(exc) == "partner_candidature_una_attiva"
        # accettata conta come attiva
        db.execute("update public.partner_candidature set stato = 'rifiutata', "
                   "decisa_at = now(), decisa_da_user_id = %s where stato = 'inviata'",
                   (sc.x_owner,))
        riga(db, sc, stato="accettata", decisa_at=now, decisa_da_user_id=sc.x_owner)
        with pytest.raises(psycopg.errors.UniqueViolation):
            riga(db, sc)
        # un'altra azienda sulla stessa call o la stessa su un'altra call: ammesse
        _, z = azienda(db)
        riga(db, sc, company_profile_id=z)
        altra, pos2 = call_con_posizione(db, sc.x)
        riga(db, sc, partner_call_id=altra, posizione_id=pos2)

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"messaggio": "x" * 49}, "pcand_messaggio_check"),
        ({"messaggio": "x" * 2001}, "pcand_messaggio_check"),
        ({"messaggio": None}, "pcand_messaggio_check"),
        ({"tipo": "invito", "messaggio": "x" * 1001, "scade_at_giorni": 1},
         "pcand_messaggio_check"),
        ({"scade_at_giorni": 3}, "pcand_scadenza_solo_invito"),
        ({"tipo": "invito", "messaggio": None}, "pcand_scadenza_solo_invito"),
        ({"tipo": "invito", "messaggio": None, "scade_at_giorni": 1,
          "requisiti_dichiarati": [str(uuid.uuid4())]}, "pcand_requisiti_solo_candidatura"),
        ({"stato": "accettata"}, "pcand_decisa_coerente"),
        ({"stato": "rifiutata", "decisa_ora": True}, "pcand_decisa_coerente"),
        ({"decisa_ora": True, "decisa_da_user_id": "UTENTE"}, "pcand_decisa_coerente"),
        ({"stato": "ritirata"}, "pcand_chiusa_coerente"),
        ({"chiusa_ora": True}, "pcand_chiusa_coerente"),
        ({"stato": "scaduta", "chiusa_ora": True}, "pcand_motivo_chiusura_coerente"),
        ({"motivo_chiusura": "ttl"}, "pcand_motivo_chiusura_coerente"),
        ({"motivo_chiusura": "boh", "stato": "ritirata", "chiusa_ora": True},
         "pcand_motivo_chiusura_check"),
        ({"motivo_rifiuto": "No grazie"}, "pcand_motivo_rifiuto_coerente"),
        ({"motivo_rifiuto": "x" * 501, "stato": "rifiutata", "decisa_ora": True,
          "decisa_da_user_id": "UTENTE"}, "pcand_motivo_rifiuto_check"),
        ({"stato": "sospesa"}, "pcand_stato_check"),
        ({"tipo": "proposta"}, "pcand_tipo_check"),
        ({"pseudonimo": "abcdefghijklmnop"}, "pcand_pseudonimo_check"),
        ({"pseudonimo": "ABCDEFGHIJKLMNO1"}, "pcand_pseudonimo_check"),
        ({"pseudonimo": "ABCDEFGHIJKLMNOPQ"}, "pcand_pseudonimo_check"),
        ({"valutazione": []}, "pcand_valutazione_check"),
        ({"requisiti_dichiarati": [None]}, "pcand_requisiti_dichiarati_check"),
        ({"requisiti_dichiarati": [str(uuid.uuid4()) for _ in range(41)]},
         "pcand_requisiti_dichiarati_check"),
    ])
    def test_vincoli(self, db, sc, colonne, vincolo):
        colonne = dict(colonne)
        if colonne.pop("scade_at_giorni", None):
            colonne["scade_at"] = datetime.now(timezone.utc) + timedelta(days=1)
        if colonne.pop("decisa_ora", None):
            colonne["decisa_at"] = datetime.now(timezone.utc)
        if colonne.pop("chiusa_ora", None):
            colonne["chiusa_at"] = datetime.now(timezone.utc)
        if colonne.get("decisa_da_user_id") == "UTENTE":
            colonne["decisa_da_user_id"] = sc.x_owner
        if isinstance(colonne.get("valutazione"), list):
            colonne["valutazione"] = Jsonb(colonne["valutazione"])
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            riga(db, sc, **colonne)
        assert vincolo_di(exc) == vincolo

    def test_non_a_se_stessa(self, db, sc):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            riga(db, sc, company_profile_id=sc.x)
        assert vincolo_di(exc) == "pcand_non_se_stessa"

    def test_bordi_ammessi(self, db, sc):
        riga(db, sc, messaggio="x" * 50)
        _, z = azienda(db)
        riga(db, sc, company_profile_id=z, messaggio="x" * 2000)
        _, w = azienda(db)
        riga(db, sc, company_profile_id=w, tipo="invito", messaggio="x" * 1000,
             scade_at=datetime.now(timezone.utc) + timedelta(days=1))

    def test_conversazione_solo_se_accettata(self, db, sc):
        k, conv_id = accettata(db, sc)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_candidature set stato = 'rifiutata' "
                       "where id = %s", (k,))
        assert vincolo_di(exc) == "pcand_conversazione_coerente"
        assert conv(db, conv_id)["candidatura_id"] == uuid.UUID(k)

    def test_indici(self, db):
        indici = dict(db.execute(
            "select indexname, indexdef from pg_indexes where schemaname = 'public' "
            "and tablename = 'partner_candidature'").fetchall())
        assert "WHERE (stato = ANY (ARRAY['inviata'::text, 'accettata'::text]))" in \
            indici["partner_candidature_una_attiva"]
        assert "UNIQUE" in indici["partner_candidature_una_attiva"]
        assert "WHERE (tipo = 'candidatura'::text)" in indici["partner_candidature_quota_idx"]
        assert "(family_parent_id, created_at)" in indici["partner_candidature_quota_idx"]
        assert "WHERE ((tipo = 'invito'::text) AND (stato = 'inviata'::text))" in \
            indici["partner_candidature_scadenza_idx"]
        for nome in ("partner_candidature_azienda_idx", "partner_candidature_creatore_idx",
                     "partner_candidature_call_idx", "partner_candidature_posizione_idx",
                     "partner_candidature_conversazione_idx"):
            assert nome in indici


class TestConversazioniTabella:
    def test_vincoli(self, db, sc):
        k, conv_id = accettata(db, sc)
        c = conv(db, conv_id)
        assert (c["stato"], c["chiusa_at"], c["ultimo_messaggio_id"]) == ("aperta", None, None)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_conversazioni set stato = 'chiusa' where id = %s",
                       (conv_id,))
        assert vincolo_di(exc) == "pconv_chiusa_coerente"
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_conversazioni set ultimo_messaggio_id = 1 "
                       "where id = %s", (conv_id,))
        assert vincolo_di(exc) == "pconv_ultimo_coerente"
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_conversazioni set company_partner_id = "
                       "company_creatore_id where id = %s", (conv_id,))
        assert vincolo_di(exc) == "pconv_aziende_diverse"
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            db.execute("insert into public.partner_conversazioni (partner_call_id, "
                       "candidatura_id, company_creatore_id, company_partner_id) "
                       "values (%s, %s, %s, %s)", (sc.call, k, sc.x, sc.y))
        assert vincolo_di(exc) == "pconv_candidatura_key"


class TestMessaggiTabella:
    def test_testo_e_idempotenza_nella_tabella(self, db, sc):
        _, conv_id = accettata(db, sc)
        client = str(uuid.uuid4())

        def ins(testo, client_id=None):
            db.execute("insert into public.partner_messaggi (conversazione_id, "
                       "mittente_company_profile_id, mittente_user_id, testo, client_msg_id) "
                       "values (%s, %s, %s, %s, %s)",
                       (conv_id, sc.x, sc.x_owner, testo, client_id or str(uuid.uuid4())))

        for testo in ("", " \n\t ", "x" * 5001):
            with pytest.raises(psycopg.errors.CheckViolation) as exc:
                ins(testo)
            assert vincolo_di(exc) == "pmsg_testo_check"
        ins("x" * 5000, client)
        ins("a")
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            ins("altro", client)
        assert vincolo_di(exc) == "pmsg_client_msg_key"

    def test_immutabili_tranne_l_oscuramento(self, db, sc):
        _, conv_id = accettata(db, sc)
        mid = scrivi(db, sc.x_owner, sc.x, conv_id)["messaggio"]["id"]
        for colonna, valore in [("testo", "'modificato'"), ("mittente_user_id", "gen_random_uuid()"),
                                ("mittente_company_profile_id", "gen_random_uuid()"),
                                ("client_msg_id", "gen_random_uuid()"),
                                ("created_at", "now() - interval '1 day'")]:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                db.execute(f"update public.partner_messaggi set {colonna} = {valore} "
                           "where id = %s", (mid,))
            assert detail_of(exc) == "messaggio_immutabile", colonna
        _, altra = accettata_altra(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("update public.partner_messaggi set conversazione_id = %s where id = %s",
                       (altra, mid))
        assert detail_of(exc) == "messaggio_immutabile"
        # Oscuramento di moderazione (e ripristino): ammesso.
        db.execute("update public.partner_messaggi set nascosto_moderazione_at = now(), "
                   "nascosto_da = gen_random_uuid() where id = %s", (mid,))
        db.execute("update public.partner_messaggi set nascosto_moderazione_at = null, "
                   "nascosto_da = null where id = %s", (mid,))
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_messaggi set nascosto_moderazione_at = now() "
                       "where id = %s", (mid,))
        assert vincolo_di(exc) == "pmsg_nascosto_coerente"

    def test_delete_non_bloccata(self, db, sc):
        """Niente trigger su DELETE: la cascade (Q21) passa."""
        _, conv_id = accettata(db, sc)
        mid = scrivi(db, sc.x_owner, sc.x, conv_id)["messaggio"]["id"]
        db.execute("delete from public.partner_messaggi where id = %s", (mid,))
        assert conta(db, "partner_messaggi") == 0


def accettata_altra(db, sc) -> tuple[str, str]:
    """Una seconda conversazione tra X e un'altra azienda Z sulla call di X."""
    z_owner, z = azienda(db)
    k = candida(db, z_owner, z, sc.call, sc.pos)["candidatura"]["id"]
    return k, decidi(db, sc.x_owner, sc.x, k)["conversazione_id"]


class TestLettureTabella:
    def test_vincoli_e_chiave(self, db, sc):
        _, conv_id = accettata(db, sc)
        db.execute("insert into public.partner_conversazione_letture (conversazione_id, user_id, "
                   "company_profile_id) values (%s, %s, %s)", (conv_id, sc.y_owner, sc.y))
        r = lettura(db, conv_id, sc.y_owner)
        assert (r["letto_fino_a_id"], r["email_fino_a_id"]) == (0, None)
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("insert into public.partner_conversazione_letture (conversazione_id, "
                       "user_id, company_profile_id) values (%s, %s, %s)",
                       (conv_id, sc.y_owner, sc.y))
        for colonna, vincolo in [("letto_fino_a_id", "pcl_letto_check"),
                                 ("email_fino_a_id", "pcl_email_check")]:
            with pytest.raises(psycopg.errors.CheckViolation) as exc:
                db.execute(f"update public.partner_conversazione_letture set {colonna} = -1")
            assert vincolo_di(exc) == vincolo
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute("insert into public.partner_conversazione_letture (conversazione_id, "
                       "user_id, company_profile_id) values (%s, %s, %s)",
                       (conv_id, str(uuid.uuid4()), sc.y))


class TestSegnalazioniMessaggio:
    def _segnala(self, db, tipo):
        db.execute(
            "insert into public.partner_segnalazioni (oggetto_tipo, oggetto_id, "
            "segnalante_user_id, motivo, descrizione, buona_fede, contenuto_snapshot) "
            "values (%s, '42', gen_random_uuid(), 'altro', 'Messaggio offensivo in chat', true, "
            "'{}'::jsonb)", (tipo,))

    @pytest.mark.parametrize("tipo", ["call", "profilo", "messaggio"])
    def test_tipi_ammessi(self, db, tipo):
        self._segnala(db, tipo)

    def test_altri_tipi_rifiutati(self, db):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._segnala(db, "conversazione")
        assert vincolo_di(exc) == "ps_oggetto_tipo_check"


# -------------------------------------------------------- candidatura spontanea


class TestInviaCandidatura:
    def test_candidatura_ok(self, db, sc):
        out = candida(db, sc.y_owner, sc.y, sc.call, sc.pos, requisiti=[sc.req],
                      messaggio=f"  {MSG}  ")
        k = cand(db, out["candidatura"]["id"])
        assert (k["tipo"], k["stato"], str(k["company_profile_id"]),
                str(k["creatore_company_profile_id"]), str(k["family_parent_id"]),
                str(k["inviata_da_user_id"]), str(k["posizione_id"])) == (
            "candidatura", "inviata", sc.y, sc.x, sc.y_owner, sc.y_owner, sc.pos)
        assert k["messaggio"] == MSG and k["requisiti_dichiarati"] == [uuid.UUID(sc.req)]
        assert k["valutazione"]["fasce"] == {"fatturato": "1m_2m"}
        assert k["scade_at"] is None
        assert (out["usate"], out["limite"]) == (1, 5)
        (a,) = audit(db, "partenariato.candidatura_inviata")
        assert str(a["actor_id"]) == sc.y_owner and str(a["family_parent_id"]) == sc.y_owner
        assert str(a["target_user_id"]) == sc.x_owner
        assert a["payload"]["candidatura_id"] == str(k["id"])

    def test_gratuito_funzione_non_inclusa(self, db, sc):
        owner, y = azienda(db, plan_slug=None)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, owner, y, sc.call, sc.pos)
        assert detail_of(exc) == "funzione_non_inclusa"

    def test_gratuito_senza_opt_in_prima_il_piano(self, db, sc):
        """Il Gratuito non si candida comunque: il piano si controlla prima dell'opt-in."""
        owner, y = azienda(db, plan_slug=None, opt_in=False)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, owner, y, sc.call, sc.pos)
        assert detail_of(exc) == "funzione_non_inclusa"

    def test_senza_abbonamento_attivo(self, db, sc):
        db.execute("update public.user_subscriptions set status = 'cancelled' where user_id = %s",
                   (sc.y_owner,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "funzione_non_inclusa"

    def test_limite_mensile(self, db, sc):
        limite_candidature(db, "smart", 2)
        for _ in range(2):
            c, p = call_con_posizione(db, sc.x)
            candida(db, sc.y_owner, sc.y, c, p)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "candidature_esaurite"

    def test_ritirate_e_rifiutate_contano(self, db, sc):
        limite_candidature(db, "smart", 1)
        k = candidatura_pendente(db, sc)
        ritira(db, sc.y_owner, sc.y, k)
        c, p = call_con_posizione(db, sc.x)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, c, p)
        assert detail_of(exc) == "candidature_esaurite"

    def test_inviti_non_contano(self, db, sc):
        limite_candidature(db, "smart", 1)
        c, _ = call_con_posizione(db, sc.x)
        invita(db, sc.x_owner, sc.x, c, sc.y)
        assert usate(db, sc.y_owner) == 0
        candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert usate(db, sc.y_owner) == 1

    def test_illimitato(self, db, sc):
        limite_candidature(db, "smart", None)
        for _ in range(7):
            c, p = call_con_posizione(db, sc.x)
            out = candida(db, sc.y_owner, sc.y, c, p)
        assert (out["usate"], out["limite"]) == (7, None)

    @pytest.mark.parametrize("fuso", ["UTC", "America/New_York", "Europe/Rome"])
    def test_mese_successivo_si_riparte(self, db, sc, fuso):
        """Mese solare Europe/Rome qualunque sia il fuso della sessione (in
        produzione UTC)."""
        db.execute(f"set time zone '{fuso}'")
        limite_candidature(db, "smart", 1)
        k = candidatura_pendente(db, sc)
        ritira(db, sc.y_owner, sc.y, k)
        # Un istante prima dell'inizio del mese (Europe/Rome): mese scorso.
        db.execute("update public.partner_candidature set created_at = "
                   "(date_trunc('month', now() at time zone 'Europe/Rome') "
                   "at time zone 'Europe/Rome') - interval '1 microsecond' where id = %s", (k,))
        assert usate(db, sc.y_owner) == 0
        c, p = call_con_posizione(db, sc.x)
        candida(db, sc.y_owner, sc.y, c, p)
        # Esattamente l'inizio del mese: conta.
        db.execute("update public.partner_candidature set created_at = "
                   "date_trunc('month', now() at time zone 'Europe/Rome') "
                   "at time zone 'Europe/Rome' where id <> %s", (k,))
        assert usate(db, sc.y_owner) == 1

    def test_limiti_senza_la_chiave_fail_closed(self, db, sc):
        """Una risposta di fn_partenariati_limiti senza candidature_mese vale 0
        (esclusa), una con la chiave a null vale illimitato."""
        db.execute("create or replace function public.fn_partenariati_limiti(p_owner uuid) "
                   "returns jsonb language sql stable security definer "
                   "set search_path = public as $$ select '{}'::jsonb $$")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "funzione_non_inclusa"
        s = snapshot(db, sc.y_owner)["candidature_mese"]
        assert (s["limite"], s["residuo"]) == (0, 0)
        db.execute("create or replace function public.fn_partenariati_limiti(p_owner uuid) "
                   "returns jsonb language sql stable security definer set search_path = "
                   "public as $$ select '{\"candidature_mese\": null}'::jsonb $$")
        assert candida(db, sc.y_owner, sc.y, sc.call, sc.pos)["limite"] is None

    def test_pool_condiviso_tra_le_aziende_advisor(self, db, sc):
        limite_candidature(db, "advisor", 2)
        owner = new_user(db, "advisor")
        _, a = azienda(db, owner)
        _, b = azienda(db, owner)
        candida(db, owner, a, sc.call, sc.pos)
        c, p = call_con_posizione(db, sc.x)
        candida(db, owner, b, c, p)
        c3, p3 = call_con_posizione(db, sc.x)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, owner, a, c3, p3)
        assert detail_of(exc) == "candidature_esaurite"
        # Un altro owner non consuma il pool.
        assert usate(db, sc.y_owner) == 0

    @pytest.mark.parametrize("caso", ["senza_profilo", "revocato", "sospeso"])
    def test_profilo_partner_non_attivo(self, db, sc, caso):
        owner, y = azienda(db, opt_in=caso != "senza_profilo")
        if caso == "revocato":
            consenso(db, owner, y, "revoca")
        elif caso == "sospeso":
            sospendi(db, y)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, owner, y, sc.call, sc.pos)
        assert detail_of(exc) == "profilo_partner_non_attivo"

    def test_identita_non_verificata(self, db, sc):
        db.execute("update public.company_data set stato_impresa = 'Cessata' "
                   "where company_profile_id = %s", (sc.y,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "identita_non_verificata"

    @pytest.mark.parametrize(("non_sandbox", "ammessa"), [
        (_ASSENTE, False), (None, False), (True, False), (False, True)])
    def test_dati_sandbox(self, db, sc, non_sandbox, ammessa):
        owner, y = azienda(db, sandbox=True)
        if ammessa:
            candida(db, owner, y, sc.call, sc.pos, non_sandbox=non_sandbox)
            return
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, owner, y, sc.call, sc.pos, non_sandbox=non_sandbox)
        assert detail_of(exc) == "identita_non_verificata"

    @pytest.mark.parametrize("caso", ["bozza", "sospesa_moderazione", "chiusa_completata",
                                      "chiusa_annullata", "scaduta", "scadenza_passata",
                                      "bando_scaduto", "creatore_archiviato",
                                      "creatore_eliminato", "inesistente"])
    def test_call_non_attiva(self, db, sc, caso):
        if caso == "inesistente":
            c, p = str(uuid.uuid4()), sc.pos
        elif caso == "scadenza_passata":
            c, p = call_con_posizione(db, sc.x, scadenza_giorni=-1)
        elif caso in ("bando_scaduto", "creatore_archiviato", "creatore_eliminato"):
            c, p = sc.call, sc.pos
            if caso == "bando_scaduto":
                db.execute("update public.partner_calls set bando_scadenza = current_date - 2 "
                           "where id = %s", (c,))
            else:
                colonna = "archived_at" if caso == "creatore_archiviato" else "deleted_at"
                db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                           (sc.x,))
        else:
            c, p = call_con_posizione(db, sc.x, stato=caso)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, c, p)
        assert detail_of(exc) == "call_non_attiva"

    def test_scadenza_oggi_ammessa(self, db, sc):
        c, p = call_con_posizione(db, sc.x, scadenza_giorni=0)
        db.execute("update public.partner_calls set scadenza_call = "
                   "(now() at time zone 'Europe/Rome')::date where id = %s", (c,))
        candida(db, sc.y_owner, sc.y, c, p)

    def test_stesso_gruppo(self, db, sc):
        # Call della stessa azienda.
        c, p = call_con_posizione(db, sc.y)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, c, p)
        assert detail_of(exc) == "stesso_gruppo"
        # Call di un'altra azienda dello stesso owner (Advisor).
        owner = new_user(db, "advisor")
        _, a = azienda(db, owner)
        _, b = azienda(db, owner)
        c, p = call_con_posizione(db, b)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, owner, a, c, p)
        assert detail_of(exc) == "stesso_gruppo"

    def test_call_solo_invitati(self, db, sc):
        c, p = call_con_posizione(db, sc.x, visibilita="solo_invitati")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, c, p)
        assert detail_of(exc) == "call_solo_invitati"

    def test_posizione_non_valida(self, db, sc):
        altra, pos_altra = call_con_posizione(db, sc.x)
        for p in (pos_altra, str(uuid.uuid4())):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                candida(db, sc.y_owner, sc.y, sc.call, p)
            assert detail_of(exc) == "posizione_non_valida"

    def test_requisiti_non_validi(self, db, sc):
        altra, _ = call_con_posizione(db, sc.x)
        req_altra = requisito(db, altra)
        for requisiti in ([req_altra], [sc.req, req_altra], [sc.req, sc.req],
                          [str(uuid.uuid4())]):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                candida(db, sc.y_owner, sc.y, sc.call, sc.pos, requisiti=requisiti)
            assert detail_of(exc) == "requisiti_non_validi", requisiti

    def test_una_attiva_poi_di_nuovo_dopo_il_rifiuto(self, db, sc):
        k = candidatura_pendente(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "candidatura_gia_attiva"
        decidi(db, sc.x_owner, sc.x, k, "rifiuta")
        candida(db, sc.y_owner, sc.y, sc.call, sc.pos)

    def test_invito_attivo(self, db, sc):
        invita(db, sc.x_owner, sc.x, sc.call, sc.y)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "invito_gia_attivo"

    def test_invito_scaduto_non_ancora_marcato_libera_il_posto(self, db, sc):
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        db.execute("update public.partner_candidature set scade_at = now() - interval '1 second' "
                   "where id = %s", (k,))
        candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert (cand(db, k)["stato"], cand(db, k)["motivo_chiusura"]) == ("scaduta", "ttl")

    @pytest.mark.parametrize(("modifica", "detail"), [
        ({"messaggio": "x" * 49}, "parametri_non_validi"),
        ({"messaggio": "  " + "x" * 48 + "  "}, "parametri_non_validi"),
        ({"messaggio": "x" * 2001}, "parametri_non_validi"),
        ({"messaggio": None}, "parametri_non_validi"),
        ({"pseudonimo": "abc"}, "parametri_non_validi"),
        ({"valutazione": [1]}, "parametri_non_validi"),
        ({"valutazione": None}, "parametri_non_validi"),
        ({"call_id": "non-un-uuid"}, "parametri_non_validi"),
        ({"posizione_id": None}, "parametri_non_validi"),
        ({"requisiti_dichiarati": {"a": 1}}, "parametri_non_validi"),
        ({"requisiti_dichiarati": ["x"]}, "parametri_non_validi"),
        ({"richiedi_non_sandbox": "forse"}, "parametri_non_validi"),
        ({"company_profile_id": "x"}, "parametri_non_validi"),
        ({"stato": "accettata"}, "parametri_non_validi"),
    ])
    def test_parametri_non_validi(self, db, sc, modifica, detail):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos, extra=modifica)
        assert detail_of(exc) == detail
        assert conta(db, "partner_candidature") == 0

    @pytest.mark.parametrize("chiave", ["owner_id", "company_id", "call_id", "valutazione",
                                        "pseudonimo", "messaggio", "posizione_id"])
    def test_chiavi_obbligatorie(self, db, sc, chiave):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos, togli=[chiave])
        assert detail_of(exc) == "parametri_non_validi"

    def test_payload_non_oggetto(self, db):
        for payload in (None, [], "x"):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                db.execute("select public.fn_partner_invia_candidatura(%s::jsonb)",
                           (None if payload is None else Jsonb(payload),))
            assert detail_of(exc) == "parametri_non_validi"

    def test_attore_e_azienda(self, db, sc):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos, attore=None)
        assert detail_of(exc) == "attore_non_titolare"
        # Azienda di un altro owner.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.x, sc.call, sc.pos)
        assert detail_of(exc) == "azienda_non_disponibile"
        # Owner inesistente.
        fantasma = str(uuid.uuid4())
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, fantasma, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "owner_not_found"

    @pytest.mark.parametrize("colonna", ["deleted_at", "archived_at"])
    def test_azienda_non_viva(self, db, sc, colonna):
        # L'aggiornamento revoca anche l'opt-in (0035): l'azienda si controlla prima.
        db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                   (sc.y,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            candida(db, sc.y_owner, sc.y, sc.call, sc.pos)
        assert detail_of(exc) == "azienda_non_disponibile"


# ------------------------------------------------------------------- inviti


class TestInvita:
    def test_invito_ok(self, db, sc):
        out = invita(db, sc.x_owner, sc.x, sc.call, sc.y, pos=sc.pos, messaggio="  Ciao!  ")
        k = cand(db, out["candidatura"]["id"])
        assert (k["tipo"], k["stato"], str(k["company_profile_id"]),
                str(k["creatore_company_profile_id"]), str(k["family_parent_id"]),
                str(k["inviata_da_user_id"]), k["messaggio"]) == (
            "invito", "inviata", sc.y, sc.x, sc.y_owner, sc.x_owner, "Ciao!")
        assert k["scade_at"] - k["created_at"] == timedelta(days=14)
        assert str(k["posizione_id"]) == sc.pos
        assert (out["inviti_attivi"], out["max_inviti"]) == (1, 30)
        (a,) = audit(db, "partenariato.invito_inviato")
        assert str(a["actor_id"]) == sc.x_owner and str(a["target_user_id"]) == sc.y_owner

    def test_messaggio_facoltativo_e_ttl(self, db, sc):
        k = cand(db, invita(db, sc.x_owner, sc.x, sc.call, sc.y, ttl=3,
                            messaggio="   ")["candidatura"]["id"])
        assert k["messaggio"] is None and k["posizione_id"] is None
        assert k["scade_at"] - k["created_at"] == timedelta(days=3)

    def test_gratuito_riceve_inviti(self, db, sc):
        owner, y = azienda(db, plan_slug=None)
        invita(db, sc.x_owner, sc.x, sc.call, y)

    def test_call_solo_invitati(self, db, sc):
        c, _ = call_con_posizione(db, sc.x, visibilita="solo_invitati")
        invita(db, sc.x_owner, sc.x, c, sc.y)

    @pytest.mark.parametrize("caso", ["senza_profilo", "revocato", "sospeso", "no_inviti",
                                      "eliminata", "archiviata", "inesistente", "stesso_owner",
                                      "stessa_azienda"])
    def test_partner_non_disponibile(self, db, sc, caso):
        y = sc.y
        if caso == "senza_profilo":
            _, y = azienda(db, opt_in=False)
        elif caso == "revocato":
            consenso(db, sc.y_owner, y, "revoca")
        elif caso == "sospeso":
            sospendi(db, y)
        elif caso == "no_inviti":
            db.execute("update public.company_partner_profiles set accetta_inviti = false "
                       "where company_profile_id = %s", (y,))
        elif caso in ("eliminata", "archiviata"):
            colonna = "deleted_at" if caso == "eliminata" else "archived_at"
            db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                       (y,))
        elif caso == "inesistente":
            y = str(uuid.uuid4())
        elif caso == "stesso_owner":
            _, y = azienda(db, sc.x_owner)
        else:
            y = sc.x
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, y)
        assert detail_of(exc) == "partner_non_disponibile"
        assert conta(db, "partner_candidature") == 0

    @pytest.mark.parametrize("colonna", ["deleted_at", "archived_at"])
    def test_azienda_non_viva_con_profilo_ancora_visibile(self, db, sc, colonna):
        """Difesa in profondità: l'azienda si controlla anche se il profilo è rimasto
        visibile (stato incoerente: la revoca di sistema non è passata)."""
        db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                   (sc.y,))
        con_guc(db, "update public.company_partner_profiles set visibile_come_partner = true "
                    "where company_profile_id = %s", (sc.y,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, sc.y)
        assert detail_of(exc) == "partner_non_disponibile"

    def test_call_di_un_altra_azienda(self, db, sc):
        owner = new_user(db, "advisor")
        _, a = azienda(db, owner, opt_in=False)
        _, b = azienda(db, owner, opt_in=False)
        c, _ = call_con_posizione(db, b)
        for company, call_id in ((a, c), (sc.x, str(uuid.uuid4()))):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                invita(db, owner if company == a else sc.x_owner, company, call_id, sc.y)
            assert detail_of(exc) == "call_not_found"

    @pytest.mark.parametrize("stato", ["bozza", "sospesa_moderazione", "chiusa_completata",
                                       "scaduta", "scadenza_passata"])
    def test_call_non_attiva(self, db, sc, stato):
        if stato == "scadenza_passata":
            c, _ = call_con_posizione(db, sc.x, scadenza_giorni=-1)
        else:
            c, _ = call_con_posizione(db, sc.x, stato=stato)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, c, sc.y)
        assert detail_of(exc) == "call_non_attiva"

    def test_posizione_non_valida(self, db, sc):
        _, pos_altra = call_con_posizione(db, sc.x)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, sc.y, pos=pos_altra)
        assert detail_of(exc) == "posizione_non_valida"

    def test_gia_attivo(self, db, sc):
        invita(db, sc.x_owner, sc.x, sc.call, sc.y)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, sc.y)
        assert detail_of(exc) == "invito_gia_attivo"
        z_owner, z = azienda(db)
        candida(db, z_owner, z, sc.call, sc.pos)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, z)
        assert detail_of(exc) == "candidatura_gia_attiva"

    def test_tetto_per_call(self, db, sc):
        _, z = azienda(db)
        _, w = azienda(db)
        invita(db, sc.x_owner, sc.x, sc.call, sc.y, max_inviti=2)
        invita(db, sc.x_owner, sc.x, sc.call, z, max_inviti=2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, w, max_inviti=2)
        assert detail_of(exc) == "inviti_esauriti_call"
        # Un'altra call ha il suo tetto.
        c, _ = call_con_posizione(db, sc.x)
        invita(db, sc.x_owner, sc.x, c, w, max_inviti=2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, c, sc.y, max_inviti=0)
        assert detail_of(exc) == "inviti_esauriti_call"

    def test_tetto_solo_sugli_inviti_pendenti(self, db, sc):
        _, z = azienda(db)
        _, w = azienda(db)
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y, max_inviti=1)["candidatura"]["id"]
        decidi(db, sc.y_owner, sc.y, k, "rifiuta")
        k2 = invita(db, sc.x_owner, sc.x, sc.call, z, max_inviti=1)["candidatura"]["id"]
        # Scaduto per TTL ma non marcato: si chiude e libera il tetto.
        db.execute("update public.partner_candidature set scade_at = now() where id = %s", (k2,))
        invita(db, sc.x_owner, sc.x, sc.call, w, max_inviti=1)
        assert (cand(db, k2)["stato"], cand(db, k2)["motivo_chiusura"]) == ("scaduta", "ttl")
        # Le candidature spontanee non contano.
        v_owner, v = azienda(db)
        candida(db, v_owner, v, sc.call, sc.pos)
        _, u = azienda(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, u, max_inviti=1)
        assert detail_of(exc) == "inviti_esauriti_call"

    def test_candidature_spontanee_fuori_dal_tetto(self, db, sc):
        z_owner, z = azienda(db)
        candida(db, z_owner, z, sc.call, sc.pos)
        candidatura_pendente(db, sc)
        _, w = azienda(db)
        assert invita(db, sc.x_owner, sc.x, sc.call, w, max_inviti=1)["inviti_attivi"] == 1

    def test_nessun_reinvito_dopo_il_rifiuto(self, db, sc):
        """Il rifiuto di Y vale per tutta la call: niente nuovi inviti (codice
        neutro), sulle altre call sì; Y può ancora candidarsi."""
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        decidi(db, sc.y_owner, sc.y, k, "rifiuta")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, sc.y)
        assert detail_of(exc) == "partner_non_disponibile"
        assert conta(db, "partner_candidature") == 1
        assert not audit(db, "partenariato.invito_inviato")[1:]
        altra, _ = call_con_posizione(db, sc.x)
        invita(db, sc.x_owner, sc.x, altra, sc.y)
        candida(db, sc.y_owner, sc.y, sc.call, sc.pos)

    @pytest.mark.parametrize("chiusura", ["ritiro", "ttl"])
    def test_reinvito_dopo_un_ritiro_o_una_scadenza(self, db, sc, chiusura):
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        if chiusura == "ritiro":
            ritira(db, sc.x_owner, sc.x, k)
        else:
            db.execute("update public.partner_candidature set scade_at = now() where id = %s",
                       (k,))
        invita(db, sc.x_owner, sc.x, sc.call, sc.y)

    @pytest.mark.parametrize("modifica", [
        {"max_inviti": -1}, {"max_inviti": 1001}, {"max_inviti": None}, {"ttl_giorni": 0},
        {"ttl_giorni": 91}, {"ttl_giorni": 1.5}, {"messaggio": "x" * 1001},
        {"pseudonimo": None}, {"valutazione": "x"}, {"invitato_company_id": None},
        {"codice_pubblico": "x"},
    ])
    def test_parametri_non_validi(self, db, sc, modifica):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, sc.y, extra=modifica)
        assert detail_of(exc) == "parametri_non_validi"

    def test_bordi_ammessi(self, db, sc):
        _, z = azienda(db)
        invita(db, sc.x_owner, sc.x, sc.call, sc.y, ttl=90, max_inviti=1000,
               messaggio="x" * 1000)
        invita(db, sc.x_owner, sc.x, sc.call, z, ttl=1)

    def test_attore_non_titolare(self, db, sc):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            invita(db, sc.x_owner, sc.x, sc.call, sc.y, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"


# ----------------------------------------------------------------- decisione


class TestDecidi:
    @pytest.mark.parametrize("rivela", [False, None])
    def test_accettazione_senza_rivelazione(self, db, sc, rivela):
        k = candidatura_pendente(db, sc)
        out = decidi(db, sc.x_owner, sc.x, k, rivela=rivela)
        riga_k = cand(db, k)
        assert riga_k["stato"] == "accettata" and str(riga_k["decisa_da_user_id"]) == sc.x_owner
        assert riga_k["decisa_at"] is not None
        assert str(riga_k["conversazione_id"]) == out["conversazione_id"]
        assert out["candidatura"]["stato"] == "accettata"
        c = conv(db, out["conversazione_id"])
        assert (str(c["partner_call_id"]), str(c["candidatura_id"]),
                str(c["company_creatore_id"]), str(c["company_partner_id"]), c["stato"]) == (
            sc.call, k, sc.x, sc.y, "aperta")
        (a,) = audit(db, "partenariato.candidatura_accettata")
        assert str(a["actor_id"]) == sc.x_owner and str(a["family_parent_id"]) == sc.x_owner
        assert str(a["target_user_id"]) == sc.y_owner
        assert a["payload"]["conversazione_id"] == out["conversazione_id"]
        assert audit(db, "partenariato.identita_rivelata") == []
        assert audit(db, "partenariato.contatti_rivelati") == []

    def test_accettazione_con_rivelazione_tre_righe(self, db, sc):
        # Dalla 0041 la rivelazione è simmetrica: entrambe le aziende con
        # l'identità verificata dall'admin.
        admin = new_user(db)
        db.execute("update public.profiles set role = 'admin' where id = %s", (admin,))
        for owner, company in ((sc.x_owner, sc.x), (sc.y_owner, sc.y)):
            db.execute("select public.fn_identita_richiedi(%s, %s, %s, null)",
                       (owner, company, owner))
            db.execute("select public.fn_identita_decidi(%s, %s, 'verificata', 'pec', null)",
                       (company, admin))
        k = candidatura_pendente(db, sc)
        out = decidi(db, sc.x_owner, sc.x, k, rivela=True)
        righe_audit = [a for a in audit(db, "partenariato.candidatura_accettata")
                       + audit(db, "partenariato.identita_rivelata")
                       + audit(db, "partenariato.contatti_rivelati")]
        assert len(righe_audit) == 3
        assert {a["payload"]["conversazione_id"] for a in righe_audit} == {
            out["conversazione_id"]}

    def test_audit_e_conversazione_nella_stessa_transazione(self, db, sc):
        """Un errore dopo la decisione annulla tutto: conversazione, stato e audit."""
        k = candidatura_pendente(db, sc)
        with pytest.raises(psycopg.errors.DivisionByZero):
            with db.transaction():
                decidi(db, sc.x_owner, sc.x, k, rivela=True)
                db.execute("select 1 / 0")
        assert cand(db, k)["stato"] == "inviata"
        assert conta(db, "partner_conversazioni") == 0
        assert audit(db, "partenariato.candidatura_accettata") == []

    def test_invito_lo_accetta_y(self, db, sc):
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "candidatura_non_trovata"
        out = decidi(db, sc.y_owner, sc.y, k)
        c = conv(db, out["conversazione_id"])
        assert (str(c["company_creatore_id"]), str(c["company_partner_id"])) == (sc.x, sc.y)
        (a,) = audit(db, "partenariato.candidatura_accettata")
        assert str(a["actor_id"]) == sc.y_owner and str(a["target_user_id"]) == sc.x_owner

    def test_lato_sbagliato_o_estraneo(self, db, sc):
        k = candidatura_pendente(db, sc)
        z_owner, z = azienda(db)
        for owner, company in ((sc.y_owner, sc.y), (z_owner, z)):
            for decisione in ("accetta", "rifiuta"):
                with pytest.raises(psycopg.errors.RaiseException) as exc:
                    decidi(db, owner, company, k, decisione)
                assert detail_of(exc) == "candidatura_non_trovata"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, str(uuid.uuid4()))
        assert detail_of(exc) == "candidatura_non_trovata"
        assert cand(db, k)["stato"] == "inviata"

    def test_azienda_del_titolare_non_viva(self, db, sc):
        k = candidatura_pendente(db, sc)
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (sc.x,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k, "rifiuta")
        assert detail_of(exc) == "azienda_non_disponibile"

    def test_doppia_decisione(self, db, sc):
        k = candidatura_pendente(db, sc)
        decidi(db, sc.x_owner, sc.x, k, "rifiuta", motivo="  Profilo non adatto  ")
        r = cand(db, k)
        assert (r["stato"], r["motivo_rifiuto"], r["conversazione_id"]) == (
            "rifiutata", "Profilo non adatto", None)
        for decisione in ("accetta", "rifiuta"):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                decidi(db, sc.x_owner, sc.x, k, decisione)
            assert detail_of(exc) == "candidatura_gia_decisa"
        (a,) = audit(db, "partenariato.candidatura_rifiutata")
        assert str(a["target_user_id"]) == sc.y_owner

    def test_rifiuto_motivo_vuoto_e_lungo(self, db, sc):
        k = candidatura_pendente(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k, "rifiuta", motivo="x" * 501)
        assert detail_of(exc) == "parametri_non_validi"
        decidi(db, sc.x_owner, sc.x, k, "rifiuta", motivo="   ")
        assert cand(db, k)["motivo_rifiuto"] is None

    def test_motivo_ignorato_all_accettazione(self, db, sc):
        k = candidatura_pendente(db, sc)
        decidi(db, sc.x_owner, sc.x, k, motivo="x" * 600)
        assert cand(db, k)["motivo_rifiuto"] is None

    @pytest.mark.parametrize("decisione", ["accetta", "rifiuta"])
    def test_invito_scaduto(self, db, sc, decisione):
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        db.execute("update public.partner_candidature set scade_at = now() - interval '1 minute' "
                   "where id = %s", (k,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.y_owner, sc.y, k, decisione)
        assert detail_of(exc) == "invito_scaduto"
        assert cand(db, k)["stato"] == "inviata"

    @pytest.mark.parametrize("caso", ["sospesa", "scadenza_passata", "bando_scaduto"])
    def test_accettare_richiede_la_call_aperta(self, db, sc, caso):
        k = candidatura_pendente(db, sc)
        if caso == "sospesa":
            db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                       "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' "
                       "where id = %s", (sc.call,))
        elif caso == "scadenza_passata":
            db.execute("update public.partner_calls set scadenza_call = current_date - 2 "
                       "where id = %s", (sc.call,))
        else:
            db.execute("update public.partner_calls set bando_scadenza = current_date - 2 "
                       "where id = %s", (sc.call,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "call_non_attiva"
        # Il rifiuto resta possibile.
        decidi(db, sc.x_owner, sc.x, k, "rifiuta")

    def test_identita_di_chi_decide(self, db, sc):
        k = candidatura_pendente(db, sc)
        db.execute("update public.company_data set sandbox = true where company_profile_id = %s",
                   (sc.x,))
        for non_sandbox in (_ASSENTE, None, True):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                decidi(db, sc.x_owner, sc.x, k, non_sandbox=non_sandbox)
            assert detail_of(exc) == "identita_non_verificata"
        decidi(db, sc.x_owner, sc.x, k, non_sandbox=False)

    @pytest.mark.parametrize("caso", ["sospeso", "identita", "eliminata"])
    def test_controparte_non_disponibile(self, db, sc, caso):
        k = candidatura_pendente(db, sc)
        if caso == "sospeso":
            sospendi(db, sc.y)
        elif caso == "identita":
            db.execute("delete from public.company_data where company_profile_id = %s",
                       (sc.y,))
        else:
            # Il soft delete revoca l'opt-in e chiude la candidatura: si simula un
            # soft delete senza revoca (profilo reso visibile dopo).
            db.execute("update public.company_profiles set deleted_at = now() where id = %s",
                       (sc.y,))
            con_guc(db, "update public.company_partner_profiles set visibile_come_partner = "
                        "true where company_profile_id = %s", (sc.y,))
            db.execute("update public.partner_candidature set stato = 'inviata', "
                       "motivo_chiusura = null, chiusa_at = null where id = %s", (k,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "controparte_non_disponibile"
        assert conta(db, "partner_conversazioni") == 0

    def test_controparte_con_owner_disattivato(self, db, sc):
        """«Viva» come per il backend (aziende_vive): anche il profilo dell'owner
        della controparte deve essere attivo, nei due sensi. Dalla 0040 un
        creatore con il titolare disattivato chiude anche la call
        (fn_partner_call_aperta), che si controlla prima: call_non_attiva."""
        k = candidatura_pendente(db, sc)
        db.execute("update public.profiles set is_active = false where id = %s", (sc.y_owner,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "controparte_non_disponibile"
        decidi(db, sc.x_owner, sc.x, k, "rifiuta")  # il rifiuto resta possibile
        db.execute("update public.profiles set is_active = true where id = %s", (sc.y_owner,))
        invito = invita(db, sc.x_owner, sc.x, sc.call, azienda(db)[1])["candidatura"]
        db.execute("update public.profiles set is_active = false where id = %s", (sc.x_owner,))
        y_owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                                 (invito["company_profile_id"],)).fetchone()[0])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, y_owner, invito["company_profile_id"], invito["id"])
        assert detail_of(exc) == "call_non_attiva"
        assert conta(db, "partner_conversazioni") == 0

    def test_y_sospeso_non_accetta_l_invito(self, db, sc):
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        sospendi(db, sc.y)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.y_owner, sc.y, k)
        assert detail_of(exc) == "profilo_partner_non_attivo"
        decidi(db, sc.y_owner, sc.y, k, "rifiuta")

    def test_invito_con_creatore_senza_identita(self, db, sc):
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        db.execute("update public.company_data set stato_impresa = 'Cessata' "
                   "where company_profile_id = %s", (sc.x,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.y_owner, sc.y, k)
        assert detail_of(exc) == "controparte_non_disponibile"

    def test_stesso_gruppo(self, db, sc):
        k = candidatura_pendente(db, sc)
        db.execute("update public.company_profiles set parent_id = %s where id = %s",
                   (sc.x_owner, sc.y))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "stesso_gruppo"

    def test_parametri(self, db, sc):
        k = candidatura_pendente(db, sc)
        for decisione in (None, "annulla"):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                decidi(db, sc.x_owner, sc.x, k, decisione)
            assert detail_of(exc) == "parametri_non_validi"
        for attore in (None, new_user(db)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                decidi(db, sc.x_owner, sc.x, k, attore=attore)
            assert detail_of(exc) == "attore_non_titolare"


class TestEsclusivita:
    def _setup(self, db, sc, *, esclusiva_a=True):
        bando = next(_bandi)
        a, pa = call_con_posizione(db, sc.x, bando_id=bando, esclusivita=esclusiva_a)
        return bando, a, pa

    def test_y_creatrice_di_un_altra_call_pubblicata(self, db, sc):
        bando, a, pa = self._setup(db, sc)
        inserisci_call(db, sc.y, bando_id=bando)
        k = candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "esclusivita_violata"
        assert cand(db, k)["stato"] == "inviata" and conta(db, "partner_conversazioni") == 0

    @pytest.mark.parametrize("stato", ["bozza", "chiusa_annullata", "chiusa_completata"])
    def test_call_di_y_non_pubblicata_non_impegna(self, db, sc, stato):
        bando, a, pa = self._setup(db, sc)
        inserisci_call(db, sc.y, bando_id=bando, stato=stato)
        k = candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"]
        decidi(db, sc.x_owner, sc.x, k)

    def test_y_gia_accettata_sullo_stesso_bando(self, db, sc):
        bando, a, pa = self._setup(db, sc)
        z_owner, z = azienda(db)
        c, pc = call_con_posizione(db, z, bando_id=bando)  # non esclusiva
        k1 = candida(db, sc.y_owner, sc.y, c, pc)["candidatura"]["id"]
        decidi(db, z_owner, z, k1)
        k2 = candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k2)
        assert detail_of(exc) == "esclusivita_violata"

    def test_simmetrica_se_l_altra_call_e_esclusiva(self, db, sc):
        """Y accettata su una call esclusiva non entra in un'altra call dello stesso
        bando, anche se questa non è marcata esclusiva (il vincolo è del bando)."""
        bando, a, pa = self._setup(db, sc)  # A esclusiva
        k1 = candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"]
        decidi(db, sc.x_owner, sc.x, k1)
        z_owner, z = azienda(db)
        c, pc = call_con_posizione(db, z, bando_id=bando)  # non esclusiva
        k2 = candida(db, sc.y_owner, sc.y, c, pc)["candidatura"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, z_owner, z, k2)
        assert detail_of(exc) == "esclusivita_violata"
        # Anche Y creatrice di una call esclusiva sullo stesso bando.
        bando2 = next(_bandi)
        inserisci_call(db, sc.y, bando_id=bando2, esclusivita=True)
        d, pd = call_con_posizione(db, sc.x, bando_id=bando2)  # non esclusiva
        k3 = candida(db, sc.y_owner, sc.y, d, pd)["candidatura"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, sc.x_owner, sc.x, k3)
        assert detail_of(exc) == "esclusivita_violata"

    def test_senza_esclusivita_nessun_vincolo(self, db, sc):
        bando, a, pa = self._setup(db, sc, esclusiva_a=False)
        inserisci_call(db, sc.y, bando_id=bando)
        z_owner, z = azienda(db)
        c, pc = call_con_posizione(db, z, bando_id=bando)
        decidi(db, z_owner, z, candida(db, sc.y_owner, sc.y, c, pc)["candidatura"]["id"])
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"])

    def test_altro_bando_o_candidature_non_accettate(self, db, sc):
        bando, a, pa = self._setup(db, sc)
        inserisci_call(db, sc.y, bando_id=next(_bandi))  # altro bando
        z_owner, z = azienda(db)
        c, pc = call_con_posizione(db, z, bando_id=bando)
        k_rif = candida(db, sc.y_owner, sc.y, c, pc)["candidatura"]["id"]
        decidi(db, z_owner, z, k_rif, "rifiuta")  # rifiutata: non impegna
        w_owner, w = azienda(db)
        d, pd = call_con_posizione(db, w, bando_id=bando)
        candida(db, sc.y_owner, sc.y, d, pd)  # pendente: non impegna
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"])

    def test_accettata_su_call_annullata_non_impegna(self, db, sc):
        """L'accettata non si ritira: se il creatore annulla la call, Y resta libera
        sul bando. Completata o scaduta invece impegna ancora."""
        bando, a, pa = self._setup(db, sc)  # A esclusiva
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"])
        z_owner, z = azienda(db)
        c, pc = call_con_posizione(db, z, bando_id=bando)
        k = candida(db, sc.y_owner, sc.y, c, pc)["candidatura"]["id"]
        chiudi_call(db, sc.x_owner, sc.x, a, "annullata")
        assert db.execute("select public.fn_partner_esclusivita_violata(%s, %s, %s, false)",
                          (sc.y, c, bando)).fetchone()[0] is False
        decidi(db, z_owner, z, k)

    @pytest.mark.parametrize("stato", ["chiusa_completata", "scaduta"])
    def test_accettata_su_call_completata_o_scaduta_impegna(self, db, sc, stato):
        bando, a, pa = self._setup(db, sc)
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"])
        z_owner, z = azienda(db)
        c, pc = call_con_posizione(db, z, bando_id=bando)
        k = candida(db, sc.y_owner, sc.y, c, pc)["candidatura"]["id"]
        motivo = "creatore_completata" if stato == "chiusa_completata" else "scadenza_call"
        db.execute("update public.partner_calls set stato = %s, chiusa_at = now(), "
                   "motivo_chiusura = %s where id = %s", (stato, motivo, a))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi(db, z_owner, z, k)
        assert detail_of(exc) == "esclusivita_violata"

    def test_pubblicazione_con_accettata_sullo_stesso_bando(self, db, sc):
        """C6: l'esclusività si controlla anche alla pubblicazione. Y accettata su
        una call esclusiva non pubblica la propria call sullo stesso bando."""
        bando, a, pa = self._setup(db, sc)  # A esclusiva
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"])
        d = bozza_completa(db, sc.y, bando_id=bando)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica_call(db, sc.y_owner, sc.y, d)
        assert detail_of(exc) == "esclusivita_violata"
        stato = db.execute("select stato from public.partner_calls where id = %s",
                           (d,)).fetchone()[0]
        assert stato == "bozza" and not audit(db, "partenariato.call_pubblicata")
        # Su un altro bando, o con la call accettata annullata, sì.
        altra = bozza_completa(db, sc.y, bando_id=next(_bandi))
        assert pubblica_call(db, sc.y_owner, sc.y, altra)["stato"] == "pubblicata"
        chiudi_call(db, sc.y_owner, sc.y, altra, "annullata")  # limite del piano Smart
        chiudi_call(db, sc.x_owner, sc.x, a, "annullata")
        assert pubblica_call(db, sc.y_owner, sc.y, d)["stato"] == "pubblicata"

    @pytest.mark.parametrize("esclusiva_propria", [True, False])
    def test_pubblicazione_simmetrica(self, db, sc, esclusiva_propria):
        """Basta che una delle due call sia esclusiva."""
        bando, a, pa = self._setup(db, sc, esclusiva_a=not esclusiva_propria)
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"])
        d = bozza_completa(db, sc.y, bando_id=bando, esclusivita=esclusiva_propria)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            pubblica_call(db, sc.y_owner, sc.y, d)
        assert detail_of(exc) == "esclusivita_violata"

    def test_pubblicazione_senza_esclusivita(self, db, sc):
        bando, a, pa = self._setup(db, sc, esclusiva_a=False)
        decidi(db, sc.x_owner, sc.x, candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"])
        d = bozza_completa(db, sc.y, bando_id=bando)
        assert pubblica_call(db, sc.y_owner, sc.y, d)["stato"] == "pubblicata"

    def test_esclusivita_violata_funzione_null_prudente(self, db, sc):
        bando, a, _ = self._setup(db, sc, esclusiva_a=False)
        inserisci_call(db, sc.y, bando_id=bando)
        assert db.execute("select public.fn_partner_esclusivita_violata(%s, %s, %s, null)",
                          (sc.y, a, bando)).fetchone()[0] is True
        assert db.execute("select public.fn_partner_esclusivita_violata(%s, %s, %s, false)",
                          (sc.y, a, bando)).fetchone()[0] is False


# ------------------------------------------------------------ ritiro e scadenze


class TestRitira:
    def test_y_ritira_la_candidatura(self, db, sc):
        k = candidatura_pendente(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.x_owner, sc.x, k)
        assert detail_of(exc) == "candidatura_non_trovata"
        out = ritira(db, sc.y_owner, sc.y, k)
        r = cand(db, k)
        assert (r["stato"], r["motivo_chiusura"], r["decisa_at"]) == ("ritirata", None, None)
        assert r["chiusa_at"] is not None and out["candidatura"]["stato"] == "ritirata"
        (a,) = audit(db, "partenariato.candidatura_ritirata")
        assert str(a["actor_id"]) == sc.y_owner
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.y_owner, sc.y, k)
        assert detail_of(exc) == "candidatura_gia_decisa"

    def test_x_ritira_l_invito(self, db, sc):
        k = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.y_owner, sc.y, k)
        assert detail_of(exc) == "candidatura_non_trovata"
        ritira(db, sc.x_owner, sc.x, k)
        assert cand(db, k)["stato"] == "ritirata"

    def test_non_si_ritira_una_decisa_o_scaduta(self, db, sc):
        k, _ = accettata(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.y_owner, sc.y, k)
        assert detail_of(exc) == "candidatura_gia_decisa"
        _, z = azienda(db)
        inv = invita(db, sc.x_owner, sc.x, sc.call, z)["candidatura"]["id"]
        db.execute("update public.partner_candidature set scade_at = now() where id = %s",
                   (inv,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.x_owner, sc.x, inv)
        assert detail_of(exc) == "invito_scaduto"

    def test_guardie(self, db, sc):
        k = candidatura_pendente(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.y_owner, sc.y, k, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.y_owner, sc.y, str(uuid.uuid4()))
        assert detail_of(exc) == "candidatura_non_trovata"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ritira(db, sc.y_owner, sc.y, None)
        assert detail_of(exc) == "parametri_non_validi"


class TestScadiInviti:
    def _inviti(self, db, sc, n):
        ids = []
        for _ in range(n):
            _, z = azienda(db)
            ids.append(invita(db, sc.x_owner, sc.x, sc.call, z)["candidatura"]["id"])
        return ids

    def test_solo_inviti_scaduti(self, db, sc):
        scaduti = self._inviti(db, sc, 2)
        vivo = self._inviti(db, sc, 1)[0]
        k = candidatura_pendente(db, sc)
        db.execute("update public.partner_candidature set scade_at = now() - interval '1 day' "
                   "where id = any (%s::uuid[])", (scaduti,))
        assert scadi_inviti(db) == 2
        for i in scaduti:
            r = cand(db, i)
            assert (r["stato"], r["motivo_chiusura"]) == ("scaduta", "ttl")
            assert r["chiusa_at"] is not None
        assert cand(db, vivo)["stato"] == "inviata" and cand(db, k)["stato"] == "inviata"
        assert scadi_inviti(db) == 0

    def test_limite_e_ordine(self, db, sc):
        ids = self._inviti(db, sc, 3)
        for giorni, i in zip((1, 3, 2), ids):
            db.execute("update public.partner_candidature set scade_at = now() - "
                       "make_interval(days => %s) where id = %s", (giorni, i))
        assert scadi_inviti(db, 1) == 1
        assert cand(db, ids[1])["stato"] == "scaduta"  # il più vecchio
        assert scadi_inviti(db, 0) == 1  # minimo 1
        assert scadi_inviti(db, None) == 1  # NULL = 500

    def test_salta_le_righe_bloccate(self, db, sc):
        (i,) = self._inviti(db, sc, 1)
        db.execute("update public.partner_candidature set scade_at = now() - interval '1 day' "
                   "where id = %s", (i,))
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select 1 from public.partner_candidature where id = %s for update",
                          (i,))
            db.execute("set lock_timeout = '300ms'")
            assert scadi_inviti(db) == 0
        finally:
            db.execute("set lock_timeout = 0")
            chiudi_tutto(altra)
        assert scadi_inviti(db) == 1


# ------------------------------------------------------------- trigger: call


class TestChiusuraCall:
    def _popola(self, db, sc):
        k_acc, conv_id = accettata(db, sc)
        z_owner, z = azienda(db)
        k_pend = candida(db, z_owner, z, sc.call, sc.pos)["candidatura"]["id"]
        _, w = azienda(db)
        inv = invita(db, sc.x_owner, sc.x, sc.call, w)["candidatura"]["id"]
        v_owner, v = azienda(db)
        k_rif = candida(db, v_owner, v, sc.call, sc.pos)["candidatura"]["id"]
        decidi(db, sc.x_owner, sc.x, k_rif, "rifiuta")
        # Una candidatura su un'altra call non si tocca.
        altra, pa = call_con_posizione(db, sc.x)
        k_altra = candida(db, z_owner, z, altra, pa)["candidatura"]["id"]
        return SimpleNamespace(acc=k_acc, conv=conv_id, pend=k_pend, inv=inv, rif=k_rif,
                               altra=k_altra)

    def _verifica(self, db, p, motivo="call_chiusa"):
        for i in (p.pend, p.inv):
            r = cand(db, i)
            assert (r["stato"], r["motivo_chiusura"]) == ("scaduta", motivo)
            assert r["chiusa_at"] is not None
        assert cand(db, p.acc)["stato"] == "accettata"
        assert cand(db, p.rif)["stato"] == "rifiutata"
        assert cand(db, p.altra)["stato"] == "inviata"
        assert conv(db, p.conv)["stato"] == "aperta"

    @pytest.mark.parametrize("esito", ["completata", "annullata"])
    def test_chiusura_dal_creatore(self, db, sc, esito):
        p = self._popola(db, sc)
        chiudi_call(db, sc.x_owner, sc.x, sc.call, esito)
        self._verifica(db, p)
        # La conversazione resta usabile dopo chiusa_completata.
        scrivi(db, sc.y_owner, sc.y, p.conv)

    def test_chiusura_automatica(self, db, sc):
        p = self._popola(db, sc)
        assert db.execute("select public.fn_partner_call_chiudi_auto(%s, 'scaduta', "
                          "'scadenza_call')", (sc.call,)).fetchone()[0] is True
        self._verifica(db, p)

    def test_chiusura_per_moderazione(self, db, sc):
        p = self._popola(db, sc)
        db.execute("update public.partner_calls set stato = 'chiusa_annullata', "
                   "chiusa_at = now(), motivo_chiusura = 'moderazione' where id = %s", (sc.call,))
        self._verifica(db, p, "moderazione")

    def test_sospensione_non_chiude_poi_la_chiusura_si(self, db, sc):
        p = self._popola(db, sc)
        db.execute("update public.partner_calls set stato = 'sospesa_moderazione', "
                   "sospesa_at = now(), stato_prima_sospensione = 'pubblicata' where id = %s",
                   (sc.call,))
        assert cand(db, p.pend)["stato"] == "inviata"
        db.execute("update public.partner_calls set stato = 'chiusa_annullata', "
                   "chiusa_at = now(), motivo_chiusura = 'creatore_annullata' where id = %s",
                   (sc.call,))
        self._verifica(db, p)

    def test_altri_aggiornamenti_non_chiudono(self, db, sc):
        p = self._popola(db, sc)
        db.execute("update public.partner_calls set bando_verificato_at = now(), "
                   "versione = versione + 1 where id = %s", (sc.call,))
        db.execute("update public.partner_calls set stato = 'pubblicata' where id = %s",
                   (sc.call,))
        assert cand(db, p.pend)["stato"] == "inviata"


# ---------------------------------------------------------- trigger: opt-out


class TestRevocaOptOut:
    def _popola(self, db, sc):
        """Y ha: una candidatura pendente, un invito pendente, una candidatura
        accettata (con conversazione) e una rifiutata; Z (altra azienda) ha una
        candidatura pendente che non si tocca."""
        k_pend = candidatura_pendente(db, sc)
        c2, _ = call_con_posizione(db, sc.x)
        inv = invita(db, sc.x_owner, sc.x, c2, sc.y)["candidatura"]["id"]
        c3, p3 = call_con_posizione(db, sc.x)
        k_acc = candida(db, sc.y_owner, sc.y, c3, p3)["candidatura"]["id"]
        conv_id = decidi(db, sc.x_owner, sc.x, k_acc)["conversazione_id"]
        c4, p4 = call_con_posizione(db, sc.x)
        k_rif = candida(db, sc.y_owner, sc.y, c4, p4)["candidatura"]["id"]
        decidi(db, sc.x_owner, sc.x, k_rif, "rifiuta")
        z_owner, z = azienda(db)
        k_z = candida(db, z_owner, z, sc.call, sc.pos)["candidatura"]["id"]
        return SimpleNamespace(pend=k_pend, inv=inv, acc=k_acc, conv=conv_id, rif=k_rif, z=k_z)

    def _verifica(self, db, p):
        r = cand(db, p.pend)
        assert (r["stato"], r["motivo_chiusura"]) == ("ritirata", "opt_out")
        r = cand(db, p.inv)
        assert (r["stato"], r["motivo_chiusura"]) == ("scaduta", "opt_out")
        assert cand(db, p.acc)["stato"] == "accettata"
        assert cand(db, p.rif)["stato"] == "rifiutata"
        assert cand(db, p.z)["stato"] == "inviata"
        assert conv(db, p.conv)["stato"] == "aperta"

    def test_revoca_dell_utente(self, db, sc):
        p = self._popola(db, sc)
        consenso(db, sc.y_owner, sc.y, "revoca")
        self._verifica(db, p)
        # La conversazione già aperta resta usabile.
        scrivi(db, sc.y_owner, sc.y, p.conv)

    @pytest.mark.parametrize("modifica", [
        "deleted_at = now()", "archived_at = now()", "partita_iva = '99999999999'",
        "ragione_sociale = 'Altro nome Srl'"])
    def test_revoca_di_sistema(self, db, sc, modifica):
        p = self._popola(db, sc)
        db.execute(f"update public.company_profiles set {modifica} where id = %s", (sc.y,))
        (r,) = db.execute("select origine from public.partner_consents where "
                          "company_profile_id = %s and azione = 'revocato'", (sc.y,)).fetchall()
        assert r[0] == "sistema"
        self._verifica(db, p)

    def test_riga_di_revoca_scritta_direttamente(self, db, sc):
        """Il trigger scatta su qualunque riga «revocato» del registro."""
        p = self._popola(db, sc)
        db.execute("insert into public.partner_consents (company_profile_id, family_parent_id, "
                   "azione, origine) values (%s, %s, 'revocato', 'sistema')",
                   (sc.y, sc.y_owner))
        self._verifica(db, p)

    @pytest.mark.parametrize("azione", ["concesso", "anonimato"])
    def test_altre_azioni_non_chiudono(self, db, sc, azione):
        k = candidatura_pendente(db, sc)
        db.execute("insert into public.partner_consents (company_profile_id, family_parent_id, "
                   "azione, informativa_versione, origine, attore_user_id, anonimo) "
                   "values (%s, %s, %s, %s, 'pagina_azienda', %s, true)",
                   (sc.y, sc.y_owner, azione, VERSIONE, sc.y_owner))
        assert cand(db, k)["stato"] == "inviata"

    def test_revoca_senza_visibilita_nessun_effetto(self, db, sc):
        k = candidatura_pendente(db, sc)
        consenso(db, sc.y_owner, sc.y, "revoca")
        assert cand(db, k)["stato"] == "ritirata"
        # Una seconda revoca non scrive righe (profilo già non visibile).
        consenso(db, sc.y_owner, sc.y, "revoca")
        assert db.execute("select count(*) from public.partner_consents where "
                          "company_profile_id = %s and azione = 'revocato'",
                          (sc.y,)).fetchone()[0] == 1

    def test_funzione_diretta(self, db, sc):
        k = candidatura_pendente(db, sc)
        assert db.execute("select public.fn_partner_scadi_per_opt_out(%s)",
                          (sc.y,)).fetchone()[0] == 1
        assert db.execute("select public.fn_partner_scadi_per_opt_out(%s)",
                          (sc.y,)).fetchone()[0] == 0
        assert cand(db, k)["stato"] == "ritirata"


# -------------------------------------------------------- posizioni della call


class TestPosizioniConCandidature:
    POS = {"titolo": "Organismo di ricerca"}

    def _lista(self, db, call_id):
        return [str(r[0]) for r in db.execute(
            "select id from public.partner_call_posizioni where call_id = %s order by ordine",
            (call_id,)).fetchall()]

    @pytest.mark.parametrize("stato", ["inviata", "accettata"])
    def test_posizione_con_candidature_attive_non_si_rimuove(self, db, sc, stato):
        altra = posizione(db, sc.call, "Seconda posizione")
        k = candidatura_pendente(db, sc)
        if stato == "accettata":
            decidi(db, sc.x_owner, sc.x, k)
        prima = self._lista(db, sc.call)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call,
                                  [{"id": altra, "titolo": "Seconda posizione"}])
        assert detail_of(exc) == "posizione_con_candidature"
        assert self._lista(db, sc.call) == prima
        assert str(cand(db, k)["posizione_id"]) == sc.pos

    def test_anche_con_un_invito_attivo(self, db, sc):
        altra = posizione(db, sc.call, "Seconda posizione")
        invita(db, sc.x_owner, sc.x, sc.call, sc.y, pos=sc.pos)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call,
                                  [{"id": altra, "titolo": "Seconda posizione"}])
        assert detail_of(exc) == "posizione_con_candidature"

    def test_candidature_chiuse_non_bloccano(self, db, sc):
        altra = posizione(db, sc.call, "Seconda posizione")
        k = candidatura_pendente(db, sc)
        decidi(db, sc.x_owner, sc.x, k, "rifiuta")
        out = sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call,
                                    [{"id": altra, "titolo": "Seconda posizione"}])
        assert [p["id"] for p in out] == [altra]
        assert cand(db, k)["posizione_id"] is None  # ON DELETE SET NULL

    def test_posizione_conservata_resta_collegata(self, db, sc):
        """La ridefinizione aggiorna per id: la candidatura resta sulla stessa riga
        (la 0037 cancellava e reinseriva tutto, azzerando posizione_id)."""
        k = candidatura_pendente(db, sc)
        creata = db.execute("select created_at from public.partner_call_posizioni "
                            "where id = %s", (sc.pos,)).fetchone()[0]
        out = sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call, [
            {"titolo": "Nuova in testa"},
            {"id": sc.pos, "titolo": "Titolo modificato", "quota_ipotizzata_pct": 25}])
        assert [p["titolo"] for p in out] == ["Nuova in testa", "Titolo modificato"]
        assert out[1]["id"] == sc.pos and out[1]["ordine"] == 1
        assert str(cand(db, k)["posizione_id"]) == sc.pos
        assert db.execute("select created_at from public.partner_call_posizioni where id = %s",
                          (sc.pos,)).fetchone()[0] == creata
        # La call pubblicata ha una nuova versione (la 0037 lo faceva già).
        assert db.execute("select versione from public.partner_calls where id = %s",
                          (sc.call,)).fetchone()[0] == 1
        # Stesso contenuto → nessuna nuova versione.
        sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call, [
            {"id": out[0]["id"], "titolo": "Nuova in testa"},
            {"id": sc.pos, "titolo": "Titolo modificato", "quota_ipotizzata_pct": 25}])
        assert db.execute("select versione from public.partner_calls where id = %s",
                          (sc.call,)).fetchone()[0] == 1

    def test_campi_omessi_tornano_ai_default(self, db, sc):
        sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call, [
            {"id": sc.pos, "titolo": "Con dettagli", "ruolo": "capofila", "note": "Nota",
             "regioni": [3], "numero": 2, "requisiti_ids": [sc.req]}])
        (p,) = sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call,
                                     [{"id": sc.pos, "titolo": "Senza dettagli"}])
        assert (p["ruolo"], p["note"], p["regioni"], p["numero"], p["requisiti_ids"]) == (
            "partner", None, [], 1, [])

    def test_errori_invariati(self, db, sc):
        for lista, detail in (([{"titolo": "ab"}], "posizioni_non_valide"),
                              ([{"id": str(uuid.uuid4()), "titolo": "Posizione"}],
                               "posizioni_non_valide"),
                              ([{"titolo": "Posizione", "requisiti_ids": [str(uuid.uuid4())]}],
                               "posizioni_non_valide"),
                              ([], "call_incompleta")):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                sostituisci_posizioni(db, sc.x_owner, sc.x, sc.call, lista)
            assert detail_of(exc) == detail
        assert self._lista(db, sc.call) == [sc.pos]


# ---------------------------------------------------------------------- chat


class TestMessaggi:
    def test_invio(self, db, sc):
        _, conv_id = accettata(db, sc)
        out = scrivi(db, sc.y_owner, sc.y, conv_id, "Ecco la nostra PEC: info@pec.example.it")
        m = out["messaggio"]
        assert (m["mittente_company_profile_id"], m["mittente_user_id"], m["testo"]) == (
            sc.y, sc.y_owner, "Ecco la nostra PEC: info@pec.example.it")
        assert out["company_destinataria_id"] == sc.x and out["duplicato"] is False
        c = conv(db, conv_id)
        assert c["ultimo_messaggio_id"] == m["id"] and c["ultimo_messaggio_at"] is not None
        assert lettura(db, conv_id, sc.y_owner)["letto_fino_a_id"] == m["id"]
        out2 = scrivi(db, sc.x_owner, sc.x, conv_id)
        assert out2["company_destinataria_id"] == sc.y
        assert conv(db, conv_id)["ultimo_messaggio_id"] == out2["messaggio"]["id"]

    def test_idempotente_per_client_msg_id(self, db, sc):
        _, conv_id = accettata(db, sc)
        client = str(uuid.uuid4())
        a = scrivi(db, sc.y_owner, sc.y, conv_id, "Primo invio", client=client)
        b = scrivi(db, sc.y_owner, sc.y, conv_id, "Ritentato", client=client)
        assert b["messaggio"]["id"] == a["messaggio"]["id"] and b["duplicato"] is True
        assert b["messaggio"]["testo"] == "Primo invio"
        assert conta(db, "partner_messaggi") == 1
        # La stessa chiave dall'altra azienda non restituisce il messaggio altrui.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.x_owner, sc.x, conv_id, "Altro", client=client)
        assert detail_of(exc) == "parametri_non_validi"
        # Stessa chiave in un'altra conversazione: messaggio nuovo.
        _, altra = accettata_altra(db, sc)
        assert scrivi(db, sc.x_owner, sc.x, altra, client=client)["duplicato"] is False

    @pytest.mark.parametrize("testo", ["", "   \n", "x" * 5001, None])
    def test_testo_non_valido(self, db, sc, testo):
        _, conv_id = accettata(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.y_owner, sc.y, conv_id, testo)
        assert detail_of(exc) == "parametri_non_validi"

    def test_testo_ai_bordi(self, db, sc):
        _, conv_id = accettata(db, sc)
        scrivi(db, sc.y_owner, sc.y, conv_id, "x")
        scrivi(db, sc.y_owner, sc.y, conv_id, "x" * 5000)

    def test_solo_le_parti_e_il_titolare(self, db, sc):
        _, conv_id = accettata(db, sc)
        z_owner, z = azienda(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, z_owner, z, conv_id)
        assert detail_of(exc) == "conversazione_non_trovata"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.y_owner, sc.y, str(uuid.uuid4()))
        assert detail_of(exc) == "conversazione_non_trovata"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.y_owner, sc.y, conv_id, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.y_owner, sc.x, conv_id)
        assert detail_of(exc) == "azienda_non_disponibile"
        fantasma = str(uuid.uuid4())
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, fantasma, sc.y, conv_id)
        assert detail_of(exc) == "owner_not_found"

    def test_conversazione_chiusa(self, db, sc):
        _, conv_id = accettata(db, sc)
        client = str(uuid.uuid4())
        scrivi(db, sc.y_owner, sc.y, conv_id, client=client)
        chiudi_conv(db, sc.x_owner, sc.x, conv_id)
        for owner, company in ((sc.y_owner, sc.y), (sc.x_owner, sc.x)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                scrivi(db, owner, company, conv_id)
            assert detail_of(exc) == "conversazione_chiusa"
        # Il ritento di un messaggio già scritto resta idempotente.
        assert scrivi(db, sc.y_owner, sc.y, conv_id, client=client)["duplicato"] is True

    @pytest.mark.parametrize("colonna", ["deleted_at", "archived_at"])
    def test_controparte_non_disponibile(self, db, sc, colonna):
        _, conv_id = accettata(db, sc)
        db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                   (sc.y,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.x_owner, sc.x, conv_id)
        assert detail_of(exc) == "controparte_non_disponibile"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.y_owner, sc.y, conv_id)
        assert detail_of(exc) == "azienda_non_disponibile"

    def test_controparte_con_owner_disattivato(self, db, sc):
        """Per il backend la conversazione è in sola lettura: anche la RPC rifiuta."""
        _, conv_id = accettata(db, sc)
        db.execute("update public.profiles set is_active = false where id = %s", (sc.y_owner,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            scrivi(db, sc.x_owner, sc.x, conv_id)
        assert detail_of(exc) == "controparte_non_disponibile"
        assert conta(db, "partner_messaggi") == 0


class TestLetture:
    def _chat(self, db, sc):
        """Y Advisor con due aziende: Y (con un membro che la vede) e Y2 (con un
        membro che vede solo Y2)."""
        y_owner = new_user(db, "advisor")
        _, y = azienda(db, y_owner)
        _, y2 = azienda(db, y_owner, opt_in=False)
        m_y = membro(db, y_owner, y)
        m_y2 = membro(db, y_owner, y2)
        k = candida(db, y_owner, y, sc.call, sc.pos)["candidatura"]["id"]
        conv_id = decidi(db, sc.x_owner, sc.x, k)["conversazione_id"]
        return SimpleNamespace(y_owner=y_owner, y=y, y2=y2, m_y=m_y, m_y2=m_y2, conv=conv_id)

    def test_segna_letto_titolare_e_membro(self, db, sc):
        c = self._chat(db, sc)
        m1 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        m2 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        assert segna_letto(db, c.conv, c.y_owner, c.y, m1) == m1
        assert segna_letto(db, c.conv, c.m_y, c.y, m2) == m2
        # Solo in avanti.
        assert segna_letto(db, c.conv, c.y_owner, c.y, 0) == m1
        # Mai oltre l'ultimo messaggio; NULL = fino all'ultimo.
        assert segna_letto(db, c.conv, c.y_owner, c.y, m2 + 1000) == m2
        assert segna_letto(db, c.conv, sc.x_owner, sc.x, None) == m2
        r = lettura(db, c.conv, c.m_y)
        assert str(r["company_profile_id"]) == c.y

    def test_segna_letto_utente_estraneo_rifiutato(self, db, sc):
        c = self._chat(db, sc)
        m1 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        z_owner, z = azienda(db)
        estranei = [
            (c.m_y2, c.y),               # membro senza visibilità su Y
            (sc.x_owner, c.y),           # titolare dell'altra parte, per Y
            (c.y_owner, sc.x),           # titolare di Y, per X
            (new_user(db), c.y),         # utente qualsiasi
            (z_owner, z),                # azienda estranea
            (c.y_owner, c.y2),           # azienda dell'owner ma non parte
        ]
        for user, company in estranei:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                segna_letto(db, c.conv, user, company, m1)
            assert detail_of(exc) == "conversazione_non_trovata", (user, company)
        assert conta(db, "partner_conversazione_letture") == 1  # solo il mittente
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            segna_letto(db, c.conv, c.y_owner, c.y, -1)
        assert detail_of(exc) == "parametri_non_validi"

    def test_membro_non_attivo_o_profilo_disattivo(self, db, sc):
        c = self._chat(db, sc)
        m1 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        in_attesa_membro = membro(db, c.y_owner, c.y, attivo=False)
        db.execute("update public.profiles set is_active = false where id = %s", (c.m_y,))
        for user in (in_attesa_membro, c.m_y):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                segna_letto(db, c.conv, user, c.y, m1)
            assert detail_of(exc) == "conversazione_non_trovata"

    def test_claim_email_una_per_raffica(self, db, sc):
        c = self._chat(db, sc)
        destinatari = [c.y_owner, c.m_y]
        m1 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        assert claim(db, c.conv, c.y, destinatari, m1) == set(destinatari)
        m2 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        # Seconda chiamata senza lettura: nessuna email.
        assert claim(db, c.conv, c.y, destinatari, m2) == set()
        # Il titolare legge fino a m1 (non m2): la sua email era per m1, ora
        # email ≤ letto → una nuova email per m2; il membro non ha letto → niente.
        segna_letto(db, c.conv, c.y_owner, c.y, m1)
        assert claim(db, c.conv, c.y, destinatari, m2) == {c.y_owner}
        assert lettura(db, c.conv, c.y_owner)["email_fino_a_id"] == m2
        # Chi ha già letto tutto non riceve email.
        segna_letto(db, c.conv, c.y_owner, c.y, m2)
        segna_letto(db, c.conv, c.m_y, c.y, m2)
        assert claim(db, c.conv, c.y, destinatari, m2) == set()
        m3 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        assert claim(db, c.conv, c.y, destinatari, m3) == set(destinatari)

    def test_claim_ignora_gli_utenti_estranei(self, db, sc):
        c = self._chat(db, sc)
        m1 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        z_owner, _ = azienda(db)
        estranei = [sc.x_owner, c.m_y2, z_owner, new_user(db), str(uuid.uuid4()), None]
        assert claim(db, c.conv, c.y, [*estranei, c.y_owner, c.y_owner], m1) == {c.y_owner}
        # Nessuna riga di lettura per gli estranei.
        utenti = {str(r[0]) for r in db.execute(
            "select user_id from public.partner_conversazione_letture where "
            "conversazione_id = %s", (c.conv,)).fetchall()}
        assert utenti == {sc.x_owner, c.y_owner}
        assert claim(db, c.conv, c.y, estranei, m1) == set()
        assert claim(db, c.conv, c.y, [], m1) == set()
        assert claim(db, c.conv, c.y, None, m1) == set()

    def test_claim_solo_sui_messaggi_dell_altra_azienda(self, db, sc):
        c = self._chat(db, sc)
        m_y = scrivi(db, c.y_owner, c.y, c.conv)["messaggio"]["id"]
        # I messaggi di Y non generano email agli utenti di Y.
        assert claim(db, c.conv, c.y, [c.y_owner, c.m_y], m_y) == set()
        m_x = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        # p_ultimo_id oltre l'ultimo: vale l'ultimo messaggio di X.
        assert claim(db, c.conv, c.y, [c.m_y], m_x + 100) == {c.m_y}
        assert lettura(db, c.conv, c.m_y)["email_fino_a_id"] == m_x
        # Un id precedente a ogni messaggio di X: nessun claim.
        assert claim(db, c.conv, c.y, [c.y_owner], m_y) == set()

    def test_claim_conversazione_estranea(self, db, sc):
        c = self._chat(db, sc)
        z_owner, z = azienda(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            claim(db, c.conv, z, [z_owner], 1)
        assert detail_of(exc) == "conversazione_non_trovata"

    def test_riepilogo_non_letti(self, db, sc):
        c = self._chat(db, sc)
        _, altra = accettata_altra(db, sc)
        m1 = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        scrivi(db, sc.x_owner, sc.x, c.conv)
        scrivi(db, c.y_owner, c.y, c.conv)  # propri: non contano
        nascosto = scrivi(db, sc.x_owner, sc.x, c.conv)["messaggio"]["id"]
        db.execute("update public.partner_messaggi set nascosto_moderazione_at = now(), "
                   "nascosto_da = gen_random_uuid() where id = %s", (nascosto,))
        assert riepilogo(db, c.m_y, c.y) == [(c.conv, 2)]
        segna_letto(db, c.conv, c.m_y, c.y, m1)
        assert riepilogo(db, c.m_y, c.y) == [(c.conv, 1)]
        # Il titolare ha letto fino al proprio messaggio (successivo ai due di X).
        assert riepilogo(db, c.y_owner, c.y) == [(c.conv, 0)]
        # X: il suo ultimo invio (il messaggio poi oscurato) segna letto fin lì.
        assert dict(riepilogo(db, sc.x_owner, sc.x))[c.conv] == 0
        scrivi(db, c.y_owner, c.y, c.conv)
        assert dict(riepilogo(db, sc.x_owner, sc.x))[c.conv] == 1
        # X: due conversazioni, ordinate per ultimo messaggio.
        scrivi(db, sc.x_owner, sc.x, altra)
        assert [r[0] for r in riepilogo(db, sc.x_owner, sc.x)] == [altra, c.conv]
        # Estranei: nessuna riga.
        assert riepilogo(db, c.m_y2, c.y) == []
        assert riepilogo(db, sc.x_owner, c.y) == []


class TestChiudiConversazione:
    def test_la_chiude_solo_il_creatore(self, db, sc):
        _, conv_id = accettata(db, sc)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi_conv(db, sc.y_owner, sc.y, conv_id)
        assert detail_of(exc) == "conversazione_non_trovata"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi_conv(db, sc.x_owner, sc.x, conv_id, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        out = chiudi_conv(db, sc.x_owner, sc.x, conv_id)
        c = conv(db, conv_id)
        assert (c["stato"], str(c["chiusa_da_user_id"])) == ("chiusa", sc.x_owner)
        assert c["chiusa_at"] is not None and out["stato"] == "chiusa"
        (a,) = audit(db, "partenariato.conversazione_chiusa")
        assert a["payload"]["conversazione_id"] == conv_id
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi_conv(db, sc.x_owner, sc.x, conv_id)
        assert detail_of(exc) == "conversazione_chiusa"

    def test_azienda_non_viva(self, db, sc):
        _, conv_id = accettata(db, sc)
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (sc.x,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi_conv(db, sc.x_owner, sc.x, conv_id)
        assert detail_of(exc) == "azienda_non_disponibile"


# ------------------------------------------------------------------ snapshot


class TestSnapshot:
    def test_candidature_usate_con_la_stessa_formula(self, db, sc):
        s = snapshot(db, sc.y_owner)
        assert (s["candidature_mese"]["limite"], s["candidature_mese"]["usate"],
                s["candidature_mese"]["residuo"]) == (5, 0, 5)
        k = candidatura_pendente(db, sc)
        c2, p2 = call_con_posizione(db, sc.x)
        ritira(db, sc.y_owner, sc.y, candida(db, sc.y_owner, sc.y, c2, p2)["candidatura"]["id"])
        c3, _ = call_con_posizione(db, sc.x)
        invita(db, sc.x_owner, sc.x, c3, sc.y)  # gli inviti non contano
        s = snapshot(db, sc.y_owner)
        assert (s["candidature_mese"]["usate"], s["candidature_mese"]["residuo"]) == (2, 3)
        assert s["candidature_mese"]["usate"] == usate(db, sc.y_owner)
        # Mese scorso: non conta.
        db.execute("update public.partner_candidature set created_at = now() - interval "
                   "'40 days' where id = %s", (k,))
        assert snapshot(db, sc.y_owner)["candidature_mese"]["usate"] == 1
        # Forma e call attive invariate rispetto alla 0037.
        inizio, fine = db.execute(
            "select date_trunc('month', now() at time zone 'Europe/Rome')::date, "
            "(date_trunc('month', now() at time zone 'Europe/Rome') + interval '1 month' "
            "- interval '1 day')::date").fetchone()
        assert s["candidature_mese"]["periodo_inizio"] == inizio.isoformat()
        assert s["candidature_mese"]["periodo_fine"] == fine.isoformat()
        assert s["call_attive"] == {"limite": 1, "usate": 0, "residuo": 1}
        assert set(s) == {"call_attive", "candidature_mese"}

    def test_residuo_mai_negativo_e_illimitato(self, db, sc):
        for _ in range(2):
            c, p = call_con_posizione(db, sc.x)
            candida(db, sc.y_owner, sc.y, c, p)
        limite_candidature(db, "smart", 1)
        s = snapshot(db, sc.y_owner)["candidature_mese"]
        assert (s["limite"], s["usate"], s["residuo"]) == (1, 2, 0)
        limite_candidature(db, "smart", None)
        s = snapshot(db, sc.y_owner)["candidature_mese"]
        assert (s["limite"], s["usate"], s["residuo"]) == (None, 2, None)

    def test_pool_dell_owner(self, db, sc):
        owner = new_user(db, "advisor")
        _, a = azienda(db, owner)
        _, b = azienda(db, owner)
        candida(db, owner, a, sc.call, sc.pos)
        c, p = call_con_posizione(db, sc.x)
        candida(db, owner, b, c, p)
        assert snapshot(db, owner)["candidature_mese"]["usate"] == 2
        assert snapshot(db, sc.y_owner)["candidature_mese"]["usate"] == 0

    def test_senza_abbonamento(self, db):
        s = snapshot(db, str(uuid.uuid4()))
        assert (s["candidature_mese"]["limite"], s["candidature_mese"]["usate"],
                s["candidature_mese"]["residuo"]) == (0, 0, 0)


# ------------------------------------------------------------------ cascade


class TestCascade:
    def _popola(self, db, sc):
        k, conv_id = accettata(db, sc)
        scrivi(db, sc.y_owner, sc.y, conv_id)
        segna_letto(db, conv_id, sc.x_owner, sc.x, None)
        z_owner, z = azienda(db)
        k_z = candida(db, z_owner, z, sc.call, sc.pos)["candidatura"]["id"]
        return k, conv_id, k_z

    def test_hard_delete_dell_owner_di_y(self, db, sc):
        k, conv_id, k_z = self._popola(db, sc)
        n_audit = conta(db, "audit_log")
        db.execute("delete from auth.users where id = %s", (sc.y_owner,))
        assert cand(db, k) is None and conv(db, conv_id) is None
        assert conta(db, "partner_messaggi") == 0
        assert conta(db, "partner_conversazione_letture") == 0
        assert cand(db, k_z)["stato"] == "inviata"
        assert conta(db, "audit_log") >= n_audit  # l'audit sopravvive
        assert audit(db, "partenariato.candidatura_accettata") != []

    def test_hard_delete_dell_azienda_creatrice(self, db, sc):
        self._popola(db, sc)
        db.execute("delete from public.company_profiles where id = %s", (sc.x,))
        for tabella in TABELLE_NUOVE:
            assert conta(db, tabella) == 0, tabella

    def test_delete_della_call(self, db, sc):
        self._popola(db, sc)
        altra, pa = call_con_posizione(db, sc.x)
        k_altra = candida(db, sc.y_owner, sc.y, altra, pa)["candidatura"]["id"]
        db.execute("delete from public.partner_calls where id = %s", (sc.call,))
        assert [str(r[0]) for r in db.execute(
            "select id from public.partner_candidature").fetchall()] == [k_altra]
        assert conta(db, "partner_conversazioni") == 0 and conta(db, "partner_messaggi") == 0

    def test_delete_di_un_utente_che_ha_letto(self, db, sc):
        self._popola(db, sc)
        owner = new_user(db, "advisor")
        _, a = azienda(db, owner)
        m = membro(db, owner, a)
        k2 = candida(db, owner, a, *call_con_posizione(db, sc.x))["candidatura"]["id"]
        conv2 = decidi(db, sc.x_owner, sc.x, k2)["conversazione_id"]
        scrivi(db, sc.x_owner, sc.x, conv2)
        segna_letto(db, conv2, m, a, None)
        db.execute("delete from auth.users where id = %s", (m,))
        assert lettura(db, conv2, m) is None
        assert conv(db, conv2) is not None and conta(db, "partner_messaggi") == 2


# --------------------------------------------------------------- concorrenza


class TestConcorrenza:
    @pytest.mark.parametrize("chiusura", ["rpc", "update_diretto"])
    def test_decisione_poi_chiusura_della_call_senza_deadlock(self, db, sc, chiusura):
        """Y accetta un invito mentre X chiude la call. La decisione prende la call
        (FOR SHARE) PRIMA della candidatura: con la candidatura bloccata da un terzo,
        la decisione attende tenendo la call e la chiusura attende la decisione (non
        il contrario). Con l'ordine inverso (candidatura → call) il trigger della
        chiusura (call → candidature) chiuderebbe un ciclo: 40P01."""
        inv = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        z_owner, z = azienda(db)
        k_z = candida(db, z_owner, z, sc.call, sc.pos)["candidatura"]["id"]
        dsn = db.info.dsn
        terzo = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        conn_c = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: decidi(conn_b, sc.y_owner, sc.y, inv))
        if chiusura == "rpc":
            tc, ec = in_thread(lambda: chiudi_call(conn_c, sc.x_owner, sc.x, sc.call))
        else:
            tc, ec = in_thread(lambda: conn_c.execute(
                "update public.partner_calls set stato = 'chiusa_annullata', "
                "chiusa_at = now(), motivo_chiusura = 'moderazione' where id = %s",
                (sc.call,)))
        try:
            terzo.execute("select 1 from public.partner_candidature where id = %s for update",
                          (inv,))
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            tc.start()
            in_attesa(monitor, conn_c.info.backend_pid, tc, ec)
            assert conn_b.info.backend_pid in bloccanti(monitor, conn_c.info.backend_pid)
            terzo.rollback()
            tb.join(timeout=10)
            tc.join(timeout=10)
        finally:
            chiudi_tutto(terzo, conn_b, conn_c, monitor)
            for t in (tb, tc):
                if t.is_alive():
                    t.join(timeout=10)
        assert "errore" not in eb, eb
        assert "errore" not in ec, ec
        assert cand(db, inv)["stato"] == "accettata"
        assert conv(db, eb["out"]["conversazione_id"])["stato"] == "aperta"
        assert cand(db, k_z)["stato"] == "scaduta"
        assert db.execute("select stato from public.partner_calls where id = %s",
                          (sc.call,)).fetchone()[0] in ("chiusa_completata", "chiusa_annullata")

    def test_chiusura_poi_decisione(self, db, sc):
        """La chiusura in corso tiene la call: la decisione attende e poi trova
        l'invito già scaduto dal trigger."""
        inv = invita(db, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
        dsn = db.info.dsn
        conn_c = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: decidi(conn_b, sc.y_owner, sc.y, inv))
        try:
            chiudi_call(conn_c, sc.x_owner, sc.x, sc.call, "annullata")  # non committata
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            assert conn_c.info.backend_pid in bloccanti(monitor, conn_b.info.backend_pid)
            conn_c.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_c, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "candidatura_gia_decisa"
        r = cand(db, inv)
        assert (r["stato"], r["motivo_chiusura"]) == ("scaduta", "call_chiusa")
        assert conta(db, "partner_conversazioni") == 0

    def test_candidatura_serializzata_con_la_chiusura(self, db, sc):
        """Una candidatura in corso tiene la call FOR SHARE: la chiusura attende e il
        suo trigger vede (e chiude) la candidatura appena committata."""
        conn_a = psycopg.connect(db.info.dsn)
        conn_c = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        tc, ec = in_thread(lambda: chiudi_call(conn_c, sc.x_owner, sc.x, sc.call))
        try:
            k = candida(conn_a, sc.y_owner, sc.y, sc.call, sc.pos)["candidatura"]["id"]
            tc.start()
            in_attesa(monitor, conn_c.info.backend_pid, tc, ec)
            conn_a.commit()
            tc.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_c, monitor)
            if tc.is_alive():
                tc.join(timeout=10)
        assert "errore" not in ec, ec
        assert (cand(db, k)["stato"], cand(db, k)["motivo_chiusura"]) == (
            "scaduta", "call_chiusa")

    def test_chiusura_in_corso_blocca_la_candidatura(self, db, sc):
        conn_c = psycopg.connect(db.info.dsn)
        conn_a = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        ta, ea = in_thread(lambda: candida(conn_a, sc.y_owner, sc.y, sc.call, sc.pos))
        try:
            chiudi_call(conn_c, sc.x_owner, sc.x, sc.call)
            ta.start()
            in_attesa(monitor, conn_a.info.backend_pid, ta, ea)
            conn_c.commit()
            ta.join(timeout=10)
        finally:
            chiudi_tutto(conn_c, conn_a, monitor)
            if ta.is_alive():
                ta.join(timeout=10)
        assert errore_detail(ea) == "call_non_attiva"

    def test_pool_dell_owner_serializzato(self, db, sc):
        """Due aziende dello stesso Advisor, limite 1: la seconda candidatura attende
        il lock dell'owner e poi trova il pool esaurito."""
        limite_candidature(db, "advisor", 1)
        owner = new_user(db, "advisor")
        _, a = azienda(db, owner)
        _, b = azienda(db, owner)
        c2, p2 = call_con_posizione(db, sc.x)
        conn_a = psycopg.connect(db.info.dsn)
        conn_b = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        tb, eb = in_thread(lambda: candida(conn_b, owner, b, c2, p2))
        try:
            candida(conn_a, owner, a, sc.call, sc.pos)  # non committata
            db.execute("set lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                candida(db, owner, b, c2, p2)
            db.execute("set lock_timeout = 0")
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            db.execute("set lock_timeout = 0")
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "candidature_esaurite"
        assert usate(db, owner) == 1

    def test_esclusivita_serializzata(self, db, sc):
        """Due accettazioni concorrenti di Y su due call esclusive dello stesso bando:
        la seconda attende il lock dell'esclusività e poi è rifiutata."""
        bando = next(_bandi)
        a, pa = call_con_posizione(db, sc.x, bando_id=bando, esclusivita=True)
        z_owner, z = azienda(db)
        c, pc = call_con_posizione(db, z, bando_id=bando, esclusivita=True)
        k1 = candida(db, sc.y_owner, sc.y, a, pa)["candidatura"]["id"]
        k2 = candida(db, sc.y_owner, sc.y, c, pc)["candidatura"]["id"]
        conn_a = psycopg.connect(db.info.dsn)
        conn_b = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        tb, eb = in_thread(lambda: decidi(conn_b, z_owner, z, k2))
        try:
            decidi(conn_a, sc.x_owner, sc.x, k1)  # non committata
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "esclusivita_violata"
        assert cand(db, k1)["stato"] == "accettata" and cand(db, k2)["stato"] == "inviata"

    def test_revoca_in_corso_blocca_l_invito(self, db, sc):
        conn_r = psycopg.connect(db.info.dsn)
        conn_i = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        ti, ei = in_thread(lambda: invita(conn_i, sc.x_owner, sc.x, sc.call, sc.y))
        try:
            consenso(conn_r, sc.y_owner, sc.y, "revoca")  # non committata
            ti.start()
            in_attesa(monitor, conn_i.info.backend_pid, ti, ei)
            conn_r.commit()
            ti.join(timeout=10)
        finally:
            chiudi_tutto(conn_r, conn_i, monitor)
            if ti.is_alive():
                ti.join(timeout=10)
        assert errore_detail(ei) == "partner_non_disponibile"
        assert conta(db, "partner_candidature") == 0

    def test_invito_in_corso_poi_revoca(self, db, sc):
        """L'invito tiene il profilo di Y (FOR SHARE): la revoca attende e il suo
        trigger chiude l'invito appena committato."""
        conn_i = psycopg.connect(db.info.dsn)
        conn_r = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        tr, er = in_thread(lambda: consenso(conn_r, sc.y_owner, sc.y, "revoca"))
        try:
            k = invita(conn_i, sc.x_owner, sc.x, sc.call, sc.y)["candidatura"]["id"]
            tr.start()
            in_attesa(monitor, conn_r.info.backend_pid, tr, er)
            conn_i.commit()
            tr.join(timeout=10)
        finally:
            chiudi_tutto(conn_i, conn_r, monitor)
            if tr.is_alive():
                tr.join(timeout=10)
        assert "errore" not in er, er
        assert (cand(db, k)["stato"], cand(db, k)["motivo_chiusura"]) == ("scaduta", "opt_out")

    def test_accettazione_in_corso_poi_revoca(self, db, sc):
        """X accetta mentre Y revoca: la revoca attende il profilo; l'accettata resta
        e la conversazione anche."""
        k = candidatura_pendente(db, sc)
        conn_d = psycopg.connect(db.info.dsn)
        conn_r = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        tr, er = in_thread(lambda: consenso(conn_r, sc.y_owner, sc.y, "revoca"))
        try:
            decidi(conn_d, sc.x_owner, sc.x, k)  # non committata
            tr.start()
            in_attesa(monitor, conn_r.info.backend_pid, tr, er)
            conn_d.commit()
            tr.join(timeout=10)
        finally:
            chiudi_tutto(conn_d, conn_r, monitor)
            if tr.is_alive():
                tr.join(timeout=10)
        assert "errore" not in er, er
        assert cand(db, k)["stato"] == "accettata"
        assert conta(db, "partner_conversazioni") == 1

    def test_sospensione_in_corso_blocca_l_accettazione(self, db, sc):
        """La decisione prende il profilo partner di Y FOR SHARE: una sospensione in
        corso si attende e si rilegge (senza lock accetterebbe un profilo sospeso)."""
        k = candidatura_pendente(db, sc)
        conn_s = psycopg.connect(db.info.dsn)
        conn_d = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        td, ed = in_thread(lambda: decidi(conn_d, sc.x_owner, sc.x, k))
        try:
            conn_s.execute("select set_config('app.partner_consenso', 'on', true)")
            conn_s.execute("update public.company_partner_profiles set sospeso_at = now(), "
                           "sospeso_motivo = 'moderazione', sospeso_da = gen_random_uuid() "
                           "where company_profile_id = %s", (sc.y,))
            td.start()
            in_attesa(monitor, conn_d.info.backend_pid, td, ed)
            conn_s.commit()
            td.join(timeout=10)
        finally:
            chiudi_tutto(conn_s, conn_d, monitor)
            if td.is_alive():
                td.join(timeout=10)
        assert errore_detail(ed) == "controparte_non_disponibile"
        assert cand(db, k)["stato"] == "inviata"

    def test_posizioni_serializzate_con_la_candidatura(self, db, sc):
        altra = posizione(db, sc.call, "Seconda posizione")
        conn_a = psycopg.connect(db.info.dsn)
        conn_p = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        tp, ep = in_thread(lambda: sostituisci_posizioni(
            conn_p, sc.x_owner, sc.x, sc.call, [{"id": altra, "titolo": "Seconda posizione"}]))
        try:
            candida(conn_a, sc.y_owner, sc.y, sc.call, sc.pos)  # non committata
            tp.start()
            in_attesa(monitor, conn_p.info.backend_pid, tp, ep)
            conn_a.commit()
            tp.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_p, monitor)
            if tp.is_alive():
                tp.join(timeout=10)
        assert errore_detail(ep) == "posizione_con_candidature"

    def test_messaggi_concorrenti_con_la_stessa_chiave(self, db, sc):
        _, conv_id = accettata(db, sc)
        client = str(uuid.uuid4())
        conn_a = psycopg.connect(db.info.dsn)
        conn_b = psycopg.connect(db.info.dsn, autocommit=True)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        tb, eb = in_thread(lambda: scrivi(conn_b, sc.y_owner, sc.y, conv_id, client=client))
        try:
            primo = scrivi(conn_a, sc.y_owner, sc.y, conv_id, client=client)
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert "errore" not in eb, eb
        assert eb["out"]["duplicato"] is True
        assert eb["out"]["messaggio"]["id"] == primo["messaggio"]["id"]
        assert conta(db, "partner_messaggi") == 1


# ---------------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0039, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0039, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")
ESEGUIBILE = "\n".join(r for r in SQL_0039.splitlines() if not r.lstrip().startswith("--"))


class TestSicurezza0039:
    def test_inventario_del_file(self):
        # Se la migration crea altro, i test sotto devono coprirlo.
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == set(FIRME)
        assert not re.search(r"^create (function|table(?! public\.))", ESEGUIBILE, re.M)

    def test_additiva(self):
        """Nessun oggetto eliminato: l'unico drop è il CHECK di oggetto_tipo (allargato);
        le uniche funzioni esistenti ridefinite sono le due previste dal contratto
        (fn_partner_consenso NON si ridefinisce: la revoca passa dal trigger)."""
        drop = re.findall(r"^\s*drop\b.*$", ESEGUIBILE, re.M | re.I)
        assert [d.strip() for d in drop] == ["drop constraint ps_oggetto_tipo_check,"]
        assert not re.search(r"^\s*(alter function|create or replace view)\b",
                             ESEGUIBILE, re.M | re.I)
        alterate = set(re.findall(r"^alter table public\.(\w+)", ESEGUIBILE, re.M))
        assert alterate == TABELLE_NUOVE | {"partner_segnalazioni"}
        assert "fn_partner_consenso" not in FUNZIONI_NEL_FILE
        assert FUNZIONI_NEL_FILE - FUNZIONI_NUOVE == RIDEFINITE
        assert set(re.findall(r"^create trigger \w+\n\s+(?:before|after) [\w ]+? on "
                              r"public\.(\w+)", ESEGUIBILE, re.M)) == (
            TABELLE_NUOVE | {"partner_calls", "partner_consents"})

    def test_ridefinite_con_la_stessa_firma(self, db):
        for nome in RIDEFINITE:
            firme = [r[0] for r in db.execute(
                "select p.oid::regprocedure::text from pg_proc p join pg_namespace n "
                "on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = %s",
                (nome,)).fetchall()]
            assert firme == [FIRME[nome]]
            assert re.search(rf"^create or replace function public\.{nome}\(", SQL_0039, re.M)

    def test_trigger(self, db):
        trigger = {(r[0], r[1]) for r in db.execute(
            "select tgname, tgrelid::regclass::text from pg_trigger where not tgisinternal "
            "and tgname in ('trg_partner_candidature_updated_at', "
            "'trg_partner_conversazioni_updated_at', 'trg_partner_messaggi_immutabili', "
            "'trg_partner_conversazione_letture_updated_at', "
            "'trg_partner_calls_chiudi_candidature', 'trg_partner_consents_chiudi_pendenti')"
        ).fetchall()}
        assert trigger == {
            ("trg_partner_candidature_updated_at", "partner_candidature"),
            ("trg_partner_conversazioni_updated_at", "partner_conversazioni"),
            ("trg_partner_messaggi_immutabili", "partner_messaggi"),
            ("trg_partner_conversazione_letture_updated_at", "partner_conversazione_letture"),
            ("trg_partner_calls_chiudi_candidature", "partner_calls"),
            ("trg_partner_consents_chiudi_pendenti", "partner_consents"),
        }
        # Nessun trigger nuovo su partner_call_posizioni: la ridefinizione basta.
        assert {r[0] for r in db.execute(
            "select tgname from pg_trigger where not tgisinternal "
            "and tgrelid = 'public.partner_call_posizioni'::regclass").fetchall()} == {
            "trg_pcp_updated_at"}
        # Nessun trigger su DELETE dei messaggi (bloccherebbe la cascade).
        assert db.execute(
            "select count(*) from pg_trigger where not tgisinternal "
            "and tgrelid = 'public.partner_messaggi'::regclass and (tgtype & 8) <> 0"
        ).fetchone()[0] == 0

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

    @pytest.mark.parametrize("tabella", sorted(TABELLE_NUOVE | {"partner_segnalazioni"}))
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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0039, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0039, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration (nuove e ridefinite) e ogni
        fn_partner_% presente nel DB è SECURITY DEFINER con search_path fissato, non
        eseguibile dai client (PUBLIC compreso) ed esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname like 'fn\_partner\_%%'"""
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
            argomenti = firma[len(nome):]
            assert re.search(
                rf"^revoke execute on function public\.{nome}\([^)]*\)\s+"
                r"from public, anon, authenticated;",
                SQL_0039, re.M,
            ), nome
            assert argomenti

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_partner_invia_candidatura('{}'::jsonb)",
        "select public.fn_partner_invita('{}'::jsonb)",
        "select public.fn_partner_decidi(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), 'accetta', null, false)",
        "select public.fn_partner_ritira(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid())",
        "select public.fn_partner_scadi_inviti(10)",
        "select public.fn_partner_scadi_per_opt_out(gen_random_uuid())",
        "select public.fn_partner_invia_messaggio(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid(), 'ciao', gen_random_uuid())",
        "select public.fn_partner_segna_letto(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), 1)",
        "select public.fn_partner_claim_email_chat(gen_random_uuid(), gen_random_uuid(), "
        "array[gen_random_uuid()], 1)",
        "select * from public.fn_partner_conversazioni_riepilogo(gen_random_uuid(), "
        "gen_random_uuid())",
        "select public.fn_partner_chiudi_conversazione(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), gen_random_uuid())",
        "select public.fn_partner_candidature_usate(gen_random_uuid())",
        "select public.fn_partenariati_snapshot(gen_random_uuid())",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, ruolo, chiamata):
        """Prova diretta: con i ruoli esposti la chiamata fallisce."""
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_esegue_il_flusso(self, db, sc):
        """Come il service_role: un grant esplicito sulle RPC basta (la revoca è ai
        soli ruoli esposti) e le funzioni scrivono senza privilegi di tabella."""
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        rpc = ("fn_partner_invia_candidatura", "fn_partner_decidi", "fn_partner_invia_messaggio",
               "fn_partner_segna_letto", "fn_partner_claim_email_chat")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            for nome in rpc:
                db.execute(f"grant execute on function public.{FIRME[nome]} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                k = candidatura_pendente(db, sc)
                conv_id = decidi(db, sc.x_owner, sc.x, k)["conversazione_id"]
                m = scrivi(db, sc.x_owner, sc.x, conv_id)["messaggio"]["id"]
                assert claim(db, conv_id, sc.y, [sc.y_owner], m) == {sc.y_owner}
                assert segna_letto(db, conv_id, sc.y_owner, sc.y, m) == m
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        assert conta(db, "partner_messaggi") == 1

    def test_nessun_dato_in_chiaro_nelle_colonne(self, db):
        """Le tabelle nuove non hanno colonne per P.IVA, CF, email o nomi (T8)."""
        colonne = {r[0] for r in db.execute(
            "select column_name::text from information_schema.columns "
            "where table_schema = 'public' and table_name::text = any (%s)",
            (sorted(TABELLE_NUOVE),)).fetchall()}
        assert len(colonne) > 20
        for vietata in ("partita_iva", "piva", "codice_fiscale", "cf", "email", "nome",
                        "ragione_sociale", "denominazione", "codice_pubblico", "punteggio"):
            assert vietata not in colonne, vietata
