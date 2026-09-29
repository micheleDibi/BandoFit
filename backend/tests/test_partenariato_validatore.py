"""Validatore deterministico del consorzio (WP8, docs/partenariati.md V2-V3,
Q11, Q17, Q20).

Proprietà difese:
- ogni controllo nei tre esiti (verde, rosso, grigio), con la regola di
  origine (bando con citazione se confermata, altrimenti creatore) e
  «Il bando non lo indica» → grigio;
- conteggi: entità affiliate e partner associati non contano; somma delle
  quote con tolleranza 0,01;
- esterni con i tipi dichiarati dal creatore, marcati «dichiarato»;
- indipendenza su TUTTE le coppie (certo, possibile, chiavi non calcolate,
  esterno), dettaglio sempre generico;
- regole finanziarie per membro: esatte per sé, sulla fascia per gli altri,
  con budget esatto o fascia; media pesata con il solo esito;
- proiezione per destinatario con canary: nessun valore esatto di altri
  membri né id di aziende nel JSON;
- esempio guida punto 5: 70/30 verde, 75/25 rosso «quota massima 70%»,
  quota di Y nulla grigio;
- matrice di copertura con la stessa `valuta_criterio`.
"""

import dataclasses
import json
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.errors import BadRequestError
from app.schemas.partenariato_consorzio import (
    BudgetIn,
    ConsorzioOut,
    DocumentoStatoIn,
    EsternoIn,
    MembroAggiornaIn,
    MembroOut,
)
from app.schemas.partner_call import RegoleCallSnapshot
from app.services import partenariato_validatore as pv
from app.services.partenariato_collegamenti import ChiaveCollegamento
from app.services.partenariato_criteri import profilo_candidato_da
from app.services.partner_call_gap import intervallo_budget
from tests.fixtures.partenariati import esempio_guida as eg

CALABRIA, LAZIO, LOMBARDIA = eg.CALABRIA, eg.LAZIO, eg.LOMBARDIA
# Voce confermata: citazione ritrovata su una pagina di un documento ufficiale
# (una voce della scheda del catalogo non si può confermare).
CIT = {"sezione": "D1-p2", "testo": "Il partenariato è composto da almeno due soggetti",
       "verificata": True}
BUDGET_ESATTO = intervallo_budget("2m_5m", "3100000.00")
BUDGET_FASCIA = intervallo_budget("2m_5m")


# ----------------------------------------------------------------- aiuti


def mid(n: int) -> str:
    return f"90000000-0000-4000-8000-{n:012d}"


A, B, C, D, E, F = (mid(i) for i in range(1, 7))


def chiave(tipo: str, n: int, quota=None) -> ChiaveCollegamento:
    return ChiaveCollegamento(tipo, f"{n:064x}", Decimal(quota) if quota is not None else None)


def conf(**voce) -> dict:
    return {"origine_voce": "confermata", "citazione": CIT, **voce}


def snapshot(**campi) -> RegoleCallSnapshot:
    dati = {"modalita": conf(valore="obbligatorio")}
    dati.update(campi)
    return RegoleCallSnapshot.model_validate(dati)


def quota_voce(id_="Q1", ambito="per_partner", minimo=None, massimo=None, categoria=None,
               **extra) -> dict:
    return conf(id=id_, ambito=ambito, categoria=categoria, min_percentuale=minimo,
                max_percentuale=massimo, base_calcolo="costo_totale_progetto",
                effetto_violazione="inammissibilita_progetto", **extra)


def regola_f(ambito="ciascun_partner", id_="F1") -> dict:
    return conf(**{**eg.REGOLA_F, "id": id_, "ambito": ambito})


def azienda(*, classe="piccola", regioni=(CALABRIA,), tipi=(), fatturati=None,
            registro=True):
    derived = {
        "ateco_principale": "62.01.00", "ateco_divisione": "62", "ateco_secondari": [],
        "regione_id": regioni[0] if regioni else None, "regioni_ids": list(regioni),
        "classe_dimensionale": classe,
    }
    esercizi = []
    if fatturati:
        esercizi = [
            {"anno": anno, "fatturato": f"{valore}.00", "patrimonio_netto": "900000.00",
             "totale_attivo": f"{valore * 2}.00"}
            for anno, valore in zip((2023, 2024), fatturati, strict=True)
        ]
    return profilo_candidato_da(
        company={"settore_id": None},
        derived=derived if registro else None,
        dossier={"anagrafica": {"forma_giuridica": "Società a responsabilità limitata"},
                 "flags": {}},
        profilo_partner={"tipi_soggetto": list(tipi), "competenze": [], "certificazioni": [],
                         "esperienze": []},
        esercizi=esercizi,
        storico_completo=True,
    )


def membro(id_, ruolo="partner", quota="50", *, creatore=False, profilo=None,
           collegamenti="auto", impegni=(), stato="proposto") -> pv.MembroSnapshot:
    if collegamenti == "auto":
        collegamenti = (chiave("identita", int(id_[-4:]) + 1000),)
    return pv.MembroSnapshot(
        id=id_, ruolo=ruolo, stato=stato, quota=Decimal(quota) if quota is not None else None,
        creatore=creatore, profilo=profilo if profilo is not None else azienda(),
        collegamenti=collegamenti, impegni_altrove=impegni,
    )


def esterno(id_, paese="DE", tipi=("impresa",), ruolo="partner", quota="20"):
    return pv.MembroSnapshot(
        id=id_, ruolo=ruolo, quota=Decimal(quota) if quota is not None else None,
        esterno=True, esterno_paese=paese, esterno_tipi=tuple(tipi),
    )


CALL = pv.CallConsorzio(id=eg.CALL_GUIDA_ID)


def valida(regole, membri, *, call=CALL, budget=BUDGET_ESATTO):
    return pv.valida_consorzio(regole, call, membri, budget=budget)


def voce(v: pv.Validazione, id_: str) -> pv.Voce:
    trovate = [x for x in v.voci if x.id == id_]
    assert trovate, f"voce {id_} assente: {[x.id for x in v.voci]}"
    return trovate[0]


def vista(v, id_, viewer=None, creatore=False):
    out = pv.proietta_validazione(v, viewer_membro_id=viewer, creatore=creatore)
    return next(x for x in out.voci if x.id == id_)


def pv_uuid(valore: str) -> UUID:
    return UUID(valore)


def coppia(q1="50", q2="50", **extra):
    return [membro(A, "capofila", q1, creatore=True), membro(B, "partner", q2, **extra)]


# ------------------------------------------------- esempio guida (punto 5)

REGOLE_GUIDA = snapshot(
    partner_min=conf(valore=2),
    composizione=[
        conf(id="C1", tipo_soggetto="organismo_ricerca", minimo=1, ruolo="qualsiasi"),
        conf(id="C2", tipo_soggetto="pmi", minimo=1, ruolo="qualsiasi"),
    ],
    quote=[quota_voce(minimo=10, massimo=70)],
)


# Requisiti COERENTI con REGOLE_GUIDA: un requisito finanziario nasce solo da
# una regola confermata dello snapshot (partner_call_gap,
# fn_partner_call_regola_ok), e il punto 5 non ha la regola F.
REQUISITI_SENZA_F = [r for r in eg.REQUISITI_GUIDA if r["etichetta"] != "F"]


