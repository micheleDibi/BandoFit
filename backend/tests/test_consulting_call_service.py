"""Consulto chiesto dalla call di partenariato (WP9, docs/partenariati.md W1,
Q19, Q23): servizio vero sul primario finto del WP8 (esempio guida) esteso
con `fn_create_consultation_request` della 0041 (stesse guardie e detail:
call dell'azienda e dell'owner del payload, un consulto aperto per owner ×
bando senza call e uno per call, consumo dell'addon).

Verifica:
- creazione dalla call: solo il titolare dell'azienda creatrice, AI-check
  facoltativo (snapshot di esito e punteggio se c'è), consumo, audit con la
  call, notifica ai progettisti, coesistenza con un consulto AI-check sullo
  stesso bando, un solo consulto aperto per call, call annullata o sospesa,
  bando ritirato (410), credito esaurito, RPC con `call_non_trovata`;
- proiezione per il progettista ASSEGNATO con canary (nessun contatto,
  messaggio, candidatura, valore esatto o id interno di altre aziende),
  audit fail-closed (502 senza dati né letture della call), non assegnato →
  404, richiesta non da call → 404, call di un'altra azienda → 404;
- pool dei progettisti: la richiesta da call è anonima per i non assegnati
  (anche il report dell'AI-check), con i dati parziali per l'assegnato.
Le guardie vere della RPC le verifica `tests/db/test_migration_0041.py`."""

import copy
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.errors import AppError, BandoRitiratoError, NotFoundError
from app.schemas.partenariato_consorzio import MembroAggiornaIn
from app.services import consulting_service
from app.services import partenariato_candidature_service as candidature
from app.services import partenariato_chat_service as chat
from app.services import partenariato_collegamenti
from app.services import partenariato_consorzio_service as consorzio
from app.services.ai_check_service import CHECK_SELECT
from app.services.partenariato_accesso import pseudonimo
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    FM_X,
    MEMBRO_X,
    MESSAGGIO_Y,
    attiva,
    candidatura_in,
    errore,
    fixture_fondo,
    utente,
)
from tests.test_partenariato_consorzio_service import (
    NUMERI_Y,
    FakePrimaryWP8,
    imposta_regole,
    regole,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    ambiente_wp6,
    carica_guida,
    secondario_guida,
)

PROGETTISTA = "f0000000-0000-4000-8000-0000000000a1"
ALTRO_PROGETTISTA = "f0000000-0000-4000-8000-0000000000a2"
PROG_USER = {"id": PROGETTISTA, "role": "progettista", "nome": "Paola", "cognome": "Ferri",
             "is_active": True}
ALTRO_PROG_USER = {"id": ALTRO_PROGETTISTA, "role": "progettista", "is_active": True}
ADDON_ID = 7
COMPANY_X2 = "c0000000-0000-4000-8000-0000000000b2"  # seconda azienda dell'owner di X
TESTO_CHAT = "CANARY-MESSAGGIO scrivimi a laura@laboratorio.example.test"
SLUG = "bando-sintetico-ricerca-sviluppo"

REQUEST_COLONNE = (
    "id", "cliente_id", "family_parent_id", "company_profile_id", "ai_check_id", "esito",
    "punteggio", "bando_id", "bando_slug", "bando_titolo", "stato", "assigned_progettista_id",
    "assigned_at", "accepted_proposal_id", "created_at", "partner_call_id", "addon_id",
    "addon_slug", "addon_prezzo",
)


