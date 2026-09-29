"""Test funzionali della migration 0038 (matching dei partenariati, WP6).

Coprono: vincoli, chiavi e indici dei collegamenti societari (solo digest HMAC
esadecimali, quota 0..100) e del loro marker; la tabella delle notifiche
proattive e fn_partner_claim_notifica (dedup per azienda × call, tetto per
settimana ISO, settimane diverse, guardie fail-closed su azienda, call e
opt-in, concorrenza e ordine dei lock con una seconda connessione); le colonne
del fan-out su partner_calls e fn_partner_fanout_claim (una volta, ripresa
dopo il TTL, solo call pubblicate, mai dopo il completamento, concorrenza, e
le RPC del WP5 che non le scrivono); impostazioni email (default e unicità
del token), registro e ledger del digest (unicità utente × settimana), call
salvate; cascade da company_profiles, partner_calls e profiles; RLS,
privilegi e firme di tutto ciò che la migration crea.
Ogni test riceve un database fresco clonato dal template.
"""

import hashlib
import itertools
import re
import threading
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0038_partenariato_matching.sql"
)
SQL_0038 = MIGRATION.read_text(encoding="utf-8")

TABELLE_NUOVE = {
    "company_collegamenti", "company_collegamenti_stato", "partner_notifiche_proattive",
    "partner_email_settings", "partner_digest_runs", "partner_digest_invii",
    "partner_call_salvate",
}
FUNZIONI_NUOVE = {"fn_partner_claim_notifica", "fn_partner_fanout_claim"}
FIRME = {
    "fn_partner_claim_notifica": "fn_partner_claim_notifica(uuid,uuid,date,integer,integer,integer)",
    "fn_partner_fanout_claim": "fn_partner_fanout_claim(uuid,integer)",
}

VERSIONE = "2026-10-bozza-1"
NS_NOTIFICHE = "partner_notifiche_proattive"

REGOLA_F1 = {
    "id": "F1",
    "descrizione": "Costo della quota non oltre il 60% del fatturato medio",
    "ambito": "ciascun_partner",
    "numeratore": "costo_quota",
    "denominatore": "fatturato_medio_2",
    "operatore": "le",
    "soglia": "0.6",
    "soglia_variabile": None,
    "soglia_coefficiente": None,
    "unita": "rapporto",
}
REQ_A = {
    "testo": "Almeno un organismo di ricerca nel partenariato",
    "criterio": {"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]},
    "ambito": "consorzio",
    "cercato": True,
    "origine": "bando_partenariato",
}
POS_1 = {
    "titolo": "Organismo di ricerca",
    "tipi_soggetto": ["organismo_ricerca"],
    "competenze": ["prototipazione_rapida"],
    "quota_ipotizzata_pct": 30,
}

_seq = itertools.count(1)
_bandi = itertools.count(8000)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def vincolo_di(exc) -> str:
    return exc.value.diag.constraint_name or ""


def oggi(db) -> date:
    return db.execute("select (now() at time zone 'Europe/Rome')::date").fetchone()[0]


def lunedi(db, settimane: int = 0) -> date:
    """Lunedì della settimana ISO corrente (Europe/Rome), spostato di N settimane."""
    base = db.execute(
        "select date_trunc('week', now() at time zone 'Europe/Rome')::date").fetchone()[0]
    return base + timedelta(weeks=settimane)


def hmac_finto(valore: str) -> str:
    """Un digest esadecimale da 64 caratteri (la forma delle chiavi HMAC)."""
    return hashlib.sha256(valore.encode()).hexdigest()


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
        (owner, f"ACME {i} Srl", f"{i:011d}"),
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


def consenso(db, owner: str, company: str, azione: str = "concedi") -> dict:
    return db.execute(
        "select public.fn_partner_consenso(%s::uuid, %s::uuid, %s::uuid, %s::text, %s::text, "
        "'pagina_azienda', true, true)",
        (owner, company, owner, azione, VERSIONE),
    ).fetchone()[0]


def azienda(db, owner: str | None = None, plan_slug: str | None = None,
            *, opt_in: bool = False) -> tuple[str, str]:
    """Titolare + azienda con identità verificata; con opt_in il profilo partner è visibile."""
    owner = owner or new_user(db, plan_slug)
    company = make_company(db, owner)
    importa(db, company)
    if opt_in:
        consenso(db, owner, company)
    return owner, company


def owner_di(db, company: str) -> str:
    return str(db.execute(
        "select parent_id from public.company_profiles where id = %s", (company,)
    ).fetchone()[0])


def inserisci_call(db, company: str, *, stato: str = "pubblicata",
                   visibilita: str = "pubblica") -> str:
    """Call scritta direttamente (come dopo le RPC del WP5) nello stato richiesto."""
    owner = owner_di(db, company)
    pubblicata = stato in ("pubblicata", "chiusa_completata", "scaduta", "sospesa_moderazione")
    motivo = {"chiusa_completata": "creatore_completata",
              "chiusa_annullata": "creatore_annullata",
              "scaduta": "scadenza_call"}.get(stato)
    bid = next(_bandi)
    return str(db.execute(
        """insert into public.partner_calls
             (company_profile_id, family_parent_id, creato_da, bando_id, bando_slug,
              bando_titolo, ruolo_creatore, titolo, descrizione_pubblica, scadenza_call,
              regole_partenariato, regole_confermate_at, visibilita, stato, pubblicata_at,
              chiusa_at, motivo_chiusura, sospesa_at, stato_prima_sospensione)
           values (%s, %s, %s, %s, %s, 'Bando di prova', 'capofila',
                   'Cerchiamo un organismo di ricerca', 'Progetto di ricerca industriale.',
                   current_date + 30, '{}'::jsonb, now(), %s, %s,
                   case when %s then now() end, case when %s::text is not null then now() end,
                   %s, case when %s then now() end, case when %s then 'pubblicata' end)
           returning id""",
        (company, owner, owner, bid, f"bando-{bid}", visibilita, stato, pubblicata, motivo,
         motivo, stato == "sospesa_moderazione", stato == "sospesa_moderazione"),
    ).fetchone()[0])


def claim(db, company, call_id, settimana=None, *, tetto=3, copertura=2,
          punteggio=70) -> bool:
    if settimana is None:
        settimana = lunedi(db)
    return db.execute(
        "select public.fn_partner_claim_notifica(%s::uuid, %s::uuid, %s::date, %s::integer, "
        "%s::integer, %s::integer)",
        (company, call_id, settimana, tetto, copertura, punteggio),
    ).fetchone()[0]


def fanout(db, call_id, ttl=600) -> bool:
    return db.execute(
        "select public.fn_partner_fanout_claim(%s::uuid, %s::integer)", (call_id, ttl)
    ).fetchone()[0]


def call(db, call_id) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.partner_calls where id = %s", (call_id,)).fetchone()


def notifiche(db, company=None) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        if company is None:
            return cur.execute(
                "select * from public.partner_notifiche_proattive order by id").fetchall()
        return cur.execute(
            "select * from public.partner_notifiche_proattive where company_profile_id = %s "
            "order by id", (company,)).fetchall()


def conta(db, tabella: str) -> int:
    return db.execute(f"select count(*) from public.{tabella}").fetchone()[0]


def advisory_libero(conn, company) -> bool:
    """True se il lock advisory della notifica per l'azienda è libero (autocommit:
    preso e rilasciato nella stessa istruzione)."""
    return conn.execute(
        "select pg_try_advisory_xact_lock(hashtext(%s), hashtext(%s::uuid::text))",
        (NS_NOTIFICHE, company),
    ).fetchone()[0]


def in_attesa(monitor, pid, thread, esito, scadenza_s=10) -> None:
    """Attende che il backend `pid` sia bloccato da un lock."""
    scadenza = time.monotonic() + scadenza_s
    while not monitor.execute(
        "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
    ).fetchone()[0]:
        assert time.monotonic() < scadenza, "la seconda transazione non attende"
        assert thread.is_alive(), f"la seconda transazione non ha atteso: {esito}"
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
    for conn in conns:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        conn.close()


@pytest.fixture()
def scenario(db):
    """X crea una call pubblicata; Y (altro owner) ha l'opt-in visibile."""
    x_owner, x = azienda(db)
    y_owner, y = azienda(db, opt_in=True)
    return {"x_owner": x_owner, "x": x, "y_owner": y_owner, "y": y,
            "call": inserisci_call(db, x)}


# ------------------------------------------------------ collegamenti societari


class TestCollegamenti:
    def _inserisci(self, db, company, tipo="socio", chiave=None, quota=None):
        db.execute(
            "insert into public.company_collegamenti (company_profile_id, tipo, chiave, quota) "
            "values (%s, %s, %s, %s)",
            (company, tipo, chiave or hmac_finto(f"{company}{tipo}"), quota),
        )

    def test_forma_e_default(self, db):
        _, company = azienda(db)
        self._inserisci(db, company, "socio", hmac_finto("a"), 25.5)
        self._inserisci(db, company, "identita", hmac_finto("b"))
        with db.cursor(row_factory=dict_row) as cur:
            righe = cur.execute(
                "select * from public.company_collegamenti order by tipo").fetchall()
        assert [(r["tipo"], r["quota"]) for r in righe] == [
            ("identita", None), ("socio", Decimal("25.5"))]
        assert all(r["created_at"] is not None for r in righe)

    @pytest.mark.parametrize("tipo", ["identita", "nome", "socio", "partecipata",
                                      "controllata", "esponente", "gruppo", "capogruppo"])
    def test_tipi_ammessi(self, db, tipo):
        _, company = azienda(db)
        self._inserisci(db, company, tipo)
        assert conta(db, "company_collegamenti") == 1

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"tipo": "cf"}, "cc_tipo_check"),
        ({"tipo": "Socio"}, "cc_tipo_check"),
        ({"chiave": "A" * 64}, "cc_chiave_check"),
        ({"chiave": "0" * 63}, "cc_chiave_check"),
        ({"chiave": "0" * 65}, "cc_chiave_check"),
        ({"chiave": "g" * 64}, "cc_chiave_check"),
        ({"chiave": "RSSMRA80A01H501U"}, "cc_chiave_check"),
        ({"chiave": "01234567890"}, "cc_chiave_check"),
        ({"chiave": "cf:" + "0" * 61}, "cc_chiave_check"),
        ({"chiave": "0" * 64 + "\n"}, "cc_chiave_check"),
        ({"quota": Decimal("-0.001")}, "cc_quota_check"),
        ({"quota": Decimal("100.001")}, "cc_quota_check"),
    ])
    def test_vincoli(self, db, colonne, vincolo):
        _, company = azienda(db)
        riga = {"tipo": "socio", "chiave": hmac_finto("x"), "quota": None}
        riga.update(colonne)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._inserisci(db, company, riga["tipo"], riga["chiave"], riga["quota"])
        assert vincolo_di(exc) == vincolo

    @pytest.mark.parametrize("quota", ["0", "100", "33.333", "25"])
    def test_quota_ai_bordi(self, db, quota):
        _, company = azienda(db)
        self._inserisci(db, company, "partecipata", hmac_finto("p"), Decimal(quota))
        assert db.execute(
            "select quota from public.company_collegamenti").fetchone()[0] == Decimal(quota)

    @pytest.mark.parametrize(("tipo", "chiave"), [(None, "0" * 64), ("socio", None)])
    def test_campi_obbligatori(self, db, tipo, chiave):
        _, company = azienda(db)
        with pytest.raises(psycopg.errors.NotNullViolation):
            db.execute("insert into public.company_collegamenti (company_profile_id, tipo, "
                       "chiave) values (%s, %s, %s)", (company, tipo, chiave))

    def test_chiave_primaria(self, db):
        _, a = azienda(db)
        _, b = azienda(db)
        k = hmac_finto("socio comune")
        self._inserisci(db, a, "socio", k, 60)
        with pytest.raises(psycopg.errors.UniqueViolation):
            self._inserisci(db, a, "socio", k, 10)
        self._inserisci(db, a, "esponente", k)  # stessa chiave, tipo diverso
        self._inserisci(db, b, "socio", k, 60)  # stessa chiave, altra azienda
        assert conta(db, "company_collegamenti") == 3
        # L'incrocio tra aziende diverse sulla stessa chiave.
        assert db.execute(
            "select count(distinct company_profile_id) from public.company_collegamenti "
            "where chiave = %s", (k,)).fetchone()[0] == 2

    def test_azienda_inesistente(self, db):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            self._inserisci(db, str(uuid.uuid4()), "socio", hmac_finto("z"))

    def test_indici(self, db):
        indici = {r[0]: r[1] for r in db.execute(
            "select indexname, indexdef from pg_indexes where schemaname = 'public' "
            "and tablename in ('company_collegamenti', 'company_collegamenti_stato')"
        ).fetchall()}
        assert "(chiave)" in indici["company_collegamenti_chiave_idx"]
        assert "(company_profile_id, tipo, chiave)" in indici["company_collegamenti_pkey"]
        assert "(company_profile_id)" in indici["company_collegamenti_stato_pkey"]


