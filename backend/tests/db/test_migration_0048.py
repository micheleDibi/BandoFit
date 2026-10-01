"""Test della migration 0048 (colonna bando_stato di calendar_events: stato del
bando all'ultimo allineamento della scadenza).

Coprono: la colonna dopo la 0047, text, nullable, senza default, con il
commento e senza CHECK; il file fatto solo dell'alter additivo e del commento;
la migration riapplicata (anche con l'involucro di docs/deploy.md) senza
cambiare nulla; eventi personali e scadenze con lo stato NULL finché non è
noto e con qualunque valore del catalogo; la rimappatura dei fusi e il suo
ripristino che funzionano con la colonna nuova; il rollback in coda al file e
la migration riapplicata dopo.
Ogni test riceve un database fresco clonato dal template.
"""

import re
import uuid
from pathlib import Path

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
FILE_0048 = next(MIGRAZIONI.glob("0048_*.sql"))
SQL_0048 = FILE_0048.read_text(encoding="utf-8")

DOPPIONE = 8001
MASTER = 8000


# ----------------------------------------------------------------- helper


def new_user(db) -> str:
    uid = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@test.it"))
    return uid


def scadenza(db, user: str, bando_id: int, stato: str | None = None) -> str:
    return str(db.execute(
        "insert into public.calendar_events (user_id, titolo, data, tipo, bando_id, bando_slug, "
        "bando_stato) values (%s, %s, current_date + 20, 'bando', %s, %s, %s) returning id",
        (user, f"Scadenza del bando {bando_id}", bando_id, f"bando-{bando_id}", stato),
    ).fetchone()[0])


def colonna(db) -> tuple | None:
    """(tipo, nullable, default) di calendar_events.bando_stato, None se assente."""
    return db.execute(
        """select data_type, is_nullable, column_default
           from information_schema.columns
           where table_schema = 'public' and table_name = 'calendar_events'
             and column_name = 'bando_stato'"""
    ).fetchone()


def commento(db) -> str | None:
    return db.execute(
        """select col_description('public.calendar_events'::regclass, a.attnum)
           from pg_attribute a
           where a.attrelid = 'public.calendar_events'::regclass
             and a.attname = 'bando_stato' and not a.attisdropped"""
    ).fetchone()[0]


def vincoli(db) -> dict[str, str]:
    righe = db.execute(
        """select conname, pg_get_constraintdef(oid) from pg_constraint
           where conrelid = 'public.calendar_events'::regclass"""
    ).fetchall()
    return dict(righe)


def senza_commenti(testo: str) -> str:
    return re.sub(r"--.*$", "", testo, flags=re.M)


def rollback_0048() -> list[str]:
    """Le istruzioni del rollback documentato in coda al file."""
    coda = SQL_0048.split("ROLLBACK 0048", 1)[1]
    return re.findall(r"^--\s+(alter table .+;)$", coda, re.M)


# ----------------------------------------------------------------- schema


class TestColonna:
    def test_dopo_la_0047(self):
        numerate = sorted(p.name for p in MIGRAZIONI.glob("*.sql"))
        assert [n for n in numerate if n.startswith("0048_")] == [FILE_0048.name]
        precedente = numerate[numerate.index(FILE_0048.name) - 1]
        assert precedente.startswith("0047_")

    def test_text_nullable_senza_default_con_commento(self, db):
        assert colonna(db) == ("text", "YES", None)
        assert "stato_effettivo" in commento(db)

    def test_nessun_vincolo_sulla_colonna(self, db):
        assert not [nome for nome, definizione in vincoli(db).items()
                    if "bando_stato" in definizione]

    def test_solo_alter_additivo_e_commento(self):
        istruzioni = [s.strip() for s in senza_commenti(SQL_0048).split(";") if s.strip()]
        assert len(istruzioni) == 2
        assert re.fullmatch(
            r"alter table public\.calendar_events\s+add column if not exists bando_stato text",
            istruzioni[0])
        assert istruzioni[1].startswith("comment on column public.calendar_events.bando_stato is")


# ----------------------------------------------------------------- applicabilità


