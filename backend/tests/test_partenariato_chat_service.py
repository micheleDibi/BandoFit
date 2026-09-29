"""Chat in-app dei partenariati (WP7, docs/partenariati.md K3-K4, Q13, Q14,
Q21): servizio vero sul primario finto del WP7 (`FakePrimaryWP7`), dopo
l'accettazione della candidatura di Y alla call di X (esempio guida).

Verifica: idempotenza per `client_msg_id`, messaggi oscurati, cursori
(`dopo`, `prima`), avvisi «una per raffica» (in-app ed email) solo agli
utenti dell'ALTRA azienda e senza il testo, letture per utente (anche i
membri), membro in sola lettura, chiusura solo del creatore, sola lettura se
l'altra azienda non è più attiva, 404 per chiunque non sia parte (anche la
seconda azienda di un Advisor), identità solo con la rivelazione accesa,
segnalazione dei messaggi dell'altra azienda, riepilogo dei non letti."""

import copy
import uuid

import pytest

from app.core.errors import AppError, BadRequestError, ForbiddenError, NotFoundError
from app.schemas.partner_call import SegnalazioneIn
from app.services import partenariato_candidature_service as svc
from app.services import partenariato_chat_service as chat
from app.services import partner_call_service as pcs
from app.services import partner_profile_service as pps
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    COMPANY_Y2,
    EMAIL_MEMBRO_X,
    MEMBRO_X,
    attiva,
    candida,
    fixture_fondo,
    pseudo,
    scenario_wp7,
    utente,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    T0,
    ambiente_wp6,
)

TESTO_Y = "Ciao, sono Laura del laboratorio: il mio numero è 333 1234567."


async def conversazione(fondo, *, rivela: bool = False, monkeypatch=None):
    """Y si candida, X accetta: la conversazione aperta (email già smaltite)."""
    if rivela:
        monkeypatch.setattr(pps, "RIVELAZIONE_IDENTITA_DISPONIBILE", True)
    db, sec = await scenario_wp7()
    await candida(db, sec)
    [riga] = db.tabelle["partner_candidature"]
    await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
    await fondo.azzera()
    db.tabelle["notifications"] = []
    [conv] = db.tabelle["partner_conversazioni"]
    return db, sec, conv["id"]


def messaggio(testo: str = TESTO_Y, client=None) -> chat.MessaggioIn:
    return chat.MessaggioIn(testo=testo, client_msg_id=client or uuid.uuid4())


async def scrive(db, sec, nome: str, conv: str, testo: str = TESTO_Y, client=None):
    return await chat.invia(db, sec, attiva(nome), utente(nome), conv, messaggio(testo, client))


