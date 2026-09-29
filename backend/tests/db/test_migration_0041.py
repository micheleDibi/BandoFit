"""Test funzionali della migration 0041 (consulto dalla call, moderazione DSA,
admin e metriche, identità verificata dall'admin: WP9).

Coprono: la colonna consultation_requests.partner_call_id (FK NO ACTION: la
call con un consulto non si cancella, la cascade dell'azienda passa), i due
indici «una richiesta aperta» (coesistenza di consulto AI-check e da call,
doppio consulto sulla stessa call), fn_create_consultation_request ridefinita
(call di un'altra azienda o di un altro owner rifiutata, consumo atomico,
payload del backend attuale invariato); le colonne di decisione e ricorso
delle segnalazioni; presa in carico, decisione motivata con effetto atomico e
coerente con l'oggetto, doppia decisione, ricorso (uno, entro 6 mesi, di chi
ne ha interesse) e sua decisione (riformata annulla o applica la
restrizione), sospensione e ripristino diretti (call scaduta nel frattempo →
scaduta), uscita di un membro da una call sospesa; il registro append-only e
lo stato protetto dell'identità, le RPC dell'identità, la revoca automatica
sui quattro eventi, l'identità forte e le ridefinizioni (rappresentante,
consenso nominativo, rivelazione simmetrica) senza regressioni; metriche su un
seed noto con valori esatti, costi per valuta, call da rivalidare;
concorrenza; RLS, privilegi, firme, copia fedele dei corpi ridefiniti e mappa
degli errori (test generici).
Ogni test riceve un database fresco clonato dal template.
"""

import base64
import difflib
import itertools
import re
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SQL_0041 = (MIGRAZIONI / "0041_partenariato_moderazione_consulto.sql").read_text(encoding="utf-8")

TABELLE_NUOVE = {"company_identita_verifiche", "company_identita_stato"}
FIRME = {
    # Nuove.
    "fn_identita_verifiche_readonly": "fn_identita_verifiche_readonly()",
    "fn_identita_stato_protetto": "fn_identita_stato_protetto()",
    "fn_identita_revoca_su_cambio_azienda": "fn_identita_revoca_su_cambio_azienda()",
    "fn_partner_admin_verifica": "fn_partner_admin_verifica(uuid)",
    "fn_partner_moderazione_autore": "fn_partner_moderazione_autore(text,text)",
    "fn_partner_moderazione_applica": "fn_partner_moderazione_applica(text,text,uuid,text)",
    "fn_partner_moderazione_annulla": "fn_partner_moderazione_annulla(text,text)",
    "fn_partner_moderazione_blocca": "fn_partner_moderazione_blocca(text,text)",
    "fn_partner_moderazione_inizio": "fn_partner_moderazione_inizio(text,text)",
    "fn_partenariato_identita_forte": "fn_partenariato_identita_forte(uuid)",
    "fn_identita_richiedi": "fn_identita_richiedi(uuid,uuid,uuid,text)",
    "fn_identita_decidi": "fn_identita_decidi(uuid,uuid,text,text,text)",
    "fn_identita_revoca": "fn_identita_revoca(uuid,uuid,text)",
    "fn_partner_segnalazione_prendi": "fn_partner_segnalazione_prendi(uuid,uuid)",
    "fn_partner_segnalazione_decidi": "fn_partner_segnalazione_decidi(uuid,uuid,text,text,text)",
    "fn_partner_segnalazione_ricorso": "fn_partner_segnalazione_ricorso(uuid,uuid,uuid,text)",
    "fn_partner_ricorso_decidi": "fn_partner_ricorso_decidi(uuid,uuid,text,text)",
    "fn_partner_admin_sospendi": "fn_partner_admin_sospendi(text,text,uuid,text)",
    "fn_partner_admin_ripristina": "fn_partner_admin_ripristina(text,text,uuid,text)",
    "fn_admin_metriche_partenariati": "fn_admin_metriche_partenariati(date,date)",
    "fn_admin_costi_partenariati": "fn_admin_costi_partenariati(date,date)",
    "fn_partner_call_validazioni_da_ricalcolare":
        "fn_partner_call_validazioni_da_ricalcolare(integer)",
    # Ridefinite con la STESSA firma.
    "fn_create_consultation_request": "fn_create_consultation_request(jsonb)",
    "fn_partenariato_rappresentante_ok": "fn_partenariato_rappresentante_ok(uuid,uuid)",
    "fn_partner_consenso":
        "fn_partner_consenso(uuid,uuid,uuid,text,text,text,boolean,boolean)",
    "fn_partner_decidi": "fn_partner_decidi(uuid,uuid,uuid,uuid,text,text,boolean,boolean)",
}
RIDEFINITE = {"fn_create_consultation_request", "fn_partenariato_rappresentante_ok",
              "fn_partner_consenso", "fn_partner_decidi"}
FUNZIONI_NUOVE = set(FIRME) - RIDEFINITE
# Ultima definizione precedente di ciascuna ridefinita (copia fedele).
ORIGINE = {
    "fn_create_consultation_request": "0028_addon_inventory_ledger.sql",
    "fn_partenariato_rappresentante_ok": "0037_call_partenariato.sql",
    "fn_partner_consenso": "0035_profili_partner.sql",
    "fn_partner_decidi": "0040_partenariato_consorzio.sql",
}

VERSIONE = "2026-10-bozza-1"
MSG = ("Siamo un organismo di ricerca con esperienza nella prototipazione rapida "
       "di componenti meccanici.")
MOTIVAZIONE = "La call contiene recapiti diretti in violazione dei Termini d'uso."
SOR = ("Fatti e circostanze: la descrizione pubblica contiene un numero di telefono. "
       "Fondamento: Termini d'uso (BOZZA). Mezzi automatizzati: no. Ricorso: entro 6 mesi.")
RICORSO = "Il numero era quello del centralino pubblico dell'ente, non personale."
CF_TITOLARE = "RSSMRA80A01H501U"
_TITOLARE = object()

_seq = itertools.count(1)
_bandi = itertools.count(41000)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


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


def new_admin(db, *, attivo: bool = True) -> str:
    uid = new_user(db)
    db.execute("update public.profiles set role = 'admin', is_active = %s where id = %s",
               (attivo, uid))
    return uid


def make_company(db, owner: str) -> str:
    i = next(_seq)
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, %s, %s) returning id",
        (owner, f"ACME {i} Srl", f"{i + 41000000:011d}"),
    ).fetchone()[0])


def importa(db, company: str, *, stato: str = "Attiva", sandbox: bool = False) -> None:
    """company_data come dopo un import IT-full (identità dal registro, T5)."""
    db.execute(
        "insert into public.company_data (company_profile_id, piva_fetched, sandbox, raw, "
        "denominazione, stato_impresa) "
        "select id, partita_iva, %s, '{}'::jsonb, ragione_sociale, %s "
        "from public.company_profiles where id = %s",
        (sandbox, stato, company),
    )


def consenso(db, owner: str, company: str, azione: str = "concedi", *,
             anonimo: bool | None = True) -> dict:
    return db.execute(
        "select public.fn_partner_consenso(%s::uuid, %s::uuid, %s::uuid, %s::text, %s::text, "
        "'pagina_azienda', %s::boolean, true)",
        (owner, company, owner, azione, VERSIONE, anonimo),
    ).fetchone()[0]


def azienda(db, owner: str | None = None, plan_slug: str | None = "smart", *,
            opt_in: bool = True) -> tuple[str, str]:
    """Titolare + azienda con T5; con opt_in il profilo partner (anonimo) è visibile."""
    owner = owner or new_user(db, plan_slug)
    company = make_company(db, owner)
    importa(db, company)
    if opt_in:
        consenso(db, owner, company)
    return owner, company


def inserisci_call(db, company: str, *, stato: str = "pubblicata", bando_id: int | None = None,
                   scadenza_giorni: int = 30, anonima: bool = True) -> str:
    """Call scritta direttamente (come dopo le RPC del WP5) nello stato richiesto."""
    owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                           (company,)).fetchone()[0])
    pubblicata = stato in ("pubblicata", "chiusa_completata", "scaduta", "sospesa_moderazione")
    motivo = {"chiusa_completata": "creatore_completata",
              "chiusa_annullata": "creatore_annullata",
              "scaduta": "scadenza_call"}.get(stato)
    bid = bando_id if bando_id is not None else next(_bandi)
    sospesa = stato == "sospesa_moderazione"
    return str(db.execute(
        """insert into public.partner_calls
             (company_profile_id, family_parent_id, creato_da, bando_id, bando_slug,
              bando_titolo, ruolo_creatore, titolo, descrizione_pubblica, scadenza_call,
              regole_partenariato, regole_confermate_at, stato, pubblicata_at, chiusa_at,
              motivo_chiusura, sospesa_at, stato_prima_sospensione, anonima)
           values (%s, %s, %s, %s, %s, 'Bando di prova', 'capofila',
                   'Cerchiamo un organismo di ricerca', 'Progetto di ricerca industriale.',
                   current_date + %s, '{}'::jsonb, now(), %s,
                   case when %s then now() end, case when %s::text is not null then now() end,
                   %s, case when %s then now() end, case when %s then 'pubblicata' end, %s)
           returning id""",
        (company, owner, owner, bid, f"bando-{bid}", scadenza_giorni, stato, pubblicata,
         motivo, motivo, sospesa, sospesa, anonima),
    ).fetchone()[0])


def posizione(db, call_id: str) -> str:
    return str(db.execute(
        "insert into public.partner_call_posizioni (call_id, titolo, ruolo, ordine) "
        "values (%s, 'Organismo di ricerca', 'partner', 0) returning id", (call_id,),
    ).fetchone()[0])


def requisito(db, call_id: str) -> str:
    return str(db.execute(
        "insert into public.partner_call_requisiti (call_id, origine, etichetta, testo, cercato) "
        "values (%s, 'manuale', 'A', 'Requisito di prova', true) returning id", (call_id,),
    ).fetchone()[0])


def candida(db, owner, company, call_id, pos) -> str:
    payload = {
        "owner_id": owner, "company_id": company, "attore_id": owner, "call_id": call_id,
        "posizione_id": pos, "messaggio": MSG, "requisiti_dichiarati": [],
        "valutazione": {"requisiti_coperti": ["A"]}, "pseudonimo": pseudo(),
    }
    return db.execute("select public.fn_partner_invia_candidatura(%s::jsonb)",
                      (Jsonb(payload),)).fetchone()[0]["candidatura"]["id"]


def decidi(db, owner, company, cand_id, *, rivela: bool = False) -> dict:
    """Chiamata per nome come PostgREST, con il default di p_richiedi_non_sandbox."""
    return db.execute(
        "select public.fn_partner_decidi(p_candidatura => %s::uuid, p_attore => %s::uuid, "
        "p_owner => %s::uuid, p_company => %s::uuid, p_decisione => 'accetta', "
        "p_motivo => null, p_rivela => %s::boolean)",
        (cand_id, owner, owner, company, rivela),
    ).fetchone()[0]


def messaggio(db, owner, company, conv_id, testo="Ecco il nostro numero: 02 1234567") -> int:
    return db.execute(
        "select public.fn_partner_invia_messaggio(p_conversazione => %s::uuid, "
        "p_attore => %s::uuid, p_owner => %s::uuid, p_company => %s::uuid, p_testo => %s, "
        "p_client_msg_id => %s::uuid)",
        (conv_id, owner, owner, company, testo, str(uuid.uuid4())),
    ).fetchone()[0]["messaggio"]["id"]


def audit(db, action) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.audit_log where action = %s order by id",
                           (action,)).fetchall()


def conta(db, tabella: str) -> int:
    return db.execute(f"select count(*) from public.{tabella}").fetchone()[0]


def riga(db, tabella: str, chiave: str, valore) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(f"select * from public.{tabella} where {chiave} = %s",
                           (valore,)).fetchone()


def call_row(db, call_id) -> dict:
    return riga(db, "partner_calls", "id", call_id)


def codice_pubblico(db, company) -> str:
    return str(db.execute("select codice_pubblico from public.company_partner_profiles "
                          "where company_profile_id = %s", (company,)).fetchone()[0])


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


def errore_detail(esito) -> str:
    exc = esito.get("errore")
    assert exc is not None, esito
    return exc.diag.message_detail or ""


# --------------------------------------------------------- consulto: helper


def make_addon(db, *, prezzo: str = "49.00", tipo_prezzo: str = "importo") -> int:
    return db.execute(
        "insert into public.addons (nome, slug, prezzo, tipo_prezzo, tipo_fruizione, is_active) "
        "values ('Consulto', %s, %s, %s, 'consumabile', true) returning id",
        (f"addon-{uuid.uuid4().hex[:8]}", prezzo, tipo_prezzo),
    ).fetchone()[0]


def credito(db, user: str, addon: int, quantita: int) -> None:
    db.execute("insert into public.user_addon_inventory (user_id, addon_id, quantita) "
               "values (%s, %s, %s)", (user, addon, quantita))


def saldo(db, user: str, addon: int) -> int:
    r = db.execute("select quantita from public.user_addon_inventory "
                   "where user_id = %s and addon_id = %s", (user, addon)).fetchone()
    return r[0] if r else 0


def payload_consulto(cliente, company, addon, bando_id, *, call=None, owner=None) -> dict:
    """Payload del backend attuale (0028); con call quello del consulto dalla call."""
    dati = {
        "cliente_id": cliente, "family_parent_id": owner or cliente,
        "company_profile_id": company, "ai_check_id": "",
        "esito": "ammissibile", "punteggio": "82",
        "bando_id": str(bando_id), "bando_slug": f"bando-{bando_id}",
        "bando_titolo": "Bando di prova", "addon_id": str(addon),
    }
    if call is not None:
        dati["partner_call_id"] = call
    return dati


def consulto(db, dati: dict) -> dict:
    return db.execute("select public.fn_create_consultation_request(%s::jsonb)",
                      (Jsonb(dati),)).fetchone()[0]


