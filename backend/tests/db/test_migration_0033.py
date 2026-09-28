"""Test funzionali della migration 0033 (bilancio ufficiale on-demand).

Coprono: il CHECK di addons.sempre_a_pagamento (nessun addon con costo
esterno attivo gratis); il seed richiamabile dell'addon (ramo INSERT sul DB
vuoto del harness, idempotenza, ramo UPDATE con disattivazione e WARNING);
vincoli, indici e trigger di company_bilancio_richieste; tetti, unicità,
FK composta e cascade di company_bilancio_documenti; le tre RPC
(creazione con consumo ATOMICO e tetti, chiusura condizionata con rimborso
una sola volta, claim del poll con un solo vincitore, anche in concorrenza);
RLS, privilegi e firme di tutto ciò che la migration crea.
Ogni test riceve un database fresco clonato dal template.
"""

import hashlib
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
from psycopg.types.json import Jsonb

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "0033_bilancio_ufficiale.sql"
)
SQL_0033 = MIGRATION.read_text(encoding="utf-8")

SLUG = "bilancio-ufficiale"
PIVA = "01234567890"
MAX_PDF = 8_388_608
MAX_XBRL = 10_485_760

STATI_APERTI = ("in_invio", "in_lavorazione", "esito_ignoto")
STATI_TERMINALI = ("completata", "non_disponibile", "annullata", "errore")
ERRORI = (
    "bilancio_non_disponibile", "forma_non_ammessa", "identificativo_non_valido",
    "credito_provider", "non_inviata", "errore_provider", "scaduta", "esito_ignoto_scaduto",
)
ESITI_XBRL = (
    "ok", "assente", "firmato_non_leggibile", "non_valido", "consolidato",
    "cf_non_corrispondente", "troppo_grande",
)

TABELLE_NUOVE = {"company_bilancio_richieste", "company_bilancio_documenti"}
FUNZIONI_NUOVE = {
    "fn_seed_addon_bilancio_ufficiale_0033", "fn_bilancio_richiesta_crea",
    "fn_bilancio_richiesta_chiudi", "fn_bilancio_richiesta_claim_poll",
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


def addon_bilancio(db) -> int:
    return db.execute("select id from public.addons where slug = %s", (SLUG,)).fetchone()[0]


def attiva_addon(db, prezzo: str = "7.90") -> int:
    """Quello che fa l'admin da AdminAddon: prezzo e attivazione."""
    db.execute(
        "update public.addons set prezzo = %s, tipo_prezzo = 'importo', is_active = true "
        "where slug = %s",
        (prezzo, SLUG),
    )
    return addon_bilancio(db)


def make_addon(db, *, prezzo: str = "49.00", tipo_prezzo: str = "importo",
               attivo: bool = True) -> int:
    return db.execute(
        "insert into public.addons (nome, slug, prezzo, tipo_prezzo, tipo_fruizione, is_active) "
        "values ('Addon T', %s, %s, %s, 'consumabile', %s) returning id",
        (f"addon-{uuid.uuid4().hex[:8]}", prezzo, tipo_prezzo, attivo),
    ).fetchone()[0]


def grant(db, user: str, addon_id: int, qty: int) -> None:
    admin = new_user(db)
    db.execute("select public.fn_admin_grant_addon(%s, %s, %s, %s, 'test')",
               (admin, user, addon_id, qty))


def saldo(db, user_id: str, addon_id: int) -> int:
    row = db.execute(
        "select quantita from public.user_addon_inventory where user_id = %s and addon_id = %s",
        (user_id, addon_id),
    ).fetchone()
    return row[0] if row else 0


def movimenti(db, request_id: str) -> list[dict]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select tipo, delta, user_id::text as user_id, addon_id, actor_id::text as actor_id, "
            "note from public.addon_ledger where request_id = %s order by id",
            (request_id,),
        ).fetchall()


def richiesta(db, rid: str) -> dict:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "select * from public.company_bilancio_richieste where id = %s", (rid,)
        ).fetchone()


def conta_richieste(db) -> int:
    return db.execute("select count(*) from public.company_bilancio_richieste").fetchone()[0]


def detail_of(exc) -> str:
    return exc.value.diag.message_detail or ""


def payload(owner: str, company: str, addon_id: int, **extra) -> dict:
    dati = {
        "company_profile_id": company, "family_parent_id": owner, "richiesto_da": owner,
        "partita_iva": PIVA, "anno_richiesto": None, "addon_id": addon_id,
        "sandbox": False, "max_piattaforma": 30, "max_owner": 5,
    }
    dati.update(extra)
    return dati


def crea(conn, dati: dict) -> dict:
    return conn.execute(
        "select public.fn_bilancio_richiesta_crea(%s::jsonb)", (json.dumps(dati),)
    ).fetchone()[0]


def chiudi(db, rid: str, stato: str | None, campi=None, rimborsa: bool | None = False) -> dict:
    return db.execute(
        "select public.fn_bilancio_richiesta_chiudi(%s, %s, %s::jsonb, %s)",
        (rid, stato, None if campi is None else json.dumps(campi), rimborsa),
    ).fetchone()[0]


def claim(conn, rid: str, min_secondi: int | None = 60) -> bool:
    return conn.execute(
        "select public.fn_bilancio_richiesta_claim_poll(%s, %s)", (rid, min_secondi)
    ).fetchone()[0]


def inserisci(db, company: str, **colonne) -> str:
    """Insert diretta (senza RPC né consumo) per i test dei vincoli."""
    valori = {
        "company_profile_id": company, "family_parent_id": str(uuid.uuid4()),
        "richiesto_da": str(uuid.uuid4()), "partita_iva": PIVA,
        "addon_id": addon_bilancio(db), "addon_prezzo": "7.90",
    }
    valori.update(colonne)
    nomi = ", ".join(valori)
    segnaposto = ", ".join(["%s"] * len(valori))
    return str(db.execute(
        f"insert into public.company_bilancio_richieste ({nomi}) values ({segnaposto}) "
        "returning id",
        list(valori.values()),
    ).fetchone()[0])


