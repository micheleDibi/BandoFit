"""Test funzionali della migration 0032 (bilanci strutturati, lock con token,
quota giornaliera openapi).

Coprono: le colonne e i CHECK nuovi del draft; vincoli, trigger e cascade di
stato/fonti/riga fusa; TUTTI i casi condivisi di precedenza delle fonti
(`tests/fixtures/bilanci/precedenza_casi.json`, gli stessi del test Python di
`bilanci_mapping.unisci_fonti`) eseguiti sulla RPC; i detail d'errore della
RPC; la serializzazione per azienda con una seconda connessione; il lock di
import con token (le vecchie funzioni restano); la quota giornaliera
fail-closed; RLS, privilegi e firme di tutto ciò che la migration crea.
Ogni test riceve un database fresco clonato dal template.
"""

import json
import re
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0032_bilanci_strutturati.sql"
)
CASI_PRECEDENZA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "bilanci" / "precedenza_casi.json")
    .read_text(encoding="utf-8")
)["casi"]

# Contratto WP1: ordine fisso, identico in SQL e Python.
CAMPI_BILANCIO = (
    "fatturato", "valore_produzione", "risultato_esercizio", "patrimonio_netto",
    "capitale_sociale", "totale_attivo", "debiti_totali", "disponibilita_liquide",
    "ebitda", "ebit", "cash_flow", "oneri_finanziari", "dipendenti",
    "costo_personale", "retribuzione_media_lorda",
)
CAMPI_CORE = (
    "fatturato", "valore_produzione", "risultato_esercizio", "patrimonio_netto", "totale_attivo",
)
RANGO_FONTE = {"xbrl": 3, "it_full": 2, "it_advanced": 1}
MOTIVI = (
    "nessun_bilancio", "forma_senza_bilancio", "errore_provider", "esito_incerto",
    "tempo_insufficiente", "dati_non_corrispondenti", "non_richiesto", "piva_diversa",
)
ESITI = ("ok", "non_disponibili", "errore", "timeout", "saltato", "mismatch")

TABELLE_NUOVE = {
    "company_financials_stato", "company_financials_fonti", "company_financials",
    "openapi_quota_giornaliera",
}
FUNZIONI_NUOVE = {
    "fn_bilanci_registra_fonte", "fn_bilanci_ricalcola_anno",
    "fn_acquire_import_lock_token", "fn_release_import_lock_token",
    "fn_openapi_prenota_operazione",
}


# ----------------------------------------------------------------- helper


def signup(db, user_id: str, email: str, plan_slug: str | None = None) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))
    if plan_slug:
        # Dalla 0026 la registrazione non assegna piani a pagamento: si passa
        # dal percorso server legittimo.
        db.execute(
            "select public.fn_switch_plan(%s, "
            "(select id from public.subscription_plans where slug = %s))",
            (user_id, plan_slug),
        )


def new_user(db, plan_slug: str | None = None) -> str:
    uid = str(uuid.uuid4())
    signup(db, uid, f"{uid[:8]}@test.it", plan_slug)
    return uid


def make_company(db, parent: str, i: int = 1) -> str:
    return str(db.execute(
        "insert into public.company_profiles (parent_id, ragione_sociale, partita_iva) "
        "values (%s, %s, %s) returning id",
        (parent, f"ACME {i}", f"{i:011d}"),
    ).fetchone()[0])


def riga(anno, valori, *, ruolo="corrente", tipo="ignoto", data=None) -> dict:
    return {"anno": anno, "data_chiusura": data, "tipo_bilancio": tipo,
            "ruolo": ruolo, "valori": valori}


def registra(conn, company_id, fonte, righe, riferimento="import", sostituisci=False):
    return conn.execute(
        "select public.fn_bilanci_registra_fonte(%s, %s, %s::jsonb, %s, %s)",
        (company_id, fonte, json.dumps(righe), riferimento, sostituisci),
    ).fetchone()[0]


def righe_fuse(db, company_id: str) -> dict[int, dict]:
    with db.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "select * from public.company_financials where company_profile_id = %s",
            (company_id,),
        ).fetchall()
    return {r["anno"]: r for r in rows}


def fonti(db, company_id: str) -> dict[tuple[int, str], dict]:
    with db.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "select * from public.company_financials_fonti where company_profile_id = %s",
            (company_id,),
        ).fetchall()
    return {(r["anno"], r["fonte"]): r for r in rows}


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


@pytest.fixture()
def azienda(db) -> str:
    return make_company(db, new_user(db))


# ------------------------------------------------------------------ draft


