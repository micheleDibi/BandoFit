"""Vocabolario controllato del modulo partenariati, versionato nel codice.

Unica fonte per: enum dello schema di estrazione (WP3), validazione del profilo
partner (WP4), posizioni delle call (WP5), matching (WP6) e checklist
documentale del consorzio (WP8). Contenuto v1: appendice A di
docs/partenariati.md (proposta da approvare). Esposto al frontend da
`GET /partenariati/vocabolario` tramite `vocabolario_out()`.

I codici coincidono, nello stesso ordine, con i `Literal` di
`app/schemas/partenariato_vocabolario.py` (test di uguaglianza). Un codice
pubblicato non si rinomina né si toglie: si aggiunge e si alza la versione.
"""

from dataclasses import dataclass

from app.schemas.partenariato_vocabolario import (
    CompetenzaOut,
    DocumentoOut,
    FormaOut,
    RuoloOut,
    TipoSoggettoOut,
    VocabolarioOut,
)

VOCABOLARIO_VERSIONE = 1


@dataclass(frozen=True)
class TipoSoggettoVoce:
    etichetta: str
    # id della tabella `beneficiari` del catalogo (1..31)
    beneficiari: tuple[int, ...] = ()


@dataclass(frozen=True)
class CompetenzaVoce:
    etichetta: str
    area: str  # chiave di AREE_COMPETENZE


@dataclass(frozen=True)
class FormaVoce:
    etichetta: str
    responsabilita: str | None  # chiave di RESPONSABILITA; None = dipende
    costituzione: str
    documenti: tuple[str, ...]  # SOLO gli specifici; i base sono DOCUMENTI_BASE
    nota: str | None = None


# --- Tipi di soggetto (→ id beneficiari del catalogo) ---------------------------

TIPI_SOGGETTO: dict[str, TipoSoggettoVoce] = {
    "impresa": TipoSoggettoVoce("Impresa", (16,)),
    "micro_impresa": TipoSoggettoVoce("Micro impresa", (22,)),
    "piccola_impresa": TipoSoggettoVoce("Piccola impresa", (26,)),
    "media_impresa": TipoSoggettoVoce("Media impresa"),
    "pmi": TipoSoggettoVoce("PMI", (27,)),
    "grande_impresa": TipoSoggettoVoce("Grande impresa", (15,)),
    "startup_innovativa": TipoSoggettoVoce("Startup innovativa", (30,)),
    "pmi_innovativa": TipoSoggettoVoce("PMI innovativa"),
    "impresa_artigiana": TipoSoggettoVoce("Impresa artigiana"),
    "cooperativa": TipoSoggettoVoce("Società cooperativa", (28,)),
    "impresa_sociale": TipoSoggettoVoce(
        "Impresa sociale, società benefit o cooperativa sociale", (17, 8)
    ),
    "libero_professionista": TipoSoggettoVoce("Libero professionista", (21,)),
    "organismo_ricerca": TipoSoggettoVoce("Organismo di ricerca", (31,)),
    "universita": TipoSoggettoVoce("Università", (31,)),
    "ente_pubblico": TipoSoggettoVoce("Ente pubblico", (12,)),
    "ente_locale": TipoSoggettoVoce("Ente locale o territoriale", (13,)),
    "ente_terzo_settore": TipoSoggettoVoce("Ente del Terzo settore", (11,)),
    "associazione_categoria": TipoSoggettoVoce(
        "Associazione di categoria o parte sociale", (2, 24)
    ),
    "organismo_formazione": TipoSoggettoVoce("Organismo di formazione", (23,)),
    "istituto_scolastico": TipoSoggettoVoce("Istituto scolastico", (20,)),
    "istituto_cultura": TipoSoggettoVoce("Istituto o luogo della cultura", (19,)),
    "fondazione": TipoSoggettoVoce("Fondazione"),
    "consorzio_rete_imprese": TipoSoggettoVoce("Consorzio o rete di imprese"),
    "intermediario_finanziario": TipoSoggettoVoce(
        "Banca, confidi o intermediario finanziario", (4, 6, 18)
    ),
    "ente_sportivo": TipoSoggettoVoce("Associazione, società o ente sportivo", (3, 9, 14, 29)),
    "persona_fisica": TipoSoggettoVoce("Persona fisica o gruppo informale", (25,)),
    "altro": TipoSoggettoVoce("Altro", (1, 5, 7, 10)),
}


