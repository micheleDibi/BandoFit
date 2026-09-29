"""Test funzionali della migration 0035 (profilo partner dell'azienda,
registro append-only dei consensi, referente, bozza AI del profilo).

Coprono: default, vincoli, indici e cascade di company_partner_profiles; il
registro partner_consents (vincoli, niente FK, append-only anche per il
TRUNCATE); il trigger con GUC sui campi protetti (update e insert diretti
rifiutati, campi liberi e upsert a whitelist ammessi, eccezione dell'ON DELETE
SET NULL, GUC ripristinata dopo RPC e trigger); la revoca automatica su
company_profiles (soft delete, archiviazione, P.IVA, ragione sociale) senza
rompere fn_soft_delete_company e fn_reconcile_companies; fn_partner_consenso
(concedi, revoca, anonimato, identità dal registro T5, legale rappresentante
Q9, sospensione, guardie e ordine dei lock); fn_partner_referente (proposta,
accettazione, rifiuto, rinuncia, rimozione, sostituzione, annullamento della
sola proposta); la prenotazione della bozza AI (bozza in corso o orfana,
limite per azienda, limite per richiedente, budget del gruppo altri,
concorrenza), la sua chiusura atomica (bozza + esecuzione) e il failsafe, che
registra il consumo delle esecuzioni che chiude (anche quelle rimaste senza
bozza in corso); RLS, privilegi e firme di tutto ciò che la migration crea.
Ogni test riceve un database fresco clonato dal template.
"""

import itertools
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0035_profili_partner.sql"
)
SQL_0035 = MIGRATION.read_text(encoding="utf-8")

TABELLE_NUOVE = {"company_partner_profiles", "partner_consents"}
FUNZIONI_NUOVE = {
    "fn_partner_consents_readonly", "fn_cpp_campi_protetti", "fn_cpp_revoca_su_cambio_azienda",
    "fn_partner_consenso", "fn_partner_referente", "fn_partner_bozza_ai_esecuzione_interrotta",
    "fn_partner_bozza_ai_prenota", "fn_partner_bozza_ai_concludi",
    "fn_partner_bozza_ai_chiudi_stale",
}

VERSIONE = "2026-10-bozza-1"
VERSIONE_REFERENTE = "2026-10-referente-1"
CF_TITOLARE = "RSSMRA80A01H501U"
_TITOLARE = object()  # sentinella: l'attore è il titolare

_seq = itertools.count(1)


# ----------------------------------------------------------------- helper


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def new_user(db, plan_slug: str | None = None) -> str:
    uid = str(uuid.uuid4())
    db.execute(
        "insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@test.it")
    )
    if plan_slug:
        db.execute(
            "select public.fn_switch_plan(%s, "
            "(select id from public.subscription_plans where slug = %s))",
            (uid, plan_slug),
        )
    return uid


def make_company(db, owner: str, *, created_at: str | None = None) -> str:
    i = next(_seq)
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva, "
        "created_at) values (%s, %s, %s, coalesce(%s::timestamptz, now())) returning id",
        (owner, f"ACME {i} Srl", f"{i:011d}", created_at),
    ).fetchone()[0])


def importa(db, company: str, *, piva: str | None = None, stato: str | None = "Attiva",
            sandbox: bool = False) -> None:
    """company_data come dopo un import IT-full (piva_fetched = P.IVA dell'azienda)."""
    db.execute(
        "insert into public.company_data (company_profile_id, piva_fetched, sandbox, raw, "
        "denominazione, stato_impresa) "
        "select id, coalesce(%s, partita_iva), %s, '{}'::jsonb, ragione_sociale, %s "
        "from public.company_profiles where id = %s",
        (piva, sandbox, stato, company),
    )


def legale(db, company: str, cf: str, *, rappresentante: bool = True) -> None:
    db.execute(
        "insert into public.company_people (company_profile_id, kind, nome, cognome, "
        "codice_fiscale, is_legale_rappresentante, raw) "
        "values (%s, 'manager', 'Mario', 'Rossi', %s, %s, '{}'::jsonb)",
        (company, cf, rappresentante),
    )


def imposta_cf(db, user: str, cf: str = CF_TITOLARE, *, verificato: bool = True) -> None:
    db.execute(
        "update public.profiles set codice_fiscale = %s, "
        "cf_verified_at = case when %s then now() end where id = %s",
        (cf, verificato, user),
    )


def azienda_pronta(db, owner: str | None = None, plan_slug: str | None = None):
    """Titolare + azienda con identità verificata sul registro."""
    owner = owner or new_user(db, plan_slug)
    company = make_company(db, owner)
    importa(db, company)
    return owner, company


def verifica_identita_admin(db, owner: str, company: str) -> None:
    """Dalla 0041 il nominativo richiede l'identità verificata dall'admin
    (fn_partenariato_rappresentante_ok): richiesta del titolare e verifica."""
    admin = new_user(db)
    db.execute("update public.profiles set role = 'admin' where id = %s", (admin,))
    db.execute("select public.fn_identita_richiedi(%s, %s, %s, null)", (owner, company, owner))
    db.execute("select public.fn_identita_decidi(%s, %s, 'verificata', 'telefonata_sede', null)",
               (company, admin))


def nominativa_pronta(db, owner: str | None = None, plan_slug: str | None = None):
    """Come azienda_pronta, con il titolare legale rappresentante verificato e,
    dalla 0041, l'identità verificata dall'admin."""
    owner, company = azienda_pronta(db, owner, plan_slug)
    imposta_cf(db, owner)
    legale(db, company, CF_TITOLARE)
    verifica_identita_admin(db, owner, company)
    return owner, company


def consenso(db, owner, company, azione="concedi", *, attore=_TITOLARE, versione=VERSIONE,
             origine="pagina_azienda", anonimo=True, non_sandbox=True) -> dict:
    if attore is _TITOLARE:
        attore = owner
    return db.execute(
        "select public.fn_partner_consenso(%s::uuid, %s::uuid, %s::uuid, %s::text, %s::text, "
        "%s::text, %s::boolean, %s::boolean)",
        (owner, company, attore, azione, versione, origine, anonimo, non_sandbox),
    ).fetchone()[0]


def referente(db, owner, company, azione, *, attore=_TITOLARE, user=None,
              versione=None) -> dict:
    if attore is _TITOLARE:
        attore = owner
    return db.execute(
        "select public.fn_partner_referente(%s::uuid, %s::uuid, %s::uuid, %s::text, "
        "%s::uuid, %s::text)",
        (owner, company, attore, azione, user, versione),
    ).fetchone()[0]


def prenota_bozza(db, owner, company, *, richiedente=_TITOLARE, budget=1000, riserva=10,
                  limite_azienda=3, limite_richiedente=None) -> str:
    if richiedente is _TITOLARE:
        richiedente = owner
    return str(db.execute(
        "select public.fn_partner_bozza_ai_prenota(%s::uuid, %s::uuid, %s::uuid, %s::integer, "
        "%s::integer, %s::integer, %s::integer)",
        (owner, company, richiedente, budget, riserva, limite_azienda, limite_richiedente),
    ).fetchone()[0])


def chiudi_stale(db, minuti=10) -> int:
    return db.execute(
        "select public.fn_partner_bozza_ai_chiudi_stale(%s::integer)", (minuti,)
    ).fetchone()[0]


def prenota_ai(db, *, servizio="partner_call_testi", gruppo="altri", budget=1000, riserva=10,
               company=None) -> str:
    """Una spesa di un altro servizio del modulo (fn_partenariati_ai_prenota, 0034)."""
    return str(db.execute(
        "select public.fn_partenariati_ai_prenota(%s::text, 'utente', %s::text, %s::integer, "
        "%s::integer, null::uuid, null::integer, null::uuid, null::integer, %s::uuid, "
        "null::integer)",
        (servizio, gruppo, budget, riserva, company),
    ).fetchone()[0])


def concludi_ai(db, esecuzione_id, stato, cost=None, input_tokens=0, output_tokens=0) -> None:
    db.execute(
        "select public.fn_partenariati_ai_concludi(%s::uuid, %s::text, %s::integer, "
        "%s::integer, %s::integer, null::text, null::text)",
        (esecuzione_id, stato, cost, input_tokens, output_tokens),
    )


def concludi_bozza(db, company, esecuzione_id, *, bozza_stato="pronta", bozza=None,
                   bozza_errore=None, stato="conclusa", cost=5, input_tokens=100,
                   output_tokens=50) -> dict:
    """Chiusura atomica del job (fn_partner_bozza_ai_concludi)."""
    return db.execute(
        "select public.fn_partner_bozza_ai_concludi(%s::uuid, %s::uuid, %s::text, %s::jsonb, "
        "%s::text, %s::text, %s::integer, %s::integer, %s::integer, 'claude-sonnet-5', null)",
        (company, esecuzione_id, bozza_stato, bozza, bozza_errore, stato, cost, input_tokens,
         output_tokens),
    ).fetchone()[0]


def consumi(db) -> list[dict]:
    """Righe del registro consumi della bozza AI (api_usage_events)."""
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.api_usage_events where service = 'partner_profilo_ai' "
            "order by id"
        ).fetchall()


def libera_bozze(db) -> None:
    """Simula la chiusura della sola bozza (campi non protetti), senza l'esecuzione."""
    db.execute(
        "update public.company_partner_profiles set bozza_ai_stato = 'pronta', "
        "bozza_ai_at = now() where bozza_ai_stato = 'in_corso'")


def profilo(db, company) -> dict | None:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.company_partner_profiles where company_profile_id = %s",
            (company,),
        ).fetchone()


def crea_profilo(db, company, owner) -> None:
    """Insert diretta con i soli campi obbligatori (ammessa dal trigger)."""
    db.execute(
        "insert into public.company_partner_profiles (company_profile_id, family_parent_id) "
        "values (%s, %s)",
        (company, owner),
    )


def registro(db, company) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.partner_consents where company_profile_id = %s order by id",
            (company,),
        ).fetchall()


def audit(db, action: str) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.audit_log where action = %s order by id", (action,)
        ).fetchall()


def esecuzione(db, esecuzione_id) -> dict:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.partenariati_ai_esecuzioni where id = %s", (esecuzione_id,)
        ).fetchone()


def conta(db, tabella: str) -> int:
    return db.execute(f"select count(*) from public.{tabella}").fetchone()[0]


def guc(db) -> str | None:
    return db.execute("select current_setting('app.partner_consenso', true)").fetchone()[0]


@contextmanager
def con_guc(db):
    """Transazione con la GUC accesa: simula una scrittura dall'interno di una RPC."""
    with db.transaction():
        db.execute("select set_config('app.partner_consenso', 'on', true)")
        yield


