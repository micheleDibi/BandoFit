"""Test funzionali della migration 0046 (ripristino lungo le catene di fusioni
e conflitti definitivi del percorso automatico; complemento della 0043, della
0044 e della 0045).

Coprono: la catena D → M → M2 seguita dal ripristino di D (automatico e
manuale), con gli anelli chiusi, l'idempotenza, una catena lunga, una che torna
al doppione, la separazione di M prima o dopo quella di D, la regola della riga
che «sostiene» un'eliminazione su un master della catena, un anello che ha
eliminato la riga; il doppione rimesso dall'utente (preferito, scadenza,
riga eliminata, evento convertito) e poi tolto: conflitto definitivo marcato
nel percorso automatico e mai ripristinato, esclusione da fn_doppioni_rimappati,
la prova che non scrive, gli altri conflitti ancora ritentati, il percorso
manuale che ignora i marcatori; il flusso reale 0043 → 0044 → 0045 → 0046 su
dati già scritti; firma, SECURITY DEFINER, search_path e revoche delle tre
funzioni; la migration applicata più volte.
Ogni test riceve un database fresco clonato dal template.
"""

import itertools
import re
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SQL_0044 = (MIGRAZIONI / "0044_ripristino_rimappatura.sql").read_text(encoding="utf-8")
SQL_0045 = (MIGRAZIONI / "0045_doppioni_rimappati.sql").read_text(encoding="utf-8")
SQL_0046 = next(MIGRAZIONI.glob("0046_*.sql")).read_text(encoding="utf-8")

ORIGINE = "catalogo.rimappatura_fuso"
RIPRISTINO = "catalogo.ripristino_rimappatura"
MARCATORE = "catalogo.conflitto_definitivo"

DOPPIONE = 7001
MASTER = 7000
MASTER2 = 7100
MASTER3 = 7150
TERZO = 7003  # un altro doppione di MASTER2
ALTRO_DOPPIONE = 7002
ALTRO_MASTER = 7200

TABELLE = ("saved_bandi", "calendar_events", "partner_calls")
_seq = itertools.count(1)


# ----------------------------------------------------------------- helper


def slug(bando: int) -> str:
    return f"bando-{bando}"


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def conteggi(sb=(0, 0), ce=(0, 0), pc=(0, 0)) -> dict:
    """Ritorno del ripristino manuale: (ripristinate, in_conflitto)."""
    return {
        tabella: {"ripristinate": rip, "in_conflitto": con}
        for tabella, (rip, con) in zip(TABELLE, (sb, ce, pc), strict=True)
    }


def auto(sb=(0, 0, 0), ce=(0, 0, 0), pc=(0, 0, 0)) -> dict:
    """Ritorno del percorso automatico: (ripristinate, in_conflitto, definitivi)."""
    return {
        tabella: {"ripristinate": rip, "in_conflitto": con, "definitivi": dfn}
        for tabella, (rip, con, dfn) in zip(TABELLE, (sb, ce, pc), strict=True)
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


def azienda(db) -> tuple[str, str]:
    owner = new_user(db, "smart")
    i = next(_seq)
    company = str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, %s, %s) returning id",
        (owner, f"ACME {i} Srl", f"{i + 46000000:011d}"),
    ).fetchone()[0])
    return owner, company


def salva(db, user: str, bando_id: int, company: str | None = None) -> str:
    return str(db.execute(
        "insert into public.saved_bandi (user_id, company_profile_id, bando_id, bando_slug, "
        "bando_titolo, data_scadenza, stato_bando) "
        "values (%s, %s, %s, %s, %s, current_date + 20, 'aperto') returning id",
        (user, company, bando_id, slug(bando_id), f"Titolo del bando {bando_id}"),
    ).fetchone()[0])


def evento(db, user: str, bando_id: int, note: str | None = None) -> str:
    return str(db.execute(
        "insert into public.calendar_events (user_id, titolo, data, note, tipo, bando_id, "
        "bando_slug) values (%s, %s, current_date + 20, %s, 'bando', %s, %s) returning id",
        (user, f"Scadenza del bando {bando_id}", note, bando_id, slug(bando_id)),
    ).fetchone()[0])