class TestCollegamentiStato:
    def test_default_e_unicita(self, db):
        _, company = azienda(db)
        db.execute("insert into public.company_collegamenti_stato "
                   "(company_profile_id, algoritmo_versione) values (%s, 1)", (company,))
        with db.cursor(row_factory=dict_row) as cur:
            r = cur.execute("select * from public.company_collegamenti_stato").fetchone()
        assert r["algoritmo_versione"] == 1 and r["fonte_fetched_at"] is None
        assert r["calcolato_at"] is not None
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("insert into public.company_collegamenti_stato "
                       "(company_profile_id, algoritmo_versione) values (%s, 2)", (company,))
        # Upsert del marker (come il backend dopo un ricalcolo).
        db.execute(
            "insert into public.company_collegamenti_stato (company_profile_id, "
            "algoritmo_versione, fonte_fetched_at) values (%s, 2, now()) "
            "on conflict (company_profile_id) do update set algoritmo_versione = "
            "excluded.algoritmo_versione, fonte_fetched_at = excluded.fonte_fetched_at, "
            "calcolato_at = now()", (company,))
        assert db.execute("select algoritmo_versione from public.company_collegamenti_stato"
                          ).fetchone()[0] == 2

    @pytest.mark.parametrize("versione", [0, -1])
    def test_versione_almeno_uno(self, db, versione):
        _, company = azienda(db)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("insert into public.company_collegamenti_stato "
                       "(company_profile_id, algoritmo_versione) values (%s, %s)",
                       (company, versione))
        assert vincolo_di(exc) == "ccs_algoritmo_versione_check"

    def test_versione_obbligatoria(self, db):
        _, company = azienda(db)
        with pytest.raises(psycopg.errors.NotNullViolation):
            db.execute("insert into public.company_collegamenti_stato (company_profile_id) "
                       "values (%s)", (company,))


# -------------------------------------------------------- notifiche proattive


