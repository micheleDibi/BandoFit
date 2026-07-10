"""Test funzionali della migration 0017 (dominio consulenze).

Coprono i vincoli di concorrenza dichiarati nel piano: doppia prenotazione,
sovrapposizione slot, accettazione vs ritiro, all-or-nothing della RPC.
Ogni test riceve un database fresco clonato dal template.
"""

import psycopg
import pytest

TITOLARE = "a0000000-0000-0000-0000-000000000017"
TITOLARE2 = "b0000000-0000-0000-0000-000000000017"
PROGETTISTA = "c0000000-0000-0000-0000-000000000017"
PROGETTISTA2 = "d0000000-0000-0000-0000-000000000017"


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


def make_proposal(db, request_id: str, progettista: str, messaggio: str = "Posso aiutarti") -> str:
    return db.execute(
        """insert into public.consultation_proposals (request_id, progettista_id, messaggio)
           values (%s, %s, %s) returning id""",
        (request_id, progettista, messaggio),
    ).fetchone()[0]


def make_slot(db, progettista: str, start_min: int = 60, end_min: int = 90) -> str:
    return db.execute(
        """insert into public.availability_slots (progettista_id, inizio, fine)
           values (%s, now() + make_interval(mins => %s), now() + make_interval(mins => %s))
           returning id""",
        (progettista, start_min, end_min),
    ).fetchone()[0]


def accept(db, request_id: str, proposal_id: str, cliente: str, slot_id: str | None = None):
    return db.execute(
        "select public.fn_accept_proposal(%s, %s, %s, %s)",
        (request_id, proposal_id, cliente, slot_id),
    ).fetchone()[0]


def book(db, request_id: str, slot_id: str, actor: str) -> str:
    return db.execute(
        "select public.fn_book_slot(%s, %s, %s)", (request_id, slot_id, actor)
    ).fetchone()[0]


def request_state(db, request_id: str) -> tuple:
    return db.execute(
        """select stato, assigned_progettista_id, accepted_proposal_id
           from public.consultation_requests where id = %s""",
        (request_id,),
    ).fetchone()


def proposal_state(db, proposal_id: str) -> str:
    return db.execute(
        "select stato from public.consultation_proposals where id = %s", (proposal_id,)
    ).fetchone()[0]


def detail_of(excinfo) -> str:
    return excinfo.value.diag.message_detail or ""


@pytest.fixture()
def scenario(db):
    """Titolare con azienda e richiesta aperta, progettista promosso."""
    signup(db, TITOLARE, "titolare17@test.it")
    make_progettista(db, PROGETTISTA, "prog17@test.it")
    company_id = make_company(db, TITOLARE)
    request_id = make_request(db, TITOLARE, company_id)
    return {"company_id": company_id, "request_id": request_id}


class TestSeedAddon:
    def test_addon_presente_e_rieseguibile(self, db):
        slug, nome = db.execute(
            "select slug, nome from public.addons where slug = 'consulto-esperto'"
        ).fetchone()
        assert nome == "Consulto esperto"
        # Ri-esecuzione del seed: nessun doppione (on conflict do nothing).
        db.execute(
            """insert into public.addons (nome, slug, prezzo, tipo_prezzo)
               values ('Consulto esperto', 'consulto-esperto', 0, 'gratis')
               on conflict (slug) do nothing"""
        )
        totale = db.execute(
            "select count(*) from public.addons where slug = 'consulto-esperto'"
        ).fetchone()[0]
        assert totale == 1