def guida(quota_x, quota_y, *, regole=REGOLE_GUIDA, requisiti=REQUISITI_SENZA_F):
    call = pv.call_consorzio_da(eg.CALL_GUIDA, requisiti)
    membri = [
        pv.MembroSnapshot(
            id=A, ruolo="capofila", quota=Decimal(quota_x), creatore=True,
            profilo=eg.profilo("X"), collegamenti=(chiave("identita", 1),),
        ),
        pv.MembroSnapshot(
            id=B, ruolo="partner", quota=None if quota_y is None else Decimal(quota_y),
            profilo=eg.profilo("Y"), collegamenti=(chiave("identita", 2),),
        ),
    ]
    budget = intervallo_budget(eg.CALL_GUIDA["budget_fascia"], eg.CALL_GUIDA["budget_progetto_eur"])
    return pv.valida_consorzio(regole, call, membri, budget=budget)


def test_esempio_guida_70_30_verde():
    v = guida("70", "30")
    assert v.esito == "verde", [(x.id, x.esito, x.dettaglio_pubblico) for x in v.voci]
    assert {x.esito for x in v.voci} == {"verde"}
    # Il requisito E (Calabria, tutte le sedi) si valuta per ogni membro.
    assert "vincolo_membro:" + eg.REQ["E"] in {x.id for x in v.voci}
    # L'organismo di ricerca di Y è dichiarato nel profilo partner.
    assert voce(v, "composizione:C1").dichiarato is True
    assert voce(v, "composizione:C2").dichiarato is False


def test_esempio_guida_con_la_regola_f_70_30_non_e_verde():
    """Incoerenza del piano: con la regola F (costo della quota ≤ 60% del
    fatturato medio) confermata, 70/30 sul budget della guida (3,1 M€) non è
    verde. X: 2,17 M€ / 3,2 M€ = 0,68."""
    con_f = snapshot(**{**REGOLE_GUIDA.model_dump(mode="json", exclude={"versione"}),
                        "regole_finanziarie": [regola_f()]})
    v = guida("70", "30", regole=con_f, requisiti=eg.REQUISITI_GUIDA)
    f = voce(v, "finanziaria:F1")
    assert f.per_membro[A].esito_proprio == "rosso"
    assert v.esito == "rosso"
    # Il requisito F è la stessa regola dello snapshot: una voce sola.
    assert "vincolo_membro:" + eg.REQ["F"] not in {x.id for x in v.voci}


def test_requisito_finanziario_fuori_dallo_snapshot_si_valuta_comunque():
    """Difesa in profondità: un requisito finanziario «di ogni membro» la
    cui regola non è nello snapshot non si ignora (in produzione non capita:
    fn_partner_call_regola_ok e il servizio lo impediscono)."""
    v = guida("70", "30", requisiti=eg.REQUISITI_GUIDA)
    f = voce(v, "vincolo_membro:" + eg.REQ["F"])
    assert f.codice == "regola_finanziaria" and f.regola.fonte == "creatore"
    assert f.per_membro[A].esito_proprio == "rosso"  # X: 0,68 > 0,6
    assert v.esito == "rosso"


def test_esempio_guida_75_25_rosso_quota_massima():
    v = guida("75", "25")
    assert v.esito == "rosso"
    q = voce(v, "quota:Q1")
    assert q.esito == "rosso"
    assert "quota massima 70%" in q.dettaglio_pubblico
    assert q.membri_coinvolti == (A,)


def test_esempio_guida_quota_y_nulla_grigio():
    v = guida("70", None)
    assert v.esito == "grigio"
    assert voce(v, "somma_quote").esito == "grigio"
    assert voce(v, "somma_quote").membri_coinvolti == (B,)
    assert voce(v, "quota:Q1").esito == "grigio"
    assert all(x.esito != "rosso" for x in v.voci)


# ------------------------------------------------------------ esito complessivo


def test_esito_complessivo():
    assert pv.esito_complessivo(["verde", "grigio", "rosso"]) == "rosso"
    assert pv.esito_complessivo(["verde", "grigio"]) == "grigio"
    assert pv.esito_complessivo(["verde", "verde"]) == "verde"
    assert pv.esito_complessivo([]) == "verde"
    v = valida(snapshot(partner_min=conf(valore=3)), coppia())
    assert pv.esito_complessivo(v.voci) == "rosso"


def test_regole_assenti_voce_grigia():
    v = valida(None, coppia())
    assert voce(v, "regole").esito == "grigio"
    assert voce(v, "regole").regola is None
    # I controlli di coerenza si fanno comunque.
    assert voce(v, "somma_quote").esito == "verde"
    assert voce(v, "indipendenza").esito == "verde"
    # Anche lo snapshot in forma jsonb illeggibile vale come assente.
    assert voce(valida({"versione": 99}, coppia()), "regole").esito == "grigio"


def test_membri_usciti_ignorati():
    membri = [*coppia("60", "40"), membro(C, "partner", "30", stato="uscito")]
    v = valida(snapshot(partner_max=conf(valore=2)), membri)
    assert voce(v, "somma_quote").esito == "verde"
    assert voce(v, "numero_partner:max").esito == "verde"


# ------------------------------------------------------------ numero di partner


def test_numero_partner_tre_esiti_ed_esclusi_affiliati_e_associati():
    membri = [
        membro(A, "capofila", "40", creatore=True),
        membro(B, "partner", "30"),
        membro(C, "affiliated_entity", "20"),
        membro(D, "associated_partner", "10"),
    ]
    regole = snapshot(partner_min=conf(valore=3), partner_max=conf(valore=3))
    v = valida(regole, membri)
    assert voce(v, "numero_partner:min").esito == "rosso"  # 2 contano, non 4
    assert voce(v, "numero_partner:max").esito == "verde"
    assert "2 partner" in voce(v, "numero_partner:min").dettaglio_pubblico
    membri.append(membro(E, "partner", "0.01"))
    v = valida(regole, membri)
    assert voce(v, "numero_partner:min").esito == "verde"
    assert voce(v, "numero_partner:max").esito == "verde"
    membri.append(membro(F, "partner", "0.01"))
    assert voce(valida(regole, membri), "numero_partner:max").esito == "rosso"
    # Nessun numero nel bando → solo il controllo di coerenza (almeno 2).
    conteggi = [x for x in valida(snapshot(), membri).voci if x.codice == "numero_partner"]
    assert [(x.id, x.esito, x.regola) for x in conteggi] == [
        ("numero_partner:coerenza", "verde", None)]


@pytest.mark.parametrize("regole", [None, "senza_minimo"])
def test_numero_partner_coerenza_senza_minimo(regole):
    """Senza un minimo nel bando (o senza regole) un consorzio col solo
    creatore non è verde: servono almeno 2 tra capofila e partner."""
    snap = None if regole is None else snapshot(partner_max=conf(valore=5))
    solo = [membro(A, "capofila", "100", creatore=True)]
    v = valida(snap, solo)
    x = voce(v, "numero_partner:coerenza")
    assert x.esito == "rosso" and x.regola is None
    assert "c'è 1 partner" in x.dettaglio_pubblico
    assert v.esito == "rosso"
    con_associato = [*solo, membro(B, "associated_partner", None)]
    assert voce(valida(snap, con_associato), "numero_partner:coerenza").esito == "rosso"
    assert voce(valida(snap, coppia()), "numero_partner:coerenza").esito == "verde"
    # Con un minimo nel bando vale quello, non la coerenza.
    ids = {x.id for x in valida(snapshot(partner_min=conf(valore=3)), solo).voci}
    assert "numero_partner:min" in ids and "numero_partner:coerenza" not in ids


