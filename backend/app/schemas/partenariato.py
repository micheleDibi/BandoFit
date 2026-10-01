"""Contratti delle regole di partenariato per bando (WP3, docs/partenariati.md §2.3).

Due famiglie:
  * `PartenariatoEstrazione`: schema di OUTPUT del modello, in forma
    COMPATTA. In output strutturato strict l'API compila lo schema in una
    grammatica e rifiuta quelle troppo grandi (HTTP 400 «The compiled grammar
    is too large»): così è fallita la prima valutazione reale con la v1, e la
    sonda del 2026-09-29 ha rifiutato anche questa v2. Per questo l'estrazione
    lo invia come schema di uno strumento forzato NON strict (nessuna
    grammatica: `AiCheckClient.estrai_con_strumento`) e ne convalida l'input
    con `convalida_tollerante`, che non cambia lo schema inviato. La forma
    compatta resta:
      - tutti i campi sono obbligatori e NESSUNO è nullable (niente `anyOf`):
        il valore assente è convenzionale, "" per testi, codici e numeri,
        lista vuota per gli elenchi, `{"sezione": "", "testo": ""}` per la
        citazione; i numeri sono stringhe di cifre ("3", "12.5");
      - enum solo piccoli (modalità, ruolo, ambito, operatore): gli altri
        codici (tipi di soggetto, forme, documenti, variabili finanziarie,
        momenti, …) sono stringhe libere, elencate nel prompt e mappate sul
        vocabolario dalla post-elaborazione (ignoto → voce `da_verificare`
        o codice residuale, mai un'eccezione);
      - una sola forma di citazione (`Citazione`), nessun vincolo numerico.
    Il tetto di dimensione dello strumento è in tests/test_schemi_ai_dimensione.py.
    La post-elaborazione (`services/partenariato_regole.py`) controlla codici,
    numeri e range e declassa la voce a `da_verificare` invece di buttare una
    risposta già pagata.
  * DTO API: `PartenariatoBandoOut` (stato dell'estrazione per bando) con le
    regole post-elaborate `RegolePartenariatoOut`, dove ogni voce porta lo
    `stato` della sua citazione (`verificata` | `da_verificare`); poi i DTO
    dell'area admin. I DTO restano tipizzati sul vocabolario controllato
    (`app/schemas/partenariato_vocabolario.py`) e sul contratto unico delle
    regole finanziarie `app/schemas/regole_finanziarie.py` (WP1).
"""

import json
import math
import re
import unicodedata
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, PrivateAttr, field_validator

from app.schemas.partenariato_vocabolario import (
    DocumentoPartenariato,
    FormaAggregazione,
    TipoSoggetto,
)
from app.schemas.regole_finanziarie import AmbitoRegola, Operatore, RegolaFinanziaria
from app.services.link_policy import url_documento_pubblicabile

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

# Nei modelli qui sotto (output del modello) i campi `str` che portano un
# codice accettano il codice del vocabolario scritto nel prompt; "" = assente.
# I numeri sono stringhe di cifre ("3", "30", "12.5"); "" = non indicato.


class Citazione(BaseModel):
    """`sezione`: identificatore del blocco senza parentesi ("META", "S2",
    "D1-p3"); `testo`: passaggio copiato alla lettera. Assente = entrambi ""."""

    sezione: str
    testo: str


class FormaAmmessa(BaseModel):
    forma: str  # codice di FormaAggregazione
    note: str
    citazione: Citazione


class ComposizioneVoce(BaseModel):
    id: str
    tipo_soggetto: str  # codice di TipoSoggetto
    # Descrizione testuale quando il tipo è «altro» (o per precisarlo).
    tipo_soggetto_testo: str
    minimo: str  # intero
    massimo: str  # intero
    ruolo: RuoloComposizione
    # Nomi delle regioni italiane (mappati sugli id del catalogo nel codice).
    regioni: list[str]
    paesi: list[str]
    vincolo_territoriale: str
    citazione: Citazione


class QuotaVoce(BaseModel):
    id: str
    ambito: AmbitoQuota
    categoria: str  # codice di TipoSoggetto
    # Percentuali 0–100 (30 = 30 %): il range lo controlla il codice.
    min_percentuale: str
    max_percentuale: str
    base_calcolo: str  # codice di BaseCalcolo
    effetto_violazione: str  # codice di EffettoViolazione
    citazione: Citazione