class TestMessaggi:
    async def test_invio_idempotente(self, fondo):
        db, sec, conv = await conversazione(fondo)
        chiave = uuid.uuid4()
        primo = await scrive(db, sec, "Y", conv, client=chiave)
        secondo = await scrive(db, sec, "Y", conv, client=chiave)
        assert primo.id == secondo.id and primo.propria is True
        assert primo.client_msg_id == chiave
        assert len(db.tabelle["partner_messaggi"]) == 1
        await fondo.esegui()
        # un solo avviso: il duplicato non rinotifica
        assert len(db.righe("notifications", tipo=chat.TIPO_NUOVI_MESSAGGI)) == 2
        # nessun filtro anti-contatti in chat: i contatti si scambiano qui
        assert db.tabelle["partner_messaggi"][0]["testo"] == TESTO_Y

    async def test_testo_vuoto_o_troppo_lungo(self):
        for testo in ("   ", "​​", "x" * 5001):
            with pytest.raises(BadRequestError):
                messaggio(testo)

    async def test_letture_con_cursori_e_oscurati(self, fondo):
        db, sec, conv = await conversazione(fondo)
        for i in range(5):
            await scrive(db, sec, "Y" if i % 2 == 0 else "X", conv, f"Messaggio numero {i}")
        db.tabelle["partner_messaggi"][1]["nascosto_moderazione_at"] = T0
        ultimi = await chat.messaggi(db, sec, attiva("X"), utente("X"), conv, limite=2)
        assert [m.testo for m in ultimi.items] == ["Messaggio numero 3", "Messaggio numero 4"]
        assert ultimi.ha_altri is True
        prima = await chat.messaggi(db, sec, attiva("X"), utente("X"), conv,
                                    prima=ultimi.items[0].id, limite=10)
        assert [m.testo for m in prima.items] == ["Messaggio numero 0", None, "Messaggio numero 2"]
        assert prima.items[1].nascosto is True and prima.ha_altri is False
        dopo = await chat.messaggi(db, sec, attiva("Y"), utente("Y"), conv, dopo=prima.items[0].id)
        assert [m.id for m in dopo.items] == [m.id for m in [*prima.items[1:], *ultimi.items]]
        # per Y i propri e quelli dell'altra azienda, mai chi li ha scritti
        assert [m.propria for m in dopo.items] == [False, True, False, True]
        assert all(m.client_msg_id is None for m in dopo.items if not m.propria)
        corpo = dopo.model_dump_json()
        assert g.OWNER["X"] not in corpo and g.COMPANY["X"] not in corpo

    async def test_avvisi_una_per_raffica_solo_all_altra_azienda(self, fondo):
        db, sec, conv = await conversazione(fondo)
        await scrive(db, sec, "Y", conv)
        await fondo.esegui()
        notifiche = db.righe("notifications", tipo=chat.TIPO_NUOVI_MESSAGGI)
        assert {n["user_id"] for n in notifiche} == {g.OWNER["X"], MEMBRO_X}
        assert all(n["url"] == f"/app/partenariati/conversazioni/{conv}?azienda={g.COMPANY['X']}"
                   for n in notifiche)
        assert {e["to"] for e in fondo.email} == {EMAIL["X"], EMAIL_MEMBRO_X}
        for email in fondo.email:
            assert "333" not in email["text"] and "Laura" not in email["text"]
            assert "333" not in email["html"]
            assert pseudo("Y") in email["text"]  # al creatore: lo pseudonimo della candidata
            assert RAGIONE["Y"] not in email["text"] and PIVA["Y"] not in email["text"]
        # seconda raffica senza lettura: nessun nuovo avviso
        await fondo.azzera()
        await scrive(db, sec, "Y", conv, "Un secondo messaggio")
        await fondo.esegui()
        assert fondo.email == []
        assert len(db.righe("notifications", tipo=chat.TIPO_NUOVI_MESSAGGI)) == 2
        # il titolare di X legge: al messaggio dopo lui (solo lui) è di nuovo avvisato
        await chat.segna_letto(db, sec, attiva("X"), utente("X"), conv, chat.LettoIn())
        await scrive(db, sec, "Y", conv, "Un terzo messaggio")
        await fondo.esegui()
        assert [e["to"] for e in fondo.email] == [EMAIL["X"]]

    async def test_avvisi_verso_la_candidata_senza_pseudonimo_ne_testo(self, fondo):
        db, sec, conv = await conversazione(fondo)
        await scrive(db, sec, "X", conv, "Buongiorno, siamo pronti a partire.")
        await fondo.esegui()
        [email] = fondo.email
        assert email["to"] == EMAIL["Y"] and "pronti" not in email["text"]
        assert pseudo("Y") not in email["text"]
        assert RAGIONE["X"] not in email["text"] and PIVA["X"] not in email["text"]
        [notifica] = db.righe("notifications", tipo=chat.TIPO_NUOVI_MESSAGGI)
        assert notifica["user_id"] == g.OWNER["Y"]