def documento(db, richiesta_id: str, company: str, tipo: str = "pdf", n: int = 1024, *,
              dimensione: int | None = None, sha: str | None = None,
              nome: str | None = None, on_conflict: bool = False) -> None:
    sha = sha if sha is not None else hashlib.sha256(b"a" * n).hexdigest()
    db.execute(
        "insert into public.company_bilancio_documenti "
        "(richiesta_id, company_profile_id, tipo, nome_file, dimensione, sha256, contenuto) "
        "values (%s, %s, %s, %s, %s, %s, convert_to(repeat('a', %s), 'UTF8'))"
        + (" on conflict (richiesta_id, tipo) do nothing" if on_conflict else ""),
        (richiesta_id, company, tipo, nome if nome is not None else f"bilancio-2024.{tipo}",
         dimensione if dimensione is not None else n, sha, n),
    )


@pytest.fixture()
def scena(db) -> dict:
    """Owner con l'addon attivo e 3 unità, un'azienda viva, un membro attore."""
    addon_id = attiva_addon(db)
    owner = new_user(db)
    company = make_company(db, owner)
    grant(db, owner, addon_id, 3)
    return {"owner": owner, "company": company, "addon": addon_id, "membro": new_user(db)}


# --------------------------------------------- catalogo: sempre_a_pagamento


class TestSempreAPagamento:
    def test_colonna_con_default_false(self, db):
        aid = make_addon(db)
        assert db.execute(
            "select sempre_a_pagamento from public.addons where id = %s", (aid,)
        ).fetchone()[0] is False
        tipo = db.execute(
            """select data_type || ':' || is_nullable from information_schema.columns
               where table_schema = 'public' and table_name = 'addons'
                 and column_name = 'sempre_a_pagamento'"""
        ).fetchone()[0]
        assert tipo == "boolean:NO"

    def test_attivare_a_prezzo_zero_viola_il_check(self, db):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            db.execute("update public.addons set is_active = true where slug = %s", (SLUG,))
        # Il backend mappa il 23514 per nome del vincolo (addon_costo_esterno).
        assert exc.value.diag.constraint_name == "addons_sempre_a_pagamento_coerente"

    @pytest.mark.parametrize("tipo_prezzo", ["gratis", "su_richiesta"])
    def test_attivare_non_a_importo_viola_il_check(self, db, tipo_prezzo):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.addons set prezzo = 10, tipo_prezzo = %s, is_active = true "
                "where slug = %s",
                (tipo_prezzo, SLUG),
            )

    def test_attivo_a_importo_positivo_passa(self, db):
        attiva_addon(db, "7.90")
        assert db.execute(
            "select is_active, prezzo from public.addons where slug = %s", (SLUG,)
        ).fetchone() == (True, Decimal("7.90"))

    @pytest.mark.parametrize("modifica", [
        "prezzo = 0",
        "tipo_prezzo = 'gratis'",
        "tipo_prezzo = 'su_richiesta'",
        "tipo_fruizione = 'permanente'",
    ])
    def test_un_addon_attivo_non_diventa_gratis(self, db, modifica):
        attiva_addon(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(f"update public.addons set {modifica} where slug = %s", (SLUG,))

    def test_disattivato_puo_tornare_a_prezzo_zero(self, db):
        attiva_addon(db)
        db.execute(
            "update public.addons set is_active = false, prezzo = 0 where slug = %s", (SLUG,)
        )

    def test_gli_addon_normali_restano_liberi(self, db):
        # Senza il flag un addon attivo gratuito resta ammesso (es. promozioni).
        make_addon(db, prezzo="0", tipo_prezzo="gratis")
        make_addon(db, prezzo="0", tipo_prezzo="importo")

    def test_marcare_un_addon_attivo_gratis_viola_il_check(self, db):
        aid = make_addon(db, prezzo="0", tipo_prezzo="gratis")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.addons set sempre_a_pagamento = true where id = %s", (aid,)
            )


# ----------------------------------------------------------------- seed