def inserisci_call(db, company: str, bando_id: int, *, stato: str = "pubblicata",
                   mancante_dal: bool = False) -> str:
    """Call attiva scritta direttamente (come dopo le RPC del WP5)."""
    owner = str(db.execute("select parent_id from public.company_profiles where id = %s",
                           (company,)).fetchone()[0])
    return str(db.execute(
        """insert into public.partner_calls
             (company_profile_id, family_parent_id, creato_da, bando_id, bando_slug,
              bando_titolo, ruolo_creatore, titolo, descrizione_pubblica,
              scadenza_call, regole_partenariato, regole_confermate_at, visibilita, stato,
              pubblicata_at, bando_mancante_dal)
           values (%s, %s, %s, %s, %s, 'Bando di prova', 'capofila',
                   'Cerchiamo un organismo di ricerca', 'Progetto di ricerca industriale.',
                   current_date + 30, '{}'::jsonb, now(), 'pubblica', %s,
                   case when %s = 'pubblicata' then now() end,
                   case when %s then current_date - 2 end)
           returning id""",
        (company, owner, owner, bando_id, slug(bando_id), stato, stato, mancante_dal),
    ).fetchone()[0])


def chiudi_call(db, call_id: str) -> None:
    db.execute("update public.partner_calls set stato = 'chiusa_annullata', chiusa_at = now(), "
               "motivo_chiusura = 'creatore_annullata' where id = %s", (call_id,))


def rimappa(db, doppione: int, master: int) -> dict:
    """Passo reale della 0043 (scrive)."""
    return db.execute(
        "select public.fn_rimappa_bando_fuso(p_doppione => %s::integer, "
        "p_master => %s::integer, p_master_slug => %s::text, p_prova => false)",
        (doppione, master, slug(master)),
    ).fetchone()[0]


def ripristina(db, doppione=DOPPIONE, *, prova: bool) -> dict:
    """Ripristino manuale (firma della 0044)."""
    return db.execute(
        "select public.fn_ripristina_rimappatura(p_doppione => %s::integer, "
        "p_dal => '-infinity'::timestamptz, p_prova => %s::boolean)",
        (doppione, prova),
    ).fetchone()[0]


def automatico(db, doppione=DOPPIONE, *, prova: bool) -> dict:
    """Percorso automatico, come lo chiama il passo del backend."""
    return db.execute(
        "select public.fn_ripristina_rimappatura_voci(p_doppione => %s::integer, "
        "p_dal => '-infinity'::timestamptz, p_prova => %s::boolean, p_automatico => true)",
        (doppione, prova),
    ).fetchone()[0]


def doppioni(db) -> list[int]:
    return db.execute("select public.fn_doppioni_rimappati()").fetchone()[0]


def riga(db, tabella: str, id_) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            sql.SQL("select * from public.{} where id = %s").format(sql.Identifier(tabella)),
            (id_,),
        ).fetchone()


def elimina(db, tabella: str, id_) -> None:
    db.execute(sql.SQL("delete from public.{} where id = %s").format(sql.Identifier(tabella)),
               (id_,))


def voci(db, azione: str) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select id, actor_id, target_user_id, family_parent_id, payload "
            "from public.audit_log where action = %s order by id", (azione,),
        ).fetchall()


def impronta(db, tabelle=TABELLE + ("audit_log",)) -> dict:
    """md5 del contenuto di ciascuna tabella (ordine canonico delle righe)."""
    return {
        tabella: db.execute(
            sql.SQL("select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) "
                    "from public.{} t").format(sql.Identifier(tabella))
        ).fetchone()[0]
        for tabella in tabelle
    }


