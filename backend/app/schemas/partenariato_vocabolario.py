"""Vocabolario controllato del modulo partenariati (v1, docs/partenariati.md
appendice A) come tipi Pydantic.

I `Literal` servono agli schemi strict (output del modello in WP3, profilo
partner in WP4, posizioni in WP5, matching in WP6) e DEVONO coincidere, anche
nell'ordine, con le costanti di `app/services/partenariato_vocabolario.py`:
lo verifica tests/test_partenariato_vocabolario.py. Un codice nuovo si aggiunge
in entrambi i posti e fa salire `VOCABOLARIO_VERSIONE`.
"""

from typing import Literal

from pydantic import BaseModel

TipoSoggetto = Literal[
    "impresa",
    "micro_impresa",
    "piccola_impresa",
    "media_impresa",
    "pmi",
    "grande_impresa",
    "startup_innovativa",
    "pmi_innovativa",
    "impresa_artigiana",
    "cooperativa",
    "impresa_sociale",
    "libero_professionista",
    "organismo_ricerca",
    "universita",
    "ente_pubblico",
    "ente_locale",
    "ente_terzo_settore",
    "associazione_categoria",
    "organismo_formazione",
    "istituto_scolastico",
    "istituto_cultura",
    "fondazione",
    "consorzio_rete_imprese",
    "intermediario_finanziario",
    "ente_sportivo",
    "persona_fisica",
    "altro",
]

Competenza = Literal[
    # Ricerca e innovazione
    "ricerca_industriale",
    "sviluppo_sperimentale",
    "prototipazione_testing",
    "trasferimento_tecnologico",
    "proprieta_intellettuale",
    # Digitale
    "sviluppo_software",
    "intelligenza_artificiale_dati",
    "cybersecurity",
    "iot_elettronica_embedded",
    "cloud_infrastrutture_it",
    "automazione_industria40",
    "marketing_digitale_ecommerce",
    # Energia e ambiente
    "efficienza_energetica",
    "energie_rinnovabili",
    "economia_circolare",
    "rifiuti_bonifiche",
    "mobilita_sostenibile",
    "edilizia_sostenibile",
    "risorse_idriche",
    # Produzione
    "meccanica_meccatronica",
    "materiali_chimica",
    "agroalimentare",
    "tessile_moda_design",
    "biotech_farmaceutica",
    "dispositivi_medici_salute",
    "aerospazio",
    "nautica_trasporti",
    "costruzioni_impianti",
    "logistica_supply_chain",
    # Servizi
    "formazione_competenze",
    "consulenza_organizzativa",
    "progettazione_rendicontazione_fondi",
    "comunicazione_disseminazione",
    "internazionalizzazione_export",
    "servizi_sociali_welfare",
    "turismo_cultura_creativita",
    "servizi_finanziari",
    # Infrastrutture
    "laboratorio_prove_accreditato",
    "impianto_pilota",
    "living_lab_sperimentazione",
    "capacita_produttiva_scala",
    # Residuale
    "altro",
]

# Le 7 forme dell'appendice A più «altra» (residuale, per ciò che il bando
# ammette ma il vocabolario non copre).
FormaAggregazione = Literal[
    "ats",
    "ati_rti",
    "rete_contratto",
    "rete_soggetto",
    "consorzio",
    "accordo_partenariato",
    "consorzio_ue",
    "altra",
]

RuoloPartenariato = Literal["capofila", "partner"]

# Chi risponde verso l'ente finanziatore, per forma.
Responsabilita = Literal["pro_quota", "solidale", "singoli_partecipanti", "consortile"]

DocumentoPartenariato = Literal[
    # base, per tutte le forme
    "nda",
    "lettera_intenti",
    "term_sheet_mou",
    "dichiarazione_sostitutiva",
    # specifici
    "mandato_collettivo",
    "atto_costitutivo",
    "impegno_costituire",
    "contratto_rete",
    "programma_rete",
    "fondo_patrimoniale",
    "organo_comune",
    "iscrizione_registro_imprese",
    "statuto",
    "accordo_partenariato",
    "consortium_agreement",
    "dichiarazioni_affiliated_entities",
    "lettere_associated_partners",
]


# --- GET /partenariati/vocabolario --------------------------------------------


class TipoSoggettoOut(BaseModel):
    codice: TipoSoggetto
    etichetta: str
    # id della tabella `beneficiari` del catalogo (1..31); vuota se nessuna
    # voce del catalogo corrisponde.
    beneficiari: list[int]


class CompetenzaOut(BaseModel):
    codice: Competenza
    etichetta: str
    # Etichetta dell'area, per raggruppare le competenze nella UI.
    area: str


class DocumentoOut(BaseModel):
    codice: DocumentoPartenariato
    etichetta: str


class FormaOut(BaseModel):
    codice: FormaAggregazione
    etichetta: str
    # None per «altra»: dipende dalla forma concreta.
    responsabilita: Responsabilita | None
    # Costituzione tipica, in parole.
    costituzione: str
    # Checklist completa: prima i documenti di base, poi gli specifici della forma.
    documenti: list[DocumentoOut]


class RuoloOut(BaseModel):
    codice: RuoloPartenariato
    etichetta: str


class VocabolarioOut(BaseModel):
    versione: int
    tipi_soggetto: list[TipoSoggettoOut]
    competenze: list[CompetenzaOut]
    forme: list[FormaOut]
    ruoli: list[RuoloOut]