class TestSlots:
    def test_sovrapposizione_rifiutata(self, db):
        make_progettista(db, PROGETTISTA, "prog17@test.it")
        make_slot(db, PROGETTISTA, 60, 120)
        with pytest.raises(psycopg.errors.ExclusionViolation):
            make_slot(db, PROGETTISTA, 90, 150)

    def test_slot_adiacenti_validi(self, db):
        """Range [): il confine condiviso non è una sovrapposizione."""
        make_progettista(db, PROGETTISTA, "prog17@test.it")
        make_slot(db, PROGETTISTA, 60, 90)
        make_slot(db, PROGETTISTA, 90, 120)

    def test_progettisti_diversi_si_sovrappongono(self, db):
        make_progettista(db, PROGETTISTA, "prog17@test.it")
        make_progettista(db, PROGETTISTA2, "prog17b@test.it")
        make_slot(db, PROGETTISTA, 60, 120)
        make_slot(db, PROGETTISTA2, 60, 120)

    def test_ordine_orari(self, db):
        make_progettista(db, PROGETTISTA, "prog17@test.it")
        with pytest.raises(psycopg.errors.CheckViolation):
            make_slot(db, PROGETTISTA, 90, 60)

    def test_update_slot_libero(self, db):
        make_progettista(db, PROGETTISTA, "prog17@test.it")
        slot_id = make_slot(db, PROGETTISTA, 60, 90)
        db.execute(
            """select public.fn_update_slot(%s, %s,
                 now() + interval '3 hours', now() + interval '4 hours')""",
            (slot_id, PROGETTISTA),
        )

    def test_update_slot_altrui_negato(self, db):
        make_progettista(db, PROGETTISTA, "prog17@test.it")
        make_progettista(db, PROGETTISTA2, "prog17b@test.it")
        slot_id = make_slot(db, PROGETTISTA, 60, 90)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute(
                """select public.fn_update_slot(%s, %s,
                     now() + interval '3 hours', now() + interval '4 hours')""",
                (slot_id, PROGETTISTA2),
            )
        assert detail_of(excinfo) == "slot_not_found"

    def test_update_con_sovrapposizione(self, db):
        make_progettista(db, PROGETTISTA, "prog17@test.it")
        make_slot(db, PROGETTISTA, 60, 90)
        slot_id = make_slot(db, PROGETTISTA, 120, 150)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute(
                """select public.fn_update_slot(%s, %s,
                     now() + make_interval(mins => 70), now() + make_interval(mins => 100))""",
                (slot_id, PROGETTISTA),
            )
        assert detail_of(excinfo) == "slot_overlap"