@pytest.fixture()
def catena(db):
    """D fuso in M, poi M fuso in M2: preferito e scadenza di u e la call di A
    (con bando_mancante_dal) nati su D stanno su M2."""
    u = new_user(db)
    _, a = azienda(db)
    sb, ce = salva(db, u, DOPPIONE), evento(db, u, DOPPIONE)
    pc = inserisci_call(db, a, DOPPIONE, mancante_dal=True)
    uno = {"saved_bandi": {"aggiornate": 1, "eliminate": 0},
           "calendar_events": {"aggiornati": 1, "eliminati": 0, "convertiti": 0},
           "partner_calls": {"aggiornate": 1, "in_collisione": 0}}
    assert rimappa(db, DOPPIONE, MASTER) == uno
    assert rimappa(db, MASTER, MASTER2) == uno
    for tabella, id_ in (("saved_bandi", sb), ("calendar_events", ce), ("partner_calls", pc)):
        assert riga(db, tabella, id_)["bando_id"] == MASTER2, tabella
    return {"u": u, "a": a, "saved_bandi": sb, "calendar_events": ce, "partner_calls": pc}


def tornate_a_d(db, c) -> None:
    for tabella in TABELLE:
        dopo = riga(db, tabella, c[tabella])
        assert (dopo["bando_id"], dopo["bando_slug"]) == (DOPPIONE, slug(DOPPIONE)), tabella
    assert riga(db, "partner_calls", c["partner_calls"])["bando_mancante_dal"] is None


# ----------------------------------------------------------------- catene