def test_regola_di_origine_bando_o_creatore():
    regole = snapshot(
        partner_min=conf(valore=2),
        partner_max={"origine_voce": "modificata", "citazione": CIT, "valore": 5},
    )
    v = valida(regole, coppia())
    minimo, massimo = voce(v, "numero_partner:min"), voce(v, "numero_partner:max")
    assert minimo.regola.fonte == "bando"
    assert minimo.regola.citazione.testo == CIT["testo"]
    assert massimo.regola.fonte == "creatore" and massimo.regola.citazione is None
    out = vista(v, "numero_partner:min", A, True)
    assert out.regola.fonte == "bando" and out.regola.citazione.sezione == "D1-p2"
    assert vista(v, "somma_quote").regola is None  # controllo di coerenza


# ------------------------------------------------------------ composizione


def test_composizione_tre_esiti():
    odr = azienda(tipi=("organismo_ricerca",))
    regole = snapshot(composizione=[
        conf(id="C1", tipo_soggetto="organismo_ricerca", minimo=1, ruolo="qualsiasi"),
        conf(id="C2", tipo_soggetto="grande_impresa", minimo=1, ruolo="qualsiasi"),
        conf(id="C3", tipo_soggetto="organismo_ricerca", minimo=1, ruolo="partner"),
    ])
    membri = [membro(A, "capofila", "60", creatore=True, profilo=odr), membro(B, "partner", "40")]
    v = valida(regole, membri)
    assert voce(v, "composizione:C1").esito == "verde"
    assert voce(v, "composizione:C1").membri_coinvolti == (A,)
    assert voce(v, "composizione:C2").esito == "rosso"  # nessuna grande impresa dal registro
    # L'organismo di ricerca è il capofila: se conta tra i «partner» dipende dal bando.
    assert voce(v, "composizione:C3").esito == "grigio"
    assert "da verificare" in voce(v, "composizione:C3").dettaglio_pubblico


def test_composizione_bando_non_indica_quanti():
    regole = snapshot(composizione=[
        conf(id="C1", tipo_soggetto="pmi", ruolo="qualsiasi"),
    ])
    x = voce(valida(regole, coppia()), "composizione:C1")
    assert x.esito == "grigio"
    assert pv.TESTO_NON_INDICA in x.dettaglio_pubblico
    assert x.regola.fonte == "bando"


def test_composizione_massimo_ruolo_territorio_e_altro():
    regole = snapshot(composizione=[
        conf(id="C1", tipo_soggetto="pmi", massimo=1, ruolo="qualsiasi"),
        conf(id="C2", tipo_soggetto="pmi", minimo=1, ruolo="qualsiasi", regioni=[LOMBARDIA]),
        conf(id="C3", tipo_soggetto="altro", tipo_soggetto_testo="Ente gestore", minimo=1,
             ruolo="qualsiasi"),
        conf(id="C4", tipo_soggetto="impresa", minimo=1, ruolo="affiliato"),
    ])
    v = valida(regole, coppia())
    assert voce(v, "composizione:C1").esito == "rosso"  # due PMI, al massimo una
    assert voce(v, "composizione:C2").esito == "rosso"  # nessuna sede in Lombardia
    assert "vincolo territoriale" in voce(v, "composizione:C2").titolo
    assert voce(v, "composizione:C3").esito == "grigio"
    assert "Ente gestore" in voce(v, "composizione:C3").titolo
    assert voce(v, "composizione:C4").esito == "rosso"  # nessuna entità affiliata


def test_composizione_esterni_con_tipi_dichiarati():
    regole = snapshot(composizione=[
        conf(id="C1", tipo_soggetto="organismo_ricerca", minimo=1, ruolo="qualsiasi"),
        conf(id="C2", tipo_soggetto="pmi", minimo=2, ruolo="qualsiasi", paesi=["DE"]),
        conf(id="C3", tipo_soggetto="pmi", minimo=1, ruolo="qualsiasi", paesi=["Germania"]),
    ])
    membri = [
        membro(A, "capofila", "60", creatore=True),
        esterno(B, "DE", ("organismo_ricerca", "pmi"), quota="40"),
    ]
    v = valida(regole, membri)
    c1 = voce(v, "composizione:C1")
    assert c1.esito == "verde" and c1.dichiarato is True
    assert "dichiarato" in c1.dettaglio_pubblico
    # A è in Italia (registro), B in Germania: una sola PMI tedesca.
    assert voce(v, "composizione:C2").esito == "rosso"
    # Paese scritto per nome: non si riconosce, resta da verificare.
    assert voce(v, "composizione:C3").esito == "grigio"


# ------------------------------------------------------------ quote


@pytest.mark.parametrize("quote,esito", [
    (("33.33", "33.33", "33.33"), "verde"),  # 99,99: dentro la tolleranza
    (("50.01", "50.00"), "verde"),  # 100,01
    (("33.33", "33.33", "33.32"), "rosso"),  # 99,98
    (("50.02", "50.00"), "rosso"),
    (("60", "40"), "verde"),
])
def test_somma_quote_tolleranza(quote, esito):
    ruoli = ["capofila", "partner", "partner"]
    membri = [membro(mid(i + 1), ruoli[i], q, creatore=i == 0) for i, q in enumerate(quote)]
    x = voce(valida(None, membri), "somma_quote")
    assert x.esito == esito
    if esito == "rosso":
        assert "deve essere 100%" in x.dettaglio_pubblico


def test_quota_per_partner_esclude_affiliati_e_associati():
    regole = snapshot(quote=[quota_voce(minimo=10, massimo=60)])
    membri = [
        membro(A, "capofila", "55", creatore=True),
        membro(B, "partner", "40"),
        membro(C, "affiliated_entity", "3"),
        membro(D, "associated_partner", "2"),
    ]
    x = voce(valida(regole, membri), "quota:Q1")
    assert x.esito == "verde"
    assert set(x.per_membro) == {A, B}


def test_quota_per_categoria_tre_esiti():
    regole = snapshot(quote=[quota_voce(ambito="per_categoria", categoria="pmi", minimo=30)])
    senza_registro = azienda(registro=False)
    v = valida(regole, [membro(A, "capofila", "80", creatore=True,
                               profilo=azienda(classe="grande")),
                        membro(B, "partner", "20")])
    assert voce(v, "quota:Q1").esito == "rosso"  # PMI al 20%
    v = valida(regole, [membro(A, "capofila", "60", creatore=True),
                        membro(B, "partner", "40", profilo=azienda(classe="grande"))])
    assert voce(v, "quota:Q1").esito == "verde"
    v = valida(regole, [membro(A, "capofila", "20", creatore=True),
                        membro(B, "partner", "20", profilo=azienda(classe="grande")),
                        membro(C, "partner", "60", profilo=senza_registro)])
    x = voce(v, "quota:Q1")
    assert x.esito == "grigio"  # dimensione di C non nota: tra 20% e 80%
    assert "fino a 80%" in x.dettaglio_pubblico
    # Esterno dichiarato PMI: conta, marcato «dichiarato».
    v = valida(regole, [membro(A, "capofila", "70", creatore=True,
                               profilo=azienda(classe="grande")),
                        esterno(B, tipi=("pmi",), quota="30")])
    assert voce(v, "quota:Q1").esito == "verde"
    assert voce(v, "quota:Q1").dichiarato is True


