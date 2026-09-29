"""Esempio guida (docs/partenariati.md §10, criterio 4) — parte PURA e, in
fondo, parte di SERVIZIO (indice, servizi, fan-out e API del WP6) e il
seguito del WP7: Y (piano a pagamento) si candida, X accetta, si apre la
conversazione con l'audit e Y scrive; variante con Y sul Gratuito (criterio
6): la candidatura spontanea no, l'invito sì. WP8 (punto 5): dopo
l'accettazione Y è nel consorzio di X; con le regole confermate (almeno 2
partner, un organismo di ricerca, una PMI, quota di ogni partner tra 10% e
70%) le quote 70/30 danno verde, 75/25 rosso «quota massima 70%», la quota
di Y mancante grigio; l'esito salvato sulla call segue.

Scenario e numeri in `tests/fixtures/partenariati/esempio_guida.py`: X
pubblica la call con i requisiti A–D (copre B e D, cerca A e C) e i vincoli
di ogni membro E (sede in Calabria) ed F (costo della quota ≤ 60% del
fatturato medio); Y copre A e C; Z, W, V e U restano fuori ciascuna per il
suo motivo; T (senza bilanci) resta, sotto Y, con un'attenzione.
"""

import uuid
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.schemas.partenariato_consorzio import MembroAggiornaIn
from app.services import partenariato_candidature_service as candidature
from app.services import partenariato_chat_service as chat
from app.services import (
    partenariato_collegamenti,
    partenariato_indice,
    partenariato_notifiche,
)
from app.services import partenariato_consorzio_service as consorzio
from app.services import partenariato_matching as pm
from app.services import partenariato_vocabolario as voc
from app.services import partner_call_service as pcs
from app.services.partenariato_accesso import pseudonimo
from app.services.partenariato_criteri import criterio_da_json
from app.services.partenariato_matching import CoperturaOut
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariati_matching_api import attiva, chiama, utente
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    MEMBRO_X,
    candidatura_in,
    fixture_fondo,
    scenario_wp7,
)
from tests.test_partenariato_consorzio_service import (
    imposta_regole,
    regole,
    scenario_wp8,
    termini,
)
from tests.test_partenariato_indice import ambiente_wp6, scenario_guida  # noqa: F401


def test_per_te_di_y_mette_la_call_in_cima():
    risultati = pm.per_te(g.indice(), g.COMPANY["Y"], oggi=g.OGGI)
    assert [m.call_id for m in risultati] == [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]
    primo = pm.proietta_match(risultati[0], vista="proprio")
    assert primo.spiegazione == g.SPIEGAZIONE_GUIDA == (
        "Copri «A» e «C», che mancano al capofila."
    )
    assert primo.copertura == CoperturaOut(coperti=2, cercati=2)
    assert [(r.requisito_id, r.etichetta) for r in primo.copre] == [
        (g.REQ["A"], "A"), (g.REQ["C"], "C"),
    ]
    assert primo.non_copre == [] and primo.attenzione == []
    assert [p.titolo for p in primo.posizioni_compatibili] == ["Ricerca e prototipazione"]
    # Il punteggio ordina ma non esce (oracolo sui dati non pubblici del creatore).
    assert risultati[0].punteggio == g.PUNTEGGIO_Y
    assert "punteggio" not in primo.model_dump()
    # La seconda call (di O): Y copre solo «B».
    assert risultati[1].spiegazione == "Copri «B», che manca al capofila."


def test_la_call_solo_su_invito_non_compare_senza_invito():
    risultati = pm.per_te(g.indice(), g.COMPANY["Y"], oggi=g.OGGI)
    assert g.CALL_RISERVATA_ID not in [m.call_id for m in risultati]
    riservata = g.indice().calls[g.CALL_RISERVATA_ID]
    assert pm.esclusione(riservata, g.profilo("Y"), oggi=g.OGGI, direzione="per_te") == (
        "solo_invitati"
    )


