"""Test del pre-classificatore deterministico (WP3): formule del §1.4 della
spec in italiano e in inglese, falsi positivi noti del catalogo, livelli,
forma dei segnali e prestazioni."""

import json
import random
import re
import time

import pytest

from app.services import partenariato_preclassificatore as pc
from app.services.partenariato_preclassificatore import (
    MAX_ESTRATTO,
    PATTERN,
    Preclassificazione,
    preclassifica,
)


def _pattern(testo: str) -> set[tuple[str, str]]:
    return {(s.categoria, s.pattern) for s in preclassifica({"S1": testo}).segnali}


def _livello(testo: str) -> str:
    return preclassifica({"S1": testo}).livello


# Input patologici (unità ripetute fino alla dimensione del test).
_PATOLOGICI = [
    "ATS ", "A.T.S. ", "consorzio ", "partenariato ", "accordo di partenariato ",
    "aggregazioni ", "capofila ", "comune ", "quota ", "almeno 2 ", "RTI gestore ",
    "a" * 200_000, "1." * 1_000, "quota almeno " + "x" * 3_000 + " ",
    "nessun partner " + "x" * 3_000 + " ", "ciascun partner " + "1" * 3_000 + " ",
]


def _vocabolario() -> list[str]:
    """Frammenti letterali delle regex del modulo (pattern ed esclusioni)."""
    parole: set[str] = set()
    for pattern in PATTERN:
        for regex in (pattern.regex, *pattern.escludi, *pattern.escludi_contesto):
            pulito = re.sub(r"\\[a-zA-Z]|\(\?[:=!<]*|[()\[\]{}?*+|^$\\]", " ", regex.pattern)
            parole.update(p for p in pulito.split() if not p.isdigit())
    return sorted(parole)


_VOCABOLARIO = _vocabolario()
# Falsi amici noti (le esclusioni) e le formule che li contengono.
_FRASI = [
    "consorzio di tutela", "consorzi di bonifica", "consorzio per lo sviluppo industriale",
    "consorzio universitario", "consorzio di garanzia collettiva dei fidi", "consorzio",
    "ATS (Agenzia di Tutela della Salute)", "tutela della salute - ATS", "ATS della Brianza",
    "ATS", "A.T.S.", "ATI", "RTI", "R.T.I.", "soggetto gestore", "strumenti finanziari",
    "partenariato pubblico-privato", "PPP", "in partenariato", "partenariato",
    "accordo di partenariato 2021", "nell'ambito dell'accordo di partenariato",
    "accordo di partenariato", "comune capofila", "comuni del distretto capofila",
    "capofila del piano di zona", "ente capofila dell'ambito", "capofila del partenariato",
    "capofila", "aggregazione sociale", "centro di aggregazione", "aggregazione dei dati",
    "aggregazioni", "mandatario", "public-private partnership", "partnership",
    "raggruppamento temporaneo", "associazione temporanea di imprese",
]
_SEPARATORI = [" ", " ", " ", ", ", ". ", "; ", "-", "'", " % ", " 12 ", " 1,5 ", " (", ") "]


def _testo_generato(seme: int, lunghezza: int, *, lunghi: bool = False) -> str:
    rnd = random.Random(seme)
    pezzi: list[str] = []
    totale = 0
    while totale < lunghezza:
        if lunghi and rnd.random() < 0.02:
            parola = rnd.choice(["x", "1", ".", "a.", "-"]) * rnd.randint(200, 3_000)
        elif rnd.random() < 0.3:
            parola = rnd.choice(_FRASI)
        else:
            parola = rnd.choice(_VOCABOLARIO)
        pezzo = parola + rnd.choice(_SEPARATORI)
        pezzi.append(pezzo)
        totale += len(pezzo)
    return "".join(pezzi)[:lunghezza]


def _escluso_a_finestre(pattern, testo: str, inizio: int, fine: int) -> bool:
    """Riferimento: le esclusioni cercate in una finestra attorno a ogni
    corrispondenza (la versione a costo corrispondenze × finestra)."""
    da = max(0, inizio - pc._CONTESTO)
    finestra = testo[da : fine + pc._CONTESTO]
    s, e = inizio - da, fine - da
    for regex in pattern.escludi:
        if any(m.start() < e and m.end() > s for m in regex.finditer(finestra)):
            return True
    return any(regex.search(finestra) for regex in pattern.escludi_contesto)