def test_quota_capofila_tre_esiti():
    regole = snapshot(quote=[quota_voce(ambito="capofila", massimo=50)])
    assert voce(valida(regole, coppia("40", "60")), "quota:Q1").esito == "verde"
    rosso = voce(valida(regole, coppia("60", "40")), "quota:Q1")
    assert rosso.esito == "rosso" and rosso.membri_coinvolti == (A,)
    senza_capofila = [membro(A, "partner", "50", creatore=True), membro(B, "partner", "50")]
    assert voce(valida(regole, senza_capofila), "quota:Q1").esito == "grigio"


@pytest.mark.parametrize("ambito", ["per_partner", "capofila", "per_categoria"])
def test_quota_che_costa_solo_la_maggiorazione_e_grigia(ambito):
    """Una regola che fa perdere solo una maggiorazione non rende il consorzio
    inammissibile: dove sarebbe rossa la voce è grigia, e lo dice."""
    campi = {"categoria": "pmi"} if ambito == "per_categoria" else {}
    limiti = {"massimo": 50} if ambito == "capofila" else {"minimo": 30}
    rossa = quota_voce(ambito=ambito, **limiti, **campi)
    grigia = {**rossa, "effetto_violazione": "perdita_maggiorazione"}
    membri = [membro(A, "capofila", "80", creatore=True, profilo=azienda(classe="grande")),
              membro(B, "partner", "20")]
    assert voce(valida(snapshot(quote=[rossa]), membri), "quota:Q1").esito == "rosso"
    v = valida(snapshot(quote=[grigia]), membri)
    x = voce(v, "quota:Q1")
    assert x.esito == "grigio" and "maggiorazione" in x.dettaglio_pubblico
    assert all(y.esito != "rosso" for y in v.voci)
    for viewer, creatore in ((A, True), (B, False)):
        assert vista(v, "quota:Q1", viewer, creatore).esito == "grigio"


def test_partner_associati_fuori_da_somma_e_quote_per_categoria():
    """I partner associati non ricevono budget: la loro quota non entra nella
    somma né nelle quote per categoria, e senza quota non la bloccano."""
    membri = [membro(A, "capofila", "60", creatore=True, profilo=azienda(classe="grande")),
              membro(B, "partner", "40", profilo=azienda(classe="grande")),
              membro(C, "associated_partner", None)]
    assert voce(valida(None, membri), "somma_quote").esito == "verde"
    con_quota = [*membri[:2], membro(C, "associated_partner", "1")]
    assert voce(valida(None, con_quota), "somma_quote").esito == "verde"
    regole = snapshot(quote=[quota_voce(ambito="per_categoria", categoria="pmi", minimo=10)])
    pmi_associata = [*membri[:2], membro(C, "associated_partner", "20")]
    x = voce(valida(regole, pmi_associata), "quota:Q1")
    assert x.esito == "rosso" and C not in x.membri_coinvolti


def test_testi_al_singolare():
    v = valida(snapshot(vincoli=[conf(id="V2", tipo="paesi_distinti", descrizione="Un paese",
                                      parametro=1, momento="domanda")]),
               [membro(A, "capofila", "60", creatore=True),
                membro(B, "partner", None, profilo=azienda(registro=False))])
    assert "Quota mancante per 1 membro:" in voce(v, "somma_quote").dettaglio_pubblico
    paesi = voce(v, "paesi:V2").dettaglio_pubblico
    assert "almeno 1 paese." in paesi and "non noto per 1 membro" in paesi
    regole, membri = trio()
    per_b = vista(valida(regole, [membri[0], membri[2]]), "finanziaria:F1", A, True)
    assert "In regola 1 membro su 2" in per_b.dettaglio_pubblico


# ------------------------------------------------------------ indipendenza


def test_indipendenza_su_tutte_le_coppie():
    comune = chiave("gruppo", 777)
    membri = [
        membro(A, "capofila", "40", creatore=True, collegamenti=(chiave("identita", 1),)),
        membro(B, "partner", "30", collegamenti=(chiave("identita", 2), comune)),
        membro(C, "partner", "30", collegamenti=(chiave("identita", 3), comune)),
    ]
    regole = snapshot(vincoli=[conf(id="V1", tipo="indipendenza", descrizione="Partner "
                                    "indipendenti", momento="domanda")])
    x = voce(valida(regole, membri), "indipendenza")
    assert x.esito == "rosso"
    # La coppia collegata non comprende il creatore: si controllano TUTTE le coppie.
    assert x.membri_coinvolti == (B, C)
    assert x.regola.fonte == "bando"
    # Dettaglio generico: mai il tipo di chiave né quale socio.
    assert "gruppo" not in x.dettaglio_pubblico.lower()


def test_indipendenza_certo_senza_regola_grigio_il_bando_non_lo_indica():
    comune = chiave("identita", 5)
    membri = [membro(A, "capofila", creatore=True, collegamenti=(comune,)),
              membro(B, collegamenti=(comune,))]
    x = voce(valida(snapshot(), membri), "indipendenza")
    assert x.esito == "grigio"
    assert pv.TESTO_NON_INDICA in x.dettaglio_pubblico
    assert x.regola is None


def test_indipendenza_possibile_marker_mancante_ed_esterno():
    esponente = chiave("esponente", 9)
    possibile = [membro(A, "capofila", creatore=True,
                        collegamenti=(chiave("identita", 1), esponente)),
                 membro(B, collegamenti=(chiave("identita", 2), esponente))]
    x = voce(valida(None, possibile), "indipendenza")
    assert x.esito == "grigio" and "verifica con visura" in x.dettaglio_pubblico
    assert x.membri_coinvolti == (A, B)

    senza_marker = [membro(A, "capofila", creatore=True), membro(B, collegamenti=None)]
    x = voce(valida(None, senza_marker), "indipendenza")
    assert x.esito == "grigio" and x.membri_coinvolti == (B,)
    assert "non ancora verificati" in x.dettaglio_pubblico

    con_esterno = [membro(A, "capofila", creatore=True), esterno(B)]
    x = voce(valida(None, con_esterno), "indipendenza")
    assert x.esito == "grigio" and "visura" in x.dettaglio_pubblico
    assert x.membri_coinvolti == (B,)

    # Le entità affiliate sono collegate per definizione al beneficiario: non si
    # controllano. I partner associati sì (tutte le coppie in piattaforma).
    affiliato = [membro(A, "capofila", creatore=True, collegamenti=(chiave("identita", 4),)),
                 membro(B, "affiliated_entity", collegamenti=(chiave("identita", 4),))]
    assert voce(valida(None, affiliato), "indipendenza").esito == "verde"
    vincolo = snapshot(vincoli=[conf(id="V1", tipo="indipendenza", descrizione="Partner "
                                     "indipendenti", momento="domanda")])
    associato = [membro(A, "capofila", creatore=True, collegamenti=(chiave("identita", 4),)),
                 membro(B, "associated_partner", None, collegamenti=(chiave("identita", 4),))]
    x = voce(valida(vincolo, associato), "indipendenza")
    assert x.esito == "rosso" and x.membri_coinvolti == (A, B)