class VincoloVoce(BaseModel):
    id: str
    tipo: str  # codice di TipoVincolo
    descrizione: str
    # Parametro numerico del vincolo (paesi distinti: quanti; costituzione
    # entro: giorni).
    parametro: str
    momento: str  # codice di Momento
    citazione: Citazione


class RegolaFinanziariaEstratta(BaseModel):
    """Stessi campi del contratto unico WP1 (`RegolaFinanziaria`) più la
    citazione, in forma compatta: variabili e unità come stringhe (codici),
    "" al posto di null. La post-elaborazione la riporta al contratto."""

    id: str
    descrizione: str
    ambito: AmbitoRegola
    numeratore: str  # codice di VariabileFinanziaria
    denominatore: str
    operatore: Operatore
    soglia: str
    soglia_variabile: str
    soglia_coefficiente: str
    unita: str  # codice di UnitaRegola
    citazione: Citazione


class DocumentoRichiesto(BaseModel):
    id: str
    tipo: str  # codice di TipoDocumentoRichiesto
    descrizione: str
    momento: str  # codice di Momento
    citazione: Citazione


class PartenariatoEstrazione(BaseModel):
    """Regole di partenariato estratte dal testo del bando (output strict,
    forma compatta: vedi la docstring del modulo)."""

    # La docstring qui sopra finisce nello schema inviato («description»):
    # resta quella della v2 misurata dalla sonda anche se l'estrazione ora usa
    # uno strumento non strict.

    modalita: Modalita
    modalita_citazione: Citazione
    forme_ammesse: list[FormaAmmessa]
    costituzione: str  # codice di Costituzione
    costituzione_citazione: Citazione
    partner_min: str  # intero
    partner_min_citazione: Citazione
    partner_max: str  # intero
    partner_max_citazione: Citazione
    conteggio_note: str
    composizione: list[ComposizioneVoce]
    quote: list[QuotaVoce]
    vincoli: list[VincoloVoce]
    regole_finanziarie: list[RegolaFinanziariaEstratta]
    documenti_richiesti: list[DocumentoRichiesto]
    fonti_insufficienti: bool
    note: str

    # Fuori dallo schema inviato e dal dump: le voci che `convalida_tollerante`
    # ha scartato o declassato. La post-elaborazione le aggiunge agli avvisi
    # globali (`avvisi_convalida`).
    _avvisi_convalida: list[str] = PrivateAttr(default_factory=list)


def avvisi_convalida(estrazione: PartenariatoEstrazione) -> list[str]:
    """Gli avvisi della convalida tollerante ([] per un'estrazione convalidata
    in modo stretto o riletta dal DB)."""
    return list(estrazione._avvisi_convalida)


# ------------------------------------------- convalida tollerante dell'output
# Lo strumento NON è strict (nessuna grammatica): il modello riceve lo schema
# come guida ma può discostarsene. `convalida_tollerante` riporta l'input
# alla forma di `PartenariatoEstrazione` senza cambiare lo schema inviato.
# Regola generale: solo un valore ASSENTE (campo mancante o null) diventa il
# valore assente convenzionale; un valore presente che non si riesce a leggere
# resta visibile (testo non vuoto, avviso, voce da verificare) e non diventa
# mai «non indicato».

# Valore «assente» dei campi a codici chiusi (Literal) che ne hanno uno. Un
# codice mancante diventa questo; uno scritto ma non riconosciuto anche, con
# un avviso, e nelle voci la citazione si svuota (la voce è da verificare).
_ASSENTE_LITERAL: dict[tuple[type[BaseModel], str], str] = {
    (PartenariatoEstrazione, "modalita"): "non_determinabile",
    # Nessun ruolo indicato = nessun vincolo di ruolo.
    (ComposizioneVoce, "ruolo"): "qualsiasi",
}
# Sinonimi certi dei codici chiusi, validi solo se il codice è ammesso dal
# campo: gli operatori scritti come simboli (codici `Operatore` del contratto
# WP1) e i ruoli delle ATS (la mandataria è il capofila, le mandanti sono i
# partner). Chiavi: il testo com'è o la sua `_chiave`.
_SINONIMI_LITERAL = {
    "<": "lt", "<=": "le", "≤": "le", ">": "gt", ">=": "ge", "≥": "ge",
    "capo_fila": "capofila", "capogruppo": "capofila", "capo_gruppo": "capofila",
    "mandataria": "capofila", "mandatario": "capofila",
    "mandante": "partner", "mandanti": "partner",
}
_MAX_CHIAVE = 120
# Un valore presente ma non convertibile resta come il suo JSON, tagliato.
_MAX_GREZZO = 200
_MAX_MOSTRATO = 60
_NESSUNA_CITAZIONE = {"sezione": "", "testo": ""}