def test_suggeriti_di_x_mettono_y_in_cima_con_la_stessa_spiegazione():
    risultati = pm.suggeriti_per_call(g.indice(), g.CALL_GUIDA_ID, oggi=g.OGGI)
    assert [m.company_id for m in risultati] == [g.COMPANY["Y"], g.COMPANY["T"]]
    y = pm.proietta_match(risultati[0], vista="terzi")
    assert y.spiegazione == g.SPIEGAZIONE_GUIDA
    assert y.copertura == CoperturaOut(coperti=2, cercati=2)
    assert y.attenzione == []
    assert risultati[0].punteggio == g.PUNTEGGIO_Y
    assert "punteggio" not in y.model_dump()
    # Y è anonima: verso X la sola fascia di fatturato (Q12).
    assert y.fasce.model_dump() == {
        "fatturato": "2m_10m", "patrimonio_netto": None, "dipendenti": None, "trend": None,
    }


@pytest.mark.parametrize(
    "nome, codice",
    [
        ("Z", "territorio"),
        ("W", "collegata"),
        ("V", "non_visibile"),
        ("U", "finanziaria_non_soddisfatta"),
        ("X", "stesso_owner"),
    ],
)
def test_esclusi_con_il_motivo_atteso(nome, codice):
    assert pm.esclusione(g.call_guida(), g.profilo(nome), oggi=g.OGGI) == codice
    risultati = pm.suggeriti_per_call(g.indice(), g.CALL_GUIDA_ID, oggi=g.OGGI)
    assert g.COMPANY[nome] not in [m.company_id for m in risultati]


def test_t_sotto_y_con_attenzione_e_penalita():
    y, t = pm.suggeriti_per_call(g.indice(), g.CALL_GUIDA_ID, oggi=g.OGGI)
    assert t.company_id == g.COMPANY["T"]
    assert t.coperti == y.coperti == 2
    assert t.spiegazione == g.SPIEGAZIONE_GUIDA
    assert [(a.codice, a.testo) for a in t.attenzione] == [
        ("bilanci_non_disponibili",
         "Bilanci non disponibili: la regola «F» non si può verificare"),
    ]
    assert t.componenti == y.componenti
    assert (y.punteggio, t.punteggio, t.penalita) == (g.PUNTEGGIO_Y, g.PUNTEGGIO_T, 5)


def test_punteggio_di_y_calcolato_a_mano():
    m = pm.valuta_coppia(g.call_guida(), g.profilo("Y"), oggi=g.OGGI)
    c = m.componenti
    assert (c.copertura_gap, c.affinita, c.complementarita, c.completezza, c.rotazione) == (
        1, pytest.approx(2 / 3), 1, pytest.approx(0.7), 1,
    )
    assert m.punteggio == 90


def test_revoca_di_y_nell_indice_la_toglie_dai_suggeriti():
    indice = g.indice()
    revocata = g.profilo("Y", profilo_partner={**g.AZIENDE["Y"]["profilo_partner"],
                                               "visibile_come_partner": False})
    dopo = pm.IndiceMatching(calls=indice.calls,
                             candidati={**indice.candidati, g.COMPANY["Y"]: revocata})
    risultati = pm.suggeriti_per_call(dopo, g.CALL_GUIDA_ID, oggi=g.OGGI)
    assert [m.company_id for m in risultati] == [g.COMPANY["T"]]


def test_budget_della_call_e_la_fascia_pubblica():
    # Il budget esatto (3,1 M€) è riservato: il matching vede 2–5 M€.
    assert g.call_guida().budget == (2_000_000, 5_000_000)