class TestDraft:
    def _draft(self, db, parent, **extra):
        cols = ["parent_id", "partita_iva", "raw", "expires_at", *extra]
        vals = ["%s", "'01234567890'", "'{}'::jsonb", "now() + interval '30 minutes'"]
        vals += ["%s::jsonb" if k == "advanced_raw" else "%s" for k in extra]
        db.execute(
            f"insert into public.company_import_drafts ({', '.join(cols)}) "
            f"values ({', '.join(vals)})",
            (parent, *extra.values()),
        )

    def test_colonne_nuove_nullable_e_insert_legacy(self, db):
        tipi = dict(db.execute(
            """select column_name, data_type || ':' || is_nullable
               from information_schema.columns
               where table_schema = 'public' and table_name = 'company_import_drafts'"""
        ).fetchall())
        assert tipi["advanced_raw"] == "jsonb:YES"
        assert tipi["advanced_esito"] == "text:YES"
        assert tipi["advanced_motivo"] == "text:YES"
        assert tipi["advanced_tentato_at"] == "timestamp with time zone:YES"
        assert tipi["company_profile_id"] == "uuid:YES"
        # Il backend già in produzione scrive il draft senza le colonne nuove.
        padre = new_user(db)
        self._draft(db, padre)
        row = db.execute(
            "select advanced_raw, advanced_esito, advanced_motivo, company_profile_id "
            "from public.company_import_drafts where parent_id = %s", (padre,),
        ).fetchone()
        assert row == (None, None, None, None)

    @pytest.mark.parametrize("esito", ESITI)
    def test_esiti_ammessi(self, db, esito):
        self._draft(db, new_user(db), advanced_esito=esito)

    @pytest.mark.parametrize("motivo", MOTIVI)
    def test_motivi_ammessi(self, db, motivo):
        self._draft(db, new_user(db), advanced_esito="saltato", advanced_motivo=motivo)

    def test_esito_e_motivo_vincolati(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            self._draft(db, new_user(db), advanced_esito="forse")
        with pytest.raises(psycopg.errors.CheckViolation):
            self._draft(db, new_user(db), advanced_esito="saltato", advanced_motivo="boh")

    def test_raw_solo_con_esito_ok(self, db):
        raw = '{"balanceSheets": {"all": []}}'
        self._draft(db, new_user(db), advanced_esito="ok", advanced_raw=raw)
        with pytest.raises(psycopg.errors.CheckViolation):
            self._draft(db, new_user(db), advanced_esito="errore", advanced_raw=raw)
        # Un esito NULL con il raw valorizzato non passa (niente buco a tre valori).
        with pytest.raises(psycopg.errors.CheckViolation):
            self._draft(db, new_user(db), advanced_raw=raw)

    def test_company_profile_id_senza_fk(self, db):
        padre = new_user(db)
        cid = make_company(db, padre)
        fk = db.execute(
            """select count(*) from pg_constraint
               where conrelid = 'public.company_import_drafts'::regclass and contype = 'f'
                 and pg_get_constraintdef(oid) like '%%company_profile_id%%'"""
        ).fetchone()[0]
        assert fk == 0
        # Staging per owner: anche un'azienda inesistente è accettata...
        self._draft(db, new_user(db), company_profile_id=str(uuid.uuid4()))
        # ...e cancellare l'azienda non tocca il draft (lo scarta il backend).
        self._draft(db, padre, company_profile_id=cid)
        db.execute("delete from public.company_profiles where id = %s", (cid,))
        assert db.execute(
            "select company_profile_id from public.company_import_drafts where parent_id = %s",
            (padre,),
        ).fetchone()[0] == uuid.UUID(cid)


# ------------------------------------------------------------------ stato


class TestStato:
    def test_default(self, db, azienda):
        db.execute(
            "insert into public.company_financials_stato (company_profile_id) values (%s)",
            (azienda,),
        )
        row = db.execute(
            "select advanced_fetch_count, mapping_versione, advanced_esito, advanced_raw "
            "from public.company_financials_stato where company_profile_id = %s", (azienda,),
        ).fetchone()
        assert row == (0, 0, None, None)

    @pytest.mark.parametrize("colonne, valori", [
        ("advanced_piva", "'1234567890'"),
        ("advanced_piva", "'IT01234567890'"),
        ("mapping_versione", "-1"),
        ("advanced_fetch_count", "-1"),
        ("advanced_esito", "'forse'"),
        ("advanced_motivo", "'boh'"),
        # raw senza marca del successo: cfs_raw_coerente
        ("advanced_raw", "'{}'::jsonb"),
    ])
    def test_vincoli(self, db, azienda, colonne, valori):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                f"insert into public.company_financials_stato (company_profile_id, {colonne}) "
                f"values (%s, {valori})",
                (azienda,),
            )

    def test_raw_dopo_un_successo_resta_anche_con_esito_diverso(self, db, azienda):
        # L'ultimo tentativo può fallire: il raw dell'ultimo successo resta.
        db.execute(
            """insert into public.company_financials_stato
                 (company_profile_id, advanced_esito, advanced_motivo, advanced_raw,
                  advanced_fetched_at, advanced_piva, advanced_sandbox, advanced_fetch_count)
               values (%s, 'errore', 'errore_provider', '{"x": 1}'::jsonb, now(),
                       '01234567890', true, 2)""",
            (azienda,),
        )

    def test_trigger_updated_at(self, db, azienda):
        db.execute(
            "insert into public.company_financials_stato (company_profile_id, updated_at) "
            "values (%s, now() - interval '1 day')",
            (azienda,),
        )
        db.execute(
            "update public.company_financials_stato set mapping_versione = 1 "
            "where company_profile_id = %s", (azienda,),
        )
        recente = db.execute(
            "select updated_at > now() - interval '1 minute' "
            "from public.company_financials_stato where company_profile_id = %s", (azienda,),
        ).fetchone()[0]
        assert recente is True


# ------------------------------------------------------------ fonti e fusa


