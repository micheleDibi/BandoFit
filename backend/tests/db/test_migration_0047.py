"""Test della migration 0047 (indici parziali di audit_log per la rimappatura
dei fusi; solo prestazioni, complemento della 0043-0046).

Coprono: i quattro indici dopo la 0043-0046, con la definizione attesa e
validi, accanto a quelli della 0003; il file fatto solo di create index if not
exists parziali su audit_log, senza concurrently; la migration riapplicata
(anche con l'involucro di docs/deploy.md) senza ricostruire nulla; il rollback
in coda al file, il ripristino che funziona uguale senza indici e la
migration riapplicata dopo. I piani delle query DENTRO le funzioni, letti con
auto_explain su un registro con un volume realistico: con le scansioni
sequenziali disabilitate ogni lettura di audit_log passa da un indice e ogni
indice è usato con la sua espressione (l'indice serve solo se l'espressione
coincide con quella delle funzioni); con le impostazioni di default le
letture per voce restano sugli indici anche dopo le prime esecuzioni (piani
in cache). Stessi risultati della 0046.
Ogni test riceve un database fresco clonato dal template.
"""

import json
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
FILE_0047 = next(MIGRAZIONI.glob("0047_*.sql"))
SQL_0047 = FILE_0047.read_text(encoding="utf-8")

DOPPIONE = 7001
MASTER = 7000
MASTER2 = 7100
TERZO = 7003  # un altro doppione di MASTER2
UTENTI = 4  # 8 voci del doppione: più delle 5 esecuzioni con piano dedicato
ALTRI_UTENTI = 1000  # utenti con MASTER fra i preferiti (volume)

INDICI = {
    "audit_log_rimappatura_doppione_idx":
        "CREATE INDEX audit_log_rimappatura_doppione_idx ON public.audit_log USING btree "
        "(((payload -> 'doppione'::text)), id) "
        "WHERE (action = 'catalogo.rimappatura_fuso'::text)",
    "audit_log_rimappatura_riga_idx":
        "CREATE INDEX audit_log_rimappatura_riga_idx ON public.audit_log USING btree "
        "(((payload -> 'id'::text)), ((payload -> 'doppione'::text))) "
        "WHERE (action = 'catalogo.rimappatura_fuso'::text)",
    "audit_log_rimappatura_master_idx":
        "CREATE INDEX audit_log_rimappatura_master_idx ON public.audit_log USING btree "
        "(((payload -> 'master'::text)), ((payload ->> 'operazione'::text))) "
        "WHERE (action = 'catalogo.rimappatura_fuso'::text)",
    "audit_log_rimappatura_voce_idx":
        "CREATE INDEX audit_log_rimappatura_voce_idx ON public.audit_log USING btree "
        "(((payload -> 'voce'::text))) "
        "WHERE (action = ANY (ARRAY['catalogo.ripristino_rimappatura'::text, "
        "'catalogo.conflitto_definitivo'::text]))",
}
INDICI_0003 = {"audit_log_pkey", "audit_log_family_idx", "audit_log_target_idx"}

# Espressione della chiave di ciascun indice, come compare nell'Index Cond.
CHIAVI = {
    "audit_log_rimappatura_doppione_idx": "(payload -> 'doppione'::text) = ",
    "audit_log_rimappatura_riga_idx": "(payload -> 'id'::text) = ",
    "audit_log_rimappatura_master_idx": "(payload -> 'master'::text) = ",
    "audit_log_rimappatura_voce_idx": "(payload -> 'voce'::text) = ",
}

# Le letture delle funzioni (riconosciute dal testo della query) e gli indici
# da cui possono partire. L'anello della catena filtra per riga e per doppione:
# per un master con molte voci il planner sceglie l'indice per riga, per un
# doppione senza voci quello per doppione.
LETTURE = {
    "ciclo principale": ("order by v.id desc", ("audit_log_rimappatura_doppione_idx",)),
    "anello della catena": ("a.id > coalesce(", ("audit_log_rimappatura_riga_idx",
                                                 "audit_log_rimappatura_doppione_idx")),
    "riga che sostiene": ("v_sostiene :=", ("audit_log_rimappatura_master_idx",)),
}
CORPO_DOPPIONI = "array_agg(d.doppione"


# ----------------------------------------------------------------- helper


def slug(bando: int) -> str:
    return f"bando-{bando}"


def auto(sb=(0, 0, 0), ce=(0, 0, 0), pc=(0, 0, 0)) -> dict:
    """Ritorno del percorso automatico: (ripristinate, in_conflitto, definitivi)."""
    return {
        tabella: {"ripristinate": rip, "in_conflitto": con, "definitivi": dfn}
        for tabella, (rip, con, dfn) in zip(
            ("saved_bandi", "calendar_events", "partner_calls"), (sb, ce, pc), strict=True)
    }