def test_indipendenza_riservata_nella_proiezione():
    comune = chiave("identita", 5)
    membri = [membro(A, "capofila", "40", creatore=True),
              membro(B, "partner", "30", collegamenti=(comune,)),
              membro(C, "partner", "30", collegamenti=(comune,))]
    v = valida(snapshot(), membri)
    assert vista(v, "indipendenza", A, True).membri_coinvolti == [pv_uuid(B), pv_uuid(C)]
    per_b = vista(v, "indipendenza", B)
    assert per_b.membri_coinvolti == [pv_uuid(B)]
    assert "collegamento" in per_b.dettaglio_privato
    assert vista(v, "indipendenza", A, True).dettaglio_privato is None
    # Un membro non coinvolto non vede chi è collegato.
    membri.append(membro(D, "partner", "0.01"))
    v = valida(snapshot(), membri)
    assert vista(v, "indipendenza", D).membri_coinvolti == []


# ------------------------------------------------------------ paesi distinti


def test_paesi_distinti_tre_esiti():
    vincolo = conf(id="V2", tipo="paesi_distinti", descrizione="Tre paesi", parametro=3,
                   momento="domanda")
    regole = snapshot(vincoli=[vincolo])
    tre = [membro(A, "capofila", "40", creatore=True), esterno(B, "DE", quota="30"),
           esterno(C, "FR", quota="30")]
    x = voce(valida(regole, tre), "paesi:V2")
    assert x.esito == "verde" and "DE, FR, IT" in x.dettaglio_pubblico
    due = [membro(A, "capofila", "40", creatore=True), membro(B, "partner", "30"),
           esterno(C, "DE", quota="30")]
    assert voce(valida(regole, due), "paesi:V2").esito == "rosso"
    ignoto = [membro(A, "capofila", "40", creatore=True), esterno(B, "DE", quota="30"),
              membro(C, "partner", "30", profilo=azienda(registro=False))]
    x = voce(valida(regole, ignoto), "paesi:V2")
    assert x.esito == "grigio" and x.membri_coinvolti == (C,)
    # Affiliati e associati non contano per i paesi.
    con_affiliato = [*due[:2], esterno(C, "DE", ruolo="affiliated_entity", quota="30")]
    assert voce(valida(regole, con_affiliato), "paesi:V2").esito == "rosso"
    senza_numero = snapshot(vincoli=[{**vincolo, "parametro": None}])
    x = voce(valida(senza_numero, tre), "paesi:V2")
    assert x.esito == "grigio" and pv.TESTO_NON_INDICA in x.dettaglio_pubblico


# ------------------------------------------------------ regole finanziarie

# F: costo della quota / fatturato medio (2 esercizi) ≤ 0,6. Budget esatto
# 3,1 M€. A (capofila, 40%): fatturato medio 12 M€ → verde anche sulla fascia.
# B (partner, 30%): 1,6 M€ → 930 k€ / 1,6 M€ = 0,58 (verde sul valore esatto),
# ma sulla fascia 500k_2m l'esito dipende dal punto (grigio). C (partner,
# 30%): 200 k€ → rosso su tutta la fascia.
FATT_A, FATT_B, FATT_C = (11_900_000, 12_100_000), (1_550_000, 1_650_000), (190_000, 210_000)


def trio(ambito="ciascun_partner", quote=("40", "30", "30")):
    membri = [
        membro(A, "capofila", quote[0], creatore=True, profilo=azienda(fatturati=FATT_A)),
        membro(B, "partner", quote[1], profilo=azienda(fatturati=FATT_B)),
        membro(C, "partner", quote[2], profilo=azienda(fatturati=FATT_C)),
    ]
    return snapshot(regole_finanziarie=[regola_f(ambito)]), membri


def test_regola_per_partner_esatta_per_se_e_sulla_fascia_per_gli_altri():
    regole, membri = trio()
    v = valida(regole, membri)
    x = voce(v, "finanziaria:F1")
    assert x.per_membro[A] == pv.EsitoMembro("verde", "verde")
    assert x.per_membro[B] == pv.EsitoMembro("grigio", "verde")
    assert x.per_membro[C] == pv.EsitoMembro("rosso", "rosso")
    assert x.regola.fonte == "bando"

    per_a = vista(v, "finanziaria:F1", A, True)
    esiti_a = {str(e.membro_id): e.esito for e in per_a.esiti_membri}
    assert esiti_a == {A: "verde", B: "grigio", C: "rosso"}
    assert per_a.esito == "rosso"
    assert [str(m) for m in per_a.membri_coinvolti] == [B, C]
    assert "12.000.000" in per_a.dettaglio_privato  # il proprio valore esatto

    per_b = vista(v, "finanziaria:F1", B)
    esiti_b = {str(e.membro_id): e.esito for e in per_b.esiti_membri}
    assert esiti_b == {A: "verde", B: "verde", C: "rosso"}
    assert "1.600.000" in per_b.dettaglio_privato
    assert "In regola 2 membri su 3" in per_b.dettaglio_pubblico


def test_regola_per_partner_con_budget_in_fascia():
    regole, membri = trio()
    x = voce(valida(regole, membri, budget=BUDGET_FASCIA), "finanziaria:F1")
    # 30% di 2-5 M€ = 600 k€-1,5 M€ su 1,6 M€: dipende dal budget anche per B.
    assert x.per_membro[B] == pv.EsitoMembro("grigio", "grigio")
    assert "budget" in x.dettaglio_privato_per[B]
    # Senza budget il costo della quota manca: grigio per tutti.
    x = voce(valida(regole, membri, budget=None), "finanziaria:F1")
    assert {e.esito for e in x.per_membro.values()} == {"grigio"}


def test_regola_capofila_totale_ed_esterni():
    regole = snapshot(regole_finanziarie=[regola_f("capofila", "F2"),
                                          regola_f("partenariato_totale", "F3"),
                                          regola_f("ciascun_partner", "F4")])
    membri = [membro(A, "capofila", "70", creatore=True, profilo=azienda(fatturati=FATT_A)),
              membro(B, "partner", "10", profilo=azienda(fatturati=FATT_C)),
              esterno(C, quota="20")]
    v = valida(regole, membri)
    capofila = voce(v, "finanziaria:F2")
    assert set(capofila.per_membro) == {A} and capofila.esito == "verde"
    assert voce(v, "finanziaria:F3").esito == "grigio"
    assert "a mano" in voce(v, "finanziaria:F3").dettaglio_pubblico
    # L'esterno non ha bilanci: grigio, mai verde.
    assert voce(v, "finanziaria:F4").per_membro[C] == pv.EsitoMembro("grigio", "grigio")


def test_media_pesata_solo_esito():
    regole, membri = trio("media_pesata_quote")
    v = valida(regole, membri)
    x = voce(v, "finanziaria:F1")
    assert x.codice == "media_pesata" and x.esito == "grigio"  # esiti discordi
    for viewer, creatore in ((A, True), (B, False), (None, False)):
        out = vista(v, "finanziaria:F1", viewer, creatore)
        assert out.esiti_membri == [] and out.membri_coinvolti == []
        assert out.esito == "grigio"
    assert "contributo" in vista(v, "finanziaria:F1", B).dettaglio_privato

    # Tutti in regola sulla fascia → verde; nessuno → rosso.
    verdi = [membro(A, "capofila", "50", creatore=True, profilo=azienda(fatturati=FATT_A)),
             membro(D, "partner", "50", profilo=azienda(fatturati=FATT_A))]
    assert voce(valida(regole, verdi), "finanziaria:F1").esito == "verde"
    rossi = [membro(A, "capofila", "50", creatore=True, profilo=azienda(fatturati=FATT_C)),
             membro(D, "partner", "50", profilo=azienda(fatturati=FATT_C))]
    assert voce(valida(regole, rossi), "finanziaria:F1").esito == "rosso"
    # Una quota mancante: la media non si calcola.
    senza = [verdi[0], membro(D, "partner", None, profilo=azienda(fatturati=FATT_A))]
    x = voce(valida(regole, senza), "finanziaria:F1")
    assert x.esito == "grigio" and "Quota mancante" in x.dettaglio_pubblico


