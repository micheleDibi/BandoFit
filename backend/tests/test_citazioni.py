"""Test della verifica delle citazioni (services/citazioni.py): artefatti dei
PDF, chiavi di sezione, cavallo di pagina, ellissi, negativi e proprietà
«tutto ciò che accetta la vecchia verifica dell'AI-check lo accetta anche la
nuova»."""

import itertools
from types import SimpleNamespace

import pytest

from app.services.ai_check_scoring import _citation_verified
from app.services.citazioni import (
    normalizza_sezione,
    normalizza_testo,
    verifica_citazione,
)
from tests.test_ai_check_scoring import SECTIONS as SEZIONI_AI_CHECK


def _vecchia(sezione: str, testo: str, sezioni: dict) -> bool:
    citazione = SimpleNamespace(sezione=sezione, testo_esatto=testo)
    return _citation_verified(citazione, sezioni)


# ------------------------------------------------------------ normalizzazione


class TestNormalizzaTesto:
    def test_legature_apostrofi_virgolette_trattini(self):
        assert normalizza_testo("La ﬁnalità è ﬂessibile") == "la finalità è flessibile"
        assert normalizza_testo("L’impresa “attiva” — 2021–2027") == (
            "l'impresa \"attiva\" - 2021-2027"
        )
        assert normalizza_testo("«Soggetti»") == '"soggetti"'

    def test_spazi_maiuscole_grassetto(self):
        assert normalizza_testo("  Le **MICRO**\n\n imprese e  PMI ") == "le micro imprese e pmi"

    def test_sillabazione_soft_hyphen_larghezza_zero(self):
        assert normalizza_testo("ammis-\nsibili") == "ammissibili"
        assert normalizza_testo("ammis- \n  sibili") == "ammissibili"
        assert normalizza_testo("parte­\ncipazione") == "partecipazione"
        assert normalizza_testo("parte­cipazione") == "partecipazione"
        assert normalizza_testo("ammis​sibili") == "ammissibili"

    def test_trattino_normale_senza_a_capo_resta(self):
        assert normalizza_testo("pubblico-privato") == "pubblico-privato"

    def test_valori_non_stringa(self):
        assert normalizza_testo(None) == ""  # type: ignore[arg-type]
        assert normalizza_testo("") == ""


class TestNormalizzaSezione:
    @pytest.mark.parametrize(
        "grezza",
        ["D1-p3", "[D1-P3]", "D1 p3", "d1-p3", "D1 pag. 3", "D1, pagina 3", "D1p3", "D1-3",
         " [ D1 - p3 ] ", "D01-p03", "D1 page 3"],
    )
    def test_chiavi_documento(self, grezza):
        assert normalizza_sezione(grezza) == "D1-p3"

    @pytest.mark.parametrize(
        ("grezza", "attesa"),
        [("S2", "S2"), ("[s2]", "S2"), (" meta ", "META"), ("[META]", "META"), ("D13", "D13"),
         ("D1", "D1"), ("A1", "A1")],
    )
    def test_altre_chiavi(self, grezza, attesa):
        assert normalizza_sezione(grezza) == attesa

    def test_non_stringa(self):
        assert normalizza_sezione(None) == ""  # type: ignore[arg-type]


# ------------------------------------------------------------ verifica

SEZIONI = {
    "META": "Titolo: Avviso Innovazione 2026\nBeneficiari (catalogo): PMI",
    "S1": "Possono partecipare le **PMI** in forma singola o associata.",
    "D1-p3": (
        "Art. 4 – Soggetti beneficiari\n"
        "L’impresa capoﬁla deve essere un’impresa attiva. Sono ammis-\n"
        "sibili i partenariati «pubblico-\nprivati» costituiti nel 2021–2027.\n"
        "Il partenariato deve essere composto da"
    ),
    "D1-p4": (
        "almeno tre imprese indipendenti tra loro.\n"
        "Nessun partner può sostenere più del 70% dei costi del pro­\ngetto."
    ),
    "D2-p1": "Documento con un​ carattere invisibile e la parte­cipazione.",
}