# ------------------------------------------------------------ positivi

POSITIVI = [
    # ammette
    ("Possono presentare domanda le PMI in forma singola o associata.",
     "ammette", "forma_singola_o_associata"),
    ("Le imprese possono partecipare in forma singola oppure in ATS.",
     "ammette", "forma_singola_o_associata"),
    ("La domanda può essere presentata anche in forma congiunta tramite contratto di rete.",
     "ammette", "contratto_di_rete"),
    ("Sono finanziati progetti congiunti tra imprese e università.",
     "ammette", "progetti_congiunti"),
    ("in qualità di co-proponenti o partner di progetti collaborativi",
     "ammette", "co_proponenti"),
    ("Il bando si rivolge ad aggregazioni di PMI del Veneto.",
     "ammette", "aggregazioni_di_imprese"),
    ("costituite in associazione temporanea di scopo", "ammette", "ats_esteso"),
    ("anche costituite in ATI/ATS con almeno due PMI", "ammette", "sigla_ats"),
    ("Associazioni Temporanee di Scopo (A.T.S.); Contratti di Rete; Consorzio",
     "ammette", "sigla_ats"),
    ("raggruppamenti temporanei composti da almeno due PMI piemontesi",
     "ammette", "ati_rti_esteso"),
    ("Possono partecipare i RTI costituiti o costituendi.", "ammette", "sigla_ati_rti"),
    ("Horizon Europe che richiedono consorzi transnazionali", "ammette", "consorzio_ue"),
    ("reti di imprese con contratto di rete", "ammette", "contratto_di_rete"),
    ("Il capofila sottoscrive un accordo di partenariato con i partner.",
     "ammette", "accordo_di_partenariato"),
    # richiede
    ("associazione temporanea composta da almeno due PMI piemontesi",
     "richiede", "almeno_n_soggetti"),
    ("Il partenariato deve essere composto da almeno tre soggetti.",
     "richiede", "partenariato_composto"),
    ("il raggruppamento costituito da almeno 3 imprese", "richiede", "composto_da_almeno"),
    ("forma congiunta tra più imprese e in collaborazione con organismi di ricerca",
     "richiede", "collaborazione_ricerca"),
    ("L'impresa capofila presenta la domanda per conto del raggruppamento.",
     "richiede", "capofila_ruolo"),
    ("anche in qualità di capofila di reti progettuali", "richiede", "capofila_ruolo"),
    ("con mandato collettivo speciale con rappresentanza al capofila",
     "richiede", "mandato_collettivo"),
    # vincola
    ("La quota di ciascun partner non può essere inferiore al 10% del costo totale.",
     "vincola", "quota_percentuale"),
    ("Nessun partner può sostenere più del 70% dei costi ammissibili.",
     "vincola", "nessun_partner"),
    ("Ciascun partner deve sostenere almeno il 5% delle spese.",
     "vincola", "ciascun_partner_percentuale"),
    ("Le imprese partecipanti non devono essere tra loro collegate.",
     "vincola", "indipendenti"),
    ("I partner devono essere imprese indipendenti.", "vincola", "indipendenti"),
    ("Ogni impresa può essere capofila di una sola ATS.", "vincola", "un_solo_partenariato"),
    ("Un soggetto non può partecipare a più raggruppamenti.", "vincola", "un_solo_partenariato"),
    ("i partner devono avere sede in paesi membri diversi", "vincola", "paesi_distinti"),
    # nega
    ("Il contributo è concesso esclusivamente in forma singola.", "nega", "solo_forma_singola"),
    ("Non sono ammesse aggregazioni di imprese né raggruppamenti.",
     "nega", "solo_forma_singola"),
    ("Non è ammessa la partecipazione in forma associata.", "nega", "solo_forma_singola"),
]

INGLESE = [
    ("The consortium must include at least three independent legal entities.",
     "richiede", "consortium"),
    ("must include at least three independent legal entities",
     "richiede", "at_least_n_entities"),
    ("each established in a different Member State", "vincola", "paesi_distinti"),
    ("at least three independent legal entities", "vincola", "independent_entities"),
    ("Affiliated entities and associated partners do not count.",
     "ammette", "affiliated_entities"),
    ("This is a collaborative research action with joint proposals.",
     "ammette", "joint_application"),
    ("The coordinator submits the proposal on behalf of the consortium.",
     "richiede", "coordinator"),
    ("MSCA Postdoctoral Fellowships are mono-beneficiary actions.", "nega", "mono_beneficiary"),
]