def test_esito_persistito_e_quello_del_creatore():
    # Il creatore B-like: 30% su 1,6 M€ → verde per sé, grigio sulla fascia.
    regole = snapshot(regole_finanziarie=[regola_f()])
    membri = [
        membro(A, "capofila", "30", creatore=True, profilo=azienda(fatturati=FATT_B)),
        membro(B, "partner", "70", profilo=azienda(fatturati=FATT_A)),
    ]
    v = valida(regole, membri)
    assert v.esito == "verde"  # vista del creatore, da persistere
    assert pv.esito_complessivo(v.voci) == "grigio"  # vista neutra (tutti sulla fascia)
    assert pv.proietta_validazione(v, viewer_membro_id=B, creatore=False).esito == "grigio"
    assert pv.proietta_validazione(v, viewer_membro_id=A, creatore=True).esito == "verde"


# ------------------------------------------------------------ esclusività


def test_esclusivita_tre_esiti_e_riservata():
    esclusiva = pv.CallConsorzio(id="c", esclusivita=True)
    membri = [membro(A, "capofila", creatore=True), membro(B, impegni=(False,))]
    x = voce(valida(None, membri, call=esclusiva), "esclusivita")
    assert x.esito == "rosso" and x.membri_coinvolti == (B,)
    assert x.regola.fonte == "creatore"
    # Call non esclusiva, ma l'altra lo è: la regola è simmetrica.
    x = voce(valida(None, [membri[0], membro(B, impegni=(True,))]), "esclusivita")
    assert x.esito == "rosso" and x.regola is None
    # Nessuna delle due è esclusiva: nessuna voce.
    v = valida(None, [membri[0], membro(B, impegni=(False,))])
    assert "esclusivita" not in {y.id for y in v.voci}
    # Call esclusiva senza impegni: verde; con un esterno: grigio; impegni non noti: grigio.
    assert voce(valida(None, coppia(), call=esclusiva), "esclusivita").esito == "verde"
    x = voce(valida(None, [membri[0], esterno(B)], call=esclusiva), "esclusivita")
    assert x.esito == "grigio" and x.membri_coinvolti == (B,)
    x = voce(valida(None, [membri[0], membro(B, impegni=None)], call=esclusiva), "esclusivita")
    assert x.esito == "grigio"
    # Vincolo del bando: regola dal bando anche con la call non esclusiva.
    regole = snapshot(vincoli=[conf(id="V9", tipo="esclusivita_partenariato",
                                    descrizione="Un solo partenariato", momento="domanda")])
    x = voce(valida(regole, [membri[0], membro(B, impegni=(False,))]), "esclusivita")
    assert x.esito == "rosso" and x.regola.fonte == "bando"
    # Proiezione: solo il creatore vede chi; l'interessato vede sé stesso.
    tre = [membro(A, "capofila", creatore=True), membro(B, impegni=(False,)), membro(C)]
    v = valida(None, tre, call=esclusiva)
    assert vista(v, "esclusivita", A, True).membri_coinvolti == [pv_uuid(B)]
    assert vista(v, "esclusivita", C).membri_coinvolti == []
    assert vista(v, "esclusivita", B).membri_coinvolti == [pv_uuid(B)]
    assert "impegno" in vista(v, "esclusivita", B).dettaglio_privato
    assert vista(v, "esclusivita", C).dettaglio_privato is None


# ------------------------------------------------------ vincoli di ogni membro


def requisito(id_, criterio, *, ambito="ogni_membro", etichetta="E", origine="manuale",
              citazione=None, cercato=False):
    riga = {"id": id_, "etichetta": etichetta, "testo": "Sede in Calabria", "criterio": criterio,
            "ambito": ambito, "cercato": cercato, "origine": origine, "citazione": citazione,
            "ordine": 0}
    return pv.requisito_da_riga(riga)


CALABRIA_OGNI = {"tipo": "regione", "regioni_ids": [CALABRIA], "modalita": "sede_attuale"}


def test_vincoli_ogni_membro_tutte_le_sedi_e_tre_esiti():
    req = requisito(mid(50), CALABRIA_OGNI, origine="bando_partenariato", citazione=CIT)
    call = pv.CallConsorzio(id="c", requisiti=(req,))
    unita_locale = azienda(regioni=(LAZIO, CALABRIA))  # sede legale nel Lazio
    membri = [membro(A, "capofila", "60", creatore=True), membro(B, "partner", "40",
                                                                  profilo=unita_locale)]
    x = voce(valida(None, membri, call=call), f"vincolo_membro:{mid(50)}")
    assert x.esito == "verde"  # basta un'unità locale in Calabria
    assert x.regola.fonte == "bando"
    solo_lombardia = [membri[0], membro(B, profilo=azienda(regioni=(LOMBARDIA,)))]
    x = voce(valida(None, solo_lombardia, call=call), f"vincolo_membro:{mid(50)}")
    assert x.esito == "rosso" and x.membri_coinvolti == (B,)
    con_esterno = [membri[0], esterno(B)]
    assert voce(valida(None, con_esterno, call=call), f"vincolo_membro:{mid(50)}").esito == (
        "grigio"
    )
    # I partner associati non ricevono budget: il vincolo non si applica.
    con_associato = [membri[0], membro(B, "associated_partner",
                                       profilo=azienda(regioni=(LOMBARDIA,)))]
    x = voce(valida(None, con_associato, call=call), f"vincolo_membro:{mid(50)}")
    assert x.esito == "verde" and set(x.per_membro) == {A}


def test_vincoli_ogni_membro_esclusi_i_consorzio_e_i_finanziari():
    consorzio = requisito(mid(51), CALABRIA_OGNI, ambito="consorzio")
    finanziario = requisito(mid(52), {"tipo": "regola_finanziaria", "regola": eg.REGOLA_F})
    manuale = requisito(mid(53), None)
    call = pv.CallConsorzio(id="c", requisiti=(consorzio, finanziario, manuale))
    con_f = snapshot(regole_finanziarie=[regola_f()])
    ids = {x.id for x in valida(con_f, coppia(), call=call).voci}
    assert f"vincolo_membro:{mid(51)}" not in ids
    # La stessa regola è nello snapshot: si valuta una volta sola (F1).
    assert f"vincolo_membro:{mid(52)}" not in ids and "finanziaria:F1" in ids
    # Una regola che nello snapshot non c'è (o diversa) non si salta.
    diversa = snapshot(regole_finanziarie=[conf(**{**eg.REGOLA_F, "soglia": "0.5"})])
    for regole in (None, diversa):
        ids = {x.id for x in valida(regole, coppia(), call=call).voci}
        assert f"vincolo_membro:{mid(52)}" in ids
    # Requisito descritto a testo: da verificare a mano, regola del creatore.
    x = voce(valida(None, coppia(), call=call), f"vincolo_membro:{mid(53)}")
    assert x.esito == "grigio" and x.regola.fonte == "creatore"