class TestNotificheTabella:
    def _inserisci(self, db, company, call_id, **colonne):
        riga = {"settimana": lunedi(db), "copertura": 1, "punteggio": 60}
        riga.update(colonne)
        db.execute(
            "insert into public.partner_notifiche_proattive (company_profile_id, "
            "partner_call_id, settimana, copertura, punteggio) values (%s, %s, %s, %s, %s)",
            (company, call_id, riga["settimana"], riga["copertura"], riga["punteggio"]),
        )

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"settimana_delta": 1}, "pnp_settimana_check"),
        ({"settimana_delta": 6}, "pnp_settimana_check"),
        ({"copertura": -1}, "pnp_copertura_check"),
        ({"punteggio": -1}, "pnp_punteggio_check"),
        ({"punteggio": 101}, "pnp_punteggio_check"),
    ])
    def test_vincoli(self, db, scenario, colonne, vincolo):
        colonne = dict(colonne)
        if "settimana_delta" in colonne:
            colonne["settimana"] = lunedi(db) + timedelta(days=colonne.pop("settimana_delta"))
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._inserisci(db, scenario["y"], scenario["call"], **colonne)
        assert vincolo_di(exc) == vincolo

    def test_bordi_ammessi_e_default(self, db, scenario):
        self._inserisci(db, scenario["y"], scenario["call"], copertura=0, punteggio=100)
        r = notifiche(db)[0]
        assert r["digest_incluso_at"] is None and r["created_at"] is not None
        assert (r["copertura"], r["punteggio"]) == (0, 100)

    def test_unica_per_azienda_e_call(self, db, scenario):
        self._inserisci(db, scenario["y"], scenario["call"])
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            self._inserisci(db, scenario["y"], scenario["call"], settimana=lunedi(db, 1))
        assert vincolo_di(exc) == "pnp_azienda_call_key"

    def test_indici(self, db):
        indici = {r[0]: r[1] for r in db.execute(
            "select indexname, indexdef from pg_indexes where schemaname = 'public' "
            "and tablename = 'partner_notifiche_proattive'").fetchall()}
        assert "(company_profile_id, settimana)" in indici["pnp_azienda_settimana_idx"]
        assert "(partner_call_id)" in indici["pnp_call_idx"]
        assert "UNIQUE" in indici["pnp_azienda_call_key"]


class TestClaimNotifica:
    def test_primo_claim_inserisce(self, db, scenario):
        assert claim(db, scenario["y"], scenario["call"], copertura=2, punteggio=83) is True
        [r] = notifiche(db)
        assert str(r["company_profile_id"]) == scenario["y"]
        assert str(r["partner_call_id"]) == scenario["call"]
        assert r["settimana"] == lunedi(db)
        assert (r["copertura"], r["punteggio"]) == (2, 83)
        assert r["digest_incluso_at"] is None

    def test_dedup(self, db, scenario):
        assert claim(db, scenario["y"], scenario["call"], punteggio=60) is True
        assert claim(db, scenario["y"], scenario["call"], punteggio=90) is False
        # Anche in un'altra settimana e con il tetto libero.
        assert claim(db, scenario["y"], scenario["call"], lunedi(db, 1), tetto=99) is False
        [r] = notifiche(db)
        assert r["punteggio"] == 60 and r["settimana"] == lunedi(db)

    def test_dedup_prima_del_tetto(self, db, scenario):
        """Con il tetto già pieno una notifica esistente resta false (nessun errore)."""
        assert claim(db, scenario["y"], scenario["call"], tetto=1) is True
        assert claim(db, scenario["y"], scenario["call"], tetto=1) is False
        assert len(notifiche(db)) == 1

    def test_tetto_settimanale(self, db, scenario):
        calls = [scenario["call"]] + [inserisci_call(db, scenario["x"]) for _ in range(3)]
        esiti = [claim(db, scenario["y"], c, tetto=3) for c in calls]
        assert esiti == [True, True, True, False]
        assert {str(r["partner_call_id"]) for r in notifiche(db)} == set(calls[:3])
        # Un tetto più alto (Settings) libera il posto.
        assert claim(db, scenario["y"], calls[3], tetto=4) is True

    def test_tetto_zero(self, db, scenario):
        assert claim(db, scenario["y"], scenario["call"], tetto=0) is False
        assert conta(db, "partner_notifiche_proattive") == 0

    def test_settimane_diverse_indipendenti(self, db, scenario):
        calls = [scenario["call"]] + [inserisci_call(db, scenario["x"]) for _ in range(3)]
        for c in calls[:2]:
            assert claim(db, scenario["y"], c, lunedi(db), tetto=2) is True
        assert claim(db, scenario["y"], calls[2], lunedi(db), tetto=2) is False
        assert claim(db, scenario["y"], calls[2], lunedi(db, 1), tetto=2) is True
        assert claim(db, scenario["y"], calls[3], lunedi(db, -1), tetto=2) is True

    def test_tetto_per_azienda(self, db, scenario):
        _, z = azienda(db, opt_in=True)
        calls = [scenario["call"], inserisci_call(db, scenario["x"])]
        assert claim(db, scenario["y"], calls[0], tetto=1) is True
        assert claim(db, scenario["y"], calls[1], tetto=1) is False
        assert claim(db, z, calls[1], tetto=1) is True

    @pytest.mark.parametrize("campo", [
        "company_null", "call_null", "settimana_null", "martedi", "domenica",
        "tetto_null", "tetto_negativo", "copertura_null", "copertura_negativa",
        "copertura_troppo_grande", "punteggio_null", "punteggio_negativo",
        "punteggio_oltre_100",
    ])
    def test_parametri_non_validi(self, db, scenario, campo):
        args = {"company": scenario["y"], "call_id": scenario["call"], "settimana": lunedi(db),
                "tetto": 3, "copertura": 2, "punteggio": 70}
        args.update({
            "company_null": {"company": None}, "call_null": {"call_id": None},
            "settimana_null": {"settimana": None},
            "martedi": {"settimana": lunedi(db) + timedelta(days=1)},
            "domenica": {"settimana": lunedi(db) - timedelta(days=1)},
            "tetto_null": {"tetto": None}, "tetto_negativo": {"tetto": -1},
            "copertura_null": {"copertura": None}, "copertura_negativa": {"copertura": -1},
            "copertura_troppo_grande": {"copertura": 32768},
            "punteggio_null": {"punteggio": None}, "punteggio_negativo": {"punteggio": -1},
            "punteggio_oltre_100": {"punteggio": 101},
        }[campo])
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "select public.fn_partner_claim_notifica(%s::uuid, %s::uuid, %s::date, "
                "%s::integer, %s::integer, %s::integer)",
                (args["company"], args["call_id"], args["settimana"], args["tetto"],
                 args["copertura"], args["punteggio"]),
            )
        assert detail_of(exc) == "parametri_non_validi"
        assert conta(db, "partner_notifiche_proattive") == 0

    @pytest.mark.parametrize(("copertura", "punteggio"), [(0, 0), (32767, 100)])
    def test_parametri_ai_bordi(self, db, scenario, copertura, punteggio):
        assert claim(db, scenario["y"], scenario["call"], copertura=copertura,
                     punteggio=punteggio) is True

    @pytest.mark.parametrize("caso", ["senza_profilo", "revocato", "sospeso"])
    def test_senza_opt_in_false(self, db, scenario, caso):
        if caso == "senza_profilo":
            _, dest = azienda(db)
        else:
            dest = scenario["y"]
            if caso == "revocato":
                consenso(db, scenario["y_owner"], dest, "revoca")
            else:
                with db.transaction():
                    db.execute("select set_config('app.partner_consenso', 'on', true)")
                    db.execute("update public.company_partner_profiles set sospeso_at = now(), "
                               "sospeso_motivo = 'x' where company_profile_id = %s", (dest,))
        assert claim(db, dest, scenario["call"]) is False
        assert conta(db, "partner_notifiche_proattive") == 0

    @pytest.mark.parametrize(("stato", "visibilita"), [
        ("bozza", "pubblica"), ("chiusa_completata", "pubblica"),
        ("chiusa_annullata", "pubblica"), ("scaduta", "pubblica"),
        ("sospesa_moderazione", "pubblica"), ("pubblicata", "solo_invitati"),
    ])
    def test_call_non_pubblica_false(self, db, scenario, stato, visibilita):
        c = inserisci_call(db, scenario["x"], stato=stato, visibilita=visibilita)
        assert claim(db, scenario["y"], c) is False
        assert conta(db, "partner_notifiche_proattive") == 0

    def test_stessa_azienda_false(self, db, scenario):
        consenso(db, scenario["x_owner"], scenario["x"])
        assert claim(db, scenario["x"], scenario["call"]) is False
        # Anche se l'owner registrato sulla call non coincide più (azienda passata a
        # un altro titolare dopo la creazione): la guardia sull'azienda basta da sola.
        db.execute("update public.partner_calls set family_parent_id = %s where id = %s",
                   (str(uuid.uuid4()), scenario["call"]))
        assert claim(db, scenario["x"], scenario["call"]) is False
        assert conta(db, "partner_notifiche_proattive") == 0

    def test_stesso_owner_false(self, db, scenario):
        """Advisor con due aziende: la seconda (con opt-in) non riceve la call della prima."""
        _, b = azienda(db, scenario["x_owner"], opt_in=True)
        assert claim(db, b, scenario["call"]) is False
        assert claim(db, scenario["y"], scenario["call"]) is True

    @pytest.mark.parametrize("caso", ["eliminata", "archiviata", "inesistente"])
    def test_azienda_non_viva_false(self, db, scenario, caso):
        dest = scenario["y"]
        if caso == "inesistente":
            dest = str(uuid.uuid4())
        else:
            colonna = "deleted_at" if caso == "eliminata" else "archived_at"
            db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                       (dest,))
        assert claim(db, dest, scenario["call"]) is False
        assert conta(db, "partner_notifiche_proattive") == 0

    @pytest.mark.parametrize("colonna", ["deleted_at", "archived_at"])
    def test_azienda_non_viva_anche_con_il_profilo_visibile(self, db, scenario, colonna):
        """Difesa propria, non solo la revoca del trigger della 0035: un profilo
        rimasto visibile (stato incoerente) su un'azienda non viva non riceve nulla."""
        _, dest = azienda(db)
        db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                   (dest,))
        with db.transaction():
            db.execute("select set_config('app.partner_consenso', 'on', true)")
            db.execute("insert into public.company_partner_profiles (company_profile_id, "
                       "family_parent_id, visibile_come_partner, consenso_versione, "
                       "consenso_at) values (%s, %s, true, %s, now())",
                       (dest, owner_di(db, dest), VERSIONE))
        assert claim(db, dest, scenario["call"]) is False
        assert conta(db, "partner_notifiche_proattive") == 0

    def test_call_inesistente_false(self, db, scenario):
        assert claim(db, scenario["y"], str(uuid.uuid4())) is False

    def test_call_cancellata_libera_il_tetto(self, db, scenario):
        altra = inserisci_call(db, scenario["x"])
        assert claim(db, scenario["y"], scenario["call"], tetto=1) is True
        assert claim(db, scenario["y"], altra, tetto=1) is False
        db.execute("delete from public.partner_calls where id = %s", (scenario["call"],))
        assert claim(db, scenario["y"], altra, tetto=1) is True