def _ora() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakePrimaryConsulto(FakePrimaryWP8):
    """Il primario del WP8 con `fn_create_consultation_request` della 0041."""

    def _fn_create_consultation_request(self, p):
        pl = p["p_payload"]
        addon = next((a for a in self.righe("addons", id=pl["addon_id"]) if a["is_active"]),
                     None)
        if addon is None:
            raise errore("addon_not_available")
        call_id = pl.get("partner_call_id") or None
        if call_id and not any(
            c["company_profile_id"] == pl["company_profile_id"]
            and c["family_parent_id"] == pl["family_parent_id"]
            for c in self.righe("partner_calls", id=call_id)
        ):
            raise errore("call_non_trovata")
        aperte = [r for r in self.tabelle.get("consultation_requests", [])
                  if r["stato"] == "nuova"]
        if call_id:
            doppia = any(r.get("partner_call_id") == call_id for r in aperte)
        else:
            doppia = any(r.get("partner_call_id") is None
                         and r["family_parent_id"] == pl["family_parent_id"]
                         and int(r["bando_id"]) == int(pl["bando_id"]) for r in aperte)
        if doppia:
            raise errore("request_gia_aperta")
        consumo = (addon["tipo_fruizione"] == "consumabile" and addon["tipo_prezzo"] == "importo"
                   and Decimal(str(addon["prezzo"])) > 0)
        residua = None
        if consumo:
            inv = next((i for i in self.righe("user_addon_inventory", user_id=pl["cliente_id"],
                                              addon_id=addon["id"]) if i["quantita"] > 0), None)
            if inv is None:
                raise errore("addon_credit_esaurito")
            inv["quantita"] -= 1
            residua = inv["quantita"]
        riga = {
            "id": str(uuid.uuid4()), "cliente_id": pl["cliente_id"],
            "family_parent_id": pl["family_parent_id"],
            "company_profile_id": pl["company_profile_id"], "ai_check_id": pl.get("ai_check_id"),
            "esito": pl.get("esito"), "punteggio": pl.get("punteggio"),
            "bando_id": int(pl["bando_id"]), "bando_slug": pl["bando_slug"],
            "bando_titolo": pl["bando_titolo"], "stato": "nuova",
            "assigned_progettista_id": None, "assigned_at": None, "accepted_proposal_id": None,
            "created_at": _ora(), "partner_call_id": call_id, "addon_id": addon["id"],
            "addon_slug": addon["slug"], "addon_prezzo": addon["prezzo"],
        }
        self.tabelle.setdefault("consultation_requests", []).append(riga)
        return {"request": copy.deepcopy(riga), "consumato": consumo,
                "quantita_residua": residua}


async def scenario(*, credito: int = 3) -> tuple[FakePrimaryConsulto, object]:
    """L'esempio guida sul primario del WP8 (X crea la call della guida, già
    pubblicata; il creatore nel suo consorzio), l'addon «consulto esperto» a
    pagamento con `credito` unità per il titolare di X e due progettisti."""
    db = carica_guida(FakePrimaryConsulto())
    db.tabelle["profiles"] += [
        {"id": MEMBRO_X, "email": "membro.x@example.test", "is_active": True},
        {**PROG_USER, "email": "paola@progettisti.example.test"},
        {**ALTRO_PROG_USER, "email": "altro@progettisti.example.test", "nome": None,
         "cognome": None},
    ]
    db.tabelle.setdefault("family_members", []).append({
        "id": FM_X, "parent_id": g.OWNER["X"], "member_id": MEMBRO_X, "status": "active",
        "denominazione": "Giulia del gruppo X"})
    db.tabelle.setdefault("family_member_company_access", []).append(
        {"family_member_id": FM_X, "company_profile_id": g.COMPANY["X"]})
    db.tabelle["company_profiles"].append({
        "id": COMPANY_X2, "parent_id": g.OWNER["X"], "ragione_sociale": "Seconda di X Srl",
        "partita_iva": "12345678903", "codice_fiscale": None, "sito_web": None,
        "settore_id": None, "deleted_at": None, "archived_at": None})
    db.tabelle["addons"] = [{
        "id": ADDON_ID, "slug": "consulto-esperto", "prezzo": "49.00", "tipo_prezzo": "importo",
        "tipo_fruizione": "consumabile", "is_active": True}]
    db.tabelle["user_addon_inventory"] = [
        {"user_id": g.OWNER["X"], "addon_id": ADDON_ID, "quantita": credito}]
    db.tabelle["consultation_requests"] = []
    db.tabelle.setdefault("audit_log", [])
    db.colonne_note = {**db.colonne_note,
                       "consultation_requests": REQUEST_COLONNE,
                       "profiles": (*db.colonne_note.get("profiles", ()), "role", "email",
                                    "azienda", "is_active"),
                       "ai_checks": tuple(CHECK_SELECT.split(","))}
    assert (await partenariato_collegamenti.backfill(db))["errori"] == 0
    assert db.backfill_membri() == 3
    db.ops.clear()
    db.rpcs.clear()
    return db, secondario_guida()