def test_vincoli_testuali_del_bando_da_verificare():
    regole = snapshot(vincoli=[
        conf(id="V5", tipo="altro", descrizione="Almeno un partner con sede all'estero",
             momento="domanda"),
        conf(id="V6", tipo="costituzione_entro", descrizione="Entro 60 giorni", parametro=60,
             momento="concessione"),
    ])
    v = valida(regole, coppia())
    x = voce(v, "vincolo:V5")
    assert x.codice == "vincolo_da_verificare" and x.esito == "grigio"
    assert x.titolo == "Almeno un partner con sede all'estero"
    # Una scadenza di costituzione non è una regola sulla composizione.
    assert "vincolo:V6" not in {y.id for y in v.voci}


def test_vincolo_sede_in_regione_senza_requisito_da_verificare():
    sede = conf(id="V7", tipo="sede_operativa_regione", descrizione="Sede operativa in "
                "Calabria", momento="domanda")
    regole = snapshot(vincoli=[sede])
    x = voce(valida(regole, coppia()), "vincolo:V7")
    assert x.codice == "vincolo_da_verificare" and x.esito == "grigio"
    assert x.regola.fonte == "bando"
    # Tradotto in un requisito «di ogni membro» sulla regione: lo valuta quello.
    req = requisito(mid(54), CALABRIA_OGNI, origine="bando_partenariato", citazione=CIT)
    v = valida(regole, coppia(), call=pv.CallConsorzio(id="c", requisiti=(req,)))
    ids = {y.id for y in v.voci}
    assert "vincolo:V7" not in ids and f"vincolo_membro:{mid(54)}" in ids


def test_origine_dei_requisiti():
    assert requisito(mid(60), CALABRIA_OGNI, citazione=CIT).regola.fonte == "bando"
    assert requisito(mid(61), CALABRIA_OGNI, origine="precheck").regola == pv.RegolaOrigine(
        "bando"
    )
    non_verificata = {**CIT, "verificata": False}
    assert requisito(mid(62), CALABRIA_OGNI, origine="ai_check",
                     citazione=non_verificata).regola.fonte == "creatore"
    # Criterio illeggibile → manuale (mai contato in automatico).
    assert requisito(mid(63), {"tipo": "sconosciuto"}).criterio.tipo == "manuale"


# ------------------------------------------------------ membri non più attivi


def test_membro_non_piu_attivo_grigio_e_senza_dati():
    """Un'azienda membro eliminata, archiviata o col titolare disattivato: i
    suoi dati non si valutano (il servizio non li passa) e una voce riservata
    lo segnala al creatore."""
    riga = {"id": B, "company_profile_id": eg.COMPANY["Y"], "ruolo": "partner",
            "stato": "confermato", "quota_percentuale": "40.00"}
    spento = pv.membro_da_riga(riga, creatore_company_id=eg.COMPANY["X"],
                               profilo=azienda(tipi=("organismo_ricerca",)),
                               collegamenti=[{"tipo": "identita", "chiave": "a" * 64}],
                               attivo=False)
    assert (spento.attivo, spento.profilo, spento.collegamenti) == (False, None, None)
    regole = snapshot(composizione=[
        conf(id="C1", tipo_soggetto="organismo_ricerca", minimo=1, ruolo="qualsiasi")])
    v = valida(regole, [membro(A, "capofila", "60", creatore=True), spento])
    x = voce(v, "membri_attivi")
    assert (x.codice, x.esito, x.membri_coinvolti, x.regola) == (
        "membri_attivi", "grigio", (B,), None)
    assert voce(v, "composizione:C1").esito == "grigio"
    assert v.esito != "verde"
    assert vista(v, "membri_attivi", A, True).membri_coinvolti == [pv_uuid(B)]
    assert vista(v, "membri_attivi", C).membri_coinvolti == []
    # Tutti attivi: nessuna voce.
    assert "membri_attivi" not in {y.id for y in valida(regole, coppia()).voci}


# ------------------------------------------------ proiezione e canary

CANARY_Y = ("2500000", "2.500.000", "2400000", "2.400.000", "2600000", "2.600.000",
            "812345", "812.345")


def _valori_esatti_x():
    return ("3200000", "3.200.000", "3100000", "3.100.000", "3300000", "3.300.000")


def test_proiezione_canary_nessun_valore_esatto_ne_id_di_altri():
    regole = snapshot(
        regole_finanziarie=[regola_f(), regola_f("media_pesata_quote", "F2")],
        quote=[quota_voce(minimo=10, massimo=70)],
    )
    call = pv.call_consorzio_da(eg.CALL_GUIDA, eg.REQUISITI_GUIDA)
    membri = [
        pv.MembroSnapshot(id=A, ruolo="capofila", quota=Decimal(70), creatore=True,
                          profilo=eg.profilo("X"), collegamenti=(chiave("identita", 1),)),
        pv.MembroSnapshot(id=B, ruolo="partner", quota=Decimal(30),
                          profilo=eg.profilo("Y"), collegamenti=(chiave("identita", 2),)),
    ]
    budget = intervallo_budget("2m_5m", "3100000.00")
    v = pv.valida_consorzio(regole, call, membri, budget=budget)
    m = pv.matrice_copertura(call.requisiti, membri, budget=budget)

    def json_per(viewer, creatore):
        return json.dumps([
            pv.proietta_validazione(v, viewer_membro_id=viewer, creatore=creatore)
            .model_dump(mode="json"),
            pv.proietta_matrice(m, viewer_membro_id=viewer).model_dump(mode="json"),
        ], ensure_ascii=False)

    per_x = json_per(A, True)
    per_y = json_per(B, False)
    neutra = json_per(None, False)
    for canary in CANARY_Y:
        assert canary not in per_x, canary
        assert canary not in neutra, canary
    for canary in _valori_esatti_x():
        assert canary not in per_y, canary
    # Il proprio valore esatto sì (fatturato medio di Y: 2,5 M€).
    assert "2.500.000" in per_y
    assert "3.200.000" in per_x
    for company in eg.COMPANY.values():
        assert company not in per_x and company not in per_y
    for codice in eg.CODICE_PUBBLICO.values():
        assert codice not in per_x and codice not in per_y


def test_voce_out_whitelist():
    v = valida(None, coppia())
    out = pv.proietta_validazione(v, viewer_membro_id=A, creatore=True)
    assert set(out.model_dump()) == {"esito", "voci", "riepilogo"}
    assert (out.riepilogo.verde, out.riepilogo.grigio, out.riepilogo.rosso) == (3, 1, 0)
    assert out.esito == "grigio"  # regole non confermate
    with pytest.raises(ValidationError):
        type(out.voci[0])(**out.voci[0].model_dump(), company_profile_id="x")


# ------------------------------------------------------------ matrice