@pytest.fixture()
def cc(db):
    """Titolare X con un'azienda e una call pubblicata sul bando B, un addon a
    pagamento e 3 unità di credito."""
    owner, x = azienda(db, opt_in=False)
    bando = next(_bandi)
    call = inserisci_call(db, x, bando_id=bando)
    addon = make_addon(db)
    credito(db, owner, addon, 3)
    return SimpleNamespace(owner=owner, x=x, bando=bando, call=call, addon=addon)


# ---------------------------------------------------------- consulto dalla call


class TestConsultoDaCall:
    def test_colonna_e_fk_no_action(self, db):
        fk = db.execute(
            "select confrelid::regclass::text, confdeltype from pg_constraint "
            "where conname = 'consultation_requests_partner_call_id_fkey'").fetchone()
        assert fk == ("partner_calls", "a")

    def test_indici(self, db):
        indici = dict(db.execute(
            "select indexname, indexdef from pg_indexes where schemaname = 'public' "
            "and tablename = 'consultation_requests'").fetchall())
        assert "UNIQUE" in indici["consultation_requests_one_open"]
        assert "(family_parent_id, bando_id)" in indici["consultation_requests_one_open"]
        assert "partner_call_id IS NULL" in indici["consultation_requests_one_open"]
        assert "stato = 'nuova'" in indici["consultation_requests_one_open"]
        assert "UNIQUE" in indici["consultation_requests_one_open_call"]
        assert "(partner_call_id)" in indici["consultation_requests_one_open_call"]
        assert "partner_call_id IS NOT NULL" in indici["consultation_requests_one_open_call"]
        assert "stato = 'nuova'" in indici["consultation_requests_one_open_call"]
        assert "partner_call_id IS NOT NULL" in indici["consultation_requests_call_idx"]

    def test_salva_la_call_e_consuma_un_credito(self, db, cc):
        out = consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call))
        assert set(out) == {"request", "consumato", "quantita_residua"}
        assert out["consumato"] is True and out["quantita_residua"] == 2
        assert out["request"]["partner_call_id"] == cc.call
        assert saldo(db, cc.owner, cc.addon) == 2
        assert db.execute("select delta from public.addon_ledger where request_id = %s "
                          "and tipo = 'consume'", (out["request"]["id"],)).fetchone()[0] == -1

    def test_senza_ai_check_esito_facoltativo(self, db, cc):
        dati = payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call)
        dati.update({"esito": None, "punteggio": ""})
        req = consulto(db, dati)["request"]
        assert (req["ai_check_id"], req["esito"], req["punteggio"]) == (None, None, None)

    @pytest.mark.parametrize("prima", ["ai_check", "call"])
    def test_coesistenza_con_il_consulto_ai_check(self, db, cc, prima):
        ai = payload_consulto(cc.owner, cc.x, cc.addon, cc.bando)
        da_call = payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call)
        for dati in ((ai, da_call) if prima == "ai_check" else (da_call, ai)):
            consulto(db, dati)
        righe = db.execute("select partner_call_id is null, stato from "
                           "public.consultation_requests where bando_id = %s",
                           (cc.bando,)).fetchall()
        assert sorted(righe) == [(False, "nuova"), (True, "nuova")]
        assert saldo(db, cc.owner, cc.addon) == 1

    def test_due_consulti_sulla_stessa_call(self, db, cc):
        dati = payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call)
        consulto(db, dati)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, dati)
        assert detail_of(exc) == "request_gia_aperta"
        assert saldo(db, cc.owner, cc.addon) == 2  # solo il primo ha consumato
        assert conta(db, "consultation_requests") == 1

    def test_dopo_la_chiusura_se_ne_chiede_un_altro(self, db, cc):
        dati = payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call)
        primo = consulto(db, dati)["request"]["id"]
        db.execute("update public.consultation_requests set stato = 'annullata' where id = %s",
                   (primo,))
        assert consulto(db, dati)["request"]["partner_call_id"] == cc.call

    def test_due_call_dello_stesso_bando_di_due_aziende(self, db, cc):
        """L'indice delle call è per call, non per owner × bando."""
        y = make_company(db, cc.owner)
        importa(db, y)
        call_y = inserisci_call(db, y, bando_id=cc.bando)
        consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call))
        consulto(db, payload_consulto(cc.owner, y, cc.addon, cc.bando, call=call_y))
        assert conta(db, "consultation_requests") == 2

    def test_call_di_un_altra_azienda_dello_stesso_owner(self, db, cc):
        """Advisor con A e B: la call di B non si allega a un consulto di A."""
        a = make_company(db, cc.owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, payload_consulto(cc.owner, a, cc.addon, cc.bando, call=cc.call))
        assert detail_of(exc) == "call_non_trovata"
        assert conta(db, "consultation_requests") == 0
        assert saldo(db, cc.owner, cc.addon) == 3

    def test_call_di_un_altro_owner(self, db, cc):
        altro, sua = azienda(db, opt_in=False)
        credito(db, altro, cc.addon, 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, payload_consulto(altro, sua, cc.addon, cc.bando, call=cc.call))
        assert detail_of(exc) == "call_non_trovata"
        # Stessa azienda della call ma owner diverso nel payload.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, payload_consulto(altro, cc.x, cc.addon, cc.bando, call=cc.call))
        assert detail_of(exc) == "call_non_trovata"
        assert saldo(db, altro, cc.addon) == 1

    @pytest.mark.parametrize("call", ["00000000-0000-0000-0000-000000000000", "non-un-uuid"])
    def test_call_inesistente_o_malformata(self, db, cc, call):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=call))
        assert detail_of(exc) == "call_non_trovata"

    def test_senza_credito_e_atomico(self, db, cc):
        db.execute("update public.user_addon_inventory set quantita = 0 where user_id = %s",
                   (cc.owner,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call))
        assert detail_of(exc) == "addon_credit_esaurito"
        assert conta(db, "consultation_requests") == 0

    def test_addon_non_disponibile_prima_della_call(self, db, cc):
        db.execute("update public.addons set is_active = false where id = %s", (cc.addon,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call="x"))
        assert detail_of(exc) == "addon_not_available"

    def test_la_call_con_un_consulto_non_si_cancella(self, db, cc):
        consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute("delete from public.partner_calls where id = %s", (cc.call,))
        assert call_row(db, cc.call) is not None

    def test_hard_delete_dell_azienda_passa(self, db, cc):
        consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando, call=cc.call))
        db.execute("delete from public.company_profiles where id = %s", (cc.x,))
        assert conta(db, "partner_calls") == 0
        assert conta(db, "consultation_requests") == 0


class TestBackendAttuale:
    """Il backend attuale (prima del deploy del WP9) sul nuovo schema."""

    def test_payload_attuale_invariato(self, db, cc):
        dati = payload_consulto(cc.owner, cc.x, cc.addon, cc.bando)
        assert "partner_call_id" not in dati
        out = consulto(db, dati)
        assert set(out) == {"request", "consumato", "quantita_residua"}
        assert out["request"]["partner_call_id"] is None
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, dati)
        assert detail_of(exc) == "request_gia_aperta"
        # Un altro bando dello stesso owner, anche da un'altra azienda: consentito.
        altra = make_company(db, cc.owner)
        consulto(db, payload_consulto(cc.owner, altra, cc.addon, cc.bando + 1))
        # Stesso bando da un'altra azienda dello stesso owner: una per owner × bando.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consulto(db, payload_consulto(cc.owner, altra, cc.addon, cc.bando))
        assert detail_of(exc) == "request_gia_aperta"
        assert saldo(db, cc.owner, cc.addon) == 1

    def test_select_del_backend_attuale(self, db, cc):
        consulto(db, payload_consulto(cc.owner, cc.x, cc.addon, cc.bando))
        colonne = ("id,cliente_id,family_parent_id,company_profile_id,ai_check_id,esito,"
                   "punteggio,bando_id,bando_slug,bando_titolo,stato,assigned_progettista_id,"
                   "assigned_at,accepted_proposal_id,created_at")
        assert len(db.execute(f"select {colonne} from public.consultation_requests"
                              ).fetchall()) == 1

    def test_la_chiamata_del_backend_attuale_e_uguale(self, db, cc):
        """Stessa firma e stesso nome del parametro (PostgREST chiama per nome)."""
        out = db.execute("select public.fn_create_consultation_request(p_payload => %s::jsonb)",
                         (Jsonb(payload_consulto(cc.owner, cc.x, cc.addon, cc.bando)),)
                         ).fetchone()[0]
        assert out["consumato"] is True


# ---------------------------------------------------- moderazione: helper


def segnala(db, oggetto_tipo, oggetto_id, segnalante, *, company=None, stato="ricevuta") -> str:
    """Segnalazione come la inserisce il backend (partner_call_service.segnala)."""
    return str(db.execute(
        "insert into public.partner_segnalazioni (oggetto_tipo, oggetto_id, segnalante_user_id, "
        "segnalante_company_id, motivo, descrizione, buona_fede, contenuto_snapshot, stato) "
        "values (%s, %s, %s, %s, 'contatti_nel_testo', 'Ci sono recapiti nel testo.', true, "
        "%s, %s) returning id",
        (oggetto_tipo, str(oggetto_id), segnalante, company, Jsonb({"titolo": "x"}), stato),
    ).fetchone()[0])


def seg(db, sid) -> dict:
    return riga(db, "partner_segnalazioni", "id", sid)


def prendi(db, sid, admin) -> dict:
    return db.execute("select public.fn_partner_segnalazione_prendi(%s::uuid, %s::uuid)",
                      (sid, admin)).fetchone()[0]


def decidi_seg(db, sid, admin, decisione, motivazione=MOTIVAZIONE, sor=SOR) -> dict:
    return db.execute(
        "select public.fn_partner_segnalazione_decidi(p_id => %s::uuid, p_admin => %s::uuid, "
        "p_decisione => %s::text, p_motivazione => %s::text, p_sor_testo => %s::text)",
        (sid, admin, decisione, motivazione, sor),
    ).fetchone()[0]


def ricorso(db, sid, user, company, testo=RICORSO) -> dict:
    return db.execute(
        "select public.fn_partner_segnalazione_ricorso(p_id => %s::uuid, p_user => %s::uuid, "
        "p_company => %s::uuid, p_testo => %s::text)",
        (sid, user, company, testo),
    ).fetchone()[0]


def decidi_ricorso(db, sid, admin, esito, motivazione=MOTIVAZIONE) -> dict:
    return db.execute(
        "select public.fn_partner_ricorso_decidi(p_id => %s::uuid, p_admin => %s::uuid, "
        "p_esito => %s::text, p_motivazione => %s::text)",
        (sid, admin, esito, motivazione),
    ).fetchone()[0]


def sospendi(db, tipo, oggetto_id, admin, motivazione=MOTIVAZIONE) -> dict:
    return db.execute(
        "select public.fn_partner_admin_sospendi(p_oggetto_tipo => %s::text, "
        "p_oggetto_id => %s::text, p_admin => %s::uuid, p_motivazione => %s::text)",
        (tipo, str(oggetto_id), admin, motivazione),
    ).fetchone()[0]


def ripristina(db, tipo, oggetto_id, admin, motivazione=MOTIVAZIONE) -> dict:
    return db.execute(
        "select public.fn_partner_admin_ripristina(p_oggetto_tipo => %s::text, "
        "p_oggetto_id => %s::text, p_admin => %s::uuid, p_motivazione => %s::text)",
        (tipo, str(oggetto_id), admin, motivazione),
    ).fetchone()[0]


@pytest.fixture()
def mod(db):
    """X (creatore, profilo partner visibile) con una call pubblicata e una
    posizione; Y (visibile) si candida, X accetta e scrive un messaggio a Y;
    un admin attivo."""
    admin = new_admin(db)
    x_owner, x = azienda(db)
    y_owner, y = azienda(db)
    call = inserisci_call(db, x)
    pos = posizione(db, call)
    k = candida(db, y_owner, y, call, pos)
    out = decidi(db, x_owner, x, k)
    msg = messaggio(db, x_owner, x, out["conversazione_id"])
    return SimpleNamespace(admin=admin, x_owner=x_owner, x=x, y_owner=y_owner, y=y, call=call,
                           pos=pos, cand=k, conv=out["conversazione_id"], msg=msg,
                           membro=out["membro_id"], profilo_x=codice_pubblico(db, x))


# ----------------------------------------------------------- segnalazioni


class TestColonneSegnalazioni:
    def test_default_e_vocabolari(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        r = seg(db, sid)
        assert r["decisione"] is None and r["autore_company_profile_id"] is None
        for colonna, valore in (("decisione", "sospensione_totale"),
                                ("ricorso_esito", "annullata")):
            with pytest.raises(psycopg.errors.CheckViolation):
                db.execute(f"update public.partner_segnalazioni set {colonna} = %s "
                           "where id = %s", (valore, sid))

    def test_deciso_richiede_decisione_motivata(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_segnalazioni set stato = 'decisa' where id = %s",
                       (sid,))
        assert exc.value.diag.constraint_name == "ps_decisione_coerente"
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_segnalazioni set decisione = 'nessuna_azione', "
                       "motivazione = %s, deciso_da = %s, deciso_at = now() where id = %s",
                       (MOTIVAZIONE, mod.admin, sid))
        assert exc.value.diag.constraint_name == "ps_decisione_coerente"

    def test_restrizione_senza_statement(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_segnalazioni set stato = 'decisa', "
                       "decisione = 'call_sospesa', motivazione = %s, deciso_da = %s, "
                       "deciso_at = now() where id = %s", (MOTIVAZIONE, mod.admin, sid))
        assert exc.value.diag.constraint_name == "ps_sor_coerente"

    @pytest.mark.parametrize(("colonna", "lunghezza"), [
        ("motivazione", 19), ("motivazione", 2001), ("ricorso_testo", 19),
        ("ricorso_motivazione", 2001), ("sor_testo", 10001)])
    def test_lunghezze(self, db, mod, colonna, lunghezza):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(f"update public.partner_segnalazioni set {colonna} = %s where id = %s",
                       ("x" * lunghezza, sid))

    def test_ricorso_coerente(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_segnalazioni set stato = 'ricorso_presentato' "
                       "where id = %s", (sid,))
        assert exc.value.diag.constraint_name == "ps_ricorso_coerente"
        ricorso(db, sid, mod.x_owner, mod.x)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_segnalazioni set stato = 'ricorso_deciso' "
                       "where id = %s", (sid,))
        assert exc.value.diag.constraint_name == "ps_ricorso_deciso_coerente"