class TestSeed:
    def _addon(self, db) -> dict:
        with db.cursor(row_factory=dict_row) as cur:
            return cur.execute(
                "select * from public.addons where slug = %s", (SLUG,)
            ).fetchone()

    def test_ramo_insert_su_db_vuoto(self, db):
        """Nel harness il catalogo parte vuoto: la migration ha creato la riga
        INATTIVA (prezzo e attivazione li fa l'admin)."""
        assert db.execute(
            "select count(*) from public.addons where slug = %s", (SLUG,)
        ).fetchone()[0] == 1
        addon = self._addon(db)
        assert addon["nome"] == "Bilancio ufficiale"
        assert addon["descrizione"]
        assert addon["is_active"] is False
        assert addon["sempre_a_pagamento"] is True
        assert addon["tipo_fruizione"] == "consumabile"
        assert addon["tipo_prezzo"] == "importo"
        assert addon["prezzo"] == Decimal("0")
        assert addon["ordering"] == 300
        assert addon["risorsa"] is None

    def test_idempotente(self, db):
        prima = self._addon(db)
        for _ in range(2):
            out = db.execute("select public.fn_seed_addon_bilancio_ufficiale_0033()").fetchone()[0]
            assert out == {"creati": 0, "aggiornati": 1, "disattivati": 0}
        dopo = self._addon(db)
        assert db.execute(
            "select count(*) from public.addons where slug = %s", (SLUG,)
        ).fetchone()[0] == 1
        campi = ("id", "nome", "descrizione", "prezzo", "tipo_prezzo", "is_active",
                 "tipo_fruizione", "sempre_a_pagamento", "ordering")
        assert {k: dopo[k] for k in campi} == {k: prima[k] for k in campi}

    def test_ramo_insert_se_la_riga_manca(self, db):
        db.execute("delete from public.addons where slug = %s", (SLUG,))
        out = db.execute("select public.fn_seed_addon_bilancio_ufficiale_0033()").fetchone()[0]
        assert out == {"creati": 1, "aggiornati": 0, "disattivati": 0}
        addon = self._addon(db)
        assert (addon["is_active"], addon["sempre_a_pagamento"]) == (False, True)

    def test_riga_esistente_attiva_e_gratis_viene_disattivata_con_warning(self, db):
        # Riga creata a mano dalla console prima della migration: attiva e gratis.
        db.execute(
            "update public.addons set sempre_a_pagamento = false, tipo_prezzo = 'gratis', "
            "tipo_fruizione = 'permanente', is_active = true, nome = 'Bilancio (console)' "
            "where slug = %s",
            (SLUG,),
        )
        avvisi: list[str] = []
        db.add_notice_handler(lambda diag: avvisi.append(diag.message_primary or ""))
        out = db.execute("select public.fn_seed_addon_bilancio_ufficiale_0033()").fetchone()[0]
        assert out == {"creati": 0, "aggiornati": 1, "disattivati": 1}
        assert any("DISATTIVATO" in a for a in avvisi), avvisi
        addon = self._addon(db)
        assert addon["is_active"] is False
        assert addon["sempre_a_pagamento"] is True
        assert addon["tipo_fruizione"] == "consumabile"
        assert addon["nome"] == "Bilancio (console)"  # i testi dell'admin non si toccano
        assert addon["tipo_prezzo"] == "gratis"

    def test_riga_esistente_attiva_a_pagamento_resta_attiva(self, db):
        db.execute(
            "update public.addons set sempre_a_pagamento = false, prezzo = 9.90, "
            "tipo_fruizione = 'permanente', is_active = true where slug = %s",
            (SLUG,),
        )
        avvisi: list[str] = []
        db.add_notice_handler(lambda diag: avvisi.append(diag.message_primary or ""))
        out = db.execute("select public.fn_seed_addon_bilancio_ufficiale_0033()").fetchone()[0]
        assert out == {"creati": 0, "aggiornati": 1, "disattivati": 0}
        assert avvisi == []
        addon = self._addon(db)
        assert (addon["is_active"], addon["prezzo"]) == (True, Decimal("9.90"))
        assert (addon["sempre_a_pagamento"], addon["tipo_fruizione"]) == (True, "consumabile")

    def test_commento_del_ledger_aggiornato(self, db):
        commento = db.execute(
            """select col_description('public.addon_ledger'::regclass, a.attnum)
               from pg_attribute a
               where a.attrelid = 'public.addon_ledger'::regclass and a.attname = 'request_id'"""
        ).fetchone()[0]
        assert "company_bilancio_richieste" in commento
        assert "consultation_requests" in commento


# --------------------------------------------------------- tabella richieste


class TestRichieste:
    def test_default(self, db, scena):
        rid = inserisci(db, scena["company"])
        r = richiesta(db, rid)
        assert r["stato"] == "in_invio"
        assert r["avvisi"] == []
        assert r["costo_provider_cents"] == 0
        assert r["sandbox"] is False
        assert r["anno_richiesto"] is None
        for col in ("ultimo_poll_at", "inviata_at", "completata_at", "rimborsata_at",
                    "provider_request_id", "errore_codice", "xbrl_esito"):
            assert r[col] is None, col

    @pytest.mark.parametrize("stato", STATI_APERTI + STATI_TERMINALI)
    def test_stati_ammessi(self, db, scena, stato):
        inserisci(db, scena["company"], stato=stato, provider_request_id="p-1")

    @pytest.mark.parametrize("codice", ERRORI)
    def test_codici_errore_ammessi(self, db, scena, codice):
        inserisci(db, scena["company"], stato="errore", errore_codice=codice)

    @pytest.mark.parametrize("esito", ESITI_XBRL)
    def test_esiti_xbrl_ammessi(self, db, scena, esito):
        inserisci(db, scena["company"], stato="completata", xbrl_esito=esito)

    @pytest.mark.parametrize("colonna, valore", [
        ("stato", "sospesa"),
        ("errore_codice", "boh"),
        ("xbrl_esito", "forse"),
        ("partita_iva", "1234567890"),
        ("partita_iva", "IT01234567890"),
        ("anno_richiesto", 1999),
        ("anno_richiesto", 2101),
        ("anno_bilancio", 1989),
        ("anno_bilancio", 2101),
        ("avvisi", Jsonb({"a": 1})),
        ("avvisi", Jsonb("testo")),
        ("costo_provider_cents", -1),
    ])
    def test_domini_vincolati(self, db, scena, colonna, valore):
        with pytest.raises(psycopg.errors.CheckViolation):
            inserisci(db, scena["company"], **{colonna: valore})

    def test_in_lavorazione_solo_con_id_del_provider(self, db, scena):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            inserisci(db, scena["company"], stato="in_lavorazione")
        assert exc.value.diag.constraint_name == "cbr_provider_id"
        rid = inserisci(db, scena["company"])
        # La transizione condizionata del backend deve portare l'id con sé.
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.company_bilancio_richieste set stato = 'in_lavorazione' "
                "where id = %s and stato = 'in_invio'", (rid,),
            )
        db.execute(
            "update public.company_bilancio_richieste "
            "set stato = 'in_lavorazione', provider_request_id = 'p-9', inviata_at = now() "
            "where id = %s and stato = 'in_invio'", (rid,),
        )

    @pytest.mark.parametrize("stato", ["completata", *STATI_APERTI])
    def test_rimborso_solo_su_chiusa_senza_bilancio(self, db, scena, stato):
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            inserisci(db, scena["company"], stato=stato, provider_request_id="p-1",
                      rimborsata_at="2026-01-01T00:00:00Z")
        assert exc.value.diag.constraint_name == "cbr_rimborso_terminale"

    @pytest.mark.parametrize("stato", ["non_disponibile", "annullata", "errore"])
    def test_rimborso_ammesso(self, db, scena, stato):
        inserisci(db, scena["company"], stato=stato, rimborsata_at="2026-01-01T00:00:00Z")

    @pytest.mark.parametrize("prima, seconda", [
        ("in_invio", "in_invio"), ("in_lavorazione", "esito_ignoto"),
        ("esito_ignoto", "in_invio"),
    ])
    def test_una_sola_aperta_per_azienda(self, db, scena, prima, seconda):
        inserisci(db, scena["company"], stato=prima, provider_request_id="p-1")
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            inserisci(db, scena["company"], stato=seconda, provider_request_id="p-2")
        assert exc.value.diag.constraint_name == "cbr_una_aperta"

    def test_le_terminali_non_bloccano(self, db, scena):
        for stato in STATI_TERMINALI:
            inserisci(db, scena["company"], stato=stato)
            inserisci(db, scena["company"], stato=stato)
        inserisci(db, scena["company"])  # una aperta accanto a otto chiuse
        # Aperte su aziende diverse: nessun conflitto.
        altra = make_company(db, new_user(db), 2)
        inserisci(db, altra)

    def test_id_del_provider_unico(self, db, scena):
        altra = make_company(db, new_user(db), 2)
        inserisci(db, scena["company"], stato="completata", provider_request_id="p-1")
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            inserisci(db, altra, stato="in_lavorazione", provider_request_id="p-1")
        assert exc.value.diag.constraint_name == "cbr_provider_id_uniq"
        # NULL multipli ammessi.
        inserisci(db, scena["company"], stato="errore")
        inserisci(db, scena["company"], stato="errore")

    def test_owner_e_attore_senza_fk(self, db, scena):
        inserisci(db, scena["company"], family_parent_id=str(uuid.uuid4()),
                  richiesto_da=str(uuid.uuid4()))
        fk = db.execute(
            """select string_agg(pg_get_constraintdef(oid), ' | ') from pg_constraint
               where conrelid = 'public.company_bilancio_richieste'::regclass
                 and contype = 'f'"""
        ).fetchone()[0]
        assert "family_parent_id" not in fk and "richiesto_da" not in fk

    def test_addon_deve_esistere(self, db, scena):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            inserisci(db, scena["company"], addon_id=999_999)

    def test_trigger_updated_at(self, db, scena):
        rid = inserisci(db, scena["company"], updated_at="2026-01-01T00:00:00Z")
        db.execute(
            "update public.company_bilancio_richieste set stato_provider = 'In ricerca' "
            "where id = %s", (rid,),
        )
        assert db.execute(
            "select updated_at > now() - interval '1 minute' "
            "from public.company_bilancio_richieste where id = %s", (rid,),
        ).fetchone()[0] is True

    def test_indici(self, db):
        definizioni = dict(db.execute(
            """select indexname, indexdef from pg_indexes
               where schemaname = 'public' and tablename = 'company_bilancio_richieste'"""
        ).fetchall())
        attesi = {
            "cbr_una_aperta": ("UNIQUE", "(company_profile_id)", "in_invio"),
            "cbr_provider_id_uniq": ("UNIQUE", "(provider_request_id)", "IS NOT NULL"),
            "cbr_company_idx": ("(company_profile_id, created_at DESC)",),
            "cbr_created_idx": ("(created_at)",),
            "cbr_owner_idx": ("(family_parent_id, created_at)",),
            "cbr_aperte_idx": ("(stato, created_at)", "esito_ignoto"),
        }
        for nome, pezzi in attesi.items():
            assert nome in definizioni, nome
            for pezzo in pezzi:
                assert pezzo in definizioni[nome], (nome, pezzo)