def membro(db, owner, company, *, attivo=True) -> str:
    """Membro della famiglia dell'owner con appartenenza (e visibilità) su company."""
    uid = new_user(db)
    mid = db.execute(
        "select public.fn_create_family_member(%s, %s, 'Sede', %s, 'existing_user', %s, null)",
        (owner, uid, f"{uid[:8]}@test.it", company),
    ).fetchone()[0]
    if attivo:
        db.execute("select public.fn_accept_invitation(%s, %s)", (mid, uid))
    return uid


def membership(db, member) -> str:
    return str(db.execute(
        "select id from public.family_members where member_id = %s "
        "and status in ('pending', 'active', 'demoted')",
        (member,),
    ).fetchone()[0])


def referente_attivo(db):
    """Owner Advisor + azienda + membro A che ha accettato di fare il referente."""
    owner = new_user(db, "advisor")
    company = make_company(db, owner)
    a = membro(db, owner, company)
    referente(db, owner, company, "proponi", user=a)
    referente(db, owner, company, "accetta", attore=a, versione=VERSIONE_REFERENTE)
    return owner, company, a


# --------------------------------------------------------- tabella profilo


class TestTabellaProfilo:
    def test_default(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        p = profilo(db, company)
        assert p["visibile_come_partner"] is False
        assert p["anonimo"] is True
        assert p["accetta_inviti"] is True
        assert p["codice_pubblico"] is not None
        assert p["ruoli_disponibili"] == ["partner"]
        for campo in ("competenze", "competenze_libere", "tipi_soggetto", "settori_interesse",
                      "regioni_interesse", "paesi_interesse", "forme_accettate",
                      "certificazioni", "categorie_bando_escluse"):
            assert p[campo] == [], campo
        assert p["esperienze"] == []
        assert (p["vocabolario_versione"], p["completezza"]) == (1, 0)
        for campo in ("consenso_versione", "consenso_at", "referente_user_id",
                      "referente_proposto_user_id", "referente_proposto_at", "sospeso_at",
                      "sospeso_motivo", "sospeso_da", "bozza_ai", "bozza_ai_stato",
                      "bozza_ai_avviata_at", "bozza_ai_at", "bozza_ai_errore",
                      "bozza_ai_esecuzione_id", "descrizione_competenze", "infrastrutture",
                      "updated_by"):
            assert p[campo] is None, campo

    def test_visibile_richiede_consenso(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            with con_guc(db):
                db.execute(
                    "update public.company_partner_profiles set visibile_come_partner = true "
                    "where company_profile_id = %s", (company,))
        assert exc.value.diag.constraint_name == "cpp_visibile_richiede_consenso"
        with pytest.raises(psycopg.errors.CheckViolation):
            with con_guc(db):
                db.execute(
                    "update public.company_partner_profiles set visibile_come_partner = true, "
                    "consenso_versione = 'v1' where company_profile_id = %s", (company,))
        with con_guc(db):
            db.execute(
                "update public.company_partner_profiles set visibile_come_partner = true, "
                "consenso_versione = 'v1', consenso_at = now() where company_profile_id = %s",
                (company,))
        assert profilo(db, company)["visibile_come_partner"] is True

    @pytest.mark.parametrize("colonna, valore, vincolo", [
        ("descrizione_competenze", "repeat('x', 2001)", "cpp_descrizione_competenze_check"),
        ("competenze", "array_fill('x'::text, array[16])", "cpp_competenze_check"),
        ("competenze_libere", "array_fill('x'::text, array[11])", "cpp_competenze_libere_check"),
        ("tipi_soggetto", "array_fill('x'::text, array[9])", "cpp_tipi_soggetto_check"),
        ("ruoli_disponibili", "'{}'::text[]", "cpp_ruoli_disponibili_check"),
        ("ruoli_disponibili", "'{partner,fornitore}'::text[]", "cpp_ruoli_disponibili_check"),
        ("settori_interesse", "array_fill(1, array[21])", "cpp_settori_interesse_check"),
        ("regioni_interesse", "array_fill(1, array[22])", "cpp_regioni_interesse_check"),
        ("paesi_interesse", "array_fill('IT'::text, array[31])", "cpp_paesi_interesse_check"),
        ("paesi_interesse", "'{it}'::text[]", "cpp_paesi_interesse_check"),
        ("paesi_interesse", "'{ITA}'::text[]", "cpp_paesi_interesse_check"),
        ("paesi_interesse", "'{IT,NULL}'::text[]", "cpp_paesi_interesse_check"),
        ("forme_accettate", "'{ats,altro}'::text[]", "cpp_forme_accettate_check"),
        ("esperienze", "'{}'::jsonb", "cpp_esperienze_check"),
        ("esperienze", "'\"testo\"'::jsonb", "cpp_esperienze_check"),
        ("esperienze", "(select jsonb_agg(i) from generate_series(1, 21) i)",
         "cpp_esperienze_check"),
        ("certificazioni", "array_fill('x'::text, array[21])", "cpp_certificazioni_check"),
        ("infrastrutture", "repeat('x', 2001)", "cpp_infrastrutture_check"),
        ("completezza", "101", "cpp_completezza_check"),
        ("completezza", "-1", "cpp_completezza_check"),
        ("bozza_ai", "'[]'::jsonb", "cpp_bozza_ai_check"),
        ("bozza_ai_stato", "'boh'", "cpp_bozza_ai_stato_check"),
        ("bozza_ai_stato", "'in_corso'", "cpp_bozza_in_corso_coerente"),
    ])
    def test_vincoli(self, db, colonna, valore, vincolo):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute(
                f"update public.company_partner_profiles set {colonna} = {valore} "
                "where company_profile_id = %s", (company,))
        assert exc.value.diag.constraint_name == vincolo

    @pytest.mark.parametrize("colonna, valore, vincolo", [
        ("consenso_versione", "''", "cpp_consenso_versione_check"),
        ("consenso_versione", "repeat('v', 51)", "cpp_consenso_versione_check"),
        ("sospeso_motivo", "repeat('x', 501)", "cpp_sospeso_motivo_check"),
    ])
    def test_vincoli_dei_campi_protetti(self, db, colonna, valore, vincolo):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            with con_guc(db):
                db.execute(
                    f"update public.company_partner_profiles set {colonna} = {valore} "
                    "where company_profile_id = %s", (company,))
        assert exc.value.diag.constraint_name == vincolo

    def test_valori_al_limite_ammessi(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        db.execute(
            """update public.company_partner_profiles set
                 descrizione_competenze = repeat('x', 2000),
                 competenze = array_fill('x'::text, array[15]),
                 competenze_libere = array_fill('x'::text, array[10]),
                 tipi_soggetto = array_fill('x'::text, array[8]),
                 ruoli_disponibili = '{capofila,partner}',
                 settori_interesse = array_fill(1, array[20]),
                 regioni_interesse = array_fill(1, array[21]),
                 paesi_interesse = array_fill('DE'::text, array[30]),
                 forme_accettate = '{ats,ati_rti,rete_contratto,rete_soggetto,consorzio,
                                     accordo_partenariato,consorzio_ue}',
                 esperienze = (select jsonb_agg(jsonb_build_object('programma', 'P' || i))
                               from generate_series(1, 20) i),
                 certificazioni = array_fill('x'::text, array[20]),
                 infrastrutture = repeat('x', 2000),
                 completezza = 100,
                 bozza_ai = '{"descrizione_competenze": "x"}',
                 bozza_ai_stato = 'pronta',
                 updated_by = %s
               where company_profile_id = %s""",
            (owner, company),
        )
        p = profilo(db, company)
        assert len(p["competenze"]) == 15 and p["completezza"] == 100

    def test_bozza_in_corso_coerente(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.company_partner_profiles set bozza_ai_stato = 'in_corso', "
                "bozza_ai_esecuzione_id = gen_random_uuid() where company_profile_id = %s",
                (company,))
        db.execute(
            "update public.company_partner_profiles set bozza_ai_stato = 'in_corso', "
            "bozza_ai_esecuzione_id = gen_random_uuid(), bozza_ai_avviata_at = now() "
            "where company_profile_id = %s", (company,))

    def test_uno_a_uno_e_codice_pubblico_unico(self, db):
        owner = new_user(db, "advisor")
        c1, c2 = make_company(db, owner), make_company(db, owner)
        crea_profilo(db, c1, owner)
        with pytest.raises(psycopg.errors.UniqueViolation):
            crea_profilo(db, c1, owner)
        codice = profilo(db, c1)["codice_pubblico"]
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                "insert into public.company_partner_profiles "
                "(company_profile_id, family_parent_id, codice_pubblico) values (%s, %s, %s)",
                (c2, owner, codice))

    def test_azienda_inesistente(self, db):
        owner = new_user(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea_profilo(db, str(uuid.uuid4()), owner)
        assert detail_of(exc) == "campo_protetto"

    def test_trigger_updated_at(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        db.execute(
            "update public.company_partner_profiles set updated_at = now() - interval '1 day' "
            "where company_profile_id = %s", (company,))
        db.execute(
            "update public.company_partner_profiles set descrizione_competenze = 'x' "
            "where company_profile_id = %s", (company,))
        assert db.execute(
            "select updated_at > now() - interval '1 minute' "
            "from public.company_partner_profiles where company_profile_id = %s", (company,)
        ).fetchone()[0] is True

    def test_indici(self, db):
        indici = {r[0]: r[1] for r in db.execute(
            "select indexname, indexdef from pg_indexes "
            "where schemaname = 'public' and tablename = 'company_partner_profiles'"
        ).fetchall()}
        assert "WHERE (visibile_come_partner AND (sospeso_at IS NULL))" in \
            indici["cpp_visibili_idx"]
        assert "(family_parent_id)" in indici["cpp_family_idx"]
        assert "cpp_referente_idx" in indici and "cpp_referente_proposto_idx" in indici
        consensi = {r[0]: r[1] for r in db.execute(
            "select indexname, indexdef from pg_indexes "
            "where schemaname = 'public' and tablename = 'partner_consents'"
        ).fetchall()}
        assert "(company_profile_id, created_at DESC)" in \
            consensi["partner_consents_company_idx"]

    def test_hard_delete_cancella_il_profilo_ma_non_i_consensi(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        consenso(db, owner, company, "revoca")
        db.execute("delete from public.company_profiles where id = %s", (company,))
        assert profilo(db, company) is None
        assert [r["azione"] for r in registro(db, company)] == ["concesso", "revocato"]

    def test_cancellazione_dell_owner(self, db):
        """Cancellazione dell'account titolare: cascade fino al profilo partner, il
        registro resta."""
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        db.execute("delete from auth.users where id = %s", (owner,))
        assert profilo(db, company) is None
        assert len(registro(db, company)) == 1


# ------------------------------------------------------ registro consensi


class TestRegistro:
    def _inserisci(self, db, **colonne):
        valori = {"company_profile_id": str(uuid.uuid4()), "family_parent_id": str(uuid.uuid4()),
                  "azione": "concesso", "informativa_versione": VERSIONE,
                  "origine": "pagina_azienda", "attore_user_id": str(uuid.uuid4()),
                  "anonimo": True}
        valori.update(colonne)
        nomi = ", ".join(valori)
        segnaposto = ", ".join(["%s"] * len(valori))
        return db.execute(
            f"insert into public.partner_consents ({nomi}) values ({segnaposto}) returning id",
            list(valori.values()),
        ).fetchone()[0]

    def test_senza_fk(self, db):
        # Azienda e owner inesistenti: il registro non ha FK (sopravvive alle cancellazioni).
        self._inserisci(db)
        assert db.execute(
            "select count(*) from pg_constraint where conrelid = 'public.partner_consents'::regclass "
            "and contype = 'f'"
        ).fetchone()[0] == 0

    @pytest.mark.parametrize("colonne, vincolo", [
        ({"azione": "boh"}, "pc_azione_check"),
        ({"origine": "client"}, "pc_origine_check"),
        ({"informativa_versione": ""}, "pc_informativa_versione_check"),
        ({"informativa_versione": "v" * 51}, "pc_informativa_versione_check"),
        ({"informativa_versione": None}, "pc_versione_richiesta"),
        ({"attore_user_id": None}, "pc_attore_richiesto"),
        ({"origine": "admin", "attore_user_id": None}, "pc_attore_richiesto"),
        ({"anonimo": None}, "pc_anonimo_registrato"),
        ({"azione": "anonimato", "anonimo": None}, "pc_anonimo_registrato"),
        ({"azione": "referente_concesso"}, "pc_referente_registrato"),
        ({"azione": "referente_revocato"}, "pc_referente_registrato"),
        ({"motivo": "x" * 201}, "pc_motivo_check"),
    ])
    def test_vincoli(self, db, colonne, vincolo):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            self._inserisci(db, **colonne)
        assert exc.value.diag.constraint_name == vincolo

    def test_origine_sistema_senza_attore_ne_versione(self, db):
        self._inserisci(db, azione="revocato", origine="sistema", attore_user_id=None,
                        informativa_versione=None, anonimo=None, motivo="azienda_eliminata")

    def test_revocato_senza_anonimo_ammesso(self, db):
        self._inserisci(db, azione="revocato", anonimo=None)

    @pytest.mark.parametrize("istruzione", [
        "update public.partner_consents set motivo = 'x'",
        "update public.partner_consents set azione = 'revocato' where id = {id}",
        "delete from public.partner_consents where id = {id}",
        "delete from public.partner_consents",
        "truncate public.partner_consents",
    ])
    def test_append_only(self, db, istruzione):
        rid = self._inserisci(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(istruzione.format(id=rid))
        assert detail_of(exc) == "registro_append_only"
        assert conta(db, "partner_consents") == 1

    def test_append_only_anche_con_la_guc(self, db):
        rid = self._inserisci(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            with con_guc(db):
                db.execute("delete from public.partner_consents where id = %s", (rid,))
        assert detail_of(exc) == "registro_append_only"


# --------------------------------------------------------- campi protetti


PROTETTI_UPDATE = [
    ("visibile_come_partner", "false"),  # revoca senza registro
    ("anonimo", "false"),
    ("consenso_versione", "'v2'"),
    ("consenso_at", "now()"),
    ("referente_user_id", "{utente}"),
    ("referente_proposto_user_id", "{utente}"),
    ("referente_proposto_at", "now()"),
    ("sospeso_at", "now()"),
    ("sospeso_motivo", "'abuso'"),
    ("sospeso_da", "{utente}"),
    ("codice_pubblico", "gen_random_uuid()"),
    ("company_profile_id", "{altra_azienda}"),
    ("family_parent_id", "{utente}"),
]
PROTETTI_INSERT = [("visibile_come_partner", "true")] + PROTETTI_UPDATE[1:10]


class TestCampiProtetti:
    @pytest.mark.parametrize("colonna, valore", PROTETTI_UPDATE)
    def test_update_diretto_rifiutato(self, db, colonna, valore):
        owner, company = azienda_pronta(db, plan_slug="advisor")
        altra = make_company(db, owner)
        consenso(db, owner, company)  # profilo visibile e con consenso
        utente = new_user(db)
        valore = valore.format(utente=f"'{utente}'::uuid", altra_azienda=f"'{altra}'::uuid")
        prima = profilo(db, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                f"update public.company_partner_profiles set {colonna} = {valore} "
                "where company_profile_id = %s", (company,))
        assert detail_of(exc) == "campo_protetto"
        assert profilo(db, company) == prima

    @pytest.mark.parametrize("colonna, valore", PROTETTI_INSERT)
    def test_insert_con_campi_protetti_non_di_default(self, db, colonna, valore):
        owner = new_user(db)
        company = make_company(db, owner)
        utente = new_user(db)
        valore = valore.format(utente=f"'{utente}'::uuid")
        extra = ", consenso_versione, consenso_at" if colonna == "visibile_come_partner" else ""
        extra_val = ", 'v1', now()" if colonna == "visibile_come_partner" else ""
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                f"insert into public.company_partner_profiles "
                f"(company_profile_id, family_parent_id, {colonna}{extra}) "
                f"values (%s, %s, {valore}{extra_val})", (company, owner))
        assert detail_of(exc) == "campo_protetto"
        assert profilo(db, company) is None

    def test_insert_con_i_default_espliciti_ammesso(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        db.execute(
            "insert into public.company_partner_profiles (company_profile_id, family_parent_id, "
            "visibile_come_partner, anonimo, descrizione_competenze) "
            "values (%s, %s, false, true, 'Meccatronica')", (company, owner))
        assert profilo(db, company)["descrizione_competenze"] == "Meccatronica"

    def test_insert_con_owner_diverso_rifiutato_anche_con_la_guc(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        for con in (False, True):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                with con_guc(db) if con else db.transaction():
                    crea_profilo(db, company, new_user(db))
            assert detail_of(exc) == "campo_protetto"

    def test_update_dei_campi_liberi_ammesso(self, db):
        owner, company = nominativa_pronta(db)
        consenso(db, owner, company, anonimo=False)
        db.execute(
            """update public.company_partner_profiles set
                 descrizione_competenze = 'Automazione industriale',
                 competenze = '{automazione_industria40}', competenze_libere = '{PLC}',
                 tipi_soggetto = '{organismo_ricerca}', ruoli_disponibili = '{capofila}',
                 settori_interesse = '{3}', regioni_interesse = '{12}',
                 paesi_interesse = '{DE,FR}', forme_accettate = '{ats}',
                 esperienze = '[{"programma": "Horizon Europe"}]', certificazioni = '{ISO 9001}',
                 infrastrutture = 'Laboratorio', accetta_inviti = false,
                 categorie_bando_escluse = '{4}', vocabolario_versione = 1, completezza = 80,
                 bozza_ai = '{"competenze": []}', bozza_ai_stato = 'pronta', bozza_ai_at = now(),
                 bozza_ai_errore = null, updated_by = %s
               where company_profile_id = %s""",
            (owner, company),
        )
        p = profilo(db, company)
        assert p["descrizione_competenze"] == "Automazione industriale"
        assert p["visibile_come_partner"] is True and p["anonimo"] is False

    def test_upsert_a_whitelist_ammesso(self, db):
        """Il PUT del backend: upsert on_conflict=company_profile_id con i soli campi liberi
        (più family_parent_id, identico) su un profilo visibile e nominativo."""
        owner, company = nominativa_pronta(db)
        consenso(db, owner, company, anonimo=False)
        db.execute(
            "insert into public.company_partner_profiles "
            "(company_profile_id, family_parent_id, descrizione_competenze, competenze) "
            "values (%s, %s, 'Nuova', '{cybersecurity}') "
            "on conflict (company_profile_id) do update set "
            "family_parent_id = excluded.family_parent_id, "
            "descrizione_competenze = excluded.descrizione_competenze, "
            "competenze = excluded.competenze",
            (company, owner),
        )
        p = profilo(db, company)
        assert p["descrizione_competenze"] == "Nuova"
        assert p["visibile_come_partner"] is True and p["anonimo"] is False

    def test_upsert_con_un_campo_protetto_rifiutato(self, db):
        """Un upsert che include anonimo (anche al valore di default) non passa."""
        owner, company = nominativa_pronta(db)
        consenso(db, owner, company, anonimo=False)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "insert into public.company_partner_profiles "
                "(company_profile_id, family_parent_id, anonimo) values (%s, %s, true) "
                "on conflict (company_profile_id) do update set anonimo = excluded.anonimo",
                (company, owner),
            )
        assert detail_of(exc) == "campo_protetto"
        assert profilo(db, company)["anonimo"] is False

    def test_con_la_guc_la_scrittura_passa(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        with con_guc(db):
            db.execute(
                "update public.company_partner_profiles set anonimo = false, "
                "sospeso_at = now(), sospeso_motivo = 'x' where company_profile_id = %s",
                (company,))
        p = profilo(db, company)
        assert p["anonimo"] is False and p["sospeso_at"] is not None

    def test_guc_con_altro_valore_non_basta(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        crea_profilo(db, company, owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            with db.transaction():
                db.execute("select set_config('app.partner_consenso', 'true', true)")
                db.execute(
                    "update public.company_partner_profiles set anonimo = false "
                    "where company_profile_id = %s", (company,))
        assert detail_of(exc) == "campo_protetto"

    def test_guc_ripristinata_dopo_la_rpc(self, db):
        """La RPC accende la GUC solo per la propria scrittura: nella stessa transazione
        un update diretto successivo resta bloccato."""
        owner, company = azienda_pronta(db)
        with db.transaction():
            consenso(db, owner, company)
            assert guc(db) != "on"
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                with db.transaction():  # savepoint
                    db.execute(
                        "update public.company_partner_profiles set anonimo = false "
                        "where company_profile_id = %s", (company,))
            assert detail_of(exc) == "campo_protetto"
        assert profilo(db, company)["visibile_come_partner"] is True

    def test_guc_precedente_rispettata(self, db):
        """Chiamata annidata (GUC già accesa dal chiamante): resta accesa dopo la RPC."""
        owner, company = azienda_pronta(db)
        with con_guc(db):
            consenso(db, owner, company)
            assert guc(db) == "on"
            assert consenso(db, owner, company, "revoca")["cambiato"] is True
            assert guc(db) == "on"

    def test_guc_ripristinata_dopo_il_trigger_di_revoca(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        with db.transaction():
            db.execute(
                "update public.company_profiles set ragione_sociale = 'Nuova Srl' where id = %s",
                (company,))
            assert profilo(db, company)["visibile_come_partner"] is False
            assert guc(db) != "on"

    def test_referente_azzerato_direttamente_rifiutato(self, db):
        """L'eccezione dell'ON DELETE SET NULL vale solo se l'utente non esiste più."""
        owner, company, a = referente_attivo(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            db.execute(
                "update public.company_partner_profiles set referente_user_id = null "
                "where company_profile_id = %s", (company,))
        assert detail_of(exc) == "campo_protetto"
        assert str(profilo(db, company)["referente_user_id"]) == a


# ------------------------------------------------------ fn_partner_consenso


class TestConsenso:
    def test_concedi(self, db):
        owner, company = azienda_pronta(db)
        out = consenso(db, owner, company, origine="import_piva")
        assert out["visibile"] is True and out["anonimo"] is True and out["cambiato"] is True
        assert out["consenso_versione"] == VERSIONE and out["consenso_at"]
        p = profilo(db, company)
        assert p["visibile_come_partner"] is True and p["anonimo"] is True
        assert p["consenso_versione"] == VERSIONE and p["consenso_at"] is not None
        assert str(p["family_parent_id"]) == owner
        (riga,) = registro(db, company)
        assert riga["azione"] == "concesso"
        assert riga["informativa_versione"] == VERSIONE
        assert riga["origine"] == "import_piva"
        assert str(riga["attore_user_id"]) == owner
        assert str(riga["family_parent_id"]) == owner
        assert riga["anonimo"] is True
        (a,) = audit(db, "partner.consenso_concesso")
        assert str(a["actor_id"]) == owner and str(a["family_parent_id"]) == owner
        assert a["payload"] == {"company_profile_id": company, "versione": VERSIONE,
                                "origine": "import_piva", "anonimo": True}

    def test_ripetizione_identica_non_scrive(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        prima = profilo(db, company)
        out = consenso(db, owner, company, origine="wizard_call")
        assert out["cambiato"] is False and out["visibile"] is True
        assert profilo(db, company) == prima
        assert len(registro(db, company)) == 1
        assert len(audit(db, "partner.consenso_concesso")) == 1

    def test_nuova_versione_o_anonimato_diverso_scrivono(self, db):
        owner, company = nominativa_pronta(db)
        consenso(db, owner, company)
        prima = profilo(db, company)["consenso_at"]
        assert consenso(db, owner, company, versione="2027-01")["cambiato"] is True
        assert profilo(db, company)["consenso_at"] > prima
        out = consenso(db, owner, company, versione="2027-01", anonimo=False)
        assert out["cambiato"] is True and out["anonimo"] is False
        righe = registro(db, company)
        assert [(r["azione"], r["informativa_versione"], r["anonimo"]) for r in righe] == [
            ("concesso", VERSIONE, True), ("concesso", "2027-01", True),
            ("concesso", "2027-01", False)]

    def test_revoca(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        out = consenso(db, owner, company, "revoca", versione="versione-in-pagina")
        assert out["cambiato"] is True and out["visibile"] is False
        p = profilo(db, company)
        assert p["visibile_come_partner"] is False
        # consenso_* restano come storico.
        assert p["consenso_versione"] == VERSIONE and p["consenso_at"] is not None
        riga = registro(db, company)[-1]
        assert riga["azione"] == "revocato"
        # La versione registrata è quella del consenso revocato.
        assert riga["informativa_versione"] == VERSIONE
        assert riga["anonimo"] is True and str(riga["attore_user_id"]) == owner
        (a,) = audit(db, "partner.consenso_revocato")
        assert a["payload"] == {"company_profile_id": company, "versione": VERSIONE,
                                "origine": "pagina_azienda"}

    def test_revoca_senza_consenso_non_scrive(self, db):
        owner, company = azienda_pronta(db)
        out = consenso(db, owner, company, "revoca", versione=None, anonimo=None)
        assert out["cambiato"] is False and out["visibile"] is False
        assert registro(db, company) == []
        consenso(db, owner, company)
        consenso(db, owner, company, "revoca")
        assert consenso(db, owner, company, "revoca")["cambiato"] is False
        assert len(registro(db, company)) == 2

    def test_revoca_senza_identita_sempre_possibile(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        db.execute("update public.company_data set stato_impresa = 'Cessata'")
        assert consenso(db, owner, company, "revoca")["visibile"] is False

    def test_anonimato(self, db):
        owner, company = nominativa_pronta(db)
        consenso(db, owner, company)
        out = consenso(db, owner, company, "anonimato", anonimo=False)
        assert out["cambiato"] is True and out["anonimo"] is False and out["visibile"] is True
        assert profilo(db, company)["anonimo"] is False
        riga = registro(db, company)[-1]
        assert (riga["azione"], riga["anonimo"], riga["informativa_versione"]) == (
            "anonimato", False, VERSIONE)
        (a,) = audit(db, "partner.anonimato_cambiato")
        assert a["payload"] == {"company_profile_id": company, "anonimo": False,
                                "origine": "pagina_azienda"}
        assert consenso(db, owner, company, "anonimato", anonimo=False)["cambiato"] is False
        assert consenso(db, owner, company, "anonimato", anonimo=True)["anonimo"] is True
        assert [r["azione"] for r in registro(db, company)] == [
            "concesso", "anonimato", "anonimato"]

    def test_anonimato_verso_il_nominativo_verifica_se_visibile(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, "anonimato", anonimo=False)
        assert detail_of(exc) == "rappresentante_non_verificato"
        db.execute("delete from public.company_data")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, "anonimato", anonimo=False)
        assert detail_of(exc) == "identita_non_verificata"
        assert profilo(db, company)["anonimo"] is True

    def test_anonimato_su_profilo_non_visibile_senza_verifiche(self, db):
        owner = new_user(db)
        company = make_company(db, owner)  # nessun dato di registro
        out = consenso(db, owner, company, "anonimato", anonimo=False)
        assert out["anonimo"] is False and out["visibile"] is False
        # La concessione successiva ripete comunque le verifiche.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, anonimo=False)
        assert detail_of(exc) == "identita_non_verificata"

    def test_anonimato_versione_dal_consenso_o_obbligatoria(self, db):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, "anonimato", anonimo=False, versione=None)
        assert detail_of(exc) == "versione_non_valida"
        consenso(db, owner, company)
        consenso(db, owner, company, "anonimato", anonimo=True, versione=None)  # invariato
        imposta_cf(db, owner)
        legale(db, company, CF_TITOLARE)
        verifica_identita_admin(db, owner, company)  # dalla 0041
        consenso(db, owner, company, "anonimato", anonimo=False, versione=None)
        assert registro(db, company)[-1]["informativa_versione"] == VERSIONE

    @pytest.mark.parametrize("azione", ["concedi", "anonimato"])
    def test_anonimato_obbligatorio(self, db, azione):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, azione, anonimo=None)
        assert detail_of(exc) == "anonimato_obbligatorio"
        assert registro(db, company) == []

    @pytest.mark.parametrize("azione", [None, "boh", "Concedi"])
    def test_azione_non_valida(self, db, azione):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, azione)
        assert detail_of(exc) == "azione_non_valida"

    @pytest.mark.parametrize("origine", [None, "client", "Admin"])
    def test_origine_non_valida(self, db, origine):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, origine=origine)
        assert detail_of(exc) == "origine_non_valida"

    @pytest.mark.parametrize("origine", ["import_piva", "pagina_azienda", "wizard_call"])
    def test_attore_non_titolare(self, db, origine):
        owner, company = azienda_pronta(db)
        for attore in (new_user(db), None):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                consenso(db, owner, company, attore=attore, origine=origine)
            assert detail_of(exc) == "attore_non_titolare"
        assert profilo(db, company) is None

    def test_origine_admin_richiede_un_attore(self, db):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, attore=None, origine="admin")
        assert detail_of(exc) == "attore_non_titolare"
        admin = new_user(db)
        consenso(db, owner, company, attore=admin, origine="admin")
        assert str(registro(db, company)[-1]["attore_user_id"]) == admin

    def test_origine_sistema_senza_attore(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        consenso(db, owner, company, "revoca", attore=None, origine="sistema", versione=None)
        riga = registro(db, company)[-1]
        assert riga["origine"] == "sistema" and riga["attore_user_id"] is None

    @pytest.mark.parametrize("versione", [None, "", "v" * 51])
    def test_versione_non_valida(self, db, versione):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, versione=versione)
        assert detail_of(exc) == "versione_non_valida"

    def test_versione_di_50_caratteri(self, db):
        owner, company = azienda_pronta(db)
        assert consenso(db, owner, company, versione="v" * 50)["visibile"] is True

    @pytest.mark.parametrize("caso", ["altro_owner", "soft_deleted", "archiviata",
                                      "inesistente"])
    def test_company_not_found(self, db, caso):
        owner, company = azienda_pronta(db)
        if caso == "altro_owner":
            owner = new_user(db)
        elif caso == "soft_deleted":
            db.execute("update public.company_profiles set deleted_at = now() where id = %s",
                       (company,))
        elif caso == "archiviata":
            db.execute("update public.company_profiles set archived_at = now() where id = %s",
                       (company,))
        else:
            company = str(uuid.uuid4())
        for azione in ("concedi", "revoca", "anonimato"):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                consenso(db, owner, company, azione)
            assert detail_of(exc) == "company_not_found"
        assert conta(db, "company_partner_profiles") == 0

    @pytest.mark.parametrize("stato", ["Attiva", "ATTIVA", " attiva "])
    def test_identita_verificata(self, db, stato):
        owner = new_user(db)
        company = make_company(db, owner)
        importa(db, company, stato=stato)
        assert consenso(db, owner, company)["visibile"] is True

    @pytest.mark.parametrize("caso", ["senza_dati", "piva_diversa", "cessata", "stato_ignoto",
                                      "in_liquidazione", "sandbox"])
    def test_identita_non_verificata(self, db, caso):
        owner = new_user(db)
        company = make_company(db, owner)
        if caso == "piva_diversa":
            importa(db, company, piva="99999999999")
        elif caso == "cessata":
            importa(db, company, stato="Cessata")
        elif caso == "stato_ignoto":
            importa(db, company, stato=None)
        elif caso == "in_liquidazione":
            importa(db, company, stato="In liquidazione")
        elif caso == "sandbox":
            importa(db, company, sandbox=True)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company)
        assert detail_of(exc) == "identita_non_verificata"
        assert profilo(db, company) is None  # rollback anche della riga creata
        assert registro(db, company) == []

    def test_sandbox_ammessa_solo_se_non_richiesto_il_contrario(self, db):
        owner = new_user(db)
        company = make_company(db, owner)
        importa(db, company, sandbox=True)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, non_sandbox=None)  # NULL vale true (fail-closed)
        assert detail_of(exc) == "identita_non_verificata"
        assert consenso(db, owner, company, non_sandbox=False)["visibile"] is True

    def test_identita_sulla_piva_attuale_dell_azienda(self, db):
        """Dopo un cambio di P.IVA il vecchio import non vale più."""
        owner, company = azienda_pronta(db)
        db.execute("update public.company_profiles set partita_iva = '99999999999' "
                   "where id = %s", (company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company)
        assert detail_of(exc) == "identita_non_verificata"

    @pytest.mark.parametrize("caso", ["senza_cf", "cf_non_verificato", "non_rappresentante",
                                      "cf_diverso", "rappresentante_altra_azienda"])
    def test_rappresentante_non_verificato(self, db, caso):
        owner, company = azienda_pronta(db, plan_slug="advisor")
        if caso == "cf_non_verificato":
            imposta_cf(db, owner, verificato=False)
            legale(db, company, CF_TITOLARE)
        elif caso == "non_rappresentante":
            imposta_cf(db, owner)
            legale(db, company, CF_TITOLARE, rappresentante=False)
        elif caso == "cf_diverso":
            imposta_cf(db, owner)
            legale(db, company, "BNCLGU70B02F205X")
        elif caso == "rappresentante_altra_azienda":
            imposta_cf(db, owner)
            legale(db, make_company(db, owner), CF_TITOLARE)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, anonimo=False)
        assert detail_of(exc) == "rappresentante_non_verificato"
        assert profilo(db, company) is None
        # In forma anonima resta possibile.
        assert consenso(db, owner, company, anonimo=True)["visibile"] is True

    def test_nominativo_con_legale_rappresentante(self, db):
        owner, company = azienda_pronta(db)
        imposta_cf(db, owner)
        legale(db, company, "  rssmra80a01h501u ")  # minuscolo e con spazi nella visura
        # Dalla 0041 il CF tra i legali rappresentanti non basta più.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, anonimo=False)
        assert detail_of(exc) == "rappresentante_non_verificato"
        verifica_identita_admin(db, owner, company)
        out = consenso(db, owner, company, anonimo=False)
        assert out["visibile"] is True and out["anonimo"] is False
        assert registro(db, company)[-1]["anonimo"] is False

    def test_profilo_sospeso(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        with con_guc(db):
            db.execute(
                "update public.company_partner_profiles set sospeso_at = now(), "
                "sospeso_motivo = 'segnalazione' where company_profile_id = %s", (company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company, versione="2027-01")
        assert detail_of(exc) == "profilo_sospeso"
        # La revoca resta sempre possibile.
        assert consenso(db, owner, company, "revoca")["visibile"] is False
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            consenso(db, owner, company)
        assert detail_of(exc) == "profilo_sospeso"

    def test_lock_azienda_non_blocca_gli_insert_con_fk(self, db):
        """FOR NO KEY UPDATE sulla riga azienda: serializza con gli UPDATE
        dell'azienda ma non blocca gli insert delle tabelle figlie (FK)."""
        owner, company = azienda_pronta(db)
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        try:
            consenso(altra, owner, company)
            db.execute("set lock_timeout = '500ms'")
            legale(db, company, "BNCLGU70B02F205X")  # FK verso company_profiles: passa
            with pytest.raises(psycopg.errors.LockNotAvailable):
                db.execute(
                    "update public.company_profiles set ragione_sociale = 'Altra Srl' "
                    "where id = %s", (company,))
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()


# ------------------------------------------ revoca automatica sull'azienda


class TestRevocaAutomatica:
    @pytest.mark.parametrize("assegnazione, motivo", [
        ("deleted_at = now()", "azienda_eliminata"),
        ("archived_at = now()", "azienda_archiviata"),
        ("partita_iva = '99999999999'", "piva_cambiata"),
        ("ragione_sociale = 'Altra Srl'", "ragione_sociale_cambiata"),
        ("ragione_sociale = 'Altra Srl', deleted_at = now()", "azienda_eliminata"),
    ])
    def test_revoca_con_origine_sistema(self, db, assegnazione, motivo):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        db.execute(f"update public.company_profiles set {assegnazione} where id = %s",
                   (company,))
        p = profilo(db, company)
        assert p["visibile_come_partner"] is False
        assert p["consenso_versione"] == VERSIONE
        riga = registro(db, company)[-1]
        assert riga["azione"] == "revocato" and riga["origine"] == "sistema"
        assert riga["attore_user_id"] is None and riga["motivo"] == motivo
        assert riga["informativa_versione"] == VERSIONE and riga["anonimo"] is True
        assert str(riga["family_parent_id"]) == owner
        (a,) = audit(db, "partner.consenso_revocato")
        assert a["actor_id"] is None and str(a["family_parent_id"]) == owner
        assert a["payload"] == {"company_profile_id": company, "origine": "sistema",
                                "motivo": motivo}

    @pytest.mark.parametrize("assegnazione", [
        "comune = 'Bari'",
        "sito_web = 'https://acme.example'",
        "ateco_codice = '62.01'",
        "ragione_sociale = ragione_sociale",
        "partita_iva = partita_iva, ragione_sociale = ragione_sociale",
        "updated_at = now()",
    ])
    def test_altri_campi_nessuna_revoca(self, db, assegnazione):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        db.execute(f"update public.company_profiles set {assegnazione} where id = %s",
                   (company,))
        assert profilo(db, company)["visibile_come_partner"] is True
        assert len(registro(db, company)) == 1
        assert audit(db, "partner.consenso_revocato") == []

    def test_profilo_non_visibile_nessuna_riga(self, db):
        owner, company = azienda_pronta(db)
        crea_profilo(db, company, owner)
        db.execute("update public.company_profiles set ragione_sociale = 'Altra Srl', "
                   "partita_iva = '99999999999' where id = %s", (company,))
        db.execute("update public.company_profiles set deleted_at = now() where id = %s",
                   (company,))
        assert registro(db, company) == []
        assert audit(db, "partner.consenso_revocato") == []

    def test_azienda_senza_profilo(self, db):
        owner, company = azienda_pronta(db)
        db.execute("update public.company_profiles set ragione_sociale = 'Altra Srl' "
                   "where id = %s", (company,))
        assert conta(db, "company_partner_profiles") == 0
        assert conta(db, "partner_consents") == 0

    def test_solo_il_profilo_dell_azienda_modificata(self, db):
        owner, c1 = azienda_pronta(db, plan_slug="advisor")
        _, c2 = azienda_pronta(db, owner)
        consenso(db, owner, c1)
        consenso(db, owner, c2)
        db.execute("update public.company_profiles set ragione_sociale = 'Altra Srl' "
                   "where id = %s", (c1,))
        assert profilo(db, c1)["visibile_come_partner"] is False
        assert profilo(db, c2)["visibile_come_partner"] is True

    def test_riattivazione_non_riconcede(self, db):
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        db.execute("update public.company_profiles set archived_at = now() where id = %s",
                   (company,))
        db.execute("update public.company_profiles set archived_at = null where id = %s",
                   (company,))
        assert profilo(db, company)["visibile_come_partner"] is False
        assert [r["azione"] for r in registro(db, company)] == ["concesso", "revocato"]

    def test_dentro_fn_soft_delete_company(self, db):
        owner, company = azienda_pronta(db, plan_slug="advisor")
        _, senza_profilo = azienda_pronta(db, owner)
        consenso(db, owner, company)
        db.execute("select public.fn_soft_delete_company(%s, %s)", (owner, company))
        db.execute("select public.fn_soft_delete_company(%s, %s)", (owner, senza_profilo))
        assert registro(db, company)[-1]["motivo"] == "azienda_eliminata"
        assert len(audit(db, "company.soft_deleted")) == 2
        assert db.execute(
            "select count(*) from public.company_profiles where deleted_at is not null"
        ).fetchone()[0] == 2

    def test_dentro_fn_reconcile_companies(self, db):
        """Downgrade: la riconciliazione archivia l'azienda più recente e ne revoca
        il consenso; la più vecchia resta visibile."""
        owner = new_user(db)  # gratuito: una sola azienda viva
        vecchia = make_company(db, owner, created_at="2026-01-01")
        importa(db, vecchia)
        nuova = make_company(db, owner, created_at="2026-02-01")
        importa(db, nuova)
        consenso(db, owner, vecchia)
        consenso(db, owner, nuova)
        db.execute("select public.fn_reconcile_companies(%s)", (owner,))
        assert profilo(db, vecchia)["visibile_come_partner"] is True
        assert profilo(db, nuova)["visibile_come_partner"] is False
        assert registro(db, nuova)[-1]["motivo"] == "azienda_archiviata"
        assert len(audit(db, "company.reconciled")) == 1


# ----------------------------------------------------- fn_partner_referente


class TestReferente:
    def test_proponi_e_accetta(self, db):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        a = membro(db, owner, company)
        out = referente(db, owner, company, "proponi", user=a)
        assert out == {"referente_user_id": None, "referente_proposto_user_id": a}
        p = profilo(db, company)
        assert p["referente_user_id"] is None  # effettivo solo dopo l'accettazione
        assert str(p["referente_proposto_user_id"]) == a
        assert p["referente_proposto_at"] is not None
        assert registro(db, company) == []
        (au,) = audit(db, "partner.referente_proposto")
        assert str(au["target_user_id"]) == a and str(au["actor_id"]) == owner

        out = referente(db, owner, company, "accetta", attore=a, versione=VERSIONE_REFERENTE)
        assert out == {"referente_user_id": a, "referente_proposto_user_id": None}
        p = profilo(db, company)
        assert str(p["referente_user_id"]) == a
        assert p["referente_proposto_user_id"] is None and p["referente_proposto_at"] is None
        (riga,) = registro(db, company)
        assert riga["azione"] == "referente_concesso"
        assert str(riga["attore_user_id"]) == a and str(riga["referente_user_id"]) == a
        assert riga["informativa_versione"] == VERSIONE_REFERENTE
        assert str(riga["family_parent_id"]) == owner
        (au,) = audit(db, "partner.referente_concesso")
        assert str(au["actor_id"]) == a and str(au["target_user_id"]) == a

    def test_proponi_il_referente_gia_in_carica_non_cambia_nulla(self, db):
        owner, company, a = referente_attivo(db)
        referente(db, owner, company, "proponi", user=a)
        p = profilo(db, company)
        assert str(p["referente_user_id"]) == a and p["referente_proposto_user_id"] is None

    @pytest.mark.parametrize("caso", ["altro_owner", "pending", "senza_accesso", "rimosso",
                                      "inesistente", "nullo"])
    def test_proponi_membro_non_valido(self, db, caso):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        if caso == "altro_owner":
            altro = new_user(db, "advisor")
            user = membro(db, altro, make_company(db, altro))
        elif caso == "pending":
            user = membro(db, owner, company, attivo=False)
        elif caso == "senza_accesso":
            user = membro(db, owner, make_company(db, owner))
        elif caso == "rimosso":
            user = membro(db, owner, company)
            db.execute("select public.fn_remove_family_member(%s, %s)",
                       (owner, membership(db, user)))
        elif caso == "inesistente":
            user = str(uuid.uuid4())
        else:
            user = None
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "proponi", user=user)
        assert detail_of(exc) == "referente_non_valido"

    def test_proponi_solo_dal_titolare(self, db):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        a = membro(db, owner, company)
        for attore in (a, None):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                referente(db, owner, company, "proponi", attore=attore, user=a)
            assert detail_of(exc) == "attore_non_titolare"

    def test_accetta_da_un_altro_utente(self, db):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        a, b = membro(db, owner, company), membro(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "accetta", attore=a, versione=VERSIONE_REFERENTE)
        assert detail_of(exc) == "nessuna_proposta_referente"  # nessuna proposta
        referente(db, owner, company, "proponi", user=a)
        for attore in (b, owner, None):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                referente(db, owner, company, "accetta", attore=attore,
                          versione=VERSIONE_REFERENTE)
            assert detail_of(exc) == "nessuna_proposta_referente"
        assert profilo(db, company)["referente_user_id"] is None

    @pytest.mark.parametrize("versione", [None, "", "v" * 51])
    def test_accetta_richiede_la_versione(self, db, versione):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        a = membro(db, owner, company)
        referente(db, owner, company, "proponi", user=a)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "accetta", attore=a, versione=versione)
        assert detail_of(exc) == "versione_non_valida"

    def test_accetta_rivalida_la_membership(self, db):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        a = membro(db, owner, company)
        referente(db, owner, company, "proponi", user=a)
        db.execute("select public.fn_remove_family_member(%s, %s)", (owner, membership(db, a)))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "accetta", attore=a, versione=VERSIONE_REFERENTE)
        assert detail_of(exc) == "referente_non_valido"

    def test_rifiuta(self, db):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        a, b = membro(db, owner, company), membro(db, owner, company)
        referente(db, owner, company, "proponi", user=a)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "rifiuta", attore=b)
        assert detail_of(exc) == "nessuna_proposta_referente"
        out = referente(db, owner, company, "rifiuta", attore=a)
        assert out == {"referente_user_id": None, "referente_proposto_user_id": None}
        assert profilo(db, company)["referente_proposto_at"] is None
        assert registro(db, company) == []
        assert len(audit(db, "partner.referente_rifiutato")) == 1

    def test_revoca_del_referente(self, db):
        owner, company, a = referente_attivo(db)
        for attore in (owner, new_user(db), None):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                referente(db, owner, company, "revoca", attore=attore)
            assert detail_of(exc) == "azione_non_valida"
        out = referente(db, owner, company, "revoca", attore=a)
        assert out["referente_user_id"] is None
        riga = registro(db, company)[-1]
        assert riga["azione"] == "referente_revocato"
        assert str(riga["attore_user_id"]) == a and str(riga["referente_user_id"]) == a
        assert riga["motivo"] == "rinuncia"
        assert riga["informativa_versione"] == VERSIONE_REFERENTE  # quella dell'accettazione
        (au,) = audit(db, "partner.referente_revocato")
        assert str(au["actor_id"]) == a

    def test_rimuovi_dal_titolare(self, db):
        owner, company, a = referente_attivo(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "rimuovi", attore=a)
        assert detail_of(exc) == "attore_non_titolare"
        out = referente(db, owner, company, "rimuovi", versione="altra-versione")
        assert out["referente_user_id"] is None
        riga = registro(db, company)[-1]
        assert (riga["azione"], riga["motivo"], str(riga["attore_user_id"])) == (
            "referente_revocato", "rimosso", owner)
        # La versione è quella dell'accettazione del referente, non quella inviata.
        assert riga["informativa_versione"] == VERSIONE_REFERENTE
        # Senza referente: nessun effetto.
        referente(db, owner, company, "rimuovi")
        assert len(registro(db, company)) == 2

    def test_proponi_il_titolare_riporta_il_referente_al_titolare(self, db):
        owner, company, a = referente_attivo(db)
        b = membro(db, owner, company)
        referente(db, owner, company, "proponi", user=b)
        out = referente(db, owner, company, "proponi", user=owner)
        assert out == {"referente_user_id": None, "referente_proposto_user_id": None}
        p = profilo(db, company)
        assert p["referente_proposto_at"] is None
        riga = registro(db, company)[-1]
        assert (riga["azione"], str(riga["referente_user_id"]), riga["motivo"]) == (
            "referente_revocato", a, "torna_titolare")
        # Solo una proposta pendente: annullata senza riga di registro.
        referente(db, owner, company, "proponi", user=b)
        referente(db, owner, company, "proponi", user=owner)
        assert profilo(db, company)["referente_proposto_user_id"] is None
        assert len(registro(db, company)) == 2
        assert len(audit(db, "partner.referente_proposta_annullata")) == 1

    def test_annulla_proposta_lascia_il_referente_in_carica(self, db):
        """Annullare una proposta pendente non toglie il ruolo al referente che
        ha già accettato: niente riga di registro, solo l'audit."""
        owner, company, a = referente_attivo(db)
        b = membro(db, owner, company)
        referente(db, owner, company, "proponi", user=b)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "annulla_proposta", attore=a)
        assert detail_of(exc) == "attore_non_titolare"
        out = referente(db, owner, company, "annulla_proposta")
        assert out == {"referente_user_id": a, "referente_proposto_user_id": None}
        p = profilo(db, company)
        assert str(p["referente_user_id"]) == a and p["referente_proposto_at"] is None
        assert [r["azione"] for r in registro(db, company)] == ["referente_concesso"]
        (au,) = audit(db, "partner.referente_proposta_annullata")
        assert str(au["target_user_id"]) == b and str(au["actor_id"]) == owner
        # Senza proposta: nessun effetto.
        referente(db, owner, company, "annulla_proposta")
        assert len(audit(db, "partner.referente_proposta_annullata")) == 1
        assert str(profilo(db, company)["referente_user_id"]) == a

    def test_proponi_il_referente_in_carica_annulla_la_proposta(self, db):
        owner, company, a = referente_attivo(db)
        b = membro(db, owner, company)
        referente(db, owner, company, "proponi", user=b)
        out = referente(db, owner, company, "proponi", user=a)
        assert out == {"referente_user_id": a, "referente_proposto_user_id": None}
        assert [r["azione"] for r in registro(db, company)] == ["referente_concesso"]
        assert len(audit(db, "partner.referente_proposta_annullata")) == 1
        # b non può più accettare una proposta annullata.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "accetta", attore=b, versione=VERSIONE_REFERENTE)
        assert detail_of(exc) == "nessuna_proposta_referente"

    def test_sostituzione_del_referente(self, db):
        owner, company, a = referente_attivo(db)
        b = membro(db, owner, company)
        referente(db, owner, company, "proponi", user=b)
        assert str(profilo(db, company)["referente_user_id"]) == a  # finché b non accetta
        referente(db, owner, company, "accetta", attore=b, versione=VERSIONE_REFERENTE)
        assert str(profilo(db, company)["referente_user_id"]) == b
        righe = registro(db, company)
        assert [(r["azione"], str(r["referente_user_id"])) for r in righe] == [
            ("referente_concesso", a), ("referente_revocato", a), ("referente_concesso", b)]
        assert righe[1]["motivo"] == "sostituito"

    @pytest.mark.parametrize("azione", [None, "boh", "concedi"])
    def test_azione_non_valida(self, db, azione):
        owner = new_user(db)
        company = make_company(db, owner)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, azione, user=owner)
        assert detail_of(exc) == "azione_non_valida"

    def test_company_not_found(self, db):
        owner = new_user(db, "advisor")
        company = make_company(db, owner)
        a = membro(db, owner, company)
        db.execute("update public.company_profiles set deleted_at = now() where id = %s",
                   (company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, owner, company, "proponi", user=a)
        assert detail_of(exc) == "company_not_found"
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            referente(db, new_user(db), make_company(db, owner), "proponi", user=a)
        assert detail_of(exc) == "company_not_found"

    def test_on_delete_set_null(self, db):
        """Cancellazione dell'utente referente o proposto: le FK si azzerano (il trigger
        lo consente), la proposta orfana perde anche la data, il registro resta."""
        owner, company, a = referente_attivo(db)
        b = membro(db, owner, company)
        referente(db, owner, company, "proponi", user=b)
        db.execute("delete from auth.users where id = %s", (a,))
        p = profilo(db, company)
        assert p["referente_user_id"] is None
        assert str(p["referente_proposto_user_id"]) == b
        db.execute("delete from auth.users where id = %s", (b,))
        p = profilo(db, company)
        assert p["referente_proposto_user_id"] is None and p["referente_proposto_at"] is None
        assert [r["azione"] for r in registro(db, company)] == ["referente_concesso"]


# ------------------------------------------------------- bozza AI: prenota


class TestBozzaAiPrenota:
    def test_prenotazione(self, db):
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company, riserva=25)
        p = profilo(db, company)
        assert p["bozza_ai_stato"] == "in_corso"
        assert str(p["bozza_ai_esecuzione_id"]) == eid
        assert p["bozza_ai_avviata_at"] is not None and p["bozza_ai_errore"] is None
        assert p["visibile_come_partner"] is False  # riga creata con i default
        e = esecuzione(db, eid)
        assert (e["servizio"], e["origine"], e["gruppo"], e["stato"]) == (
            "partner_profilo_ai", "utente", "altri", "in_corso")
        assert str(e["company_profile_id"]) == company and str(e["owner_id"]) == owner
        assert str(e["richiedente_user_id"]) == owner and e["costo_riservato_cents"] == 25
        assert e["bando_id"] is None

    def test_azzera_l_errore_precedente(self, db):
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company)
        concludi_ai(db, eid, "errore", cost=0)
        db.execute(
            "update public.company_partner_profiles set bozza_ai_stato = 'errore', "
            "bozza_ai_errore = 'timeout' where company_profile_id = %s", (company,))
        prenota_bozza(db, owner, company)
        assert profilo(db, company)["bozza_ai_errore"] is None

    def test_bozza_in_corso(self, db):
        owner, company = azienda_pronta(db)
        prenota_bozza(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, company)
        assert detail_of(exc) == "bozza_in_corso"
        assert conta(db, "partenariati_ai_esecuzioni") == 1

    def test_bozza_orfana_si_chiude_e_si_riprenota(self, db):
        owner, company = azienda_pronta(db)
        vecchia = prenota_bozza(db, owner, company)
        db.execute(
            "update public.company_partner_profiles set bozza_ai_avviata_at = "
            "now() - interval '11 minutes' where company_profile_id = %s", (company,))
        nuova = prenota_bozza(db, owner, company, riserva=17)
        assert nuova != vecchia
        assert str(profilo(db, company)["bozza_ai_esecuzione_id"]) == nuova
        e = esecuzione(db, vecchia)
        assert e["stato"] == "interrotta" and e["cost_cents"] is None
        assert e["errore_codice"] == "interrotta"
        # La chiamata perduta può essere stata addebitata: una riga
        # timeout_unknown con la riserva (10) della vecchia esecuzione.
        (uso,) = consumi(db)
        assert (uso["provider"], uso["outcome"], uso["cost_cents"]) == (
            "anthropic", "timeout_unknown", 10)
        assert str(uso["user_id"]) == owner and str(uso["family_parent_id"]) == owner
        assert uso["request_meta"]["esecuzione_id"] == vecchia
        assert uso["request_meta"]["failsafe"] is True

    def test_limite_per_azienda(self, db):
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company, limite_azienda=1)
        concludi_ai(db, eid, "conclusa", cost=5, input_tokens=100, output_tokens=50)
        libera_bozze(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, company, limite_azienda=1)
        assert detail_of(exc) == "ai_limite_azienda"
        assert profilo(db, company)["bozza_ai_stato"] == "pronta"  # nessun effetto
        prenota_bozza(db, owner, company, limite_azienda=2)
        libera_bozze(db)
        for limite in (2, 0):
            with pytest.raises(psycopg.errors.RaiseException) as exc:
                prenota_bozza(db, owner, company, limite_azienda=limite)
            assert detail_of(exc) == "ai_limite_azienda"
        assert conta(db, "partenariati_ai_esecuzioni") == 2

    def test_limite_per_azienda_nullo(self, db):
        owner, company = azienda_pronta(db)
        for _ in range(3):
            eid = prenota_bozza(db, owner, company, limite_azienda=None)
            concludi_ai(db, eid, "conclusa", cost=5, input_tokens=10)
            libera_bozze(db)
        assert conta(db, "partenariati_ai_esecuzioni") == 3

    def test_limite_per_azienda_cosa_conta(self, db):
        """Non contano: errore/interrotta a costo 0 senza LLM, giorni precedenti, altre
        aziende dello stesso owner, altri servizi. Conta un'interrotta a costo ignoto."""
        owner, company = azienda_pronta(db, plan_slug="advisor")
        _, altra = azienda_pronta(db, owner)
        libera = lambda: libera_bozze(db)  # noqa: E731

        eid = prenota_bozza(db, owner, company, limite_azienda=1)
        concludi_ai(db, eid, "errore", cost=0)  # modello mai chiamato
        libera()
        eid = prenota_bozza(db, owner, company, limite_azienda=1)
        concludi_ai(db, eid, "conclusa", cost=5, input_tokens=10)
        db.execute("update public.partenariati_ai_esecuzioni set giorno = giorno - 1 "
                   "where id = %s", (eid,))
        libera()
        prenota_bozza(db, owner, altra, limite_azienda=1)
        prenota_ai(db, servizio="partner_call_testi", company=company)
        libera()
        eid = prenota_bozza(db, owner, company, limite_azienda=1)
        concludi_ai(db, eid, "interrotta", cost=None)
        libera()
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, company, limite_azienda=1)
        assert detail_of(exc) == "ai_limite_azienda"

    def test_limite_per_richiedente(self, db):
        owner, company = azienda_pronta(db, plan_slug="advisor")
        _, altra = azienda_pronta(db, owner)
        prenota_bozza(db, owner, company, limite_richiedente=1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, altra, limite_richiedente=1)
        assert detail_of(exc) == "ai_limite_utente"
        assert profilo(db, altra) is None

    def test_budget_del_gruppo_altri_esaurito(self, db):
        owner, company = azienda_pronta(db)
        prenota_ai(db, servizio="partner_call_testi", gruppo="altri", budget=100, riserva=90)
        prenota_ai(db, servizio="partenariato_estrazione", gruppo="bando", budget=1000,
                   riserva=500)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, company, budget=100, riserva=20)
        assert detail_of(exc) == "ai_budget_esaurito"
        assert profilo(db, company) is None
        # Il gruppo bando non conta: con budget sufficiente per altri passa.
        prenota_bozza(db, owner, company, budget=110, riserva=20)

    @pytest.mark.parametrize("budget", [0, None])
    def test_budget_nullo_o_zero(self, db, budget):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, company, budget=budget)
        assert detail_of(exc) == "ai_budget_esaurito"

    def test_parametri_non_validi(self, db):
        owner, company = azienda_pronta(db)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, company, riserva=None)
        assert detail_of(exc) == "parametri_non_validi"

    @pytest.mark.parametrize("caso", ["altro_owner", "soft_deleted", "archiviata"])
    def test_company_not_found(self, db, caso):
        owner, company = azienda_pronta(db)
        if caso == "altro_owner":
            owner = new_user(db)
        else:
            colonna = "deleted_at" if caso == "soft_deleted" else "archived_at"
            db.execute(f"update public.company_profiles set {colonna} = now() where id = %s",
                       (company,))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            prenota_bozza(db, owner, company)
        assert detail_of(exc) == "company_not_found"
        assert conta(db, "partenariati_ai_esecuzioni") == 0

    def test_concorrenza_stessa_azienda(self, db):
        """Due prenotazioni concorrenti sulla stessa azienda: la seconda attende la
        prima e, dopo il commit, trova la bozza in corso. Una sola esecuzione."""
        owner, company = azienda_pronta(db)
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esito: dict = {}

        def seconda():
            try:
                esito["out"] = prenota_bozza(db, owner, company)
            except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
                esito["errore"] = exc

        thread = threading.Thread(target=seconda)
        pid = db.info.backend_pid
        try:
            prenota_bozza(altra, owner, company)
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
        assert errore.diag.message_detail == "bozza_in_corso"
        assert conta(db, "partenariati_ai_esecuzioni") == 1

    def test_concorrenza_budget_tra_aziende(self, db):
        """Aziende diverse: la seconda attende il lock globale del budget e ne vede la
        riserva (niente sforamento con due check-then-act paralleli)."""
        owner, c1 = azienda_pronta(db, plan_slug="advisor")
        _, c2 = azienda_pronta(db, owner)
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esito: dict = {}

        def seconda():
            try:
                esito["out"] = prenota_bozza(db, owner, c2, budget=100, riserva=60)
            except Exception as exc:  # noqa: BLE001
                esito["errore"] = exc

        thread = threading.Thread(target=seconda)
        pid = db.info.backend_pid
        try:
            prenota_bozza(altra, owner, c1, budget=100, riserva=60)
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
        finally:
            if altra.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                altra.rollback()
            altra.close()
            monitor.close()
            if thread.is_alive():
                thread.join(timeout=10)

        assert esito["errore"].diag.message_detail == "ai_budget_esaurito", esito
        assert conta(db, "partenariati_ai_esecuzioni") == 1
        assert profilo(db, c2) is None