class TestVincoliFonti:
    def _fonte(self, db, cid, *, anno=2023, fonte="it_full", ruolo="corrente",
               valori='{"fatturato": "10"}', tipo="ignoto", riferimento="import"):
        db.execute(
            """insert into public.company_financials_fonti
                 (company_profile_id, anno, fonte, ruolo, tipo_bilancio, valori, riferimento)
               values (%s, %s, %s, %s, %s, %s::jsonb, %s)""",
            (cid, anno, fonte, ruolo, tipo, valori, riferimento),
        )

    def test_comparativo_solo_xbrl(self, db, azienda):
        self._fonte(db, azienda, fonte="xbrl", ruolo="comparativo")
        for fonte in ("it_full", "it_advanced"):
            with pytest.raises(psycopg.errors.CheckViolation):
                self._fonte(db, azienda, fonte=fonte, ruolo="comparativo", anno=2020)

    @pytest.mark.parametrize("valori", [
        '{"capitale_sociale": "5000"}',
        '{"fatturato": null, "dipendenti": "3"}',
        "{}",
        "[]",
    ])
    def test_anti_segnaposto_per_fonte(self, db, azienda, valori):
        with pytest.raises(psycopg.errors.CheckViolation):
            self._fonte(db, azienda, valori=valori)

    @pytest.mark.parametrize("campo, valore", [
        ("anno", 1989), ("anno", 2101), ("fonte", "visura"), ("ruolo", "precedente"),
        ("tipo", "consolidato"), ("riferimento", "boh"), ("riferimento", "bilancio:"),
    ])
    def test_domini_vincolati(self, db, azienda, campo, valore):
        with pytest.raises(psycopg.errors.CheckViolation):
            self._fonte(db, azienda, **{campo: valore})

    def test_riferimenti_ammessi_e_pk(self, db, azienda):
        for anno, rif in enumerate(("import", "recupero", "rimappatura"), start=2020):
            self._fonte(db, azienda, anno=anno, riferimento=rif)
        self._fonte(db, azienda, fonte="xbrl", riferimento=f"bilancio:{uuid.uuid4()}")
        with pytest.raises(psycopg.errors.UniqueViolation):
            self._fonte(db, azienda, anno=2020)


class TestVincoliFusa:
    def test_anti_segnaposto(self, db, azienda):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.company_financials "
                "(company_profile_id, anno, capitale_sociale, dipendenti) "
                "values (%s, 2023, 10000, 3)",
                (azienda,),
            )
        for core in CAMPI_CORE:
            db.execute(
                f"insert into public.company_financials (company_profile_id, anno, {core}) "
                "values (%s, %s, -1)",
                (azienda, 2000 + CAMPI_CORE.index(core)),
            )

    def test_colonne_nell_ordine_di_campi_bilancio(self, db):
        colonne = [r[0] for r in db.execute(
            """select column_name from information_schema.columns
               where table_schema = 'public' and table_name = 'company_financials'
               order by ordinal_position"""
        ).fetchall()]
        i = colonne.index("data_chiusura")
        assert tuple(colonne[i + 1:i + 1 + len(CAMPI_BILANCIO)]) == CAMPI_BILANCIO
        assert colonne[i + 1 + len(CAMPI_BILANCIO)] == "tipo_bilancio"

    def test_campi_delle_funzioni_allineati(self, db):
        """Le costanti v_campi/v_core delle due RPC = CAMPI_BILANCIO/CAMPI_CORE."""
        for nome in ("fn_bilanci_registra_fonte", "fn_bilanci_ricalcola_anno"):
            src = db.execute(
                "select prosrc from pg_proc where proname = %s", (nome,)
            ).fetchone()[0]
            campi = re.search(r"v_campi\s+constant text\[\] := array\[(.*?)\];", src, re.S)
            assert tuple(re.findall(r"'(\w+)'", campi.group(1))) == CAMPI_BILANCIO, nome
        src = db.execute(
            "select prosrc from pg_proc where proname = 'fn_bilanci_registra_fonte'"
        ).fetchone()[0]
        core = re.search(r"v_core\s+constant text\[\] := array\[(.*?)\];", src, re.S)
        assert tuple(re.findall(r"'(\w+)'", core.group(1))) == CAMPI_CORE

    def test_precisioni(self, db):
        tipi = dict(db.execute(
            """select column_name, numeric_precision || ',' || numeric_scale
               from information_schema.columns
               where table_schema = 'public' and table_name = 'company_financials'
                 and data_type = 'numeric'"""
        ).fetchall())
        attesi = {c: "18,2" for c in CAMPI_BILANCIO}
        attesi.update(dipendenti="10,2", retribuzione_media_lorda="12,2")
        assert tipi == attesi

    def test_trigger_updated_at(self, db, azienda):
        db.execute(
            "insert into public.company_financials "
            "(company_profile_id, anno, fatturato, updated_at) "
            "values (%s, 2023, 1, now() - interval '1 day')",
            (azienda,),
        )
        db.execute(
            "update public.company_financials set fatturato = 2 "
            "where company_profile_id = %s", (azienda,),
        )
        assert db.execute(
            "select updated_at > now() - interval '1 minute', created_at < updated_at "
            "from public.company_financials where company_profile_id = %s", (azienda,),
        ).fetchone() == (True, True)