def test_la_fixture_usa_solo_il_vocabolario_v1():
    for nome, azienda in g.AZIENDE.items():
        profilo = azienda["profilo_partner"]
        if profilo is None:
            continue
        assert set(profilo["competenze"]) <= set(voc.COMPETENZE), nome
        assert set(profilo["tipi_soggetto"]) <= set(voc.TIPI_SOGGETTO), nome
        assert set(profilo["forme_accettate"]) <= set(voc.FORME), nome
        assert set(profilo["ruoli_disponibili"]) <= set(voc.RUOLI), nome
        assert all(e["ruolo"] in voc.RUOLI for e in profilo["esperienze"]), nome
    for call in (g.CALL_GUIDA, g.CALL_ALTRA, g.CALL_RISERVATA):
        forma = call["forma_aggregazione_prevista"]
        assert forma is None or forma in voc.FORME
    righe = [*g.REQUISITI_GUIDA, *g.REQUISITI_ALTRA, *g.REQUISITI_RISERVATA]
    for riga in righe:
        criterio = criterio_da_json(riga["criterio"])
        # Un codice fuori vocabolario diventerebbe `manuale`.
        assert criterio.tipo == riga["criterio"]["tipo"], riga["etichetta"]
    for posizione in [*g.POSIZIONI_GUIDA, *g.POSIZIONI_ALTRA, *g.POSIZIONI_RISERVATA]:
        assert set(posizione["tipi_soggetto"]) <= set(voc.TIPI_SOGGETTO)
        assert set(posizione["competenze"]) <= set(voc.COMPETENZE)
        assert posizione["ruolo"] in voc.RUOLI


# ------------------------------------------------ parte di SERVIZIO (WP6)
#
# Le stesse righe della fixture caricate nel primario finto del WP6
# (`test_partenariato_indice.carica_guida`), con le chiavi dei collegamenti
# CALCOLATE da `partenariato_collegamenti` (W e X hanno un socio comune al
# 60%): indice, servizi e API veri.