# --- Competenze (42, per area) ---------------------------------------------------

AREE_COMPETENZE: dict[str, str] = {
    "ricerca_innovazione": "Ricerca e innovazione",
    "digitale": "Digitale",
    "energia_ambiente": "Energia e ambiente",
    "produzione": "Produzione",
    "servizi": "Servizi",
    "infrastrutture": "Infrastrutture",
    "altro": "Altro",
}

COMPETENZE: dict[str, CompetenzaVoce] = {
    # Ricerca e innovazione
    "ricerca_industriale": CompetenzaVoce("Ricerca industriale", "ricerca_innovazione"),
    "sviluppo_sperimentale": CompetenzaVoce("Sviluppo sperimentale", "ricerca_innovazione"),
    "prototipazione_testing": CompetenzaVoce("Prototipazione e test", "ricerca_innovazione"),
    "trasferimento_tecnologico": CompetenzaVoce(
        "Trasferimento tecnologico", "ricerca_innovazione"
    ),
    "proprieta_intellettuale": CompetenzaVoce(
        "Proprietà intellettuale e brevetti", "ricerca_innovazione"
    ),
    # Digitale
    "sviluppo_software": CompetenzaVoce("Sviluppo software", "digitale"),
    "intelligenza_artificiale_dati": CompetenzaVoce("Intelligenza artificiale e dati", "digitale"),
    "cybersecurity": CompetenzaVoce("Cybersicurezza", "digitale"),
    "iot_elettronica_embedded": CompetenzaVoce("IoT ed elettronica embedded", "digitale"),
    "cloud_infrastrutture_it": CompetenzaVoce("Cloud e infrastrutture IT", "digitale"),
    "automazione_industria40": CompetenzaVoce("Automazione e Industria 4.0", "digitale"),
    "marketing_digitale_ecommerce": CompetenzaVoce(
        "Marketing digitale ed e-commerce", "digitale"
    ),
    # Energia e ambiente
    "efficienza_energetica": CompetenzaVoce("Efficienza energetica", "energia_ambiente"),
    "energie_rinnovabili": CompetenzaVoce("Energie rinnovabili", "energia_ambiente"),
    "economia_circolare": CompetenzaVoce("Economia circolare", "energia_ambiente"),
    "rifiuti_bonifiche": CompetenzaVoce("Rifiuti e bonifiche", "energia_ambiente"),
    "mobilita_sostenibile": CompetenzaVoce("Mobilità sostenibile", "energia_ambiente"),
    "edilizia_sostenibile": CompetenzaVoce("Edilizia sostenibile", "energia_ambiente"),
    "risorse_idriche": CompetenzaVoce("Risorse idriche", "energia_ambiente"),
    # Produzione
    "meccanica_meccatronica": CompetenzaVoce("Meccanica e meccatronica", "produzione"),
    "materiali_chimica": CompetenzaVoce("Materiali e chimica", "produzione"),
    "agroalimentare": CompetenzaVoce("Agroalimentare", "produzione"),
    "tessile_moda_design": CompetenzaVoce("Tessile, moda e design", "produzione"),
    "biotech_farmaceutica": CompetenzaVoce("Biotecnologie e farmaceutica", "produzione"),
    "dispositivi_medici_salute": CompetenzaVoce("Dispositivi medici e salute", "produzione"),
    "aerospazio": CompetenzaVoce("Aerospazio", "produzione"),
    "nautica_trasporti": CompetenzaVoce("Nautica e trasporti", "produzione"),
    "costruzioni_impianti": CompetenzaVoce("Costruzioni e impianti", "produzione"),
    "logistica_supply_chain": CompetenzaVoce("Logistica e catena di fornitura", "produzione"),
    # Servizi
    "formazione_competenze": CompetenzaVoce("Formazione e sviluppo delle competenze", "servizi"),
    "consulenza_organizzativa": CompetenzaVoce("Consulenza organizzativa", "servizi"),
    "progettazione_rendicontazione_fondi": CompetenzaVoce(
        "Progettazione e rendicontazione di fondi", "servizi"
    ),
    "comunicazione_disseminazione": CompetenzaVoce("Comunicazione e disseminazione", "servizi"),
    "internazionalizzazione_export": CompetenzaVoce("Internazionalizzazione ed export", "servizi"),
    "servizi_sociali_welfare": CompetenzaVoce("Servizi sociali e welfare", "servizi"),
    "turismo_cultura_creativita": CompetenzaVoce("Turismo, cultura e creatività", "servizi"),
    "servizi_finanziari": CompetenzaVoce("Servizi finanziari", "servizi"),
    # Infrastrutture
    "laboratorio_prove_accreditato": CompetenzaVoce(
        "Laboratorio di prova accreditato", "infrastrutture"
    ),
    "impianto_pilota": CompetenzaVoce("Impianto pilota", "infrastrutture"),
    "living_lab_sperimentazione": CompetenzaVoce(
        "Living lab e sperimentazione sul campo", "infrastrutture"
    ),
    "capacita_produttiva_scala": CompetenzaVoce(
        "Capacità produttiva su larga scala", "infrastrutture"
    ),
    # Residuale
    "altro": CompetenzaVoce("Altro", "altro"),
}


