"""Test funzionali della migration 0034 (regole di partenariato per bando e
budget LLM fail-closed del modulo partenariati).

Coprono: vincoli, indici e trigger di bando_partenariato,
partenariati_ai_esecuzioni e partenariati_runs; la prenotazione della spesa
(limite per richiedente e per owner, budget del gruppo con riserva e costo
reale, budget 0, gruppi separati, giorno di Europe/Rome, serializzazione
sull'advisory lock); la chiusura dell'esecuzione; il ciclo di vita
dell'estrazione per bando (prenota con claim valido/scaduto, cooldown,
«analizza comunque», backoff; heartbeat; concludi in ogni esito con un solo
vincitore; failsafe dei claim scaduti); gli id per il filtro; RLS, privilegi
e firme di tutto ciò che la migration crea.
Ogni test riceve un database fresco clonato dal template.
"""

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0034_partenariato_regole.sql"
)
SQL_0034 = MIGRATION.read_text(encoding="utf-8")

TABELLE_NUOVE = {"bando_partenariato", "partenariati_ai_esecuzioni", "partenariati_runs"}
FUNZIONI_NUOVE = {
    "fn_partenariati_ai_prenota", "fn_partenariati_ai_concludi",
    "fn_partenariato_prenota", "fn_partenariato_rinnova", "fn_partenariato_concludi",
    "fn_partenariato_chiudi_stale", "fn_partenariato_bando_ids",
    "fn_partenariato_esecuzione_scaduta",
}

MODALITA = ("obbligatorio", "ammesso", "non_ammesso", "non_determinabile")
SERVIZI = ("partenariato_estrazione", "partner_profilo_ai", "partner_call_posizioni",
           "partner_call_testi", "partner_bozza")
ORIGINI = ("utente", "call", "batch", "admin", "valutazione", "sistema")
GRUPPI = ("bando", "altri", "batch", "valutazione")
STATI_ESECUZIONE = ("in_corso", "riusata", "conclusa", "nessun_segnale", "errore",
                    "timeout", "interrotta")

UTENTE = str(uuid.uuid4())
OWNER = str(uuid.uuid4())
AZIENDA = str(uuid.uuid4())


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def uid() -> str:
    return str(uuid.uuid4())


def prenota_ai(conn, *, servizio="partenariato_estrazione", origine="utente", gruppo="bando",
               budget=1000, riserva=10, richiedente=None, limite_richiedente=None,
               owner=None, limite_owner=None, company=None, bando_id=None) -> str:
    return str(conn.execute(
        "select public.fn_partenariati_ai_prenota(%s::text, %s::text, %s::text, %s::integer, "
        "%s::integer, %s::uuid, %s::integer, %s::uuid, %s::integer, %s::uuid, %s::integer)",
        (servizio, origine, gruppo, budget, riserva, richiedente, limite_richiedente,
         owner, limite_owner, company, bando_id),
    ).fetchone()[0])


def concludi_ai(conn, esecuzione_id, stato, cost=None, input_tokens=0, output_tokens=0,
                model=None, errore=None) -> None:
    conn.execute(
        "select public.fn_partenariati_ai_concludi(%s::uuid, %s::text, %s::integer, "
        "%s::integer, %s::integer, %s::text, %s::text)",
        (esecuzione_id, stato, cost, input_tokens, output_tokens, model, errore),
    )


def prenota(conn, bando_id: int = 1, *, slug=None, titolo=None, origine="utente",
            richiedente=UTENTE, company=AZIENDA, owner=OWNER, budget=500, riserva=30,
            limite=10, cooldown=1440, ttl=900, ignora=False) -> dict:
    return conn.execute(
        "select public.fn_partenariato_prenota(%s::integer, %s::text, %s::text, %s::text, "
        "%s::uuid, %s::uuid, %s::uuid, %s::integer, %s::integer, %s::integer, %s::integer, "
        "%s::integer, %s::boolean)",
        (bando_id, slug or f"bando-{bando_id}", titolo or f"Bando {bando_id}", origine,
         richiedente, company, owner, budget, riserva, limite, cooldown, ttl, ignora),
    ).fetchone()[0]


def rinnova(conn, bando_id, token, fase=None, ttl=900) -> bool:
    return conn.execute(
        "select public.fn_partenariato_rinnova(%s::integer, %s::uuid, %s::text, %s::integer)",
        (bando_id, token, fase, ttl),
    ).fetchone()[0]


def concludi(conn, bando_id, token, esito, dati=None) -> bool:
    return conn.execute(
        "select public.fn_partenariato_concludi(%s::integer, %s::uuid, %s::text, %s::jsonb)",
        (bando_id, token, esito, None if dati is None else json.dumps(dati)),
    ).fetchone()[0]


def chiudi_stale(conn) -> int:
    return conn.execute("select public.fn_partenariato_chiudi_stale()").fetchone()[0]


def bando_ids(conn, modalita, limite) -> list[int]:
    return conn.execute(
        "select public.fn_partenariato_bando_ids(%s::text[], %s::integer)", (modalita, limite)
    ).fetchone()[0]


def riga(db, bando_id: int = 1) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.bando_partenariato where bando_id = %s", (bando_id,)
        ).fetchone()


def esecuzione(db, esecuzione_id) -> dict:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.partenariati_ai_esecuzioni where id = %s", (esecuzione_id,)
        ).fetchone()


def consumi(db) -> list[dict]:
    """Righe del registro consumi (api_usage_events) scritte dalla migration."""
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select user_id, family_parent_id, provider, service, outcome, cost_cents, "
            "request_meta from public.api_usage_events order by id"
        ).fetchall()


def conta_esecuzioni(db) -> int:
    return db.execute("select count(*) from public.partenariati_ai_esecuzioni").fetchone()[0]


def secondi(db, espressione: str, bando_id: int = 1) -> float:
    """Valuta un'espressione intervallo sulla riga del bando, in secondi."""
    return float(db.execute(
        f"select extract(epoch from {espressione}) from public.bando_partenariato "
        "where bando_id = %s",
        (bando_id,),
    ).fetchone()[0])


def sposta(db, bando_id: int, assegnazioni: str) -> None:
    db.execute(
        f"update public.bando_partenariato set {assegnazioni} where bando_id = %s", (bando_id,)
    )


def dati_estratta(**extra) -> dict:
    dati = {
        "modalita": "ammesso", "modalita_effettiva": "ammesso",
        "extraction": {"modalita": "ammesso", "partner_min": 2},
        "regole": {"modalita": {"valore": "ammesso", "stato": "verificata"}},
        "preclassificazione": {"livello": "forte", "segnali": [{"categoria": "ammette"}]},
        "fonti_usate": [{"n": 1, "stato": "letto", "dominio": "regione.example.it"}],
        "catalogo_hash": "c1", "content_hash": "h1",
        "catalogo_aggiornato_at": "2026-09-01T10:00:00+00:00",
        "prompt_version": 1, "schema_version": 1, "model": "claude-sonnet-5",
        "input_tokens": 30000, "output_tokens": 8000, "cost_cents": 14,
    }
    dati.update(extra)
    return dati


def estrai(db, bando_id: int = 1, dati: dict | None = None, **kw) -> dict:
    """Ciclo completo prenota → concludi estratta. Ritorna l'esito della prenota."""
    res = prenota(db, bando_id, **kw)
    assert res["esito"] == "prenotata"
    assert concludi(db, bando_id, res["claim_token"], "estratta",
                    dati if dati is not None else dati_estratta()) is True
    return res


def inserisci_bando(db, bando_id: int, **colonne) -> None:
    """Insert diretta per i test dei vincoli e del filtro."""
    valori = {"bando_id": bando_id, "bando_slug": f"bando-{bando_id}",
              "bando_titolo": f"Bando {bando_id}", "stato": "pronta", "esito": "estratta"}
    valori.update(colonne)
    nomi = ", ".join(valori)
    segnaposto = ", ".join(["%s"] * len(valori))
    db.execute(
        f"insert into public.bando_partenariato ({nomi}) values ({segnaposto})",
        list(valori.values()),
    )


def inserisci_esecuzione(db, *, stato="conclusa", servizio="partenariato_estrazione",
                         origine="utente", gruppo="bando", riserva=10, cost=None,
                         richiedente=None, owner=None, llm=False, avviata_at=None) -> str:
    """Esecuzione storica inserita a mano; giorno = giorno di Roma di avviata_at."""
    return str(db.execute(
        """insert into public.partenariati_ai_esecuzioni
             (servizio, origine, gruppo, richiedente_user_id, owner_id, stato, llm_eseguito,
              costo_riservato_cents, cost_cents, avviata_at, giorno, conclusa_at)
           values (%s, %s, %s, %s::uuid, %s::uuid, %s, %s, %s, %s,
                   coalesce(%s::timestamptz, now()),
                   (coalesce(%s::timestamptz, now()) at time zone 'Europe/Rome')::date,
                   case when %s = 'in_corso' then null else now() end)
           returning id""",
        (servizio, origine, gruppo, richiedente, owner, stato, llm, riserva, cost,
         avviata_at, avviata_at, stato),
    ).fetchone()[0])