# Frammenti del campione WP3 (map del catalogo, bandi 1-25): tutti con un
# segnale forte.
CAMPIONE_CATALOGO = [
    "associazione temporanea composta da almeno due PMI piemontesi",
    "ATI/ATS con almeno due PMI piemontesi",
    "raggruppamenti temporanei composti da almeno due PMI piemontesi",
    "anche in forma singola o costituite in ATI/ATS",
    "PMI lucane, in forma singola o associata",
    "in forma singola o associata (con un capofila)",
    "Possono partecipare in partenariato transnazionale: enti pubblici",
    "ATS costituite tra sole Agenzie formative",
    "Associazioni Temporanee di Scopo (A.T.S.); Contratti di Rete; Consorzio",
    "anche in forma congiunta tramite contratto di rete",
    "anche in forma congiunta tramite contratti di rete",
    "proposto da un'impresa capofila, all'interno di filiere produttive",
    "forma congiunta tra più imprese e in collaborazione con organismi di ricerca",
    "in qualità di co-proponenti o partner di progetti collaborativi",
    "Eventuali accordi di collaborazione con organismi di ricerca",
    "in collaborazione con università e organismi di ricerca",
    "reti di imprese che presentano progetti congiunti",
    "reti di imprese con contratto di rete",
    "si rivolge ad aggregazioni di PMI del Veneto",
    "Comuni lombardi in forma singola o associata",
    "anche in qualità di capofila di reti progettuali",
    "deve includere almeno tre soggetti giuridici indipendenti",
    "Horizon Europe che richiedono consorzi transnazionali",
    "si rivolge a consorzi internazionali che riuniscono soggetti",
    "partner a progetti congiunti italo-statunitensi su intelligenza artificiale",
]


class TestPositivi:
    @pytest.mark.parametrize("testo", CAMPIONE_CATALOGO)
    def test_campione_del_catalogo(self, testo):
        assert _livello(testo) == "forte", _pattern(testo)

    def test_forma_singola_o_costituite_in_ati(self):
        testo = "anche in forma singola o costituite in ATI/ATS"
        assert ("ammette", "forma_singola_o_associata") in _pattern(testo)
        assert ("nega", "forma_singola") not in _pattern(testo)

    def test_in_partenariato_pubblico_privato_escluso(self):
        assert _livello("opere realizzate in partenariato pubblico-privato") == "nessuno"

    @pytest.mark.parametrize(("testo", "categoria", "pattern"), POSITIVI)
    def test_italiano(self, testo, categoria, pattern):
        assert (categoria, pattern) in _pattern(testo)
        assert _livello(testo) == "forte"

    @pytest.mark.parametrize(("testo", "categoria", "pattern"), INGLESE)
    def test_inglese_ue(self, testo, categoria, pattern):
        assert (categoria, pattern) in _pattern(testo)

    def test_apostrofi_tipografici_e_spazi(self):
        testo = "Il progetto è presentato da un’aggregazione\ndi   imprese del territorio."
        assert ("ammette", "aggregazioni_di_imprese") in _pattern(testo)

    def test_segnali_generici_danno_livello_debole(self):
        assert _livello("Il partner tecnologico fornisce la piattaforma.") == "debole"
        assert _livello("Rientrano i consorzi e le cooperative.") == "debole"
        assert _livello("Il soggetto proponente è il capofila.") == "debole"
        assert _livello("Si presenta in forma singola.") == "debole"


# ------------------------------------------------------------ falsi positivi

FALSI_POSITIVI = [
    "ATS (Agenzia di Tutela della Salute) della Città Metropolitana di Milano",
    "L'Agenzia di Tutela della Salute (ATS) rilascia il parere.",
    "L'ATS Brianza e l'ATS Insubria verificano i requisiti igienico-sanitari.",
    "Possono partecipare i Consorzi di tutela dei vini DOC.",
    "contributi ai consorzi di bonifica",
    "in coerenza con l'Accordo di partenariato 2021-2027 tra Italia e Commissione",
    "come previsto dall'Accordo di Partenariato",
    "interventi realizzati in partenariato pubblico-privato (PPP)",
    "centri di aggregazione giovanile e spazi di aggregazione sociale",
    "attività di aggregazione sociale per anziani",
    "Il Comune capofila del distretto presenta la domanda.",
    "l'ente capofila dell'ambito territoriale sociale",
    "Il fondo è gestito dal RTI composto da due banche, con la mandataria Mediocredito.",
    "spese non strettamente collegate al progetto",
    "Le imprese collegate tra loro sono considerate un'impresa unica ai fini del de minimis.",
    "La domanda è presentata singolarmente: non sono ammesse proposte multiple.",
    "Le statistiche (STATS) del bando e i dati di ATSeco.",
    "public-private partnership",
    "Ogni corso deve avere almeno 8 partecipanti e una durata di almeno 2 anni.",
]