class TestApplicabilita:
    def test_applicabile_piu_volte(self, db):
        """La 0048 è già nel template: riapplicata due volte (una con
        l'involucro di docs/deploy.md) non cambia nulla e non tocca i dati."""
        utente = new_user(db)
        evento = scadenza(db, utente, MASTER, "sospeso")
        prima = (colonna(db), commento(db), vincoli(db))
        db.execute(SQL_0048)
        db.execute("begin; set local lock_timeout = '5s'; " + SQL_0048 + "\ncommit;")
        assert (colonna(db), commento(db), vincoli(db)) == prima
        assert db.execute("select bando_stato from public.calendar_events where id = %s",
                          (evento,)).fetchone()[0] == "sospeso"

    def test_rollback_poi_riapplicata(self, db):
        utente = new_user(db)
        scadenza(db, utente, MASTER, "aperto")
        istruzioni = rollback_0048()
        assert istruzioni == [
            "alter table public.calendar_events drop column if exists bando_stato;"]
        db.execute("begin; " + " ".join(istruzioni) + " commit;")
        assert colonna(db) is None
        # senza la colonna il calendario funziona come prima
        db.execute(
            "insert into public.calendar_events (user_id, titolo, data) "
            "values (%s, 'Riunione', current_date)", (utente,))

        db.execute("begin; set local lock_timeout = '5s'; " + SQL_0048 + "\ncommit;")
        assert colonna(db) == ("text", "YES", None)
        assert db.execute(
            "select count(*) from public.calendar_events where bando_stato is not null"
        ).fetchone()[0] == 0


# ----------------------------------------------------------------- dati


class TestValori:
    def test_null_finche_non_e_noto(self, db):
        utente = new_user(db)
        personale = db.execute(
            "insert into public.calendar_events (user_id, titolo, data) "
            "values (%s, 'Riunione', current_date) returning bando_stato", (utente,)
        ).fetchone()[0]
        assert personale is None
        evento = scadenza(db, utente, MASTER)
        assert db.execute("select bando_stato from public.calendar_events where id = %s",
                          (evento,)).fetchone()[0] is None

    def test_qualunque_valore_del_catalogo(self, db):
        """Nessun CHECK: anche uno stato che il catalogo introdurrà domani."""
        utente = new_user(db)
        for bando, stato in enumerate(("aperto", "sospeso", "revocato", "stato_nuovo"), 9000):
            evento = scadenza(db, utente, bando, stato)
            db.execute("update public.calendar_events set bando_stato = 'chiuso' where id = %s",
                       (evento,))
            assert db.execute("select bando_stato from public.calendar_events where id = %s",
                              (evento,)).fetchone()[0] == "chiuso"


class TestRimappatura:
    def test_rimappatura_e_ripristino_con_la_colonna(self, db):
        """La scadenza del doppione, eliminata dalla rimappatura (c'è già
        quella del master, senza note), porta lo stato nella riga intera
        salvata in audit_log; il ripristino la reinserisce con lo stato NULL,
        che il riallineamento successivo riscrive."""
        utente = new_user(db)
        doppione = scadenza(db, utente, DOPPIONE, "aperto")
        scadenza(db, utente, MASTER, "aperto")

        esito = db.execute(
            "select public.fn_rimappa_bando_fuso(p_doppione => %s::integer, "
            "p_master => %s::integer, p_master_slug => %s::text, p_prova => false)",
            (DOPPIONE, MASTER, f"bando-{MASTER}"),
        ).fetchone()[0]
        assert esito["calendar_events"]["eliminati"] == 1
        prima = db.execute(
            "select payload->'prima' from public.audit_log "
            "where action = 'catalogo.rimappatura_fuso' and payload->>'id' = %s", (doppione,)
        ).fetchone()[0]
        assert prima["bando_stato"] == "aperto"

        ripristino = db.execute(
            "select public.fn_ripristina_rimappatura_voci(p_doppione => %s::integer, "
            "p_dal => '-infinity'::timestamptz, p_prova => false, p_automatico => true)",
            (DOPPIONE,),
        ).fetchone()[0]
        assert ripristino["calendar_events"]["ripristinate"] == 1
        assert db.execute(
            "select bando_id, bando_stato from public.calendar_events where id = %s", (doppione,)
        ).fetchone() == (DOPPIONE, None)
