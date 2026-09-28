"""Contratti delle regole di partenariato per bando (WP3, docs/partenariati.md §2.3).

Due famiglie:
  * `PartenariatoEstrazione`: schema di OUTPUT imposto al modello (structured
    output strict). Tutti i campi sono OBBLIGATORI (nullable dove serve) e lo
    schema contiene SOLO tipi ed enum, nessun vincolo numerico (niente
    `ge`/`le`/`min_length`): gli structured output non li supportano, l'SDK li
    toglie e li valida lato client, e un solo valore fuori range farebbe
    fallire una chiamata già pagata. I range si controllano nella
    post-elaborazione (`services/partenariato_regole.py`), che declassa la
    voce a `da_verificare` invece di buttare la risposta.
  * DTO API: `PartenariatoBandoOut` (stato dell'estrazione per bando) con le
    regole post-elaborate `RegolePartenariatoOut`, dove ogni voce porta lo
    `stato` della sua citazione (`verificata` | `da_verificare`); poi i DTO
    dell'area admin.

I tipi di soggetto e le forme vengono dal vocabolario controllato
(`app/schemas/partenariato_vocabolario.py`); le regole finanziarie dal
contratto unico `app/schemas/regole_finanziarie.py` (WP1), senza ridefinirlo.
"""

from typing import Literal

from pydantic import BaseModel

from app.schemas.ai_check import CitazioneBando
from app.schemas.partenariato_vocabolario import (
    DocumentoPartenariato,
    FormaAggregazione,
    TipoSoggetto,
)
from app.schemas.regole_finanziarie import RegolaFinanziaria

# ------------------------------------------------------- output del modello

Modalita = Literal["obbligatorio", "ammesso", "non_ammesso", "non_determinabile"]
Costituzione = Literal["costituenda_ammessa", "costituita_richiesta", "non_indicato"]
RuoloComposizione = Literal["capofila", "partner", "qualsiasi", "affiliato", "partner_associato"]
AmbitoQuota = Literal["per_partner", "per_categoria", "capofila"]
BaseCalcolo = Literal[
    "costo_totale_progetto", "spese_ammissibili", "contributo", "budget_partner", "non_indicata"
]
EffettoViolazione = Literal[
    "inammissibilita_progetto", "esclusione_partner", "perdita_maggiorazione", "non_indicato"
]
TipoVincolo = Literal[
    "indipendenza",
    "esclusivita_partenariato",
    "paesi_distinti",
    "sede_operativa_regione",
    "costituzione_entro",
    "requisito_capofila",
    "altro",
]
Momento = Literal["domanda", "concessione", "prima_erogazione", "non_indicato"]
# I documenti del vocabolario (checklist del consorzio, WP8) più «altro».
TipoDocumentoRichiesto = Literal[DocumentoPartenariato, "altro"]


class FormaAmmessa(BaseModel):
    forma: FormaAggregazione
    note: str | None
    citazione: CitazioneBando


class ComposizioneVoce(BaseModel):
    id: str
    tipo_soggetto: TipoSoggetto
    # Descrizione testuale quando il tipo è «altro» (o per precisarlo).
    tipo_soggetto_testo: str | None
    minimo: int | None
    massimo: int | None
    ruolo: RuoloComposizione
    # Nomi delle regioni italiane (mappati sugli id del catalogo nel codice).
    regioni: list[str]
    paesi: list[str]
    vincolo_territoriale: str | None
    citazione: CitazioneBando


class QuotaVoce(BaseModel):
    id: str
    ambito: AmbitoQuota
    categoria: TipoSoggetto | None
    # Percentuali come numeri 0–100 (30 = 30 %): il range lo controlla il codice.
    min_percentuale: float | None
    max_percentuale: float | None
    base_calcolo: BaseCalcolo
    effetto_violazione: EffettoViolazione
    citazione: CitazioneBando


class VincoloVoce(BaseModel):
    id: str
    tipo: TipoVincolo
    descrizione: str
    # Parametro numerico del vincolo (paesi distinti: quanti; costituzione
    # entro: giorni), altrimenti null.
    parametro: float | None
    momento: Momento
    citazione: CitazioneBando


class RegolaFinanziariaEstratta(RegolaFinanziaria):
    """Il contratto unico WP1 più l'ancoraggio al testo del bando."""

    citazione: CitazioneBando