class TestCampiPython:
    def test_allineati_al_mapping_python(self):
        """Gemello Python (ruolo backend): stessi campi, stesso ordine, stessi ranghi."""
        mapping = pytest.importorskip("app.services.bilanci_mapping")
        assert tuple(mapping.CAMPI_BILANCIO) == CAMPI_BILANCIO
        assert tuple(mapping.CAMPI_CORE) == CAMPI_CORE
        assert dict(mapping.RANGO_FONTE) == RANGO_FONTE


# ---------------------------------------------------------------- cascade


class TestCascade:
    def test_cancellare_l_azienda_porta_via_stato_fonti_e_fusa(self, db):
        padre = new_user(db, "advisor")
        a = make_company(db, padre, 1)
        b = make_company(db, padre, 2)
        for cid in (a, b):
            registra(db, cid, "it_advanced", [riga(2022, {"fatturato": "90"}),
                                              riga(2023, {"fatturato": "100"})])
            registra(db, cid, "it_full", [riga(2023, {"patrimonio_netto": "50"})])
            db.execute(
                "insert into public.company_financials_stato (company_profile_id) values (%s)",
                (cid,),
            )
        db.execute("delete from public.company_profiles where id = %s", (a,))
        for tabella in ("company_financials_stato", "company_financials_fonti",
                        "company_financials"):
            per_azienda = dict(db.execute(
                f"select company_profile_id::text, count(*) from public.{tabella} "
                "group by 1"
            ).fetchall())
            assert a not in per_azienda, tabella
            assert per_azienda[b] >= 1, tabella  # l'altra azienda dell'owner è intatta

    def test_quota_segue_il_profilo(self, db):
        owner = new_user(db)
        assert db.execute(
            "select public.fn_openapi_prenota_operazione(%s, 3)", (owner,)
        ).fetchone()[0] is True
        db.execute("delete from auth.users where id = %s", (owner,))
        assert db.execute(
            "select count(*) from public.openapi_quota_giornaliera"
        ).fetchone()[0] == 0


# ------------------------------------------------ precedenza (casi condivisi)


class TestPrecedenzaCondivisa:
    @pytest.mark.parametrize("caso", CASI_PRECEDENZA, ids=[c["nome"] for c in CASI_PRECEDENZA])
    def test_caso(self, db, azienda, caso):
        for passo in caso["passi"]:
            registra(db, azienda, passo["fonte"], passo["righe"],
                     passo["riferimento"], passo["sostituisci"])

        fuse = righe_fuse(db, azienda)
        attesi = {int(anno): atteso for anno, atteso in caso["atteso"].items()}
        # atteso null = riga fusa assente; nessun anno oltre a quelli attesi.
        assert set(fuse) == {anno for anno, atteso in attesi.items() if atteso is not None}
        for anno, atteso in attesi.items():
            if atteso is None:
                continue
            assert set(atteso["valori"]) <= set(CAMPI_BILANCIO)  # niente refusi nel file
            riga_fusa = fuse[anno]
            for campo in CAMPI_BILANCIO:
                valore = atteso["valori"].get(campo)
                if valore is None:
                    assert riga_fusa[campo] is None, (anno, campo)
                else:
                    assert riga_fusa[campo] == Decimal(valore), (anno, campo)
            assert riga_fusa["fonte_per_campo"] == atteso["fonte_per_campo"], anno

    def test_ci_sono_tutti_i_casi_del_piano(self):
        nomi = {c["nome"] for c in CASI_PRECEDENZA}
        assert {"xbrl_batte_it_full_batte_it_advanced", "null_non_sovrascrive",
                "anni_indipendenti", "comparativo_non_sostituisce_corrente",
                "sostituisci_rimuove_anni_spariti"} <= nomi


# ----------------------------------------------------- RPC: comportamento