def new_user(db) -> str:
    uid = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@test.it"))
    return uid


def salva(db, user: str, bando_id: int) -> str:
    return str(db.execute(
        "insert into public.saved_bandi (user_id, bando_id, bando_slug, bando_titolo, "
        "data_scadenza, stato_bando) values (%s, %s, %s, %s, current_date + 20, 'aperto') "
        "returning id",
        (user, bando_id, slug(bando_id), f"Titolo del bando {bando_id}"),
    ).fetchone()[0])


def evento(db, user: str, bando_id: int) -> str:
    return str(db.execute(
        "insert into public.calendar_events (user_id, titolo, data, tipo, bando_id, bando_slug) "
        "values (%s, %s, current_date + 20, 'bando', %s, %s) returning id",
        (user, f"Scadenza del bando {bando_id}", bando_id, slug(bando_id)),
    ).fetchone()[0])


def rimappa(db, doppione: int, master: int) -> dict:
    """Passo reale della 0043 (scrive)."""
    return db.execute(
        "select public.fn_rimappa_bando_fuso(p_doppione => %s::integer, "
        "p_master => %s::integer, p_master_slug => %s::text, p_prova => false)",
        (doppione, master, slug(master)),
    ).fetchone()[0]


def automatico(db, doppione=DOPPIONE, *, prova: bool = False) -> dict:
    """Percorso automatico, come lo chiama il passo del backend."""
    return db.execute(
        "select public.fn_ripristina_rimappatura_voci(p_doppione => %s::integer, "
        "p_dal => '-infinity'::timestamptz, p_prova => %s::boolean, p_automatico => true)",
        (doppione, prova),
    ).fetchone()[0]


def doppioni(db) -> list[int]:
    return db.execute("select public.fn_doppioni_rimappati()").fetchone()[0]


def indici(db) -> dict[str, tuple[int, str, bool, bool]]:
    """Indici di audit_log: nome → (oid, definizione, valido, pronto)."""
    righe = db.execute(
        """select c.relname, c.oid::bigint, pg_get_indexdef(c.oid), i.indisvalid, i.indisready
           from pg_index i
           join pg_class c on c.oid = i.indexrelid
           where i.indrelid = 'public.audit_log'::regclass"""
    ).fetchall()
    return {nome: (oid, definizione, valido, pronto)
            for nome, oid, definizione, valido, pronto in righe}


def senza_commenti(testo: str) -> str:
    return re.sub(r"--.*$", "", testo, flags=re.M)


def rollback_0047() -> list[str]:
    """Le istruzioni del rollback documentato in coda al file."""
    coda = SQL_0047.split("ROLLBACK 0047", 1)[1]
    return re.findall(r"^--\s+(drop index if exists [\w.]+;)$", coda, re.M)


def prepara_scenario(db) -> dict:
    """Per ciascun utente: preferito e scadenza di D, preferito di TERZO.
    D → M, M → M2 (catena), poi TERZO → M2 elimina il preferito di TERZO
    (c'era già M2): il preferito di D lo «sostiene» finché TERZO non torna."""
    utenti = [new_user(db) for _ in range(UTENTI)]
    righe = {u: (salva(db, u, DOPPIONE), evento(db, u, DOPPIONE), salva(db, u, TERZO))
             for u in utenti}
    rimappa(db, DOPPIONE, MASTER)
    rimappa(db, MASTER, MASTER2)
    assert rimappa(db, TERZO, MASTER2)["saved_bandi"]["eliminate"] == UTENTI
    return righe


@pytest.fixture()
def scenario(db):
    return prepara_scenario(db)


def separazioni(db, aperti: tuple[int, ...] = ()) -> None:
    """D e TERZO separati: i risultati della 0046, con o senza indici. Gli
    anelli della catena si chiudono: MASTER resta fra i doppioni rimappati
    solo con le righe di altri utenti (`aperti`)."""
    n = UTENTI
    assert automatico(db) == auto(sb=(0, n, 0), ce=(n, 0, 0))
    assert automatico(db, TERZO) == auto(sb=(n, 0, 0))
    assert automatico(db) == auto(sb=(n, 0, 0))
    assert [d for d in doppioni(db) if d in (DOPPIONE, MASTER, TERZO)] == list(aperti)


