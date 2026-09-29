"""Matching deterministico call ↔ aziende (WP6, docs/partenariati.md M1-M3,
Q11, Q12, Q17, Q25).

Proprietà difese:
- ogni filtro rigido esclude con il suo codice e NELL'ORDINE del contratto
  (con tutti i filtri successivi violati vince sempre il primo);
- `dato_mancante` e `incerto` non escludono: voce «attenzione» e penalità
  (5 per voce, al massimo 20);
- formula del punteggio con numeri calcolati a mano, arrotondamento half-up;
- ordine: requisiti coperti, punteggio, tie-break sha256 settimanale
  (stesso ordine nella settimana, rotazione alla successiva); determinismo;
- al massimo 2 aziende dello stesso owner per pagina;
- spiegazioni esatte da template (capofila e «cerco capofila»);
- vista terzi senza nessun numero di bilancio (canary sul JSON), profili
  anonimi con la sola fascia di fatturato;
- inferenza: al variare del budget l'esito finanziario cambia solo ai bordi
  della fascia del candidato, e due candidati nella stessa fascia hanno
  sempre lo stesso esito;
- il budget è sempre la fascia pubblica, mai quello esatto.
"""

import hashlib
import re
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from app.core.config import Settings
from app.services import partenariato_matching as pm
from app.services.partenariato_matching import (
    Componenti,
    FiltriSuggeriti,
    IndiceMatching,
    MatchInterno,
    PesiMatching,
    RequisitoRif,
)
from tests.fixtures.partenariati import esempio_guida as g

OGGI = g.OGGI
CALL_ID = g.CALL_GUIDA_ID
REQ_G, REQ_H, REQ_I = "f0000000-0000-4000-8000-000000000007", \
    "f0000000-0000-4000-8000-000000000008", "f0000000-0000-4000-8000-000000000009"


def Y(**modifiche) -> pm.ProfiloMatching:
    return replace(g.profilo("Y"), **modifiche)


def call_guida(**modifiche) -> pm.CallSnapshot:
    return replace(g.call_guida(), **modifiche)


# Call della guida con tre vincoli di ogni membro in più (paese, dimensione,
# certificazione): servono a far scattare ogni filtro.
REQUISITI_FILTRI = [
    *g.REQUISITI_GUIDA,
    g.riga_requisito(REQ_G, CALL_ID, "G", 6, {"tipo": "paese", "paesi": ["IT"]},
                     ambito="ogni_membro"),
    g.riga_requisito(REQ_H, CALL_ID, "H", 7,
                     {"tipo": "dimensione", "valori": ["micro", "piccola", "media"]},
                     ambito="ogni_membro"),
    g.riga_requisito(REQ_I, CALL_ID, "I", 8,
                     {"tipo": "certificazione", "categorie": ["settoriale"]},
                     ambito="ogni_membro"),
]


def call_filtri(**modifiche) -> pm.CallSnapshot:
    call = pm.call_snapshot_da(g.CALL_GUIDA, REQUISITI_FILTRI, g.POSIZIONI_GUIDA, g.profilo("X"))
    return replace(call, **modifiche)


def call_da(requisiti, posizioni, **riga) -> pm.CallSnapshot:
    return pm.call_snapshot_da({**g.CALL_GUIDA, **riga}, requisiti, posizioni, g.profilo("X"))


def esercizi(fatturato: int) -> tuple:
    """Esercizi con fatturato medio (2 anni) uguale a `fatturato`."""
    return g.profilo("Y", esercizi=g._esercizi((fatturato, fatturato), 500_000, 10)).esercizi


# ============================================================ filtri rigidi

# (codice, modifiche alla call, modifiche al candidato), nell'ordine M1.
GUASTI: list[tuple[str, dict, dict]] = [
    ("call_non_attiva", {"stato": "chiusa_completata"}, {}),
    ("non_visibile", {}, {"visibile": False}),
    ("sospeso", {}, {"sospeso": True}),
    ("non_viva", {}, {"viva": False}),
    ("cessata", {}, {"stato_impresa": "Cessata"}),
    ("stesso_owner", {}, {"owner_id": g.OWNER["X"]}),
    ("collegamenti_non_calcolati", {}, {"collegamenti_ok": False}),
    ("collegata", {}, {"collegate": frozenset({g.COMPANY["X"]})}),
    ("categoria_esclusa", {}, {"categorie_bando_escluse": frozenset({g.TIPOLOGIA_BANDO})}),
    ("forma_non_accettata", {}, {"forme_accettate": ("consorzio",)}),
    ("nessuna_posizione_compatibile", {}, {"tipi_dichiarati": ()}),
    ("territorio", {}, {"regioni_ids": frozenset({g.LOMBARDIA})}),
    ("paese", {}, {"paese": "FR"}),
    ("dimensione", {}, {"classe_dimensionale": "grande"}),
    ("finanziaria_non_soddisfatta", {}, {"esercizi": esercizi(200_000)}),
    ("esclusivita", {"esclusivita": True}, {"impegni_bando": frozenset({g.BANDO_GUIDA})}),
    ("solo_invitati", {"visibilita": "solo_invitati"}, {"accetta_inviti": False}),
]


def test_i_codici_sono_quelli_del_modulo_nello_stesso_ordine():
    assert tuple(codice for codice, _, _ in GUASTI) == pm.CODICI_ESCLUSIONE


def test_senza_guasti_la_coppia_passa():
    assert pm.esclusione(call_filtri(), Y(), oggi=OGGI) is None
    assert pm.valuta_coppia(call_filtri(), Y(), oggi=OGGI) is not None


