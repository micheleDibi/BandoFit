"""Test funzionali della migration 0045 (doppioni rimappati non ancora
ripristinati, complemento della 0043 e della 0044).

Coprono: fn_doppioni_rimappati senza voci ('{}', mai NULL); doppioni con voci
non ripristinate, distinti e ordinati; una voce ripristinata ne esclude una
sola (un doppione con altre voci resta); voci con un doppione non numerico e
di altre azioni ignorate; il flusso reale 0043 → 0045 → 0044 (compare dopo la
rimappatura, resta dopo la prova, sparisce dopo il ripristino); firma,
SECURITY DEFINER, search_path e revoche ai client.
Ogni test riceve un database fresco clonato dal template.
"""

import re
import uuid
from pathlib import Path

import psycopg
import pytest

MIGRAZIONI = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SQL_0045 = (MIGRAZIONI / "0045_doppioni_rimappati.sql").read_text(encoding="utf-8")

FIRMA = "fn_doppioni_rimappati()"
ORIGINE = "catalogo.rimappatura_fuso"
RIPRISTINO = "catalogo.ripristino_rimappatura"

DOPPIONE = 7001
MASTER = 7000
SLUG_MASTER = "bando-master"


# ----------------------------------------------------------------- helper


def doppioni(db) -> list[int]:
    return db.execute("select public.fn_doppioni_rimappati()").fetchone()[0]


def voce(db, azione: str, payload: dict) -> int:
    """Una voce di audit_log scritta come la scriverebbero la 0043 e la 0044."""
    return db.execute(
        "insert into public.audit_log (action, payload) values (%s, %s::jsonb) returning id",
        (azione, psycopg.types.json.Jsonb(payload)),
    ).fetchone()[0]


def voce_rimappatura(db, doppione, **extra) -> int:
    payload = {"tabella": "saved_bandi", "id": str(uuid.uuid4()), "operazione": "aggiornata",
               "doppione": doppione, "master": MASTER, "master_slug": SLUG_MASTER,
               "prima": {"bando_id": doppione, "bando_slug": f"bando-{doppione}"}}
    payload.update(extra)
    return voce(db, ORIGINE, payload)


def voce_ripristino(db, id_voce: int, doppione: int = DOPPIONE) -> int:
    return voce(db, RIPRISTINO, {"tabella": "saved_bandi", "operazione": "aggiornata",
                                 "doppione": doppione, "master": MASTER, "voce": id_voce,
                                 "prima": {"bando_id": MASTER, "bando_slug": SLUG_MASTER}})


def new_user(db) -> str:
    uid = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@test.it"))
    return uid


def salva(db, user: str, bando_id: int) -> str:
    return str(db.execute(
        "insert into public.saved_bandi (user_id, bando_id, bando_slug, bando_titolo, "
        "data_scadenza, stato_bando) values (%s, %s, %s, %s, current_date + 20, 'aperto') "
        "returning id",
        (user, bando_id, f"bando-{bando_id}", f"Titolo del bando {bando_id}"),
    ).fetchone()[0])


def rimappa(db, doppione=DOPPIONE) -> dict:
    return db.execute(
        "select public.fn_rimappa_bando_fuso(p_doppione => %s::integer, p_master => %s::integer, "
        "p_master_slug => %s::text, p_prova => false)",
        (doppione, MASTER, SLUG_MASTER),
    ).fetchone()[0]


def ripristina(db, doppione=DOPPIONE, *, prova: bool) -> dict:
    return db.execute(
        "select public.fn_ripristina_rimappatura(p_doppione => %s::integer, "
        "p_dal => '-infinity'::timestamptz, p_prova => %s::boolean)",
        (doppione, prova),
    ).fetchone()[0]


# ------------------------------------------------------------ fn_doppioni_rimappati