@pytest.fixture(autouse=True)
def catalogo_e_fondo(monkeypatch):
    """Bando della call dal catalogo (R0-b) e task email del consulto
    raccolti (nessuna rete). `stato.ritirato` simula il 410."""
    class Stato:
        ritirato = False
        slug_richiesti: list[str] = []

    stato = Stato()

    async def carica_per_slug(secondary, slug, select):
        stato.slug_richiesti.append(slug)
        if stato.ritirato:
            raise BandoRitiratoError()
        return {"id": g.BANDO_GUIDA, "slug": slug, "titolo": "Bando sintetico R&S",
                "titolo_breve": None}

    monkeypatch.setattr(consulting_service.bandi_risoluzione, "carica_per_slug",
                        carica_per_slug)
    email: list = []

    def spawn(coro):
        email.append(coro)
        coro.close()

    monkeypatch.setattr(consulting_service, "_spawn", spawn)
    stato.email = email
    return stato


async def chiedi(db, sec, nome="X", **k):
    return await consulting_service.create_request_da_call(
        db, sec, utente(nome), attiva(nome, **k), g.CALL_GUIDA_ID)


def assegna(db, request_id, progettista=PROGETTISTA) -> dict:
    riga = db.una("consultation_requests", id=request_id)
    riga.update(stato="assegnata", assigned_progettista_id=progettista, assigned_at=_ora())
    return riga


def ai_check_ready(db, **campi) -> dict:
    riga = {c: None for c in CHECK_SELECT.split(",")}
    riga.update(id=str(uuid.uuid4()), company_profile_id=g.COMPANY["X"], user_id=g.OWNER["X"],
                family_parent_id=g.OWNER["X"], bando_id=g.BANDO_GUIDA, bando_slug=SLUG,
                bando_titolo="Bando sintetico R&S", status="ready", esito="ammissibile",
                punteggio=81, report={"requisiti": []}, created_at=_ora(), ready_at=_ora(),
                **campi)
    db.tabelle.setdefault("ai_checks", []).append(riga)
    return riga


async def attendi(coro, code: str, status: int) -> AppError:
    with pytest.raises(AppError) as exc:
        await coro
    assert (exc.value.status_code, exc.value.code) == (status, code), exc.value.message
    return exc.value


# ------------------------------------------------------------ creazione