class TestVerificaCitazione:
    def test_legatura_e_apostrofo_tipografico(self):
        assert verifica_citazione(
            "D1-p3", "L'impresa capofila deve essere un'impresa attiva.", SEZIONI
        )

    def test_apostrofo_tipografico_nell_ago(self):
        assert verifica_citazione("S1", "Possono partecipare le PMI in forma singola", SEZIONI)
        assert verifica_citazione("D1-p3", "L’impresa capoﬁla", SEZIONI)

    def test_sillabazione_a_fine_riga(self):
        assert verifica_citazione("D1-p3", "Sono ammissibili i partenariati", SEZIONI)
        # Il modello copia la sillabazione come «ammis- sibili».
        assert verifica_citazione("D1-p3", "Sono ammis- sibili i partenariati", SEZIONI)

    def test_trattino_reale_a_fine_riga(self):
        # «pubblico-\nprivati»: con la sillabazione diventa «pubblicoprivati»,
        # ma il confronto insensibile ai trattini la riconosce.
        assert verifica_citazione("D1-p3", "i partenariati \"pubblico-privati\"", SEZIONI)

    def test_virgolette_caporali_e_trattino_lungo(self):
        assert verifica_citazione("D1-p3", "costituiti nel 2021-2027", SEZIONI)
        assert verifica_citazione("D1-p3", "«pubblico-privati»", SEZIONI)

    def test_soft_hyphen_e_larghezza_zero(self):
        assert verifica_citazione("D1-p4", "più del 70% dei costi del progetto", SEZIONI)
        assert verifica_citazione("D2-p1", "Documento con un carattere invisibile", SEZIONI)
        assert verifica_citazione("D2-p1", "la partecipazione", SEZIONI)

    def test_grassetto_maiuscole_spazi(self):
        assert verifica_citazione("S1", "le   pmi in FORMA singola\no associata", SEZIONI)

    def test_virgolette_e_punto_ai_bordi(self):
        assert verifica_citazione("S1", '"in forma singola o associata."', SEZIONI)

    @pytest.mark.parametrize("chiave", ["[D1-P3]", "D1 p3", "d1-p3", "D1 pag. 3", "D1p3"])
    def test_chiave_di_sezione_normalizzata(self, chiave):
        assert verifica_citazione(chiave, "Soggetti beneficiari", SEZIONI)

    def test_chiavi_del_dizionario_normalizzate(self):
        sezioni = {"[d1-P3]": "Testo della pagina tre del documento"}
        assert verifica_citazione("D1-p3", "pagina tre del documento", sezioni)

    def test_cavallo_di_pagina(self):
        assert verifica_citazione(
            "D1-p3", "Il partenariato deve essere composto da almeno tre imprese", SEZIONI
        )

    def test_cavallo_indicato_con_la_pagina_dove_finisce(self):
        # Il modello indica la pagina dove la citazione FINISCE: inizia in
        # fondo alla precedente.
        assert verifica_citazione(
            "D1-p4", "Il partenariato deve essere composto da almeno tre imprese", SEZIONI
        )

    def test_citazione_tutta_nella_pagina_precedente_non_vale(self):
        # Non tocca la pagina indicata: la pagina citata è sbagliata.
        assert not verifica_citazione(
            "D1-p4", "L'impresa capofila deve essere un'impresa attiva.", SEZIONI
        )
        assert not verifica_citazione(
            "D1-p4", "Soggetti beneficiari ... L'impresa capofila deve essere", SEZIONI
        )

    def test_ellissi_a_cavallo_indicata_con_la_pagina_finale(self):
        # Due tratti uniti da «...», il primo nella pagina precedente (una
        # tabella che continua), indicati con la pagina del secondo.
        assert verifica_citazione(
            "D1-p4",
            "Art. 4 – Soggetti beneficiari ... almeno tre imprese indipendenti tra loro",
            SEZIONI,
        )
        # In ordine inverso no.
        assert not verifica_citazione(
            "D1-p4",
            "almeno tre imprese indipendenti tra loro ... Art. 4 – Soggetti beneficiari",
            SEZIONI,
        )

    def test_pagine_non_adiacenti_non_si_uniscono(self):
        sezioni = {
            "D1-p2": "Il partenariato deve essere composto da",
            "D1-p4": "almeno tre imprese indipendenti tra loro.",
        }
        for chiave in ("D1-p2", "D1-p4"):
            assert not verifica_citazione(
                chiave, "Il partenariato deve essere composto da almeno tre imprese", sezioni
            )

    def test_prima_pagina_senza_precedente(self):
        sezioni = {"D1-p1": "Testo della prima pagina del documento", "D1-p0": "prima di"}
        assert verifica_citazione("D1-p1", "prima pagina del documento", sezioni)
        # «D1-p0» non è una pagina precedente valida.
        assert not verifica_citazione("D1-p1", "prima di Testo della prima pagina", sezioni)

    def test_nessun_cavallo_senza_pagina_successiva(self):
        assert not verifica_citazione("D2-p1", "la partecipazione. Altro testo", SEZIONI)

    @pytest.mark.parametrize("ellissi", ["...", "…", "[...]", "[…]", "(...)"])
    def test_ellissi_frammenti_in_ordine(self, ellissi):
        citazione = (
            f"Il partenariato deve essere composto {ellissi} almeno tre imprese indipendenti"
        )
        assert verifica_citazione("D1-p3", citazione, SEZIONI)

    def test_ellissi_nella_stessa_pagina(self):
        assert verifica_citazione(
            "D1-p3", "L'impresa capofila deve essere … costituiti nel 2021-2027", SEZIONI
        )

    def test_ellissi_ordine_sbagliato(self):
        citazione = "almeno tre imprese indipendenti ... Il partenariato deve essere composto"
        assert not verifica_citazione("D1-p3", citazione, SEZIONI)

    def test_ellissi_con_frammento_corto(self):
        assert not verifica_citazione("D1-p3", "Il partenariato ... imprese", SEZIONI)

    def test_ellissi_iniziale_o_finale(self):
        assert verifica_citazione("D1-p3", "... deve essere un'impresa attiva ...", SEZIONI)

    def test_negativi(self):
        assert not verifica_citazione("D1-p3", "testo inventato dal modello", SEZIONI)
        # Testo vero, sezione sbagliata: non si cerca altrove.
        assert not verifica_citazione("S1", "L'impresa capofila deve essere", SEZIONI)
        assert not verifica_citazione("D9-p1", "Soggetti beneficiari", SEZIONI)
        assert not verifica_citazione("", "Soggetti beneficiari", SEZIONI)
        assert not verifica_citazione("D1-p3", "", SEZIONI)
        assert not verifica_citazione("D1-p3", "   ", SEZIONI)
        assert not verifica_citazione("D1-p3", "**", SEZIONI)
        assert not verifica_citazione("D1-p3", "Soggetti beneficiari", {})
        assert not verifica_citazione(None, "x", SEZIONI)  # type: ignore[arg-type]
        assert not verifica_citazione("S1", None, SEZIONI)  # type: ignore[arg-type]

    def test_parola_diversa_non_passa(self):
        assert not verifica_citazione("D1-p4", "almeno quattro imprese indipendenti", SEZIONI)
        assert not verifica_citazione("D1-p4", "più del 80% dei costi", SEZIONI)

    def test_sezione_vuota(self):
        assert not verifica_citazione("S9", "qualcosa", {"S9": ""})