# ------------------------------------------------- bozza AI: concludi


class TestBozzaAiConcludi:
    def test_bozza_ed_esecuzione_insieme(self, db):
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company, riserva=40)
        out = concludi_bozza(db, company, eid, bozza='{"competenze": ["x"]}', cost=7)
        assert out == {"bozza_scritta": True, "esecuzione_chiusa": True}
        p = profilo(db, company)
        assert (p["bozza_ai_stato"], p["bozza_ai"], p["bozza_ai_errore"]) == (
            "pronta", {"competenze": ["x"]}, None)
        assert p["bozza_ai_at"] is not None
        e = esecuzione(db, eid)
        assert (e["stato"], e["cost_cents"], e["input_tokens"], e["llm_eseguito"]) == (
            "conclusa", 7, 100, True)
        # Idempotente: una seconda chiusura non cambia nulla.
        out = concludi_bozza(db, company, eid, bozza_stato="errore", bozza_errore="timeout",
                             stato="timeout", cost=40)
        assert out == {"bozza_scritta": False, "esecuzione_chiusa": False}
        assert profilo(db, company)["bozza_ai_stato"] == "pronta"
        assert esecuzione(db, eid)["cost_cents"] == 7

    def test_errore_senza_proposta(self, db):
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company)
        concludi_bozza(db, company, eid, bozza_stato="errore", bozza='{"x": 1}',
                       bozza_errore="timeout", stato="timeout", cost=10, input_tokens=0,
                       output_tokens=0)
        p = profilo(db, company)
        assert (p["bozza_ai_stato"], p["bozza_ai"], p["bozza_ai_errore"]) == (
            "errore", None, "timeout")
        assert esecuzione(db, eid)["stato"] == "timeout"

    def test_bozza_superata_chiude_comunque_l_esecuzione(self, db):
        """Il failsafe o uno scarto hanno già chiuso la bozza: la proposta non si
        scrive, ma la spesa reale arriva al registro unico."""
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company)
        libera_bozze(db)
        out = concludi_bozza(db, company, eid, bozza='{"competenze": []}', cost=9)
        assert out == {"bozza_scritta": False, "esecuzione_chiusa": True}
        assert profilo(db, company)["bozza_ai"] is None
        assert esecuzione(db, eid)["cost_cents"] == 9

    def test_atomica(self, db):
        """Una chiusura dell'esecuzione che fallisce annulla anche la bozza: non
        resta mai un'esecuzione in_corso con la bozza già chiusa."""
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi_bozza(db, company, eid, stato="boh")
        assert detail_of(exc) == "stato_non_valido"
        assert profilo(db, company)["bozza_ai_stato"] == "in_corso"
        assert esecuzione(db, eid)["stato"] == "in_corso"

    def test_altra_azienda_nessun_effetto(self, db):
        owner, company = azienda_pronta(db, plan_slug="advisor")
        _, altra = azienda_pronta(db, owner)
        eid = prenota_bozza(db, owner, company)
        crea_profilo(db, altra, owner)
        out = concludi_bozza(db, altra, eid)
        assert out == {"bozza_scritta": False, "esecuzione_chiusa": False}
        assert esecuzione(db, eid)["stato"] == "in_corso"
        assert profilo(db, company)["bozza_ai_stato"] == "in_corso"

    @pytest.mark.parametrize("campo", ["company", "esecuzione", "bozza_stato"])
    def test_parametri_non_validi(self, db, campo):
        owner, company = azienda_pronta(db)
        eid = prenota_bozza(db, owner, company)
        argomenti = {"company": company, "esecuzione": eid, "bozza_stato": "pronta"}
        argomenti[campo] = "in_corso" if campo == "bozza_stato" else None
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            concludi_bozza(db, argomenti["company"], argomenti["esecuzione"],
                           bozza_stato=argomenti["bozza_stato"])
        assert detail_of(exc) == "parametri_non_validi"