# Per gli avvisi: la sezione di ogni tipo di voce e i campi a codice chiuso.
_SEZIONI_VOCE: dict[type[BaseModel], str] = {
    FormaAmmessa: "Forme di aggregazione",
    ComposizioneVoce: "Composizione",
    QuotaVoce: "Quote",
    VincoloVoce: "Vincoli",
    RegolaFinanziariaEstratta: "Regole finanziarie",
    DocumentoRichiesto: "Documenti richiesti",
}
_CAMPI_CODICE = {"ambito": "l'ambito", "operatore": "l'operatore", "ruolo": "il ruolo"}


class _VoceNonValida(ValueError):
    """Una voce di un elenco con un codice chiuso mancante o ignoto e senza
    valore assente (es. l'operatore di una regola): la voce si scarta, con un
    avviso. `ValueError`: se mai sfuggisse, il client la tratta come input
    respinto (errore con l'usage), non come un crash."""

    def __init__(self, campo: str, valore: Any):
        super().__init__(campo)
        self.campo, self.valore = campo, valore


def _chiave(valore: str) -> str:
    """«Non ammesso», «NON-AMMESSO» → «non_ammesso» (come i codici)."""
    ascii_ = unicodedata.normalize("NFKD", valore[:_MAX_CHIAVE]).encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", "_", ascii_.decode().casefold()).strip("_")


def _da_json(valore: Any) -> Any:
    """Un oggetto o un elenco serializzato in una stringa JSON (il modello a
    volte lo fa con gli strumenti) torna oggetto o elenco."""
    if isinstance(valore, str) and valore.strip()[:1] in ("{", "["):
        try:
            return json.loads(valore)
        except ValueError:
            return valore
    return valore


# Il prompt chiede «""» per un valore assente e il modello a volte scrive
# proprio le virgolette (la stringa di due caratteri `""`): un testo fatto
# solo di virgolette o apici (anche tipografici) vale vuoto.
_SOLO_VIRGOLETTE = re.compile(r"[\s\"'\u201c\u201d\u201e\u00ab\u00bb\u2018\u2019`]*")


def _solo_virgolette(testo: str) -> bool:
    return _SOLO_VIRGOLETTE.fullmatch(testo) is not None


def _assente(valore: Any) -> bool:
    """Il valore assente del modello: campo mancante, null, testo vuoto o fatto
    di sole virgolette («""»)."""
    return valore is None or (isinstance(valore, str) and _solo_virgolette(valore))


def _grezzo(valore: Any, limite: int = _MAX_GREZZO) -> str:
    try:
        testo = json.dumps(valore, ensure_ascii=False)
    except (TypeError, ValueError):
        testo = repr(valore)
    return testo[:limite] or "?"


def _semplice(valore: Any) -> bool:
    return isinstance(valore, str | int | float) and not isinstance(valore, bool)


def _testo_tollerante(valore: Any) -> str:
    """Testo di un campo `str`. null (o campo mancante) → "" (assente); numero
    → la sua stringa di cifre (3 → "3", 12.5 → "12.5", 3.0 → "3"); elenco di
    un solo valore semplice → quel valore ([1] → "1"). Ogni altro valore
    (booleano, oggetto, elenco, numero non finito) è PRESENTE ma non
    convertibile: resta il suo JSON, un testo non vuoto che la
    post-elaborazione segnala come non leggibile o non riconosciuto."""
    if valore is None:
        return ""
    if isinstance(valore, str):
        return "" if _solo_virgolette(valore) else valore
    if isinstance(valore, list) and len(valore) == 1 and _semplice(valore[0]):
        return _testo_tollerante(valore[0])
    if isinstance(valore, int) and not isinstance(valore, bool):
        return str(valore)
    if isinstance(valore, float) and math.isfinite(valore):
        return str(int(valore)) if valore.is_integer() else repr(valore)
    return _grezzo(valore)


def _mostra(valore: Any) -> str:
    """Il valore del modello dentro un avviso, tagliato."""
    testo = valore.strip() if isinstance(valore, str) else _grezzo(valore)
    return testo[:_MAX_MOSTRATO]


