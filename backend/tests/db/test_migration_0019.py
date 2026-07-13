"""Test funzionali della migration 0019 (parità admin ↔ progettista).

Coprono il codice PRG pigro (fn_ensure_progettista_codice, riuso condiviso
con fn_promote_progettista) e la guardia ridefinita di fn_accept_proposal:
una proposta il cui autore è un admin attivo è accettabile.
"""

import psycopg
import pytest

TITOLARE = "a0000000-0000-0000-0000-000000000019"
ADMIN = "b0000000-0000-0000-0000-000000000019"
PROGETTISTA = "c0000000-0000-0000-0000-000000000019"
INESISTENTE = "e0000000-0000-0000-0000-000000000019"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


def make_admin(db, user_id: str, email: str) -> None:
    signup(db, user_id, email)
    db.execute("update public.profiles set role = 'admin' where id = %s", (user_id,))


def make_progettista(db, user_id: str, email: str) -> None:
    signup(db, user_id, email)
    db.execute("select public.fn_promote_progettista(%s, %s)", (user_id, user_id))


def make_company(db, parent: str, piva: str = "01234567890") -> str:
    return db.execute(
        """insert into public.company_profiles (parent_id, ragione_sociale, partita_iva)
           values (%s, 'ACME Srl', %s) returning id""",
        (parent, piva),
    ).fetchone()[0]


def make_request(db, cliente: str, company_id: str, bando_id: int = 1) -> str:
    return db.execute(
        """insert into public.consultation_requests
             (cliente_id, family_parent_id, company_profile_id, bando_id,
              bando_slug, bando_titolo, addon_slug, esito, punteggio)
           values (%s, %s, %s, %s, 'bando-di-prova', 'Bando di prova',
                   'consulto-esperto', 'ammissibile', 82)
           returning id""",
        (cliente, cliente, company_id, bando_id),
    ).fetchone()[0]


def make_proposal(db, request_id: str, autore: str) -> str:
    return db.execute(
        """insert into public.consultation_proposals (request_id, progettista_id, messaggio)
           values (%s, %s, 'Posso aiutarti') returning id""",
        (request_id, autore),
    ).fetchone()[0]


def accept(db, request_id: str, proposal_id: str, cliente: str):
    return db.execute(
        "select public.fn_accept_proposal(%s, %s, %s, null)",
        (request_id, proposal_id, cliente),
    ).fetchone()[0]


def ensure_codice(db, user_id: str) -> str:
    return db.execute(
        "select public.fn_ensure_progettista_codice(%s)", (user_id,)
    ).fetchone()[0]


def role_of(db, user_id: str) -> str:
    return db.execute(
        "select role from public.profiles where id = %s", (user_id,)
    ).fetchone()[0]


def detail_of(excinfo) -> str:
    return excinfo.value.diag.message_detail or ""


class TestEnsureCodice:
    def test_assegna_e_riusa(self, db):
        make_admin(db, ADMIN, "admin19@test.it")
        codice = ensure_codice(db, ADMIN)
        assert codice.startswith("PRG-") and len(codice) == 9
        assert ensure_codice(db, ADMIN) == codice  # idempotente
        righe = db.execute(
            "select count(*) from public.progettisti where user_id = %s", (ADMIN,)
        ).fetchone()[0]
        assert righe == 1

    def test_il_ruolo_non_cambia(self, db):
        make_admin(db, ADMIN, "admin19@test.it")
        ensure_codice(db, ADMIN)
        assert role_of(db, ADMIN) == "admin"

    def test_utente_inesistente(self, db):
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            ensure_codice(db, INESISTENTE)
        assert detail_of(excinfo) == "user_not_found"


class TestCodiceCondiviso:
    def test_promozione_riusa_il_codice_pigro(self, db):
        make_admin(db, ADMIN, "admin19@test.it")
        codice = ensure_codice(db, ADMIN)
        promosso = db.execute(
            "select public.fn_promote_progettista(%s, %s)", (ADMIN, ADMIN)
        ).fetchone()[0]
        assert promosso == codice

    def test_ensure_riusa_il_codice_della_promozione(self, db):
        make_progettista(db, PROGETTISTA, "prog19@test.it")
        codice = db.execute(
            "select codice from public.progettisti where user_id = %s", (PROGETTISTA,)
        ).fetchone()[0]
        # Demozione ad admin: la riga progettisti sopravvive, ensure la riusa.
        db.execute(
            "update public.profiles set role = 'admin' where id = %s", (PROGETTISTA,)
        )
        assert ensure_codice(db, PROGETTISTA) == codice


class TestAcceptProposalAdmin:
    @pytest.fixture()
    def scenario(self, db):
        signup(db, TITOLARE, "titolare19@test.it")
        make_admin(db, ADMIN, "admin19@test.it")
        company_id = make_company(db, TITOLARE)
        request_id = make_request(db, TITOLARE, company_id)
        proposal_id = make_proposal(db, request_id, ADMIN)
        return {"request_id": request_id, "proposal_id": proposal_id}

    def test_proposta_di_un_admin_accettabile(self, db, scenario):
        accept(db, scenario["request_id"], scenario["proposal_id"], TITOLARE)
        stato, assegnato = db.execute(
            """select stato, assigned_progettista_id
               from public.consultation_requests where id = %s""",
            (scenario["request_id"],),
        ).fetchone()
        assert stato == "assegnata"
        assert str(assegnato) == ADMIN

    def test_autore_demolito_a_cliente_rifiutato(self, db, scenario):
        db.execute(
            "update public.profiles set role = 'cliente' where id = %s", (ADMIN,)
        )
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            accept(db, scenario["request_id"], scenario["proposal_id"], TITOLARE)
        assert detail_of(excinfo) == "progettista_not_available"

    def test_admin_sospeso_rifiutato(self, db, scenario):
        db.execute(
            "update public.profiles set is_active = false where id = %s", (ADMIN,)
        )
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            accept(db, scenario["request_id"], scenario["proposal_id"], TITOLARE)
        assert detail_of(excinfo) == "progettista_not_available"


class TestSicurezza0019:
    def test_rpc_non_eseguibili_dai_client(self, db):
        firme = [
            "public.fn_ensure_progettista_codice(uuid)",
            # La ridefinizione della 0019 deve aver conservato le revoche 0017.
            "public.fn_accept_proposal(uuid, uuid, uuid, uuid)",
        ]
        for firma in firme:
            checks = db.execute(
                f"""select
                     has_function_privilege('anon', '{firma}', 'execute'),
                     has_function_privilege('authenticated', '{firma}', 'execute')"""
            ).fetchone()
            assert not any(checks), firma
