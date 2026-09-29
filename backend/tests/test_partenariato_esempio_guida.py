"""Esempio guida (docs/partenariati.md §10, criterio 4) — parte PURA e, in
fondo, parte di SERVIZIO (indice, servizi, fan-out e API del WP6).

Scenario e numeri in `tests/fixtures/partenariati/esempio_guida.py`: X
pubblica la call con i requisiti A–D (copre B e D, cerca A e C) e i vincoli
di ogni membro E (sede in Calabria) ed F (costo della quota ≤ 60% del
fatturato medio); Y copre A e C; Z, W, V e U restano fuori ciascuna per il
suo motivo; T (senza bilanci) resta, sotto Y, con un'attenzione.
"""

import uuid

import pytest

from app.services import (
    partenariato_collegamenti,
    partenariato_indice,
    partenariato_notifiche,
)
from app.services import partenariato_matching as pm
from app.services import partenariato_vocabolario as voc
from app.services import partner_call_service as pcs
from app.services.partenariato_accesso import pseudonimo
from app.services.partenariato_criteri import criterio_da_json
from app.services.partenariato_matching import CoperturaOut
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariati_matching_api import attiva, chiama, utente
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