class TestDoppioniRimappati:
    def test_senza_voci_array_vuoto_mai_null(self, db):
        assert db.execute("select public.fn_doppioni_rimappati() = '{}'::integer[]").fetchone()[0]
        assert db.execute("select public.fn_doppioni_rimappati() is null").fetchone()[0] is False
        assert doppioni(db) == []

    def test_distinti_e_ordinati(self, db):
        for doppione in (7005, 7001, 7003, 7005, 7001):
            voce_rimappatura(db, doppione)
        assert doppioni(db) == [7001, 7003, 7005]

    def test_una_voce_ripristinata_esclude_solo_quella(self, db):
        prima = voce_rimappatura(db, 7001)
        seconda = voce_rimappatura(db, 7001, tabella="calendar_events")
        sola = voce_rimappatura(db, 7002)
        voce_ripristino(db, prima, 7001)
        voce_ripristino(db, sola, 7002)
        # 7001 ha ancora una voce non citata; 7002 no
        assert doppioni(db) == [7001]
        voce_ripristino(db, seconda, 7001)
        assert doppioni(db) == []

    def test_voci_non_numeriche_e_altre_azioni_ignorate(self, db):
        voce(db, ORIGINE, {"doppione": "7001", "master": MASTER})
        voce(db, ORIGINE, {"master": MASTER})
        voce(db, "payments.orphan", {"doppione": 7009})
        voce(db, RIPRISTINO, {"doppione": 7008, "voce": 0})
        assert doppioni(db) == []
        voce_rimappatura(db, 7002)
        assert doppioni(db) == [7002]

    def test_un_ripristino_di_un_altra_voce_non_esclude(self, db):
        propria = voce_rimappatura(db, 7001)
        voce_ripristino(db, propria + 1000, 7001)  # cita una voce che non è questa
        assert doppioni(db) == [7001]

    def test_flusso_reale_rimappatura_prova_e_ripristino(self, db):
        """0043 (scrittura) → il doppione compare; 0044 in prova → resta;
        0044 in scrittura → sparisce."""
        u = new_user(db)
        salva(db, u, DOPPIONE)
        assert doppioni(db) == []
        assert rimappa(db)["saved_bandi"] == {"aggiornate": 1, "eliminate": 0}
        assert doppioni(db) == [DOPPIONE]
        assert ripristina(db, prova=True)["saved_bandi"] == {"ripristinate": 1,
                                                             "in_conflitto": 0}
        assert doppioni(db) == [DOPPIONE]
        assert ripristina(db, prova=False)["saved_bandi"] == {"ripristinate": 1,
                                                              "in_conflitto": 0}
        assert doppioni(db) == []

    def test_una_riga_in_conflitto_lascia_il_doppione(self, db):
        """Una riga che il ripristino non tocca (in_conflitto) resta senza
        voce di ripristino: il doppione continua a comparire."""
        u = new_user(db)
        salva(db, u, DOPPIONE)
        rimappa(db)
        salva(db, u, DOPPIONE)  # il doppione è di nuovo tra i salvati: conflitto
        assert ripristina(db, prova=False)["saved_bandi"] == {"ripristinate": 0,
                                                              "in_conflitto": 1}
        assert doppioni(db) == [DOPPIONE]


# ---------------------------------------------------------------- sicurezza


class TestSicurezza0045:
    def test_firma_e_protezioni(self, db):
        righe = db.execute(
            """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                      coalesce(p.proacl::text, ''), p.oid::regprocedure::text,
                      pg_get_function_result(p.oid), p.provolatile
               from pg_proc p join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'public' and p.proname = 'fn_doppioni_rimappati'""",
        ).fetchall()
        assert len(righe) == 1
        oid, secdef, config, acl, firma, ritorno, volatilita = righe[0]
        assert firma == FIRMA
        assert ritorno == "integer[]"
        assert secdef is True
        assert "search_path=public" in config
        assert volatilita == "s"
        assert acl and not re.search(r"[{,]=X", acl), f"PUBLIC esegue ({acl})"
        for ruolo in ("anon", "authenticated"):
            assert not db.execute(
                "select has_function_privilege(%s, %s::oid, 'execute')", (ruolo, oid)
            ).fetchone()[0], ruolo

    def test_revoca_scritta_nel_file(self):
        assert re.search(
            r"^revoke execute on function public\.fn_doppioni_rimappati\(\)\s+"
            r"from public, anon, authenticated;",
            SQL_0045, re.M,
        )

    def test_additiva(self):
        corpo = re.sub(r"^--.*$", "", SQL_0045, flags=re.M).lower()
        assert "create or replace function public.fn_doppioni_rimappati" in corpo
        for vietato in ("alter table", "drop ", "create table", "create index", "update ",
                        "delete ", "insert "):
            assert vietato not in corpo, vietato

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    def test_i_client_non_eseguono_la_rpc(self, db, ruolo):
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute("select public.fn_doppioni_rimappati()")
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_la_esegue_senza_privilegi_di_tabella(self, db):
        voce_rimappatura(db, DOPPIONE)
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            db.execute(f"grant execute on function public.{FIRMA} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                assert doppioni(db) == [DOPPIONE]
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    db.execute("select 1 from public.audit_log")
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
