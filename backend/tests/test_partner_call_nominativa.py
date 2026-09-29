"""Call NOMINATIVE verso le altre aziende (completamento del WP9, decisione di
Michele: la verifica dell'identità sblocca le call con il nome).

Il creatore di una call con `anonima` false compare con la denominazione del
Registro Imprese SOLO se OGGI la sua azienda ha l'identità verificata dalla
piattaforma (`fn_partenariato_identita_forte`); altrimenti resta «Azienda
anonima», e una revoca della verifica la rende di nuovo anonima (anche con
l'indice della bacheca fresco). Mai altri dati nominativi: niente P.IVA,
sito, PEC, persone né id interni.

Canary su ogni vista per terzi (dettaglio pubblico, bacheca, «Per te», vista
della controparte accettata, consorzio visto da un altro membro) e sulle
viste «come ti vedono» del creatore (anteprima, le mie call). Servizi veri sul
primario finto del WP9 caricato con l'esempio guida."""

import pytest

from app.services import partenariato_consorzio_service as consorzio
from app.services import partenariato_indice
from app.services import partner_call_service as pcs
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    attiva,
    errore,
    fixture_fondo,
    utente,
)
from tests.test_partenariato_consorzio_service import accetta
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    CF_SOCIO_COMUNE,
    PIVA,
    RAGIONE,
    ambiente_wp6,
)
from tests.test_partenariato_moderazione_service import scenario_wp9

# La denominazione del registro di X (creatrice della call della guida).
NOME_X = RAGIONE["X"].upper()
SITO_X = "https://www.impresa-sintetica-x.example.test"
PEC_X = "impresa.x@pec.example.test"
ANONIMA = "Azienda anonima"


async def scenario(*, nominativa: bool = True, verificata: bool = False):
    """La guida con la call di X nominativa (o no), il sito e la PEC di X nel
    registro (canary) e, se richiesto, l'identità di X verificata."""
    db, sec = await scenario_wp9()
    db.una("partner_calls", id=g.CALL_GUIDA_ID)["anonima"] = not nominativa
    dati = db.una("company_data", company_profile_id=g.COMPANY["X"])
    dati["raw"] = {**dati["raw"], "pec": PEC_X, "webAndSocial": {"website": SITO_X}}
    if verificata:
        db.verifica_identita(g.COMPANY["X"])
    partenariato_indice.invalida()
    return db, sec


def solo_la_denominazione(testo: str) -> None:
    """Oltre alla denominazione, nessun dato che identifica X."""
    for canary in (PIVA["X"], SITO_X, PEC_X, CF_SOCIO_COMUNE, g.COMPANY["X"], g.OWNER["X"],
                   RAGIONE["X"]):
        assert canary not in testo, canary


def anonimo(creatore) -> None:
    assert (creatore.anonima, creatore.denominazione) == (True, ANONIMA)


def nominativo(creatore) -> None:
    assert (creatore.anonima, creatore.denominazione) == (False, NOME_X)
    # restano i dati pubblici della card anonima, dal registro
    assert creatore.regione == "Calabria"


async def viste_di_y(db, sec):
    """Dettaglio pubblico, card della bacheca e card di «Per te» della call
    della guida, come le vede Y."""
    dettaglio = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
    bacheca = await pcs.bacheca(db, sec, attiva("Y"), utente("Y"), vista="tutte")
    per_te = await pcs.per_te(db, sec, attiva("Y"), utente("Y"))
    [card] = [c for c in bacheca.items if str(c.id) == g.CALL_GUIDA_ID]
    [card_per_te] = [c for c in per_te.items if str(c.id) == g.CALL_GUIDA_ID]
    testo = "".join(v.model_dump_json() for v in (dettaglio, bacheca, per_te))
    return dettaglio.creatore, card.creatore, card_per_te.creatore, testo


