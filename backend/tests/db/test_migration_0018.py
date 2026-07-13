"""Test funzionali della migration 0018 (serie di slot ricorrenti).

Coprono il contratto delle due RPC: le occorrenze sovrapposte si SALTANO
(non abortiscono la serie), l'eliminazione della serie non tocca mai gli
slot prenotati. Ogni test riceve un database fresco clonato dal template.
"""

import json
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

TITOLARE = "a0000000-0000-0000-0000-000000000018"
PROGETTISTA = "c0000000-0000-0000-0000-000000000018"
PROGETTISTA2 = "d0000000-0000-0000-0000-000000000018"


def signup(db, user_id: str, email: str) -> None:
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))


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


def make_proposal(db, request_id: str, progettista: str) -> str:
    return db.execute(
        """insert into public.consultation_proposals (request_id, progettista_id, messaggio)
           values (%s, %s, 'Posso aiutarti') returning id""",
        (request_id, progettista),
    ).fetchone()[0]


def make_slot(db, progettista: str, start_min: int = 60, end_min: int = 90) -> str:
    return db.execute(
        """insert into public.availability_slots (progettista_id, inizio, fine)
           values (%s, now() + make_interval(mins => %s), now() + make_interval(mins => %s))
           returning id""",
        (progettista, start_min, end_min),
    ).fetchone()[0]


def accept(db, request_id: str, proposal_id: str, cliente: str):
    return db.execute(
        "select public.fn_accept_proposal(%s, %s, %s, null)",
        (request_id, proposal_id, cliente),
    ).fetchone()[0]


def book(db, request_id: str, slot_id: str, actor: str) -> str:
    return db.execute(
        "select public.fn_book_slot(%s, %s, %s)", (request_id, slot_id, actor)
    ).fetchone()[0]


def occ(start_min: int, end_min: int) -> dict:
    """Occorrenza come la manda il backend: ISO con offset (UTC)."""
    base = datetime.now(timezone.utc)
    return {
        "inizio": (base + timedelta(minutes=start_min)).isoformat(),
        "fine": (base + timedelta(minutes=end_min)).isoformat(),
    }


def crea_serie(db, progettista: str, occorrenze) -> dict:
    return db.execute(
        "select public.fn_create_slot_serie(%s, %s::jsonb)",
        (progettista, json.dumps(occorrenze)),
    ).fetchone()[0]


def elimina_serie(db, serie_id: str, progettista: str) -> dict:
    return db.execute(
        "select public.fn_delete_slot_serie(%s, %s)", (serie_id, progettista)
    ).fetchone()[0]


def slot_count(db) -> int:
    return db.execute("select count(*) from public.availability_slots").fetchone()[0]


def detail_of(excinfo) -> str:
    return excinfo.value.diag.message_detail or ""


class TestColonnaSerie:
    def test_serie_id_null_di_default(self, db):
        """Il flusso dello slot singolo (insert diretto) resta invariato."""
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        slot_id = make_slot(db, PROGETTISTA)
        serie_id = db.execute(
            "select serie_id from public.availability_slots where id = %s", (slot_id,)
        ).fetchone()[0]
        assert serie_id is None