class TestClaimConcorrenza:
    def test_seconda_connessione_attende_l_advisory(self, db, scenario):
        altra_call = inserisci_call(db, scenario["x"])
        altra = psycopg.connect(db.info.dsn)
        try:
            assert claim(altra, scenario["y"], scenario["call"]) is True  # non committata
            db.execute("set lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                claim(db, scenario["y"], altra_call)
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        assert claim(db, scenario["y"], altra_call) is True

    @pytest.mark.parametrize("fine", ["commit", "rollback"])
    def test_tetto_esatto_in_concorrenza(self, db, scenario, fine):
        """Tetto 1: la seconda transazione attende la prima e rilegge il conteggio."""
        altra_call = inserisci_call(db, scenario["x"])
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: claim(db, scenario["y"], altra_call, tetto=1))
        try:
            assert claim(altra, scenario["y"], scenario["call"], tetto=1) is True
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            getattr(altra, fine)()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert "errore" not in esito, esito
        assert esito["out"] is (fine == "rollback")
        assert len(notifiche(db, scenario["y"])) == 1

    def test_stessa_coppia_in_concorrenza(self, db, scenario):
        """Dedup: due claim della stessa coppia, uno solo vince."""
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: claim(db, scenario["y"], scenario["call"], tetto=9))
        try:
            assert claim(altra, scenario["y"], scenario["call"], tetto=9) is True
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") is False, esito
        assert len(notifiche(db)) == 1

    def test_aziende_diverse_non_si_bloccano(self, db, scenario):
        _, z = azienda(db, opt_in=True)
        altra = psycopg.connect(db.info.dsn)
        try:
            assert claim(altra, scenario["y"], scenario["call"]) is True  # non committata
            db.execute("set lock_timeout = '300ms'")
            assert claim(db, z, scenario["call"]) is True
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()

    def test_azienda_prima_dell_advisory(self, db, scenario):
        """Con la riga dell'azienda bloccata (FOR UPDATE) il claim attende SENZA aver
        ancora preso il lock advisory (ordine azienda → advisory)."""
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: claim(db, scenario["y"], scenario["call"]))
        try:
            altra.execute("select 1 from public.company_profiles where id = %s for update",
                          (scenario["y"],))
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            assert advisory_libero(terza, scenario["y"]) is True
            altra.rollback()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") is True, esito

    def test_advisory_prima_della_call(self, db, scenario):
        """Con la call bloccata (FOR UPDATE, come una chiusura in corso) il claim
        attende tenendo già il lock advisory dell'azienda (ordine advisory → call)."""
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: claim(db, scenario["y"], scenario["call"]))
        try:
            altra.execute("select 1 from public.partner_calls where id = %s for update",
                          (scenario["call"],))
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            assert advisory_libero(terza, scenario["y"]) is False
            altra.rollback()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") is True, esito

    def test_chiusura_concorrente_rende_false(self, db, scenario):
        """Il claim attende la chiusura della call in corso e poi rilegge lo stato."""
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: claim(db, scenario["y"], scenario["call"]))
        try:
            altra.execute(
                "update public.partner_calls set stato = 'chiusa_annullata', chiusa_at = now(), "
                "motivo_chiusura = 'creatore_annullata' where id = %s", (scenario["call"],))
            altra.execute("select 1 from public.partner_calls where id = %s for update",
                          (scenario["call"],))
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") is False, esito
        assert conta(db, "partner_notifiche_proattive") == 0

    def test_non_blocca_le_rpc_che_prendono_l_azienda(self, db, scenario):
        """FOR NO KEY UPDATE sull'azienda (il lock delle RPC del modulo) è compatibile
        con il FOR KEY SHARE del claim: nessuna attesa."""
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute("select 1 from public.company_profiles where id = %s "
                          "for no key update", (scenario["y"],))
            db.execute("set lock_timeout = '300ms'")
            assert claim(db, scenario["y"], scenario["call"]) is True
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()

    def test_modifica_della_call_in_corso(self, db, scenario):
        """Un UPDATE della call non ancora committato (per esempio lo scheduler o il
        completamento del fan-out) si attende; poi il claim passa."""
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: claim(db, scenario["y"], scenario["call"]))
        try:
            altra.execute("update public.partner_calls set bando_verificato_at = now() "
                          "where id = %s", (scenario["call"],))
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") is True, esito

    @pytest.mark.parametrize("caso", ["revoca", "sospensione", "soft_delete"])
    def test_revoca_in_corso_rende_false(self, db, scenario, caso):
        """Revoca immediata (P6): una revoca, una sospensione o un soft delete
        dell'azienda (che revoca la visibilità) non ancora committati si attendono
        PRIMA del lock advisory, e dopo il commit il claim è false."""
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: claim(db, scenario["y"], scenario["call"]))
        try:
            if caso == "revoca":
                consenso(altra, scenario["y_owner"], scenario["y"], "revoca")
            elif caso == "sospensione":
                altra.execute("select set_config('app.partner_consenso', 'on', true)")
                altra.execute("update public.company_partner_profiles set sospeso_at = now(), "
                              "sospeso_motivo = 'x' where company_profile_id = %s",
                              (scenario["y"],))
            else:
                altra.execute("update public.company_profiles set deleted_at = now() "
                              "where id = %s", (scenario["y"],))
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            assert advisory_libero(terza, scenario["y"]) is True
            altra.commit()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert esito.get("out") is False, esito
        assert conta(db, "partner_notifiche_proattive") == 0