class TestCatena:
    @pytest.mark.parametrize("percorso", ["automatico", "manuale"])
    def test_catena_poi_separazione_le_righe_tornano_a_d(self, db, catena, percorso):
        if percorso == "automatico":
            atteso = auto(sb=(1, 0, 0), ce=(1, 0, 0), pc=(1, 0, 0))
            chiama = lambda prova: automatico(db, prova=prova)  # noqa: E731
        else:
            atteso = conteggi(sb=(1, 0), ce=(1, 0), pc=(1, 0))
            chiama = lambda prova: ripristina(db, prova=prova)  # noqa: E731
        prima = impronta(db)
        assert chiama(True) == atteso
        assert impronta(db) == prima
        assert chiama(False) == atteso
        tornate_a_d(db, catena)

        origine = {(v["payload"]["doppione"], v["payload"]["tabella"]): v["id"]
                   for v in voci(db, ORIGINE)}
        scritte = voci(db, RIPRISTINO)
        assert len(scritte) == 6
        for v in scritte:
            p = v["payload"]
            assert v["actor_id"] is None
            if p["doppione"] == DOPPIONE:
                # la voce di D: lo stato sovrascritto è quello sull'ultimo master
                assert (p["operazione"], p["master"]) == ("aggiornata", MASTER)
                assert p["voce"] == origine[(DOPPIONE, p["tabella"])]
                assert p["prima"]["bando_id"] == MASTER2
                assert p["prima"]["bando_slug"] == slug(MASTER2)
            else:
                # l'anello M → M2 chiuso dal ripristino di D
                assert (p["operazione"], p["doppione"], p["master"]) == (
                    "catena", MASTER, MASTER2)
                assert p["voce"] == origine[(MASTER, p["tabella"])]
                assert p["tramite"] == origine[(DOPPIONE, p["tabella"])]
                assert p["prima"] is None
        assert doppioni(db) == []

        # idempotente: nessuna voce aperta, nessuna scrittura
        prima = impronta(db)
        vuoto = auto() if percorso == "automatico" else conteggi()
        assert chiama(False) == vuoto
        assert impronta(db) == prima

    def test_la_separazione_successiva_di_m_non_riprova_l_anello(self, db, catena):
        automatico(db, prova=False)
        prima = impronta(db)
        assert automatico(db, MASTER, prova=False) == auto()
        assert ripristina(db, MASTER, prova=False) == conteggi()
        assert impronta(db) == prima
        tornate_a_d(db, catena)

    def test_m_separato_prima_di_d(self, db, catena):
        """M si separa per primo: le righe tornano su M; poi D si separa e
        la catena si ferma su M (anello già ripristinato)."""
        assert automatico(db, MASTER, prova=False) == auto(sb=(1, 0, 0), ce=(1, 0, 0),
                                                           pc=(1, 0, 0))
        for tabella in TABELLE:
            assert riga(db, tabella, catena[tabella])["bando_id"] == MASTER
        assert automatico(db, prova=False) == auto(sb=(1, 0, 0), ce=(1, 0, 0), pc=(1, 0, 0))
        tornate_a_d(db, catena)
        assert doppioni(db) == []
        assert [v["payload"]["operazione"] for v in voci(db, RIPRISTINO)].count("catena") == 0

    def test_catena_lunga(self, db):
        u = new_user(db)
        sid = salva(db, u, DOPPIONE)
        for doppione, master in ((DOPPIONE, MASTER), (MASTER, MASTER2), (MASTER2, MASTER3)):
            assert rimappa(db, doppione, master)["saved_bandi"]["aggiornate"] == 1
        assert automatico(db, prova=True) == auto(sb=(1, 0, 0))
        assert automatico(db, prova=False) == auto(sb=(1, 0, 0))
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE
        catena_ = [v["payload"] for v in voci(db, RIPRISTINO)
                   if v["payload"]["operazione"] == "catena"]
        assert [(p["doppione"], p["master"]) for p in catena_] == [
            (MASTER, MASTER2), (MASTER2, MASTER3)]
        assert doppioni(db) == []

    def test_catena_che_torna_al_doppione(self, db):
        """D fuso in M, poi M fuso in D: la riga è già su D, il ripristino lo
        conferma e chiude l'anello, senza conflitto con sé stessa."""
        u = new_user(db)
        sid = salva(db, u, DOPPIONE)
        rimappa(db, DOPPIONE, MASTER)
        rimappa(db, MASTER, DOPPIONE)
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE
        assert automatico(db, prova=False) == auto(sb=(1, 0, 0))
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE
        assert doppioni(db) == []

    def test_la_riga_che_sostiene_un_eliminazione_sul_master_finale(self, db):
        """u aveva salvato D e TERZO. D → M → M2 porta la riga di D su M2; poi
        TERZO → M2 elimina quella di TERZO (c'era già M2). Riportare indietro la
        riga di D lascerebbe u senza nessuno dei due: resta su M2 finché TERZO
        non è ripristinato."""
        u = new_user(db)
        sid, terzo = salva(db, u, DOPPIONE), salva(db, u, TERZO)
        rimappa(db, DOPPIONE, MASTER)
        rimappa(db, MASTER, MASTER2)
        assert rimappa(db, TERZO, MASTER2)["saved_bandi"]["eliminate"] == 1
        assert automatico(db, prova=False) == auto(sb=(0, 1, 0))  # nessun marcatore
        assert riga(db, "saved_bandi", sid)["bando_id"] == MASTER2
        assert voci(db, MARCATORE) == []
        assert automatico(db, TERZO, prova=False) == auto(sb=(1, 0, 0))
        assert automatico(db, prova=False) == auto(sb=(1, 0, 0))
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE
        assert riga(db, "saved_bandi", terzo)["bando_id"] == TERZO

    def test_un_anello_che_ha_eliminato_la_riga_ferma_la_catena(self, db):
        """M → M2 ha eliminato la riga nata su D (u aveva già M2): resta un
        conflitto da vedere a mano, mai definitivo."""
        u = new_user(db)
        sid = salva(db, u, DOPPIONE)
        salva(db, u, MASTER2)
        rimappa(db, DOPPIONE, MASTER)
        assert rimappa(db, MASTER, MASTER2)["saved_bandi"]["eliminate"] == 1
        assert automatico(db, prova=False) == auto(sb=(0, 1, 0))
        assert riga(db, "saved_bandi", sid) is None
        assert voci(db, MARCATORE) == [] and DOPPIONE in doppioni(db)


# ----------------------------------------------------------------- conflitti definitivi


def _preferito(db, u):
    rid = salva(db, u, DOPPIONE)
    assert rimappa(db, DOPPIONE, MASTER)["saved_bandi"]["aggiornate"] == 1
    return "saved_bandi", rid, "aggiornata", lambda: salva(db, u, DOPPIONE)


def _scadenza(db, u):
    rid = evento(db, u, DOPPIONE)
    assert rimappa(db, DOPPIONE, MASTER)["calendar_events"]["aggiornati"] == 1
    return "calendar_events", rid, "aggiornata", lambda: evento(db, u, DOPPIONE)