class TestAccessi:
    async def test_membro_legge_e_segna_letto_ma_non_scrive(self, fondo):
        db, sec, conv = await conversazione(fondo)
        await scrive(db, sec, "Y", conv)
        membro = attiva("X", editable=False)
        with pytest.raises(ForbiddenError):
            await chat.invia(db, sec, membro, {"id": MEMBRO_X}, conv, messaggio())
        dettaglio = await chat.dettaglio(db, sec, membro, {"id": MEMBRO_X}, conv)
        assert dettaglio.sola_lettura is True and dettaglio.puo_scrivere is False
        assert dettaglio.non_letti == 1
        letto = await chat.segna_letto(db, sec, membro, {"id": MEMBRO_X}, conv, chat.LettoIn())
        assert letto == db.tabelle["partner_messaggi"][0]["id"]
        dopo = await chat.dettaglio(db, sec, membro, {"id": MEMBRO_X}, conv)
        assert dopo.non_letti == 0 and dopo.letto_fino_a_id == letto
        # il titolare ha ancora il suo non letto (letture per utente)
        assert (await chat.dettaglio(db, sec, attiva("X"), utente("X"), conv)).non_letti == 1

    async def test_estranei_e_seconda_azienda_dell_advisor_404(self, fondo):
        db, sec, conv = await conversazione(fondo)
        riga = copy.deepcopy(db.una("company_profiles", id=g.COMPANY["Y"]))
        riga.update(id=COMPANY_Y2, partita_iva="30000000001")
        db.tabelle["company_profiles"].append(riga)
        for active, user in ((attiva("O"), utente("O")),
                             (attiva("Y", company=COMPANY_Y2), utente("Y"))):
            with pytest.raises(NotFoundError):
                await chat.dettaglio(db, sec, active, user, conv)
            with pytest.raises(NotFoundError):
                await chat.messaggi(db, sec, active, user, conv)
            with pytest.raises(NotFoundError):
                await chat.invia(db, sec, active, user, conv, messaggio())
            with pytest.raises(NotFoundError):
                await chat.segna_letto(db, sec, active, user, conv, chat.LettoIn())
            assert (await chat.lista_conversazioni(db, sec, active, user)).items == []
        with pytest.raises(NotFoundError):
            await chat.dettaglio(db, sec, attiva("X"), utente("X"), "non-un-id")

    async def test_chiusura_solo_del_creatore(self, fondo):
        db, sec, conv = await conversazione(fondo)
        with pytest.raises(NotFoundError):
            await chat.chiudi(db, sec, attiva("Y"), utente("Y"), conv)
        with pytest.raises(ForbiddenError):
            await chat.chiudi(db, sec, attiva("X", editable=False), {"id": MEMBRO_X}, conv)
        chiusa = await chat.chiudi(db, sec, attiva("X"), utente("X"), conv)
        assert chiusa.stato == "chiusa" and chiusa.sola_lettura is True
        assert chiusa.puo_chiudere is False
        with pytest.raises(AppError) as exc:
            await scrive(db, sec, "Y", conv)
        assert exc.value.code == "conversazione_chiusa"
        with pytest.raises(AppError) as exc:
            await chat.chiudi(db, sec, attiva("X"), utente("X"), conv)
        assert exc.value.code == "conversazione_chiusa"
        # lo storico resta leggibile
        assert (await chat.messaggi(db, sec, attiva("Y"), utente("Y"), conv)).items == []

    async def test_controparte_non_piu_attiva_sola_lettura(self, fondo):
        db, sec, conv = await conversazione(fondo)
        db.una("company_profiles", id=g.COMPANY["Y"])["archived_at"] = T0
        dettaglio = await chat.dettaglio(db, sec, attiva("X"), utente("X"), conv)
        assert dettaglio.controparte.attiva is False and dettaglio.sola_lettura is True
        with pytest.raises(AppError) as exc:
            await scrive(db, sec, "X", conv)
        assert exc.value.code == "controparte_non_disponibile"

    async def test_owner_della_controparte_disattivato_sola_lettura_anche_nella_rpc(
            self, fondo):
        """Sola lettura e RPC con la stessa idea di azienda viva: con il titolare
        dell'altra azienda disattivato non si scrive e non parte nessun avviso."""
        db, sec, conv = await conversazione(fondo)
        db.una("profiles", id=g.OWNER["Y"])["is_active"] = False
        dettaglio = await chat.dettaglio(db, sec, attiva("X"), utente("X"), conv)
        assert dettaglio.sola_lettura is True and dettaglio.puo_scrivere is False
        with pytest.raises(AppError) as exc:
            await scrive(db, sec, "X", conv)
        assert (exc.value.status_code, exc.value.code) == (409, "controparte_non_disponibile")
        await fondo.esegui()
        assert db.tabelle.get("partner_messaggi", []) == []
        assert db.righe("notifications", tipo=chat.TIPO_NUOVI_MESSAGGI) == []

    async def test_lista_e_dettaglio_senza_identita(self, fondo):
        db, sec, conv = await conversazione(fondo)
        await scrive(db, sec, "Y", conv)
        [x] = (await chat.lista_conversazioni(db, sec, attiva("X"), utente("X"))).items
        [y] = (await chat.lista_conversazioni(db, sec, attiva("Y"), utente("Y"))).items
        assert (x.lato, x.controparte.pseudonimo, x.non_letti) == ("creatore", pseudo("Y"), 1)
        assert (y.lato, y.controparte.pseudonimo, y.non_letti) == ("partner", None, 0)
        dettaglio = await chat.dettaglio(db, sec, attiva("X"), utente("X"), conv)
        assert dettaglio.identita is None and dettaglio.identita_rivelata is False
        assert dettaglio.puo_chiudere is True and dettaglio.puo_scrivere is True
        testo = x.model_dump_json() + dettaglio.model_dump_json()
        for valore in (g.COMPANY["Y"], g.OWNER["Y"], PIVA["Y"], RAGIONE["Y"],
                       RAGIONE["Y"].upper(), EMAIL["Y"], g.CODICE_PUBBLICO["Y"]):
            assert valore not in testo
        testo_y = y.model_dump_json()
        for valore in (g.COMPANY["X"], g.OWNER["X"], PIVA["X"], RAGIONE["X"].upper()):
            assert valore not in testo_y

    async def test_rivelazione_accesa_identita_nel_dettaglio(self, fondo, monkeypatch):
        db, sec, conv = await conversazione(fondo, rivela=True, monkeypatch=monkeypatch)
        per_x = await chat.dettaglio(db, sec, attiva("X"), utente("X"), conv)
        per_y = await chat.dettaglio(db, sec, attiva("Y"), utente("Y"), conv)
        assert per_x.identita_rivelata is True
        assert per_x.identita.ragione_sociale == RAGIONE["Y"].upper()
        assert per_y.identita.ragione_sociale == RAGIONE["X"].upper()
        assert per_y.identita.referente_ruolo == "titolare"
        for vista, nome in ((per_x, "Y"), (per_y, "X")):
            assert EMAIL[nome] not in vista.model_dump_json()  # mai l'email del referente

    async def test_riepilogo_dei_non_letti(self, fondo):
        db, sec, conv = await conversazione(fondo)
        await scrive(db, sec, "Y", conv)
        await scrive(db, sec, "Y", conv, "Secondo")
        x = await pcs.riepilogo_partenariati(db, sec, attiva("X"), utente("X"))
        y = await pcs.riepilogo_partenariati(db, sec, attiva("Y"), utente("Y"))
        assert (x.messaggi_non_letti, y.messaggi_non_letti) == (2, 0)