class TestRegistraFonte:
    def test_ritorna_gli_anni_registrati(self, db, azienda):
        esito = registra(db, azienda, "it_advanced", [
            riga(2023, {"fatturato": "100"}), riga(2019, {"fatturato": "70"}),
            riga(2021, {"fatturato": "80"}),
        ])
        assert esito == {"anni": [2019, 2021, 2023]}
        # Con sostituisci gli anni rimossi NON sono tra quelli registrati.
        esito = registra(db, azienda, "it_advanced", [riga(2021, {"fatturato": "81"})],
                         "recupero", True)
        assert esito == {"anni": [2021]}
        assert set(righe_fuse(db, azienda)) == {2021}

    def test_valori_normalizzati_senza_null(self, db, azienda):
        registra(db, azienda, "it_full", [riga(2023, {
            "fatturato": "1.5E+3", "patrimonio_netto": "+42.10", "ebitda": "-7",
            "ebit": None, "dipendenti": "12.5",
        })])
        salvata = fonti(db, azienda)[(2023, "it_full")]
        assert salvata["valori"] == {"fatturato": "1500", "patrimonio_netto": "42.10",
                                     "ebitda": "-7", "dipendenti": "12.5"}
        fusa = righe_fuse(db, azienda)[2023]
        assert fusa["fatturato"] == Decimal("1500")
        assert fusa["ebitda"] == Decimal("-7")
        assert fusa["ebit"] is None
        assert fusa["dipendenti"] == Decimal("12.5")
        assert "ebit" not in fusa["fonte_per_campo"]

    def test_data_tipo_e_fetched_per_rango(self, db, azienda):
        registra(db, azienda, "it_advanced",
                 [riga(2023, {"fatturato": "100"}, tipo="micro", data="2023-12-31")])
        registra(db, azienda, "it_full",
                 [riga(2023, {"fatturato": "110"}, tipo="ordinario", data=None)])
        fusa = righe_fuse(db, azienda)[2023]
        assert fusa["tipo_bilancio"] == "ordinario"      # primo ≠ ignoto per rango
        assert str(fusa["data_chiusura"]) == "2023-12-31"  # unica data non nulla
        # xbrl con tipo ignoto non cancella il tipo noto, ma la sua data vince.
        registra(db, azienda, "xbrl",
                 [riga(2023, {"fatturato": "120"}, tipo="ignoto", data="2023-06-30")],
                 "bilancio:x")
        fusa = righe_fuse(db, azienda)[2023]
        assert fusa["tipo_bilancio"] == "ordinario"
        assert str(fusa["data_chiusura"]) == "2023-06-30"
        massimo = db.execute(
            "select max(fetched_at) from public.company_financials_fonti "
            "where company_profile_id = %s and anno = 2023", (azienda,),
        ).fetchone()[0]
        assert fusa["fetched_at"] == massimo

    def test_comparativo_non_tocca_la_riga_corrente(self, db, azienda):
        registra(db, azienda, "xbrl", [riga(2022, {"fatturato": "300"}, tipo="abbreviato")],
                 "bilancio:a")
        registra(db, azienda, "xbrl", [riga(2022, {"fatturato": "290", "ebit": "5"},
                                            ruolo="comparativo")], "bilancio:b")
        salvata = fonti(db, azienda)[(2022, "xbrl")]
        assert (salvata["ruolo"], salvata["riferimento"]) == ("corrente", "bilancio:a")
        assert salvata["valori"] == {"fatturato": "300"}
        assert righe_fuse(db, azienda)[2022]["ebit"] is None

    def test_sostituisci_con_array_vuoto_svuota_solo_quella_fonte(self, db, azienda):
        registra(db, azienda, "it_advanced", [riga(2022, {"fatturato": "90"}),
                                              riga(2023, {"fatturato": "100"})])
        registra(db, azienda, "it_full", [riga(2023, {"patrimonio_netto": "50"})])
        registra(db, azienda, "it_advanced", [], "recupero", True)
        fuse = righe_fuse(db, azienda)
        assert set(fuse) == {2023}
        assert fuse[2023]["fatturato"] is None
        assert fuse[2023]["fonte_per_campo"] == {"patrimonio_netto": "it_full"}

    def test_sostituisci_false_conserva_gli_anni_precedenti(self, db, azienda):
        # B5: la rimappatura di it_full non usa mai «sostituisci».
        registra(db, azienda, "it_full", [riga(2022, {"patrimonio_netto": "40"})])
        registra(db, azienda, "it_full", [riga(2023, {"patrimonio_netto": "50"})],
                 "rimappatura", False)
        assert set(righe_fuse(db, azienda)) == {2022, 2023}

    def test_arrotondamento_alla_scala_della_colonna(self, db, azienda):
        registra(db, azienda, "it_advanced",
                 [riga(2023, {"fatturato": "100.005", "dipendenti": "3.333"})])
        fusa = righe_fuse(db, azienda)[2023]
        assert fusa["fatturato"] == Decimal("100.01")
        assert fusa["dipendenti"] == Decimal("3.33")


# ---------------------------------------------------------- RPC: errori


RIGHE_NON_VALIDE = [
    ("chiave_ignota", "it_full", [riga(2023, {"fatturato": "1", "utile": "2"})]),
    ("segnaposto_solo_non_core", "it_advanced", [riga(2023, {"capitale_sociale": "10000"})]),
    ("segnaposto_tutti_null", "it_advanced", [riga(2023, {"fatturato": None})]),
    ("segnaposto_valori_vuoti", "it_advanced", [riga(2023, {})]),
    ("comparativo_su_it_full", "it_full", [riga(2023, {"fatturato": "1"}, ruolo="comparativo")]),
    ("comparativo_su_it_advanced", "it_advanced",
     [riga(2023, {"fatturato": "1"}, ruolo="comparativo")]),
    ("ruolo_ignoto", "xbrl", [riga(2023, {"fatturato": "1"}, ruolo="precedente")]),
    ("anno_prima_del_1990", "it_advanced", [riga(1989, {"fatturato": "1"})]),
    ("anno_dopo_il_2100", "it_advanced", [riga(2101, {"fatturato": "1"})]),
    ("anno_non_intero", "it_advanced", [riga(2023.5, {"fatturato": "1"})]),
    ("anno_stringa", "it_advanced", [riga("2023", {"fatturato": "1"})]),
    ("anno_mancante", "it_advanced", [{"valori": {"fatturato": "1"}}]),
    ("anno_ripetuto", "it_advanced", [riga(2023, {"fatturato": "1"}),
                                      riga(2023, {"fatturato": "2"})]),
    ("valore_numero_json", "it_full", [riga(2023, {"fatturato": 100.5})]),
    ("valore_con_virgola", "it_full", [riga(2023, {"fatturato": "100,5"})]),
    ("valore_nan", "it_full", [riga(2023, {"fatturato": "NaN"})]),
    ("valore_con_spazi", "it_full", [riga(2023, {"fatturato": " 100"})]),
    ("fatturato_fuori_scala", "it_full", [riga(2023, {"fatturato": "1e16"})]),
    ("dipendenti_fuori_scala", "it_full",
     [riga(2023, {"fatturato": "1", "dipendenti": "100000000"})]),
    ("tipo_bilancio_ignoto", "it_full", [riga(2023, {"fatturato": "1"}, tipo="consolidato")]),
    ("data_inesistente", "it_full", [riga(2023, {"fatturato": "1"}, data="2023-02-30")]),
    ("data_non_iso", "it_full", [riga(2023, {"fatturato": "1"}, data="31/12/2023")]),
    ("data_numero", "it_full", [riga(2023, {"fatturato": "1"}, data=20231231)]),
    ("riga_non_oggetto", "it_full", [[2023, {"fatturato": "1"}]]),
    ("valori_non_oggetto", "it_full", [riga(2023, ["1"])]),
    ("righe_non_array", "it_full", riga(2023, {"fatturato": "1"})),
    ("righe_null", "it_full", None),
]