class DocumentoRichiesto(BaseModel):
    id: str
    tipo: TipoDocumentoRichiesto
    descrizione: str
    momento: Momento
    citazione: CitazioneBando


class PartenariatoEstrazione(BaseModel):
    """Regole di partenariato estratte dal testo del bando (output strict)."""

    modalita: Modalita
    modalita_citazione: CitazioneBando | None
    forme_ammesse: list[FormaAmmessa]
    costituzione: Costituzione
    costituzione_citazione: CitazioneBando | None
    partner_min: int | None
    partner_min_citazione: CitazioneBando | None
    partner_max: int | None
    partner_max_citazione: CitazioneBando | None
    conteggio_note: str | None
    composizione: list[ComposizioneVoce]
    quote: list[QuotaVoce]
    vincoli: list[VincoloVoce]
    regole_finanziarie: list[RegolaFinanziariaEstratta]
    documenti_richiesti: list[DocumentoRichiesto]
    fonti_insufficienti: bool
    note: str | None


# ------------------------------------------------ regole post-elaborate (API)

StatoVoce = Literal["verificata", "da_verificare"]


class CitazioneRegolaOut(BaseModel):
    """Citazione di una voce, pronta per la UI: da dove viene («Avviso
    pubblico — pag. 3» o «Scheda del bando»), il testo citato (testo
    semplice: mai reso come link) e se il codice l'ha ritrovato nel testo."""

    sezione: str
    fonte_etichetta: str
    testo: str
    verificata: bool
    # Solo https; solo per le citazioni dei documenti ufficiali.
    url_documento: str | None = None
    pagina: int | None = None


class _Voce(BaseModel):
    # verificata = citazione ritrovata nel testo E nessun controllo di
    # coerenza o di range fallito; altrimenti da_verificare (esclusa dagli
    # usi deterministici: filtro, motori dei WP successivi).
    stato: StatoVoce
    citazione: CitazioneRegolaOut | None = None
    avvisi: list[str] = []


class ModalitaOut(_Voce):
    # Modalità dichiarata dal modello.
    valore: Modalita
    # = valore solo se la citazione è verificata e coerente, altrimenti
    # non_determinabile (ripetuta in RegolePartenariatoOut.modalita_effettiva).
    effettiva: Modalita


class CostituzioneOut(_Voce):
    valore: Costituzione


class ConteggioOut(_Voce):
    valore: int | None = None


class FormaAmmessaOut(_Voce):
    forma: FormaAggregazione
    etichetta: str
    note: str | None = None


class ComposizioneOut(_Voce):
    id: str
    tipo_soggetto: TipoSoggetto
    tipo_soggetto_etichetta: str
    tipo_soggetto_testo: str | None = None
    # id della tabella `beneficiari` del catalogo corrispondenti al tipo
    beneficiari: list[int] = []
    minimo: int | None = None
    massimo: int | None = None
    ruolo: RuoloComposizione
    # Solo le regioni riconosciute: id del lookup `regioni` del catalogo e i
    # loro nomi; le ignote sono scartate con un avviso.
    regioni: list[int] = []
    regioni_nomi: list[str] = []
    paesi: list[str] = []
    vincolo_territoriale: str | None = None


class QuotaOut(_Voce):
    id: str
    ambito: AmbitoQuota
    categoria: TipoSoggetto | None = None
    min_percentuale: float | None = None
    max_percentuale: float | None = None
    base_calcolo: BaseCalcolo
    effetto_violazione: EffettoViolazione


class VincoloOut(_Voce):
    id: str
    tipo: TipoVincolo
    descrizione: str
    parametro: float | None = None
    momento: Momento


class RegolaFinanziariaOut(RegolaFinanziaria):
    """Regola del contratto WP1 con l'esito della verifica (i controlli di
    `bilanci_indicatori.valida_regola` finiscono negli avvisi)."""

    stato: StatoVoce
    citazione: CitazioneRegolaOut | None = None
    avvisi: list[str] = []


class DocumentoRichiestoOut(_Voce):
    id: str
    tipo: TipoDocumentoRichiesto
    descrizione: str
    momento: Momento