def _codice_chiuso(valore: Any, ammessi: tuple) -> str | None:
    if not isinstance(valore, str):
        return None
    grezzo = valore.strip()
    chiave = _chiave(grezzo)
    for candidato in (
        grezzo, _SINONIMI_LITERAL.get(grezzo), chiave, _SINONIMI_LITERAL.get(chiave)
    ):
        if candidato in ammessi:
            return candidato
    return None


def _chi(modello: type[BaseModel], dati: Any) -> str:
    """«Quote: voce Q2» (o «Quote: una voce» senza id), per gli avvisi."""
    sezione = _SEZIONI_VOCE.get(modello, modello.__name__)
    id_ = _testo_tollerante(dati.get("id")).strip()[:20] if isinstance(dati, dict) else ""
    return f"{sezione}: voce {id_}" if id_ else f"{sezione}: una voce"


def _avviso_scartata(modello: type[BaseModel], dati: Any, motivo: str) -> str:
    return f"{_chi(modello, dati)} non considerata perché {motivo}"


def _motivo_codice(campo: str, valore: Any) -> str:
    nome = _CAMPI_CODICE.get(campo, f"il campo {campo}")
    if _assente(valore):
        return f"manca {nome}"
    return f"{nome} «{_mostra(valore)}» non è riconosciuto"


def _campo_tollerante(
    modello: type[BaseModel], nome: str, tipo: Any, valore: Any, avvisi: list[str]
) -> Any:
    if tipo is str:
        if modello is Citazione:
            # Solo un testo è una citazione: un numero («2») si ritroverebbe
            # quasi in ogni sezione. Altro → assente (la voce è da verificare).
            return valore if isinstance(valore, str) else ""
        return _testo_tollerante(valore)
    if tipo is bool:
        if isinstance(valore, str):
            return valore.strip().casefold() in ("true", "vero", "si", "sì")
        return valore is True
    if isinstance(tipo, type) and issubclass(tipo, BaseModel):
        valore = _da_json(valore)
        return _oggetto_tollerante(tipo, valore if isinstance(valore, dict) else {}, avvisi)
    if get_origin(tipo) is list:
        (elemento,) = get_args(tipo)
        valore = _da_json(valore)
        if _assente(valore):
            return []
        if not isinstance(valore, list):
            valore = [valore]  # un valore singolo al posto dell'elenco
        if elemento is str:
            # null = nessun elemento; ogni altro elemento resta, anche se non
            # convertibile (la post-elaborazione lo segnala come ignoto).
            return [_testo_tollerante(v) for v in valore if v is not None]
        voci = []
        for voce in map(_da_json, valore):
            if voce is None:
                continue
            if not isinstance(voce, dict):
                avvisi.append(_avviso_scartata(elemento, None, "non è leggibile"))
                continue
            try:
                voci.append(_oggetto_tollerante(elemento, voce, avvisi))
            except _VoceNonValida as exc:
                avvisi.append(
                    _avviso_scartata(elemento, voce, _motivo_codice(exc.campo, exc.valore))
                )
        return voci
    raise TypeError(f"campo {modello.__name__}.{nome}: tipo non gestito {tipo!r}")


def _oggetto_tollerante(modello: type[BaseModel], dati: dict, avvisi: list[str]) -> dict:
    # Solo i campi dello schema: quelli ignoti si ignorano. Gli avvisi di una
    # voce scartata non restano: al suo posto c'è quello dello scarto.
    campi: dict[str, Any] = {}
    propri: list[str] = []
    ignoti: list[tuple[str, Any]] = []
    for nome, info in modello.model_fields.items():
        tipo, valore = info.annotation, dati.get(nome)
        if get_origin(tipo) is not Literal:
            campi[nome] = _campo_tollerante(modello, nome, tipo, valore, propri)
            continue
        codice = _codice_chiuso(valore, get_args(tipo))
        if codice is None:
            codice = _ASSENTE_LITERAL.get((modello, nome))
            if codice is None:
                raise _VoceNonValida(nome, valore)
            if not _assente(valore):
                ignoti.append((nome, valore))
        campi[nome] = codice
    for nome, valore in ignoti:
        # Codice scritto ma fuori vocabolario: vale il valore assente, che non
        # è ciò che dice il bando. La modalità «non_determinabile» non vale
        # mai; una voce resta visibile ma, senza citazione, da verificare
        # (esclusa dagli usi deterministici e dalla conferma).
        if modello is PartenariatoEstrazione:
            propri.append(f"Modalità «{_mostra(valore)}» non riconosciuta")
            continue
        campi["citazione"] = dict(_NESSUNA_CITAZIONE)
        propri.append(
            f"{_chi(modello, dati)} da verificare perché "
            f"{_CAMPI_CODICE.get(nome, f'il campo {nome}')} «{_mostra(valore)}» "
            "non è riconosciuto"
        )
    avvisi.extend(propri)
    return campi