# Spazi del testo dei PDF: il lettore li deduce dalla distanza tra i glifi, e
# ne aggiunge dentro le parole o attorno alla punteggiatura.
SEZIONI_SPAZI = {
    "D1-p8": (
        "5.1 L’aggregazione è formata con la firma dell’”A ccordo operativo” ( Allegato B) e\n"
        "deve contare almeno n. 3 imprese  aderenti."
    ),
    "D1-p1": (
        "Beneficiari: Micro, Piccole e Medie Imprese (MPMI) , come definite dal Reg. (UE) "
        "n. 651/ 2014,\nin forma singola o aggregata con un Organismo di Ricerca."
    ),
    "D1-p2": "Il capofi la presenta la domanda. Gli altri par tner firmano il mandato.",
    "D2-p1": "Sono ammessi almeno 2 5 partner diversi tra loro nel progetto comune.",
    "D3-p1": "Sono ammessi almeno 25 partner diversi tra loro nel progetto comune.",
}


class TestSpaziDelPdf:
    def test_spazio_dentro_una_parola_e_dopo_la_parentesi(self):
        assert verifica_citazione(
            "D1-p8",
            "L'aggregazione è formata con la firma dell'\"Accordo operativo\" (Allegato B) "
            "e deve contare almeno n. 3 imprese",
            SEZIONI_SPAZI,
        )

    def test_spazio_prima_della_virgola_e_dopo_la_barra(self):
        assert verifica_citazione(
            "D1-p1",
            "Micro, Piccole e Medie Imprese (MPMI), come definite dal Reg. (UE) n. 651/2014, "
            "in forma singola o aggregata",
            SEZIONI_SPAZI,
        )

    def test_spazio_mancante_nel_pdf(self):
        sezioni = {"D1-p5": "La domanda è presentata dal capofila(mandatario)entro il 30 giugno."}
        assert verifica_citazione(
            "D1-p5", "presentata dal capofila (mandatario) entro il 30 giugno", sezioni
        )

    def test_le_lettere_devono_restare_le_stesse(self):
        assert not verifica_citazione(
            "D1-p8", "la firma dell'\"Accordo operativa\" (Allegato B)", SEZIONI_SPAZI
        )
        assert not verifica_citazione(
            "D1-p1", "Medie Imprese (MPMI), come definite dal Reg. (UE) n. 651/2015",
            SEZIONI_SPAZI,
        )

    def test_gli_spazi_tra_cifre_contano(self):
        # «2 5» non è «25» (due celle di una tabella, un numero spezzato): in
        # entrambe le direzioni.
        assert not verifica_citazione(
            "D2-p1", "almeno 25 partner diversi tra loro", SEZIONI_SPAZI
        )
        assert not verifica_citazione(
            "D3-p1", "almeno 2 5 partner diversi tra loro", SEZIONI_SPAZI
        )
        assert verifica_citazione("D2-p1", "almeno 2 5 partner diversi tra loro", SEZIONI_SPAZI)

    @pytest.mark.parametrize("intervallo", ["2 - 5", "2 -5", "2 – 5"])
    def test_un_intervallo_con_spazi_non_diventa_un_numero(self, intervallo):
        # senza spazi il trattino tra due cifre resta: «2 - 5» non è «25»
        sezioni = {"D1-p1": f"Il partenariato composto da {intervallo} imprese è ammesso."}
        assert not verifica_citazione("D1-p1", "partenariato composto da 25 imprese", sezioni)
        assert verifica_citazione("D1-p1", "partenariato composto da 2-5 imprese", sezioni)
        assert verifica_citazione("D1-p1", "partenariato composto da 2 - 5 imprese", sezioni)

    def test_citazione_corta_senza_tolleranza_sugli_spazi(self):
        assert not verifica_citazione("D1-p2", "capofila presenta", SEZIONI_SPAZI)  # < 20
        assert verifica_citazione("D1-p2", "Il capofila presenta la domanda", SEZIONI_SPAZI)

    def test_frammenti_con_ellissi(self):
        assert verifica_citazione(
            "D1-p2", "Il capofila presenta la domanda ... gli altri partner firmano",
            SEZIONI_SPAZI,
        )
        assert not verifica_citazione(
            "D1-p2", "gli altri partner firmano ... Il capofila presenta la domanda",
            SEZIONI_SPAZI,
        )

    def test_a_cavallo_di_pagina(self):
        sezioni = {"D1-p6": "Il partenariato è compo sto da", "D1-p7": "almeno tre im prese."}
        assert verifica_citazione("D1-p6", "Il partenariato è composto da almeno tre imprese",
                                  sezioni)
        assert verifica_citazione("D1-p7", "Il partenariato è composto da almeno tre imprese",
                                  sezioni)
        assert not verifica_citazione("D1-p7", "Il partenariato è composto da", sezioni)