class TestAcceptProposal:
    def test_assegnazione_chiude_le_altre_proposte(self, db, scenario):
        make_progettista(db, PROGETTISTA2, "prog17b@test.it")
        vincente = make_proposal(db, scenario["request_id"], PROGETTISTA)
        perdente = make_proposal(db, scenario["request_id"], PROGETTISTA2)
        result = accept(db, scenario["request_id"], vincente, TITOLARE)
        stato, assegnato, accettata = request_state(db, scenario["request_id"])
        assert stato == "assegnata"
        assert str(assegnato) == PROGETTISTA
        assert str(accettata) == str(vincente)
        assert proposal_state(db, vincente) == "accettata"
        assert proposal_state(db, perdente) == "superata"
        assert result["booking_id"] is None
        audit = db.execute(
            "select count(*) from public.audit_log where action = 'consulenza.assigned'"
        ).fetchone()[0]
        assert audit == 1

    def test_solo_il_titolare_accetta(self, db, scenario):
        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        signup(db, TITOLARE2, "altro17@test.it")
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            accept(db, scenario["request_id"], proposta, TITOLARE2)
        assert detail_of(excinfo) == "not_request_owner"

    def test_richiesta_gia_assegnata(self, db, scenario):
        make_progettista(db, PROGETTISTA2, "prog17b@test.it")
        prima = make_proposal(db, scenario["request_id"], PROGETTISTA)
        seconda = make_proposal(db, scenario["request_id"], PROGETTISTA2)
        accept(db, scenario["request_id"], prima, TITOLARE)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            accept(db, scenario["request_id"], seconda, TITOLARE)
        assert detail_of(excinfo) == "request_not_open"

    def test_proposta_ritirata_non_accettabile(self, db, scenario):
        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        db.execute(
            "update public.consultation_proposals set stato = 'ritirata' where id = %s",
            (proposta,),
        )
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            accept(db, scenario["request_id"], proposta, TITOLARE)
        assert detail_of(excinfo) == "proposal_not_open"

    def test_accettazione_attende_il_lock_di_un_ritiro_in_corso(self, db, scenario):
        """La RPC prende FOR UPDATE sulla proposta: con un ritiro non ancora
        committato deve METTERSI IN CODA (lock), non sovrascriverlo."""
        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        ritiro = psycopg.connect(db.info.dsn)  # transazione implicita aperta
        try:
            ritiro.execute(
                "update public.consultation_proposals set stato = 'ritirata' where id = %s",
                (proposta,),
            )
            db.execute("set lock_timeout = '500ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                accept(db, scenario["request_id"], proposta, TITOLARE)
        finally:
            db.execute("set lock_timeout = 0")
            ritiro.rollback()
            ritiro.close()

    def test_progettista_demoted_non_assegnabile(self, db, scenario):
        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        db.execute(
            "update public.profiles set role = 'cliente' where id = %s", (PROGETTISTA,)
        )
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            accept(db, scenario["request_id"], proposta, TITOLARE)
        assert detail_of(excinfo) == "progettista_not_available"

    def test_accettazione_con_slot_prenota(self, db, scenario):
        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        slot_id = make_slot(db, PROGETTISTA)
        result = accept(db, scenario["request_id"], proposta, TITOLARE, slot_id)
        assert result["booking_id"] is not None
        stato, inizio = db.execute(
            "select stato, inizio from public.consultation_bookings where id = %s",
            (result["booking_id"],),
        ).fetchone()
        assert stato == "confermata"
        assert inizio is not None
        audit = db.execute(
            "select count(*) from public.audit_log where action = 'consulenza.booked'"
        ).fetchone()[0]
        assert audit == 1

    def test_slot_occupato_annulla_tutto(self, db, scenario):
        """All-or-nothing: se lo slot è preso non resta NEMMENO l'assegnazione."""
        # Un secondo titolare si è già preso lo slot del progettista.
        signup(db, TITOLARE2, "altro17@test.it")
        company2 = make_company(db, TITOLARE2, piva="09876543210")
        request2 = make_request(db, TITOLARE2, company2, bando_id=2)
        proposta2 = make_proposal(db, request2, PROGETTISTA)
        slot_id = make_slot(db, PROGETTISTA)
        accept(db, request2, proposta2, TITOLARE2, slot_id)

        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            accept(db, scenario["request_id"], proposta, TITOLARE, slot_id)
        assert detail_of(excinfo) == "slot_taken"
        stato, assegnato, _ = request_state(db, scenario["request_id"])
        assert stato == "nuova"
        assert assegnato is None
        assert proposal_state(db, proposta) == "inviata"


class TestBookSlot:
    @pytest.fixture()
    def assegnata(self, db, scenario):
        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        accept(db, scenario["request_id"], proposta, TITOLARE)
        return scenario["request_id"]

    def test_prenotazione_e_doppia_prenotazione(self, db, assegnata):
        signup(db, TITOLARE2, "altro17@test.it")
        company2 = make_company(db, TITOLARE2, piva="09876543210")
        request2 = make_request(db, TITOLARE2, company2, bando_id=2)
        proposta2 = make_proposal(db, request2, PROGETTISTA)
        accept(db, request2, proposta2, TITOLARE2)

        slot_id = make_slot(db, PROGETTISTA)
        book(db, assegnata, slot_id, TITOLARE)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            book(db, request2, slot_id, TITOLARE2)
        assert detail_of(excinfo) == "slot_taken"

    def test_richiesta_non_assegnata(self, db, scenario):
        slot_id = make_slot(db, PROGETTISTA)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            book(db, scenario["request_id"], slot_id, TITOLARE)
        assert detail_of(excinfo) == "request_not_assigned"

    def test_slot_di_un_altro_progettista(self, db, assegnata):
        make_progettista(db, PROGETTISTA2, "prog17b@test.it")
        slot_altrui = make_slot(db, PROGETTISTA2)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            book(db, assegnata, slot_altrui, TITOLARE)
        assert detail_of(excinfo) == "slot_wrong_progettista"

    def test_slot_passato(self, db, assegnata):
        slot_id = make_slot(db, PROGETTISTA, -90, -60)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            book(db, assegnata, slot_id, TITOLARE)
        assert detail_of(excinfo) == "slot_in_past"

    def test_un_solo_appuntamento_per_consulenza(self, db, assegnata):
        primo = make_slot(db, PROGETTISTA, 60, 90)
        secondo = make_slot(db, PROGETTISTA, 120, 150)
        book(db, assegnata, primo, TITOLARE)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            book(db, assegnata, secondo, TITOLARE)
        assert detail_of(excinfo) == "booking_already_exists"

    def test_annullamento_libera_lo_slot(self, db, assegnata):
        slot_id = make_slot(db, PROGETTISTA)
        booking_id = book(db, assegnata, slot_id, TITOLARE)
        db.execute(
            "update public.consultation_bookings set stato = 'annullata' where id = %s",
            (booking_id,),
        )
        nuovo = book(db, assegnata, slot_id, TITOLARE)
        assert nuovo != booking_id

    def test_slot_prenotato_non_modificabile_ne_eliminabile(self, db, assegnata):
        slot_id = make_slot(db, PROGETTISTA)
        book(db, assegnata, slot_id, TITOLARE)
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute(
                """select public.fn_update_slot(%s, %s,
                     now() + interval '5 hours', now() + interval '6 hours')""",
                (slot_id, PROGETTISTA),
            )
        assert detail_of(excinfo) == "slot_booked"
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute("select public.fn_delete_slot(%s, %s)", (slot_id, PROGETTISTA))
        assert detail_of(excinfo) == "slot_booked"

    def test_lo_storico_annullato_non_blocca_la_cancellazione(self, db, assegnata):
        """FK set null + snapshot: lo slot si elimina, l'appuntamento annullato
        conserva i suoi orari."""
        slot_id = make_slot(db, PROGETTISTA)
        booking_id = book(db, assegnata, slot_id, TITOLARE)
        db.execute(
            "update public.consultation_bookings set stato = 'annullata' where id = %s",
            (booking_id,),
        )
        db.execute("select public.fn_delete_slot(%s, %s)", (slot_id, PROGETTISTA))
        slot_ref, inizio = db.execute(
            "select slot_id, inizio from public.consultation_bookings where id = %s",
            (booking_id,),
        ).fetchone()
        assert slot_ref is None
        assert inizio is not None


class TestVincoliRichieste:
    def test_una_sola_richiesta_aperta_per_bando(self, db, scenario):
        with pytest.raises(psycopg.errors.UniqueViolation):
            make_request(db, TITOLARE, scenario["company_id"], bando_id=1)

    def test_richiesta_assegnata_non_blocca_la_successiva(self, db, scenario):
        proposta = make_proposal(db, scenario["request_id"], PROGETTISTA)
        accept(db, scenario["request_id"], proposta, TITOLARE)
        make_request(db, TITOLARE, scenario["company_id"], bando_id=1)

    def test_assegnata_a_mano_incoerente_rifiutata(self, db, scenario):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.consultation_requests set stato = 'assegnata' where id = %s",
                (scenario["request_id"],),
            )

    def test_riproposta_dopo_ritiro_consentita(self, db, scenario):
        prima = make_proposal(db, scenario["request_id"], PROGETTISTA)
        with pytest.raises(psycopg.errors.UniqueViolation):
            make_proposal(db, scenario["request_id"], PROGETTISTA)
        db.execute(
            "update public.consultation_proposals set stato = 'ritirata' where id = %s",
            (prima,),
        )
        make_proposal(db, scenario["request_id"], PROGETTISTA, "Riprovo con più dettagli")

    def test_cascade_dallazienda(self, db, scenario):
        """Right to erasure: cancellato il titolare, spariscono azienda,
        richieste e proposte."""
        make_proposal(db, scenario["request_id"], PROGETTISTA)
        db.execute("delete from auth.users where id = %s", (TITOLARE,))
        richieste = db.execute(
            "select count(*) from public.consultation_requests"
        ).fetchone()[0]
        proposte = db.execute(
            "select count(*) from public.consultation_proposals"
        ).fetchone()[0]
        assert richieste == 0
        assert proposte == 0