async def test_servizio_per_te_di_y_e_suggeriti_di_x():
    db, sec = await scenario_guida()
    per_te = await pcs.per_te(db, sec, attiva("Y"), utente("Y"))
    assert [i.id for i in per_te.items] == [uuid.UUID(g.CALL_GUIDA_ID),
                                           uuid.UUID(g.CALL_ALTRA_ID)]
    assert per_te.items[0].match.spiegazione == g.SPIEGAZIONE_GUIDA
    assert "punteggio" not in per_te.items[0].match.model_dump()

    suggeriti = await pcs.suggeriti(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
    assert [c.pseudonimo for c in suggeriti.items] == [
        pseudonimo(g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["Y"]),
        pseudonimo(g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["T"]),
    ]
    y, t = suggeriti.items
    assert y.match.spiegazione == t.match.spiegazione == g.SPIEGAZIONE_GUIDA
    assert [a.codice for a in t.match.attenzione] == ["bilanci_non_disponibili"]
    # W fuori per il collegamento calcolato dalle chiavi HMAC, non a mano
    chiavi = {n: partenariato_collegamenti.chiavi_da_righe(
        db.righe("company_collegamenti", company_profile_id=g.COMPANY[n])) for n in ("X", "W")}
    assert partenariato_collegamenti.valuta_collegamento(chiavi["X"], chiavi["W"]) == "certo"
    testo = suggeriti.model_dump_json()
    for nome in ("Y", "T", "Z", "W", "V", "U"):
        assert g.COMPANY[nome] not in testo and g.CODICE_PUBBLICO[nome] not in testo


async def test_servizio_revoca_di_y_sparisce_subito_anche_con_indice_fresco():
    db, sec = await scenario_guida()
    await pcs.suggeriti(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
    fresco = await partenariato_indice.indice(db, sec)
    assert g.COMPANY["Y"] in fresco.matching.candidati  # l'indice vede ancora Y
    # revoca registrata a DB (per esempio da un altro processo o dal trigger
    # T5): nessuna invalidazione dell'indice
    db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
        "visibile_come_partner"] = False
    dopo = await pcs.suggeriti(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
    assert [c.pseudonimo for c in dopo.items] == [
        pseudonimo(g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["T"])]
    # e il fan-out della call non la raggiunge
    esito = await partenariato_notifiche.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["notificate"] == 1
    assert {r["company_profile_id"] for r in db.tabelle["partner_notifiche_proattive"]} == {
        g.COMPANY["T"]}


async def test_servizio_fan_out_della_call_guida():
    db, sec = await scenario_guida()
    esito = await partenariato_notifiche.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["claim"] is True and esito["notificate"] == 2
    assert all(p["p_copertura"] == 2 for p in db.chiamate("fn_partner_claim_notifica"))
    chiavi = {n["dedup_key"] for n in db.tabelle["notifications"]}
    assert chiavi == {f"partner-per-te:{g.CALL_GUIDA_ID}:{g.COMPANY[n]}" for n in ("Y", "T")}
    for notifica in db.tabelle["notifications"]:
        assert "Impresa Sintetica X" not in f"{notifica['titolo']} {notifica['corpo']}"


async def test_api_per_te_di_y_e_suggeriti_di_x():
    db, sec = await scenario_guida()
    per_te = await chiama(db, sec, "Y", "GET", "/partenariati/per-te")
    suggeriti = await chiama(db, sec, "X", "GET",
                             f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti")
    assert per_te.json()["items"][0]["match"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
    assert suggeriti.json()["items"][0]["match"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
    assert suggeriti.json()["items"][0]["pseudonimo"] == pseudonimo(
        g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["Y"])
    assert g.COMPANY["Y"] not in suggeriti.text and g.COMPANY["X"] not in per_te.text


# ------------------------------------------------ parte di SERVIZIO (WP7)
#
# Lo stesso scenario sul primario finto del WP7 (le RPC della 0039): X ha un
# membro con visibilità; Y ha il piano Smart (5 candidature al mese) oppure,
# nella variante, il Gratuito (0).


async def test_wp7_y_si_candida_x_accetta_conversazione_audit_messaggio(fondo):
    db, sec = await scenario_wp7()
    per_te = await pcs.per_te(db, sec, attiva("Y"), utente("Y"))
    assert str(per_te.items[0].id) == g.CALL_GUIDA_ID
    # Y si candida alla call in cima al suo «Per te»
    inviata = await candidature.invia_candidatura(db, sec, attiva("Y"), utente("Y"),
                                                  g.CALL_GUIDA_ID, candidatura_in())
    assert inviata.quota.model_dump() == {"usate": 1, "limite": 5}
    # X la trova tra le ricevute con lo pseudonimo e la spiegazione della guida
    [ricevuta] = (await candidature.lista(db, sec, attiva("X"), utente("X"),
                                          direzione="ricevute")).items
    assert ricevuta.candidato.pseudonimo == pseudonimo(g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["Y"])
    assert ricevuta.valutazione.spiegazione == g.SPIEGAZIONE_GUIDA
    assert ricevuta.valutazione.copertura.model_dump() == {"coperti": 2, "cercati": 2}
    # X accetta: conversazione e audit nella stessa RPC, nessuna rivelazione
    accettata = await candidature.decidi(db, sec, attiva("X"), utente("X"), ricevuta.id,
                                         "accetta")
    [conv] = db.tabelle["partner_conversazioni"]
    assert str(accettata.conversazione_id) == conv["id"]
    azioni = [a["action"] for a in db.tabelle["audit_log"]]
    assert azioni == ["partenariato.candidatura_inviata", "partenariato.candidatura_accettata"]
    # Y scrive: X (titolare e membro) avvisato una volta, senza il testo
    await fondo.azzera()
    scritto = await chat.invia(db, sec, attiva("Y"), utente("Y"), conv["id"], chat.MessaggioIn(
        testo="Grazie! Possiamo partire con il laboratorio.", client_msg_id=uuid.uuid4()))
    await fondo.esegui()
    assert scritto.propria is True
    [x] = (await chat.lista_conversazioni(db, sec, attiva("X"), utente("X"))).items
    assert x.non_letti == 1 and x.controparte.pseudonimo == ricevuta.candidato.pseudonimo
    assert {n["user_id"] for n in db.righe("notifications", tipo=chat.TIPO_NUOVI_MESSAGGI)} == {
        g.OWNER["X"], MEMBRO_X}
    assert all("laboratorio" not in e["text"] for e in fondo.email)
    # il bando della guida è ora un impegno di Y (esclusività nell'indice)
    idx = await partenariato_indice.indice(db, sec)
    assert g.BANDO_GUIDA in idx.impegni[g.COMPANY["Y"]]


async def test_wp7_variante_y_gratuito_l_invito_passa_la_candidatura_no(fondo):
    db, sec = await scenario_wp7(Y=0)
    with pytest.raises(AppError) as exc:
        await candidature.invia_candidatura(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID,
                                            candidatura_in())
    assert exc.value.code == "funzione_non_inclusa"
    # X invita Y dai suoi suggeriti (con lo pseudonimo)
    suggeriti = await pcs.suggeriti(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
    invito = await candidature.invita(
        db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
        candidature.InvitoIn(pseudonimo=suggeriti.items[0].pseudonimo))
    assert invito.tipo == "invito" and invito.candidato.pseudonimo == suggeriti.items[0].pseudonimo
    # Y (Gratuito) accetta l'invito: si apre la conversazione
    [ricevuto] = (await candidature.lista(db, sec, attiva("Y"), utente("Y"),
                                          direzione="ricevute")).items
    accettato = await candidature.decidi(db, sec, attiva("Y"), utente("Y"), ricevuto.id,
                                         "accetta")
    assert accettato.stato == "accettata" and accettato.conversazione_id is not None
    assert len(db.tabelle["partner_conversazioni"]) == 1
    assert db.usate(g.OWNER["Y"]) == 0  # l'invito non consuma la quota


# ------------------------------------------------ parte di SERVIZIO (WP8)
#
# Lo stesso scenario sul primario finto del WP8 (le RPC della 0040): alla
# pubblicazione X è già nel suo consorzio (capofila, 80%); all'accettazione
# la RPC di decisione aggiunge Y (proposto, posizione P1, 20%).


async def test_wp8_punto_5_quote_70_30_verde_75_25_rosso_quota_mancante_grigio(fondo):
    db, sec = await scenario_wp8()
    # Y si candida e X accetta (WP7): Y entra nel consorzio
    inviata = await candidature.invia_candidatura(db, sec, attiva("Y"), utente("Y"),
                                                  g.CALL_GUIDA_ID, candidatura_in())
    await candidature.decidi(db, sec, attiva("X"), utente("X"), inviata.id, "accetta")
    iniziale = await consorzio.get_consorzio(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
    x, y = iniziale.membri
    assert (x.creatore, x.ruolo, str(x.quota_percentuale)) == (True, "capofila", "80.00")
    assert (y.ruolo, str(y.quota_percentuale), y.stato) == ("partner", "20.00", "proposto")
    assert y.posizione.titolo == "Ricerca e prototipazione"
    # le regole del bando confermate da X (snapshot della call)
    imposta_regole(db, regole())

    async def quote(quota_x, quota_y):
        await consorzio.aggiorna_membro(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, x.id,
            MembroAggiornaIn(ruolo="capofila", quota_percentuale=Decimal(quota_x)))
        return await consorzio.aggiorna_membro(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, y.id,
            MembroAggiornaIn(ruolo="partner", posizione_id=g.POS_P1,
                             quota_percentuale=None if quota_y is None else Decimal(quota_y)))

    def salvato() -> str:
        return db.una("partner_calls", id=g.CALL_GUIDA_ID)["validazione_esito"]

    verde = await quote("70", "30")
    assert verde.validazione.esito == "verde" == salvato(), [
        (v.id, v.esito, v.dettaglio_pubblico) for v in verde.validazione.voci]
    # Y conferma la sua partecipazione e vede lo stesso esito
    confermato = await consorzio.conferma(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID,
                                          y.id, termini(db, y.id))
    assert next(m for m in confermato.membri if m.sei_tu).stato == "confermato"
    assert confermato.validazione.esito == "verde"

    rosso = await quote("75", "25")
    assert rosso.validazione.esito == "rosso" == salvato()
    quota = next(v for v in rosso.validazione.voci if v.id == "quota:Q1")
    assert quota.esito == "rosso" and "quota massima 70%" in quota.dettaglio_pubblico
    assert [str(m) for m in quota.membri_coinvolti] == [str(x.id)]

    grigio = await quote("70", None)
    assert grigio.validazione.esito == "grigio" == salvato()
    assert next(v for v in grigio.validazione.voci if v.id == "somma_quote").esito == "grigio"
    assert all(v.esito != "rosso" for v in grigio.validazione.voci)