# -------------------------------------------------- bozza AI: failsafe


class TestBozzaAiChiudiStale:
    def _in_corso(self, db, owner, company, minuti_fa: int, riserva: int = 10) -> str:
        eid = prenota_bozza(db, owner, company, riserva=riserva)
        db.execute(
            "update public.company_partner_profiles set bozza_ai_avviata_at = "
            "now() - make_interval(mins => %s) where company_profile_id = %s",
            (minuti_fa, company))
        db.execute(
            "update public.partenariati_ai_esecuzioni set avviata_at = "
            "now() - make_interval(mins => %s) where id = %s", (minuti_fa, eid))
        return eid

    def test_chiude_solo_le_orfane(self, db):
        owner, vecchia = azienda_pronta(db, plan_slug="advisor")
        _, fresca = azienda_pronta(db, owner)
        e_vecchia = self._in_corso(db, owner, vecchia, 11, riserva=23)
        e_fresca = self._in_corso(db, owner, fresca, 2)
        assert chiudi_stale(db, 10) == 1
        p = profilo(db, vecchia)
        assert (p["bozza_ai_stato"], p["bozza_ai_errore"]) == ("errore", "interrotta")
        e = esecuzione(db, e_vecchia)
        assert e["stato"] == "interrotta" and e["cost_cents"] is None
        assert profilo(db, fresca)["bozza_ai_stato"] == "in_corso"
        assert esecuzione(db, e_fresca)["stato"] == "in_corso"
        # Chi chiude registra: una riga timeout_unknown con la riserva.
        (uso,) = consumi(db)
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", 23)
        assert uso["request_meta"]["esecuzione_id"] == e_vecchia
        assert uso["request_meta"]["company_profile_id"] == vecchia
        assert chiudi_stale(db, 10) == 0
        assert len(consumi(db)) == 1

    def test_esecuzione_gia_chiusa_non_cambia(self, db):
        """Il job ha chiuso l'esecuzione ma non la bozza: il failsafe chiude solo la
        bozza e non registra nulla (il consumo l'ha registrato chi l'ha chiusa)."""
        owner, company = azienda_pronta(db)
        eid = self._in_corso(db, owner, company, 30)
        concludi_ai(db, eid, "conclusa", cost=7, input_tokens=100)
        assert chiudi_stale(db, 10) == 1
        e = esecuzione(db, eid)
        assert e["stato"] == "conclusa" and e["cost_cents"] == 7
        assert consumi(db) == []

    def test_esecuzione_in_corso_senza_bozza_in_corso(self, db):
        """Bozza chiusa ma esecuzione rimasta in_corso (chiusura a metà): prima
        nessuno la chiudeva più. Ora il failsafe la chiude e la registra."""
        owner, company = azienda_pronta(db)
        eid = self._in_corso(db, owner, company, 30, riserva=12)
        libera_bozze(db)
        assert chiudi_stale(db, 10) == 1
        e = esecuzione(db, eid)
        assert (e["stato"], e["cost_cents"]) == ("interrotta", None)
        assert profilo(db, company)["bozza_ai_stato"] == "pronta"
        (uso,) = consumi(db)
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", 12)
        assert chiudi_stale(db, 10) == 0

    def test_esecuzione_orfana_dopo_la_cancellazione_dell_azienda(self, db):
        owner, company = azienda_pronta(db)
        eid = self._in_corso(db, owner, company, 30)
        db.execute("delete from public.company_profiles where id = %s", (company,))
        assert profilo(db, company) is None
        assert chiudi_stale(db, 10) == 1
        assert esecuzione(db, eid)["stato"] == "interrotta"
        assert len(consumi(db)) == 1

    def test_esecuzione_orfana_recente_resta(self, db):
        owner, company = azienda_pronta(db)
        eid = self._in_corso(db, owner, company, 2)
        libera_bozze(db)
        assert chiudi_stale(db, 10) == 0
        assert esecuzione(db, eid)["stato"] == "in_corso"

    def test_altri_servizi_non_si_toccano(self, db):
        owner, company = azienda_pronta(db)
        altro = prenota_ai(db, servizio="partner_call_testi", company=company)
        db.execute("update public.partenariati_ai_esecuzioni set avviata_at = "
                   "now() - interval '1 hour' where id = %s", (altro,))
        assert chiudi_stale(db, 10) == 0
        assert esecuzione(db, altro)["stato"] == "in_corso"
        assert consumi(db) == []

    @pytest.mark.parametrize("minuti, chiuse", [(None, 0), (0, 1), (-5, 1), (1, 1), (3, 0)])
    def test_soglia(self, db, minuti, chiuse):
        """NULL = 10 minuti; minimo 1 minuto."""
        owner, company = azienda_pronta(db)
        self._in_corso(db, owner, company, 2)
        db.execute(
            "update public.company_partner_profiles set bozza_ai_avviata_at = "
            "bozza_ai_avviata_at - interval '10 seconds' where company_profile_id = %s",
            (company,))
        assert chiudi_stale(db, minuti) == chiuse

    def test_soglia_minima_protegge_le_appena_avviate(self, db):
        owner, company = azienda_pronta(db)
        prenota_bozza(db, owner, company)
        assert chiudi_stale(db, 0) == 0

    def test_salta_le_righe_bloccate(self, db):
        owner, company = azienda_pronta(db)
        self._in_corso(db, owner, company, 30)
        altra = psycopg.connect(db.info.dsn)
        try:
            altra.execute(
                "select 1 from public.company_partner_profiles where company_profile_id = %s "
                "for update", (company,))
            db.execute("set lock_timeout = '2s'")  # senza SKIP LOCKED: errore, non attesa
            assert chiudi_stale(db, 10) == 0
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()
        assert chiudi_stale(db, 10) == 1