class TestSicurezza0017:
    TABELLE = [
        "availability_slots",
        "consultation_requests",
        "consultation_proposals",
        "consultation_bookings",
    ]

    def test_privilegi_revocati(self, db):
        for tabella in self.TABELLE:
            checks = db.execute(
                f"""select
                     has_table_privilege('anon', 'public.{tabella}', 'select'),
                     has_table_privilege('authenticated', 'public.{tabella}', 'select'),
                     has_table_privilege('authenticated', 'public.{tabella}', 'insert')"""
            ).fetchone()
            assert not any(checks), tabella

    def test_rls_abilitata(self, db):
        for tabella in self.TABELLE:
            attiva = db.execute(
                "select relrowsecurity from pg_class where relname = %s", (tabella,)
            ).fetchone()[0]
            assert attiva is True, tabella

    def test_rpc_non_eseguibili_dai_client(self, db):
        firme = [
            "public.fn_book_slot(uuid, uuid, uuid)",
            "public.fn_accept_proposal(uuid, uuid, uuid, uuid)",
            "public.fn_update_slot(uuid, uuid, timestamptz, timestamptz)",
            "public.fn_delete_slot(uuid, uuid)",
        ]
        for firma in firme:
            checks = db.execute(
                f"""select
                     has_function_privilege('anon', '{firma}', 'execute'),
                     has_function_privilege('authenticated', '{firma}', 'execute')"""
            ).fetchone()
            assert not any(checks), firma