class TestCreaDaCall:
    async def test_senza_ai_check_consuma_e_avvisa(self, catalogo_e_fondo):
        db, sec = await scenario()
        out = await chiedi(db, sec)
        [p] = db.chiamate("fn_create_consultation_request")
        pl = p["p_payload"]
        assert pl["partner_call_id"] == g.CALL_GUIDA_ID
        assert (pl["company_profile_id"], pl["family_parent_id"], pl["cliente_id"]) == (
            g.COMPANY["X"], g.OWNER["X"], g.OWNER["X"])
        assert (pl["ai_check_id"], pl["esito"], pl["punteggio"]) == (None, None, None)
        assert (pl["bando_id"], pl["bando_slug"], pl["addon_id"]) == (
            g.BANDO_GUIDA, SLUG, ADDON_ID)
        assert catalogo_e_fondo.slug_richiesti == [SLUG]
        assert str(out.partner_call_id) == g.CALL_GUIDA_ID and out.stato == "nuova"
        assert db.una("user_addon_inventory", user_id=g.OWNER["X"])["quantita"] == 2
        [audit] = [a for a in db.tabelle["audit_log"] if a["action"] == "consulenza.created"]
        assert audit["payload"]["partner_call_id"] == g.CALL_GUIDA_ID
        # notifica ai progettisti (mai l'azienda: solo il bando)
        notifiche = db.righe("notifications", tipo="consulenza.nuova_richiesta")
        assert {n["user_id"] for n in notifiche} == {PROGETTISTA, ALTRO_PROGETTISTA}
        for n in notifiche:
            assert RAGIONE["X"] not in str(n) and g.COMPANY["X"] not in str(n)
        assert len(catalogo_e_fondo.email) == 1

    async def test_snapshot_dell_ai_check_se_c_e(self):
        db, sec = await scenario()
        check = ai_check_ready(db)
        await chiedi(db, sec)
        pl = db.chiamate("fn_create_consultation_request")[0]["p_payload"]
        assert (pl["ai_check_id"], pl["esito"], pl["punteggio"]) == (
            check["id"], "ammissibile", 81)
        assert not [op for op in db.ops if op["tabella"] == "ai_checks" and op["op"] != "select"]

    async def test_coesiste_con_il_consulto_ai_check_uno_per_call(self):
        db, sec = await scenario()
        db.tabelle["consultation_requests"].append({
            **{c: None for c in REQUEST_COLONNE}, "id": str(uuid.uuid4()),
            "cliente_id": g.OWNER["X"], "family_parent_id": g.OWNER["X"],
            "company_profile_id": g.COMPANY["X"], "bando_id": g.BANDO_GUIDA,
            "bando_slug": SLUG, "bando_titolo": "Bando", "stato": "nuova",
            "created_at": _ora()})
        await chiedi(db, sec)
        errore_doppio = await attendi(chiedi(db, sec), "conflict", 409)
        assert "per questa call" in errore_doppio.message
        assert len(db.tabelle["consultation_requests"]) == 2

    async def test_area_consulenze_del_cliente(self):
        """Completamento WP9: nell'area Consulenze del cliente (elenco e
        dettaglio) il consulto chiesto dalla call porta la call e l'azienda
        (per il link `?azienda=` alla call); quello da AI-check no."""
        db, sec = await scenario()
        da_ai_check = str(uuid.uuid4())
        db.tabelle["consultation_requests"].append({
            **{c: None for c in REQUEST_COLONNE}, "id": da_ai_check,
            "cliente_id": g.OWNER["X"], "family_parent_id": g.OWNER["X"],
            "company_profile_id": g.COMPANY["X"], "bando_id": g.BANDO_GUIDA,
            "bando_slug": SLUG, "bando_titolo": "Bando", "stato": "nuova",
            "created_at": _ora()})
        creata = await chiedi(db, sec)
        elenco = {str(c.id): c for c in await consulting_service.list_my_requests(
            db, utente("X"))}
        dettaglio = await consulting_service.get_my_request(db, utente("X"), str(creata.id))
        for vista in (elenco[str(creata.id)], dettaglio):
            assert str(vista.partner_call_id) == g.CALL_GUIDA_ID
            assert str(vista.company_profile_id) == g.COMPANY["X"]
        assert elenco[da_ai_check].partner_call_id is None
        assert str(elenco[da_ai_check].company_profile_id) == g.COMPANY["X"]

    async def test_solo_il_titolare_dell_azienda_creatrice(self):
        db, sec = await scenario()
        await attendi(chiedi(db, sec, editable=False), "forbidden", 403)
        # un'altra azienda (anche candidata) e un'altra azienda dello stesso owner: 404
        with pytest.raises(NotFoundError):
            await chiedi(db, sec, "Y")
        with pytest.raises(NotFoundError):
            await chiedi(db, sec, company=COMPANY_X2)
        assert db.chiamate("fn_create_consultation_request") == []

    @pytest.mark.parametrize(("modifiche", "stato"), [
        ({"stato": "chiusa_annullata"}, 409),
        ({"stato": "sospesa_moderazione", "sospesa_at": "2026-09-01T10:00:00+00:00"}, 409),
        # difesa: una sospensione registrata vale anche con lo stato non allineato
        ({"sospesa_at": "2026-09-01T10:00:00+00:00"}, 409),
    ])
    async def test_call_annullata_o_sospesa(self, modifiche, stato):
        db, sec = await scenario()
        db.una("partner_calls", id=g.CALL_GUIDA_ID).update(modifiche)
        await attendi(chiedi(db, sec), "call_non_attiva", stato)
        assert db.chiamate("fn_create_consultation_request") == []

    async def test_bando_ritirato_410_senza_consulto(self, catalogo_e_fondo):
        db, sec = await scenario()
        catalogo_e_fondo.ritirato = True
        errore_410 = await attendi(chiedi(db, sec), "bando_ritirato", 410)
        assert "consulto" in errore_410.message
        assert db.chiamate("fn_create_consultation_request") == []
        assert db.una("user_addon_inventory", user_id=g.OWNER["X"])["quantita"] == 3

    async def test_credito_esaurito(self):
        db, sec = await scenario(credito=0)
        await attendi(chiedi(db, sec), "payment_required", 409)
        assert db.chiamate("fn_create_consultation_request") == []

    async def test_detail_della_rpc_mappati(self):
        db, sec = await scenario()
        db.rpc_guasti["fn_create_consultation_request"] = errore("call_non_trovata")
        await attendi(chiedi(db, sec), "not_found", 404)
        db.rpc_guasti["fn_create_consultation_request"] = errore("addon_credit_esaurito")
        await attendi(chiedi(db, sec), "payment_required", 409)
        db.rpc_guasti["fn_create_consultation_request"] = errore("detail_ignoto")
        await attendi(chiedi(db, sec), "upstream_error", 502)


# ------------------------------------------------------------ progettista