# --------------------------------------------------------------------- fan-out


class TestFanoutClaim:
    def test_colonne_nuove(self, db, scenario):
        tipi = dict(db.execute(
            "select column_name, data_type || ':' || is_nullable || ':' || "
            "coalesce(column_default, '-') from information_schema.columns "
            "where table_schema = 'public' and table_name = 'partner_calls' "
            "and column_name like 'fanout%%'").fetchall())
        assert tipi == {
            "fanout_claim_at": "timestamp with time zone:YES:-",
            "fanout_completato_at": "timestamp with time zone:YES:-",
        }
        r = call(db, scenario["call"])
        assert r["fanout_claim_at"] is None and r["fanout_completato_at"] is None

    def test_una_volta(self, db, scenario):
        assert fanout(db, scenario["call"]) is True
        preso = call(db, scenario["call"])["fanout_claim_at"]
        assert preso is not None
        assert fanout(db, scenario["call"]) is False
        assert call(db, scenario["call"])["fanout_claim_at"] == preso

    @pytest.mark.parametrize(("eta_s", "ttl", "atteso"), [
        (601, 600, True), (600, 600, True), (599, 600, False), (5, 1, True), (3600, 86400, False),
    ])
    def test_ripresa_dopo_il_ttl(self, db, scenario, eta_s, ttl, atteso):
        db.execute("update public.partner_calls set fanout_claim_at = now() - "
                   "make_interval(secs => %s) where id = %s", (eta_s, scenario["call"]))
        prima = call(db, scenario["call"])["fanout_claim_at"]
        assert fanout(db, scenario["call"], ttl) is atteso
        dopo = call(db, scenario["call"])["fanout_claim_at"]
        assert (dopo > prima) is atteso

    def test_ttl_al_bordo_esatto(self, db, scenario):
        """Nella stessa transazione now() non cambia: un claim vecchio esattamente
        quanto il TTL è già scaduto."""
        with db.transaction():
            db.execute("update public.partner_calls set fanout_claim_at = now() - "
                       "interval '600 seconds' where id = %s", (scenario["call"],))
            assert fanout(db, scenario["call"], 600) is True
        with db.transaction():
            db.execute("update public.partner_calls set fanout_claim_at = now() - "
                       "interval '599.999 seconds' where id = %s", (scenario["call"],))
            assert fanout(db, scenario["call"], 600) is False

    def test_mai_dopo_il_completamento(self, db, scenario):
        assert fanout(db, scenario["call"]) is True
        db.execute("update public.partner_calls set fanout_completato_at = now(), "
                   "fanout_claim_at = now() - interval '2 days' where id = %s",
                   (scenario["call"],))
        assert fanout(db, scenario["call"], 1) is False

    @pytest.mark.parametrize("stato", ["bozza", "chiusa_completata", "chiusa_annullata",
                                       "scaduta", "sospesa_moderazione"])
    def test_solo_call_pubblicate(self, db, scenario, stato):
        c = inserisci_call(db, scenario["x"], stato=stato)
        assert fanout(db, c) is False
        assert call(db, c)["fanout_claim_at"] is None

    def test_call_solo_invitati_pubblicata(self, db, scenario):
        """Il claim guarda solo lo stato: il fan-out di una call solo_invitati non
        notifica nessuno (fn_partner_claim_notifica la rifiuta)."""
        c = inserisci_call(db, scenario["x"], visibilita="solo_invitati")
        assert fanout(db, c) is True
        assert claim(db, scenario["y"], c) is False

    def test_call_inesistente(self, db):
        assert fanout(db, str(uuid.uuid4())) is False

    @pytest.mark.parametrize(("p_call", "ttl"), [
        (None, 600), ("call", None), ("call", 0), ("call", -1), ("call", 86401),
    ])
    def test_parametri_non_validi(self, db, scenario, p_call, ttl):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            fanout(db, scenario["call"] if p_call == "call" else None, ttl)
        assert detail_of(exc) == "parametri_non_validi"
        assert call(db, scenario["call"])["fanout_claim_at"] is None

    def test_completato_richiede_il_claim(self, db, scenario):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.partner_calls set fanout_completato_at = now() "
                       "where id = %s", (scenario["call"],))
        assert vincolo_di(exc) == "pcall_fanout_coerente"

    @pytest.mark.parametrize("fine", ["commit", "rollback"])
    def test_concorrenza(self, db, scenario, fine):
        altra = psycopg.connect(db.info.dsn)
        terza = psycopg.connect(db.info.dsn, autocommit=True)
        thread, esito = in_thread(lambda: fanout(db, scenario["call"]))
        try:
            assert fanout(altra, scenario["call"]) is True  # non committato
            thread.start()
            in_attesa(terza, db.info.backend_pid, thread, esito)
            getattr(altra, fine)()
            thread.join(timeout=10)
        finally:
            chiudi_tutto(altra, terza)
            if thread.is_alive():
                thread.join(timeout=10)
        assert "errore" not in esito, esito
        assert esito["out"] is (fine == "rollback")

    @pytest.mark.parametrize("riga", ["owner", "azienda"])
    def test_blocca_solo_la_call(self, db, scenario, riga):
        """Nessun lock di owner o azienda: con le loro righe bloccate il claim passa."""
        tabella, rid = (("profiles", scenario["x_owner"]) if riga == "owner"
                        else ("company_profiles", scenario["x"]))
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute(f"select 1 from public.{tabella} where id = %s for update", (rid,))
            db.execute("set lock_timeout = '300ms'")
            assert fanout(db, scenario["call"]) is True
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()

    def test_indice_pendenti(self, db):
        indexdef = db.execute(
            "select indexdef from pg_indexes where schemaname = 'public' "
            "and indexname = 'partner_calls_fanout_pendenti_idx'").fetchone()[0]
        assert "(pubblicata_at)" in indexdef
        assert "'pubblicata'" in indexdef and "fanout_completato_at IS NULL" in indexdef


