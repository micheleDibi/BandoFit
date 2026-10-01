"""Autorizzazione e proiezioni delle call (WP5, docs/partenariati.md T3, C3):
ruoli sulla call (creatore, titolare_o_membro, pubblico, admin), 404 fuori
autorizzazione (altra azienda dello stesso owner, altro owner, call non
pubblica, azienda non viva, id malformato, ruoli non ammessi dalla rotta) e
CANARY sulle proiezioni verso terzi: `company_profile_id`,
`family_parent_id`, `creato_da`, budget esatto, dettagli riservati,
denominazione, P.IVA, fasce e coperture del creatore non compaiono MAI."""

import uuid
from decimal import Decimal

import pytest

from app.api.deps import ActiveCompany
from app.core.errors import NotFoundError
from app.schemas.partner_call import CallCardOut, CallPubblicaOut, CreatoreCallOut
from app.services import partenariato_accesso as acc
from app.services.partenariato_anonimato import identificativi_azienda
from tests.test_partner_call_service import (  # noqa: F401  fixture autouse
    ALTRA_COMPANY,
    ALTRO_OWNER,
    COMPANY,
    COMPANY_B,
    MEMBRO,
    OWNER,
    PIVA,
    USER_ADMIN,
    USER_ALTRO,
    USER_MEMBRO,
    USER_OWNER,
    FakeDb,
    _iso,
    catalogo,
    company_data,
    riga_call,
    stub_settings,
)

CANARY_BUDGET = "987654.32"
CANARY_RISERVATI = "CANARYRISERVATO nome del cliente"
CANARY_NOTA = "CANARYNOTA fatturato 7.654.321"
CANARY_OVERRIDE = "CANARYOVERRIDE motivo riservato del creatore"
CREATO_DA = "f0000000-0000-0000-0000-00000000c0de"
REGIONI = {1: "Piemonte", 3: "Lombardia", 5: "Veneto"}


def titolare(company=COMPANY) -> ActiveCompany:
    return ActiveCompany(company_id=company, owner_id=OWNER, editable=True)


def membro() -> ActiveCompany:
    return ActiveCompany(company_id=COMPANY, owner_id=OWNER, editable=False)


def terzo() -> ActiveCompany:
    return ActiveCompany(company_id=ALTRA_COMPANY, owner_id=ALTRO_OWNER, editable=True)


def call_canary(**modifiche) -> dict:
    return riga_call(**{
        "stato": "pubblicata", "pubblicata_at": _iso(), "scadenza_call": "2099-01-01",
        "creato_da": CREATO_DA, "titolo": "Cerchiamo partner per Rossi Meccanica",
        "descrizione_pubblica": "Progetto nella logistica: scrivete a info@rossimeccanica.it",
        "profilo_partner_ideale": "Un laboratorio (P.IVA 01234567897)",
        "dettagli_riservati": CANARY_RISERVATI, "budget_fascia": "500k_1m",
        "budget_progetto_eur": CANARY_BUDGET, "quota_creatore_pct": 61.5,
        "override_non_ammesso_motivo": CANARY_OVERRIDE,
        "partenariato_ref": {"modalita_effettiva": "CANARYREF"},
        "regole_partenariato": {"CANARYREGOLE": True}, "ai_check_id": str(uuid.uuid4()),
        "ai_testi_proposta": {"titolo": "CANARYPROPOSTA"}, **modifiche,
    })