def _preferito_eliminato(db, u):
    rid = salva(db, u, DOPPIONE)
    salva(db, u, MASTER)
    assert rimappa(db, DOPPIONE, MASTER)["saved_bandi"]["eliminate"] == 1
    return "saved_bandi", rid, "eliminata", lambda: salva(db, u, DOPPIONE)


def _evento_convertito(db, u):
    rid = evento(db, u, DOPPIONE, note="Portare i documenti")
    evento(db, u, MASTER)
    assert rimappa(db, DOPPIONE, MASTER)["calendar_events"]["convertiti"] == 1
    return "calendar_events", rid, "convertita", lambda: evento(db, u, DOPPIONE)


SCENARI = {"preferito": _preferito, "scadenza": _scadenza,
           "preferito_eliminato": _preferito_eliminato, "evento_convertito": _evento_convertito}


class TestConflittiDefinitivi:
    @pytest.mark.parametrize("scenario", list(SCENARI))
    def test_doppione_rimesso_poi_tolto_nessun_ripristino_automatico(self, db, scenario):
        """Dopo la separazione l'utente rimette il doppione (conflitto), poi lo
        toglie: il percorso automatico non riporta la riga contro la sua
        scelta."""
        u = new_user(db)
        tabella, rid, operazione, rimetti = SCENARI[scenario](db, u)
        nuova = rimetti()
        chiave = "sb" if tabella == "saved_bandi" else "ce"
        atteso = auto(**{chiave: (0, 1, 1)})

        prima = impronta(db)
        assert automatico(db, prova=True) == atteso
        assert impronta(db) == prima  # la prova non marca
        assert doppioni(db) == [DOPPIONE]

        assert automatico(db, prova=False) == atteso
        [origine] = voci(db, ORIGINE)
        [marcatore] = voci(db, MARCATORE)
        assert marcatore["actor_id"] is None
        assert marcatore["target_user_id"] == origine["target_user_id"]
        assert marcatore["payload"] == {
            "tabella": tabella, "id": rid, "operazione": operazione,
            "company_profile_id": None, "doppione": DOPPIONE, "master": MASTER,
            "voce": origine["id"], "motivo": "doppione_nello_stesso_ambito"}
        assert voci(db, RIPRISTINO) == []
        assert doppioni(db) == []  # il passo non lo cerca più

        # l'utente toglie il doppione rimesso: nessun ripristino automatico
        elimina(db, tabella, nuova)
        prima = impronta(db)
        assert automatico(db, prova=True) == auto()
        assert automatico(db, prova=False) == auto()
        assert impronta(db) == prima

    def test_il_percorso_manuale_ignora_i_marcatori(self, db):
        """La procedura manuale decide l'operatore: non scrive marcatori e può
        ripristinare una voce marcata."""
        u = new_user(db)
        _, rid, _, rimetti = _preferito(db, u)
        nuova = rimetti()
        assert ripristina(db, prova=False) == conteggi(sb=(0, 1))
        assert voci(db, MARCATORE) == [] and doppioni(db) == [DOPPIONE]
        assert automatico(db, prova=False) == auto(sb=(0, 1, 1))
        elimina(db, "saved_bandi", nuova)
        assert ripristina(db, prova=True) == conteggi(sb=(1, 0))
        assert ripristina(db, prova=False) == conteggi(sb=(1, 0))
        assert riga(db, "saved_bandi", rid)["bando_id"] == DOPPIONE
        assert len(voci(db, RIPRISTINO)) == 1 and doppioni(db) == []

    def test_gli_altri_conflitti_restano_ritentati(self, db):
        """Sullo stesso doppione un preferito rimesso (definitivo) e una call
        in collisione (ritentata): il doppione resta nell'elenco per la call, il
        percorso automatico salta la voce marcata e ripristina la call appena
        la collisione sparisce."""
        u = new_user(db)
        _, rid, _, rimetti = _preferito(db, u)
        _, a = azienda(db)
        call = inserisci_call(db, a, DOPPIONE)
        rimappa(db, DOPPIONE, MASTER)
        nuova_call = inserisci_call(db, a, DOPPIONE, stato="bozza")
        nuovo = rimetti()

        assert automatico(db, prova=False) == auto(sb=(0, 1, 1), pc=(0, 1, 0))
        assert len(voci(db, MARCATORE)) == 1
        assert doppioni(db) == [DOPPIONE]

        elimina(db, "saved_bandi", nuovo)
        assert automatico(db, prova=False) == auto(pc=(0, 1, 0))
        assert riga(db, "saved_bandi", rid)["bando_id"] == MASTER

        chiudi_call(db, nuova_call)
        assert automatico(db, prova=False) == auto(pc=(1, 0, 0))
        assert riga(db, "partner_calls", call)["bando_id"] == DOPPIONE
        assert riga(db, "saved_bandi", rid)["bando_id"] == MASTER
        assert len(voci(db, MARCATORE)) == 1 and doppioni(db) == []

    def test_conflitti_non_definitivi_senza_marcatore(self, db):
        """Riga cancellata dall'utente o utente non più esistente: conflitti
        ritentati, nessun marcatore."""
        u, w = new_user(db), new_user(db)
        sid = salva(db, u, DOPPIONE)
        salva(db, w, DOPPIONE)
        salva(db, w, MASTER)
        rimappa(db, DOPPIONE, MASTER)
        elimina(db, "saved_bandi", sid)
        db.execute("delete from auth.users where id = %s", (w,))
        assert automatico(db, prova=False) == auto(sb=(0, 2, 0))
        assert voci(db, MARCATORE) == [] and doppioni(db) == [DOPPIONE]