def test_matrice_copertura_viste_e_rapporto_gap():
    call = pv.call_consorzio_da(eg.CALL_GUIDA, eg.REQUISITI_GUIDA)
    membri = [
        pv.MembroSnapshot(id=A, ruolo="capofila", quota=Decimal(70), creatore=True,
                          profilo=eg.profilo("X")),
        pv.MembroSnapshot(id=B, ruolo="partner", quota=Decimal(30), profilo=eg.profilo("Y")),
        esterno(C, "DE", ("organismo_ricerca",), ruolo="associated_partner", quota=None),
    ]
    m = pv.matrice_copertura(call.requisiti, membri, budget=BUDGET_ESATTO)
    assert m.membri == (A, B, C)
    assert m.copertura_gap_ratio == Decimal("1.000")  # A e C cercati, coperti da Y

    per_x = pv.proietta_matrice(m, viewer_membro_id=A)
    righe = {r.etichetta: r for r in per_x.righe}
    assert righe["A"].esito == "verde" and righe["A"].cercato is True
    celle_a = {str(c.membro_id): c for c in righe["A"].celle}
    assert celle_a[A].esito == "non_coperto" and celle_a[B].esito == "coperto"
    assert celle_a[C].fonte == "dichiarato"
    # F (costo quota / fatturato medio ≤ 0,6): X al 70% di 3,1 M€ su 3,2 M€ = 0,68.
    celle_f = {str(c.membro_id): c for c in righe["F"].celle}
    assert celle_f[A].esito == "non_coperto" and celle_f[A].testo_privato
    assert celle_f[B].esito == "coperto" and celle_f[B].testo_privato is None
    assert celle_f[C].si_applica is False  # partner associato
    assert righe["F"].esito == "rosso"
    # Dalla colonna di Y, X è valutata sulla fascia: dipende dalla fascia.
    per_y = pv.proietta_matrice(m, viewer_membro_id=B)
    riga_f = next(r for r in per_y.righe if r.etichetta == "F")
    celle = {str(c.membro_id): c for c in riga_f.celle}
    assert celle[A].esito == "incerto" and celle[A].testo_privato is None
    assert riga_f.esito == "grigio"
    # E (Calabria, ogni membro): l'associato esterno non conta.
    assert righe["E"].esito == "verde"
    assert per_x.copertura_gap_ratio == 1.0


def test_matrice_senza_cercati_e_membri_usciti():
    req = requisito(mid(70), CALABRIA_OGNI)
    membri = [membro(A, "capofila", creatore=True),
              membro(B, profilo=azienda(regioni=(LOMBARDIA,)), stato="uscito")]
    m = pv.matrice_copertura([req], membri)
    assert m.copertura_gap_ratio is None
    assert m.membri == (A,)
    assert pv.proietta_matrice(m, viewer_membro_id=A).righe[0].esito == "verde"


# ------------------------------------------------------------ builder


def test_membro_da_riga():
    riga = {"id": A, "company_profile_id": eg.COMPANY["X"], "ruolo": "capofila",
            "stato": "confermato", "quota_percentuale": "70.00", "posizione_id": None}
    m = pv.membro_da_riga(riga, creatore_company_id=eg.COMPANY["X"], profilo=eg.profilo("X"),
                          collegamenti=[{"tipo": "identita", "chiave": "a" * 64, "quota": None},
                                        {"tipo": "ignoto", "chiave": "b" * 64}])
    assert m.creatore and not m.esterno and m.quota == Decimal("70.00")
    assert m.collegamenti == (ChiaveCollegamento("identita", "a" * 64),)
    senza = pv.membro_da_riga({**riga, "company_profile_id": eg.COMPANY["Y"]},
                              creatore_company_id=eg.COMPANY["X"])
    assert not senza.creatore and senza.collegamenti is None
    est = pv.membro_da_riga(
        {"id": B, "company_profile_id": None, "ruolo": "partner", "stato": "proposto",
         "quota_percentuale": None, "esterno_denominazione": "Istituto Beta",
         "esterno_paese": "de", "esterno_tipi_soggetto": ["organismo_ricerca", "inventato"]},
        creatore_company_id=eg.COMPANY["X"], profilo=eg.profilo("Y"),
    )
    assert est.esterno and est.profilo is None and est.esterno_paese == "DE"
    assert est.esterno_tipi == ("organismo_ricerca",)
    # Il membro non porta l'id dell'azienda: l'unico handle è l'id del membro
    # (che nel JSON non esca nulla lo verifica il canary della proiezione).
    campi = {f.name for f in dataclasses.fields(pv.MembroSnapshot)}
    assert not {"company_id", "company_profile_id"} & campi


# ------------------------------------------------------------ DTO in ingresso


def test_membro_aggiorna_in_quota():
    assert MembroAggiornaIn(ruolo="partner", quota_percentuale=Decimal("30.5")).ruolo == "partner"
    for sbagliata in ("0", "100.01", "10.555", "-5"):
        with pytest.raises(BadRequestError):
            MembroAggiornaIn(ruolo="partner", quota_percentuale=Decimal(sbagliata))
    with pytest.raises(ValidationError):
        MembroAggiornaIn(ruolo="coordinatore")
    with pytest.raises(ValidationError):
        MembroAggiornaIn(ruolo="partner", company_profile_id="x")


def test_esterno_in():
    e = EsternoIn(denominazione="  Istituto​  Beta ", paese="el",
                  tipi_soggetto=["pmi", "pmi"], ruolo="affiliated_entity")
    assert e.denominazione == "Istituto Beta" and e.paese == "GR" and e.tipi_soggetto == ["pmi"]
    # Le chiavi di `p_payload` di fn_partner_membro_esterno (0040), nient'altro.
    assert set(e.payload("m1")) == {"membro_id", "denominazione", "paese", "tipi_soggetto",
                                    "ruolo", "quota", "posizione_id"}
    assert e.payload("m1")["membro_id"] == "m1" and "membro_id" not in e.payload()
    with pytest.raises(BadRequestError):
        EsternoIn(denominazione="X", paese="DE", tipi_soggetto=["pmi"])
    with pytest.raises(BadRequestError):
        EsternoIn(denominazione="Istituto", paese="ZZ", tipi_soggetto=["pmi"])
    with pytest.raises(ValidationError):
        EsternoIn(denominazione="Istituto", paese="DE", tipi_soggetto=[])
    with pytest.raises(BadRequestError):
        EsternoIn(denominazione="Istituto", paese="DE",
                  tipi_soggetto=["pmi", "universita", "impresa", "altro", "fondazione",
                                 "ente_pubblico"])


def test_budget_e_documento_in():
    assert BudgetIn(budget_fascia="2m_5m", budget_progetto_eur=Decimal("3100000")).campi() == {
        "budget_fascia": "2m_5m", "budget_progetto_eur": "3100000"}
    with pytest.raises(BadRequestError):
        BudgetIn(budget_fascia="2m_5m", budget_progetto_eur=Decimal("100"))
    with pytest.raises(BadRequestError):
        BudgetIn(budget_fascia="2m_5m", budget_progetto_eur=Decimal("3000000.001"))
    assert DocumentoStatoIn(stato="fatto", note="   ").note is None
    with pytest.raises(BadRequestError):
        DocumentoStatoIn(stato="fatto", note="x" * 501)
    with pytest.raises(ValidationError):
        DocumentoStatoIn(stato="finito")


def test_consorzio_out_whitelist():
    out = ConsorzioOut(validazione=pv.proietta_validazione(
        valida(None, coppia()), viewer_membro_id=A, creatore=True))
    assert out.budget.esatto is None and out.membri_max == 30
    with pytest.raises(ValidationError):
        MembroOut(id=A, nome="Membro", ruolo="partner", stato="proposto",
                  company_profile_id=eg.COMPANY["Y"])