def tornate(db, righe) -> None:
    for sb, ce, terzo in righe.values():
        for tabella, id_, bando in (("saved_bandi", sb, DOPPIONE),
                                    ("calendar_events", ce, DOPPIONE),
                                    ("saved_bandi", terzo, TERZO)):
            assert db.execute(f"select bando_id from public.{tabella} where id = %s",
                              (id_,)).fetchone()[0] == bando, tabella


def volume(db) -> None:
    """Un registro con un volume realistico, prima dello scenario: altre
    azioni, voci di rimappatura di altri doppioni (una su tre eliminata), una
    su due ripristinata, con id dei bandi lontani da quelli dello scenario; e
    MASTER fra i preferiti di molti altri utenti, così la sua fusione in
    MASTER2 scrive migliaia di voci con doppione MASTER e l'anello della
    catena di una riga conviene cercarlo per riga. Con poche migliaia di voci
    il planner preferisce ancora scorrere tutto un indice parziale piccolo
    invece di cercarvi la chiave: il volume è scelto perché ogni chiave
    convenga, come su un registro che cresce."""
    db.execute(
        """insert into public.audit_log (action, payload)
           select 'consulenza.dossier_accessed', jsonb_build_object('n', g)
           from generate_series(1, 6000) g""")
    db.execute(
        """insert into public.audit_log (action, target_user_id, payload)
           select 'catalogo.rimappatura_fuso', gen_random_uuid(), jsonb_build_object(
                    'tabella', 'saved_bandi', 'id', gen_random_uuid(),
                    'operazione', case when g % 3 = 0 then 'eliminata' else 'aggiornata' end,
                    'company_profile_id', null, 'doppione', 20000 + g / 4,
                    'master', 30000 + g / 40, 'prima', '{}'::jsonb)
           from generate_series(1, 12000) g""")
    db.execute(
        """insert into public.audit_log (action, payload)
           select 'catalogo.ripristino_rimappatura',
                  jsonb_build_object('voce', v.id, 'operazione', 'aggiornata', 'prima', null)
           from public.audit_log v
           where v.action = 'catalogo.rimappatura_fuso' and v.id % 2 = 0""")
    db.execute(
        """insert into auth.users (id, email)
           select u, u::text || '@volume.test'
           from (select gen_random_uuid() as u from generate_series(1, %s)) g""",
        (ALTRI_UTENTI,))
    db.execute(
        """insert into public.saved_bandi (user_id, bando_id, bando_slug, bando_titolo,
                                           data_scadenza, stato_bando)
           select u.id, %s, %s, 'Titolo del bando', current_date + 20, 'aperto'
           from auth.users u where u.email like '%%@volume.test'""",
        (MASTER, slug(MASTER)))


@pytest.fixture()
def auto_explain(db):
    try:
        db.execute("load 'auto_explain'")
    except psycopg.Error as exc:
        pytest.skip(f"auto_explain non disponibile su questo Postgres: {exc}")


@contextmanager
def piani_annidati(db):
    """Piani (JSON) di ogni query eseguita, anche dentro le funzioni."""
    raccolti: list[dict] = []

    def raccogli(diag) -> None:
        testo = diag.message_primary or ""
        if "plan:\n" in testo:
            raccolti.append(json.loads(testo.split("plan:\n", 1)[1]))

    db.add_notice_handler(raccogli)
    for impostazione in ("auto_explain.log_level = notice", "auto_explain.log_format = json",
                         "auto_explain.log_nested_statements = on",
                         "auto_explain.log_min_duration = 0"):
        db.execute(f"set {impostazione}")
    try:
        yield raccolti
    finally:
        db.execute("set auto_explain.log_min_duration = -1")
        db.remove_notice_handler(raccogli)


def nodi(piano: dict):
    yield piano
    for figlio in piano.get("Plans", []):
        yield from nodi(figlio)


def su_audit_log(piano: dict) -> list[dict]:
    """I nodi del piano che leggono audit_log (heap o indice)."""
    return [n for n in nodi(piano["Plan"])
            if n.get("Relation Name") == "audit_log"
            or str(n.get("Index Name", "")).startswith("audit_log")]


def con_chiave(piano: dict, *ammessi: str) -> bool:
    """Il piano parte da uno degli indici ammessi, cercandovi la sua chiave."""
    return any(n.get("Index Name") in ammessi
               and CHIAVI[n["Index Name"]] in n.get("Index Cond", "")
               for n in nodi(piano["Plan"]))


def letture(piani: list[dict], frammento: str) -> list[dict]:
    """I piani di una lettura delle funzioni che toccano davvero audit_log
    (con un ramo costante falso il piano si riduce a un Result)."""
    return [p for p in piani if frammento in p["Query Text"] and su_audit_log(p)]