NEGATIVI = [
    "Il fondo microfinanza sostiene le imprese con prestiti fino a 25.000 euro.",
    "Il voucher si rivolge ai liberi professionisti iscritti agli Ordini professionali.",
    "Incentivi alle imprese e ai datori di lavoro privati che assumono disoccupati over 36.",
    "Borsa di studio destinata a studenti e neolaureati.",
    "Il contributo a fondo perduto copre il 50% delle spese ammissibili.",
    "",
]


class TestFalsiPositiviENegativi:
    @pytest.mark.parametrize("testo", FALSI_POSITIVI)
    def test_falsi_positivi_noti_esclusi(self, testo):
        assert _livello(testo) == "nessuno", _pattern(testo)

    @pytest.mark.parametrize("testo", NEGATIVI)
    def test_negativi(self, testo):
        assert _livello(testo) == "nessuno"

    def test_ats_vero_accanto_a_ats_salute(self):
        testo = (
            "Il parere dell'ATS Milano è obbligatorio. Le imprese possono partecipare "
            "costituendo una ATS con capofila."
        )
        assert ("ammette", "sigla_ats") in _pattern(testo)

    def test_sigla_minuscola_non_e_una_sigla(self):
        assert ("ammette", "sigla_ats") not in _pattern("gli ats e le ati")

    def test_nessuna_sezione(self):
        assert preclassifica({}).livello == "nessuno"
        assert preclassifica({"S1": None}).livello == "nessuno"  # type: ignore[dict-item]


# ------------------------------------------------------------ forma


class TestForma:
    def test_segnale_per_pattern_e_sezione_e_conteggi(self):
        sezioni = {
            "META": "Beneficiari (catalogo): PMI",
            "S1": "in forma singola o associata. Anche in forma associata. E in forma aggregata.",
            "D1-p3": "Le PMI in forma associata. Nessun partner può superare il 70%.",
        }
        esito = preclassifica(sezioni)
        assert esito.livello == "forte"
        assert "META" not in esito.per_sezione
        assert esito.per_sezione["S1"] == 3
        chiavi = [(s.pattern, s.sezione) for s in esito.segnali]
        assert chiavi.count(("forma_singola_o_associata", "S1")) == 1
        assert ("forma_singola_o_associata", "D1-p3") in chiavi
        # ordine: prima le sezioni nell'ordine del dizionario
        assert [s.sezione for s in esito.segnali][0] == "S1"

    def test_estratto_breve_e_centrato(self):
        riempitivo = "parola " * 200
        testo = f"{riempitivo} le imprese in forma singola o associata {riempitivo}"
        segnale = preclassifica({"S1": testo}).segnali[0]
        assert len(segnale.estratto) <= MAX_ESTRATTO
        assert "in forma singola o associata" in segnale.estratto

    def test_a_dict_serializzabile(self):
        esito = preclassifica({"S1": "Il partenariato deve essere composto da almeno tre PMI."})
        dati = esito.a_dict()
        assert json.loads(json.dumps(dati)) == dati
        assert set(dati) == {"segnali", "per_sezione", "livello"}
        assert set(dati["segnali"][0]) == {"categoria", "pattern", "sezione", "estratto"}

    def test_non_decide_la_modalita(self):
        esito = preclassifica({"S1": "Non sono ammesse aggregazioni. In forma associata."})
        assert isinstance(esito, Preclassificazione)
        assert not hasattr(esito, "modalita")
        assert {s.categoria for s in esito.segnali} == {"nega", "ammette"}