@pytest.mark.parametrize("i", range(len(GUASTI)), ids=[c for c, _, _ in GUASTI])
def test_ogni_filtro_da_solo_esclude_con_il_suo_codice(i):
    codice, mod_call, mod_cand = GUASTI[i]
    call, cand = call_filtri(**mod_call), Y(**mod_cand)
    assert pm.esclusione(call, cand, oggi=OGGI) == codice
    assert pm.valuta_coppia(call, cand, oggi=OGGI) is None
    assert pm.posizioni_compatibili(call, cand, oggi=OGGI) == []


@pytest.mark.parametrize("i", range(len(GUASTI)), ids=[c for c, _, _ in GUASTI])
def test_filtri_in_ordine_vince_il_primo(i):
    # Violati il filtro i e TUTTI i successivi: il codice è quello di i.
    mod_call: dict = {}
    mod_cand: dict = {}
    for _, c, k in GUASTI[i:]:
        mod_call.update(c)
        mod_cand.update(k)
    assert pm.esclusione(call_filtri(**mod_call), Y(**mod_cand), oggi=OGGI) == GUASTI[i][0]


class TestFiltriNelDettaglio:
    @pytest.mark.parametrize(
        "modifiche",
        [
            {"sospesa": True},
            {"stato": "bozza"},
            {"scadenza_call": OGGI - timedelta(days=1)},
            {"bando_scadenza": OGGI - timedelta(days=1)},
        ],
    )
    def test_call_non_attiva(self, modifiche):
        assert pm.esclusione(call_guida(**modifiche), Y(), oggi=OGGI) == "call_non_attiva"

    def test_call_che_scade_oggi_resta_attiva(self):
        assert pm.esclusione(call_guida(scadenza_call=OGGI), Y(), oggi=OGGI) is None

    def test_creatore_non_vivo(self):
        call = call_guida(creatore=replace(g.profilo("X"), viva=False))
        assert pm.esclusione(call, Y(), oggi=OGGI) == "call_non_attiva"

    @pytest.mark.parametrize("stato, codice", [
        ("Attiva", None), ("In liquidazione", "cessata"), ("Inattiva", "cessata"), (None, None),
    ])
    def test_stato_impresa_solo_se_noto(self, stato, codice):
        assert pm.esclusione(call_guida(), Y(stato_impresa=stato), oggi=OGGI) == codice

    def test_stessa_azienda(self):
        cand = Y(company_id=g.COMPANY["X"])
        assert pm.esclusione(call_guida(), cand, oggi=OGGI) == "stesso_owner"

    def test_collegamento_in_entrambe_le_direzioni(self):
        solo_creatore = call_guida(
            creatore=replace(g.profilo("X"), collegate=frozenset({g.COMPANY["Y"]}))
        )
        assert pm.esclusione(solo_creatore, Y(), oggi=OGGI) == "collegata"
        assert pm.esclusione(
            call_guida(), Y(collegate=frozenset({g.COMPANY["X"]})), oggi=OGGI
        ) == "collegata"

    def test_collegamenti_del_creatore_non_calcolati(self):
        call = call_guida(creatore=replace(g.profilo("X"), collegamenti_ok=False))
        assert pm.esclusione(call, Y(), oggi=OGGI) == "collegamenti_non_calcolati"
        assert pm.esclusione(call, Y(visibile=False), oggi=OGGI, direzione="per_te") == (
            "collegamenti_non_calcolati"
        )

    def test_forme_accettate_vuote_o_call_senza_forma_non_filtrano(self):
        assert pm.esclusione(call_guida(), Y(forme_accettate=()), oggi=OGGI) is None
        assert pm.esclusione(
            call_guida(forma=None), Y(forme_accettate=("consorzio",)), oggi=OGGI
        ) is None

    def test_ruolo_della_posizione(self):
        capofila = [dict(g.POSIZIONI_GUIDA[0], ruolo="capofila")]
        call = call_da(g.REQUISITI_GUIDA, capofila)
        assert pm.esclusione(call, Y(), oggi=OGGI) == "nessuna_posizione_compatibile"
        assert pm.esclusione(call, Y(ruoli_disponibili=("capofila", "partner")), oggi=OGGI) is None
        # Senza profilo partner i ruoli non si conoscono: non si esclude.
        assert pm.esclusione(call, Y(ruoli_disponibili=None), oggi=OGGI) is None

    def test_vincoli_della_posizione_e_posizioni_rimaste(self):
        in_calabria = dict(g.POSIZIONI_GUIDA[0], regioni=[g.CALABRIA],
                           territorio_modalita="sede_attuale")
        in_lombardia = dict(g.POSIZIONI_GUIDA[0], id="b0000000-0000-4000-8000-000000000009",
                            titolo="Sede lombarda", regioni=[g.LOMBARDIA],
                            territorio_modalita="sede_attuale", ordine=1)
        senza_e = [r for r in g.REQUISITI_GUIDA if r["etichetta"] != "E"]
        cand = Y(regioni_ids=frozenset({g.LOMBARDIA}))
        assert pm.esclusione(call_da(senza_e, [in_calabria]), cand, oggi=OGGI) == "territorio"
        call = call_da(senza_e, [in_calabria, in_lombardia])
        assert pm.esclusione(call, cand, oggi=OGGI) is None
        assert [p.titolo for p in pm.posizioni_compatibili(call, cand, oggi=OGGI)] == [
            "Sede lombarda"
        ]

    def test_esclusivita_solo_sullo_stesso_bando(self):
        call = call_guida(esclusivita=True)
        assert pm.esclusione(call, Y(impegni_bando=frozenset({g.BANDO_ALTRO})), oggi=OGGI) is None
        assert pm.esclusione(call_guida(), Y(impegni_bando=frozenset({g.BANDO_GUIDA})),
                             oggi=OGGI) is None

    def test_solo_invitati_nelle_due_direzioni(self):
        call = call_guida(visibilita="solo_invitati")
        # Per te: senza invito mai (nel WP6 nessun invito), con invito sì.
        assert pm.esclusione(call, Y(), oggi=OGGI, direzione="per_te") == "solo_invitati"
        assert pm.esclusione(
            call, Y(), oggi=OGGI, direzione="per_te", invitati=[g.COMPANY["Y"]]
        ) is None
        # Suggeriti al creatore: sì, purché l'azienda accetti inviti.
        assert pm.esclusione(call, Y(), oggi=OGGI) is None

    def test_altri_requisiti_di_ogni_membro_non_escludono(self):
        # Elenco chiuso di M1: solo territorio, paese e dimensione escludono.
        # Una certificazione `ogni_membro` non dichiarata (I: settoriale) è da
        # verificare, con la penalità: Y resta con A e C coperti.
        cand = Y(certificazioni_categorie=("ambiente",))
        assert pm.esclusione(call_filtri(), cand, oggi=OGGI) is None
        base = pm.valuta_coppia(call_filtri(), Y(), oggi=OGGI)
        m = pm.valuta_coppia(call_filtri(), cand, oggi=OGGI)
        assert m is not None and [r.etichetta for r in m.copre] == ["A", "C"]
        assert [(a.codice, a.testo.split(":")[0]) for a in m.attenzione] == [
            ("requisito_da_verificare", "Requisito «I»")]
        assert m.penalita == base.penalita + 5
        assert "requisito_membro" not in pm.CODICI_ESCLUSIONE

    def test_per_te_anche_senza_opt_in(self):
        senza = Y(visibile=False, collegamenti_ok=False)
        assert pm.esclusione(call_guida(), senza, oggi=OGGI) == "non_visibile"
        assert pm.esclusione(call_guida(), senza, oggi=OGGI, direzione="per_te") is None
        # Collegamenti calcolati (ha una call): valgono anche nella scoperta.
        collegata = Y(visibile=False, collegate=frozenset({g.COMPANY["X"]}))
        assert pm.esclusione(call_guida(), collegata, oggi=OGGI, direzione="per_te") == (
            "collegata"
        )