# --------------------------------------------------------- tabella documenti


class TestDocumenti:
    @pytest.fixture()
    def rid(self, db, scena) -> str:
        return inserisci(db, scena["company"], stato="completata")

    def test_pdf_e_xbrl_una_volta_per_richiesta(self, db, scena, rid):
        documento(db, rid, scena["company"], "pdf")
        documento(db, rid, scena["company"], "xbrl")
        with pytest.raises(psycopg.errors.UniqueViolation):
            documento(db, rid, scena["company"], "pdf", 10)
        # Il completamento ripetuto del backend (upsert do nothing) non fallisce
        # e non sovrascrive il documento già salvato.
        documento(db, rid, scena["company"], "pdf", 10, on_conflict=True)
        assert db.execute(
            "select dimensione from public.company_bilancio_documenti "
            "where richiesta_id = %s and tipo = 'pdf'", (rid,),
        ).fetchone()[0] == 1024

    def test_contenuto_integro(self, db, scena, rid):
        dati = b"%PDF-1.7\n\x00\xff binario"
        db.execute(
            "insert into public.company_bilancio_documenti "
            "(richiesta_id, company_profile_id, tipo, nome_file, dimensione, sha256, contenuto) "
            "values (%s, %s, 'pdf', 'bilancio-2024.pdf', %s, %s, %s)",
            (rid, scena["company"], len(dati), hashlib.sha256(dati).hexdigest(), dati),
        )
        letto = db.execute(
            "select contenuto from public.company_bilancio_documenti where richiesta_id = %s",
            (rid,),
        ).fetchone()[0]
        assert bytes(letto) == dati

    @pytest.mark.parametrize("tipo, n, ok", [
        ("pdf", MAX_PDF, True),
        ("pdf", MAX_PDF + 1, False),
        ("xbrl", MAX_PDF + 1, True),
        ("xbrl", MAX_XBRL, True),
        ("xbrl", MAX_XBRL + 1, False),
        ("pdf", 0, False),
        ("xbrl", 0, False),
    ])
    def test_limiti_di_dimensione(self, db, scena, rid, tipo, n, ok):
        if ok:
            documento(db, rid, scena["company"], tipo, n)
        else:
            with pytest.raises(psycopg.errors.CheckViolation):
                documento(db, rid, scena["company"], tipo, n)

    def test_dimensione_dichiarata_uguale_al_contenuto(self, db, scena, rid):
        # Il tetto vale sul contenuto reale, non su un numero dichiarato.
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            documento(db, rid, scena["company"], "pdf", MAX_PDF + 1, dimensione=1024)
        assert exc.value.diag.constraint_name == "cbd_dimensione_reale"

    @pytest.mark.parametrize("campi", [
        {"tipo": "verbale"},
        {"sha": "A" * 64},
        {"sha": "0" * 63},
        {"sha": "g" * 64},
        {"nome": ""},
        {"nome": "x" * 201},
    ])
    def test_domini_vincolati(self, db, scena, rid, campi):
        tipo = campi.pop("tipo", "pdf")
        with pytest.raises(psycopg.errors.CheckViolation):
            documento(db, rid, scena["company"], tipo, **campi)

    def test_stessa_azienda_della_richiesta(self, db, scena, rid):
        altra = make_company(db, new_user(db), 2)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            documento(db, rid, altra, "pdf")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            documento(db, str(uuid.uuid4()), scena["company"], "pdf")

    def test_cascade_dalla_richiesta(self, db, scena, rid):
        documento(db, rid, scena["company"], "pdf")
        documento(db, rid, scena["company"], "xbrl")
        db.execute("delete from public.company_bilancio_richieste where id = %s", (rid,))
        assert db.execute(
            "select count(*) from public.company_bilancio_documenti"
        ).fetchone()[0] == 0

    def test_cascade_dall_azienda_e_ledger_intatto(self, db, scena):
        padre = new_user(db, "advisor")
        grant(db, padre, scena["addon"], 2)
        a = make_company(db, padre, 1)
        b = make_company(db, padre, 2)
        ids = {}
        for cid in (a, b):
            ids[cid] = crea(db, payload(padre, cid, scena["addon"]))["richiesta"]["id"]
            chiudi(db, ids[cid], "completata", {"xbrl_esito": "ok", "anno_bilancio": 2024})
            documento(db, ids[cid], cid, "pdf")
            documento(db, ids[cid], cid, "xbrl")
        db.execute("delete from public.company_profiles where id = %s", (a,))
        per_azienda = dict(db.execute(
            "select company_profile_id::text, count(*) from public.company_bilancio_richieste "
            "group by 1"
        ).fetchall())
        assert a not in per_azienda and per_azienda[b] == 1
        documenti = dict(db.execute(
            "select company_profile_id::text, count(*) from public.company_bilancio_documenti "
            "group by 1"
        ).fetchall())
        assert a not in documenti and documenti[b] == 2
        # Il ledger è append-only e senza FK: il consumo resta tracciato.
        assert [m["tipo"] for m in movimenti(db, ids[a])] == ["consume"]


