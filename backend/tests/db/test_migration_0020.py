"""Test funzionali della migration 0020 (token videochiamata Jitsi).

Il contratto: il token nasce col booking (default di colonna, una sola
volta), è unico e non viene MAI rigenerato dagli update. La retroattività
sulle righe pre-esistenti (rewrite col default volatile) non è testabile
qui — il template applica le migration su un DB vuoto — ed è documentata
nel commento della migration.
"""

import psycopg
import pytest

TITOLARE = "a0000000-0000-0000-0000-000000000020"
PROGETTISTA = "c0000000-0000-0000-0000-000000000020"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def make_progettista(db, user_id: str, email: str) -> None:
    signup(db, user_id, email)
    db.execute("select public.fn_promote_progettista(%s, %s)", (user_id, user_id))


def make_company(db, parent: str) -> str:
    return db.execute(
        """insert into public.company_profiles (parent_id, ragione_sociale, partita_iva)
           values (%s, 'ACME Srl', '01234567890') returning id""",
        (parent,),
    ).fetchone()[0]


def make_request(db, cliente: str, company_id: str, bando_id: int = 1) -> str:
    return db.execute(
        """insert into public.consultation_requests
             (cliente_id, family_parent_id, company_profile_id, bando_id,
              bando_slug, bando_titolo, addon_slug)
           values (%s, %s, %s, %s, 'bando-di-prova', 'Bando di prova',
                   'consulto-esperto')
           returning id""",
        (cliente, cliente, company_id, bando_id),
    ).fetchone()[0]


def make_booking(db, request_id: str, token: str | None = None) -> tuple[str, str]:
    """Insert diretto (come fa fn_book_slot): ritorna (id, videocall_token)."""
    if token is None:
        row = db.execute(
            """insert into public.consultation_bookings
                 (request_id, cliente_id, progettista_id, inizio, fine)
               values (%s, %s, %s, now() + interval '1 hour', now() + interval '90 minutes')
               returning id, videocall_token""",
            (request_id, TITOLARE, PROGETTISTA),
        ).fetchone()
    else:
        row = db.execute(
            """insert into public.consultation_bookings
                 (request_id, cliente_id, progettista_id, inizio, fine, videocall_token)
               values (%s, %s, %s, now() + interval '1 hour', now() + interval '90 minutes', %s)
               returning id, videocall_token""",
            (request_id, TITOLARE, PROGETTISTA, token),
        ).fetchone()
    return str(row[0]), str(row[1])


@pytest.fixture()
def scenario(db):
    signup(db, TITOLARE, "titolare20@test.it")
    make_progettista(db, PROGETTISTA, "prog20@test.it")
    company_id = make_company(db, TITOLARE)
    return {
        "request_1": make_request(db, TITOLARE, company_id, bando_id=1),
        "request_2": make_request(db, TITOLARE, company_id, bando_id=2),
    }


class TestVideocallToken:
    def test_assegnato_allinsert(self, db, scenario):
        _, token = make_booking(db, scenario["request_1"])
        assert token  # uuid valorizzato dal default di colonna

    def test_distinto_per_riga(self, db, scenario):
        _, primo = make_booking(db, scenario["request_1"])
        _, secondo = make_booking(db, scenario["request_2"])
        assert primo != secondo

    def test_unicita(self, db, scenario):
        _, token = make_booking(db, scenario["request_1"])
        with pytest.raises(psycopg.errors.UniqueViolation):
            make_booking(db, scenario["request_2"], token=token)

    def test_mai_rigenerato_dagli_update(self, db, scenario):
        """L'idempotenza del link vive qui: nessun update tocca il token."""
        booking_id, token = make_booking(db, scenario["request_1"])
        db.execute(
            "update public.consultation_bookings set stato = 'annullata' where id = %s",
            (booking_id,),
        )
        dopo = db.execute(
            "select videocall_token from public.consultation_bookings where id = %s",
            (booking_id,),
        ).fetchone()[0]
        assert str(dopo) == token