# ---------------------------------------------- tabella bando_partenariato


class TestBandoPartenariato:
    def test_default(self, db):
        inserisci_bando(db, 7, stato="errore", esito=None)
        r = riga(db, 7)
        assert r["preclassificazione"] == {}
        assert r["fonti_usate"] == []
        assert (r["input_tokens"], r["output_tokens"], r["cost_cents"]) == (0, 0, 0)
        assert r["tentativi_falliti"] == 0
        assert r["claim_token"] is None and r["fase"] is None
        assert r["created_at"] is not None and r["updated_at"] is not None

    @pytest.mark.parametrize("colonna,valori", [
        ("stato", ("pronta", "errore")),
        ("modalita", MODALITA),
        ("modalita_effettiva", MODALITA),
        ("esito", ("estratta", "nessun_segnale")),
    ])
    def test_valori_ammessi(self, db, colonna, valori):
        for i, valore in enumerate(valori):
            inserisci_bando(db, 100 + i, **{colonna: valore})

    @pytest.mark.parametrize("colonna,valore,vincolo", [
        ("stato", "non_estratta", "bp_stato_check"),
        ("stato", "nessun_segnale", "bp_stato_check"),
        ("fase", "scrittura", "bp_fase_check"),
        ("esito", "riusata", "bp_esito_check"),
        ("modalita", "forse", "bp_modalita_check"),
        ("modalita_effettiva", "ammessa", "bp_modalita_effettiva_check"),
        ("preclassificazione", "[]", "bp_preclassificazione_check"),
        ("fonti_usate", "{}", "bp_fonti_usate_check"),
        ("cost_cents", -1, None),
        ("input_tokens", -1, None),
        ("output_tokens", -1, None),
        ("tentativi_falliti", -1, None),
    ])
    def test_domini_vincolati(self, db, colonna, valore, vincolo):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            inserisci_bando(db, 1, **{colonna: valore})
        if vincolo:
            assert exc.value.diag.constraint_name == vincolo

    @pytest.mark.parametrize("colonne", [
        {"stato": "in_corso", "esito": None},
        {"stato": "in_corso", "esito": None, "claim_token": str(uuid.uuid4())},
        {"stato": "in_corso", "esito": None, "claim_scade_at": "2030-01-01T00:00:00Z"},
        {"stato": "pronta", "claim_token": str(uuid.uuid4()),
         "claim_scade_at": "2030-01-01T00:00:00Z"},
        {"stato": "errore", "esito": None, "claim_token": str(uuid.uuid4()),
         "claim_scade_at": "2026-01-01T00:00:00Z"},
    ])
    def test_claim_coerente(self, db, colonne):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            inserisci_bando(db, 1, **colonne)
        assert exc.value.diag.constraint_name == "bp_claim_coerente"

    def test_in_corso_con_claim_completo(self, db):
        inserisci_bando(db, 1, stato="in_corso", esito=None, fase="documenti",
                        claim_token=str(uuid.uuid4()),
                        claim_scade_at="2030-01-01T00:00:00Z")
        assert riga(db)["stato"] == "in_corso"

    def test_pronta_ha_esito(self, db):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            inserisci_bando(db, 1, stato="pronta", esito=None)
        assert exc.value.diag.constraint_name == "bp_pronta_ha_esito"

    @pytest.mark.parametrize("stato,esito", [("pronta", "estratta"), ("errore", None)])
    def test_fase_solo_in_corso(self, db, stato, esito):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            inserisci_bando(db, 1, stato=stato, esito=esito, fase="analisi")
        assert exc.value.diag.constraint_name == "bp_fase_solo_in_corso"

    def test_bando_id_unico(self, db):
        inserisci_bando(db, 1)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            inserisci_bando(db, 1, stato="errore", esito=None)
        assert exc.value.diag.constraint_name == "bando_partenariato_bando_key"

    def test_senza_fk_cross_db(self, db):
        assert db.execute(
            "select count(*) from pg_constraint "
            "where conrelid = 'public.bando_partenariato'::regclass and contype = 'f'"
        ).fetchone()[0] == 0

    def test_trigger_updated_at(self, db):
        inserisci_bando(db, 1)
        db.execute("update public.bando_partenariato set updated_at = now() - interval '1 day'")
        prima = riga(db)["updated_at"]
        db.execute("update public.bando_partenariato set errore_codice = 'x'")
        assert riga(db)["updated_at"] > prima

    def test_indici(self, db):
        indici = dict(db.execute(
            "select indexname, indexdef from pg_indexes "
            "where schemaname = 'public' and tablename = 'bando_partenariato'"
        ).fetchall())
        assert "UNIQUE" in indici["bando_partenariato_bando_key"]
        assert "(bando_id)" in indici["bando_partenariato_bando_key"]
        modalita = indici["bando_partenariato_modalita_idx"]
        assert "(modalita_effettiva, estratta_at DESC)" in modalita
        assert "WHERE (esito = 'estratta'::text)" in modalita
        in_corso = indici["bando_partenariato_in_corso_idx"]
        assert "(claim_scade_at)" in in_corso
        assert "WHERE (stato = 'in_corso'::text)" in in_corso


# -------------------------------------- tabella partenariati_ai_esecuzioni


class TestEsecuzioni:
    def test_default(self, db):
        eid = inserisci_esecuzione(db, stato="in_corso")
        e = esecuzione(db, eid)
        assert e["stato"] == "in_corso" and e["conclusa_at"] is None
        assert e["llm_eseguito"] is False
        assert e["cost_cents"] is None
        assert (e["input_tokens"], e["output_tokens"]) == (0, 0)
        default_giorno = db.execute(
            """select pg_get_expr(d.adbin, d.adrelid) from pg_attrdef d
               join pg_attribute a on a.attrelid = d.adrelid and a.attnum = d.adnum
               where d.adrelid = 'public.partenariati_ai_esecuzioni'::regclass
                 and a.attname = 'giorno'"""
        ).fetchone()[0]
        assert "Europe/Rome" in default_giorno and "now()" in default_giorno
        db.execute(
            "insert into public.partenariati_ai_esecuzioni "
            "(servizio, origine, gruppo, costo_riservato_cents) "
            "values ('partner_bozza', 'utente', 'altri', 0)"
        )
        assert db.execute(
            "select giorno = (now() at time zone 'Europe/Rome')::date "
            "from public.partenariati_ai_esecuzioni where servizio = 'partner_bozza'"
        ).fetchone()[0] is True

    @pytest.mark.parametrize("colonna,valori", [
        ("servizio", SERVIZI), ("origine", ORIGINI), ("gruppo", GRUPPI),
        ("stato", STATI_ESECUZIONE),
    ])
    def test_valori_ammessi(self, db, colonna, valori):
        for valore in valori:
            inserisci_esecuzione(db, **{colonna: valore})

    @pytest.mark.parametrize("colonne,vincolo", [
        ({"servizio": "ai_check"}, "pae_servizio_check"),
        ({"origine": "cron"}, "pae_origine_check"),
        ({"gruppo": "utente"}, "pae_gruppo_check"),
        ({"stato": "estratta"}, "pae_stato_check"),
        ({"riserva": -1}, None),
        ({"cost": -1}, None),
    ])
    def test_domini_vincolati(self, db, colonne, vincolo):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            inserisci_esecuzione(db, **colonne)
        if vincolo:
            assert exc.value.diag.constraint_name == vincolo

    def test_riserva_obbligatoria(self, db):
        with pytest.raises(psycopg.errors.NotNullViolation):
            db.execute(
                "insert into public.partenariati_ai_esecuzioni (servizio, origine, gruppo) "
                "values ('partner_bozza', 'utente', 'altri')"
            )

    @pytest.mark.parametrize("stato,conclusa", [("in_corso", True), ("conclusa", False)])
    def test_conclusa_coerente(self, db, stato, conclusa):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(
                "insert into public.partenariati_ai_esecuzioni "
                "(servizio, origine, gruppo, costo_riservato_cents, stato, conclusa_at) "
                "values ('partner_bozza', 'utente', 'altri', 0, %s, "
                "case when %s then now() end)",
                (stato, conclusa),
            )
        assert exc.value.diag.constraint_name == "pae_conclusa_coerente"

    def test_indici(self, db):
        indici = dict(db.execute(
            "select indexname, indexdef from pg_indexes "
            "where schemaname = 'public' and tablename = 'partenariati_ai_esecuzioni'"
        ).fetchall())
        assert "(giorno, gruppo)" in indici["pae_giorno_gruppo_idx"]
        assert "(richiedente_user_id, giorno, servizio)" in indici["pae_richiedente_idx"]
        assert "(owner_id, giorno, servizio)" in indici["pae_owner_idx"]
        assert "(bando_id, avviata_at DESC)" in indici["pae_bando_idx"]