class TestErroriRegistra:
    @pytest.mark.parametrize("fonte", ["visura", "XBRL", None])
    def test_fonte_non_valida(self, db, azienda, fonte):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            registra(db, azienda, fonte, [riga(2023, {"fatturato": "1"})])
        assert detail_of(exc) == "fonte_non_valida"

    def test_fonte_controllata_prima_dell_azienda(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            registra(db, str(uuid.uuid4()), "visura", [])
        assert detail_of(exc) == "fonte_non_valida"

    @pytest.mark.parametrize("company_id", [str(uuid.uuid4()), None])
    def test_azienda_non_trovata(self, db, company_id):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            registra(db, company_id, "it_full", [riga(2023, {"fatturato": "1"})])
        assert detail_of(exc) == "azienda_non_trovata"

    @pytest.mark.parametrize("fonte, righe", [(f, r) for _, f, r in RIGHE_NON_VALIDE],
                             ids=[n for n, _, _ in RIGHE_NON_VALIDE])
    def test_righe_non_valide(self, db, azienda, fonte, righe):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            registra(db, azienda, fonte, righe)
        assert detail_of(exc) == "righe_non_valide"
        assert db.execute("select count(*) from public.company_financials_fonti").fetchone()[0] == 0

    def test_una_riga_non_valida_annulla_tutta_la_chiamata(self, db, azienda):
        registra(db, azienda, "it_advanced", [riga(2020, {"fatturato": "70"}),
                                              riga(2021, {"fatturato": "80"})])
        prima = fonti(db, azienda)
        with pytest.raises(psycopg.errors.RaiseException):
            # La prima riga è valida, la seconda no; con sostituisci il 2020
            # sarebbe stato cancellato: nulla deve cambiare.
            registra(db, azienda, "it_advanced",
                     [riga(2021, {"fatturato": "81"}), riga(2022, {"utile": "1"})],
                     "recupero", True)
        assert fonti(db, azienda) == prima
        assert {a: r["fatturato"] for a, r in righe_fuse(db, azienda).items()} == {
            2020: Decimal("70"), 2021: Decimal("80")}


# -------------------------------------------------------- concorrenza


class TestConcorrenza:
    def test_registrazione_in_coda_sul_lock_dell_azienda(self, db):
        padre = new_user(db, "advisor")
        a = make_company(db, padre, 1)
        b = make_company(db, padre, 2)
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        try:
            registra(altra, a, "it_full", [riga(2023, {"patrimonio_netto": "50"})])
            db.execute("set lock_timeout = '500ms'")
            # FOR NO KEY UPDATE non blocca gli insert con FK verso l'azienda...
            db.execute(
                "insert into public.company_people (company_profile_id, kind, nome, raw) "
                "values (%s, 'manager', 'Mario', '{}'::jsonb)",
                (a,),
            )
            # ...né le registrazioni di un'altra azienda...
            registra(db, b, "it_advanced", [riga(2023, {"fatturato": "1"})])
            # ...ma una seconda registrazione sulla stessa azienda si mette in coda,
            # anche per un altro anno (nessun conflitto di riga: è il lock
            # dell'azienda a serializzare).
            with pytest.raises(psycopg.errors.LockNotAvailable):
                registra(db, a, "it_advanced", [riga(2021, {"fatturato": "100"})])
        finally:
            db.execute("set lock_timeout = 0")
            altra.rollback()
            altra.close()

    def test_registrazioni_concorrenti_non_perdono_valori(self, db, azienda):
        """Senza il lock la seconda transazione ricalcolerebbe l'anno senza
        vedere la fonte non ancora committata della prima, e il suo upsert
        della riga fusa perderebbe il patrimonio netto (lost update)."""
        altra = psycopg.connect(db.info.dsn)
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esito: dict = {}

        def seconda():
            try:
                esito["anni"] = registra(db, azienda, "it_advanced",
                                         [riga(2023, {"fatturato": "100",
                                                      "risultato_esercizio": "10"})])
            except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
                esito["errore"] = exc

        thread = threading.Thread(target=seconda)
        pid = db.info.backend_pid
        try:
            registra(altra, azienda, "it_full",
                     [riga(2023, {"fatturato": "110", "patrimonio_netto": "50"})])
            thread.start()
            scadenza = time.monotonic() + 10
            while not monitor.execute(
                "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
            ).fetchone()[0]:
                assert time.monotonic() < scadenza, "la seconda registrazione non attende"
                assert thread.is_alive(), f"la seconda registrazione non ha atteso: {esito}"
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

        assert "errore" not in esito, esito
        assert esito["anni"] == {"anni": [2023]}
        fusa = righe_fuse(db, azienda)[2023]
        assert fusa["fatturato"] == Decimal("110")
        assert fusa["patrimonio_netto"] == Decimal("50")
        assert fusa["risultato_esercizio"] == Decimal("10")
        assert fusa["fonte_per_campo"] == {"fatturato": "it_full", "patrimonio_netto": "it_full",
                                           "risultato_esercizio": "it_advanced"}


# ---------------------------------------------------------- lock con token


def acquire_token(db, parent: str, ttl: int = 120):
    return db.execute(
        "select public.fn_acquire_import_lock_token(%s, %s)", (parent, ttl)
    ).fetchone()[0]


def release_token(db, parent: str, token) -> None:
    db.execute("select public.fn_release_import_lock_token(%s, %s)", (parent, token))


def lock_presente(db, parent: str) -> bool:
    return db.execute(
        "select exists (select 1 from public.company_import_locks where parent_id = %s)",
        (parent,),
    ).fetchone()[0]


class TestLockToken:
    def test_acquisizione_e_occupato(self, db):
        padre = new_user(db)
        token = acquire_token(db, padre)
        assert isinstance(token, uuid.UUID)
        assert db.execute(
            "select token from public.company_import_locks where parent_id = %s", (padre,)
        ).fetchone()[0] == token
        assert acquire_token(db, padre) is None  # lock ancora valido

    def test_furto_dopo_scadenza_con_token_nuovo(self, db):
        padre = new_user(db)
        vecchio = acquire_token(db, padre)
        db.execute(
            "update public.company_import_locks set expires_at = now() - interval '1 second' "
            "where parent_id = %s", (padre,),
        )
        nuovo = acquire_token(db, padre)
        assert nuovo is not None and nuovo != vecchio
        # Il detentore scaduto non può più cancellare il lock di chi l'ha ripreso.
        release_token(db, padre, vecchio)
        assert lock_presente(db, padre)
        release_token(db, padre, nuovo)
        assert not lock_presente(db, padre)

    def test_release_con_token_sbagliato_o_nullo_non_cancella(self, db):
        padre = new_user(db)
        token = acquire_token(db, padre)
        release_token(db, padre, uuid.uuid4())
        release_token(db, padre, None)
        assert lock_presente(db, padre)
        release_token(db, padre, token)
        assert not lock_presente(db, padre)
        assert acquire_token(db, padre) is not None

    def test_release_non_tocca_il_lock_di_un_altro_owner(self, db):
        a, b = new_user(db), new_user(db)
        token_a = acquire_token(db, a)
        acquire_token(db, b)
        release_token(db, b, token_a)
        assert lock_presente(db, b)

    def test_ttl_limitato(self, db):
        padre = new_user(db)
        acquire_token(db, padre, ttl=99999)
        assert db.execute(
            "select expires_at <= now() + interval '600 seconds' "
            "from public.company_import_locks where parent_id = %s", (padre,),
        ).fetchone()[0] is True
        altro = new_user(db)
        acquire_token(db, altro, ttl=0)  # clamp a 1 s: il lock nasce valido
        assert db.execute(
            "select expires_at > now() from public.company_import_locks where parent_id = %s",
            (altro,),
        ).fetchone()[0] is True

    def test_vecchie_funzioni_continuano_a_funzionare(self, db):
        padre = new_user(db)
        # Backend in produzione (senza token): stessa semantica di prima.
        assert db.execute(
            "select public.fn_acquire_import_lock(%s, 120)", (padre,)
        ).fetchone()[0] is True
        legacy = db.execute(
            "select token from public.company_import_locks where parent_id = %s", (padre,)
        ).fetchone()[0]
        assert legacy is not None  # default per le righe della vecchia acquire
        assert acquire_token(db, padre) is None  # i due percorsi si escludono
        release_token(db, padre, uuid.uuid4())
        assert lock_presente(db, padre)
        db.execute("select public.fn_release_import_lock(%s)", (padre,))
        assert not lock_presente(db, padre)
        # E al contrario: un lock col token blocca la vecchia acquire.
        token = acquire_token(db, padre)
        assert db.execute(
            "select public.fn_acquire_import_lock(%s, 120)", (padre,)
        ).fetchone()[0] is False
        release_token(db, padre, token)
        assert db.execute(
            "select public.fn_acquire_import_lock(%s, 120)", (padre,)
        ).fetchone()[0] is True


# --------------------------------------------------- quota giornaliera


def prenota(db, owner: str, per_azienda: int | None = 3) -> bool:
    return db.execute(
        "select public.fn_openapi_prenota_operazione(%s, %s)", (owner, per_azienda)
    ).fetchone()[0]


def conteggi(db, owner: str) -> dict:
    return {str(g): c for g, c in db.execute(
        "select giorno, conteggio from public.openapi_quota_giornaliera where owner_id = %s",
        (owner,),
    ).fetchall()}


def oggi_roma(db) -> str:
    return str(db.execute("select (now() at time zone 'Europe/Rome')::date").fetchone()[0])


class TestQuotaGiornaliera:
    def test_gratuito_tre_al_giorno(self, db):
        owner = new_user(db)
        assert [prenota(db, owner) for _ in range(3)] == [True, True, True]
        assert prenota(db, owner) is False
        assert prenota(db, owner) is False
        # Le prenotazioni rifiutate non consumano.
        assert conteggi(db, owner) == {oggi_roma(db): 3}

    def test_advisor_trenta_al_giorno(self, db):
        owner = new_user(db, "advisor")  # 10 aziende gestibili
        assert all(prenota(db, owner) for _ in range(30))
        assert prenota(db, owner) is False
        assert conteggi(db, owner) == {oggi_roma(db): 30}

    def test_limite_segue_le_aziende_gestibili(self, db):
        owner = new_user(db)
        db.execute("update public.profiles set max_aziende_override = 2 where id = %s", (owner,))
        assert all(prenota(db, owner) for _ in range(6))
        assert prenota(db, owner) is False

    def test_owner_indipendenti(self, db):
        a, b = new_user(db), new_user(db)
        for _ in range(3):
            prenota(db, a)
        assert prenota(db, a) is False
        assert prenota(db, b) is True

    def test_giorno_diverso_riparte(self, db):
        owner = new_user(db)
        for _ in range(3):
            prenota(db, owner)
        assert prenota(db, owner) is False
        # La riga di oggi diventa quella di ieri: oggi si riparte da zero.
        db.execute(
            "update public.openapi_quota_giornaliera set giorno = giorno - 1 "
            "where owner_id = %s", (owner,),
        )
        assert prenota(db, owner) is True
        ieri = str(db.execute(
            "select (now() at time zone 'Europe/Rome')::date - 1"
        ).fetchone()[0])
        assert conteggi(db, owner) == {ieri: 3, oggi_roma(db): 1}

    def test_giorno_solare_di_roma_qualunque_fuso_di_sessione(self, db):
        """Con due fusi di sessione distanti 26 ore (date sempre diverse fra
        loro) le due prenotazioni devono finire sulla STESSA riga, quella del
        giorno di Roma: un'implementazione con current_date fallirebbe."""
        owner = new_user(db)
        try:
            db.execute("set timezone = 'Pacific/Kiritimati'")
            assert prenota(db, owner) is True
            db.execute("set timezone = 'Etc/GMT+12'")
            assert prenota(db, owner) is True
        finally:
            db.execute("reset timezone")
        assert conteggi(db, owner) == {oggi_roma(db): 2}

    @pytest.mark.parametrize("per_azienda", [0, -1, None])
    def test_parametri_insensati_fail_closed(self, db, per_azienda):
        owner = new_user(db)
        assert prenota(db, owner, per_azienda) is False
        assert conteggi(db, owner) == {}

    def test_owner_nullo_fail_closed(self, db):
        assert db.execute(
            "select public.fn_openapi_prenota_operazione(null, 3)"
        ).fetchone()[0] is False

    def test_conteggio_non_negativo(self, db):
        owner = new_user(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into public.openapi_quota_giornaliera (owner_id, giorno, conteggio) "
                "values (%s, current_date, -1)", (owner,),
            )


# ----------------------------------------------------------- sicurezza


SQL_0032 = MIGRATION.read_text(encoding="utf-8")
TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0032, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0032, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")


class TestSicurezza0032:
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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0032, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0032, re.M
        ), tabella

    def test_funzioni_nuove_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration e ogni fn_bilanci_% presente
        nel DB è SECURITY DEFINER con search_path fissato, non eseguibile dai
        client (PUBLIC compreso) ed esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname like 'fn\_bilanci\_%%'"""
        ).fetchall()}
        nomi = FUNZIONI_NEL_FILE | FUNZIONI_NUOVE | dal_db
        for nome in sorted(nomi):
            righe = db.execute(
                """select p.oid, p.prosecdef, coalesce(p.proconfig, '{}')
                   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and p.proname = %s""",
                (nome,),
            ).fetchall()
            assert len(righe) == 1, f"{nome}: {len(righe)} firme"
            oid, secdef, config = righe[0]
            assert secdef is True, f"{nome} non è security definer"
            assert "search_path=public" in config, f"{nome}: search_path non fissato"
            for ruolo in ("anon", "authenticated"):
                assert not db.execute(
                    "select has_function_privilege(%s, %s::oid, 'execute')", (ruolo, oid)
                ).fetchone()[0], f"{ruolo} esegue {nome}"

    def test_lock_legacy_ancora_protetti(self, db):
        for firma in ("public.fn_acquire_import_lock(uuid, integer)",
                      "public.fn_release_import_lock(uuid)"):
            assert not db.execute(
                "select has_function_privilege('anon', %s, 'execute')", (firma,)
            ).fetchone()[0]