class TestFanoutConLeRpcDelWp5:
    """Le RPC della 0037 funzionano con le colonne nuove e non le scrivono."""

    @staticmethod
    def _bando(db) -> dict:
        bid = next(_bandi)
        return {"id": bid, "slug": f"bando-{bid}", "titolo": f"Bando di prova {bid}",
                "scadenza": (oggi(db) + timedelta(days=90)).isoformat(),
                "programma_id": 7, "tipologia_id": 3, "stato_effettivo": "aperto"}

    @staticmethod
    def _regole() -> dict:
        return {
            "versione": 1,
            "fonte": {"estratta_at": "2026-09-01T10:00:00Z", "prompt_version": 1,
                      "modalita_effettiva": "ammesso"},
            "modalita": {"valore": "ammesso", "origine_voce": "confermata"},
            "forme_ammesse": [{"forma": "ats", "origine_voce": "confermata"}],
            "costituzione": {"valore": "da_costituire", "origine_voce": "modificata"},
            "partner_min": {"valore": 2, "origine_voce": "confermata"},
            "partner_max": None,
            "composizione": [{"id": "K1", "tipo_soggetto": "organismo_ricerca", "minimo": 1,
                              "origine_voce": "confermata"}],
            "quote": [],
            "vincoli": [],
            "regole_finanziarie": [dict(REGOLA_F1, origine_voce="confermata",
                                        citazione={"testo": "..."})],
            "documenti_richiesti": [],
        }

    def _crea(self, db, owner, company, dati) -> dict:
        return db.execute(
            "select public.fn_partner_call_crea_bozza(%s::uuid, %s::uuid, %s::uuid, "
            "%s::jsonb, %s::jsonb, 5)",
            (owner, company, owner, Jsonb(self._bando(db)), Jsonb(dati)),
        ).fetchone()[0]

    def _pubblicata(self, db, owner, company) -> str:
        c = self._crea(db, owner, company, {
            "ruolo_creatore": "capofila", "titolo": "Cerchiamo un organismo di ricerca",
            "descrizione_pubblica": "Progetto di ricerca industriale su nuovi materiali."})["id"]
        db.execute("select public.fn_partner_call_conferma_regole(%s::uuid, %s::uuid, "
                   "%s::uuid, %s::uuid, %s::jsonb, false)",
                   (owner, company, owner, c, Jsonb(self._regole())))
        db.execute("select public.fn_partner_call_sostituisci_requisiti(%s::uuid, %s::uuid, "
                   "%s::uuid, %s::uuid, %s::jsonb)", (owner, company, owner, c, Jsonb([REQ_A])))
        db.execute("select public.fn_partner_call_sostituisci_posizioni(%s::uuid, %s::uuid, "
                   "%s::uuid, %s::uuid, %s::jsonb)", (owner, company, owner, c, Jsonb([POS_1])))
        db.execute("select public.fn_partner_call_pubblica(%s::uuid, %s::uuid, %s::uuid, "
                   "%s::uuid, 'aperto', %s::date, %s::date, true)",
                   (owner, company, owner, c, oggi(db) + timedelta(days=90),
                    oggi(db) + timedelta(days=30)))
        return str(c)

    def test_call_pubblicata_dalle_rpc(self, db):
        x_owner, x = azienda(db, plan_slug="smart")
        _, y = azienda(db, opt_in=True)
        c = self._pubblicata(db, x_owner, x)
        assert call(db, c)["stato"] == "pubblicata"
        assert fanout(db, c) is True
        assert claim(db, y, c) is True
        db.execute("update public.partner_calls set fanout_completato_at = now() where id = %s",
                   (c,))
        assert fanout(db, c, 1) is False
        # Una modifica dopo la pubblicazione non tocca il fan-out.
        db.execute("select public.fn_partner_call_aggiorna(%s::uuid, %s::uuid, %s::uuid, "
                   "%s::uuid, %s::jsonb)",
                   (x_owner, x, x_owner, c, Jsonb({"descrizione_pubblica": "Seconda versione"})))
        r = call(db, c)
        assert r["versione"] == 2 and r["fanout_completato_at"] is not None

    @pytest.mark.parametrize("campo", ["fanout_claim_at", "fanout_completato_at"])
    def test_le_rpc_non_scrivono_il_fan_out(self, db, campo):
        owner, company = azienda(db, plan_slug="smart")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            self._crea(db, owner, company, {"ruolo_creatore": "capofila", campo: "2026-01-01"})
        assert detail_of(exc) == "campo_non_modificabile"
        c = self._crea(db, owner, company, {"ruolo_creatore": "capofila"})["id"]
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute("select public.fn_partner_call_aggiorna(%s::uuid, %s::uuid, %s::uuid, "
                       "%s::uuid, %s::jsonb)",
                       (owner, company, owner, c, Jsonb({campo: "2026-01-01"})))
        assert detail_of(exc) == "campo_non_modificabile"
        assert call(db, c)[campo] is None


# ----------------------------------------------------------------- email e digest