class TestRuns:
    def test_claim_per_insert(self, db):
        db.execute("insert into public.partenariati_runs (giorno) values ('2026-09-28')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("insert into public.partenariati_runs (giorno) values ('2026-09-28')")
        riepilogo, creata = db.execute(
            "select riepilogo, created_at from public.partenariati_runs"
        ).fetchone()
        assert riepilogo == {} and creata is not None

    def test_riepilogo_oggetto(self, db):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(
                "insert into public.partenariati_runs (giorno, riepilogo) "
                "values ('2026-09-28', '[]')"
            )
        assert exc.value.diag.constraint_name == "pr_riepilogo_check"


# ------------------------------------------- RPC: prenotazione della spesa


class TestAiPrenota:
    def test_prima_prenotazione(self, db):
        eid = prenota_ai(db, servizio="partner_profilo_ai", origine="utente", gruppo="altri",
                         riserva=25, richiedente=UTENTE, limite_richiedente=3, owner=OWNER,
                         limite_owner=3, company=AZIENDA, bando_id=42)
        e = esecuzione(db, eid)
        assert (e["servizio"], e["origine"], e["gruppo"]) == ("partner_profilo_ai", "utente",
                                                              "altri")
        assert (str(e["richiedente_user_id"]), str(e["owner_id"]),
                str(e["company_profile_id"]), e["bando_id"]) == (UTENTE, OWNER, AZIENDA, 42)
        assert e["stato"] == "in_corso" and e["conclusa_at"] is None
        assert e["costo_riservato_cents"] == 25 and e["cost_cents"] is None
        assert e["llm_eseguito"] is False
        assert db.execute(
            "select giorno = (now() at time zone 'Europe/Rome')::date "
            "from public.partenariati_ai_esecuzioni where id = %s", (eid,)
        ).fetchone()[0] is True

    @pytest.mark.parametrize("campo", ["servizio", "origine", "gruppo", "riserva"])
    def test_parametri_nulli(self, db, campo):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, **{campo: None})
        assert detail_of(exc) == "parametri_non_validi"
        assert conta_esecuzioni(db) == 0

    def test_riserva_negativa(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, riserva=-1)
        assert detail_of(exc) == "parametri_non_validi"

    @pytest.mark.parametrize("campo,valore", [
        ("servizio", "ai_check"), ("origine", "cron"), ("gruppo", "tutti"),
    ])
    def test_domini_non_validi_non_prenotano(self, db, campo, valore):
        with pytest.raises(psycopg.errors.CheckViolation):
            prenota_ai(db, **{campo: valore})
        assert conta_esecuzioni(db) == 0

    def test_limite_richiedente(self, db):
        for _ in range(2):
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        assert detail_of(exc) == "ai_limite_utente"
        assert conta_esecuzioni(db) == 2
        # Altro utente, altro servizio: conteggi separati.
        prenota_ai(db, richiedente=uid(), limite_richiedente=2)
        prenota_ai(db, servizio="partner_bozza", gruppo="altri", richiedente=UTENTE,
                   limite_richiedente=2)

    def test_limite_esclude_riusata_e_nessun_segnale_senza_llm(self, db):
        a = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, a, "riusata", cost=0)
        b = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, b, "nessun_segnale", cost=0)
        # Non contano: il limite 2 è ancora tutto disponibile.
        c = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, c, "errore", cost=None, errore="errore_ai")
        d = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, d, "interrotta")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        assert detail_of(exc) == "ai_limite_utente"

    def test_limite_esclude_errore_e_interrotta_a_costo_zero_senza_llm(self, db):
        """Documenti non raggiungibili o claim perso prima dell'analisi: il
        modello non è stato chiamato (costo 0 esplicito), non si consuma il
        limite dell'utente. Costo ignoto o LLM eseguito: conta."""
        a = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, a, "errore", cost=0, errore="documenti_non_raggiungibili")
        b = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, b, "interrotta", cost=0, errore="claim_scaduto")
        c = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, c, "errore", cost=0, input_tokens=10, errore="errore_ai")
        d = prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        concludi_ai(db, d, "timeout", cost=0)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=2)
        assert detail_of(exc) == "ai_limite_utente"
        # stesso criterio per il limite dell'owner
        e = prenota_ai(db, owner=OWNER, limite_owner=1)
        concludi_ai(db, e, "errore", cost=0)
        prenota_ai(db, owner=OWNER, limite_owner=1)

    def test_nessun_segnale_con_llm_conta(self, db):
        a = prenota_ai(db, richiedente=UTENTE, limite_richiedente=1)
        concludi_ai(db, a, "nessun_segnale", cost=0, input_tokens=100)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=1)
        assert detail_of(exc) == "ai_limite_utente"

    def test_limite_richiedente_zero(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=0)
        assert detail_of(exc) == "ai_limite_utente"

    def test_senza_limite_o_senza_richiedente(self, db):
        for _ in range(3):
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=None)
        for _ in range(3):
            prenota_ai(db, richiedente=None, limite_richiedente=1, origine="batch",
                       gruppo="batch")
        assert conta_esecuzioni(db) == 6

    def test_limite_owner_nullo_nessun_limite(self, db):
        for _ in range(4):
            prenota_ai(db, servizio="partner_bozza", gruppo="altri", richiedente=uid(),
                       owner=OWNER, limite_owner=None)
        assert conta_esecuzioni(db) == 4

    def test_limite_owner_valorizzato(self, db):
        # Il limite per owner somma gli utenti diversi della stessa azienda.
        for _ in range(2):
            prenota_ai(db, servizio="partner_bozza", gruppo="altri", richiedente=uid(),
                       limite_richiedente=5, owner=OWNER, limite_owner=2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, servizio="partner_bozza", gruppo="altri", richiedente=uid(),
                       limite_richiedente=5, owner=OWNER, limite_owner=2)
        assert detail_of(exc) == "ai_limite_owner"
        # Altro owner e altro servizio: separati.
        prenota_ai(db, servizio="partner_bozza", gruppo="altri", owner=uid(), limite_owner=2)
        prenota_ai(db, servizio="partner_call_testi", gruppo="altri", owner=OWNER,
                   limite_owner=2)

    def test_limite_owner_zero(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, owner=OWNER, limite_owner=0)
        assert detail_of(exc) == "ai_limite_owner"

    def test_limite_utente_prima_del_budget(self, db):
        prenota_ai(db, richiedente=UTENTE, limite_richiedente=1, budget=100, riserva=100)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=1, budget=100, riserva=100)
        assert detail_of(exc) == "ai_limite_utente"

    def test_budget_con_riserva(self, db):
        prenota_ai(db, budget=100, riserva=40)
        prenota_ai(db, budget=100, riserva=40)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, budget=100, riserva=40)  # 80 + 40 > 100
        assert detail_of(exc) == "ai_budget_esaurito"
        assert conta_esecuzioni(db) == 2
        prenota_ai(db, budget=100, riserva=20)  # 80 + 20 = 100: al limite si passa
        with pytest.raises(psycopg.errors.RaiseException):
            prenota_ai(db, budget=100, riserva=1)

    def test_budget_con_costo_reale(self, db):
        a = prenota_ai(db, budget=100, riserva=40)
        concludi_ai(db, a, "conclusa", cost=5, input_tokens=1000, output_tokens=200)
        # Speso 5 invece della riserva 40: 5 + 40 + 40 = 85.
        prenota_ai(db, budget=100, riserva=40)
        prenota_ai(db, budget=100, riserva=40)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, budget=100, riserva=40)
        assert detail_of(exc) == "ai_budget_esaurito"

    def test_costo_reale_oltre_la_riserva(self, db):
        a = prenota_ai(db, budget=100, riserva=10)
        concludi_ai(db, a, "conclusa", cost=95, input_tokens=1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, budget=100, riserva=10)  # 95 + 10 > 100
        assert detail_of(exc) == "ai_budget_esaurito"

    @pytest.mark.parametrize("stato", ["timeout", "interrotta", "errore"])
    def test_costo_ignoto_lascia_la_riserva(self, db, stato):
        a = prenota_ai(db, budget=100, riserva=60)
        concludi_ai(db, a, stato, cost=None)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, budget=100, riserva=60)
        assert detail_of(exc) == "ai_budget_esaurito"

    @pytest.mark.parametrize("budget", [0, -5, None])
    def test_budget_zero_o_assente(self, db, budget):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, budget=budget, riserva=0)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert conta_esecuzioni(db) == 0

    def test_gruppi_separati(self, db):
        prenota_ai(db, gruppo="bando", budget=50, riserva=50)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, gruppo="bando", budget=50, riserva=1)
        assert detail_of(exc) == "ai_budget_esaurito"
        # Gli altri gruppi hanno il loro budget.
        prenota_ai(db, servizio="partner_bozza", gruppo="altri", budget=50, riserva=50)
        prenota_ai(db, origine="batch", gruppo="batch", budget=50, riserva=50)
        prenota_ai(db, origine="valutazione", gruppo="valutazione", budget=50, riserva=50)
        assert conta_esecuzioni(db) == 4

    def test_giorno_di_roma(self, db):
        """Righe alle 23:30 di ieri e alle 00:30 di oggi (ora di Roma): conta solo
        la seconda, anche se in UTC cade ancora nel giorno di ieri."""
        mezzanotte = db.execute(
            "select date_trunc('day', now() at time zone 'Europe/Rome') "
            "at time zone 'Europe/Rome'"
        ).fetchone()[0]
        ieri_2330 = mezzanotte - timedelta(minutes=30)
        oggi_0030 = mezzanotte + timedelta(minutes=30)
        oggi, ieri, utc_0030 = db.execute(
            "select (now() at time zone 'Europe/Rome')::date, "
            "(%s::timestamptz at time zone 'Europe/Rome')::date, "
            "(%s::timestamptz at time zone 'UTC')::date",
            (ieri_2330, oggi_0030),
        ).fetchone()
        assert ieri == oggi - timedelta(days=1)
        assert utc_0030 == oggi - timedelta(days=1)  # in UTC è ancora ieri

        for ts in (ieri_2330, oggi_0030):
            inserisci_esecuzione(db, richiedente=UTENTE, riserva=100, cost=100, llm=True,
                                 avviata_at=ts)
        giorni = sorted(r[0] for r in db.execute(
            "select giorno from public.partenariati_ai_esecuzioni").fetchall())
        assert giorni == [ieri, oggi]

        # Budget: oggi 100 (solo la riga delle 00:30) + 100 ≤ 250.
        prenota_ai(db, richiedente=uid(), budget=250, riserva=100)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=uid(), budget=250, riserva=100)
        assert detail_of(exc) == "ai_budget_esaurito"
        # Limite: per UTENTE oggi c'è solo la riga delle 00:30.
        prenota_ai(db, richiedente=UTENTE, limite_richiedente=2, budget=10_000)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_ai(db, richiedente=UTENTE, limite_richiedente=2, budget=10_000)
        assert detail_of(exc) == "ai_limite_utente"

    def test_giorno_indipendente_dal_fuso_della_sessione(self, db):
        """In ogni istante almeno uno dei due fusi (UTC-12, UTC+14) ha una data
        diversa da quella di Roma: il giorno non deve seguire il TimeZone."""
        try:
            for fuso in ("Etc/GMT+12", "Pacific/Kiritimati"):
                db.execute(f"set timezone = '{fuso}'")
                eid = prenota_ai(db, richiedente=uid(), limite_richiedente=1)
                assert db.execute(
                    "select giorno = (now() at time zone 'Europe/Rome')::date "
                    "from public.partenariati_ai_esecuzioni where id = %s", (eid,)
                ).fetchone()[0] is True, fuso
                db.execute(
                    "insert into public.partenariati_ai_esecuzioni "
                    "(servizio, origine, gruppo, costo_riservato_cents, richiedente_user_id) "
                    "values ('partner_bozza', 'utente', 'altri', 0, %s)", (UTENTE,),
                )
                assert db.execute(
                    "select giorno = (now() at time zone 'Europe/Rome')::date "
                    "from public.partenariati_ai_esecuzioni where richiedente_user_id = %s",
                    (UTENTE,),
                ).fetchone()[0] is True, fuso
                db.execute("delete from public.partenariati_ai_esecuzioni "
                           "where richiedente_user_id = %s", (UTENTE,))
        finally:
            db.execute("reset timezone")

    def test_lock_globale_con_lock_timeout(self, db):
        """Il lock è globale: anche un altro gruppo attende la transazione aperta."""
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        try:
            prenota_ai(altra, budget=100, riserva=10)
            db.execute("set lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                prenota_ai(db, servizio="partner_bozza", gruppo="altri", budget=100, riserva=10)
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        prenota_ai(db, budget=100, riserva=10)
        assert conta_esecuzioni(db) == 1

    def test_budget_serializzato(self, db):
        """Una seconda prenotazione concorrente attende la prima sul lock e,
        dopo il commit, ne vede la riserva: il budget non si sfora con due
        check-then-act paralleli."""
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esito: dict = {}

        def seconda():
            try:
                esito["out"] = prenota_ai(db, budget=100, riserva=60)
            except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
                esito["errore"] = exc

        thread = threading.Thread(target=seconda)
        pid = db.info.backend_pid
        try:
            prenota_ai(altra, budget=100, riserva=60)
            thread.start()
            scadenza = time.monotonic() + 10
            while not monitor.execute(
                "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
            ).fetchone()[0]:
                assert time.monotonic() < scadenza, "la seconda prenotazione non attende"
                assert thread.is_alive(), f"la seconda prenotazione non ha atteso: {esito}"
                time.sleep(0.02)
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
        errore = esito["errore"]
        assert isinstance(errore, psycopg.errors.RaiseException)
        assert errore.diag.message_detail == "ai_budget_esaurito"
        assert conta_esecuzioni(db) == 1


# ---------------------------------------------- RPC: chiusura dell'esecuzione


class TestAiConcludi:
    def test_conclusa(self, db):
        eid = prenota_ai(db, riserva=30)
        concludi_ai(db, eid, "conclusa", cost=14, input_tokens=30000, output_tokens=8000,
                    model="claude-sonnet-5")
        e = esecuzione(db, eid)
        assert e["stato"] == "conclusa"
        assert (e["cost_cents"], e["input_tokens"], e["output_tokens"]) == (14, 30000, 8000)
        assert e["model"] == "claude-sonnet-5"
        assert e["llm_eseguito"] is True
        assert e["conclusa_at"] is not None
        assert e["costo_riservato_cents"] == 30
        assert e["errore_codice"] is None

    def test_solo_da_in_corso(self, db):
        eid = prenota_ai(db)
        concludi_ai(db, eid, "conclusa", cost=14, input_tokens=10, model="m1")
        concludi_ai(db, eid, "errore", cost=None, errore="tardi")
        e = esecuzione(db, eid)
        assert (e["stato"], e["cost_cents"], e["model"], e["errore_codice"]) == (
            "conclusa", 14, "m1", None)

    @pytest.mark.parametrize("cost,inp,out,atteso", [
        (0, 0, 0, False),
        (None, 0, 0, False),
        (None, None, None, False),
        (1, 0, 0, True),
        (None, 5, 0, True),
        (0, 0, 7, True),
    ])
    def test_llm_eseguito(self, db, cost, inp, out, atteso):
        eid = prenota_ai(db)
        concludi_ai(db, eid, "errore", cost=cost, input_tokens=inp, output_tokens=out)
        e = esecuzione(db, eid)
        assert e["llm_eseguito"] is atteso
        assert (e["input_tokens"], e["output_tokens"]) == (inp or 0, out or 0)

    @pytest.mark.parametrize("stato", ["in_corso", "estratta", "annullata", "", None])
    def test_stato_non_valido(self, db, stato):
        eid = prenota_ai(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi_ai(db, eid, stato)
        assert detail_of(exc) == "stato_non_valido"
        assert esecuzione(db, eid)["stato"] == "in_corso"

    def test_costo_negativo_non_chiude(self, db):
        eid = prenota_ai(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            concludi_ai(db, eid, "conclusa", cost=-1)
        assert esecuzione(db, eid)["stato"] == "in_corso"

    def test_inesistente_nessun_effetto(self, db):
        concludi_ai(db, uid(), "conclusa", cost=1)
        assert conta_esecuzioni(db) == 0


# --------------------------------------- RPC: prenotazione dell'estrazione


class TestPrenota:
    def test_prima_prenotazione(self, db):
        res = prenota(db, 7, slug="bando-sette", titolo="Bando sette")
        assert res["esito"] == "prenotata"
        token, eid = res["claim_token"], res["esecuzione_id"]
        assert uuid.UUID(token) and uuid.UUID(eid)
        r = riga(db, 7)
        assert (r["stato"], r["fase"], str(r["claim_token"]), str(r["esecuzione_id"])) == (
            "in_corso", "documenti", token, eid)
        assert (r["bando_slug"], r["bando_titolo"]) == ("bando-sette", "Bando sette")
        assert r["esito"] is None and r["ultima_forzata_at"] is None
        assert r["ultima_esecuzione_at"] is None
        assert 890 <= secondi(db, "claim_scade_at - now()", 7) <= 900
        e = esecuzione(db, eid)
        assert (e["servizio"], e["origine"], e["gruppo"], e["bando_id"]) == (
            "partenariato_estrazione", "utente", "bando", 7)
        assert (str(e["richiedente_user_id"]), str(e["owner_id"]),
                str(e["company_profile_id"])) == (UTENTE, OWNER, AZIENDA)
        assert e["costo_riservato_cents"] == 30 and e["stato"] == "in_corso"

    def test_claim_valido_resta_in_corso(self, db):
        res = prenota(db, 1)
        for ignora in (False, True):
            assert prenota(db, 1, ignora=ignora) == {
                "esito": "in_corso", "claim_token": None, "esecuzione_id": None}
        assert conta_esecuzioni(db) == 1
        r = riga(db)
        assert str(r["claim_token"]) == res["claim_token"]
        assert r["ultima_forzata_at"] is None

    def test_claim_scaduto_riacquisito(self, db):
        vecchio = prenota(db, 1)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        nuovo = prenota(db, 1)
        assert nuovo["esito"] == "prenotata"
        assert nuovo["claim_token"] != vecchio["claim_token"]
        assert nuovo["esecuzione_id"] != vecchio["esecuzione_id"]
        v = esecuzione(db, vecchio["esecuzione_id"])
        # ancora in fase documenti: il modello non può essere stato chiamato
        assert v["stato"] == "interrotta" and v["cost_cents"] == 0
        assert v["errore_codice"] == "claim_scaduto" and v["conclusa_at"] is not None
        assert v["llm_eseguito"] is False
        assert consumi(db) == []
        assert esecuzione(db, nuovo["esecuzione_id"])["stato"] == "in_corso"
        r = riga(db)
        assert str(r["claim_token"]) == nuovo["claim_token"]
        assert str(r["esecuzione_id"]) == nuovo["esecuzione_id"]
        assert r["stato"] == "in_corso" and r["fase"] == "documenti"
        # Il vecchio proprietario ha perso: né heartbeat né chiusura.
        assert rinnova(db, 1, vecchio["claim_token"], "analisi") is False
        assert concludi(db, 1, vecchio["claim_token"], "estratta", dati_estratta()) is False
        assert riga(db)["stato"] == "in_corso"

    def test_claim_scaduto_la_riserva_resta_nel_budget(self, db):
        # arrivati all'analisi: la chiamata può essere partita
        prenota(db, 1, budget=60, riserva=30)
        sposta(db, 1, "fase = 'analisi', claim_scade_at = now() - interval '1 second'")
        seconda = prenota(db, 1, budget=60, riserva=30)  # 30 (vecchia) + 30 = 60
        sposta(db, 1, "fase = 'analisi', claim_scade_at = now() - interval '1 second'")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1, budget=60, riserva=30)  # 60 + 30 > 60
        assert detail_of(exc) == "ai_budget_esaurito"
        # Tutto annullato: la seconda esecuzione è ancora in corso con il suo claim.
        assert esecuzione(db, seconda["esecuzione_id"])["stato"] == "in_corso"
        assert str(riga(db)["claim_token"]) == seconda["claim_token"]
        assert conta_esecuzioni(db) == 2

    def test_claim_scaduto_in_analisi_costo_ignoto_e_registro_consumi(self, db):
        vecchio = prenota(db, 1, riserva=30)
        assert rinnova(db, 1, vecchio["claim_token"], "analisi") is True
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        assert prenota(db, 1)["esito"] == "prenotata"
        v = esecuzione(db, vecchio["esecuzione_id"])
        assert (v["stato"], v["cost_cents"]) == ("interrotta", None)
        [uso] = consumi(db)
        assert (uso["provider"], uso["service"], uso["outcome"], uso["cost_cents"]) == (
            "anthropic", "partenariato_estrazione", "timeout_unknown", 30)
        assert (str(uso["user_id"]), str(uso["family_parent_id"])) == (UTENTE, OWNER)
        assert uso["request_meta"]["esecuzione_id"] == vecchio["esecuzione_id"]
        assert uso["request_meta"]["bando_id"] == 1

    def test_claim_scaduto_prima_dell_analisi_libera_la_riserva(self, db):
        prenota(db, 1, budget=60, riserva=30)
        sposta(db, 1, "fase = 'lettura', claim_scade_at = now() - interval '1 second'")
        prenota(db, 1, budget=60, riserva=30)  # la vecchia vale 0
        prenota(db, 2, budget=60, riserva=30)  # 30 + 30 = 60: ancora nel budget

    def test_claim_scaduto_su_una_riverifica(self, db):
        estrai(db, 1)
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        prenota(db, 1)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        # Il cooldown guarda l'ultima esecuzione CONCLUSA (25 h fa).
        assert prenota(db, 1)["esito"] == "prenotata"
        assert riga(db)["regole"] == dati_estratta()["regole"]  # ancora servite

    @pytest.mark.parametrize("ttl,atteso", [(10, 60), (5000, 1800), (None, 900), (300, 300)])
    def test_ttl_limitato(self, db, ttl, atteso):
        prenota(db, 1, ttl=ttl)
        assert atteso - 10 <= secondi(db, "claim_scade_at - now()") <= atteso

    def test_cooldown(self, db):
        estrai(db, 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1)
        assert detail_of(exc) == "partenariato_cooldown"
        assert conta_esecuzioni(db) == 1
        assert riga(db)["stato"] == "pronta"
        # Cooldown più corto del tempo trascorso: si passa.
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '2 hours'")
        with pytest.raises(psycopg.errors.RaiseException):
            prenota(db, 1, cooldown=180)
        assert prenota(db, 1, cooldown=60)["esito"] == "prenotata"

    def test_cooldown_nullo_vale_24_ore(self, db):
        estrai(db, 1)
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '23 hours'")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1, cooldown=None)
        assert detail_of(exc) == "partenariato_cooldown"
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        assert prenota(db, 1, cooldown=None)["esito"] == "prenotata"

    def test_ignora_cooldown(self, db):
        res = prenota(db, 1)
        concludi(db, 1, res["claim_token"], "nessun_segnale",
                 {"cost_cents": 0, "preclassificazione": {"livello": "nessuno"}})
        with pytest.raises(psycopg.errors.RaiseException):
            prenota(db, 1)
        forzata = prenota(db, 1, ignora=True)
        assert forzata["esito"] == "prenotata"
        r = riga(db)
        assert r["ultima_forzata_at"] is not None and r["stato"] == "in_corso"
        forzata_at = r["ultima_forzata_at"]
        concludi(db, 1, forzata["claim_token"], "estratta", dati_estratta())
        # Una prenotazione normale non tocca ultima_forzata_at.
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        prenota(db, 1)
        assert riga(db)["ultima_forzata_at"] == forzata_at

    def test_ignora_cooldown_non_salta_budget_ne_limite(self, db):
        estrai(db, 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1, ignora=True, budget=0)
        assert detail_of(exc) == "ai_budget_esaurito"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1, ignora=True, limite=1)  # l'estrazione di prima conta
        assert detail_of(exc) == "ai_limite_utente"
        assert riga(db)["ultima_forzata_at"] is None

    def test_errore_con_backoff(self, db):
        res = prenota(db, 1)
        concludi(db, 1, res["claim_token"], "errore", {"errore_codice": "errore_ai"})
        assert riga(db)["stato"] == "errore"
        # In errore vale il backoff (6 h), anche con cooldown 0.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1, cooldown=0)
        assert detail_of(exc) == "partenariato_cooldown"
        # Passato il backoff si riprova, anche se il cooldown di 24 h non è passato.
        sposta(db, 1, "prossimo_tentativo_at = now() - interval '1 second'")
        assert prenota(db, 1)["esito"] == "prenotata"
        r = riga(db)
        assert r["stato"] == "in_corso" and r["tentativi_falliti"] == 1
        assert r["errore_codice"] == "errore_ai"  # informativo fino al prossimo esito

    def test_errore_con_regole_precedenti_segue_il_backoff(self, db):
        estrai(db, 1)
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        res = prenota(db, 1)
        concludi(db, 1, res["claim_token"], "timeout", {"cost_cents": None})
        assert riga(db)["stato"] == "pronta"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1, cooldown=0)
        assert detail_of(exc) == "partenariato_cooldown"
        sposta(db, 1, "prossimo_tentativo_at = now() - interval '1 second'")
        assert prenota(db, 1)["esito"] == "prenotata"

    @pytest.mark.parametrize("origine,gruppo", [
        ("utente", "bando"), ("call", "bando"), ("admin", "bando"), ("sistema", "bando"),
        ("batch", "batch"), ("valutazione", "valutazione"),
    ])
    def test_gruppo_per_origine(self, db, origine, gruppo):
        res = prenota(db, 1, origine=origine)
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["origine"], e["gruppo"]) == (origine, gruppo)

    def test_origine_non_valida(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            prenota(db, 1, origine="cron")
        assert riga(db) is None and conta_esecuzioni(db) == 0

    def test_budget_batch_separato(self, db):
        prenota(db, 1, budget=30, riserva=30)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 2, budget=30, riserva=30)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert prenota(db, 2, origine="batch", richiedente=None, budget=30,
                       riserva=30)["esito"] == "prenotata"

    def test_limite_utente(self, db):
        prenota(db, 1, limite=1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 2, limite=1)
        assert detail_of(exc) == "ai_limite_utente"
        assert riga(db, 2) is None
        assert conta_esecuzioni(db) == 1
        # Nessun limite senza richiedente (batch) o con limite NULL (admin).
        prenota(db, 2, origine="batch", richiedente=None, limite=1)
        prenota(db, 3, origine="admin", limite=None)

    def test_budget_esaurito_nessuna_riga(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1, budget=10, riserva=30)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert riga(db) is None and conta_esecuzioni(db) == 0

    def test_slug_e_titolo_aggiornati(self, db):
        estrai(db, 1, slug="vecchio", titolo="Vecchio")
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        prenota(db, 1, slug="nuovo", titolo="Nuovo")
        r = riga(db)
        assert (r["bando_slug"], r["bando_titolo"]) == ("nuovo", "Nuovo")

    def test_bando_nullo(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, None)
        assert detail_of(exc) == "parametri_non_validi"

    def test_lock_per_bando_con_lock_timeout(self, db):
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        try:
            assert prenota(altra, 1)["esito"] == "prenotata"
            db.execute("set lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                prenota(db, 1)
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        assert riga(db) is None and conta_esecuzioni(db) == 0

    def test_concorrenza_una_sola_esecuzione(self, db):
        """Due prenotazioni concorrenti sullo stesso bando: la seconda attende
        e poi vede il claim della prima → in_corso, nessuna seconda spesa."""
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esito: dict = {}

        def seconda():
            try:
                esito["out"] = prenota(db, 1)
            except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
                esito["errore"] = exc

        thread = threading.Thread(target=seconda)
        pid = db.info.backend_pid
        try:
            prima = prenota(altra, 1)
            thread.start()
            scadenza = time.monotonic() + 10
            while not monitor.execute(
                "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
            ).fetchone()[0]:
                assert time.monotonic() < scadenza, "la seconda prenotazione non attende"
                assert thread.is_alive(), f"la seconda prenotazione non ha atteso: {esito}"
                time.sleep(0.02)
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

        assert esito == {"out": {"esito": "in_corso", "claim_token": None,
                                 "esecuzione_id": None}}
        assert conta_esecuzioni(db) == 1
        assert str(riga(db)["claim_token"]) == prima["claim_token"]


# ------------------------------------------------------- RPC: heartbeat


class TestRinnova:
    def test_token_giusto(self, db):
        res = prenota(db, 1, ttl=60)
        assert rinnova(db, 1, res["claim_token"], "lettura", ttl=900) is True
        r = riga(db)
        assert r["fase"] == "lettura"
        assert 890 <= secondi(db, "claim_scade_at - now()") <= 900
        # Fase NULL = invariata; TTL limitato.
        assert rinnova(db, 1, res["claim_token"], None, ttl=10) is True
        assert riga(db)["fase"] == "lettura"
        assert 50 <= secondi(db, "claim_scade_at - now()") <= 60
        assert rinnova(db, 1, res["claim_token"], "analisi", ttl=99_999) is True
        assert riga(db)["fase"] == "analisi"
        assert 1790 <= secondi(db, "claim_scade_at - now()") <= 1800

    def test_token_sbagliato(self, db):
        prenota(db, 1)
        prima = riga(db)
        assert rinnova(db, 1, uid(), "analisi") is False
        assert rinnova(db, 2, prima["claim_token"], "analisi") is False
        dopo = riga(db)
        assert (dopo["fase"], dopo["claim_scade_at"]) == ("documenti", prima["claim_scade_at"])

    def test_claim_scaduto(self, db):
        res = prenota(db, 1)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        assert rinnova(db, 1, res["claim_token"], "analisi") is False
        r = riga(db)
        assert r["fase"] == "documenti"
        assert secondi(db, "claim_scade_at - now()") < 0  # non resuscita

    def test_dopo_la_chiusura(self, db):
        res = estrai(db, 1)
        assert rinnova(db, 1, res["claim_token"], "analisi") is False
        assert riga(db)["fase"] is None

    def test_token_nullo(self, db):
        prenota(db, 1)
        assert rinnova(db, 1, None, "analisi") is False

    @pytest.mark.parametrize("fase", ["scrittura", ""])
    def test_fase_non_valida(self, db, fase):
        res = prenota(db, 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            rinnova(db, 1, res["claim_token"], fase)
        assert detail_of(exc) == "fase_non_valida"


# ------------------------------------------------------- RPC: chiusura


class TestConcludi:
    def test_estratta(self, db):
        res = prenota(db, 1)
        dati = dati_estratta()
        assert concludi(db, 1, res["claim_token"], "estratta", dati) is True
        r = riga(db)
        assert (r["stato"], r["esito"], r["fase"], r["claim_token"], r["claim_scade_at"]) == (
            "pronta", "estratta", None, None, None)
        for campo in ("modalita", "modalita_effettiva", "extraction", "regole",
                      "preclassificazione", "fonti_usate", "catalogo_hash", "content_hash",
                      "prompt_version", "schema_version", "model", "input_tokens",
                      "output_tokens", "cost_cents"):
            assert r[campo] == dati[campo], campo
        assert r["catalogo_aggiornato_at"] == datetime.fromisoformat("2026-09-01T10:00:00+00:00")
        assert r["estratta_at"] is not None
        assert r["verificata_at"] == r["estratta_at"] == r["ultima_esecuzione_at"]
        assert (r["errore_codice"], r["tentativi_falliti"], r["prossimo_tentativo_at"]) == (
            None, 0, None)
        assert str(r["esecuzione_id"]) == res["esecuzione_id"]
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["input_tokens"], e["output_tokens"]) == (
            "conclusa", 14, 30000, 8000)
        assert (e["model"], e["content_hash"], e["llm_eseguito"]) == (
            "claude-sonnet-5", "h1", True)
        # Un solo vincitore: la seconda chiusura non ha effetto.
        assert concludi(db, 1, res["claim_token"], "errore", {"errore_codice": "x"}) is False
        assert riga(db)["errore_codice"] is None

    def test_nessun_segnale(self, db):
        res = prenota(db, 1)
        assert concludi(db, 1, res["claim_token"], "nessun_segnale", {
            "cost_cents": 0, "preclassificazione": {"livello": "nessuno", "segnali": []},
            "fonti_usate": [{"n": 1, "stato": "letto"}], "catalogo_hash": "c0",
        }) is True
        r = riga(db)
        assert (r["stato"], r["esito"]) == ("pronta", "nessun_segnale")
        assert (r["modalita"], r["modalita_effettiva"], r["regole"], r["extraction"]) == (
            None, None, None, None)
        assert r["preclassificazione"] == {"livello": "nessuno", "segnali": []}
        assert (r["catalogo_hash"], r["cost_cents"], r["model"]) == ("c0", 0, None)
        assert r["estratta_at"] is not None
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["llm_eseguito"]) == ("nessun_segnale", 0, False)

    def test_nessun_segnale_sostituisce_un_estrazione(self, db):
        estrai(db, 1)
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        res = prenota(db, 1)
        concludi(db, 1, res["claim_token"], "nessun_segnale", {"cost_cents": 0})
        r = riga(db)
        assert (r["esito"], r["modalita_effettiva"], r["regole"]) == (
            "nessun_segnale", None, None)
        # Le chiavi assenti di meta restano.
        assert (r["content_hash"], r["fonti_usate"]) == ("h1", dati_estratta()["fonti_usate"])

    def test_riusata(self, db):
        estrai(db, 1)
        prima = riga(db)
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours', "
                      "verificata_at = now() - interval '25 hours', "
                      "estratta_at = now() - interval '25 hours'")
        estratta_at = riga(db)["estratta_at"]
        res = prenota(db, 1)
        assert concludi(db, 1, res["claim_token"], "riusata", {
            "cost_cents": 0, "catalogo_hash": "c2", "content_hash": "h1",
            "catalogo_aggiornato_at": "2026-09-20T08:00:00+00:00",
            "model": "claude-opus-5", "input_tokens": 5, "regole": {"altro": True},
        }) is True
        r = riga(db)
        assert (r["stato"], r["esito"]) == ("pronta", "estratta")
        # Risultato della generazione originale invariato.
        for campo in ("modalita", "modalita_effettiva", "extraction", "regole", "model",
                      "input_tokens", "output_tokens", "cost_cents", "prompt_version"):
            assert r[campo] == prima[campo], campo
        assert r["estratta_at"] == estratta_at
        assert secondi(db, "now() - verificata_at") < 60
        assert (r["catalogo_hash"], r["content_hash"]) == ("c2", "h1")
        assert r["catalogo_aggiornato_at"] == datetime.fromisoformat("2026-09-20T08:00:00+00:00")
        # L'esecuzione registra ciò che le è stato passato (qui 5 token).
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["input_tokens"], e["llm_eseguito"]) == (
            "riusata", 0, 5, True)

    def test_riusata_senza_llm_non_conta_nel_limite(self, db):
        estrai(db, 1)
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        res = prenota(db, 1, limite=2)
        concludi(db, 1, res["claim_token"], "riusata", {"cost_cents": 0})
        assert esecuzione(db, res["esecuzione_id"])["llm_eseguito"] is False
        # Conta solo l'estrazione: resta un'analisi disponibile oggi.
        assert prenota(db, 2, limite=2)["esito"] == "prenotata"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 3, limite=2)
        assert detail_of(exc) == "ai_limite_utente"

    def test_riusata_senza_estrazione_precedente(self, db):
        res = prenota(db, 1)
        assert concludi(db, 1, res["claim_token"], "riusata", {"cost_cents": 0}) is True
        r = riga(db)
        assert (r["stato"], r["esito"], r["errore_codice"]) == (
            "errore", None, "riusata_senza_estrazione")
        assert r["tentativi_falliti"] == 1
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["stato"], e["errore_codice"]) == ("errore", "riusata_senza_estrazione")

    def test_errore_senza_estrazione_precedente(self, db):
        res = prenota(db, 1)
        assert concludi(db, 1, res["claim_token"], "errore", {
            "errore_codice": "output_non_valido", "input_tokens": 40000,
            "output_tokens": 16000, "model": "claude-sonnet-5",
        }) is True
        r = riga(db)
        assert (r["stato"], r["esito"], r["errore_codice"], r["tentativi_falliti"]) == (
            "errore", None, "output_non_valido", 1)
        assert (r["claim_token"], r["fase"]) == (None, None)
        assert secondi(db, "prossimo_tentativo_at - errore_at") == 6 * 3600
        assert r["ultima_esecuzione_at"] is not None
        e = esecuzione(db, res["esecuzione_id"])
        # Costo assente = ignoto: la riserva resta nel budget.
        assert (e["stato"], e["cost_cents"], e["errore_codice"]) == (
            "errore", None, "output_non_valido")
        assert (e["input_tokens"], e["output_tokens"], e["llm_eseguito"]) == (
            40000, 16000, True)

    @pytest.mark.parametrize("esito,dati,codice", [
        ("errore", {}, "errore"),
        ("errore", {"errore_codice": ""}, "errore"),
        ("timeout", None, "timeout"),
        ("timeout", {"errore_codice": "ai_timeout"}, "ai_timeout"),
    ])
    def test_codice_di_errore_predefinito(self, db, esito, dati, codice):
        res = prenota(db, 1)
        concludi(db, 1, res["claim_token"], esito, dati)
        assert riga(db)["errore_codice"] == codice
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["stato"], e["errore_codice"]) == (esito, codice)

    def test_errore_ripristina_le_regole_precedenti(self, db):
        estrai(db, 1)
        prima = riga(db)
        sposta(db, 1, "ultima_esecuzione_at = now() - interval '25 hours'")
        res = prenota(db, 1)
        assert riga(db)["regole"] == prima["regole"]  # servite durante l'in_corso
        assert concludi(db, 1, res["claim_token"], "timeout", {
            "cost_cents": 27, "input_tokens": 50000, "output_tokens": 16000,
            "content_hash": "h2", "regole": {"nuove": True}, "modalita_effettiva": "obbligatorio",
            "catalogo_hash": "c9",
        }) is True
        r = riga(db)
        assert (r["stato"], r["esito"], r["errore_codice"]) == ("pronta", "estratta", "timeout")
        for campo in ("modalita", "modalita_effettiva", "extraction", "regole",
                      "preclassificazione", "fonti_usate", "catalogo_hash", "content_hash",
                      "prompt_version", "model", "input_tokens", "output_tokens",
                      "cost_cents", "estratta_at", "verificata_at"):
            assert r[campo] == prima[campo], campo
        assert r["tentativi_falliti"] == 1
        assert secondi(db, "prossimo_tentativo_at - errore_at") == 6 * 3600
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["content_hash"]) == ("timeout", 27, "h2")

    def test_backoff_esponenziale_con_tetto(self, db):
        attesi = [6, 12, 24, 48, 72, 72]
        for n, ore in enumerate(attesi, start=1):
            if n > 1:
                sposta(db, 1, "prossimo_tentativo_at = now() - interval '1 second'")
            res = prenota(db, 1)
            concludi(db, 1, res["claim_token"], "errore", {"errore_codice": "e"})
            r = riga(db)
            assert r["tentativi_falliti"] == n
            assert secondi(db, "prossimo_tentativo_at - errore_at") == ore * 3600, n

    def test_backoff_senza_overflow(self, db):
        res = prenota(db, 1)
        sposta(db, 1, "tentativi_falliti = 40")
        concludi(db, 1, res["claim_token"], "errore", {})
        assert riga(db)["tentativi_falliti"] == 41
        assert secondi(db, "prossimo_tentativo_at - errore_at") == 72 * 3600

    def test_un_successo_azzera_errori_e_backoff(self, db):
        res = prenota(db, 1)
        concludi(db, 1, res["claim_token"], "errore", {"errore_codice": "e"})
        sposta(db, 1, "prossimo_tentativo_at = now() - interval '1 second'")
        estrai(db, 1)
        r = riga(db)
        assert (r["errore_codice"], r["errore_at"], r["tentativi_falliti"],
                r["prossimo_tentativo_at"]) == (None, None, 0, None)

    def test_perso_con_token_sbagliato(self, db):
        res = prenota(db, 1)
        assert concludi(db, 1, uid(), "estratta", dati_estratta()) is False
        assert concludi(db, 2, res["claim_token"], "estratta", dati_estratta()) is False
        assert concludi(db, 1, None, "estratta", dati_estratta()) is False
        r = riga(db)
        assert r["stato"] == "in_corso" and r["regole"] is None
        assert esecuzione(db, res["esecuzione_id"])["stato"] == "in_corso"

    def test_token_residuo_fuori_da_in_corso(self, db):
        """bp_claim_coerente ammette un token SENZA scadenza fuori da in_corso:
        conoscerlo non basta per chiudere né per rinnovare."""
        token = str(uuid.uuid4())
        inserisci_bando(db, 1, claim_token=token, regole='{"a": 1}')
        assert concludi(db, 1, token, "errore", {"errore_codice": "x"}) is False
        assert rinnova(db, 1, token, "analisi") is False
        r = riga(db)
        assert (r["stato"], r["errore_codice"], r["fase"], r["regole"]) == (
            "pronta", None, None, {"a": 1})

    def test_perso_dopo_il_failsafe(self, db):
        res = prenota(db, 1)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        assert chiudi_stale(db) == 1
        assert concludi(db, 1, res["claim_token"], "estratta", dati_estratta()) is False
        r = riga(db)
        assert (r["stato"], r["regole"]) == ("errore", None)
        e = esecuzione(db, res["esecuzione_id"])
        # chiuso in fase documenti: il modello non era stato chiamato
        assert (e["stato"], e["cost_cents"]) == ("interrotta", 0)

    def test_claim_scaduto_non_ripreso_vince_ancora(self, db):
        res = prenota(db, 1)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        assert concludi(db, 1, res["claim_token"], "estratta", dati_estratta()) is True
        assert riga(db)["stato"] == "pronta"

    @pytest.mark.parametrize("esito", ["pronta", "in_corso", "conclusa", "", None])
    def test_esito_non_valido(self, db, esito):
        res = prenota(db, 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi(db, 1, res["claim_token"], esito, {})
        assert detail_of(exc) == "esito_non_valido"
        assert riga(db)["stato"] == "in_corso"

    @pytest.mark.parametrize("dati", [None, [1, 2], "testo"])
    def test_dati_non_oggetto_valgono_vuoti(self, db, dati):
        res = prenota(db, 1)
        assert concludi(db, 1, res["claim_token"], "nessun_segnale", dati) is True
        e = esecuzione(db, res["esecuzione_id"])
        assert e["cost_cents"] is None and e["stato"] == "nessun_segnale"

    def test_chiavi_ignote_ignorate(self, db):
        res = prenota(db, 1)
        assert concludi(db, 1, res["claim_token"], "estratta",
                        dati_estratta(stato="errore", claim_token=None, documenti=3)) is True
        assert riga(db)["stato"] == "pronta"

    @pytest.mark.parametrize("dati", [
        {"modalita_effettiva": "forse"},
        {"fonti_usate": {"n": 1}},
        {"cost_cents": -3},
    ])
    def test_valori_non_validi_non_chiudono(self, db, dati):
        res = prenota(db, 1)
        with pytest.raises(psycopg.errors.CheckViolation):
            concludi(db, 1, res["claim_token"], "estratta", dati_estratta(**dati))
        assert riga(db)["stato"] == "in_corso"
        assert esecuzione(db, res["esecuzione_id"])["stato"] == "in_corso"


# ------------------------------------------------------- RPC: failsafe


class TestChiudiStale:
    def test_niente_da_chiudere(self, db):
        prenota(db, 1)
        estrai(db, 2)
        assert chiudi_stale(db) == 0
        assert riga(db, 1)["stato"] == "in_corso"

    def test_chiude_solo_gli_scaduti(self, db):
        senza = prenota(db, 1)
        estrai(db, 2)
        prima_2 = riga(db, 2)
        sposta(db, 2, "ultima_esecuzione_at = now() - interval '25 hours'")
        con = prenota(db, 2)
        valido = prenota(db, 3)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        sposta(db, 2, "claim_scade_at = now() - interval '1 second'")

        assert chiudi_stale(db) == 2

        r1 = riga(db, 1)
        assert (r1["stato"], r1["esito"], r1["errore_codice"], r1["tentativi_falliti"]) == (
            "errore", None, "interrotta", 1)
        assert (r1["claim_token"], r1["claim_scade_at"], r1["fase"]) == (None, None, None)
        assert secondi(db, "prossimo_tentativo_at - errore_at", 1) == 6 * 3600
        assert r1["ultima_esecuzione_at"] is not None

        r2 = riga(db, 2)
        assert (r2["stato"], r2["esito"], r2["errore_codice"]) == (
            "pronta", "estratta", "interrotta")
        assert r2["regole"] == prima_2["regole"]

        for res in (senza, con):
            e = esecuzione(db, res["esecuzione_id"])
            # fase documenti: modello mai chiamato, costo 0
            assert (e["stato"], e["cost_cents"], e["errore_codice"]) == (
                "interrotta", 0, "claim_scaduto")
            assert e["conclusa_at"] is not None
        assert consumi(db) == []

        assert riga(db, 3)["stato"] == "in_corso"
        assert esecuzione(db, valido["esecuzione_id"])["stato"] == "in_corso"
        assert chiudi_stale(db) == 0  # idempotente

    def test_backoff_dopo_l_interruzione(self, db):
        prenota(db, 1)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second', tentativi_falliti = 2")
        chiudi_stale(db)
        assert riga(db)["tentativi_falliti"] == 3
        assert secondi(db, "prossimo_tentativo_at - errore_at") == 24 * 3600
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 1)
        assert detail_of(exc) == "partenariato_cooldown"

    def test_la_riserva_resta_nel_budget(self, db):
        res = prenota(db, 1, budget=30, riserva=30)
        assert rinnova(db, 1, res["claim_token"], "analisi") is True
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        chiudi_stale(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota(db, 2, budget=30, riserva=1)
        assert detail_of(exc) == "ai_budget_esaurito"
        [uso] = consumi(db)
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", 30)
        assert uso["request_meta"]["failsafe"] is True

    @pytest.mark.parametrize("fase", ["documenti", "lettura"])
    def test_prima_dell_analisi_riserva_liberata_e_limite_intatto(self, db, fase):
        res = prenota(db, 1, budget=30, riserva=30, limite=1)
        if fase != "documenti":
            assert rinnova(db, 1, res["claim_token"], fase) is True
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        assert chiudi_stale(db) == 1
        e = esecuzione(db, res["esecuzione_id"])
        assert (e["stato"], e["cost_cents"], e["llm_eseguito"]) == ("interrotta", 0, False)
        assert consumi(db) == []
        # budget e limite dell'utente di nuovo disponibili
        prenota(db, 2, budget=30, riserva=30, limite=1)

    def test_helper_idempotente(self, db):
        res = prenota(db, 1, riserva=30)
        assert rinnova(db, 1, res["claim_token"], "analisi") is True
        for _ in range(2):
            db.execute(
                "select public.fn_partenariato_esecuzione_scaduta(%s::uuid, 'analisi', 'x')",
                (res["esecuzione_id"],),
            )
        assert len(consumi(db)) == 1
        assert esecuzione(db, res["esecuzione_id"])["stato"] == "interrotta"

    def test_salta_le_righe_bloccate(self, db):
        prenota(db, 1)
        sposta(db, 1, "claim_scade_at = now() - interval '1 second'")
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        try:
            altra.execute(
                "select 1 from public.bando_partenariato where bando_id = 1 for update")
            db.execute("set lock_timeout = '2s'")
            inizio = time.monotonic()
            assert chiudi_stale(db) == 0
            assert time.monotonic() - inizio < 1.5  # non ha atteso il lock
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        assert chiudi_stale(db) == 1


# ------------------------------------------------------- RPC: filtro


class TestBandoIds:
    def test_ordinati_e_filtrati(self, db):
        inserisci_bando(db, 10, modalita_effettiva="ammesso",
                        estratta_at="2026-09-10T10:00:00Z")
        inserisci_bando(db, 11, modalita_effettiva="obbligatorio",
                        estratta_at="2026-09-12T10:00:00Z")
        inserisci_bando(db, 12, modalita_effettiva="ammesso",
                        estratta_at="2026-09-11T10:00:00Z")
        inserisci_bando(db, 13, modalita_effettiva="non_ammesso",
                        estratta_at="2026-09-13T10:00:00Z")
        inserisci_bando(db, 14, modalita_effettiva="non_determinabile",
                        estratta_at="2026-09-14T10:00:00Z")
        inserisci_bando(db, 15, esito="nessun_segnale", modalita_effettiva="ammesso",
                        estratta_at="2026-09-15T10:00:00Z")
        inserisci_bando(db, 16, stato="errore", esito=None)
        # Riverifica in corso: le regole precedenti restano valide per il filtro.
        inserisci_bando(db, 17, stato="in_corso", fase="lettura",
                        claim_token=str(uuid.uuid4()),
                        claim_scade_at="2030-01-01T00:00:00Z", modalita_effettiva="ammesso",
                        estratta_at="2026-09-09T10:00:00Z")
        # A parità di data vince il bando_id più basso.
        inserisci_bando(db, 9, modalita_effettiva="ammesso",
                        estratta_at="2026-09-10T10:00:00Z")

        assert bando_ids(db, ["ammesso", "obbligatorio"], 1000) == [11, 12, 9, 10, 17]
        assert bando_ids(db, ["obbligatorio"], 1000) == [11]
        assert bando_ids(db, ["ammesso", "obbligatorio"], 2) == [11, 12]

    @pytest.mark.parametrize("modalita", [[], None, ["forse"]])
    def test_nessuna_modalita(self, db, modalita):
        inserisci_bando(db, 1, modalita_effettiva="ammesso", estratta_at="2026-09-10T10:00:00Z")
        assert bando_ids(db, modalita, 1000) == []

    def test_vuoto(self, db):
        assert bando_ids(db, ["ammesso"], 1000) == []

    @pytest.mark.parametrize("limite,atteso", [(5000, 2000), (None, 2000), (1500, 1500),
                                               (0, 0), (-1, 0)])
    def test_tetto(self, db, limite, atteso):
        db.execute(
            """insert into public.bando_partenariato
                 (bando_id, bando_slug, bando_titolo, stato, esito, modalita_effettiva,
                  estratta_at)
               select g, 'b-' || g, 'B ' || g, 'pronta', 'estratta', 'ammesso',
                      timestamptz '2026-01-01' + g * interval '1 minute'
               from generate_series(1, 2100) g"""
        )
        ids = bando_ids(db, ["ammesso"], limite)
        assert len(ids) == atteso
        assert ids == list(range(2100, 2100 - atteso, -1))


# ----------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0034, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0034, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")


class TestSicurezza0034:
    def test_inventario_del_file(self):
        # Se la migration crea altro, i test sotto devono coprirlo.
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == FUNZIONI_NUOVE

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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0034, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0034, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration e ogni fn_partenariat% presente
        nel DB è SECURITY DEFINER con search_path fissato, non eseguibile dai
        client (PUBLIC compreso) ed esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname like 'fn\_partenariat%%'"""
        ).fetchall()}
        assert FUNZIONI_NUOVE <= dal_db
        nomi = FUNZIONI_NEL_FILE | FUNZIONI_NUOVE | dal_db
        for nome in sorted(nomi):
            righe = db.execute(
                """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}'),
                          coalesce(p.proacl::text, '')
                   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and p.proname = %s""",
                (nome,),
            ).fetchall()
            assert len(righe) == 1, f"{nome}: {len(righe)} firme"
            oid, secdef, config, acl = righe[0]
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
                SQL_0034, re.M,
            ), nome

    def test_i_client_non_eseguono_le_rpc(self, db):
        """Prova diretta: con il ruolo authenticated la chiamata fallisce."""
        db.execute("set role authenticated")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute("select public.fn_partenariato_chiudi_stale()")
        finally:
            db.execute("reset role")