# ------------------------------------------------- RPC: creazione e consumo


class TestCrea:
    def test_consumo_atomico(self, db, scena):
        out = crea(db, payload(scena["owner"], scena["company"], scena["addon"],
                               richiesto_da=scena["membro"], anno_richiesto=2023,
                               sandbox=True))
        assert out["quantita_residua"] == 2
        assert saldo(db, scena["owner"], scena["addon"]) == 2
        r = out["richiesta"]
        assert r["stato"] == "in_invio"
        assert r["company_profile_id"] == scena["company"]
        assert r["family_parent_id"] == scena["owner"]
        assert r["richiesto_da"] == scena["membro"]
        assert r["partita_iva"] == PIVA
        assert r["anno_richiesto"] == 2023
        assert r["sandbox"] is True
        assert r["addon_id"] == scena["addon"]
        assert Decimal(str(r["addon_prezzo"])) == Decimal("7.90")  # dal catalogo
        assert movimenti(db, r["id"]) == [{
            "tipo": "consume", "delta": -1, "user_id": scena["owner"],
            "addon_id": scena["addon"], "actor_id": scena["membro"], "note": None,
        }]
        assert richiesta(db, r["id"])["stato"] == "in_invio"

    def test_anno_nullo_vuol_dire_ultimo_disponibile(self, db, scena):
        r = crea(db, payload(scena["owner"], scena["company"], scena["addon"]))["richiesta"]
        assert r["anno_richiesto"] is None and r["sandbox"] is False

    def test_prezzo_fotografato_al_momento_della_richiesta(self, db, scena):
        rid = crea(db, payload(scena["owner"], scena["company"], scena["addon"]))["richiesta"]["id"]
        db.execute("update public.addons set prezzo = 12.00 where id = %s", (scena["addon"],))
        assert richiesta(db, rid)["addon_prezzo"] == Decimal("7.90")

    def test_saldo_zero_nessuna_riga(self, db, scena):
        owner = new_user(db)  # nessuna riga di inventario
        company = make_company(db, owner, 2)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(owner, company, scena["addon"]))
        assert detail_of(exc) == "addon_credit_esaurito"
        assert conta_richieste(db) == 0

    def test_saldo_consumato_nessuna_riga_in_piu(self, db, scena):
        # Riga di inventario presente ma a 0 (ramo CHECK di fn_addon_apply_movement).
        owner = new_user(db, "advisor")
        grant(db, owner, scena["addon"], 1)
        a, b = make_company(db, owner, 1), make_company(db, owner, 2)
        crea(db, payload(owner, a, scena["addon"]))
        assert saldo(db, owner, scena["addon"]) == 0
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(owner, b, scena["addon"]))
        assert detail_of(exc) == "addon_credit_esaurito"
        assert db.execute(
            "select count(*) from public.company_bilancio_richieste where company_profile_id = %s",
            (b,),
        ).fetchone()[0] == 0
        assert saldo(db, owner, scena["addon"]) == 0

    def test_addon_inattivo(self, db, scena):
        db.execute("update public.addons set is_active = false where id = %s", (scena["addon"],))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], scena["company"], scena["addon"]))
        assert detail_of(exc) == "addon_not_available"
        assert conta_richieste(db) == 0
        assert saldo(db, scena["owner"], scena["addon"]) == 3

    def test_addon_senza_sempre_a_pagamento(self, db, scena):
        # Un addon qualunque (es. il consulto) non paga un bilancio.
        altro = make_addon(db)
        grant(db, scena["owner"], altro, 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], scena["company"], altro))
        assert detail_of(exc) == "addon_not_available"
        assert saldo(db, scena["owner"], altro) == 1

    def test_addon_inesistente(self, db, scena):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], scena["company"], 999_999))
        assert detail_of(exc) == "addon_not_available"

    def test_seconda_richiesta_aperta(self, db, scena):
        prima = crea(db, payload(scena["owner"], scena["company"], scena["addon"]))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], scena["company"], scena["addon"]))
        assert detail_of(exc) == "bilancio_in_corso"
        assert conta_richieste(db) == 1
        assert saldo(db, scena["owner"], scena["addon"]) == 2  # un solo consumo
        assert db.execute(
            "select count(*) from public.addon_ledger where tipo = 'consume' and addon_id = %s",
            (scena["addon"],),
        ).fetchone()[0] == 1
        # Chiusa la prima, se ne può aprire un'altra.
        chiudi(db, prima["richiesta"]["id"], "completata", {"xbrl_esito": "ok"})
        crea(db, payload(scena["owner"], scena["company"], scena["addon"]))
        assert saldo(db, scena["owner"], scena["addon"]) == 1

    def test_azienda_di_un_altro_owner(self, db, scena):
        estraneo = new_user(db)
        grant(db, estraneo, scena["addon"], 1)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(estraneo, scena["company"], scena["addon"]))
        assert detail_of(exc) == "company_not_found"
        assert conta_richieste(db) == 0
        assert saldo(db, estraneo, scena["addon"]) == 1

    @pytest.mark.parametrize("colonna", ["archived_at", "deleted_at"])
    def test_azienda_archiviata_o_cancellata(self, db, scena, colonna):
        db.execute(
            f"update public.company_profiles set {colonna} = now() where id = %s",
            (scena["company"],),
        )
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], scena["company"], scena["addon"]))
        assert detail_of(exc) == "company_not_found"
        assert saldo(db, scena["owner"], scena["addon"]) == 3

    def test_azienda_inesistente(self, db, scena):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], str(uuid.uuid4()), scena["addon"]))
        assert detail_of(exc) == "company_not_found"

    def test_owner_inesistente(self, db, scena):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(str(uuid.uuid4()), scena["company"], scena["addon"]))
        assert detail_of(exc) == "owner_not_found"

    def test_tetto_per_owner(self, db, scena):
        dati = payload(scena["owner"], scena["company"], scena["addon"], max_owner=2)
        for _ in range(2):
            rid = crea(db, dati)["richiesta"]["id"]
            chiudi(db, rid, "errore", {"errore_codice": "non_inviata"}, rimborsa=True)
        # Anche le richieste chiuse (e rimborsate) contano nelle 24 ore.
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, dati)
        assert detail_of(exc) == "bilanci_limite_owner"
        assert conta_richieste(db) == 2
        assert saldo(db, scena["owner"], scena["addon"]) == 3
        # Un altro owner non è toccato dal tetto del primo.
        altro = new_user(db)
        grant(db, altro, scena["addon"], 1)
        crea(db, payload(altro, make_company(db, altro, 2), scena["addon"], max_owner=2))
        # Fuori dalla finestra di 24 ore le richieste non contano più.
        db.execute(
            "update public.company_bilancio_richieste "
            "set created_at = now() - interval '25 hours' where family_parent_id = %s",
            (scena["owner"],),
        )
        crea(db, dati)

    def test_tetto_della_piattaforma(self, db, scena):
        altri = []
        for i in range(2, 4):
            owner = new_user(db)
            grant(db, owner, scena["addon"], 1)
            altri.append((owner, make_company(db, owner, i)))
        for owner, company in altri:
            crea(db, payload(owner, company, scena["addon"], max_piattaforma=2))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], scena["company"], scena["addon"],
                             max_piattaforma=2))
        assert detail_of(exc) == "bilanci_limite_piattaforma"
        assert conta_richieste(db) == 2
        assert saldo(db, scena["owner"], scena["addon"]) == 3
        db.execute(
            "update public.company_bilancio_richieste "
            "set created_at = now() - interval '25 hours'"
        )
        crea(db, payload(scena["owner"], scena["company"], scena["addon"], max_piattaforma=2))

    def test_tetto_piattaforma_prima_di_tutto(self, db, scena):
        # Con la piattaforma sospesa non si guarda nient'altro (nemmeno l'owner).
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(str(uuid.uuid4()), str(uuid.uuid4()), 999_999,
                             max_piattaforma=0))
        assert detail_of(exc) == "bilanci_limite_piattaforma"

    @pytest.mark.parametrize("tetto, codice", [
        ("max_piattaforma", "bilanci_limite_piattaforma"),
        ("max_owner", "bilanci_limite_owner"),
    ])
    @pytest.mark.parametrize("valore", [None, 0, -3])
    def test_tetti_mancanti_fail_closed(self, db, scena, tetto, codice, valore):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            crea(db, payload(scena["owner"], scena["company"], scena["addon"],
                             **{tetto: valore}))
        assert detail_of(exc) == codice
        assert conta_richieste(db) == 0
        assert saldo(db, scena["owner"], scena["addon"]) == 3

    def test_partita_iva_non_valida_annulla_tutto(self, db, scena):
        with pytest.raises(psycopg.errors.CheckViolation):
            crea(db, payload(scena["owner"], scena["company"], scena["addon"],
                             partita_iva="IT01234567890"))
        assert conta_richieste(db) == 0
        assert saldo(db, scena["owner"], scena["addon"]) == 3

    def test_tetto_piattaforma_serializzato(self, db, scena):
        """Una seconda creazione concorrente attende la prima sul lock della
        piattaforma e, dopo il commit, ne vede la riga: il tetto non si sfora
        con due check-then-act paralleli."""
        owner2 = new_user(db)
        grant(db, owner2, scena["addon"], 1)
        company2 = make_company(db, owner2, 2)
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esito: dict = {}

        def seconda():
            try:
                esito["out"] = crea(db, payload(owner2, company2, scena["addon"],
                                                max_piattaforma=1))
            except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
                esito["errore"] = exc

        thread = threading.Thread(target=seconda)
        pid = db.info.backend_pid
        try:
            crea(altra, payload(scena["owner"], scena["company"], scena["addon"],
                                max_piattaforma=1))
            thread.start()
            scadenza = time.monotonic() + 10
            while not monitor.execute(
                "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
            ).fetchone()[0]:
                assert time.monotonic() < scadenza, "la seconda creazione non attende"
                assert thread.is_alive(), f"la seconda creazione non ha atteso: {esito}"
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
        assert errore.diag.message_detail == "bilanci_limite_piattaforma"
        assert conta_richieste(db) == 1
        assert saldo(db, owner2, scena["addon"]) == 1