class TestRegoleFinanziarie:
    def _con_regola(self, ambito: str, ruolo_posizione: str = "partner", quota="20.00"):
        regola = dict(g.REGOLA_F, ambito=ambito)
        requisiti = [
            dict(r, criterio={"tipo": "regola_finanziaria", "regola": regola})
            if r["etichetta"] == "F" else r
            for r in g.REQUISITI_GUIDA
        ]
        posizioni = [dict(g.POSIZIONI_GUIDA[0], ruolo=ruolo_posizione,
                          quota_ipotizzata_pct=quota)]
        return call_da(requisiti, posizioni)

    def test_regola_del_capofila_solo_sulle_posizioni_da_capofila(self):
        povera = Y(esercizi=esercizi(200_000), ruoli_disponibili=("capofila", "partner"))
        assert pm.esclusione(self._con_regola("capofila"), povera, oggi=OGGI) is None
        assert pm.esclusione(self._con_regola("capofila", "capofila"), povera, oggi=OGGI) == (
            "finanziaria_non_soddisfatta"
        )

    @pytest.mark.parametrize("ambito", ["media_pesata_quote", "partenariato_totale"])
    def test_regole_aggregate_sono_del_validatore(self, ambito):
        povera = Y(esercizi=esercizi(200_000))
        m = pm.valuta_coppia(self._con_regola(ambito), povera, oggi=OGGI)
        assert m is not None and m.attenzione == ()

    def test_senza_quota_della_posizione_attenzione(self):
        m = pm.valuta_coppia(self._con_regola("ciascun_partner", quota=None), Y(), oggi=OGGI)
        assert [a.codice for a in m.attenzione] == ["costo_quota_mancante"]
        assert m.penalita == 5

    def test_budget_aperto_e_fascia_aperta_non_fermano_il_ranking(self):
        # Prerequisito 1 (bilanci_indicatori, altro ruolo): con budget
        # `oltre_5m` e fascia `oltre_50m` l'aritmetica ∞ − ∞ non deve
        # sollevare. Il matching si difende comunque: dato mancante.
        call = replace(call_guida(), budget=(Decimal(5_000_000), Decimal("Infinity")))
        ricca = Y(esercizi=esercizi(60_000_000))
        m = pm.valuta_coppia(call, ricca, oggi=OGGI)
        assert m is not None
        assert len(m.attenzione) == 1
        assert m.attenzione[0].codice in {
            "dipende_budget", "dipende_fascia", "finanziaria_dato_mancante"
        }


# ================================================= dati mancanti o incerti