def requisiti_canary(call_id) -> list[dict]:
    base = {"call_id": call_id, "origine": "manuale", "rif_origine": None, "citazione": None,
            "copertura_creatore": "coperto", "copertura_fonte": "bilanci",
            "copertura_nota": CANARY_NOTA}
    return [
        {**base, "id": str(uuid.uuid4()), "etichetta": "A", "ordine": 0, "cercato": True,
         "ambito": "consorzio", "testo": "Organismo di ricerca",
         "criterio": {"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]}},
        {**base, "id": str(uuid.uuid4()), "etichetta": "B", "ordine": 1, "cercato": False,
         "ambito": "consorzio", "testo": "CANARYNONCERCATO requisito che il creatore copre",
         "criterio": {"tipo": "ateco", "divisioni": ["62"]}},
        {**base, "id": str(uuid.uuid4()), "etichetta": "C", "ordine": 2, "cercato": False,
         "ambito": "ogni_membro", "testo": "Sede in Lombardia",
         "criterio": {"tipo": "regione", "regioni_ids": [3], "modalita": "sede_attuale"}},
        {**base, "id": str(uuid.uuid4()), "etichetta": "D", "ordine": 3, "cercato": True,
         "ambito": "consorzio", "testo": "Criterio corrotto", "criterio": {"tipo": "boh"}},
    ]


def posizioni_canary(call_id, requisiti) -> list[dict]:
    return [{
        "id": str(uuid.uuid4()), "call_id": call_id, "titolo": "Partner come Rossi Meccanica",
        "ruolo": "partner", "tipi_soggetto": ["organismo_ricerca", "codice_ignoto"],
        "competenze": ["prototipazione_testing"], "ateco_divisioni": ["72"], "regioni": [3, 99],
        "territorio_modalita": "sede_attuale", "paesi": ["IT"], "dimensioni": ["piccola"],
        "quota_ipotizzata_pct": 20.0, "numero": 2,
        "requisiti_ids": [r["id"] for r in requisiti], "note": "Tel. 02 1234 5678", "ordine": 0,
    }]


def azienda():
    db = FakeDb()
    company = db.tabelle["company_profiles"][0]
    return company, company_data(), db.tabelle["company_people"]


def proiezione(call=None, *, dati_registro=True):
    call = call or call_canary()
    company, dati, persone = azienda()
    ident = identificativi_azienda(company, dati, persone)
    requisiti = requisiti_canary(call["id"])
    creatore = acc.creatore_pubblico(dati if dati_registro else None, {}, REGIONI)
    return acc.call_pubblica(call, requisiti, posizioni_canary(call["id"], requisiti), creatore,
                             ident=ident, regioni=REGIONI)


# ------------------------------------------------------------------ ruoli


class TestRuoli:
    @pytest.mark.parametrize(
        ("active", "user", "modifiche", "atteso"),
        [
            (titolare(), USER_OWNER, {}, "creatore"),
            (membro(), USER_MEMBRO, {}, "titolare_o_membro"),
            (titolare(), USER_OWNER, {"stato": "bozza"}, "creatore"),
            (membro(), USER_MEMBRO, {"stato": "chiusa_annullata"}, "titolare_o_membro"),
            # Advisor: la call di B con A attiva non è «sua»
            (titolare(COMPANY_B), USER_OWNER, {"stato": "bozza"}, None),
            (titolare(COMPANY_B), USER_OWNER, {}, "pubblico"),
            (terzo(), USER_ALTRO, {}, "pubblico"),
            (terzo(), USER_ALTRO, {"stato": "bozza"}, None),
            (terzo(), USER_ALTRO, {"visibilita": "solo_invitati"}, None),
            (terzo(), USER_ALTRO, {"stato": "sospesa_moderazione"}, None),
            (terzo(), USER_ALTRO, {"stato": "scaduta"}, None),
            (terzo(), USER_ADMIN, {"stato": "bozza"}, "admin"),
            # stessa azienda ma un owner diverso nella riga (dati incoerenti)
            (titolare(), USER_OWNER, {"family_parent_id": ALTRO_OWNER}, "pubblico"),
            (ActiveCompany(company_id=None, owner_id=OWNER, editable=True), USER_OWNER,
             {"stato": "bozza"}, None),
        ],
    )
    def test_matrice(self, active, user, modifiche, atteso):
        riga = riga_call(**{"stato": "pubblicata", **modifiche})
        assert acc.ruolo_su_call(riga, active, user) == atteso


class TestCaricaAutorizzata:
    async def test_creatore_e_membro(self):
        db = FakeDb()
        call = db.con_call()
        _, ruolo = await acc.carica_call_autorizzata(db, call["id"], titolare(), USER_OWNER)
        assert ruolo == "creatore"
        _, ruolo = await acc.carica_call_autorizzata(
            db, call["id"], membro(), USER_MEMBRO, ammessi=acc.RUOLI_AZIENDA)
        assert ruolo == "titolare_o_membro"

    @pytest.mark.parametrize(
        ("modifiche", "active", "user", "ammessi"),
        [
            ({}, titolare(COMPANY_B), USER_OWNER, None),  # bozza di A con B attiva
            ({}, terzo(), USER_ALTRO, None),  # bozza di un altro owner
            ({"stato": "pubblicata"}, terzo(), USER_ALTRO, acc.RUOLI_AZIENDA),
            ({"stato": "pubblicata"}, membro(), USER_MEMBRO, acc.RUOLI_SCRITTURA),
            ({"stato": "pubblicata", "visibilita": "solo_invitati"}, terzo(), USER_ALTRO, None),
        ],
        ids=["advisor_altra_azienda", "altro_owner", "pubblico_su_rotta_interna",
             "membro_su_scrittura", "solo_invitati"],
    )
    async def test_fuori_autorizzazione_404(self, modifiche, active, user, ammessi):
        db = FakeDb()
        call = db.con_call(**modifiche)
        with pytest.raises(NotFoundError) as exc:
            await acc.carica_call_autorizzata(db, call["id"], active, user, ammessi=ammessi)
        assert exc.value.message == "Call di partenariato non trovata"

    async def test_pubblico_solo_con_azienda_viva(self):
        db = FakeDb()
        call = db.con_call(stato="pubblicata", pubblicata_at=_iso())
        _, ruolo = await acc.carica_call_autorizzata(db, call["id"], terzo(), USER_ALTRO)
        assert ruolo == "pubblico"
        db.tabelle["company_profiles"][0]["deleted_at"] = _iso()
        with pytest.raises(NotFoundError):
            await acc.carica_call_autorizzata(db, call["id"], terzo(), USER_ALTRO)

    @pytest.mark.parametrize("identificativo", ["x", "", None, "1' or '1'='1",
                                                str(uuid.uuid4())])
    async def test_id_malformato_o_inesistente(self, identificativo):
        db = FakeDb()
        with pytest.raises(NotFoundError):
            await acc.carica_call_autorizzata(db, identificativo, titolare(), USER_OWNER)
        # un id malformato non arriva nemmeno al DB (niente 22P02 → 502)
        if identificativo != "" and identificativo is not None and len(str(identificativo)) < 30:
            assert db.ops == []

    async def test_id_in_forma_canonica(self):
        db = FakeDb()
        call = db.con_call()
        maiuscolo = call["id"].upper()
        riga, _ = await acc.carica_call_autorizzata(db, maiuscolo, titolare(), USER_OWNER)
        assert riga["id"] == call["id"]


# ------------------------------------------------------------ canary


VIETATI = (
    COMPANY, OWNER, CREATO_DA, PIVA, CANARY_BUDGET, "987654", CANARY_RISERVATI,
    "CANARYRISERVATO", CANARY_NOTA, "CANARYNOTA", CANARY_OVERRIDE, "CANARYREF",
    "CANARYREGOLE", "CANARYPROPOSTA", "CANARYNONCERCATO", "ROSSI MECCANICA", "Rossi Meccanica",
    "rossimeccanica", "500k_2m", "fascia_fatturato", "61.5", "copertura", "company_profile_id",
    "family_parent_id", "creato_da", "budget_progetto_eur", "dettagli_riservati",
    "quota_creatore_pct", "regole_partenariato", "ai_check_id",
)


class TestCanary:
    def test_vista_pubblica(self):
        out = proiezione()
        testo = out.model_dump_json()
        for vietato in VIETATI:
            assert vietato not in testo, vietato
        assert out.creatore == CreatoreCallOut(
            anonima=True, denominazione="Azienda anonima", regione="Lombardia",
            ateco_sezione={"lettera": "J", "descrizione": out.creatore.ateco_sezione.descrizione},
            classe_dimensionale="piccola",
        )
        # testi pubblici anonimizzati anche se salvati male
        assert "[rimosso]" in out.titolo and "[rimosso]" in out.descrizione_pubblica
        assert "[rimosso]" in out.profilo_partner_ideale

    def test_requisiti_cercati_o_per_ogni_membro(self):
        out = proiezione()
        assert [(r.etichetta, r.ambito) for r in out.requisiti] == [
            ("A", "consorzio"), ("C", "ogni_membro"), ("D", "consorzio")]
        # criterio corrotto → manuale, mai un criterio inventato
        assert out.requisiti[2].criterio.tipo == "manuale"
        [posizione] = out.posizioni
        assert posizione.requisiti == ["A", "C", "D"]  # B non è visibile
        assert posizione.tipi_soggetto == ["organismo_ricerca"]
        assert posizione.regioni_nomi == ["Lombardia"]
        assert posizione.note == "Tel. [rimosso]"
        assert "Rossi" not in posizione.titolo

    def test_registro_non_dell_azienda_nessun_dato(self):
        out = proiezione(dati_registro=False)
        assert out.creatore.regione is None and out.creatore.ateco_sezione is None
        assert out.creatore.classe_dimensionale is None

    def test_whitelist_anche_in_costruzione(self):
        assert CallPubblicaOut.model_config.get("extra") == "forbid"
        assert CallCardOut.model_config.get("extra") == "forbid"
        campi = set(CallPubblicaOut.model_fields)
        for riservato in ("company_profile_id", "family_parent_id", "creato_da",
                          "budget_progetto_eur", "dettagli_riservati", "quota_creatore_pct",
                          "regole_partenariato", "override_non_ammesso_motivo"):
            assert riservato not in campi

    def test_card(self):
        company, dati, persone = azienda()
        ident = identificativi_azienda(company, dati, persone)
        creatore = acc.creatore_pubblico(dati, {}, REGIONI)
        call = call_canary()
        altrui = acc.call_card(call, creatore, posizioni_n=1, requisiti_cercati_n=2, mia=False,
                               ident=ident)
        testo = altrui.model_dump_json()
        for vietato in VIETATI:
            assert vietato not in testo, vietato
        assert altrui.wizard_passo is None and altrui.updated_at is None
        mia = acc.call_card(call, creatore, posizioni_n=1, requisiti_cercati_n=2, mia=True,
                            ident=ident)
        assert mia.wizard_passo == 1 and mia.updated_at is not None

    @pytest.mark.parametrize("stato", ["sospeso", "aperto", None])
    def test_stato_del_bando_nella_proiezione(self, stato):
        # C4/Q18: lo snapshot `bando_stato_effettivo` esce come `bando.stato_effettivo`.
        company, dati, persone = azienda()
        ident = identificativi_azienda(company, dati, persone)
        creatore = acc.creatore_pubblico(dati, {}, REGIONI)
        call = call_canary(bando_stato_effettivo=stato)
        assert proiezione(call).bando.stato_effettivo == stato
        card = acc.call_card(call, creatore, posizioni_n=1, requisiti_cercati_n=2, mia=False,
                             ident=ident)
        assert card.bando.stato_effettivo == stato

    def test_stato_del_bando_assente_none(self):
        call = {k: v for k, v in call_canary().items() if k != "bando_stato_effettivo"}
        assert proiezione(call).bando.stato_effettivo is None

    def test_forma_altra_non_esce(self):
        out = proiezione(call_canary(forma_aggregazione_prevista="altra"))
        assert out.forma_aggregazione_prevista is None

    def test_denominazione_solo_nominativa_e_verificata(self):
        """Completamento WP9: il nome del registro solo per una call con
        `anonima` false di un'azienda verificata oggi, ripulito dai caratteri
        invisibili; il resto della card del creatore non cambia."""
        dati = company_data(denominazione="ROSSI​ MECCANICA   SRL")
        nominativa = {"anonima": False}
        assert acc.denominazione_nominativa(nominativa, dati, verificata_oggi=True) == (
            "ROSSI MECCANICA SRL")
        assert acc.denominazione_nominativa(nominativa, dati, verificata_oggi=False) is None
        assert acc.denominazione_nominativa({"anonima": True}, dati, verificata_oggi=True) is None
        assert acc.denominazione_nominativa({}, dati, verificata_oggi=True) is None
        assert acc.denominazione_nominativa(nominativa, None, verificata_oggi=True) is None
        con_nome = acc.creatore_pubblico(dati, {}, REGIONI, denominazione="ROSSI MECCANICA SRL")
        senza = acc.creatore_pubblico(dati, {}, REGIONI)
        assert (con_nome.anonima, con_nome.denominazione) == (False, "ROSSI MECCANICA SRL")
        assert (senza.anonima, senza.denominazione) == (True, "Azienda anonima")
        assert con_nome.model_dump(exclude={"anonima", "denominazione"}) == senza.model_dump(
            exclude={"anonima", "denominazione"})
        assert acc.creatore_pubblico(dati, {}, REGIONI, denominazione="  ").anonima is True

    def test_testo_ridotto_al_solo_rimosso(self):
        assert acc.testo_pubblico("info@rossi.it", None) is None
        assert acc.testo_pubblico("   ", None) is None
        assert acc.testo_pubblico(None, None) is None


class TestFormaCanonica:
    """Il controllo anti-contatti e la proiezione lavorano sulla forma
    canonica: caratteri invisibili, spazi Unicode e cifre o simboli a
    larghezza piena non nascondono un contatto; il nome dell'azienda si
    riconosce anche scritto attaccato."""

    @pytest.fixture
    def ident(self):
        return identificativi_azienda(*azienda())

    @pytest.mark.parametrize(
        ("testo", "tipo", "atteso"),
        [
            ("Contattaci: 333\u200b1234567", "telefono", "Contattaci: [rimosso]"),
            ("Contattaci: \uff13\uff13\uff13\uff11\uff12\uff13\uff14\uff15\uff16\uff17",
             "telefono", "Contattaci: [rimosso]"),
            ("Chiama il 333\u2009123\u20094567", "telefono", "Chiama il [rimosso]"),
            ("Scrivi a mario\u200b@\u200bgmail\u200b.com", "email", "Scrivi a [rimosso]"),
            ("Scrivi a mario\uff20gmail\uff0ecom", "email", "Scrivi a [rimosso]"),
            ("Siamo la Ros\u200bsi Meccanica", "ragione_sociale", "Siamo la [rimosso]"),
            ("Siamo la RossiMeccanica", "ragione_sociale", "Siamo la [rimosso]"),
            ("Vedi rossimeccanica\u200b.it", "dominio_azienda", "Vedi [rimosso]"),
        ],
    )
    def test_contatti_nascosti(self, ident, testo, tipo, atteso):
        rilievi = acc.rilievi_testo(testo, ident)
        assert tipo in {r.tipo for r in rilievi if r.bloccante}
        assert acc.testo_pubblico(testo, ident) == atteso

    def test_riservati_solo_contatti(self):
        rilievi = acc.rilievi_testo("La RossiMeccanica guida: mario\uff20rossi\uff0eit", None,
                                    anonima=False)
        assert {r.tipo for r in rilievi} == {"email"}

    def test_testo_senza_contatti_resta_com_e(self, ident):
        # nessuna normalizzazione visibile se non c'è niente da togliere
        testo = "Il 1º classificato riceve un premio per l'area di 20 m²"
        assert acc.rilievi_testo(testo, ident) == []
        assert acc.testo_pubblico(testo, ident) == testo
        # i caratteri invisibili però non escono mai: nemmeno un controllo di
        # direzione che mostrerebbe al contrario («7654321 333» → «333 1234567»)
        assert acc.testo_pubblico("Partner\u200b per la logistica", ident) == (
            "Partner per la logistica")
        assert acc.testo_pubblico("Chiamaci \u202e7654321 333", ident) == (
            "Chiamaci 7654321 333")

    def test_nome_attaccato_solo_se_abbastanza_lungo(self):
        corto = acc.Identificativi(ragione_sociale="alfa beta")  # «alfabeta»: 8
        cortissimo = acc.Identificativi(ragione_sociale="ab cd")
        assert {r.tipo for r in acc.rilievi_testo("Siamo Alfabeta", corto)} == {"ragione_sociale"}
        assert acc.rilievi_testo("Siamo Abcd", cortissimo) == []


class TestVersioni:
    def test_snapshot_a_whitelist(self):
        call = call_canary(sospeso_da=str(uuid.uuid4()))
        snapshot = {
            "call": call,
            "requisiti": [{**r, "call_id": call["id"]} for r in requisiti_canary(call["id"])],
            "posizioni": posizioni_canary(call["id"], []),
        }
        out = acc.proietta_versione(snapshot)
        assert set(out) == {"call", "requisiti", "posizioni"}
        for interno in ("family_parent_id", "creato_da", "company_profile_id", "sospeso_da",
                        "partenariato_ref", "ai_check_id", "ai_testi_proposta", "wizard_passo"):
            assert interno not in out["call"], interno
        assert out["call"]["budget_progetto_eur"] == CANARY_BUDGET  # azienda creatrice
        assert all("call_id" not in r for r in out["requisiti"] + out["posizioni"])
        assert acc.proietta_versione(None) == {"call": {}, "requisiti": [], "posizioni": []}


def test_decimali_come_numeri_decimali():
    out = proiezione()
    assert out.posizioni[0].quota_ipotizzata_pct == Decimal("20.0")