# ----------------------------------------------------------------- flusso reale


class TestFlussoReale:
    def test_dati_scritti_con_la_0044_e_la_0045_poi_la_0046(self, db):
        """Definizioni della 0044 e della 0045 (create or replace), dati scritti
        con quelle, poi la 0046: la catena prima in conflitto si ripristina, il
        preferito rimesso diventa definitivo e non torna quando è tolto."""
        db.execute(SQL_0044)
        db.execute(SQL_0045)
        u, v = new_user(db), new_user(db)
        sid = salva(db, u, DOPPIONE)
        rimappa(db, DOPPIONE, MASTER)
        rimappa(db, MASTER, MASTER2)
        rid = salva(db, v, ALTRO_DOPPIONE)
        rimappa(db, ALTRO_DOPPIONE, ALTRO_MASTER)
        nuovo = salva(db, v, ALTRO_DOPPIONE)
        # 0044: la catena è in conflitto; 0045: tutti i doppioni nell'elenco
        assert ripristina(db, prova=False) == conteggi(sb=(0, 1))
        assert ripristina(db, ALTRO_DOPPIONE, prova=False) == conteggi(sb=(0, 1))
        assert doppioni(db) == [MASTER, DOPPIONE, ALTRO_DOPPIONE]

        db.execute(SQL_0046)
        assert automatico(db, prova=False) == auto(sb=(1, 0, 0))
        assert riga(db, "saved_bandi", sid)["bando_id"] == DOPPIONE
        assert automatico(db, ALTRO_DOPPIONE, prova=False) == auto(sb=(0, 1, 1))
        assert doppioni(db) == []
        elimina(db, "saved_bandi", nuovo)
        assert automatico(db, ALTRO_DOPPIONE, prova=False) == auto()
        assert riga(db, "saved_bandi", rid)["bando_id"] == ALTRO_MASTER


# ----------------------------------------------------------------- parametri


class TestParametri:
    @pytest.mark.parametrize("doppione,prova,automatico_", [
        (None, False, True), (0, False, True), (DOPPIONE, None, True), (DOPPIONE, False, None),
    ])
    def test_parametri_non_validi(self, db, catena, doppione, prova, automatico_):
        prima = impronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "select public.fn_ripristina_rimappatura_voci(%s::integer, "
                "'-infinity'::timestamptz, %s::boolean, %s::boolean)",
                (doppione, prova, automatico_),
            )
        assert detail_of(exc) == "parametri_non_validi"
        assert impronta(db) == prima

    def test_dal_filtra_le_voci_come_la_0044(self, db, catena):
        dopo = "infinity"
        assert db.execute(
            "select public.fn_ripristina_rimappatura_voci(%s, %s::timestamptz, false, true)",
            (DOPPIONE, dopo),
        ).fetchone()[0] == auto()