class RegolePartenariatoOut(BaseModel):
    """Ciò che esce dall'API (colonna `bando_partenariato.regole`)."""

    modalita: ModalitaOut
    # Quella da usare (filtro, motori): = modalita.valore solo se verificata e
    # coerente, altrimenti non_determinabile.
    modalita_effettiva: Modalita
    forme_ammesse: list[FormaAmmessaOut] = []
    costituzione: CostituzioneOut
    partner_min: ConteggioOut
    partner_max: ConteggioOut
    conteggio_note: str | None = None
    composizione: list[ComposizioneOut] = []
    quote: list[QuotaOut] = []
    vincoli: list[VincoloOut] = []
    regole_finanziarie: list[RegolaFinanziariaOut] = []
    documenti_richiesti: list[DocumentoRichiestoOut] = []
    fonti_insufficienti: bool = False
    note: str | None = None
    # Incoerenze globali rilevate dal codice (es. «non ammesso» con forme).
    avvisi: list[str] = []


# ------------------------------------------------------------- stato (API)

StatoFonte = Literal[
    "candidato",
    "bloccato_policy",
    "formato_non_supportato",
    "errore_download",
    "troppo_grande",
    "non_pdf",
    "schema_non_https",
    "scaricato",
    "letto",
    "letto_parziale",
    "non_leggibile",
    "protetto",
    "corrotto",
    "timeout",
    "escluso_tetto",
]


class FonteOut(BaseModel):
    """Un documento ufficiale considerato: `n` è il numero del blocco
    `[Dn-pk]` nel testo mandato al modello."""

    n: int
    etichetta: str
    dominio: str | None = None
    url: str | None = None  # solo https
    stato: StatoFonte
    pagine_totali: int = 0
    # Numeri delle pagine entrate nel testo analizzato.
    pagine_incluse: list[int] = []
    # true = non tutte le pagine del documento sono state analizzate (tetti).
    troncato: bool = False


StatoPartenariato = Literal["non_estratta", "in_corso", "pronta", "errore", "nessun_segnale"]
FaseEstrazione = Literal["documenti", "lettura", "analisi"]


class PartenariatoBandoOut(BaseModel):
    bando_id: int
    bando_slug: str
    # in_corso solo alla prima estrazione; durante un aggiornamento lo stato
    # resta quello del risultato servito, con aggiornamento_in_corso=true.
    stato: StatoPartenariato
    fase: FaseEstrazione | None = None
    aggiornamento_in_corso: bool = False
    # Il risultato è vecchio (prompt nuovo, catalogo cambiato, riverifica) e
    # fuori dal cooldown: il frontend può rilanciare l'analisi.
    aggiornabile: bool = False
    regole: RegolePartenariatoOut | None = None
    fonti: list[FonteOut] = []
    estratta_at: str | None = None
    verificata_at: str | None = None
    avviata_at: str | None = None
    errore: str | None = None
    riprova_dopo: str | None = None
    # Una POST (con `forza` per «Analizza comunque» dopo nessun_segnale)
    # partirebbe adesso, salvo limiti giornalieri e budget. Motivo quando no:
    # in_corso | aggiornata | cooldown | ai_non_configurata.
    puo_avviare: bool = False
    motivo_non_avviabile: str | None = None
    calls_aperte: int = 0
    stato_bando: str | None = None


class AvviaAnalisiIn(BaseModel):
    # «Analizza comunque»: ammesso una volta sola dopo un esito nessun_segnale.
    forza: bool = False


# ------------------------------------------------------------------- admin


class ForzaEstrazioneIn(BaseModel):
    ignora_cooldown: bool = False


class EstrazioneAdminOut(BaseModel):
    bando_id: int
    bando_slug: str
    bando_titolo: str
    stato: Literal["in_corso", "pronta", "errore"]
    esito: Literal["estratta", "nessun_segnale"] | None = None
    fase: FaseEstrazione | None = None
    modalita_effettiva: Modalita | None = None
    model: str | None = None
    cost_cents: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estratta_at: str | None = None
    verificata_at: str | None = None
    ultima_esecuzione_at: str | None = None
    errore_codice: str | None = None
    tentativi_falliti: int = 0
    prossimo_tentativo_at: str | None = None
    updated_at: str | None = None


class PartenariatiRunOut(BaseModel):
    giorno: str
    riepilogo: dict
