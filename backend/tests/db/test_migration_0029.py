"""Test funzionali della migration 0029 (venditore croato).

Coprono: il nuovo CHECK a 2 valori su billing_profiles.tipo_soggetto (accetta
azienda/privato, rifiuta i tre valori storici), le colonne SDI congelate
(esistono ancora, l'insert che le omette usa il default), e il roundtrip di
subscription_plans.features_override (text[] e NULL).

NOTA: la rimappatura UPDATE dei valori storici NON è testabile qui —
l'harness applica tutte le migration su un DB vuoto, quindi non possono
esistere righe pre-0029. La prova vive nel blocco DO di verifica in coda
alla migration stessa (abortisce se resta un valore non mappato).
"""

import uuid

import psycopg
import pytest


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def new_user(db) -> str:
    uid = str(uuid.uuid4())
    signup(db, uid, f"{uid[:8]}@test.it")
    return uid


def insert_profile(db, user_id: str, tipo: str) -> None:
    # Omette codice_destinatario/pec: il default deve coprire (colonne
    # congelate, il backend non le scrive più).
    db.execute(
        "insert into public.billing_profiles "
        "(user_id, tipo_soggetto, denominazione, partita_iva, paese, "
        " indirizzo, comune, cap) "
        "values (%s, %s, 'ACME', '03930330794', 'IT', 'Via Roma 1', "
        "        'Catanzaro', '88100')",
        (user_id, tipo),
    )


class TestTipoSoggetto:
    def test_valori_nuovi_accettati(self, db):
        for tipo in ("azienda", "privato"):
            insert_profile(db, new_user(db), tipo)

    def test_valori_storici_rifiutati(self, db):
        for tipo in ("azienda_it", "privato_it", "azienda_ue"):
            with pytest.raises(psycopg.errors.CheckViolation):
                insert_profile(db, new_user(db), tipo)


class TestColonneCongelate:
    def test_codice_destinatario_default_su_insert_che_lo_omette(self, db):
        uid = new_user(db)
        insert_profile(db, uid, "azienda")
        row = db.execute(
            "select codice_destinatario, pec from public.billing_profiles "
            "where user_id = %s", (uid,),
        ).fetchone()
        assert row == ("0000000", None)  # default NOT NULL intatto, pec nullable


class TestFeaturesOverride:
    def test_roundtrip_array_e_null(self, db):
        db.execute(
            "insert into public.subscription_plans "
            "(nome, slug, prezzo_annuale, ai_check, num_account_aziendali, "
            " features_override) "
            "values ('Tailored', 'tailored-test', 0, 0, 1, "
            "        array['Proposta su misura in base alle esigenze'])"
        )
        row = db.execute(
            "select features_override from public.subscription_plans "
            "where slug = 'tailored-test'"
        ).fetchone()
        assert row[0] == ["Proposta su misura in base alle esigenze"]
        # NULL = bullet derivate dai campi numerici (default dei piani seed).
        assert db.execute(
            "select features_override from public.subscription_plans "
            "where slug = 'gratuito'"
        ).fetchone()[0] is None