# ----------------------------------------------------------------- indici


class TestIndici:
    def test_definizioni_attese(self, db):
        trovati = indici(db)
        assert set(trovati) == INDICI_0003 | set(INDICI)
        for nome, definizione in INDICI.items():
            _, attuale, valido, pronto = trovati[nome]
            assert attuale == definizione, nome
            assert valido and pronto, nome

    def test_dopo_la_0046(self):
        numerate = sorted(p.name for p in MIGRAZIONI.glob("*.sql"))
        assert [n for n in numerate if n.startswith("0047_")] == [FILE_0047.name]
        precedente = numerate[numerate.index(FILE_0047.name) - 1]
        assert precedente.startswith("0046_")

    def test_solo_indici_parziali_additivi(self):
        istruzioni = [s.strip() for s in senza_commenti(SQL_0047).split(";") if s.strip()]
        nomi = []
        for istruzione in istruzioni:
            trovata = re.fullmatch(
                r"create index if not exists (audit_log_rimappatura_\w+)\s+"
                r"on public\.audit_log \(.+\)\s+where .+",
                istruzione, flags=re.S)
            assert trovata, istruzione
            nomi.append(trovata.group(1))
        assert sorted(nomi) == sorted(INDICI)
        assert "concurrently" not in senza_commenti(SQL_0047).lower()


# ----------------------------------------------------------------- applicabilità


class TestApplicabilita:
    def test_applicabile_piu_volte(self, db):
        """La 0047 è già nel template: riapplicata due volte (una con
        l'involucro di docs/deploy.md) non ricostruisce nulla."""
        prima = indici(db)
        db.execute(SQL_0047)
        db.execute("begin; set local lock_timeout = '5s'; " + SQL_0047 + "\ncommit;")
        assert indici(db) == prima

    def test_rollback_senza_indici_tutto_funziona_poi_riapplicata(self, db, scenario):
        istruzioni = rollback_0047()
        assert len(istruzioni) == len(INDICI)
        db.execute("begin; " + " ".join(istruzioni) + " commit;")
        assert set(indici(db)) == INDICI_0003
        separazioni(db)
        tornate(db, scenario)

        db.execute("begin; set local lock_timeout = '5s'; " + SQL_0047 + "\ncommit;")
        trovati = indici(db)
        assert {nome: trovati[nome][1] for nome in INDICI} == INDICI


# ----------------------------------------------------------------- piani


class TestPiani:
    def test_scansioni_sequenziali_disabilitate(self, db, auto_explain):
        """Ogni lettura di audit_log delle funzioni passa da un indice, ogni
        lettura per voce parte dal suo indice e ogni indice nuovo è usato con
        la sua espressione."""
        volume(db)
        righe = prepara_scenario(db)
        db.execute("analyze public.audit_log")
        db.execute("set enable_seqscan = off")
        with piani_annidati(db) as piani:
            separazioni(db, aperti=(MASTER,))
        tornate(db, righe)

        for piano in piani:
            for nodo in su_audit_log(piano):
                assert nodo["Node Type"] != "Seq Scan", piano["Query Text"]
        for lettura, (frammento, ammessi) in LETTURE.items():
            trovati = letture(piani, frammento)
            assert trovati, lettura
            for piano in trovati:
                assert con_chiave(piano, *ammessi), (lettura, json.dumps(piano["Plan"]))
        for indice in INDICI:
            assert any(con_chiave(p, indice) for p in piani), indice
        corpo = letture(piani, CORPO_DOPPIONI)
        assert corpo
        for piano in corpo:
            assert any(n.get("Index Name") == "audit_log_rimappatura_voce_idx"
                       for n in nodi(piano["Plan"])), json.dumps(piano["Plan"])

    def test_impostazioni_di_default(self, db, auto_explain):
        """Con le impostazioni di default e un registro realistico, le letture
        per voce partono dal loro indice a ogni esecuzione, anche dopo le
        prime cinque: la cache dei piani non passa a un piano generico, che
        con gli indici parziali sarebbe una scansione."""
        volume(db)
        prepara_scenario(db)
        db.execute("analyze public.audit_log")
        with piani_annidati(db) as piani:
            separazioni(db, aperti=(MASTER,))

        for lettura, (frammento, ammessi) in LETTURE.items():
            trovati = letture(piani, frammento)
            # il ciclo principale gira una volta per chiamata, le altre per voce
            assert len(trovati) > (2 if lettura == "ciclo principale" else 5), lettura
            for piano in trovati:
                assert con_chiave(piano, *ammessi), (lettura, json.dumps(piano["Plan"]))