# --- Forme di aggregazione e checklist documentale -----------------------------

RESPONSABILITA: dict[str, str] = {
    "pro_quota": "Ciascun partner risponde per la propria parte",
    "solidale": "I partner rispondono in solido verso l'ente",
    "singoli_partecipanti": "Le obbligazioni restano in capo ai singoli partecipanti",
    "consortile": "Risponde il soggetto comune con il proprio fondo",
}

DOCUMENTI: dict[str, str] = {
    "nda": "Accordo di riservatezza (NDA)",
    "lettera_intenti": "Lettera d'intenti",
    "term_sheet_mou": "Term sheet o protocollo d'intesa (MoU)",
    "dichiarazione_sostitutiva": "Dichiarazioni sostitutive dei partner",
    "mandato_collettivo": "Mandato collettivo con rappresentanza al capofila",
    "atto_costitutivo": "Atto costitutivo",
    "impegno_costituire": "Impegno a costituire il raggruppamento",
    "contratto_rete": "Contratto di rete",
    "programma_rete": "Programma di rete",
    "fondo_patrimoniale": "Fondo patrimoniale comune",
    "organo_comune": "Nomina dell'organo comune",
    "iscrizione_registro_imprese": "Iscrizione al Registro delle imprese",
    "statuto": "Statuto",
    "accordo_partenariato": "Accordo di partenariato",
    "consortium_agreement": "Consortium Agreement (modello DESCA)",
    "dichiarazioni_affiliated_entities": "Dichiarazioni delle affiliated entities",
    "lettere_associated_partners": "Lettere degli associated partners",
}

# Valgono per ogni forma (appendice A: «base per tutte»).
DOCUMENTI_BASE: tuple[str, ...] = (
    "nda",
    "lettera_intenti",
    "term_sheet_mou",
    "dichiarazione_sostitutiva",
)