class TestVersoTerzi:
    async def test_nominativa_di_un_azienda_non_verificata_resta_anonima(self):
        db, sec = await scenario(verificata=False)
        *creatori, testo = await viste_di_y(db, sec)
        for creatore in creatori:
            anonimo(creatore)
        assert NOME_X not in testo
        solo_la_denominazione(testo)

    async def test_verificata_mostra_la_denominazione_del_registro(self):
        db, sec = await scenario(verificata=True)
        *creatori, testo = await viste_di_y(db, sec)
        for creatore in creatori:
            nominativo(creatore)
        solo_la_denominazione(testo)

    async def test_revoca_torna_anonima_anche_con_l_indice_fresco(self):
        db, sec = await scenario(verificata=True)
        *creatori, _ = await viste_di_y(db, sec)
        nominativo(creatori[1])
        db.revoca_identita(g.COMPANY["X"])  # l'indice NON si invalida
        *creatori, testo = await viste_di_y(db, sec)
        for creatore in creatori:
            anonimo(creatore)
        assert NOME_X not in testo

    async def test_call_anonima_di_un_azienda_verificata_resta_anonima(self):
        db, sec = await scenario(nominativa=False, verificata=True)
        *creatori, testo = await viste_di_y(db, sec)
        for creatore in creatori:
            anonimo(creatore)
        assert NOME_X not in testo

    async def test_interruttore_globale_spento(self, monkeypatch):
        monkeypatch.setattr(pcs.pps, "NOMINATIVO_DISPONIBILE", False)
        db, sec = await scenario(verificata=True)
        *creatori, testo = await viste_di_y(db, sec)
        for creatore in creatori:
            anonimo(creatore)
        assert NOME_X not in testo

    async def test_verifica_illeggibile_resta_anonima(self):
        db, sec = await scenario(verificata=True)
        db.rpc_guasti["fn_partenariato_identita_forte"] = errore("errore_interno")
        *creatori, testo = await viste_di_y(db, sec)
        for creatore in creatori:
            anonimo(creatore)
        assert NOME_X not in testo

    async def test_registro_di_un_altra_piva_resta_anonima(self):
        """T5: con i dati del registro di un'altra partita IVA non c'è una
        denominazione dell'azienda da mostrare (qui la verifica resta
        registrata: il nome non esce comunque)."""
        db, sec = await scenario(verificata=True)
        db.una("company_data", company_profile_id=g.COMPANY["X"])["piva_fetched"] = "99999999999"
        *creatori, testo = await viste_di_y(db, sec)
        for creatore in creatori:
            anonimo(creatore)
        assert NOME_X not in testo


class TestControparteEConsorzio:
    @pytest.mark.parametrize("verificata", [False, True])
    async def test_controparte_e_membro_del_consorzio(self, fondo, verificata):
        """Y accettata (senza rivelazione: Y non è verificata) vede il
        creatore della call nominativa con il nome solo se X è verificata,
        nella vista della call come nel consorzio."""
        db, sec = await scenario(verificata=verificata)
        await accetta(db, sec)
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.vista == "controparte" and vista.identita is None
        membri = (await consorzio.get_consorzio(db, sec, attiva("Y"), utente("Y"),
                                                g.CALL_GUIDA_ID)).membri
        [creatore] = [m for m in membri if m.creatore]
        testo = vista.model_dump_json() + creatore.model_dump_json()
        if verificata:
            nominativo(vista.creatore)
            assert creatore.nome == NOME_X
        else:
            anonimo(vista.creatore)
            assert creatore.nome == ANONIMA
            assert NOME_X not in testo
        assert creatore.pseudonimo is None and creatore.profilo is None
        # i riservati restano ripuliti dagli identificativi (niente rivelazione)
        for canary in (PIVA["X"], SITO_X, PEC_X, g.COMPANY["X"]):
            assert canary not in testo
        # revoca: il consorzio torna anonimo
        db.revoca_identita(g.COMPANY["X"])
        membri = (await consorzio.get_consorzio(db, sec, attiva("Y"), utente("Y"),
                                                g.CALL_GUIDA_ID)).membri
        assert [m.nome for m in membri if m.creatore] == [ANONIMA]


class TestComeTiVedono:
    @pytest.mark.parametrize("verificata", [False, True])
    async def test_anteprima_e_le_mie_call(self, verificata):
        db, sec = await scenario(verificata=verificata)
        anteprima = await pcs.anteprima(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
        mie = await pcs.lista_mie(db, sec, attiva("X"), utente("X"))
        [mia] = [c for c in mie.items if str(c.id) == g.CALL_GUIDA_ID]
        for creatore in (anteprima.call.creatore, mia.creatore):
            if verificata:
                nominativo(creatore)
            else:
                anonimo(creatore)