# ----------------------------------------------------------------- sicurezza


FUNZIONI = {
    "fn_ripristina_rimappatura_voci": (
        "fn_ripristina_rimappatura_voci(integer,timestamp with time zone,boolean,boolean)",
        "jsonb", "v"),
    "fn_ripristina_rimappatura": (
        "fn_ripristina_rimappatura(integer,timestamp with time zone,boolean)", "jsonb", "v"),
    "fn_doppioni_rimappati": ("fn_doppioni_rimappati()", "integer[]", "s"),
}


def controlla_protezioni(db) -> None:
    for nome, (firma_attesa, ritorno_atteso, volatilita_attesa) in FUNZIONI.items():
        righe = db.execute(
            """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                      coalesce(p.proacl::text, ''), p.oid::regprocedure::text,
                      pg_get_function_result(p.oid), p.provolatile
               from pg_proc p join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'public' and p.proname = %s""",
            (nome,),
        ).fetchall()
        assert len(righe) == 1, nome
        oid, secdef, config, acl, firma, ritorno, volatilita = righe[0]
        assert firma == firma_attesa
        assert ritorno == ritorno_atteso
        assert volatilita == volatilita_attesa, nome
        assert secdef is True, nome
        assert "search_path=public" in config, nome
        assert acl and not re.search(r"[{,]=X", acl), f"PUBLIC esegue {nome} ({acl})"
        for ruolo in ("anon", "authenticated"):
            assert not db.execute(
                "select has_function_privilege(%s, %s::oid, 'execute')", (ruolo, oid)
            ).fetchone()[0], (nome, ruolo)


class TestSicurezza0046:
    def test_firme_e_protezioni(self, db):
        controlla_protezioni(db)

    def test_default_di_p_prova_resta_true(self, db):
        argomenti = db.execute(
            "select pg_get_function_arguments('public.fn_ripristina_rimappatura"
            "(integer,timestamptz,boolean)'::regprocedure)"
        ).fetchone()[0]
        assert argomenti == ("p_doppione integer, p_dal timestamp with time zone, "
                             "p_prova boolean DEFAULT true")

    def test_revoche_scritte_nel_file(self):
        for nome in FUNZIONI:
            assert re.search(
                rf"^revoke execute on function public\.{nome}\([^)]*\)\s+"
                r"from public, anon, authenticated;",
                SQL_0046, re.M,
            ), nome

    def test_additiva(self):
        corpo = re.sub(r"^\s*--.*$", "", SQL_0046, flags=re.M).lower()
        for nome in FUNZIONI:
            assert f"create or replace function public.{nome}(" in corpo, nome
        for vietato in ("alter table", "drop ", "create table", "create index", "truncate"):
            assert vietato not in corpo, vietato

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_ripristina_rimappatura_voci(7001, '-infinity', true, true)",
        "select public.fn_ripristina_rimappatura(7001, '-infinity')",
        "select public.fn_doppioni_rimappati()",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, ruolo, chiamata):
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_esegue_il_percorso_automatico(self, db, catena):
        """Solo EXECUTE, nessun privilegio sulle tabelle: SECURITY DEFINER."""
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            db.execute(f"grant execute on function public."
                       f"{FUNZIONI['fn_ripristina_rimappatura_voci'][0]} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                assert automatico(db, prova=False) == auto(sb=(1, 0, 0), ce=(1, 0, 0),
                                                           pc=(1, 0, 0))
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    db.execute("select 1 from public.audit_log")
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        tornate_a_d(db, catena)


class TestApplicabilita:
    def test_applicabile_piu_volte(self, db, catena):
        """La 0046 è già nel template: la si riapplica due volte (una con
        l'involucro di docs/deploy.md) senza errori e senza cambiare nulla."""
        db.execute(SQL_0046)
        db.execute("begin; set local lock_timeout = '5s'; " + SQL_0046 + "\ncommit;")
        controlla_protezioni(db)
        assert automatico(db, prova=False) == auto(sb=(1, 0, 0), ce=(1, 0, 0), pc=(1, 0, 0))
        tornate_a_d(db, catena)