# ---------------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0035, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0035, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")


class TestSicurezza0035:
    def test_inventario_del_file(self):
        # Se la migration crea altro, i test sotto devono coprirlo.
        assert TABELLE_NEL_FILE == TABELLE_NUOVE
        assert FUNZIONI_NEL_FILE == FUNZIONI_NUOVE
        assert not re.search(r"^create function", SQL_0035, re.M)

    def test_additiva(self):
        """Nessuna funzione o tabella esistente ridefinita, modificata o eliminata:
        company_profiles la tocca solo un trigger nuovo."""
        eseguibile = "\n".join(r for r in SQL_0035.splitlines()
                               if not r.lstrip().startswith("--"))
        assert not re.search(r"^\s*(drop|alter function|alter table public\.company_profiles)",
                             eseguibile, re.M | re.I)
        assert re.findall(r"^alter table public\.(\w+)", eseguibile, re.M) == [
            "company_partner_profiles", "partner_consents"]

    def test_trigger(self, db):
        trigger = {(r[0], r[1]) for r in db.execute(
            "select tgname, tgrelid::regclass::text from pg_trigger where not tgisinternal "
            "and tgname in ('trg_cpp_updated_at', 'trg_cpp_campi_protetti', "
            "'trg_partner_consents_readonly', 'trg_partner_consents_no_truncate', "
            "'trg_cpp_revoca_su_cambio_azienda')"
        ).fetchall()}
        assert trigger == {
            ("trg_cpp_updated_at", "company_partner_profiles"),
            ("trg_cpp_campi_protetti", "company_partner_profiles"),
            ("trg_partner_consents_readonly", "partner_consents"),
            ("trg_partner_consents_no_truncate", "partner_consents"),
            ("trg_cpp_revoca_su_cambio_azienda", "company_profiles"),
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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0035, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0035, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration e ogni fn_partner_% / fn_cpp_%
        presente nel DB è SECURITY DEFINER con search_path fissato, non eseguibile
        dai client (PUBLIC compreso) ed esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public'
                  and (p.proname like 'fn\_partner\_%%' or p.proname like 'fn\_cpp\_%%')"""
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
                SQL_0035, re.M,
            ), nome

    def test_i_client_non_eseguono_le_rpc(self, db):
        """Prova diretta: con il ruolo authenticated la chiamata fallisce."""
        db.execute("set role authenticated")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute("select public.fn_partner_bozza_ai_chiudi_stale(10)")
        finally:
            db.execute("reset role")

    def test_i_trigger_scattano_senza_execute(self, db):
        """Le revoche sulle funzioni trigger non fermano i trigger: un ruolo con i
        privilegi di tabella ma senza EXECUTE li attiva comunque."""
        owner, company = azienda_pronta(db)
        consenso(db, owner, company)
        ruolo = f"scrittore_{uuid.uuid4().hex[:8]}"
        # Come il service_role: privilegi di tabella e BYPASSRLS, niente EXECUTE.
        db.execute(f"create role {ruolo} nologin bypassrls")
        try:
            db.execute(f"grant usage on schema public to {ruolo}")
            db.execute("grant select, update on public.company_profiles, "
                       f"public.company_partner_profiles to {ruolo}")
            for funzione in ("fn_cpp_revoca_su_cambio_azienda()", "fn_cpp_campi_protetti()"):
                assert not db.execute(
                    "select has_function_privilege(%s, %s, 'execute')",
                    (ruolo, f"public.{funzione}"),
                ).fetchone()[0]
            db.execute(f"set role {ruolo}")
            try:
                with pytest.raises(psycopg.errors.RaiseException) as exc:
                    db.execute("update public.company_partner_profiles set anonimo = false")
                assert detail_of(exc) == "campo_protetto"
                db.execute("update public.company_profiles set ragione_sociale = 'Altra Srl'")
            finally:
                db.execute("reset role")
        finally:
            db.execute(f"drop owned by {ruolo}")
            db.execute(f"drop role {ruolo}")
        assert profilo(db, company)["visibile_come_partner"] is False
        assert registro(db, company)[-1]["origine"] == "sistema"