FORME: dict[str, FormaVoce] = {
    "ats": FormaVoce(
        etichetta="Associazione temporanea di scopo (ATS)",
        responsabilita="pro_quota",
        costituzione="Da costituire alla domanda, costituita prima della concessione",
        documenti=("mandato_collettivo", "atto_costitutivo"),
    ),
    "ati_rti": FormaVoce(
        etichetta="Associazione o raggruppamento temporaneo di imprese (ATI/RTI)",
        responsabilita="solidale",
        costituzione=(
            "Impegno a costituire alla domanda, poi mandato con scrittura privata autenticata"
        ),
        documenti=("impegno_costituire", "mandato_collettivo"),
        nota="Chi partecipa a più raggruppamenti per lo stesso bando viene escluso",
    ),
    "rete_contratto": FormaVoce(
        etichetta="Rete-contratto (contratto di rete senza soggettività giuridica)",
        responsabilita="singoli_partecipanti",
        costituzione="Contratto di rete sottoscritto e iscritto al Registro delle imprese",
        documenti=("contratto_rete", "programma_rete"),
    ),
    "rete_soggetto": FormaVoce(
        etichetta="Rete-soggetto (contratto di rete con soggettività giuridica)",
        responsabilita="consortile",
        costituzione=(
            "Contratto di rete con fondo patrimoniale e organo comune, iscritto al "
            "Registro delle imprese"
        ),
        documenti=(
            "contratto_rete",
            "programma_rete",
            "fondo_patrimoniale",
            "organo_comune",
            "iscrizione_registro_imprese",
        ),
    ),
    "consorzio": FormaVoce(
        etichetta="Consorzio",
        responsabilita="consortile",
        costituzione="Già costituito: partecipa il consorzio stesso",
        documenti=("atto_costitutivo", "statuto"),
    ),
    "accordo_partenariato": FormaVoce(
        etichetta="Accordo di partenariato o di collaborazione",
        responsabilita="pro_quota",
        costituzione=(
            "Accordo tra i partner, con mandato al capofila per atto pubblico o scrittura "
            "privata autenticata"
        ),
        documenti=("accordo_partenariato", "mandato_collettivo"),
        nota="Nei bandi MIMIT ATS e RTI non sono beneficiari diretti (FAQ MIMIT)",
    ),
    "consorzio_ue": FormaVoce(
        etichetta="Consorzio di progetto europeo",
        responsabilita="pro_quota",
        costituzione="Consortium Agreement firmato di norma prima del Grant Agreement",
        documenti=(
            "consortium_agreement",
            "dichiarazioni_affiliated_entities",
            "lettere_associated_partners",
        ),
        nota="Affiliated entities e associated partners non contano per il numero minimo",
    ),
    "altra": FormaVoce(
        etichetta="Altra forma di aggregazione",
        responsabilita=None,
        costituzione="Dipende dalla forma indicata dal bando",
        documenti=(),
    ),
}


# --- Ruoli -----------------------------------------------------------------------

RUOLI: dict[str, str] = {
    "capofila": "Capofila",
    "partner": "Partner",
}


# --- Funzioni --------------------------------------------------------------------


def beneficiari_per_tipo(codice: str) -> list[int]:
    """Id `beneficiari` del catalogo per un tipo di soggetto; vuota se il tipo
    non ha corrispondenze o non esiste."""
    voce = TIPI_SOGGETTO.get(codice)
    return list(voce.beneficiari) if voce else []


def documenti_forma(codice: str) -> list[str]:
    """Checklist completa di una forma: i documenti di base, poi gli specifici
    (senza doppioni). Forma sconosciuta → solo i documenti di base."""
    voce = FORME.get(codice)
    specifici = voce.documenti if voce else ()
    return list(dict.fromkeys((*DOCUMENTI_BASE, *specifici)))


def vocabolario_out() -> VocabolarioOut:
    """Il vocabolario nella forma di `GET /partenariati/vocabolario`."""
    return VocabolarioOut(
        versione=VOCABOLARIO_VERSIONE,
        tipi_soggetto=[
            TipoSoggettoOut(
                codice=codice, etichetta=voce.etichetta, beneficiari=list(voce.beneficiari)
            )
            for codice, voce in TIPI_SOGGETTO.items()
        ],
        competenze=[
            CompetenzaOut(
                codice=codice, etichetta=voce.etichetta, area=AREE_COMPETENZE[voce.area]
            )
            for codice, voce in COMPETENZE.items()
        ],
        forme=[
            FormaOut(
                codice=codice,
                etichetta=voce.etichetta,
                responsabilita=voce.responsabilita,
                costituzione=voce.costituzione,
                documenti=[
                    DocumentoOut(codice=doc, etichetta=DOCUMENTI[doc])
                    for doc in documenti_forma(codice)
                ],
            )
            for codice, voce in FORME.items()
        ],
        ruoli=[RuoloOut(codice=codice, etichetta=etichetta) for codice, etichetta in RUOLI.items()],
    )