class TestPrestazioni:
    def test_quattrocentomila_caratteri(self):
        paragrafo = (
            "Le imprese beneficiarie devono avere almeno una sede operativa in regione. La quota "
            "di cofinanziamento non può essere inferiore al 20% delle spese. Ciascun soggetto "
            "partecipante presenta la documentazione entro 30 giorni in forma associata. "
        )
        sezioni = {f"D1-p{i}": paragrafo * 9 for i in range(1, 201)}
        assert sum(len(t) for t in sezioni.values()) >= 400_000
        inizio = time.process_time()
        esito = preclassifica(sezioni)
        durata = time.process_time() - inizio
        assert esito.livello == "forte"
        # Obiettivo di progetto < 200 ms; margine per macchine lente in CI.
        assert durata < 1.0

    # Tempo di CPU del processo (`process_time`), non del muro: il carico di
    # altri processi non conta. Tipicamente sotto 0,2 s: il limite ha un
    # margine di almeno 5 volte.
    @pytest.mark.parametrize("unita", _PATOLOGICI, ids=lambda _: "patologico")
    def test_input_patologici_200_kb_una_sezione(self, unita):
        testo = (unita * (200_000 // len(unita) + 1))[:200_000]
        inizio = time.process_time()
        preclassifica({"D1-p1": testo})
        assert time.process_time() - inizio < 1.0

    @pytest.mark.parametrize("unita", _PATOLOGICI, ids=lambda _: "patologico")
    def test_input_patologici_200_kb_in_molte_pagine(self, unita):
        testo = (unita * (200_000 // len(unita) + 1))[:200_000]
        sezioni = {f"D1-p{i + 1}": testo[j : j + 2_000] for i, j in
                   enumerate(range(0, len(testo), 2_000))}
        inizio = time.process_time()
        preclassifica(sezioni)
        assert time.process_time() - inizio < 1.0

    @pytest.mark.parametrize("seme", range(3))
    def test_testi_generati_200_kb(self, seme):
        testo = _testo_generato(seme, 200_000, lunghi=seme == 2)
        inizio = time.process_time()
        preclassifica({"D1-p1": testo})
        assert time.process_time() - inizio < 1.0


class TestEsclusioniUnaVoltaPerSezione:
    """Le esclusioni si cercano una volta per sezione: stesso esito della
    ricerca in una finestra attorno a ogni corrispondenza, salvo ai confini
    della finestra, dove vale il testo intero."""

    @pytest.mark.parametrize(
        ("testo", "attesi"),
        [
            ("la digestione " + "x" * 87 + " un RTI tra imprese", {"sigla_ati_rti"}),
            ("un RTI tra imprese " + "y" * 79 + " gestorex fine", {"sigla_ati_rti"}),
            ("il comune " + "w" * 120 + " capofila del progetto", set()),
        ],
    )
    def test_ai_confini_della_finestra_vale_il_testo_intero(self, testo, attesi):
        assert {s.pattern for s in preclassifica({"S1": testo}).segnali} == attesi

    def test_stesso_esito_della_ricerca_a_finestre_sui_testi_generati(self, monkeypatch):
        testi = [_testo_generato(seme, 4_000, lunghi=seme % 3 == 0) for seme in range(60)]
        attesi = [preclassifica({"D1-p1": t, "S1": t[:1_500]}) for t in testi]
        esiti: list[bool] = []

        def a_finestre(pattern, esclusioni, inizio, fine):
            esiti.append(_escluso_a_finestre(pattern, esclusioni._testo, inizio, fine))
            return esiti[-1]

        monkeypatch.setattr(pc, "_escluso", a_finestre)
        assert [preclassifica({"D1-p1": t, "S1": t[:1_500]}) for t in testi] == attesi
        # il confronto non è banale: segnali, corrispondenze escluse e no
        assert sum(len(e.segnali) for e in attesi) > 500
        assert esiti.count(True) > 1_000 and esiti.count(False) > 1_000

    @pytest.mark.parametrize(
        "testo",
        [
            "Il consorzio di tutela del vino e un consorzio di imprese.",
            "La ATS (Agenzia di Tutela della Salute) e le ATS costituite dai partner.",
            "Il Comune capofila del distretto e il soggetto capofila del partenariato.",
            "Il RTI è il soggetto gestore del fondo. " + "parola " * 40 + "Ammesse le RTI.",
        ],
    )
    def test_casi_noti_come_prima(self, testo, monkeypatch):
        atteso = preclassifica({"S1": testo})
        monkeypatch.setattr(
            pc, "_escluso",
            lambda pattern, esclusioni, inizio, fine: _escluso_a_finestre(
                pattern, esclusioni._testo, inizio, fine),
        )
        assert preclassifica({"S1": testo}) == atteso