async def consulto_assegnato(db, sec, fondo) -> dict:
    """Y accettata nel consorzio con le quote, un messaggio in chat di Y e
    il consulto della call di X assegnato a PROGETTISTA."""
    cand = await candidature.invia_candidatura(db, sec, attiva("Y"), utente("Y"),
                                               g.CALL_GUIDA_ID, candidatura_in())
    await candidature.decidi(db, sec, attiva("X"), utente("X"), cand.id, "accetta")
    imposta_regole(db, regole())
    for nome, quota, ruolo in (("X", "70", "capofila"), ("Y", "30", "partner")):
        riga = db.membro_di(g.COMPANY[nome])
        await consorzio.aggiorna_membro(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, riga["id"],
            MembroAggiornaIn(ruolo=ruolo, posizione_id=riga["posizione_id"],
                             quota_percentuale=Decimal(quota)))
    [conv] = db.tabelle["partner_conversazioni"]
    await chat.invia(db, sec, attiva("Y"), utente("Y"), conv["id"],
                     chat.MessaggioIn(testo=TESTO_CHAT, client_msg_id=uuid.uuid4()))
    await fondo.azzera()
    out = await chiedi(db, sec)
    return assegna(db, str(out.id))


async def vista(db, sec, request_id, progettista=PROG_USER):
    return await consulting_service.get_call_per_progettista(db, sec, progettista,
                                                             str(request_id))


class TestVistaProgettista:
    async def test_proiezione_del_creatore_con_canary(self, fondo):
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        out = await vista(db, sec, richiesta["id"])
        assert str(out.richiesta_id) == richiesta["id"] and str(out.id) == g.CALL_GUIDA_ID
        assert out.stato == "pubblicata" and out.regole_partenariato is not None
        assert out.requisiti and out.posizioni and out.consorzio is not None
        assert any(r.copertura_creatore for r in out.requisiti)  # la vista del creatore
        assert out.consorzio.validazione.voci and out.consorzio.matrice.righe
        assert out.consorzio.editable is False and out.consorzio.modificabile is False
        membri = {m.creatore: m for m in out.consorzio.membri}
        partner = membri[False]
        assert partner.nome == "Azienda anonima" and partner.pseudonimo is None
        assert partner.profilo is None and not partner.sei_tu
        assert not any(m.puo_modificare or m.puo_confermare or m.puo_uscire
                       for m in out.consorzio.membri)
        testo = out.model_dump_json()
        # Nessun dato di Y: identità, contatti, valori esatti, pseudonimo.
        for valore in (g.COMPANY["Y"], g.OWNER["Y"], PIVA["Y"], RAGIONE["Y"],
                       RAGIONE["Y"].upper(), g.CODICE_PUBBLICO["Y"], EMAIL["Y"],
                       pseudonimo(g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["Y"]), *NUMERI_Y):
            assert valore not in testo, valore
        # Niente messaggi, candidature, conversazioni né id interni.
        [cand] = db.tabelle["partner_candidature"]
        [conv] = db.tabelle["partner_conversazioni"]
        for valore in (TESTO_CHAT, "CANARY-MESSAGGIO", MESSAGGIO_Y[:40], cand["id"], conv["id"],
                       g.OWNER["X"], EMAIL["X"], MEMBRO_X, "family_parent_id",
                       "company_profile_id", "candidatura_id", "_user_id", "limiti",
                       "ai_posizioni", "motivi_blocco"):
            assert valore not in testo, valore
        # L'accesso è registrato, con la richiesta e la call.
        [audit] = [a for a in db.tabelle["audit_log"]
                   if a["action"] == "consulenza.call_accessed"]
        assert audit["actor_id"] == PROGETTISTA and audit["target_user_id"] == g.OWNER["X"]
        assert audit["payload"] == {"request_id": richiesta["id"],
                                    "partner_call_id": g.CALL_GUIDA_ID}

    async def test_audit_fail_closed_502_senza_dati(self, fondo):
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        db.ops.clear()
        db.guasti[("audit_log", "insert")] = errore("audit_giu")
        await attendi(vista(db, sec, richiesta["id"]), "upstream_error", 502)
        lette = {op["tabella"] for op in db.ops if op["op"] == "select"}
        assert lette <= {"consultation_requests", "partner_calls"}, lette
        assert not ({"partner_call_requisiti", "partner_call_posizioni", "partner_call_membri",
                     "company_data", "company_financials"} & lette)

    async def test_non_assegnato_404(self, fondo):
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        with pytest.raises(NotFoundError):
            await vista(db, sec, richiesta["id"], ALTRO_PROG_USER)
        # ancora nel pool, non assegnata: nessuno la vede
        richiesta.update(stato="nuova", assigned_progettista_id=None)
        with pytest.raises(NotFoundError):
            await vista(db, sec, richiesta["id"])
        with pytest.raises(NotFoundError):
            await vista(db, sec, uuid.uuid4())
        assert not [a for a in db.tabelle["audit_log"]
                    if a["action"] == "consulenza.call_accessed"]

    async def test_richiesta_senza_call_404(self, fondo):
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        richiesta["partner_call_id"] = None
        with pytest.raises(NotFoundError):
            await vista(db, sec, richiesta["id"])

    async def test_call_di_un_altra_azienda_404(self, fondo):
        """Difesa oltre alla RPC di creazione (Q23): la call della richiesta
        deve essere dell'azienda e dell'owner della richiesta."""
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        richiesta["company_profile_id"] = COMPANY_X2
        with pytest.raises(NotFoundError):
            await vista(db, sec, richiesta["id"])
        richiesta.update(company_profile_id=g.COMPANY["X"], family_parent_id=g.OWNER["Y"])
        with pytest.raises(NotFoundError):
            await vista(db, sec, richiesta["id"])
        assert not [a for a in db.tabelle["audit_log"]
                    if a["action"] == "consulenza.call_accessed"]

    async def test_bozza_senza_consorzio(self, fondo):
        db, sec = await scenario()
        out = await chiedi(db, sec)
        richiesta = assegna(db, str(out.id))
        db.una("partner_calls", id=g.CALL_GUIDA_ID).update(stato="bozza", pubblicata_at=None)
        out = await vista(db, sec, richiesta["id"])
        assert out.stato == "bozza" and out.consorzio is None
        assert out.requisiti