class TestEmailSettings:
    def test_default(self, db):
        user = new_user(db)
        db.execute("insert into public.partner_email_settings (user_id) values (%s)", (user,))
        with db.cursor(row_factory=dict_row) as cur:
            r = cur.execute("select * from public.partner_email_settings").fetchone()
        assert r["digest_abilitato"] is True and r["eventi_abilitati"] is True
        assert isinstance(r["unsubscribe_token"], uuid.UUID)
        assert r["created_at"] is not None and r["updated_at"] is not None

    def test_token_unico_e_diverso(self, db):
        a, b = new_user(db), new_user(db)
        for user in (a, b):
            db.execute("insert into public.partner_email_settings (user_id) values (%s)",
                       (user,))
        token_a, token_b = [r[0] for r in db.execute(
            "select unsubscribe_token from public.partner_email_settings order by created_at"
        ).fetchall()]
        assert token_a != token_b
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            db.execute("update public.partner_email_settings set unsubscribe_token = %s "
                       "where user_id = %s", (token_a, b))
        assert vincolo_di(exc) == "partner_email_settings_unsubscribe_token_key"
        # Diverso anche dal token degli alert sui bandi (tabella separata).
        db.execute("insert into public.bando_alert_settings (user_id) values (%s)", (a,))
        assert db.execute(
            "select a.unsubscribe_token <> p.unsubscribe_token from public.bando_alert_settings a "
            "join public.partner_email_settings p using (user_id)").fetchone()[0] is True

    def test_una_riga_per_utente_e_upsert(self, db):
        user = new_user(db)
        db.execute("insert into public.partner_email_settings (user_id) values (%s)", (user,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("insert into public.partner_email_settings (user_id) values (%s)",
                       (user,))
        token = db.execute("select unsubscribe_token from public.partner_email_settings"
                           ).fetchone()[0]
        # Riga pigra: l'upsert che non cambia il token (come il backend all'invio).
        db.execute("insert into public.partner_email_settings (user_id) values (%s) "
                   "on conflict (user_id) do nothing", (user,))
        assert db.execute("select unsubscribe_token from public.partner_email_settings"
                          ).fetchone()[0] == token

    @pytest.mark.parametrize("colonna", ["digest_abilitato", "eventi_abilitati",
                                         "unsubscribe_token"])
    def test_non_null(self, db, colonna):
        user = new_user(db)
        with pytest.raises(psycopg.errors.NotNullViolation):
            db.execute(f"insert into public.partner_email_settings (user_id, {colonna}) "
                       "values (%s, null)", (user,))

    def test_trigger_updated_at(self, db):
        user = new_user(db)
        db.execute("insert into public.partner_email_settings (user_id, updated_at) "
                   "values (%s, now() - interval '1 day')", (user,))
        db.execute("update public.partner_email_settings set digest_abilitato = false")
        r = db.execute("select updated_at > now() - interval '1 minute', digest_abilitato, "
                       "eventi_abilitati from public.partner_email_settings").fetchone()
        assert r == (True, False, True)

    def test_utente_inesistente(self, db):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute("insert into public.partner_email_settings (user_id) values (%s)",
                       (str(uuid.uuid4()),))


class TestDigestRuns:
    def test_claim_per_insert(self, db):
        settimana = lunedi(db)
        db.execute("insert into public.partner_digest_runs (settimana) values (%s)",
                   (settimana,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("insert into public.partner_digest_runs (settimana) values (%s)",
                       (settimana,))
        db.execute("insert into public.partner_digest_runs (settimana) values (%s)",
                   (lunedi(db, -1),))
        r = db.execute("select riepilogo, created_at is not null from "
                       "public.partner_digest_runs where settimana = %s",
                       (settimana,)).fetchone()
        assert r == ({}, True)

    @pytest.mark.parametrize("riepilogo", [[], "x", 1, None])
    def test_riepilogo_oggetto(self, db, riepilogo):
        errore = (psycopg.errors.NotNullViolation if riepilogo is None
                  else psycopg.errors.CheckViolation)
        with pytest.raises(errore) as exc:
            db.execute("insert into public.partner_digest_runs (settimana, riepilogo) "
                       "values (%s, %s)", (lunedi(db), Jsonb(riepilogo) if riepilogo is not None
                                           else None))
        if riepilogo is not None:
            assert vincolo_di(exc) == "pdr_riepilogo_check"

    @pytest.mark.parametrize("giorni", [1, 2, 3, 4, 5, 6])
    def test_solo_lunedi(self, db, giorni):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("insert into public.partner_digest_runs (settimana) values (%s)",
                       (lunedi(db) + timedelta(days=giorni),))
        assert vincolo_di(exc) == "pdr_settimana_check"


class TestDigestInvii:
    def _inserisci(self, db, user, settimana=None, **colonne) -> int:
        colonne = {"settimana": settimana or lunedi(db), **colonne}
        nomi = ", ".join(colonne)
        segnaposto = ", ".join(["%s"] * len(colonne))
        return db.execute(
            f"insert into public.partner_digest_invii (user_id, {nomi}) "
            f"values (%s, {segnaposto}) returning id",
            (user, *colonne.values()),
        ).fetchone()[0]

    def test_default(self, db):
        user = new_user(db)
        rid = self._inserisci(db, user)
        with db.cursor(row_factory=dict_row) as cur:
            r = cur.execute("select * from public.partner_digest_invii where id = %s",
                            (rid,)).fetchone()
        assert r["stato"] == "in_invio" and r["errore"] is None
        assert r["created_at"] is not None and r["updated_at"] is not None

    def test_unico_per_utente_e_settimana(self, db):
        a, b = new_user(db), new_user(db)
        self._inserisci(db, a)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            self._inserisci(db, a, stato="inviata")
        assert vincolo_di(exc) == "pdi_utente_settimana_key"
        self._inserisci(db, a, lunedi(db, 1))
        self._inserisci(db, b)
        # Claim per insert come il backend: il secondo non inserisce nulla.
        assert db.execute(
            "insert into public.partner_digest_invii (user_id, settimana) values (%s, %s) "
            "on conflict (user_id, settimana) do nothing returning id", (a, lunedi(db))
        ).fetchone() is None
        assert conta(db, "partner_digest_invii") == 3

    @pytest.mark.parametrize("stato", ["in_invio", "inviata", "fallita", "incerta"])
    def test_stati_ammessi(self, db, stato):
        self._inserisci(db, new_user(db), stato=stato)

    @pytest.mark.parametrize(("colonne", "vincolo"), [
        ({"stato": "inviato"}, "pdi_stato_check"),
        ({"stato": "annullata"}, "pdi_stato_check"),
        ({"settimana_delta": 3}, "pdi_settimana_check"),
    ])
    def test_vincoli(self, db, colonne, vincolo):
        colonne = dict(colonne)
        settimana = lunedi(db) + timedelta(days=colonne.pop("settimana_delta", 0))
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._inserisci(db, new_user(db), settimana, **colonne)
        assert vincolo_di(exc) == vincolo

    def test_transizione_condizionata_e_updated_at(self, db):
        user = new_user(db)
        rid = self._inserisci(db, user, updated_at=db.execute(
            "select now() - interval '1 hour'").fetchone()[0])
        # UPDATE condizionato come il backend: vince solo da in_invio.
        assert db.execute("update public.partner_digest_invii set stato = 'inviata' "
                          "where id = %s and stato = 'in_invio' returning id",
                          (rid,)).fetchone() == (rid,)
        assert db.execute("update public.partner_digest_invii set stato = 'fallita' "
                          "where id = %s and stato = 'in_invio' returning id",
                          (rid,)).fetchone() is None
        assert db.execute("select stato, updated_at > now() - interval '1 minute' from "
                          "public.partner_digest_invii where id = %s",
                          (rid,)).fetchone() == ("inviata", True)

    def test_utente_obbligatorio_ed_esistente(self, db):
        with pytest.raises(psycopg.errors.NotNullViolation):
            self._inserisci(db, None)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            self._inserisci(db, str(uuid.uuid4()))


# ---------------------------------------------------------------- call salvate


class TestCallSalvate:
    def _salva(self, db, company, call_id, user=None):
        db.execute("insert into public.partner_call_salvate (company_profile_id, "
                   "partner_call_id, user_id) values (%s, %s, %s)",
                   (company, call_id, user or owner_di(db, company)))

    def test_forma_e_unicita(self, db, scenario):
        self._salva(db, scenario["y"], scenario["call"])
        with db.cursor(row_factory=dict_row) as cur:
            r = cur.execute("select * from public.partner_call_salvate").fetchone()
        assert str(r["user_id"]) == scenario["y_owner"] and r["created_at"] is not None
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            self._salva(db, scenario["y"], scenario["call"], new_user(db))
        assert vincolo_di(exc) == "partner_call_salvate_pkey"
        _, z = azienda(db)
        self._salva(db, z, scenario["call"])
        assert conta(db, "partner_call_salvate") == 2

    def test_utente_senza_fk_ma_obbligatorio(self, db, scenario):
        self._salva(db, scenario["y"], scenario["call"], str(uuid.uuid4()))
        with pytest.raises(psycopg.errors.NotNullViolation):
            db.execute("insert into public.partner_call_salvate (company_profile_id, "
                       "partner_call_id, user_id) values (%s, %s, null)",
                       (scenario["x"], scenario["call"]))
        fk = {r[0] for r in db.execute(
            "select a.attname from pg_constraint c join pg_attribute a "
            "on a.attrelid = c.conrelid and a.attnum = any (c.conkey) "
            "where c.conrelid = 'public.partner_call_salvate'::regclass and c.contype = 'f'"
        ).fetchall()}
        assert fk == {"company_profile_id", "partner_call_id"}

    def test_riferimenti_esistenti(self, db, scenario):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            self._salva(db, scenario["y"], str(uuid.uuid4()), scenario["y_owner"])
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            self._salva(db, str(uuid.uuid4()), scenario["call"], scenario["y_owner"])

    def test_indice_per_call(self, db):
        assert "(partner_call_id)" in db.execute(
            "select indexdef from pg_indexes where schemaname = 'public' "
            "and indexname = 'pcs_call_idx'").fetchone()[0]


# --------------------------------------------------------------------- cascade


class TestCascade:
    def _popola(self, db, scenario) -> dict:
        """Righe di Y (destinataria) e sulla call di X in tutte le tabelle nuove."""
        y, x, c = scenario["y"], scenario["x"], scenario["call"]
        for company in (x, y):
            db.execute("insert into public.company_collegamenti (company_profile_id, tipo, "
                       "chiave) values (%s, 'identita', %s)", (company, hmac_finto(company)))
            db.execute("insert into public.company_collegamenti_stato (company_profile_id, "
                       "algoritmo_versione) values (%s, 1)", (company,))
        assert claim(db, y, c) is True
        db.execute("insert into public.partner_call_salvate (company_profile_id, "
                   "partner_call_id, user_id) values (%s, %s, %s)", (y, c, scenario["y_owner"]))
        assert fanout(db, c) is True
        return {"altra_call": inserisci_call(db, y)}

    def test_hard_delete_dell_azienda_destinataria(self, db, scenario):
        self._popola(db, scenario)
        db.execute("delete from public.company_profiles where id = %s", (scenario["y"],))
        assert conta(db, "partner_notifiche_proattive") == 0
        assert conta(db, "partner_call_salvate") == 0
        for tabella in ("company_collegamenti", "company_collegamenti_stato"):
            assert [str(r[0]) for r in db.execute(
                f"select company_profile_id from public.{tabella}").fetchall()] == [
                scenario["x"]], tabella
        assert call(db, scenario["call"]) is not None

    def test_hard_delete_dell_azienda_creatrice(self, db, scenario):
        """La cascade passa dalla call: spariscono notifiche e salvataggi di terzi."""
        self._popola(db, scenario)
        db.execute("delete from public.company_profiles where id = %s", (scenario["x"],))
        assert call(db, scenario["call"]) is None
        assert conta(db, "partner_notifiche_proattive") == 0
        assert conta(db, "partner_call_salvate") == 0
        assert conta(db, "company_collegamenti") == 1

    def test_delete_della_call(self, db, scenario):
        altra = self._popola(db, scenario)["altra_call"]
        _, z = azienda(db, opt_in=True)
        assert claim(db, z, altra) is True
        db.execute("delete from public.partner_calls where id = %s", (scenario["call"],))
        assert [str(r["partner_call_id"]) for r in notifiche(db)] == [altra]
        assert conta(db, "partner_call_salvate") == 0
        assert conta(db, "company_collegamenti") == 2

    def test_delete_dell_utente(self, db):
        user = new_user(db)
        altro = new_user(db)
        for u in (user, altro):
            db.execute("insert into public.partner_email_settings (user_id) values (%s)", (u,))
            db.execute("insert into public.partner_digest_invii (user_id, settimana) "
                       "values (%s, %s)", (u, lunedi(db)))
        db.execute("insert into public.partner_digest_runs (settimana) values (%s)",
                   (lunedi(db),))
        db.execute("delete from auth.users where id = %s", (user,))
        for tabella in ("partner_email_settings", "partner_digest_invii"):
            assert [str(r[0]) for r in db.execute(
                f"select user_id from public.{tabella}").fetchall()] == [altro], tabella
        assert conta(db, "partner_digest_runs") == 1

    def test_delete_dell_owner_con_aziende(self, db, scenario):
        self._popola(db, scenario)
        db.execute("delete from auth.users where id = %s", (scenario["y_owner"],))
        assert conta(db, "partner_notifiche_proattive") == 0
        assert conta(db, "partner_call_salvate") == 0
        assert conta(db, "company_collegamenti_stato") == 1


# ---------------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0038, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0038, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")
ESEGUIBILE = "\n".join(r for r in SQL_0038.splitlines() if not r.lstrip().startswith("--"))


class TestSicurezza0038:
    def test_inventario_del_file(self):
        # Se la migration crea altro, i test sotto devono coprirlo.
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == FUNZIONI_NUOVE
        assert not re.search(r"^create (function|table(?! public\.))", ESEGUIBILE, re.M)

    def test_additiva(self):
        """Nessun oggetto esistente eliminato o ridefinito: su partner_calls solo
        colonne e vincolo nuovi."""
        assert not re.search(r"^\s*(drop|alter function|create or replace view)\b",
                             ESEGUIBILE, re.M | re.I)
        alterate = set(re.findall(r"^alter table public\.(\w+)", ESEGUIBILE, re.M))
        assert alterate == TABELLE_NUOVE | {"partner_calls"}
        blocco = re.search(r"^alter table public\.partner_calls\n(.*?);", ESEGUIBILE,
                           re.M | re.S).group(1)
        clausole = [c.strip() for c in re.split(r",\n", blocco)]
        assert [c.split()[:2] for c in clausole] == [
            ["add", "column"], ["add", "column"], ["add", "constraint"]]
        esistenti = {"fn_partner_consenso", "fn_partner_call_aggiorna", "fn_partner_call_pubblica",
                     "fn_partner_call_contenuto", "fn_partner_call_campi_editabili",
                     "set_updated_at"}
        assert not (FUNZIONI_NEL_FILE & esistenti)
        # Trigger solo sulle tabelle nuove.
        for tabella in re.findall(r"^\s+before update on public\.(\w+)", ESEGUIBILE, re.M):
            assert tabella in TABELLE_NUOVE

    def test_trigger(self, db):
        trigger = {(r[0], r[1]) for r in db.execute(
            "select tgname, tgrelid::regclass::text from pg_trigger where not tgisinternal "
            "and tgrelid = any (%s::regclass[])",
            ([f"public.{t}" for t in sorted(TABELLE_NUOVE | {"partner_calls"})],),
        ).fetchall()}
        assert trigger == {
            ("trg_partner_email_settings_updated_at", "partner_email_settings"),
            ("trg_partner_digest_invii_updated_at", "partner_digest_invii"),
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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0038, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0038, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration e ogni fn_partner_% presente nel
        DB è SECURITY DEFINER con search_path fissato, non eseguibile dai client
        (PUBLIC compreso) ed esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname like 'fn\_partner\_%%'"""
        ).fetchall()}
        assert FUNZIONI_NUOVE <= dal_db
        for nome in sorted(FUNZIONI_NEL_FILE | FUNZIONI_NUOVE | dal_db):
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
        for nome in FUNZIONI_NUOVE:
            assert re.search(
                rf"^revoke execute on function public\.{nome}\([^)]*\)\s+"
                r"from public, anon, authenticated;",
                SQL_0038, re.M,
            ), nome

    @pytest.mark.parametrize("ruolo", ["anon", "authenticated"])
    @pytest.mark.parametrize("chiamata", [
        "select public.fn_partner_claim_notifica(gen_random_uuid(), gen_random_uuid(), "
        "date_trunc('week', now())::date, 3, 1, 60)",
        "select public.fn_partner_fanout_claim(gen_random_uuid(), 600)",
    ])
    def test_i_client_non_eseguono_le_rpc(self, db, ruolo, chiamata):
        """Prova diretta: con i ruoli esposti la chiamata fallisce."""
        db.execute(f"set role {ruolo}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(chiamata)
        finally:
            db.execute("reset role")

    def test_un_ruolo_di_servizio_esegue_le_rpc(self, db, scenario):
        """Come il service_role: un grant esplicito basta (la revoca è ai soli ruoli
        esposti) e le funzioni scrivono senza privilegi di tabella (SECURITY DEFINER)."""
        ruolo = f"servizio_{uuid.uuid4().hex[:8]}"
        db.execute(f"create role {ruolo} nologin")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            for firma in FIRME.values():
                db.execute(f"grant execute on function public.{firma} to {ruolo}")
            db.execute(f"set role {ruolo}")
            try:
                assert fanout(db, scenario["call"]) is True
                assert claim(db, scenario["y"], scenario["call"]) is True
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        assert len(notifiche(db)) == 1

    def test_nessun_dato_in_chiaro_nelle_colonne(self, db):
        """Le tabelle nuove non hanno colonne per P.IVA, CF, email o nomi (T8)."""
        colonne = {r[0] for r in db.execute(
            "select column_name::text from information_schema.columns "
            "where table_schema = 'public' and table_name::text = any (%s)",
            (sorted(TABELLE_NUOVE),)).fetchall()}
        assert len(colonne) > 10
        for vietata in ("partita_iva", "piva", "codice_fiscale", "cf", "email", "nome",
                        "ragione_sociale", "denominazione", "valore"):
            assert vietata not in colonne, vietata