class TestPresaInCarico:
    def test_ricevuta_in_esame_con_autore(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        out = prendi(db, sid, mod.admin)
        assert out["modificato"] is True
        r = seg(db, sid)
        assert r["stato"] == "in_esame" and str(r["autore_company_profile_id"]) == mod.x
        (a,) = audit(db, "moderazione.presa_in_carico")
        assert str(a["actor_id"]) == mod.admin and a["payload"]["segnalazione_id"] == sid
        assert prendi(db, sid, mod.admin)["modificato"] is False
        assert len(audit(db, "moderazione.presa_in_carico")) == 1

    @pytest.mark.parametrize("tipo", ["call", "profilo", "messaggio"])
    def test_autore_per_ogni_oggetto(self, db, mod, tipo):
        oggetto, autore = {"call": (mod.call, mod.x), "profilo": (mod.profilo_x, mod.x),
                           "messaggio": (mod.msg, mod.x)}[tipo]
        sid = segnala(db, tipo, oggetto, mod.y_owner)
        prendi(db, sid, mod.admin)
        assert str(seg(db, sid)["autore_company_profile_id"]) == autore

    def test_autore_del_messaggio_e_il_mittente(self, db, mod):
        """Il messaggio di Y (partner, non creatore della conversazione) segnalato
        da X: l'autrice è Y."""
        msg_y = messaggio(db, mod.y_owner, mod.y, mod.conv, "Scrivici a info at esempio")
        sid = segnala(db, "messaggio", msg_y, mod.x_owner)
        prendi(db, sid, mod.admin)
        assert str(seg(db, sid)["autore_company_profile_id"]) == mod.y
        out = decidi_seg(db, sid, mod.admin, "contenuto_rimosso")
        assert (out["autore_company_id"], out["autore_owner_id"]) == (mod.y, mod.y_owner)

    def test_autore_dato_dal_backend_non_si_sovrascrive(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        db.execute("update public.partner_segnalazioni set autore_company_profile_id = %s "
                   "where id = %s", (mod.y, sid))
        prendi(db, sid, mod.admin)
        assert str(seg(db, sid)["autore_company_profile_id"]) == mod.y

    @pytest.mark.parametrize("oggetto", ["non-un-id", "00000000-0000-0000-0000-000000000000"])
    def test_oggetto_malformato_o_sparito(self, db, mod, oggetto):
        sid = segnala(db, "call", oggetto, mod.y_owner)
        prendi(db, sid, mod.admin)
        assert seg(db, sid)["autore_company_profile_id"] is None

    def test_decisa_non_si_riprende(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "nessuna_azione")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prendi(db, sid, mod.admin)
        assert detail_of(exc) == "segnalazione_gia_decisa"

    @pytest.mark.parametrize("chi", ["cliente", "admin_disattivato", "inesistente"])
    def test_solo_un_admin_attivo(self, db, mod, chi):
        attore = {"cliente": mod.x_owner, "admin_disattivato": new_admin(db, attivo=False),
                  "inesistente": str(uuid.uuid4())}[chi]
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prendi(db, sid, attore)
        assert detail_of(exc) == "admin_non_autorizzato"
        assert seg(db, sid)["stato"] == "ricevuta"

    def test_segnalazione_inesistente(self, db, mod):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prendi(db, str(uuid.uuid4()), mod.admin)
        assert detail_of(exc) == "segnalazione_non_trovata"


class TestDecisione:
    def test_call_sospesa_effetto_atomico(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        prendi(db, sid, mod.admin)
        out = decidi_seg(db, sid, mod.admin, "call_sospesa")
        c = call_row(db, mod.call)
        assert (c["stato"], c["stato_prima_sospensione"]) == ("sospesa_moderazione", "pubblicata")
        assert c["sospesa_at"] is not None and str(c["sospeso_da"]) == mod.admin
        assert c["sospeso_motivo"] == MOTIVAZIONE
        r = seg(db, sid)
        assert (r["stato"], r["decisione"], r["motivazione"], r["sor_testo"]) == (
            "decisa", "call_sospesa", MOTIVAZIONE, SOR)
        assert str(r["deciso_da"]) == mod.admin and r["deciso_at"] is not None
        assert out["effetto"] == "applicato"
        assert (out["autore_company_id"], out["autore_owner_id"]) == (mod.x, mod.x_owner)
        (a,) = audit(db, "moderazione.decisione")
        assert str(a["target_user_id"]) == mod.x_owner
        assert a["payload"]["decisione"] == "call_sospesa"
        # Mai il segnalante nell'audit che riguarda l'autore.
        assert mod.y_owner not in str(a["payload"]) and mod.y not in str(a["payload"])
        # La candidatura accettata e la conversazione restano.
        assert riga(db, "partner_candidature", "id", mod.cand)["stato"] == "accettata"

    def test_decisione_direttamente_da_ricevuta(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "nessuna_azione", sor=None)
        r = seg(db, sid)
        assert (r["stato"], r["sor_testo"], str(r["autore_company_profile_id"])) == (
            "decisa", None, mod.x)
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_contenuto_rimosso_oscura_il_messaggio(self, db, mod):
        sid = segnala(db, "messaggio", mod.msg, mod.y_owner)
        out = decidi_seg(db, sid, mod.admin, "contenuto_rimosso")
        m = riga(db, "partner_messaggi", "id", mod.msg)
        assert m["nascosto_moderazione_at"] is not None and str(m["nascosto_da"]) == mod.admin
        assert m["testo"].startswith("Ecco")  # immutabile: solo oscurato
        assert out["effetto"] == "applicato" and out["autore_company_id"] == mod.x

    def test_profilo_sospeso(self, db, mod):
        sid = segnala(db, "profilo", mod.profilo_x, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "profilo_sospeso")
        p = riga(db, "company_partner_profiles", "company_profile_id", mod.x)
        assert p["sospeso_at"] is not None and str(p["sospeso_da"]) == mod.admin
        assert p["visibile_come_partner"] is True  # la sospensione non revoca il consenso
        # Il profilo sospeso non riconcede il consenso (0035).
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_partner_consenso(%s, %s, %s, 'concedi', '2027-01', "
                       "'pagina_azienda', true, true)", (mod.x_owner, mod.x, mod.x_owner))
        assert detail_of(exc) == "profilo_sospeso"

    def test_la_guc_del_profilo_non_resta_accesa(self, db, mod):
        sid = segnala(db, "profilo", mod.profilo_x, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            with db.transaction():
                decidi_seg(db, sid, mod.admin, "profilo_sospeso")
                db.execute("update public.company_partner_profiles set sospeso_at = null "
                           "where company_profile_id = %s", (mod.x,))
        assert detail_of(exc) == "campo_protetto"

    @pytest.mark.parametrize(("tipo", "decisione"), [
        ("call", "contenuto_rimosso"), ("call", "profilo_sospeso"),
        ("messaggio", "call_sospesa"), ("messaggio", "profilo_sospeso"),
        ("profilo", "call_sospesa"), ("profilo", "contenuto_rimosso"),
        ("call", "sospensione"), ("call", None)])
    def test_coerenza_oggetto_decisione(self, db, mod, tipo, decisione):
        oggetto = {"call": mod.call, "messaggio": mod.msg, "profilo": mod.profilo_x}[tipo]
        sid = segnala(db, tipo, oggetto, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_seg(db, sid, mod.admin, decisione)
        assert detail_of(exc) == "decisione_non_valida"
        assert seg(db, sid)["stato"] == "ricevuta"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    @pytest.mark.parametrize("motivazione", [None, "   ", "troppo corta", "x" * 2001])
    def test_motivazione(self, db, mod, motivazione):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_seg(db, sid, mod.admin, "nessuna_azione", motivazione=motivazione)
        assert detail_of(exc) == "motivazione_non_valida"

    @pytest.mark.parametrize("sor", [None, "  "])
    def test_restrizione_senza_statement_of_reasons(self, db, mod, sor):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_seg(db, sid, mod.admin, "call_sospesa", sor=sor)
        assert detail_of(exc) == "statement_mancante"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_statement_troppo_lungo(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_seg(db, sid, mod.admin, "call_sospesa", sor="x" * 10001)
        assert detail_of(exc) == "parametri_non_validi"

    def test_doppia_decisione(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "nessuna_azione")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_seg(db, sid, mod.admin, "call_sospesa")
        assert detail_of(exc) == "segnalazione_gia_decisa"
        assert call_row(db, mod.call)["stato"] == "pubblicata"
        assert len(audit(db, "moderazione.decisione")) == 1

    @pytest.mark.parametrize("stato", ["chiusa_completata", "scaduta", "chiusa_annullata"])
    def test_call_non_sospendibile_niente_scritture(self, db, mod, stato):
        altra = inserisci_call(db, mod.x, stato=stato)
        sid = segnala(db, "call", altra, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_seg(db, sid, mod.admin, "call_sospesa")
        assert detail_of(exc) == "oggetto_non_sospendibile"
        assert seg(db, sid)["stato"] == "ricevuta"
        assert call_row(db, altra)["stato"] == stato
        # nessuna_azione resta possibile.
        decidi_seg(db, sid, mod.admin, "nessuna_azione")

    @pytest.mark.parametrize(("tipo", "oggetto"), [
        ("messaggio", "999999999"), ("messaggio", "12a"),
        ("call", "00000000-0000-0000-0000-000000000000"),
        ("profilo", "00000000-0000-0000-0000-000000000000")])
    def test_oggetto_sparito(self, db, mod, tipo, oggetto):
        decisione = {"call": "call_sospesa", "messaggio": "contenuto_rimosso",
                     "profilo": "profilo_sospeso"}[tipo]
        sid = segnala(db, tipo, oggetto, mod.y_owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_seg(db, sid, mod.admin, decisione)
        assert detail_of(exc) == "oggetto_non_trovato"
        out = decidi_seg(db, sid, mod.admin, "nessuna_azione")
        assert out["autore_company_id"] is None and out["effetto"] is None

    def test_errore_dopo_la_decisione_annulla_tutto(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.DivisionByZero):
            with db.transaction():
                decidi_seg(db, sid, mod.admin, "call_sospesa")
                db.execute("select 1 / 0")
        assert seg(db, sid)["stato"] == "ricevuta"
        assert call_row(db, mod.call)["stato"] == "pubblicata"
        assert audit(db, "moderazione.decisione") == []

    def test_gia_sospesa_da_un_altra_decisione(self, db, mod):
        z_owner, _ = azienda(db)
        s1 = segnala(db, "call", mod.call, mod.y_owner)
        s2 = segnala(db, "call", mod.call, z_owner)
        decidi_seg(db, s1, mod.admin, "call_sospesa")
        out = decidi_seg(db, s2, mod.admin, "call_sospesa")
        assert out["effetto"] == "gia_applicato"
        c = call_row(db, mod.call)
        assert (c["stato"], c["stato_prima_sospensione"]) == ("sospesa_moderazione", "pubblicata")

    def test_bozza_sospesa(self, db, mod):
        bozza = inserisci_call(db, mod.x, stato="bozza")
        sid = segnala(db, "call", bozza, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        c = call_row(db, bozza)
        assert (c["stato"], c["stato_prima_sospensione"], c["pubblicata_at"]) == (
            "sospesa_moderazione", "bozza", None)


# ---------------------------------------------------------------- ricorso


class TestRicorso:
    def test_autore_contro_una_restrizione(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        out = ricorso(db, sid, mod.x_owner, mod.x)
        assert out["ruolo"] == "autore"
        r = seg(db, sid)
        assert (r["stato"], r["ricorso_testo"], str(r["ricorso_da_user_id"])) == (
            "ricorso_presentato", RICORSO, mod.x_owner)
        (a,) = audit(db, "moderazione.ricorso_presentato")
        assert a["payload"] == {"segnalazione_id": sid, "ruolo": "autore"}

    def test_segnalante_contro_nessuna_azione(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "nessuna_azione")
        assert ricorso(db, sid, mod.y_owner, mod.y)["ruolo"] == "segnalante"

    def test_il_segnalante_ricorre_da_qualunque_azienda(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "nessuna_azione")
        assert ricorso(db, sid, mod.y_owner, None)["ruolo"] == "segnalante"

    def test_chi_non_ha_interesse_non_consuma_il_ricorso(self, db, mod):
        """L'autore contro nessuna_azione e il segnalante contro una restrizione
        non ricorrono: l'unico ricorso resta a chi ne ha interesse."""
        s1 = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, s1, mod.admin, "nessuna_azione")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ricorso(db, s1, mod.x_owner, mod.x)
        assert detail_of(exc) == "ricorso_non_ammesso"
        assert ricorso(db, s1, mod.y_owner, mod.y)["ruolo"] == "segnalante"
        s2 = segnala(db, "messaggio", mod.msg, mod.y_owner)
        decidi_seg(db, s2, mod.admin, "contenuto_rimosso")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ricorso(db, s2, mod.y_owner, mod.y)
        assert detail_of(exc) == "ricorso_non_ammesso"
        assert ricorso(db, s2, mod.x_owner, mod.x)["ruolo"] == "autore"

    def test_estranei(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        estraneo, sua = azienda(db)
        # Un estraneo, l'azienda autrice passata da un altro utente, un'altra
        # azienda del titolare autore: come una segnalazione inesistente.
        for user, company in ((estraneo, sua), (estraneo, mod.x),
                              (mod.x_owner, make_company(db, mod.x_owner))):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                ricorso(db, sid, user, company)
            assert detail_of(exc) == "segnalazione_non_trovata"
        assert seg(db, sid)["stato"] == "decisa"

    def test_una_sola_volta(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        ricorso(db, sid, mod.x_owner, mod.x)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ricorso(db, sid, mod.x_owner, mod.x)
        assert detail_of(exc) == "ricorso_non_ammesso"
        decidi_ricorso(db, sid, mod.admin, "confermata")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ricorso(db, sid, mod.x_owner, mod.x)
        assert detail_of(exc) == "ricorso_non_ammesso"

    @pytest.mark.parametrize(("intervallo", "ammesso"), [
        ("6 months - 1 hour", True), ("6 months + 1 hour", False)])
    def test_entro_6_mesi(self, db, mod, intervallo, ammesso):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        db.execute("update public.partner_segnalazioni set deciso_at = now() - %s::interval "
                   "where id = %s", (intervallo, sid))
        if ammesso:
            assert ricorso(db, sid, mod.x_owner, mod.x)["ruolo"] == "autore"
        else:
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                ricorso(db, sid, mod.x_owner, mod.x)
            assert detail_of(exc) == "ricorso_non_ammesso"

    @pytest.mark.parametrize("stato", ["ricevuta", "in_esame"])
    def test_prima_della_decisione(self, db, mod, stato):
        sid = segnala(db, "call", mod.call, mod.y_owner, stato=stato)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ricorso(db, sid, mod.y_owner, mod.y)
        assert detail_of(exc) == "ricorso_non_ammesso"

    @pytest.mark.parametrize("testo", [None, "breve", "x" * 2001])
    def test_testo(self, db, mod, testo):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            ricorso(db, sid, mod.x_owner, mod.x, testo=testo)
        assert detail_of(exc) == "ricorso_testo_non_valido"


class TestDecisioneDelRicorso:
    def _sospesa_con_ricorso(self, db, mod) -> str:
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        ricorso(db, sid, mod.x_owner, mod.x)
        return sid

    def test_confermata(self, db, mod):
        sid = self._sospesa_con_ricorso(db, mod)
        out = decidi_ricorso(db, sid, mod.admin, "confermata")
        assert out["effetto"] is None
        r = seg(db, sid)
        assert (r["stato"], r["ricorso_esito"], r["ricorso_motivazione"]) == (
            "ricorso_deciso", "confermata", MOTIVAZIONE)
        assert str(r["ricorso_deciso_da"]) == mod.admin and r["ricorso_deciso_at"] is not None
        assert call_row(db, mod.call)["stato"] == "sospesa_moderazione"
        (a,) = audit(db, "moderazione.ricorso_deciso")
        assert str(a["target_user_id"]) == mod.x_owner

    def test_riformata_ripristina_la_call(self, db, mod):
        sid = self._sospesa_con_ricorso(db, mod)
        out = decidi_ricorso(db, sid, mod.admin, "riformata")
        assert (out["effetto"], out["autore_owner_id"]) == ("annullato", mod.x_owner)
        c = call_row(db, mod.call)
        assert (c["stato"], c["stato_prima_sospensione"], c["sospesa_at"], c["sospeso_da"],
                c["sospeso_motivo"], c["chiusa_at"]) == ("pubblicata", None, None, None, None,
                                                         None)

    def test_riformata_su_call_scaduta_nel_frattempo(self, db, mod):
        z_owner, z = azienda(db)
        k = candida(db, z_owner, z, mod.call, mod.pos)
        sid = self._sospesa_con_ricorso(db, mod)
        db.execute("update public.partner_calls set scadenza_call = current_date - 3 "
                   "where id = %s", (mod.call,))
        assert decidi_ricorso(db, sid, mod.admin, "riformata")["effetto"] == "annullato"
        c = call_row(db, mod.call)
        assert (c["stato"], c["motivo_chiusura"]) == ("scaduta", "scadenza_call")
        assert c["chiusa_at"] is not None and c["sospesa_at"] is None
        # Il trigger della 0039 chiude la candidatura pendente.
        kz = riga(db, "partner_candidature", "id", k)
        assert (kz["stato"], kz["motivo_chiusura"]) == ("scaduta", "call_chiusa")

    def test_riformata_rende_visibile_il_messaggio(self, db, mod):
        sid = segnala(db, "messaggio", mod.msg, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "contenuto_rimosso")
        ricorso(db, sid, mod.x_owner, mod.x)
        decidi_ricorso(db, sid, mod.admin, "riformata")
        m = riga(db, "partner_messaggi", "id", mod.msg)
        assert (m["nascosto_moderazione_at"], m["nascosto_da"]) == (None, None)

    def test_riformata_riattiva_il_profilo(self, db, mod):
        sid = segnala(db, "profilo", mod.profilo_x, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "profilo_sospeso")
        ricorso(db, sid, mod.x_owner, mod.x)
        decidi_ricorso(db, sid, mod.admin, "riformata")
        p = riga(db, "company_partner_profiles", "company_profile_id", mod.x)
        assert (p["sospeso_at"], p["sospeso_motivo"], p["sospeso_da"]) == (None, None, None)

    def test_riformata_di_nessuna_azione_applica_la_restrizione(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "nessuna_azione")
        ricorso(db, sid, mod.y_owner, mod.y)
        out = decidi_ricorso(db, sid, mod.admin, "riformata")
        assert out["effetto"] == "applicato"
        assert call_row(db, mod.call)["stato"] == "sospesa_moderazione"

    def test_riformata_non_annulla_se_un_altra_decisione_regge(self, db, mod):
        z_owner, _ = azienda(db)
        s1 = self._sospesa_con_ricorso(db, mod)
        s2 = segnala(db, "call", mod.call, z_owner)
        decidi_seg(db, s2, mod.admin, "call_sospesa")
        assert decidi_ricorso(db, s1, mod.admin, "riformata")["effetto"] == "mantenuto"
        assert call_row(db, mod.call)["stato"] == "sospesa_moderazione"

    def test_riformata_annulla_se_l_altra_decisione_e_caduta(self, db, mod):
        z_owner, _ = azienda(db)
        s2 = segnala(db, "call", mod.call, z_owner)
        decidi_seg(db, s2, mod.admin, "call_sospesa")
        ricorso(db, s2, mod.x_owner, mod.x)
        decidi_ricorso(db, s2, mod.admin, "riformata")
        s1 = self._sospesa_con_ricorso(db, mod)
        assert decidi_ricorso(db, s1, mod.admin, "riformata")["effetto"] == "annullato"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_riformata_non_annulla_una_sospensione_d_ufficio(self, db, mod):
        """La call era già sospesa d'ufficio quando è arrivata la decisione
        (effetto gia_applicato): il ricorso accolto su quella decisione non
        toglie la sospensione d'ufficio, che nessuna segnalazione regge."""
        assert sospendi(db, "call", mod.call, mod.admin)["modificato"] is True
        sid = segnala(db, "call", mod.call, mod.y_owner)
        assert decidi_seg(db, sid, mod.admin, "call_sospesa")["effetto"] == "gia_applicato"
        ricorso(db, sid, mod.x_owner, mod.x)
        assert decidi_ricorso(db, sid, mod.admin, "riformata")["effetto"] == "mantenuto"
        c = call_row(db, mod.call)
        assert (c["stato"], c["stato_prima_sospensione"]) == ("sospesa_moderazione", "pubblicata")
        # Il ripristino diretto resta la via per toglierla.
        assert ripristina(db, "call", mod.call, mod.admin)["stato"] == "pubblicata"

    def test_riformata_dopo_un_ripristino_diretto(self, db, mod):
        """Una decisione la cui sospensione l'admin ha già tolto con un
        ripristino diretto non regge più la sospensione successiva: il
        ricorso accolto sulla nuova decisione la annulla."""
        z_owner, _ = azienda(db)
        s1 = segnala(db, "call", mod.call, mod.y_owner)
        assert decidi_seg(db, s1, mod.admin, "call_sospesa")["effetto"] == "applicato"
        assert ripristina(db, "call", mod.call, mod.admin)["modificato"] is True
        assert seg(db, s1)["effetto_revocato_at"] is not None
        s2 = segnala(db, "call", mod.call, z_owner)
        assert decidi_seg(db, s2, mod.admin, "call_sospesa")["effetto"] == "applicato"
        assert seg(db, s2)["effetto_revocato_at"] is None
        ricorso(db, s2, mod.x_owner, mod.x)
        assert decidi_ricorso(db, s2, mod.admin, "riformata")["effetto"] == "annullato"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_il_ripristino_diretto_revoca_solo_le_decisioni_valide(self, db, mod):
        z_owner, _ = azienda(db)
        valida = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, valida, mod.admin, "call_sospesa")
        nessuna = segnala(db, "call", mod.call, z_owner)
        decidi_seg(db, nessuna, mod.admin, "nessuna_azione")
        aperta = segnala(db, "call", mod.call, mod.x_owner)
        altra = segnala(db, "profilo", mod.profilo_x, mod.y_owner)
        decidi_seg(db, altra, mod.admin, "profilo_sospeso")
        ripristina(db, "call", mod.call, mod.admin)
        assert seg(db, valida)["effetto_revocato_at"] is not None
        for sid in (nessuna, aperta, altra):
            assert seg(db, sid)["effetto_revocato_at"] is None, sid
        # Senza restrizione da togliere nessuna scrittura.
        ripristina(db, "call", mod.call, mod.admin)
        assert seg(db, nessuna)["effetto_revocato_at"] is None

    def test_riformata_con_l_oggetto_gia_ripristinato(self, db, mod):
        sid = self._sospesa_con_ricorso(db, mod)
        ripristina(db, "call", mod.call, mod.admin)
        assert decidi_ricorso(db, sid, mod.admin, "riformata")["effetto"] == "non_sospeso"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_effetto_revocato_solo_su_una_decisione(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("update public.partner_segnalazioni set effetto_revocato_at = now() "
                       "where id = %s", (sid,))

    def test_senza_ricorso_da_decidere(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, sid, mod.admin, "call_sospesa")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_ricorso(db, sid, mod.admin, "riformata")
        assert detail_of(exc) == "ricorso_non_in_attesa"
        assert call_row(db, mod.call)["stato"] == "sospesa_moderazione"

    def test_parametri(self, db, mod):
        sid = self._sospesa_con_ricorso(db, mod)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_ricorso(db, sid, mod.admin, "accolto")
        assert detail_of(exc) == "parametri_non_validi"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_ricorso(db, sid, mod.admin, "riformata", motivazione="corta")
        assert detail_of(exc) == "motivazione_non_valida"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_ricorso(db, sid, mod.x_owner, "riformata")
        assert detail_of(exc) == "admin_non_autorizzato"
        assert seg(db, sid)["stato"] == "ricorso_presentato"


# ------------------------------------------------ sospensione diretta


class TestSospensioneDiretta:
    def test_call_sospesa_e_ripristinata(self, db, mod):
        out = sospendi(db, "call", mod.call, mod.admin)
        assert (out["esito"], out["modificato"], out["stato_precedente"]) == (
            "applicato", True, "pubblicata")
        (a,) = audit(db, "admin.partner_call_sospesa")
        assert str(a["target_user_id"]) == mod.x_owner
        assert a["payload"]["motivazione"] == MOTIVAZIONE
        assert sospendi(db, "call", mod.call, mod.admin)["modificato"] is False
        assert len(audit(db, "admin.partner_call_sospesa")) == 1
        out = ripristina(db, "call", mod.call, mod.admin)
        assert (out["esito"], out["stato"], out["modificato"]) == ("annullato", "pubblicata", True)
        assert call_row(db, mod.call)["stato"] == "pubblicata"
        assert len(audit(db, "admin.partner_call_ripristinata")) == 1
        assert ripristina(db, "call", mod.call, mod.admin)["modificato"] is False
        assert len(audit(db, "admin.partner_call_ripristinata")) == 1

    def test_ripristino_di_una_call_scaduta_nel_frattempo(self, db, mod):
        sospendi(db, "call", mod.call, mod.admin)
        db.execute("update public.partner_calls set scadenza_call = current_date - 1 "
                   "where id = %s", (mod.call,))
        assert ripristina(db, "call", mod.call, mod.admin)["stato"] == "scaduta"
        c = call_row(db, mod.call)
        assert (c["stato"], c["motivo_chiusura"], c["stato_prima_sospensione"]) == (
            "scaduta", "scadenza_call", None)

    def test_ripristino_della_scadenza_di_oggi(self, db, mod):
        sospendi(db, "call", mod.call, mod.admin)
        db.execute("update public.partner_calls set "
                   "scadenza_call = (now() at time zone 'Europe/Rome')::date where id = %s",
                   (mod.call,))
        assert ripristina(db, "call", mod.call, mod.admin)["stato"] == "pubblicata"

    def test_ripristino_di_una_bozza(self, db, mod):
        bozza = inserisci_call(db, mod.x, stato="bozza", scadenza_giorni=-5)
        sospendi(db, "call", bozza, mod.admin)
        assert ripristina(db, "call", bozza, mod.admin)["stato"] == "bozza"

    @pytest.mark.parametrize("stato", ["chiusa_completata", "scaduta", "chiusa_annullata"])
    def test_call_chiusa_non_sospendibile(self, db, mod, stato):
        altra = inserisci_call(db, mod.x, stato=stato)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            sospendi(db, "call", altra, mod.admin)
        assert detail_of(exc) == "oggetto_non_sospendibile"
        assert ripristina(db, "call", altra, mod.admin)["modificato"] is False

    def test_profilo(self, db, mod):
        assert sospendi(db, "profilo", mod.profilo_x, mod.admin)["modificato"] is True
        assert riga(db, "company_partner_profiles", "company_profile_id",
                    mod.x)["sospeso_at"] is not None
        assert len(audit(db, "moderazione.profilo_sospeso")) == 1
        assert ripristina(db, "profilo", mod.profilo_x, mod.admin)["modificato"] is True
        assert riga(db, "company_partner_profiles", "company_profile_id",
                    mod.x)["sospeso_at"] is None
        assert len(audit(db, "moderazione.profilo_ripristinato")) == 1

    def test_messaggio(self, db, mod):
        assert sospendi(db, "messaggio", mod.msg, mod.admin)["modificato"] is True
        assert len(audit(db, "moderazione.messaggio_oscurato")) == 1
        assert ripristina(db, "messaggio", mod.msg, mod.admin)["modificato"] is True
        assert riga(db, "partner_messaggi", "id", mod.msg)["nascosto_moderazione_at"] is None
        assert len(audit(db, "moderazione.messaggio_ripristinato")) == 1

    @pytest.mark.parametrize(("tipo", "oggetto"), [
        ("call", "00000000-0000-0000-0000-000000000000"), ("call", "abc"),
        ("profilo", "00000000-0000-0000-0000-000000000000"), ("messaggio", "123456789"),
        ("messaggio", "-1")])
    def test_oggetto_non_trovato(self, db, mod, tipo, oggetto):
        for funzione in (sospendi, ripristina):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                funzione(db, tipo, oggetto, mod.admin)
            assert detail_of(exc) == "oggetto_non_trovato"

    def test_parametri_e_admin(self, db, mod):
        for funzione in (sospendi, ripristina):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                funzione(db, "azienda", mod.x, mod.admin)
            assert detail_of(exc) == "parametri_non_validi"
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                funzione(db, "call", mod.call, mod.admin, motivazione="breve")
            assert detail_of(exc) == "motivazione_non_valida"
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                funzione(db, "call", mod.call, mod.x_owner)
            assert detail_of(exc) == "admin_non_autorizzato"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_un_membro_esce_da_una_call_sospesa(self, db, mod):
        """WP8 P14: la sospensione non trattiene i membri nel consorzio; il
        creatore invece non modifica il consorzio di una call sospesa."""
        sospendi(db, "call", mod.call, mod.admin)
        esci = ("select public.fn_partner_membro_esci(p_membro => %s::uuid, "
                "p_attore => %s::uuid, p_owner => %s::uuid, p_company => %s::uuid)")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(esci, (mod.membro, mod.x_owner, mod.x_owner, mod.x))
        assert detail_of(exc) == "call_non_modificabile"
        out = db.execute(esci, (mod.membro, mod.y_owner, mod.y_owner, mod.y)).fetchone()[0]
        assert (out["modificato"], out["origine"], out["membro"]["stato"]) == (
            True, "membro", "uscito")
        assert call_row(db, mod.call)["stato"] == "sospesa_moderazione"


# ------------------------------------------------ identità: helper


def richiedi(db, owner, company, *, attore=_TITOLARE, nota=None) -> dict:
    return db.execute(
        "select public.fn_identita_richiedi(p_owner => %s::uuid, p_company => %s::uuid, "
        "p_attore => %s::uuid, p_nota => %s::text)",
        (owner, company, owner if attore is _TITOLARE else attore, nota),
    ).fetchone()[0]


def decidi_identita(db, company, admin, esito="verificata", metodo="telefonata_sede",
                    nota=None) -> dict:
    return db.execute(
        "select public.fn_identita_decidi(p_company => %s::uuid, p_admin => %s::uuid, "
        "p_esito => %s::text, p_metodo => %s::text, p_nota => %s::text)",
        (company, admin, esito, metodo, nota),
    ).fetchone()[0]


def revoca_identita(db, company, admin, motivo="Documento risultato non valido") -> dict:
    return db.execute(
        "select public.fn_identita_revoca(p_company => %s::uuid, p_admin => %s::uuid, "
        "p_motivo => %s::text)", (company, admin, motivo),
    ).fetchone()[0]


def verifica(db, owner, company, admin) -> None:
    richiedi(db, owner, company)
    decidi_identita(db, company, admin)


def forte(db, company) -> bool:
    return db.execute("select public.fn_partenariato_identita_forte(%s)",
                      (company,)).fetchone()[0]


def stato_identita(db, company) -> dict | None:
    return riga(db, "company_identita_stato", "company_profile_id", company)


def registro_identita(db, company) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute("select * from public.company_identita_verifiche "
                           "where company_profile_id = %s order by id", (company,)).fetchall()


@pytest.fixture()
def idn(db):
    admin = new_admin(db)
    owner, company = azienda(db, opt_in=False)
    return SimpleNamespace(admin=admin, owner=owner, company=company)


# ---------------------------------------------------------------- identità


class TestTabelleIdentita:
    def test_registro_append_only(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        for sql in ("update public.company_identita_verifiche set nota = 'x'",
                    "delete from public.company_identita_verifiche",
                    "truncate public.company_identita_verifiche"):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                db.execute(sql)
            assert detail_of(exc) == "registro_append_only"
        assert len(registro_identita(db, idn.company)) == 1

    def test_vincoli_del_registro(self, db, idn):
        base = ("insert into public.company_identita_verifiche (company_profile_id, "
                "family_parent_id, azione, metodo, attore_user_id, origine) "
                "values (%s, %s, %s, %s, %s, %s)")
        for azione, metodo, attore, origine in (
            ("verificata", None, idn.admin, "admin"),        # metodo obbligatorio
            ("rifiutata", "pec", idn.admin, "admin"),        # metodo solo per verificata
            ("richiesta", None, None, "utente"),             # attore obbligatorio
            ("revocata", None, idn.admin, "sistema"),        # sistema senza attore
            ("verificata", "pec", idn.owner, "utente"),      # verifica solo dell'admin
            ("richiesta", None, idn.admin, "admin"),         # richiesta solo dell'utente
            ("verificata", "fax", idn.admin, "admin"),       # metodo sconosciuto
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                db.execute(base, (idn.company, idn.owner, azione, metodo, attore, origine))

    def test_stato_solo_dalle_rpc(self, db, idn):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("insert into public.company_identita_stato (company_profile_id, stato, "
                       "metodo, verificata_at, verificata_da) "
                       "values (%s, 'verificata', 'pec', now(), %s)", (idn.company, idn.admin))
        assert detail_of(exc) == "campo_protetto"
        richiedi(db, idn.owner, idn.company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("update public.company_identita_stato set stato = 'non_richiesta' "
                       "where company_profile_id = %s", (idn.company,))
        assert detail_of(exc) == "campo_protetto"
        assert stato_identita(db, idn.company)["stato"] == "richiesta"

    def test_la_guc_non_resta_accesa(self, db, idn):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            with db.transaction():
                richiedi(db, idn.owner, idn.company)
                db.execute("update public.company_identita_stato set stato = 'verificata', "
                           "metodo = 'pec', verificata_at = now(), verificata_da = %s",
                           (idn.admin,))
        assert detail_of(exc) == "campo_protetto"

    def test_vincoli_dello_stato(self, db, idn):
        verifica(db, idn.owner, idn.company, idn.admin)
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                db.execute("select set_config('app.identita_azienda', 'on', true)")
                db.execute("update public.company_identita_stato set metodo = null")
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                db.execute("select set_config('app.identita_azienda', 'on', true)")
                db.execute("update public.company_identita_stato set stato = 'richiesta', "
                           "metodo = null, verificata_at = null, verificata_da = null, "
                           "richiesta_at = null")

    def test_cascade_dell_azienda_e_registro_che_resta(self, db, idn):
        verifica(db, idn.owner, idn.company, idn.admin)
        db.execute("delete from public.company_profiles where id = %s", (idn.company,))
        assert stato_identita(db, idn.company) is None
        assert len(registro_identita(db, idn.company)) == 2


class TestRpcIdentita:
    def test_richiesta(self, db, idn):
        out = richiedi(db, idn.owner, idn.company, nota="  Chiamatemi in sede la mattina  ")
        assert out["modificato"] is True and out["family_parent_id"] == idn.owner
        s = stato_identita(db, idn.company)
        assert s["stato"] == "richiesta" and s["richiesta_at"] is not None
        (r,) = registro_identita(db, idn.company)
        assert (r["azione"], r["origine"], str(r["attore_user_id"]), r["nota"]) == (
            "richiesta", "utente", idn.owner, "Chiamatemi in sede la mattina")
        (a,) = audit(db, "identita.richiesta")
        assert a["payload"] == {"company_profile_id": idn.company}
        assert forte(db, idn.company) is False

    def test_una_richiesta_aperta_alla_volta(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            richiedi(db, idn.owner, idn.company)
        assert detail_of(exc) == "identita_richiesta_aperta"
        decidi_identita(db, idn.company, idn.admin)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            richiedi(db, idn.owner, idn.company)
        assert detail_of(exc) == "identita_gia_verificata"

    def test_dopo_un_rifiuto_si_richiede(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        decidi_identita(db, idn.company, idn.admin, "rifiutata", metodo="pec")
        s = stato_identita(db, idn.company)
        assert (s["stato"], s["metodo"], s["verificata_at"]) == ("rifiutata", None, None)
        assert richiedi(db, idn.owner, idn.company)["stato"]["stato"] == "richiesta"

    def test_solo_il_titolare(self, db, idn):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            richiedi(db, idn.owner, idn.company, attore=new_user(db))
        assert detail_of(exc) == "attore_non_titolare"
        altro = new_user(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            richiedi(db, altro, idn.company)
        assert detail_of(exc) == "company_not_found"

    @pytest.mark.parametrize("caso", ["senza_import", "piva_diversa", "cessata"])
    def test_richiede_t5(self, db, caso):
        owner = new_user(db)
        company = make_company(db, owner)
        if caso == "piva_diversa":
            importa(db, company)
            db.execute("update public.company_data set piva_fetched = '00000000001' "
                       "where company_profile_id = %s", (company,))
        elif caso == "cessata":
            importa(db, company, stato="Cessata")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            richiedi(db, owner, company)
        assert detail_of(exc) == "identita_non_verificata"
        assert stato_identita(db, company) is None

    @pytest.mark.parametrize("colonna", ["deleted_at", "archived_at"])
    def test_azienda_non_viva(self, db, idn, colonna):
        db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                   (idn.company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            richiedi(db, idn.owner, idn.company)
        assert detail_of(exc) == "company_not_found"

    def test_nota_troppo_lunga(self, db, idn):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            richiedi(db, idn.owner, idn.company, nota="x" * 501)
        assert detail_of(exc) == "parametri_non_validi"

    def test_verifica(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        out = decidi_identita(db, idn.company, idn.admin, metodo="documento_legale_rappresentante",
                              nota="Visura e documento controllati")
        assert out["family_parent_id"] == idn.owner
        s = stato_identita(db, idn.company)
        assert (s["stato"], s["metodo"], str(s["verificata_da"])) == (
            "verificata", "documento_legale_rappresentante", idn.admin)
        assert s["verificata_at"] is not None
        r = registro_identita(db, idn.company)[-1]
        assert (r["azione"], r["metodo"], r["origine"], str(r["attore_user_id"])) == (
            "verificata", "documento_legale_rappresentante", "admin", idn.admin)
        (a,) = audit(db, "identita.verificata")
        assert str(a["target_user_id"]) == idn.owner
        assert forte(db, idn.company) is True

    @pytest.mark.parametrize("metodo", [None, "fax"])
    def test_metodo_obbligatorio(self, db, idn, metodo):
        richiedi(db, idn.owner, idn.company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_identita(db, idn.company, idn.admin, metodo=metodo)
        assert detail_of(exc) == "metodo_obbligatorio"
        assert stato_identita(db, idn.company)["stato"] == "richiesta"

    def test_rifiuto_ignora_il_metodo(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        decidi_identita(db, idn.company, idn.admin, "rifiutata", metodo=None)
        assert registro_identita(db, idn.company)[-1]["metodo"] is None
        assert len(audit(db, "identita.rifiutata")) == 1

    def test_decisione_senza_richiesta(self, db, idn):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_identita(db, idn.company, idn.admin)
        assert detail_of(exc) == "identita_non_richiesta"
        verifica(db, idn.owner, idn.company, idn.admin)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_identita(db, idn.company, idn.admin, "rifiutata")
        assert detail_of(exc) == "identita_non_richiesta"

    def test_verifica_richiede_t5_ancora_valido(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        db.execute("update public.company_data set stato_impresa = 'Cessata' "
                   "where company_profile_id = %s", (idn.company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_identita(db, idn.company, idn.admin)
        assert detail_of(exc) == "identita_non_verificata"
        # Il rifiuto resta possibile.
        decidi_identita(db, idn.company, idn.admin, "rifiutata")

    def test_decisione_su_azienda_non_viva(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (idn.company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            decidi_identita(db, idn.company, idn.admin)
        assert detail_of(exc) == "company_not_found"

    def test_parametri_della_decisione(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        for esito, nota in (("approvata", None), (None, None), ("verificata", "x" * 501)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                decidi_identita(db, idn.company, idn.admin, esito, nota=nota)
            assert detail_of(exc) == "parametri_non_validi"

    def test_solo_l_admin_decide_e_revoca(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        for chi in (idn.owner, new_admin(db, attivo=False)):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                decidi_identita(db, idn.company, chi)
            assert detail_of(exc) == "admin_non_autorizzato"
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                revoca_identita(db, idn.company, chi)
            assert detail_of(exc) == "admin_non_autorizzato"

    def test_revoca(self, db, idn):
        verifica(db, idn.owner, idn.company, idn.admin)
        out = revoca_identita(db, idn.company, idn.admin, motivo="  Documento contraffatto  ")
        assert out["modificato"] is True
        s = stato_identita(db, idn.company)
        assert (s["stato"], s["metodo"], s["verificata_at"], s["verificata_da"]) == (
            "non_richiesta", None, None, None)
        r = registro_identita(db, idn.company)[-1]
        assert (r["azione"], r["origine"], r["motivo"]) == (
            "revocata", "admin", "Documento contraffatto")
        assert forte(db, idn.company) is False
        assert revoca_identita(db, idn.company, idn.admin)["modificato"] is False
        assert len(audit(db, "identita.revocata")) == 1
        # Dopo la revoca il titolare può chiederla di nuovo.
        richiedi(db, idn.owner, idn.company)

    def test_revoca_senza_verifica(self, db, idn):
        out = revoca_identita(db, idn.company, idn.admin)
        assert (out["modificato"], out["stato"]) == (False, None)
        assert registro_identita(db, idn.company) == []

    @pytest.mark.parametrize("motivo", [None, "  ", "x" * 501])
    def test_motivo_della_revoca(self, db, idn, motivo):
        verifica(db, idn.owner, idn.company, idn.admin)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            revoca_identita(db, idn.company, idn.admin, motivo=motivo)
        assert detail_of(exc) == "motivo_obbligatorio"
        assert forte(db, idn.company) is True

    def test_revoca_di_azienda_inesistente(self, db, idn):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            revoca_identita(db, str(uuid.uuid4()), idn.admin)
        assert detail_of(exc) == "company_not_found"


class TestRevocaAutomatica:
    @pytest.mark.parametrize(("sql", "motivo"), [
        ("deleted_at = now()", "azienda_eliminata"),
        ("archived_at = now()", "azienda_archiviata"),
        ("partita_iva = '99999999999'", "piva_cambiata"),
        ("ragione_sociale = 'Altra Srl'", "ragione_sociale_cambiata"),
    ])
    def test_quattro_eventi(self, db, idn, sql, motivo):
        verifica(db, idn.owner, idn.company, idn.admin)
        db.execute(f"update public.company_profiles set {sql} where id = %s", (idn.company,))
        s = stato_identita(db, idn.company)
        assert (s["stato"], s["metodo"], s["verificata_da"]) == ("non_richiesta", None, None)
        r = registro_identita(db, idn.company)[-1]
        assert (r["azione"], r["origine"], r["attore_user_id"], r["motivo"]) == (
            "revocata", "sistema", None, motivo)
        (a,) = audit(db, "identita.revocata")
        assert a["actor_id"] is None and a["payload"]["motivo"] == motivo
        assert forte(db, idn.company) is False

    def test_altri_cambi_non_revocano(self, db, idn):
        verifica(db, idn.owner, idn.company, idn.admin)
        db.execute("update public.company_profiles set ragione_sociale = ragione_sociale, "
                   "partita_iva = partita_iva, codice_fiscale = 'X' where id = %s",
                   (idn.company,))
        assert stato_identita(db, idn.company)["stato"] == "verificata"
        assert audit(db, "identita.revocata") == []

    def test_senza_verifica_nessun_effetto(self, db, idn):
        richiedi(db, idn.owner, idn.company)
        db.execute("update public.company_profiles set partita_iva = '99999999999' "
                   "where id = %s", (idn.company,))
        assert stato_identita(db, idn.company)["stato"] == "richiesta"
        assert [r["azione"] for r in registro_identita(db, idn.company)] == ["richiesta"]

    def test_soft_delete_dalla_funzione_esistente(self, db, idn):
        verifica(db, idn.owner, idn.company, idn.admin)
        db.execute("select public.fn_soft_delete_company(%s, %s)", (idn.owner, idn.company))
        assert stato_identita(db, idn.company)["stato"] == "non_richiesta"


class TestIdentitaForte:
    def test_serve_la_verifica_e_t5(self, db, idn):
        assert forte(db, idn.company) is False
        verifica(db, idn.owner, idn.company, idn.admin)
        assert forte(db, idn.company) is True
        # T5 perso (nuovo import con dati diversi) senza cambiare l'azienda.
        db.execute("update public.company_data set piva_fetched = '00000000001' "
                   "where company_profile_id = %s", (idn.company,))
        assert forte(db, idn.company) is False

    def test_sandbox_non_la_decide_questa_funzione(self, db):
        admin = new_admin(db)
        owner = new_user(db)
        company = make_company(db, owner)
        importa(db, company, sandbox=True)
        verifica(db, owner, company, admin)
        assert forte(db, company) is True
        assert db.execute("select public.fn_partenariato_identita_ok(%s, true)",
                          (company,)).fetchone()[0] is False

    def test_null_e_inesistente(self, db):
        assert forte(db, None) is False
        assert forte(db, str(uuid.uuid4())) is False

    def test_rappresentante_ok(self, db, idn):
        rapp = "select public.fn_partenariato_rappresentante_ok(%s, %s)"
        # Il CF verificato tra i legali rappresentanti non basta più.
        db.execute("update public.profiles set codice_fiscale = %s, cf_verified_at = now() "
                   "where id = %s", (CF_TITOLARE, idn.owner))
        db.execute("insert into public.company_people (company_profile_id, kind, nome, cognome, "
                   "codice_fiscale, is_legale_rappresentante, raw) "
                   "values (%s, 'manager', 'Mario', 'Rossi', %s, true, '{}'::jsonb)",
                   (idn.company, CF_TITOLARE))
        assert db.execute(rapp, (idn.owner, idn.company)).fetchone()[0] is False
        verifica(db, idn.owner, idn.company, idn.admin)
        assert db.execute(rapp, (idn.owner, idn.company)).fetchone()[0] is True
        assert db.execute(rapp, (new_user(db), idn.company)).fetchone()[0] is False
        # Senza CF: basta la verifica dell'admin.
        owner2, c2 = azienda(db, opt_in=False)
        verifica(db, owner2, c2, idn.admin)
        assert db.execute(rapp, (owner2, c2)).fetchone()[0] is True


# ----------------------------------------------- ridefinizioni: non regressione


class TestConsensoRidefinito:
    def test_nominativo_solo_con_identita_verificata(self, db, idn):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, idn.owner, idn.company, anonimo=False)
        assert detail_of(exc) == "rappresentante_non_verificato"
        verifica(db, idn.owner, idn.company, idn.admin)
        out = consenso(db, idn.owner, idn.company, anonimo=False)
        assert (out["visibile"], out["anonimo"], out["cambiato"]) == (True, False, True)

    def test_anonimato_verso_il_nominativo(self, db, idn):
        consenso(db, idn.owner, idn.company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, idn.owner, idn.company, "anonimato", anonimo=False)
        assert detail_of(exc) == "rappresentante_non_verificato"
        verifica(db, idn.owner, idn.company, idn.admin)
        assert consenso(db, idn.owner, idn.company, "anonimato", anonimo=False)["anonimo"] is False
        # Ritorno all'anonimato e revoca non si bloccano mai.
        revoca_identita(db, idn.company, idn.admin)
        assert consenso(db, idn.owner, idn.company, "anonimato", anonimo=True)["anonimo"] is True
        assert consenso(db, idn.owner, idn.company, "revoca", anonimo=None)["visibile"] is False

    def test_invariato_per_l_anonimo(self, db, idn):
        out = consenso(db, idn.owner, idn.company)
        assert (out["visibile"], out["anonimo"], out["cambiato"]) == (True, True, True)
        assert consenso(db, idn.owner, idn.company)["cambiato"] is False
        db.execute("delete from public.company_data")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_partner_consenso(%s, %s, %s, 'concedi', '2027-01', "
                       "'pagina_azienda', true, true)", (idn.owner, idn.company, idn.owner))
        assert detail_of(exc) == "identita_non_verificata"

    def test_sandbox_nel_consenso_resta_alla_rpc(self, db):
        admin = new_admin(db)
        owner = new_user(db)
        company = make_company(db, owner)
        importa(db, company, sandbox=True)
        verifica(db, owner, company, admin)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, anonimo=False)
        assert detail_of(exc) == "identita_non_verificata"


class TestPubblicazioneNominativa:
    def _bozza(self, db, owner, company, *, anonima: bool) -> str:
        c = inserisci_call(db, company, stato="bozza", anonima=anonima)
        posizione(db, c)
        requisito(db, c)
        return c

    def _pubblica(self, db, owner, company, c) -> dict:
        return db.execute(
            "select public.fn_partner_call_pubblica(%s::uuid, %s::uuid, %s::uuid, %s::uuid, "
            "'aperto', current_date + 90, current_date + 30, true)",
            (owner, company, owner, c)).fetchone()[0]

    def test_nominativa_solo_verificata(self, db):
        admin = new_admin(db)
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        c = self._bozza(db, owner, company, anonima=False)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            self._pubblica(db, owner, company, c)
        assert detail_of(exc) == "rappresentante_non_verificato"
        verifica(db, owner, company, admin)
        assert self._pubblica(db, owner, company, c)["stato"] == "pubblicata"

    def test_anonima_invariata(self, db):
        owner, company = azienda(db, plan_slug="pro", opt_in=False)
        c = self._bozza(db, owner, company, anonima=True)
        assert self._pubblica(db, owner, company, c)["stato"] == "pubblicata"


class TestRivelazioneSimmetrica:
    def _accetta(self, db, *, verifica_x: bool, verifica_y: bool, rivela: bool = True):
        admin = new_admin(db)
        x_owner, x = azienda(db, opt_in=False)
        y_owner, y = azienda(db)
        if verifica_x:
            verifica(db, x_owner, x, admin)
        if verifica_y:
            verifica(db, y_owner, y, admin)
        call = inserisci_call(db, x)
        pos = posizione(db, call)
        k = candida(db, y_owner, y, call, pos)
        return decidi(db, x_owner, x, k, rivela=rivela), SimpleNamespace(
            admin=admin, x_owner=x_owner, x=x, y_owner=y_owner, y=y)

    def test_entrambe_verificate_si_rivela(self, db):
        out, _ = self._accetta(db, verifica_x=True, verifica_y=True)
        assert set(out) == {"candidatura", "conversazione_id", "membro_id"}
        righe = (audit(db, "partenariato.candidatura_accettata")
                 + audit(db, "partenariato.identita_rivelata")
                 + audit(db, "partenariato.contatti_rivelati"))
        assert len(righe) == 3
        assert {a["payload"]["conversazione_id"] for a in righe} == {out["conversazione_id"]}

    @pytest.mark.parametrize(("vx", "vy"), [(True, False), (False, True), (False, False)])
    def test_una_sola_verificata_restano_anonime(self, db, vx, vy):
        out, _ = self._accetta(db, verifica_x=vx, verifica_y=vy)
        assert out["candidatura"]["stato"] == "accettata"
        assert len(audit(db, "partenariato.candidatura_accettata")) == 1
        assert audit(db, "partenariato.identita_rivelata") == []
        assert audit(db, "partenariato.contatti_rivelati") == []

    def test_senza_p_rivela_nessuna_rivelazione(self, db):
        self._accetta(db, verifica_x=True, verifica_y=True, rivela=False)
        assert audit(db, "partenariato.identita_rivelata") == []

    def test_dopo_la_revoca_niente_rivelazione_nuova(self, db):
        _, s = self._accetta(db, verifica_x=True, verifica_y=True)
        assert len(audit(db, "partenariato.identita_rivelata")) == 1
        revoca_identita(db, s.y, s.admin)
        call = inserisci_call(db, s.x)
        pos = posizione(db, call)
        k = candida(db, s.y_owner, s.y, call, pos)
        decidi(db, s.x_owner, s.x, k, rivela=True)
        # Il vecchio audit resta, nessun audit nuovo.
        assert len(audit(db, "partenariato.identita_rivelata")) == 1
        assert len(audit(db, "partenariato.candidatura_accettata")) == 2


# ---------------------------------------------------------------- metriche


def pubblica_da(db, company, fa: str, *, esito=None, copertura=None) -> str:
    """Call pubblicata `fa` (intervallo SQL) con l'ultima validazione indicata."""
    c = inserisci_call(db, company)
    db.execute(
        "update public.partner_calls set pubblicata_at = now() - %s::interval, "
        "validazione_esito = %s, validazione_at = case when %s::text is not null then now() end, "
        "copertura_gap_ratio = %s where id = %s",
        (fa, esito, esito, copertura, c))
    return c


def cand_diretta(db, call, creatore, tipo, stato, dopo: str) -> str:
    """Candidatura o invito scritto direttamente, creato `dopo` la pubblicazione."""
    owner, company = azienda(db, opt_in=False)
    decisa = stato in ("accettata", "rifiutata")
    return str(db.execute(
        "insert into public.partner_candidature (partner_call_id, tipo, company_profile_id, "
        "family_parent_id, creatore_company_profile_id, messaggio, pseudonimo, "
        "inviata_da_user_id, stato, decisa_at, decisa_da_user_id, scade_at, created_at) "
        "select %s, %s, %s, %s, %s, %s, %s, %s, %s, case when %s then now() end, "
        "case when %s then %s::uuid end, case when %s = 'invito' then now() + interval '14 days' "
        "end, c.pubblicata_at + %s::interval from public.partner_calls c where c.id = %s "
        "returning id",
        (call, tipo, company, owner, creatore, MSG if tipo == "candidatura" else None, pseudo(),
         owner, stato, decisa, decisa, owner, tipo, dopo, call),
    ).fetchone()[0])


def metriche(db, da_giorni: int = 90) -> dict:
    return db.execute(
        "select public.fn_admin_metriche_partenariati("
        "(now() at time zone 'Europe/Rome')::date - %s, (now() at time zone 'Europe/Rome')::date)",
        (da_giorni,)).fetchone()[0]


class TestMetriche:
    def test_seed_noto(self, db):
        _, x = azienda(db, opt_in=False)
        c1 = pubblica_da(db, x, "60 days", esito="verde", copertura=Decimal("0.5"))
        c2 = pubblica_da(db, x, "40 days", esito="rosso", copertura=Decimal("1"))
        c3 = pubblica_da(db, x, "5 days")
        # Pubblicata da meno di 30 giorni per 12 ore: non ancora osservabile.
        c5 = pubblica_da(db, x, "29 days 12 hours")
        c4 = pubblica_da(db, x, "200 days", esito="verde", copertura=Decimal("0"))  # fuori
        inserisci_call(db, x, stato="bozza")                                        # mai pubblicata
        cand_diretta(db, c1, x, "candidatura", "accettata", "10 hours")
        cand_diretta(db, c1, x, "candidatura", "rifiutata", "40 days")
        cand_diretta(db, c1, x, "invito", "rifiutata", "1 day")
        cand_diretta(db, c2, x, "candidatura", "inviata", "35 days")
        cand_diretta(db, c2, x, "invito", "accettata", "2 days")
        cand_diretta(db, c3, x, "candidatura", "rifiutata", "30 hours")
        cand_diretta(db, c5, x, "candidatura", "inviata", "3 days")
        cand_diretta(db, c4, x, "candidatura", "accettata", "1 hour")
        m = metriche(db)
        oggi = db.execute("select (now() at time zone 'Europe/Rome')::date").fetchone()[0]
        assert m == {
            "da": str(oggi.fromordinal(oggi.toordinal() - 90)), "a": str(oggi),
            "call_pubblicate": 4,
            "candidature": 5,
            "inviti": 2,
            "candidature_per_call": 1.25,
            "call_osservabili_30_giorni": 2,
            "call_con_candidatura_30_giorni": 1,
            "percentuale_call_con_candidatura_30_giorni": 50.0,
            "accettazione": {
                "candidatura": {"accettate": 1, "rifiutate": 2, "tasso": 0.333},
                "invito": {"accettate": 1, "rifiutate": 1, "tasso": 0.5},
            },
            # Prime candidature spontanee: 10 h, 840 h, 30 h, 72 h (gli inviti no).
            "ore_mediane_prima_candidatura": 51.0,
            "copertura_media_gap": 0.75,
            "consorzi_validati": 2,
            "consorzi_validati_verde": 1,
        }

    def test_periodo_vuoto(self, db):
        m = metriche(db)
        assert (m["call_pubblicate"], m["candidature"], m["inviti"]) == (0, 0, 0)
        for chiave in ("candidature_per_call", "percentuale_call_con_candidatura_30_giorni",
                       "ore_mediane_prima_candidatura", "copertura_media_gap"):
            assert m[chiave] is None, chiave
        assert m["accettazione"] == {
            "candidatura": {"accettate": 0, "rifiutate": 0, "tasso": None},
            "invito": {"accettate": 0, "rifiutate": 0, "tasso": None}}

    def test_estremi_del_periodo_su_roma(self, db):
        _, x = azienda(db, opt_in=False)
        dentro = inserisci_call(db, x)
        fuori = inserisci_call(db, x)
        db.execute("update public.partner_calls set pubblicata_at = "
                   "'2026-03-01 00:00'::timestamp at time zone 'Europe/Rome' where id = %s",
                   (dentro,))
        db.execute("update public.partner_calls set pubblicata_at = "
                   "'2026-02-28 23:59:59'::timestamp at time zone 'Europe/Rome' where id = %s",
                   (fuori,))
        m = db.execute("select public.fn_admin_metriche_partenariati('2026-03-01', '2026-03-01')"
                       ).fetchone()[0]
        assert m["call_pubblicate"] == 1

    @pytest.mark.parametrize(("da", "a"), [
        ("2026-03-02", "2026-03-01"), (None, "2026-03-01"), ("2026-03-01", None),
        ("2016-01-01", "2026-03-01")])
    def test_periodo_non_valido(self, db, da, a):
        for funzione in ("fn_admin_metriche_partenariati", "fn_admin_costi_partenariati"):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                db.execute(f"select public.{funzione}(%s::date, %s::date)", (da, a))
            assert detail_of(exc) == "periodo_non_valido"


def evento(db, provider, service, outcome, cents, quando_roma) -> None:
    db.execute(
        "insert into public.api_usage_events (provider, service, outcome, cost_cents, created_at) "
        "values (%s, %s, %s, %s, %s::timestamp at time zone 'Europe/Rome')",
        (provider, service, outcome, cents, quando_roma))


class TestCosti:
    def test_per_provider_e_valuta_mai_sommati(self, db):
        evento(db, "anthropic", "partenariato_estrazione", "success", 10, "2026-03-01 00:00")
        evento(db, "anthropic", "partenariato_estrazione", "success", 20, "2026-03-15 12:00")
        evento(db, "anthropic", "partenariato_estrazione", "success", 30, "2026-03-31 23:59:59")
        evento(db, "anthropic", "partner_call_testi", "timeout_unknown", 7, "2026-03-10 08:00")
        evento(db, "openapi", "IT-advanced", "success", 10, "2026-03-05 09:00")
        evento(db, "openapi", "IT-advanced", "success", 10, "2026-03-06 09:00")
        evento(db, "openapi", "bilancio-ottico", "error", 0, "2026-03-07 09:00")
        # Fuori dal modulo o dal periodo.
        evento(db, "openapi", "IT-full", "success", 30, "2026-03-08 09:00")
        evento(db, "anthropic", "ai_check", "success", 50, "2026-03-09 09:00")
        evento(db, "openapi", "partenariato_estrazione", "success", 40, "2026-03-09 09:00")
        evento(db, "anthropic", "partenariato_estrazione", "success", 99, "2026-04-01 00:00")
        evento(db, "anthropic", "partenariato_estrazione", "success", 98, "2026-02-28 23:59:59")
        out = db.execute("select public.fn_admin_costi_partenariati('2026-03-01', '2026-03-31')"
                         ).fetchone()[0]
        assert out == {
            "da": "2026-03-01", "a": "2026-03-31",
            "voci": [
                {"provider": "openapi", "service": "IT-advanced", "outcome": "success",
                 "valuta": "EUR", "eventi": 2, "cost_cents": 20},
                {"provider": "openapi", "service": "bilancio-ottico", "outcome": "error",
                 "valuta": "EUR", "eventi": 1, "cost_cents": 0},
                {"provider": "anthropic", "service": "partenariato_estrazione",
                 "outcome": "success", "valuta": "USD", "eventi": 3, "cost_cents": 60},
                {"provider": "anthropic", "service": "partner_call_testi",
                 "outcome": "timeout_unknown", "valuta": "USD", "eventi": 1, "cost_cents": 7},
            ],
            "totali": [
                {"valuta": "EUR", "eventi": 3, "cost_cents": 20},
                {"valuta": "USD", "eventi": 4, "cost_cents": 67},
            ],
        }

    def test_tutti_i_servizi_del_modulo(self, db):
        servizi = [("anthropic", s) for s in ("partenariato_estrazione", "partner_profilo_ai",
                                              "partner_call_posizioni", "partner_call_testi",
                                              "partner_bozza")]
        servizi += [("openapi", s) for s in ("IT-advanced", "bilancio-ottico",
                                             "bilancio-ottico-stato", "visure-impresa")]
        for provider, service in servizi:
            evento(db, provider, service, "success", 1, "2026-03-10 10:00")
        out = db.execute("select public.fn_admin_costi_partenariati('2026-03-10', '2026-03-10')"
                         ).fetchone()[0]
        assert {(v["provider"], v["service"]) for v in out["voci"]} == set(servizi)
        assert out["totali"] == [{"valuta": "EUR", "eventi": 4, "cost_cents": 4},
                                 {"valuta": "USD", "eventi": 5, "cost_cents": 5}]

    def test_periodo_vuoto(self, db):
        out = db.execute("select public.fn_admin_costi_partenariati('2026-03-01', '2026-03-31')"
                         ).fetchone()[0]
        assert (out["voci"], out["totali"]) == ([], [])


# ------------------------------------------------------ validazioni da rifare


class TestValidazioniDaRicalcolare:
    def test_selezione_e_ordine(self, db):
        _, x = azienda(db, opt_in=False)
        mai = inserisci_call(db, x)                         # mai validata
        aggiornata = inserisci_call(db, x)                  # validata dopo i membri
        vecchia = inserisci_call(db, x)                     # membro scritto dopo
        scaduta = inserisci_call(db, x, stato="scaduta")    # mai validata
        completata = inserisci_call(db, x, stato="chiusa_completata")
        for stato in ("bozza", "chiusa_annullata", "sospesa_moderazione"):
            inserisci_call(db, x, stato=stato)
        db.execute("select public.fn_partner_backfill_membri()")
        for c, quando in ((aggiornata, "now() + interval '1 hour'"),
                          (vecchia, "now() - interval '1 hour'"),
                          (completata, "now() + interval '1 hour'")):
            db.execute(f"update public.partner_calls set validazione_esito = 'grigio', "
                       f"validazione_at = {quando} where id = %s", (c,))
        ids = [str(r[0]) for r in db.execute(
            "select public.fn_partner_call_validazioni_da_ricalcolare(100)").fetchall()]
        assert set(ids) == {mai, vecchia, scaduta}
        assert ids[-1] == vecchia  # prima le mai validate
        assert len(db.execute("select public.fn_partner_call_validazioni_da_ricalcolare(1)"
                              ).fetchall()) == 1
        assert len(db.execute("select public.fn_partner_call_validazioni_da_ricalcolare(0)"
                              ).fetchall()) == 1
        assert len(db.execute("select public.fn_partner_call_validazioni_da_ricalcolare(null)"
                              ).fetchall()) == 3

    def test_un_membro_modificato_la_rimette_in_coda(self, db, mod):
        db.execute("select public.fn_partner_call_validazione_salva(%s, 'verde', 1)",
                   (mod.call,))
        db.execute("update public.partner_calls set validazione_at = now() + interval '1 hour' "
                   "where id = %s", (mod.call,))
        prima = {str(r[0]) for r in db.execute(
            "select public.fn_partner_call_validazioni_da_ricalcolare(10)").fetchall()}
        assert mod.call not in prima
        db.execute("update public.partner_calls set validazione_at = now() - interval '1 hour' "
                   "where id = %s", (mod.call,))
        db.execute("update public.partner_call_membri set quota_percentuale = 30 "
                   "where id = %s", (mod.membro,))
        dopo = {str(r[0]) for r in db.execute(
            "select public.fn_partner_call_validazioni_da_ricalcolare(10)").fetchall()}
        assert mod.call in dopo


# ---------------------------------------------------------------- concorrenza


class TestConcorrenza:
    def test_due_decisioni_sulla_stessa_segnalazione(self, db, mod):
        sid = segnala(db, "call", mod.call, mod.y_owner)
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: decidi_seg(conn_b, sid, mod.admin, "call_sospesa"))
        try:
            decidi_seg(conn_a, sid, mod.admin, "nessuna_azione")
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "segnalazione_gia_decisa"
        assert seg(db, sid)["decisione"] == "nessuna_azione"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_due_ricorsi_accolti_sulla_stessa_call(self, db, mod):
        """Due ricorsi sulla stessa call decisi insieme: il secondo attende il
        lock dell'oggetto, vede il primo già accolto e annulla la sospensione
        (senza il lock entrambi vedrebbero l'altro ancora valido e la call
        resterebbe sospesa)."""
        z_owner, _ = azienda(db)
        s1 = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, s1, mod.admin, "call_sospesa")
        s2 = segnala(db, "call", mod.call, z_owner)
        decidi_seg(db, s2, mod.admin, "call_sospesa")
        ricorso(db, s1, mod.x_owner, mod.x)
        ricorso(db, s2, mod.x_owner, mod.x)
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: decidi_ricorso(conn_b, s2, mod.admin, "riformata"))
        try:
            assert decidi_ricorso(conn_a, s1, mod.admin, "riformata")["effetto"] == "mantenuto"
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert "errore" not in eb, eb
        assert eb["out"]["effetto"] == "annullato"
        assert call_row(db, mod.call)["stato"] == "pubblicata"

    def test_ricorso_accolto_contro_nuova_decisione(self, db, mod):
        """Una seconda segnalazione viene decisa con la sospensione (effetto
        gia_applicato, non ancora committata) mentre si accoglie il ricorso
        sulla decisione che ha sospeso la call: il ricorso attende il lock
        dell'oggetto, vede la nuova decisione e mantiene la sospensione (senza
        il lock la leggerebbe ancora «in esame» e toglierebbe la sospensione
        che la nuova decisione regge)."""
        z_owner, _ = azienda(db)
        s1 = segnala(db, "call", mod.call, mod.y_owner)
        decidi_seg(db, s1, mod.admin, "call_sospesa")
        ricorso(db, s1, mod.x_owner, mod.x)
        s2 = segnala(db, "call", mod.call, z_owner)
        prendi(db, s2, mod.admin)
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: decidi_ricorso(conn_b, s1, mod.admin, "riformata"))
        try:
            assert decidi_seg(conn_a, s2, mod.admin, "call_sospesa")["effetto"] == "gia_applicato"
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert "errore" not in eb, eb
        assert eb["out"]["effetto"] == "mantenuto"
        assert call_row(db, mod.call)["stato"] == "sospesa_moderazione"

    def test_sospensione_contro_chiusura_del_creatore(self, db, mod):
        """Entrambe bloccano owner → azienda → call: la sospensione attende la
        chiusura e poi trova la call non più sospendibile (nessun deadlock)."""
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: sospendi(conn_b, "call", mod.call, mod.admin))
        try:
            conn_a.execute(
                "select public.fn_partner_call_chiudi(%s::uuid, %s::uuid, %s::uuid, %s::uuid, "
                "'annullata')", (mod.x_owner, mod.x, mod.x_owner, mod.call))
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "oggetto_non_sospendibile"
        assert call_row(db, mod.call)["stato"] == "chiusa_annullata"

    def test_revoca_automatica_contro_revoca_dell_admin(self, db, idn):
        verifica(db, idn.owner, idn.company, idn.admin)
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: revoca_identita(conn_b, idn.company, idn.admin))
        try:
            conn_a.execute("update public.company_profiles set partita_iva = '99999999999' "
                           "where id = %s", (idn.company,))
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert "errore" not in eb, eb
        assert eb["out"]["modificato"] is False
        assert [(r["azione"], r["origine"]) for r in registro_identita(db, idn.company)] == [
            ("richiesta", "utente"), ("verificata", "admin"), ("revocata", "sistema")]

    def test_due_richieste_concorrenti(self, db, idn):
        dsn = db.info.dsn
        conn_a = psycopg.connect(dsn)
        conn_b = psycopg.connect(dsn, autocommit=True)
        monitor = psycopg.connect(dsn, autocommit=True)
        tb, eb = in_thread(lambda: richiedi(conn_b, idn.owner, idn.company))
        try:
            richiedi(conn_a, idn.owner, idn.company)
            tb.start()
            in_attesa(monitor, conn_b.info.backend_pid, tb, eb)
            conn_a.commit()
            tb.join(timeout=10)
        finally:
            chiudi_tutto(conn_a, conn_b, monitor)
            if tb.is_alive():
                tb.join(timeout=10)
        assert errore_detail(eb) == "identita_richiesta_aperta"
        assert len(registro_identita(db, idn.company)) == 1


# ---------------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0041, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0041, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")
ESEGUIBILE = "\n".join(r for r in SQL_0041.splitlines() if not r.lstrip().startswith("--"))
DETAIL_NEL_FILE = set(re.findall(r"detail = '([a-z_]+)'", ESEGUIBILE))
# Detail NON mappati di proposito (bug del backend → 502, come nei WP5-WP8).
DETAIL_NON_MAPPATI = {"parametri_non_validi", "campo_protetto", "registro_append_only"}


def _corpo(sql: str, nome: str) -> list[str]:
    m = re.search(rf"^create or replace function public\.{nome}\(.*?^\$\$;\n", sql, re.M | re.S)
    assert m, nome
    return m.group(0).splitlines()


class TestSicurezza0041:
    def test_inventario_del_file(self):
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == set(FIRME)
        assert not re.search(r"^create (function|table(?! public\.))", ESEGUIBILE, re.M)

    def test_additiva(self):
        """L'unico oggetto eliminato è l'indice one_open, ricreato con lo stesso nome;
        le sole tabelle esistenti alterate sono consultation_requests e
        partner_segnalazioni (colonne e vincoli nuovi)."""
        assert re.findall(r"^[ \t]*drop\b.*$", ESEGUIBILE, re.M | re.I) == [
            "drop index public.consultation_requests_one_open;"]
        assert re.search(r"^create unique index consultation_requests_one_open\n", ESEGUIBILE,
                         re.M)
        assert not re.search(r"^\s*(alter function|create or replace view)\b",
                             ESEGUIBILE, re.M | re.I)
        alterate = set(re.findall(r"^alter table public\.(\w+)", ESEGUIBILE, re.M))
        assert alterate == TABELLE_NUOVE | {"consultation_requests", "partner_segnalazioni"}
        for tabella in ("consultation_requests", "partner_segnalazioni"):
            blocco = re.search(rf"^alter table public\.{tabella}\n(.*?);\n", ESEGUIBILE,
                               re.M | re.S).group(1)
            clausole = [r.split()[0] for r in blocco.splitlines() if re.match(r"^  \S", r)]
            assert clausole and set(clausole) == {"add"}, tabella
        assert FUNZIONI_NEL_FILE - FUNZIONI_NUOVE == RIDEFINITE
        # Nessun trigger nuovo su partner_calls e partner_segnalazioni (i test
        # della 0037 e della 0040 ne fissano l'insieme).
        assert set(re.findall(r"^create trigger \w+\n\s+(?:before|after) [\w ,]+?\s+on "
                              r"public\.(\w+)", ESEGUIBILE, re.M)) == (
            TABELLE_NUOVE | {"company_profiles"})

    def test_ridefinite_con_la_stessa_firma(self, db):
        for nome in RIDEFINITE:
            firme = [r[0] for r in db.execute(
                "select p.oid::regprocedure::text from pg_proc p join pg_namespace n "
                "on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = %s",
                (nome,)).fetchall()]
            assert firme == [FIRME[nome]]
        assert db.execute(
            "select pg_get_function_arguments('public.fn_partner_decidi'::regproc)"
        ).fetchone()[0].endswith("p_richiedi_non_sandbox boolean DEFAULT true")
        assert db.execute(
            "select pg_get_function_arguments('public.fn_create_consultation_request'::regproc)"
        ).fetchone()[0] == "p_payload jsonb"

    def test_copia_fedele_dei_corpi(self):
        """Ogni corpo ridefinito è quello dell'ultima definizione con le sole
        modifiche previste (righe tolte e aggiunte esatte)."""
        attese = {
            "fn_create_consultation_request": (
                ["punteggio, bando_id, bando_slug, bando_titolo, addon_id, addon_slug, "
                 "addon_prezzo)",
                 "v_addon.id, v_addon.slug, v_addon.prezzo)"],
                ["v_call  uuid;  -- 0041", "end if;", "",
                 "-- 0041: consulto dalla call (W1).", "begin",
                 "v_call := nullif(p_payload ->> 'partner_call_id', '')::uuid;",
                 "exception when invalid_text_representation then",
                 "raise exception 'Call di partenariato non trovata' using detail = "
                 "'call_non_trovata';",
                 "end;", "if v_call is not null then", "perform 1 from public.partner_calls",
                 "where id = v_call",
                 "and company_profile_id = (p_payload ->> 'company_profile_id')::uuid",
                 "and family_parent_id = (p_payload ->> 'family_parent_id')::uuid",
                 "for key share;", "if not found then",
                 "raise exception 'Call di partenariato non trovata' using detail = "
                 "'call_non_trovata';",
                 "end if;",
                 "punteggio, bando_id, bando_slug, bando_titolo, addon_id, addon_slug, "
                 "addon_prezzo,",
                 "partner_call_id)", "v_addon.id, v_addon.slug, v_addon.prezzo,", "v_call)",
                 "-- 0041: anche consultation_requests_one_open_call (un consulto aperto",
                 "-- per call), stesso detail."]),
            "fn_partenariato_rappresentante_ok": (None, None),
            "fn_partner_consenso": (
                ["if not p_anonimo and not exists (", "select 1", "from public.profiles pr",
                 "join public.company_people pe",
                 "on upper(btrim(pe.codice_fiscale)) = upper(btrim(pr.codice_fiscale))",
                 "where pr.id = p_owner", "and pr.cf_verified_at is not null",
                 "and pe.company_profile_id = p_company", "and pe.is_legale_rappresentante",
                 ") then"],
                ["-- 0041: il nominativo richiede l'identità forte (verificata dall'admin)",
                 "-- dell'azienda del titolare; il CF resta solo informativo.",
                 "if not p_anonimo",
                 "and not public.fn_partenariato_rappresentante_ok(p_owner, p_company) then"]),
            "fn_partner_decidi": (
                ["if coalesce(p_rivela, false) then"],
                ["-- 0041: rivelazione SIMMETRICA (decisione di Michele): solo se anche oggi",
                 "-- entrambe le aziende hanno l'identità forte; altrimenti restano anonime e",
                 "-- non si scrive l'audit di rivelazione.",
                 "if coalesce(p_rivela, false)",
                 "and public.fn_partenariato_identita_forte(v_k.creatore_company_profile_id)",
                 "and public.fn_partenariato_identita_forte(v_k.company_profile_id) then"]),
        }
        for nome, (tolte_attese, aggiunte_attese) in attese.items():
            orig = _corpo((MIGRAZIONI / ORIGINE[nome]).read_text(encoding="utf-8"), nome)
            nuovo = _corpo(SQL_0041, nome)
            diff = list(difflib.ndiff(orig, nuovo))
            tolte = [r[2:].strip() for r in diff if r.startswith("- ")]
            aggiunte = [r[2:].strip() for r in diff if r.startswith("+ ")]
            if nome == "fn_partenariato_rappresentante_ok":
                # Riscritta del tutto: stessa intestazione, corpo nuovo.
                assert orig[:10] == nuovo[:10]
                continue
            assert tolte == tolte_attese, nome
            assert aggiunte == aggiunte_attese, nome

    def test_trigger(self, db):
        trigger = {(r[0], r[1]) for r in db.execute(
            "select tgname, tgrelid::regclass::text from pg_trigger where not tgisinternal "
            "and tgname like 'trg\\_identita\\_%%'").fetchall()}
        assert trigger == {
            ("trg_identita_verifiche_readonly", "company_identita_verifiche"),
            ("trg_identita_verifiche_no_truncate", "company_identita_verifiche"),
            ("trg_identita_stato_protetto", "company_identita_stato"),
            ("trg_identita_revoca_su_cambio_azienda", "company_profiles"),
        }
        # Nessun trigger su DELETE dello stato: la cascade dell'azienda passa.
        assert db.execute(
            "select count(*) from pg_trigger where not tgisinternal "
            "and tgrelid = 'public.company_identita_stato'::regclass and (tgtype & 8) <> 0"
        ).fetchone()[0] == 0

    def test_volatilita(self, db):
        for nome in ("fn_admin_metriche_partenariati", "fn_admin_costi_partenariati",
                     "fn_partenariato_identita_forte", "fn_partenariato_rappresentante_ok",
                     "fn_partner_moderazione_autore",
                     "fn_partner_call_validazioni_da_ricalcolare"):
            assert db.execute("select provolatile from pg_proc where proname = %s",
                              (nome,)).fetchone()[0] == "s", nome

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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0041, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0041, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration e ogni fn_partner_% /
        fn_partenariat% / fn_identita_% / fn_admin_% presente nel DB è SECURITY
        DEFINER con search_path fissato, non eseguibile dai client (PUBLIC
        compreso) ed esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public'
                  and (p.proname like 'fn\_partner\_%%' or p.proname like 'fn\_partenariat%%'
                       or p.proname like 'fn\_identita\_%%' or p.proname like 'fn\_admin\_%%')"""
        ).fetchall()}
        assert FUNZIONI_NUOVE <= dal_db | {"fn_create_consultation_request"}
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
                SQL_0041, re.M,
            ), nome
            assert firma.startswith(nome)

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_create_consultation_request('{}'::jsonb)",
        "select public.fn_identita_richiedi(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), null)",
        "select public.fn_identita_decidi(gen_random_uuid(), gen_random_uuid(), 'verificata', "
        "'pec', null)",
        "select public.fn_identita_revoca(gen_random_uuid(), gen_random_uuid(), 'x')",
        "select public.fn_partenariato_identita_forte(gen_random_uuid())",
        "select public.fn_partner_segnalazione_prendi(gen_random_uuid(), gen_random_uuid())",
        "select public.fn_partner_segnalazione_decidi(gen_random_uuid(), gen_random_uuid(), "
        "'nessuna_azione', 'x', null)",
        "select public.fn_partner_segnalazione_ricorso(gen_random_uuid(), gen_random_uuid(), "
        "gen_random_uuid(), 'x')",
        "select public.fn_partner_ricorso_decidi(gen_random_uuid(), gen_random_uuid(), "
        "'confermata', 'x')",
        "select public.fn_partner_admin_sospendi('call', 'x', gen_random_uuid(), 'x')",
        "select public.fn_partner_admin_ripristina('call', 'x', gen_random_uuid(), 'x')",
        "select public.fn_admin_metriche_partenariati(current_date, current_date)",
        "select public.fn_admin_costi_partenariati(current_date, current_date)",
        "select public.fn_partner_call_validazioni_da_ricalcolare(1)",
        "select public.fn_partner_moderazione_applica('call', 'x', null, 'x')",
        "select public.fn_partner_moderazione_annulla('call', 'x')",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, ruolo, chiamata):
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_esegue_il_flusso(self, db, mod):
        """Come il service_role: un grant esplicito sulle RPC basta e le funzioni
        scrivono senza privilegi di tabella (trigger e GUC compresi)."""
        sid = segnala(db, "profilo", mod.profilo_x, mod.y_owner)
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        rpc = ("fn_partner_segnalazione_prendi", "fn_partner_segnalazione_decidi",
               "fn_partner_segnalazione_ricorso", "fn_partner_ricorso_decidi",
               "fn_partner_admin_sospendi", "fn_partner_admin_ripristina",
               "fn_identita_richiedi", "fn_identita_decidi", "fn_identita_revoca",
               "fn_admin_metriche_partenariati", "fn_admin_costi_partenariati",
               "fn_partner_call_validazioni_da_ricalcolare", "fn_partenariato_identita_forte")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            for nome in rpc:
                db.execute(f"grant execute on function public.{FIRME[nome]} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                prendi(db, sid, mod.admin)
                decidi_seg(db, sid, mod.admin, "profilo_sospeso")
                ricorso(db, sid, mod.x_owner, mod.x)
                decidi_ricorso(db, sid, mod.admin, "riformata")
                sospendi(db, "call", mod.call, mod.admin)
                ripristina(db, "call", mod.call, mod.admin)
                richiedi(db, mod.y_owner, mod.y)
                decidi_identita(db, mod.y, mod.admin)
                assert forte(db, mod.y) is True
                revoca_identita(db, mod.y, mod.admin)
                db.execute("select public.fn_admin_metriche_partenariati(current_date, "
                           "current_date)")
                db.execute("select public.fn_admin_costi_partenariati(current_date, "
                           "current_date)")
                db.execute("select public.fn_partner_call_validazioni_da_ricalcolare(10)")
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        assert seg(db, sid)["stato"] == "ricorso_deciso"
        assert stato_identita(db, mod.y)["stato"] == "non_richiesta"

    def test_nessun_identificativo_in_chiaro_nelle_colonne(self, db):
        """Le tabelle nuove non hanno colonne per P.IVA, CF, email o nomi (T8)."""
        colonne = {r[0] for r in db.execute(
            "select column_name::text from information_schema.columns "
            "where table_schema = 'public' and table_name::text = any (%s)",
            (sorted(TABELLE_NUOVE),)).fetchall()}
        assert len(colonne) > 12
        for vietata in ("partita_iva", "piva", "codice_fiscale", "cf", "email", "nome",
                        "cognome", "ragione_sociale", "denominazione", "telefono"):
            assert vietata not in colonne, vietata

    def test_ogni_detail_nuovo_e_mappato(self):
        """Ogni detail della 0041 ha la sua voce in partenariato_errori.RPC_ERRORS
        (status, code, messaggio), salvo quelli lasciati a 502 di proposito."""
        errori = pytest.importorskip("app.services.partenariato_errori")
        mancanti = DETAIL_NEL_FILE - set(errori.RPC_ERRORS) - DETAIL_NON_MAPPATI
        assert not mancanti, sorted(mancanti)
        assert not DETAIL_NON_MAPPATI & set(errori.RPC_ERRORS)
        for detail in ("call_non_trovata", "segnalazione_gia_decisa", "ricorso_non_ammesso",
                       "identita_richiesta_aperta", "periodo_non_valido",
                       "identita_non_verificata_admin"):
            status, code, messaggio = errori.RPC_ERRORS[detail]
            assert 400 <= status < 500 and code and messaggio