class TestDatiMancanti:
    def test_territorio_senza_registro_penalizza_senza_escludere(self):
        base = pm.valuta_coppia(call_guida(), Y(), oggi=OGGI)
        m = pm.valuta_coppia(call_guida(), Y(regioni_ids=frozenset()), oggi=OGGI)
        assert m is not None
        assert [a.codice for a in m.attenzione] == ["territorio_da_verificare"]
        assert m.attenzione[0].testo == (
            "Requisito «E»: Sede nella regione: dato mancante (Registro Imprese)"
        )
        assert m.penalita == 5
        # L'affinità di regione resta (Calabria è una regione d'interesse):
        # cambia solo la penalità.
        assert m.componenti == base.componenti
        assert m.punteggio == base.punteggio - 5 == 85

    def test_sede_da_aprire_e_incerto(self):
        requisiti = [
            dict(r, criterio={"tipo": "regione", "regioni_ids": [g.CALABRIA],
                              "modalita": "sede_entro_erogazione"})
            if r["etichetta"] == "E" else r
            for r in g.REQUISITI_GUIDA
        ]
        cand = Y(regioni_ids=frozenset({g.LOMBARDIA}))
        m = pm.valuta_coppia(call_da(requisiti, g.POSIZIONI_GUIDA), cand, oggi=OGGI)
        assert m is not None
        assert [a.codice for a in m.attenzione] == ["territorio_da_verificare"]
        assert "sede da aprire entro l'erogazione" in m.attenzione[0].testo

    def test_bilanci_mancanti(self):
        m = pm.valuta_coppia(call_guida(), Y(esercizi=(), fasce=None), oggi=OGGI)
        assert [(a.codice, a.testo) for a in m.attenzione] == [
            ("bilanci_non_disponibili",
             "Bilanci non disponibili: la regola «F» non si può verificare"),
        ]

    def test_finanziaria_incerta_sulla_fascia(self):
        # Fatturato medio 1 M€ (fascia 500k_2m): con costo 400 k€–1 M€ la
        # regola vale per una parte della fascia e non per un'altra.
        m = pm.valuta_coppia(call_guida(), Y(esercizi=esercizi(1_000_000)), oggi=OGGI)
        assert m is not None
        assert [a.codice for a in m.attenzione] == ["dipende_fascia"]

    def test_tipo_soggetto_senza_profilo(self):
        senza_profilo = Y(profilo_presente=False, tipi_dichiarati=(), competenze=())
        m = pm.valuta_coppia(call_guida(), senza_profilo, oggi=OGGI, direzione="per_te")
        assert m is not None
        assert "tipo_soggetto_da_verificare" in [a.codice for a in m.attenzione]
        assert m.coperti == 0 and not m.rilevante

    def test_penalita_al_massimo_20(self):
        tanti_dubbi = Y(
            regioni_ids=frozenset(), paese=None, classe_dimensionale=None,
            certificazioni_categorie=(), esercizi=(), fasce=None,
        )
        m = pm.valuta_coppia(call_filtri(), tanti_dubbi, oggi=OGGI)
        assert m is not None
        assert len(m.attenzione) == 5
        assert m.penalita == 20


# ================================================================ punteggio