def _senza_involucro(dati: dict) -> dict:
    """{"regole": {...}} o {"input": "{...}"}: un involucro di UNA chiave
    ignota attorno all'estrazione (capita con gli strumenti non strict) si
    scioglie, se dentro c'è un oggetto con almeno un campo dell'estrazione."""
    campi = PartenariatoEstrazione.model_fields.keys()
    if len(dati) != 1 or dati.keys() & campi:
        return dati
    interno = _da_json(next(iter(dati.values())))
    return interno if isinstance(interno, dict) and interno.keys() & campi else dati


def convalida_tollerante(dati: Any) -> PartenariatoEstrazione:
    """`PartenariatoEstrazione` dall'input dello strumento, in modo TOLLERANTE:
    - campo mancante o null → valore assente convenzionale ("", lista vuota,
      citazione vuota, false; la modalità «non_determinabile», il ruolo
      «qualsiasi»);
    - numero al posto della stringa di cifre → stringa; elenco di un solo
      valore semplice → quel valore; valore singolo al posto dell'elenco →
      elenco di un elemento; oggetto o elenco serializzato in una stringa JSON
      → letto; involucro di una sola chiave ignota → sciolto;
    - valore presente ma non convertibile (booleano, oggetto, elenco in un
      campo di testo) → il suo JSON: la post-elaborazione lo segnala, mai
      «assente»; nelle citazioni vale solo un testo;
    - codici chiusi confrontati senza maiuscole, accenti e punteggiatura, con
      pochi sinonimi certi (simboli degli operatori, mandataria/mandante);
      un codice scritto ma ignoto con un valore assente vale quello, con un
      avviso (voce senza citazione, da verificare);
    - una voce di un elenco che non è un oggetto, o con un codice chiuso
      mancante o ignoto senza valore assente (ambito della quota, ambito e
      operatore della regola finanziaria), si scarta con un avviso;
    - campi ignoti ignorati.
    Gli avvisi restano sull'estrazione (`avvisi_convalida`), fuori dal dump.
    Errore (`ValueError`): un input che non è un oggetto JSON, un oggetto
    vuoto (`{}`: il modello non ha estratto nulla, non si salva come
    «estratta» da riusare) o un oggetto senza nessun campo dell'estrazione."""
    dati = _da_json(dati)
    if not isinstance(dati, dict):
        raise ValueError("l'input dello strumento non è un oggetto JSON")
    dati = _senza_involucro(dati)
    if not dati:
        # Risposta pagata ma vuota: errore con l'usage, come un input
        # illeggibile (niente estrazione «non determinabile» da riusare).
        raise ValueError("l'input dello strumento è vuoto")
    if not dati.keys() & PartenariatoEstrazione.model_fields.keys():
        # Non un'estrazione vuota: una risposta illeggibile (pagata, finisce
        # in errore con l'usage, e non si riusa).
        raise ValueError("l'input dello strumento non ha nessun campo dell'estrazione")
    avvisi: list[str] = []
    estrazione = PartenariatoEstrazione.model_validate(
        _oggetto_tollerante(PartenariatoEstrazione, dati, avvisi)
    )
    estrazione._avvisi_convalida = avvisi
    return estrazione


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
    # Solo per le citazioni dei documenti ufficiali: solo https e ammesso
    # dal filtro dei link della scheda. Il validatore vale ovunque si
    # costruisca il modello: all'estrazione (`partenariato_regole`), sulle
    # regole storiche rilette dal DB (`_regole_out`) e sugli snapshot delle
    # partner call (`partner_call_gap._citazione_out`); un URL non ammesso
    # diventa None, mai un errore di validazione (scarterebbe tutte le
    # regole o la citazione).
    url_documento: str | None = None
    pagina: int | None = None

    @field_validator("url_documento", mode="after")
    @classmethod
    def _url_ammesso(cls, valore: str | None) -> str | None:
        return url_documento_pubblicabile(valore)


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
    # Stato effettivo del bando da `bando_pubblico` (contratto DB bandi §4):
    # il frontend lo usa con ripiego su `stato_bando`.
    stato_effettivo: str | None = None


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