# ----------------------------------------------------- RPC: chiusura


class TestChiudi:
    @pytest.fixture()
    def rid(self, db, scena) -> str:
        return crea(db, payload(scena["owner"], scena["company"], scena["addon"],
                                richiesto_da=scena["membro"]))["richiesta"]["id"]

    def test_completata_con_i_campi(self, db, scena, rid):
        out = chiudi(db, rid, "completata", {
            "anno_bilancio": 2024, "xbrl_esito": "ok", "stato_provider": "Dati disponibili",
            "avvisi": ["X9: attivo diverso dal passivo"],
        })
        assert out == {"aggiornata": True, "rimborsata": False, "quantita_residua": None}
        r = richiesta(db, rid)
        assert r["stato"] == "completata"
        assert r["anno_bilancio"] == 2024
        assert r["xbrl_esito"] == "ok"
        assert r["stato_provider"] == "Dati disponibili"
        assert r["avvisi"] == ["X9: attivo diverso dal passivo"]
        assert r["completata_at"] is not None
        assert r["rimborsata_at"] is None
        assert saldo(db, scena["owner"], scena["addon"]) == 2

    def test_chiavi_assenti_lasciano_il_valore(self, db, rid):
        db.execute(
            "update public.company_bilancio_richieste set stato = 'in_lavorazione', "
            "provider_request_id = 'p-1', stato_provider = 'In erogazione', "
            "avvisi = '[\"a\"]'::jsonb where id = %s", (rid,),
        )
        chiudi(db, rid, "errore", {"errore_codice": "scaduta"})
        r = richiesta(db, rid)
        assert (r["stato"], r["errore_codice"]) == ("errore", "scaduta")
        assert r["stato_provider"] == "In erogazione"
        assert r["avvisi"] == ["a"]
        assert r["provider_request_id"] == "p-1"

    @pytest.mark.parametrize("da", STATI_APERTI)
    def test_si_chiude_da_ogni_stato_aperto(self, db, rid, da):
        db.execute(
            "update public.company_bilancio_richieste set stato = %s, "
            "provider_request_id = 'p-1' where id = %s", (da, rid),
        )
        assert chiudi(db, rid, "annullata")["aggiornata"] is True
        assert richiesta(db, rid)["stato"] == "annullata"

    def test_rimborso_una_sola_volta(self, db, scena, rid):
        assert saldo(db, scena["owner"], scena["addon"]) == 2
        out = chiudi(db, rid, "non_disponibile",
                     {"errore_codice": "bilancio_non_disponibile"}, rimborsa=True)
        assert out == {"aggiornata": True, "rimborsata": True, "quantita_residua": 3}
        assert saldo(db, scena["owner"], scena["addon"]) == 3
        r = richiesta(db, rid)
        assert r["stato"] == "non_disponibile" and r["rimborsata_at"] is not None
        rimborso = movimenti(db, rid)[-1]
        assert rimborso == {
            "tipo": "refund", "delta": 1, "user_id": scena["owner"], "addon_id": scena["addon"],
            "actor_id": None, "note": "rimborso automatico: bilancio_non_disponibile",
        }
        # Seconda chiusura (stessa o diversa): nessun effetto, nessun secondo rimborso.
        for stato, rimborsa in (("non_disponibile", True), ("errore", True),
                                ("completata", False)):
            assert chiudi(db, rid, stato, {"errore_codice": "non_inviata"}, rimborsa) == {
                "aggiornata": False, "rimborsata": False, "quantita_residua": None}
        assert saldo(db, scena["owner"], scena["addon"]) == 3
        assert [m["tipo"] for m in movimenti(db, rid)] == ["consume", "refund"]
        assert richiesta(db, rid)["stato"] == "non_disponibile"
        assert richiesta(db, rid)["errore_codice"] == "bilancio_non_disponibile"

    def test_nota_del_rimborso_senza_codice(self, db, rid):
        chiudi(db, rid, "annullata", {"stato_provider": "Annullata"}, rimborsa=True)
        assert movimenti(db, rid)[-1]["note"] == "rimborso automatico: annullata"

    def test_rimborso_all_owner_anche_se_ha_chiesto_un_membro(self, db, scena, rid):
        chiudi(db, rid, "errore", {"errore_codice": "credito_provider"}, rimborsa=True)
        assert saldo(db, scena["owner"], scena["addon"]) == 3
        assert saldo(db, scena["membro"], scena["addon"]) == 0

    def test_senza_rimborsa_nessun_rimborso(self, db, scena, rid):
        out = chiudi(db, rid, "errore", {"errore_codice": "esito_ignoto_scaduto"})
        assert out == {"aggiornata": True, "rimborsata": False, "quantita_residua": None}
        assert saldo(db, scena["owner"], scena["addon"]) == 2
        assert richiesta(db, rid)["rimborsata_at"] is None
        assert [m["tipo"] for m in movimenti(db, rid)] == ["consume"]

    def test_nessun_rimborso_senza_consumo(self, db, scena):
        # Riga senza consume nel ledger (es. creata a mano): non si regala nulla.
        rid = inserisci(db, scena["company"], family_parent_id=scena["owner"])
        out = chiudi(db, rid, "errore", {"errore_codice": "non_inviata"}, rimborsa=True)
        assert out == {"aggiornata": True, "rimborsata": False, "quantita_residua": None}
        assert richiesta(db, rid)["rimborsata_at"] is None
        assert saldo(db, scena["owner"], scena["addon"]) == 3
        assert movimenti(db, rid) == []

    def test_rimborso_dopo_la_disattivazione_dell_addon(self, db, scena, rid):
        db.execute("update public.addons set is_active = false where id = %s", (scena["addon"],))
        assert chiudi(db, rid, "errore", {"errore_codice": "non_inviata"},
                      rimborsa=True)["rimborsata"] is True
        assert saldo(db, scena["owner"], scena["addon"]) == 3

    def test_richiesta_inesistente(self, db):
        assert chiudi(db, str(uuid.uuid4()), "errore", rimborsa=True) == {
            "aggiornata": False, "rimborsata": False, "quantita_residua": None}

    def test_stato_non_rimborsabile(self, db, scena, rid):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, rid, "completata", {"xbrl_esito": "non_valido"}, rimborsa=True)
        assert detail_of(exc) == "stato_non_rimborsabile"
        assert richiesta(db, rid)["stato"] == "in_invio"
        assert saldo(db, scena["owner"], scena["addon"]) == 2

    @pytest.mark.parametrize("stato", [*STATI_APERTI, "sospesa", "", None])
    def test_stato_non_valido(self, db, rid, stato):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, rid, stato)
        assert detail_of(exc) == "stato_non_valido"
        assert richiesta(db, rid)["stato"] == "in_invio"

    @pytest.mark.parametrize("campi", [
        {"errore_code": "non_inviata"},
        {"stato": "completata"},
        {"rimborsata_at": "2026-01-01T00:00:00Z"},
        ["errore_codice"],
        "errore",
    ])
    def test_campi_non_validi(self, db, rid, campi):
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            chiudi(db, rid, "errore", campi, rimborsa=True)
        assert detail_of(exc) == "campi_non_validi"
        assert richiesta(db, rid)["stato"] == "in_invio"

    @pytest.mark.parametrize("campi", [
        {"errore_codice": "boh"},
        {"xbrl_esito": "forse"},
        {"avvisi": {"a": 1}},
        {"avvisi": None},
        {"anno_bilancio": 1900},
    ])
    def test_valori_non_validi_non_chiudono(self, db, scena, rid, campi):
        with pytest.raises(psycopg.errors.CheckViolation):
            chiudi(db, rid, "errore", campi, rimborsa=True)
        assert richiesta(db, rid)["stato"] == "in_invio"
        assert saldo(db, scena["owner"], scena["addon"]) == 2

    def test_campi_null_equivale_a_nessun_campo(self, db, rid):
        assert chiudi(db, rid, "annullata", None)["aggiornata"] is True