# ------------------------------------------------------------ pool


class TestPool:
    async def test_pool_anonimo_per_i_non_assegnati(self):
        db, sec = await scenario()
        ai_check_ready(db)
        out = await chiedi(db, sec)
        pool = await consulting_service.list_pool(db, ALTRO_PROG_USER)
        [voce] = pool.aperte
        assert voce.da_call is True and voce.denominazione_utente == "Consulto su call di " \
                                                                     "partenariato"
        assert (voce.ragione_sociale, voce.partita_iva, voce.email) == (None, None, None)
        assert (voce.esito, voce.punteggio) == (None, None)
        assert voce.bando_titolo == "Bando sintetico R&S"
        dettaglio = await consulting_service.get_pool_request(db, ALTRO_PROG_USER, str(out.id))
        assert dettaglio.ai_check is None and dettaglio.denominazione_utente == (
            "Consulto su call di partenariato")
        testo = pool.model_dump_json() + dettaglio.model_dump_json()
        for valore in (RAGIONE["X"], PIVA["X"], EMAIL["X"], g.COMPANY["X"], g.OWNER["X"]):
            assert valore not in testo, valore

    async def test_l_assegnato_vede_i_dati_parziali(self):
        db, sec = await scenario()
        check = ai_check_ready(db)
        out = await chiedi(db, sec)
        assegna(db, str(out.id))
        pool = await consulting_service.list_pool(db, PROG_USER)
        [voce] = pool.assegnate
        assert voce.da_call is True and voce.assegnata_a_me is True
        assert voce.ragione_sociale == RAGIONE["X"] and voce.esito == "ammissibile"
        dettaglio = await consulting_service.get_pool_request(db, PROG_USER, str(out.id))
        assert dettaglio.ai_check is not None and str(dettaglio.ai_check.id) == check["id"]

    async def test_consulto_ai_check_invariato(self):
        """Un consulto da AI-check resta con i dati parziali per tutti."""
        db, sec = await scenario()
        db.tabelle["consultation_requests"].append({
            **{c: None for c in REQUEST_COLONNE}, "id": str(uuid.uuid4()),
            "cliente_id": g.OWNER["X"], "family_parent_id": g.OWNER["X"],
            "company_profile_id": g.COMPANY["X"], "bando_id": g.BANDO_GUIDA,
            "bando_slug": SLUG, "bando_titolo": "Bando", "stato": "nuova",
            "created_at": _ora()})
        [voce] = (await consulting_service.list_pool(db, ALTRO_PROG_USER)).aperte
        assert voce.da_call is False and voce.ragione_sociale == RAGIONE["X"]