class TestSegnalazioneMessaggi:
    def test_schema(self):
        base = {"oggetto_tipo": "messaggio", "motivo": "contenuto_illecito",
                "descrizione": "Il messaggio contiene insulti", "buona_fede": True}
        assert SegnalazioneIn(**base, oggetto_id=" 12 ").oggetto_id == "12"
        for valore in ("abc", "0", "-3", str(uuid.uuid4()), "1" * 19):
            with pytest.raises(BadRequestError):
                SegnalazioneIn(**base, oggetto_id=valore)

    async def test_solo_i_messaggi_dell_altra_azienda_dalle_parti(self, fondo, monkeypatch):
        db, sec, conv = await conversazione(fondo)
        da_y = await scrive(db, sec, "Y", conv)
        da_x = await scrive(db, sec, "X", conv, "Nostro messaggio")
        db.tabelle.setdefault("partner_segnalazioni", [])

        def dati(mid):
            return SegnalazioneIn(oggetto_tipo="messaggio", oggetto_id=str(mid),
                                  motivo="contenuto_illecito",
                                  descrizione="Il messaggio contiene insulti", buona_fede=True)

        async def rate(*_a, **_k):
            return True

        monkeypatch.setattr(pcs.rate_limit_service, "allow", rate)
        out = await pcs.segnala(db, sec, attiva("X"), utente("X"), dati(da_y.id))
        [riga] = db.tabelle["partner_segnalazioni"]
        assert (riga["oggetto_tipo"], riga["oggetto_id"]) == ("messaggio", str(da_y.id))
        assert riga["contenuto_snapshot"]["testo"] == TESTO_Y
        assert g.OWNER["Y"] not in str(riga["contenuto_snapshot"])
        assert str(out.id) == riga["id"]
        for active, user, mid in ((attiva("X"), utente("X"), da_x.id),  # il proprio
                                  (attiva("O"), utente("O"), da_y.id),  # un estraneo
                                  (attiva("X"), utente("X"), 999)):  # inesistente
            with pytest.raises(NotFoundError):
                await pcs.segnala(db, sec, active, user, dati(mid))