# ----------------------------------------------------- RPC: claim del poll


class TestClaimPoll:
    @pytest.fixture()
    def rid(self, db, scena) -> str:
        return crea(db, payload(scena["owner"], scena["company"], scena["addon"]))["richiesta"]["id"]

    def test_un_solo_vincitore_in_sequenza(self, db, rid):
        assert claim(db, rid) is True
        assert richiesta(db, rid)["ultimo_poll_at"] is not None
        assert claim(db, rid) is False
        # Passato l'intervallo, il poll si può riprendere.
        db.execute(
            "update public.company_bilancio_richieste "
            "set ultimo_poll_at = now() - interval '61 seconds' where id = %s", (rid,),
        )
        assert claim(db, rid) is True

    @pytest.mark.parametrize("da", STATI_APERTI)
    def test_su_ogni_stato_aperto(self, db, rid, da):
        db.execute(
            "update public.company_bilancio_richieste set stato = %s, "
            "provider_request_id = 'p-1' where id = %s", (da, rid),
        )
        assert claim(db, rid) is True

    def test_mai_su_una_chiusa(self, db, rid):
        chiudi(db, rid, "completata", {"xbrl_esito": "ok"})
        assert claim(db, rid) is False
        assert richiesta(db, rid)["ultimo_poll_at"] is None

    def test_richiesta_inesistente(self, db):
        assert claim(db, str(uuid.uuid4())) is False

    def test_intervallo_minimo_e_default(self, db, rid):
        db.execute(
            "update public.company_bilancio_richieste "
            "set ultimo_poll_at = now() - interval '5 seconds' where id = %s", (rid,),
        )
        assert claim(db, rid, None) is False  # NULL = 60 s
        assert claim(db, rid, 0) is True      # 0 → almeno 1 s
        assert claim(db, rid, 0) is False     # appena preso: meno di 1 s fa

    def test_un_solo_vincitore_in_concorrenza(self, db, rid):
        altra = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        monitor = psycopg.connect(db.info.dsn, autocommit=True)
        esito: dict = {}

        def seconda():
            try:
                esito["vinto"] = claim(db, rid)
            except Exception as exc:  # noqa: BLE001 - riportato nel thread principale
                esito["errore"] = exc

        thread = threading.Thread(target=seconda)
        pid = db.info.backend_pid
        try:
            assert claim(altra, rid) is True
            thread.start()
            scadenza = time.monotonic() + 10
            while not monitor.execute(
                "select cardinality(pg_blocking_pids(%s)) > 0", (pid,)
            ).fetchone()[0]:
                assert time.monotonic() < scadenza, "il secondo claim non attende"
                assert thread.is_alive(), f"il secondo claim non ha atteso: {esito}"
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

        assert esito == {"vinto": False}


# ----------------------------------------------------------- sicurezza


TABELLE_NEL_FILE = set(re.findall(r"^create table public\.(\w+)", SQL_0033, re.M))
FUNZIONI_NEL_FILE = set(re.findall(r"^create or replace function public\.(\w+)", SQL_0033, re.M))
PRIVILEGI_TABELLA = ("select", "insert", "update", "delete", "truncate", "references", "trigger")


class TestSicurezza0033:
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
            rf"^revoke all on public\.{tabella}\s+from anon, authenticated;", SQL_0033, re.M
        ), tabella
        assert re.search(
            rf"^alter table public\.{tabella}\s+enable row level security;", SQL_0033, re.M
        ), tabella

    def test_funzioni_protette_e_senza_overload(self, db):
        """Generico: ogni funzione della migration (seed compreso) e ogni
        fn_bilancio_richiesta_% presente nel DB è SECURITY DEFINER con
        search_path fissato, non eseguibile dai client (PUBLIC compreso) ed
        esiste in una sola firma."""
        dal_db = {r[0] for r in db.execute(
            r"""select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname like 'fn\_bilancio\_richiesta\_%%'"""
        ).fetchall()}
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
                SQL_0033, re.M,
            ), nome