class TestPunteggio:
    def _call_semplice(self, **riga):
        requisiti = [
            g.riga_requisito(g.REQ["A"], CALL_ID, "A", 0,
                             {"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]},
                             cercato=True),
            g.riga_requisito(g.REQ["C"], CALL_ID, "C", 1,
                             {"tipo": "tag", "tags": ["prototipazione_testing"]}, cercato=True),
            g.riga_requisito(g.REQ["D"], CALL_ID, "D", 2,
                             {"tipo": "dimensione", "valori": ["micro", "piccola", "media"]},
                             ambito="ogni_membro"),
        ]
        posizioni = [dict(g.POSIZIONI_GUIDA[0], competenze=[])]
        return call_da(requisiti, posizioni, **{"bando_programma_id": g.FESR, **riga})

    def test_formula_calcolata_a_mano(self):
        cand = Y(
            competenze=("ricerca_industriale", "sviluppo_software", "dispositivi_medici_salute",
                        "intelligenza_artificiale_dati"),
            completezza=55,
            esposizioni_7g=2,
            classe_dimensionale=None,
        )
        m = pm.valuta_coppia(self._call_semplice(), cand, oggi=OGGI)
        # copertura: A sì, C no (manca prototipazione) → 1/2
        # affinità: programma FESR no, ATECO/settore con X no, regione sì → 1/3
        # complementarità: 2 delle 4 competenze non sono di X → 1/2
        # completezza 55/100; rotazione 1 − 2/5 = 3/5
        assert m.componenti == Componenti(
            copertura_gap=Fraction(1, 2),
            affinita=Fraction(1, 3),
            complementarita=Fraction(1, 2),
            completezza=Fraction(11, 20),
            rotazione=Fraction(3, 5),
        )
        # 100·(0,25 + 0,0667 + 0,05 + 0,055 + 0,06) = 48,17 → 48; −5 (D senza
        # classe dimensionale) → 43
        assert [a.codice for a in m.attenzione] == ["dimensione_da_verificare"]
        assert m.punteggio == 43

    def test_affinita_piena(self):
        cand = Y(settori_interesse_ids=frozenset({g.SETTORE_ICT}))
        m = pm.valuta_coppia(self._call_semplice(bando_programma_id=g.HORIZON), cand, oggi=OGGI)
        assert m.componenti.affinita == 1

    def test_senza_requisiti_cercati_conta_la_posizione(self):
        requisiti = [dict(r, cercato=False) for r in g.REQUISITI_GUIDA]
        m = pm.valuta_coppia(call_da(requisiti, g.POSIZIONI_GUIDA), Y(), oggi=OGGI)
        assert m.cercati == 0 and m.posizione_coperta
        assert m.componenti.copertura_gap == 1
        assert m.spiegazione == "Corrispondi alla posizione «Ricerca e prototipazione»."

    @pytest.mark.parametrize("esposizioni, attesa", [
        (0, Fraction(1)), (2, Fraction(3, 5)), (5, Fraction(0)), (7, Fraction(0)),
    ])
    def test_rotazione(self, esposizioni, attesa):
        m = pm.valuta_coppia(call_guida(), Y(esposizioni_7g=esposizioni), oggi=OGGI)
        assert m.componenti.rotazione == attesa

    def test_complementarita_senza_competenze_zero(self):
        m = pm.valuta_coppia(call_guida(), Y(competenze=()), oggi=OGGI, direzione="per_te")
        assert m.componenti.complementarita == 0

    @pytest.mark.parametrize("completezza, attesa", [
        (Fraction(1, 20), 1),   # 0,5 → 1 (half-up, non banker's)
        (Fraction(3, 20), 2),   # 1,5 → 2
        (Fraction(1, 25), 0),   # 0,4 → 0
    ])
    def test_arrotondamento_half_up(self, completezza, attesa):
        zero = Fraction(0)
        componenti = Componenti(zero, zero, zero, completezza, zero)
        assert pm.calcola_punteggio(componenti, 0) == (attesa, 0)

    def test_limiti_0_100_e_pesi_personalizzati(self):
        uno = Fraction(1)
        tutto = Componenti(uno, uno, uno, uno, uno)
        assert pm.calcola_punteggio(tutto, 0) == (100, 0)
        nulla = Componenti(*(Fraction(0),) * 5)
        assert pm.calcola_punteggio(nulla, 3) == (0, 15)
        solo_copertura = PesiMatching(copertura=1.0, affinita=0, complementarita=0,
                                      completezza=0, rotazione=0, penalita_dato_mancante=3,
                                      penalita_max=4)
        meta = Componenti(Fraction(1, 2), uno, uno, uno, uno)
        assert pm.calcola_punteggio(meta, 2, solo_copertura) == (46, 4)

    def test_pesi_dalle_settings(self):
        settings = Settings(
            _env_file=None,
            primary_supabase_url="https://x.supabase.co",
            primary_supabase_service_role_key="k",
            secondary_supabase_url="https://y.supabase.co",
            secondary_supabase_anon_key="k",
        )
        assert PesiMatching.da_settings(settings) == pm.PESI_PREDEFINITI
        assert (settings.partenariato_suggeriti_pagina, settings.partenariato_notifiche_top_k,
                settings.partenariato_notifiche_soglia,
                settings.partenariato_notifiche_tetto_settimana) == (20, 20, 50, 3)
        altra = settings.model_copy(update={"partenariato_peso_copertura": 0.6,
                                            "partenariato_penalita_max": 10})
        pesi = PesiMatching.da_settings(altra)
        assert (pesi.copertura, pesi.penalita_max) == (0.6, 10)


# =================================================================== ordine


def _m(company: str, owner: str, coperti: int, punteggio: int, *, call_id: str = "call",
       codice: str | None = None, call_owner: str = "owner-call") -> MatchInterno:
    zero = Fraction(0)
    return MatchInterno(
        call_id=call_id, company_id=company, candidato_owner_id=owner,
        call_owner_id=call_owner, codice_pubblico=codice or f"k-{company}", anonimo=True,
        invitabile=True, ruolo_creatore="capofila", cercati=3,
        copre=tuple(RequisitoRif(f"r{i}", chr(65 + i)) for i in range(coperti)),
        non_copre=(), attenzione=(), posizioni=(),
        componenti=Componenti(zero, zero, zero, zero, zero), penalita=0,
        punteggio=punteggio, fasce=None,
    )


def _cloni(n: int, **modifiche) -> list[pm.ProfiloMatching]:
    return [
        Y(company_id=f"c0000000-0000-4000-8000-1000000000{i:02d}",
          owner_id=f"a0000000-0000-4000-8000-1000000000{i:02d}",
          codice_pubblico=f"d0000000-0000-4000-8000-1000000000{i:02d}", **modifiche)
        for i in range(n)
    ]


class TestOrdine:
    def test_prima_i_requisiti_coperti_poi_il_punteggio(self):
        ordinati = pm.ordina([_m("a", "o1", 1, 95), _m("b", "o2", 2, 40), _m("c", "o3", 2, 60)],
                             oggi=OGGI)
        assert [m.company_id for m in ordinati] == ["c", "b", "a"]

    def test_tie_break_sha256_della_settimana(self):
        pari = [_m(f"c{i}", f"o{i}", 1, 50) for i in range(12)]
        ordinati = pm.ordina(pari, oggi=OGGI)
        settimana = pm.settimana_iso(OGGI)
        assert settimana == "2026-W41"
        chiavi = [pm.chiave_rotazione(m.call_id, m.codice_pubblico, settimana) for m in ordinati]
        assert chiavi == sorted(chiavi)
        assert chiavi[0] == hashlib.sha256(
            f"call:{ordinati[0].codice_pubblico}:2026-W41".encode()
        ).hexdigest()

    def test_rotazione_settimanale(self):
        indice = IndiceMatching.da_liste([g.call_guida()], _cloni(12))
        lunedi = [m.company_id for m in pm.suggeriti_per_call(indice, CALL_ID, oggi=OGGI)]
        domenica = [
            m.company_id
            for m in pm.suggeriti_per_call(indice, CALL_ID, oggi=OGGI + timedelta(days=6))
        ]
        dopo = [
            m.company_id
            for m in pm.suggeriti_per_call(indice, CALL_ID, oggi=OGGI + timedelta(days=7))
        ]
        assert len(lunedi) == 12
        assert lunedi == domenica  # stessa settimana ISO: stesso ordine
        assert dopo != lunedi and sorted(dopo) == sorted(lunedi)

    def test_determinismo_e_indipendenza_dall_ordine_di_caricamento(self):
        candidati = [g.profilo(n) for n in g.CANDIDATI_INDICE] + _cloni(6)
        uno = IndiceMatching.da_liste(g.calls(), candidati)
        due = IndiceMatching.da_liste(list(reversed(g.calls())), list(reversed(candidati)))
        for indice_a, indice_b in ((uno, due), (uno, uno)):
            a = pm.suggeriti_per_call(indice_a, CALL_ID, oggi=OGGI)
            b = pm.suggeriti_per_call(indice_b, CALL_ID, oggi=OGGI)
            assert a == b
            assert pm.per_te(indice_a, g.COMPANY["Y"], oggi=OGGI) == pm.per_te(
                indice_b, g.COMPANY["Y"], oggi=OGGI
            )


class TestCapPerOwner:
    def test_al_massimo_due_dello_stesso_owner_per_pagina(self):
        items = pm.ordina(
            [_m(f"a{i}", "advisor", 3, 90 - i) for i in range(5)]
            + [_m("b", "ob", 2, 80), _m("c", "oc", 2, 70), _m("d", "od", 1, 60)],
            oggi=OGGI,
        )
        pagine = pm.impagina(items, dimensione=4)
        assert [[m.company_id for m in p] for p in pagine] == [
            ["a0", "a1", "b", "c"], ["a2", "a3", "d"], ["a4"],
        ]
        for p in pagine:
            assert max(sum(1 for m in p if m.candidato_owner_id == o)
                       for o in {m.candidato_owner_id for m in p}) <= 2
        assert pm.pagina(items, 2, dimensione=4) == (pagine[1], True)
        assert pm.pagina(items, 3, dimensione=4) == (pagine[2], False)
        assert pm.pagina(items, 4, dimensione=4) == ([], False)

    def test_per_te_limita_gli_owner_delle_call(self):
        items = [_m("y", "oy", 1, 90 - i, call_id=f"call{i}", call_owner="advisor")
                 for i in range(3)]
        pagine = pm.impagina(items, dimensione=10, owner=pm.owner_call)
        assert [len(p) for p in pagine] == [2, 1]

    def test_suggeriti_di_un_advisor_con_tante_aziende(self):
        stesse = [replace(c, owner_id=g.OWNER["Y"]) for c in _cloni(5)]
        indice = IndiceMatching.da_liste([g.call_guida()], stesse + [g.profilo("T")])
        risultati = pm.suggeriti_per_call(indice, CALL_ID, oggi=OGGI)
        prima, altre = pm.pagina(risultati, 1, dimensione=3)
        assert altre
        assert [m.company_id for m in prima].count(g.COMPANY["T"]) == 1
        assert sum(1 for m in prima if m.candidato_owner_id == g.OWNER["Y"]) == 2


# ============================================================== spiegazioni


class TestSpiegazioni:
    def test_capofila(self):
        m = pm.valuta_coppia(call_guida(), Y(), oggi=OGGI)
        assert m.spiegazione == "Copri «A» e «C», che mancano al capofila."

    def test_cerco_capofila(self):
        m = pm.valuta_coppia(call_guida(ruolo_creatore="cerco_capofila"), Y(), oggi=OGGI)
        assert m.spiegazione == "Copri «A» e «C», che mancano al proponente."

    @pytest.mark.parametrize("etichette, ruolo, attesa", [
        (["A"], "capofila", "Copri «A», che manca al capofila."),
        (["A", "B", "C"], "capofila", "Copri «A», «B» e «C», che mancano al capofila."),
        (["B"], "cerco_capofila", "Copri «B», che manca al proponente."),
    ])
    def test_template(self, etichette, ruolo, attesa):
        m = replace(_m("y", "oy", 0, 50),
                    copre=tuple(RequisitoRif(f"r{e}", e) for e in etichette))
        assert pm.spiegazione(m, ruolo_creatore=ruolo) == attesa

    def test_senza_requisiti_coperti(self):
        coperta = replace(_m("y", "oy", 0, 50),
                          posizioni=(pm.PosizioneMatch("p", "Laboratorio prove", True),))
        assert pm.spiegazione(coperta) == "Corrispondi alla posizione «Laboratorio prove»."
        nulla = replace(_m("y", "oy", 0, 50),
                        posizioni=(pm.PosizioneMatch("p", "Laboratorio prove", False),))
        assert pm.spiegazione(nulla) == (
            "Sei compatibile con la call, ma non copri requisiti mancanti."
        )


# =========================================================== vista terzi


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _varianti(valore: int) -> list[str]:
    italiano = f"{valore:,}".replace(",", ".")
    return [str(valore), f"{valore}.00", f"{valore},00", italiano, f"{valore:,}"]


class TestVistaTerzi:
    CANARY = [
        *g.FATTURATO_Y,
        sum(g.FATTURATO_Y) // 2,   # fatturato medio
        g.PATRIMONIO_NETTO_Y,
        g.FATTURATO_Y[0] * 2,      # totale attivo
    ]

    def _json(self, m, vista):
        return pm.proietta_match(m, vista=vista).model_dump_json()

    def test_nessun_numero_di_bilancio_verso_terzi(self):
        casi = [
            pm.valuta_coppia(call_guida(), Y(), oggi=OGGI, dettaglio_proprio=True),
            # esito «dipende dalla fascia»: attenzione senza importi
            pm.valuta_coppia(call_guida(), Y(esercizi=esercizi(1_234_567)), oggi=OGGI,
                             dettaglio_proprio=True),
        ]
        canary = [*self.CANARY, 1_234_567]
        for m in casi:
            testo = self._json(m, "terzi")
            for valore in canary:
                for forma in _varianti(valore):
                    assert forma not in testo, forma
            # nessun numero lungo, tolti gli uuid (fasce e punteggi sono corti)
            assert re.findall(r"\d[\d.,]{3,}", _UUID.sub("", testo)) == []
            assert '"dettaglio":null' in testo

    def test_il_canary_funziona_sulla_vista_proprio(self):
        m = pm.valuta_coppia(call_guida(), Y(), oggi=OGGI, dettaglio_proprio=True)
        proprio = self._json(m, "proprio")
        assert "2.500.000" in proprio  # i propri valori, solo a sé stessi

    def test_nessun_identificativo_interno(self):
        m = pm.valuta_coppia(call_guida(), Y(), oggi=OGGI)
        testo = self._json(m, "terzi")
        for valore in (g.COMPANY["Y"], g.OWNER["Y"], g.COMPANY["X"], g.OWNER["X"],
                       g.CODICE_PUBBLICO["Y"]):
            assert valore not in testo

    def test_il_punteggio_non_fa_da_oracolo_sui_dati_del_creatore(self):
        # Affinità e complementarità usano competenze e sedi del creatore, che
        # la sua proiezione non mostra: cambiando solo il PROPRIO profilo
        # (una competenza che X ha / che non ha; una regione d'interesse che
        # è / non è una sede di X) il punteggio interno cambia, il match
        # esposto no, in nessuna vista.
        di_x, non_di_x = "sviluppo_software", "ricerca_industriale"
        assert di_x in call_guida().creatore.competenze
        assert non_di_x not in call_guida().creatore.competenze
        coppie = [
            (Y(competenze=("prototipazione_testing", di_x)),
             Y(competenze=("prototipazione_testing", non_di_x))),
            (Y(regioni_ids=frozenset({g.CALABRIA}), regioni_interesse_ids=frozenset()),
             Y(regioni_ids=frozenset({g.CALABRIA}),
               regioni_interesse_ids=frozenset({g.LOMBARDIA}))),
        ]
        cambiati = 0
        for primo, secondo in coppie:
            a = pm.valuta_coppia(call_guida(), primo, oggi=OGGI)
            b = pm.valuta_coppia(call_guida(), secondo, oggi=OGGI)
            cambiati += a.punteggio != b.punteggio
            for vista in ("proprio", "terzi"):
                assert "punteggio" not in pm.proietta_match(a, vista=vista).model_dump()
                assert self._json(a, vista) == self._json(b, vista)
        assert cambiati >= 1  # l'oracolo c'era: il punteggio interno cambia

    def test_profili_anonimi_solo_fascia_di_fatturato(self):
        m = pm.valuta_coppia(call_guida(), Y(), oggi=OGGI)
        terzi = pm.proietta_match(m, vista="terzi").fasce
        assert terzi.model_dump() == {
            "fatturato": "2m_10m", "patrimonio_netto": None, "dipendenti": None, "trend": None,
        }
        proprio = pm.proietta_match(m, vista="proprio").fasce
        assert proprio.patrimonio_netto == "500k_2m" and proprio.dipendenti == "10_49"
        nominativo = pm.valuta_coppia(call_guida(), Y(anonimo=False), oggi=OGGI)
        assert pm.proietta_match(nominativo, vista="terzi").fasce.patrimonio_netto == "500k_2m"
        senza_bilanci = pm.valuta_coppia(call_guida(), Y(esercizi=(), fasce=None), oggi=OGGI)
        assert pm.proietta_match(senza_bilanci, vista="terzi").fasce is None


# =============================================================== inferenza


def _stato_finanziario(call, cand) -> str:
    codice = pm.esclusione(call, cand, oggi=OGGI)
    if codice is not None:
        return codice
    m = pm.valuta_coppia(call, cand, oggi=OGGI)
    return ",".join(a.codice for a in m.attenzione) or "soddisfatto"


def test_inferenza_30_budget_esito_cambia_solo_ai_bordi_di_fascia():
    """Anche se il creatore potesse scegliere il budget al centesimo, l'esito
    sui terzi dipende solo dalla FASCIA: due aziende nella stessa fascia
    (500k_2m) hanno sempre lo stesso esito, e l'esito cambia solo quando il
    costo della quota (20% del budget) attraversa 0,6 × gli estremi della
    fascia (0,6 × 500 k€ e 0,6 × 2 M€, cioè budget 1,5 M€ e 6 M€)."""
    budget = [Decimal(250_000 * i) for i in range(1, 31)]  # 250 k€ … 7,5 M€
    bassa, alta = Y(esercizi=esercizi(600_000)), Y(esercizi=esercizi(1_900_000))
    stati = []
    for b in budget:
        call = replace(call_guida(), budget=(b, b))
        stato = _stato_finanziario(call, bassa)
        assert _stato_finanziario(call, alta) == stato, b
        stati.append(stato)
    cambi = [budget[i] for i in range(1, len(budget)) if stati[i] != stati[i - 1]]
    assert cambi == [Decimal(1_750_000), Decimal(6_250_000)]
    assert stati[budget.index(Decimal(1_500_000))] == "soddisfatto"
    assert stati[budget.index(Decimal(1_750_000))] == "dipende_fascia"
    assert stati[budget.index(Decimal(6_000_000))] == "dipende_fascia"
    assert stati[-1] == "finanziaria_non_soddisfatta"


# ================================================================== builder


class TestBuilder:
    def test_budget_sempre_la_fascia_pubblica(self):
        call = g.call_guida()
        assert g.CALL_GUIDA["budget_progetto_eur"] is not None
        assert call.budget == (Decimal(2_000_000), Decimal(5_000_000))
        assert pm.call_snapshot_da(
            {**g.CALL_GUIDA, "budget_fascia": None}, [], [], g.profilo("X")
        ).budget is None

    def test_testi_pubblici_e_campi(self):
        call = pm.call_snapshot_da(
            {**g.CALL_GUIDA, "sospesa_at": "2026-10-02T10:00:00+00:00",
             "forma_aggregazione_prevista": "altra"},
            g.REQUISITI_GUIDA, g.POSIZIONI_GUIDA, g.profilo("X"),
            pubblico=lambda t: f"[{t}]",
        )
        assert call.sospesa and call.forma is None
        assert [r.etichetta for r in call.requisiti] == ["[A]", "[B]", "[C]", "[D]", "[E]", "[F]"]
        assert call.posizioni[0].titolo == "[Ricerca e prototipazione]"
        assert call.posizioni[0].quota_pct == Decimal("20.00")
        assert [r.cercato for r in call.requisiti] == [True, False, True, False, False, False]
        assert call.scadenza_call == date(2026, 12, 15)

    def test_posizione_con_vincoli_illeggibili_esclusa(self):
        rotta = dict(g.POSIZIONI_GUIDA[0], regioni=[0], territorio_modalita="sede_attuale")
        call = pm.call_snapshot_da(g.CALL_GUIDA, g.REQUISITI_GUIDA, [rotta], g.profilo("X"))
        assert call.posizioni == ()

    def test_profilo_matching_dalla_riga(self):
        y = g.profilo("Y")
        assert (y.visibile, y.anonimo, y.sospeso, y.accetta_inviti) == (True, True, False, True)
        assert y.codice_pubblico == g.CODICE_PUBBLICO["Y"]
        assert y.ruoli_disponibili == ("partner",)
        assert y.forme_accettate == ("ats", "ati_rti")
        assert y.regioni_interesse_ids == frozenset({g.CALABRIA})
        assert y.completezza == 70
        assert isinstance(y, pm.ProfiloCandidato)  # valuta_criterio lo accetta
        riga = dict(g.AZIENDE["Y"]["profilo_partner"], sospeso_at="2026-10-01T00:00:00Z",
                    accetta_inviti=False, ruoli_disponibili=["regista"],
                    forme_accettate=["ats", "inventata"], categorie_bando_escluse=[3, True, "4"])
        z = g.profilo("Y", profilo_partner=riga)
        assert z.sospeso and not z.accetta_inviti
        assert z.ruoli_disponibili is None
        assert z.forme_accettate == ("ats",)
        assert z.categorie_bando_escluse == frozenset({3})
        o = g.profilo("O")
        assert (o.visibile, o.ruoli_disponibili, o.codice_pubblico) == (False, None, None)

    def test_default_fail_closed(self):
        vuoto = pm.ProfiloMatching()
        assert not vuoto.viva and not vuoto.visibile and not vuoto.collegamenti_ok


# ================================================================ viste


class TestViste:
    def test_per_te_esclude_stessa_azienda_e_stesso_owner(self):
        gemella = Y(company_id="c0000000-0000-4000-8000-999999999999", owner_id=g.OWNER["X"])
        indice = IndiceMatching.da_liste(g.calls(), [gemella])
        assert [m.call_id for m in pm.per_te(indice, gemella.company_id, oggi=OGGI)] == [
            g.CALL_ALTRA_ID
        ]

    def test_per_te_senza_opt_in_con_il_profilo_passato(self):
        v = g.profilo("V")
        assert g.COMPANY["V"] not in g.indice().candidati
        assert pm.per_te(g.indice(), g.COMPANY["V"], oggi=OGGI) == []
        risultati = pm.per_te(g.indice(), v, oggi=OGGI)
        assert [m.call_id for m in risultati] == [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]

    def test_solo_match_rilevanti(self):
        # Nessun requisito cercato coperto e nessuna posizione coperta.
        irrilevante = Y(tipi_dichiarati=("universita",), competenze=("turismo_cultura_creativita",),
                        esperienze=())
        indice = IndiceMatching.da_liste(g.calls(), [irrilevante])
        assert pm.per_te(indice, irrilevante.company_id, oggi=OGGI) == []
        assert pm.suggeriti_per_call(indice, CALL_ID, oggi=OGGI) == []

    def test_filtri_dei_suggeriti(self):
        indice = g.indice()
        tutti = pm.suggeriti_per_call(indice, CALL_ID, oggi=OGGI)
        assert len(tutti) == 2
        assert pm.suggeriti_per_call(
            indice, CALL_ID, FiltriSuggeriti(min_punteggio=86), oggi=OGGI
        ) == tutti[:1]
        assert pm.suggeriti_per_call(
            indice, CALL_ID, FiltriSuggeriti(min_coperti=3), oggi=OGGI
        ) == []
        assert pm.suggeriti_per_call(
            indice, CALL_ID, FiltriSuggeriti(posizione_id=g.POS_P1), oggi=OGGI
        ) == tutti
        assert pm.suggeriti_per_call(
            indice, CALL_ID, FiltriSuggeriti(posizione_id="altra"), oggi=OGGI
        ) == []
        assert pm.suggeriti_per_call(indice, "inesistente", oggi=OGGI) == []

    def test_revoca_nell_indice_esce_subito(self):
        indice = g.indice()
        revocato = replace(indice.candidati[g.COMPANY["Y"]], visibile=False)
        dopo = IndiceMatching(
            calls=indice.calls,
            candidati={**indice.candidati, g.COMPANY["Y"]: revocato},
        )
        ids = [m.company_id for m in pm.suggeriti_per_call(dopo, CALL_ID, oggi=OGGI)]
        assert g.COMPANY["Y"] not in ids