# ------------------------------------------------------------ proprietà


class TestProprietaRispettoAllAiCheck:
    """La nuova verifica non deve mai rifiutare ciò che accettava la vecchia
    (`_citation_verified`, che resta invariata)."""

    CASI_VERI_DEI_TEST_AI_CHECK = [
        # tests/test_ai_check_scoring.py: requisito() di default, criterio(),
        # test_citazione_verificata_come_substring_della_sezione e
        # test_citazione_con_indice_tra_parentesi_quadre_verificata.
        ("S1", "Possono presentare domanda le micro e piccole imprese con sede nel Lazio."),
        ("S2", "Fino a 20 punti per il grado di innovazione"),
        ("S1", "micro e piccole imprese"),
        ("[S1]", "micro e piccole imprese"),
    ]

    @pytest.mark.parametrize(("sezione", "testo"), CASI_VERI_DEI_TEST_AI_CHECK)
    def test_casi_veri_dei_test_ai_check(self, sezione, testo):
        assert _vecchia(sezione, testo, SEZIONI_AI_CHECK) is True
        assert verifica_citazione(sezione, testo, SEZIONI_AI_CHECK) is True

    def test_proprieta_su_varianti_generate(self):
        sezioni = {
            **SEZIONI_AI_CHECK,
            "S3": "Le **PMI** con sede  nel Lazio\npossono presentare **domanda** entro il 30/6.",
            "A1": "Allegato: requisiti   di ammissibilità — art. 3 (vedi «note»)...",
            "D1-p2": "Testo della pagina con l’apostrofo e la ﬁne riga ammis-\nsibile.",
        }
        chiavi = list(sezioni) + ["[S1]", "s1", " [ S2 ] ", "meta", "[D1-p2]", "d1-p2", "S9"]
        casi = 0
        for chiave in chiavi:
            testo = sezioni.get(chiave.strip().lstrip("[").rstrip("]").strip().upper(), "")
            testo = testo or sezioni.get(chiave.strip().lstrip("[").rstrip("]").strip(), "")
            parole = testo.split()
            for i, j in itertools.combinations(range(len(parole) + 1), 2):
                frammento = " ".join(parole[i:j])
                for variante in (frammento, frammento.upper(), frammento.replace(" ", "\n  "),
                                 frammento.replace("**", "")):
                    if _vecchia(chiave, variante, sezioni):
                        casi += 1
                        assert verifica_citazione(chiave, variante, sezioni), (chiave, variante)
        assert casi > 500