class TestCreateSlotSerie:
    def test_crea_tutte_le_occorrenze(self, db):
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        result = crea_serie(db, PROGETTISTA, [occ(60, 90), occ(120, 150), occ(180, 210)])
        assert result["saltati"] == 0
        assert len(result["creati"]) == 3
        serie_ids = {row["serie_id"] for row in result["creati"]}
        assert serie_ids == {result["serie_id"]}
        a_db = db.execute(
            "select count(*) from public.availability_slots where serie_id = %s",
            (result["serie_id"],),
        ).fetchone()[0]
        assert a_db == 3

    def test_salta_le_sovrapposte(self, db):
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        make_slot(db, PROGETTISTA, 60, 90)  # pre-esistente
        result = crea_serie(
            db, PROGETTISTA, [occ(10, 40), occ(70, 100), occ(120, 150)]
        )
        assert result["saltati"] == 1
        assert len(result["creati"]) == 2
        assert slot_count(db) == 3  # pre-esistente intatto + 2 nuove

    def test_occorrenze_interne_sovrapposte(self, db):
        """La prima occorrenza del payload entra, la seconda che la copre
        viene saltata dallo stesso meccanismo (nessuna gestione speciale)."""
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        result = crea_serie(db, PROGETTISTA, [occ(60, 120), occ(90, 150)])
        assert result["saltati"] == 1
        assert len(result["creati"]) == 1

    def test_tutte_sovrapposte(self, db):
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        make_slot(db, PROGETTISTA, 60, 180)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            crea_serie(db, PROGETTISTA, [occ(60, 90), occ(120, 150)])
        assert detail_of(excinfo) == "serie_tutta_sovrapposta"
        assert slot_count(db) == 1  # rollback totale: resta solo il pre-esistente

    def test_progettisti_diversi_non_confliggono(self, db):
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        make_progettista(db, PROGETTISTA2, "prog18b@test.it")
        make_slot(db, PROGETTISTA, 60, 90)
        result = crea_serie(db, PROGETTISTA2, [occ(60, 90)])
        assert result["saltati"] == 0

    def test_serie_vuota(self, db):
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            crea_serie(db, PROGETTISTA, [])
        assert detail_of(excinfo) == "serie_vuota"
        # Anche un non-array è una serie vuota, non un errore grezzo.
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            crea_serie(db, PROGETTISTA, {})
        assert detail_of(excinfo) == "serie_vuota"

    def test_troppe_occorrenze(self, db):
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        troppe = [occ(60 + i * 60, 90 + i * 60) for i in range(371)]
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            crea_serie(db, PROGETTISTA, troppe)
        assert detail_of(excinfo) == "serie_troppo_lunga"
        assert slot_count(db) == 0


class TestDeleteSlotSerie:
    @pytest.fixture()
    def scenario(self, db):
        """Serie di 3 slot; il secondo è prenotato da una consulenza assegnata."""
        signup(db, TITOLARE, "titolare18@test.it")
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        company_id = make_company(db, TITOLARE)
        request_id = make_request(db, TITOLARE, company_id)
        proposta = make_proposal(db, request_id, PROGETTISTA)
        accept(db, request_id, proposta, TITOLARE)
        serie = crea_serie(db, PROGETTISTA, [occ(60, 90), occ(120, 150), occ(180, 210)])
        prenotato = serie["creati"][1]["id"]
        book(db, request_id, prenotato, TITOLARE)
        return {"serie_id": serie["serie_id"], "prenotato": prenotato}

    def test_elimina_liberi_mantiene_prenotati(self, db, scenario):
        result = elimina_serie(db, scenario["serie_id"], PROGETTISTA)
        assert result == {"eliminati": 2, "mantenuti": 1}
        superstiti = db.execute(
            "select id from public.availability_slots where serie_id = %s",
            (scenario["serie_id"],),
        ).fetchall()
        assert [str(row[0]) for row in superstiti] == [scenario["prenotato"]]

    def test_serie_inesistente(self, db):
        make_progettista(db, PROGETTISTA, "prog18@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            elimina_serie(db, "e0000000-0000-0000-0000-000000000018", PROGETTISTA)
        assert detail_of(excinfo) == "serie_not_found"

    def test_serie_di_altro_progettista(self, db, scenario):
        make_progettista(db, PROGETTISTA2, "prog18b@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            elimina_serie(db, scenario["serie_id"], PROGETTISTA2)
        assert detail_of(excinfo) == "serie_not_found"
        assert slot_count(db) == 3  # tutto intatto

    def test_tutti_prenotati(self, db, scenario):
        """Serie ridotta al solo slot prenotato: successo con contatori,
        non un errore (l'operazione è comunque andata a buon fine)."""
        elimina_serie(db, scenario["serie_id"], PROGETTISTA)
        result = elimina_serie(db, scenario["serie_id"], PROGETTISTA)
        assert result == {"eliminati": 0, "mantenuti": 1}


class TestSicurezza0018:
    def test_rpc_non_eseguibili_dai_client(self, db):
        firme = [
            "public.fn_create_slot_serie(uuid, jsonb)",
            "public.fn_delete_slot_serie(uuid, uuid)",
        ]
        for firma in firme:
            checks = db.execute(
                f"""select
                     has_function_privilege('anon', '{firma}', 'execute'),
                     has_function_privilege('authenticated', '{firma}', 'execute')"""
            ).fetchone()
            assert not any(checks), firma
