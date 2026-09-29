"""Post-elaborazione deterministica delle regole di partenariato (WP3, PURO).

Il modello estrae, il codice decide che cosa vale. Per ogni voce:
- la citazione si verifica sul testo inviato (`citazioni.verifica_citazione`,
  pagine adiacenti ed ellissi comprese); non ritrovata → `da_verificare`;
- fa fede solo il testo dei documenti ufficiali (pagine `D<n>-p<m>`): una
  citazione della scheda del catalogo (META, S1…) resta visibile, ma la voce
  resta `da_verificare` con un avviso (`AVVISO_SCHEDA`), perché la scheda è
  testo generato o classificato, non estratto dall'atto;
- i controlli di coerenza e di RANGE (partner_min ≤ partner_max, percentuali
  0–100, minimo ≤ massimo, «non ammesso» con forme ammesse, regole
  finanziarie con `bilanci_indicatori.valida_regola`) declassano la voce a
  `da_verificare` con un avviso: MAI un'eccezione, la chiamata è già pagata;
- `modalita_effettiva` = modalità dichiarata SOLO se la sua citazione è
  verificata su un documento ufficiale e coerente, altrimenti
  `non_determinabile` (è ciò che usa il filtro «Ammette partenariato»);
  «non ammesso» vale solo se il passaggio
  citato contiene un'esclusione esplicita della forma associata e la sua
  frase non la ammette (`esclusione_esplicita`);
- una quota la cui frase non nomina il partenariato né chi la sostiene (o
  parla di aiuti senza nominare il partenariato, o di una quota di adesione,
  di un cofinanziamento, di un'intensità di aiuto) resta, da verificare: è
  probabilmente un'intensità di aiuto o un limite di spesa; lo stesso se la
  sua percentuale non compare nella frase citata (le frazioni «due terzi»,
  «2/3» si leggono e si convertono, solo per le quote); la frase si
  ricostruisce con le stesse tolleranze della verifica della citazione, e una
  citazione ritrovata di cui la frase non si ricostruisce (frammenti di
  un'ellissi in frasi diverse) resta da verificare;
- le quote uguali (ambito, categoria, min, max; base ed effetto uguali o non
  indicati), con percentuali leggibili e una categoria nota, si tengono una
  volta sola; una quota che il modello ha scritto come vincolo «altro» nella
  forma «nessun partner sostiene da solo più di X» del costo complessivo del
  progetto, senza eccezioni, si recupera come quota per partner (frase
  ricostruita da un documento ufficiale), SEMPRE da verificare, con un avviso
  globale; il vincolo resta;
- le regioni si mappano sugli id del catalogo (le ignote si scartano con un
  avviso), i tipi di soggetto sugli id `beneficiari`;
- `scrub_menzioni` su tutto il testo (domini esclusi dal catalogo), in tempo
  lineare anche su stringhe ostili del modello.

Lo schema del modello è COMPATTO (vedi `schemas/partenariato.py`): codici e
numeri arrivano come stringhe, "" = assente. Qui si riportano ai tipi dei DTO:
- codici: confronto senza maiuscole, accenti e punteggiatura sul codice o
  sull'etichetta del vocabolario, più pochi sinonimi univoci («ATI» →
  `ati_rti`, «EBITDA» → `mol`); un valore IGNOTO diventa il codice residuale
  («altro», «altra», «non_indicato»…) con un avviso, quindi `da_verificare`;
  una regola finanziaria con una variabile ignota non è rappresentabile nel
  contratto WP1: resta fuori dalle regole, con un avviso globale;
- numeri: cifre con punto (o virgola) decimale; un marcatore di assenza
  scritto a parole («n.d.», «non indicato», «-») vale assente; ogni altra
  stringa non leggibile vale assente con un avviso (`da_verificare`), mai
  un'eccezione.

Le voci `da_verificare` restano visibili all'utente (con il badge) ma sono
escluse dagli usi deterministici del modulo.
"""

import math
import re
import unicodedata
from bisect import bisect_left
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any, get_args

from app.schemas.partenariato import (
    BaseCalcolo,
    Citazione,
    CitazioneRegolaOut,
    ComposizioneOut,
    ComposizioneVoce,
    ConteggioOut,
    Costituzione,
    CostituzioneOut,
    DocumentoRichiestoOut,
    EffettoViolazione,
    FormaAmmessaOut,
    ModalitaOut,
    Momento,
    PartenariatoEstrazione,
    QuotaOut,
    QuotaVoce,
    RegolaFinanziariaEstratta,
    RegolaFinanziariaOut,
    RegolePartenariatoOut,
    TipoDocumentoRichiesto,
    TipoVincolo,
    VincoloOut,
    avvisi_convalida,
)
from app.schemas.regole_finanziarie import RegolaFinanziaria, UnitaRegola, VariabileFinanziaria
from app.services.bilanci_indicatori import valida_regola
# Le tolleranze della verifica, riusate per ricostruire la frase delle quote
# (`_trova_frase` tollerante): stessi tentativi, stesso ordine.
from app.services.citazioni import (
    _BORDI_AGO,
    _ELLISSI,
    _SPAZIO_NON_TRA_CIFRE,
    _TRATTINO_NON_TRA_CIFRE,
    _TRATTINO_TRA_PAROLE,
    MIN_CARATTERI_SENZA_SPAZI,
    MIN_FRAMMENTO_ELLISSI,
    _senza_spazi,
    _varianti_ago,
    normalizza_caratteri,
    normalizza_sezione,
    normalizza_testo,
    verifica_citazione,
)
from app.services.partenariato_preclassificatore import PATTERN as PATTERN_PRECLASSIFICATORE
from app.services.partenariato_prompts import scrub_menzioni
from app.services.partenariato_vocabolario import (
    DOCUMENTI,
    FORME,
    TIPI_SOGGETTO,
    beneficiari_per_tipo,
)

MAX_TESTO = 2000
MAX_URL = 2048
# La citazione della modalità decide `modalita_effettiva` (filtro «Ammette
# partenariato»): ritrovarla alla lettera non basta se è un frammento che
# compare ovunque («partenariato», «in ATS»). Sotto soglia la voce resta
# da verificare. Le altre voci non hanno soglia (citano spesso numeri brevi:
# «almeno 3 imprese»).
MIN_PAROLE_CITAZIONE_MODALITA = 3
MIN_CARATTERI_CITAZIONE_MODALITA = 15
_DOCUMENTO = re.compile(r"D(\d+)-p(\d+)")
_SEZIONE_SCHEDA = re.compile(r"S\d+")
# Prefisso minimo per riconoscere una regione per inizio del nome
# («Valle d'Aosta» → «Valle d'Aosta/Vallée d'Aoste»).
_MIN_PREFISSO_REGIONE = 5


# ------------------------------------------------------------ utilità


# Margine oltre il limite prima della pulizia: le menzioni tolte accorciano.
_MARGINE_TESTO = 512


def _testo(valore: Any, limite: int = MAX_TESTO) -> str | None:
    """Testo del modello per l'API: spazi compattati, tagliato PRIMA della
    pulizia (con un margine) e di nuovo dopo: l'output del modello non ha
    lunghezza massima per campo."""
    if not isinstance(valore, str):
        return None
    compatto = " ".join(valore.split())[: limite + _MARGINE_TESTO]
    pulito = " ".join(scrub_menzioni(compatto).split())
    return pulito[:limite] or None


def _url_https(url: Any) -> str | None:
    if not isinstance(url, str) or len(url) > MAX_URL:
        return None
    return url if url.lower().startswith("https://") else None


def _finito(numero: float | int | None) -> bool:
    return numero is not None and math.isfinite(float(numero))


def _stato(verificata: bool, avvisi: list[str]) -> str:
    return "verificata" if verificata and not avvisi else "da_verificare"


# ------------------------------------------------------------ codici e numeri

# Oltre questa lunghezza un «codice» del modello è comunque ignoto: la chiave
# si calcola su un prefisso (stringhe ostili lunghe restano in tempo lineare).
_MAX_CHIAVE = 120


def _chiave(valore: str) -> str:
    """«Micro impresa», «micro-impresa», «MICRO_IMPRESA» → «micro_impresa»."""
    ascii_ = unicodedata.normalize("NFKD", valore[:_MAX_CHIAVE]).encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", "_", ascii_.decode().casefold()).strip("_")


def _indice(
    codici: Iterable[str],
    etichette: Mapping[str, str] | None = None,
    sinonimi: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Chiave normalizzata → codice: i codici, poi le etichette del
    vocabolario, poi i sinonimi (a parità vince il primo)."""
    indice: dict[str, str] = {}
    coppie = [(c, c) for c in codici]
    coppie += [(etichetta, c) for c, etichetta in (etichette or {}).items()]
    coppie += list((sinonimi or {}).items())
    for testo, codice in coppie:
        chiave = _chiave(testo)
        if chiave:
            indice.setdefault(chiave, codice)
    return indice


def _vuoto(valore: Any) -> bool:
    return not isinstance(valore, str) or not valore.strip()


def _codice(valore: Any, indice: Mapping[str, str]) -> str | None:
    """Il codice del vocabolario per il testo del modello; None se assente
    ("") o ignoto (distinguerli con `_vuoto`)."""
    if _vuoto(valore):
        return None
    chiave = _chiave(valore)
    return indice.get(chiave) if chiave else None


def _codice_o_residuo(
    valore: Any, indice: Mapping[str, str], residuo: str, avviso: str, avvisi: list[str]
) -> str:
    """Codice riconosciuto; "" → `residuo` (è il valore «assente»); ignoto →
    `residuo` e l'avviso «<avviso>: <valore>» (la voce va da verificare)."""
    codice = _codice(valore, indice)
    if codice is not None:
        return codice
    if not _vuoto(valore):
        avvisi.append(f"{avviso}: {_testo(valore, 80) or '?'}")
    return residuo


# Solo sinonimi univoci; il resto passa da codice ed etichetta. Niente
# «startup» → startup_innovativa: la startup innovativa è una categoria
# giuridica (sezione speciale del Registro delle imprese), una «startup»
# generica resta «altro» con il testo del modello, da verificare.
_TIPI = _indice(
    TIPI_SOGGETTO,
    {codice: voce.etichetta for codice, voce in TIPI_SOGGETTO.items()},
    {
        "piccole e medie imprese": "pmi",
        "piccola e media impresa": "pmi",
        "microimpresa": "micro_impresa",
        "start up innovativa": "startup_innovativa",
        "ente di ricerca": "organismo_ricerca",
        "organismo di ricerca e diffusione della conoscenza": "organismo_ricerca",
        "ateneo": "universita",
        "pubblica amministrazione": "ente_pubblico",
        "ente del terzo settore": "ente_terzo_settore",
        "ets": "ente_terzo_settore",
    },
)
_FORME = _indice(
    FORME,
    {codice: voce.etichetta for codice, voce in FORME.items()},
    {
        "ati": "ati_rti",
        "rti": "ati_rti",
        "associazione temporanea di imprese": "ati_rti",
        "raggruppamento temporaneo di imprese": "ati_rti",
        "associazione temporanea di scopo": "ats",
        "contratto di rete": "rete_contratto",
        "altro": "altra",
    },
)
_COSTITUZIONE = _indice(
    get_args(Costituzione),
    sinonimi={"costituenda": "costituenda_ammessa", "costituita": "costituita_richiesta"},
)
_BASI = _indice(get_args(BaseCalcolo), sinonimi={"non_indicato": "non_indicata"})
_EFFETTI = _indice(get_args(EffettoViolazione), sinonimi={"non_indicata": "non_indicato"})
_TIPI_VINCOLO = _indice(get_args(TipoVincolo))
_MOMENTI = _indice(
    get_args(Momento),
    sinonimi={"presentazione della domanda": "domanda", "non_indicata": "non_indicato"},
)
_DOCUMENTI = _indice(
    get_args(TipoDocumentoRichiesto),
    DOCUMENTI,
    {
        "lettera di intenti": "lettera_intenti",
        "accordo di riservatezza": "nda",
        "memorandum of understanding": "term_sheet_mou",
        "mou": "term_sheet_mou",
    },
)
_VARIABILI = _indice(
    get_args(VariabileFinanziaria),
    sinonimi={
        "ebitda": "mol",
        "margine operativo lordo": "mol",
        "utile": "risultato_esercizio",
        "utile di esercizio": "risultato_esercizio",
        "utile netto": "risultato_esercizio",
        "ricavi": "fatturato",
        "numero di dipendenti": "dipendenti",
    },
)
_UNITA = _indice(get_args(UnitaRegola), sinonimi={"eur": "euro"})
# Come `bilanci_indicatori.valida_regola`: le variabili che si contano.
_CONTEGGI = frozenset({"dipendenti", "bilanci_approvati_n"})

# Numeri del modello: cifre con punto o virgola decimale (anche «30%»). Un
# numero con un solo separatore seguito da tre cifre («1.000», «12,500») è
# ambiguo (migliaia o decimali?) e non si legge: meglio da verificare che
# sbagliato.
_NUMERO = re.compile(r"[+-]?\d{1,12}(?:[.,]\d{1,6})?")
_AMBIGUO = re.compile(r"[+-]?[1-9]\d{0,2}[.,]\d{3}")

# L'assenza scritta a parole al posto di "" (il prompt chiede "", il modello a
# volte scrive «non indicato»): vale ASSENTE, non «non leggibile». Confronto
# senza maiuscole, accenti, spazi e punteggiatura («N.D.» → «nd»), ma con le
# parentesi: «[null]» è il JSON di un elenco (convalida tollerante), un valore
# presente e non convertibile. Solo questi marcatori, interi: tutto il resto
# («almeno 2», «nessun massimo indicato») resta non leggibile. «Illimitato» ha
# senso solo per i massimi, ma anche su un minimo non è un numero: assente.
_ASSENZA_NUMERO = frozenset(
    ["null", "na", "nd", "assente", "assenti", "nonapplicabile", "nessunlimite",
     "senzalimite", "senzalimiti", "nonpresente", "nonpresenti"]
    + [f"{radice}{finale}" for radice in ("nonindicat", "nonspecificat", "nonprevist",
                                          "nondefinit", "illimitat") for finale in "oaie"]
)
# «Nessuno» su un MINIMO vale assente (nessun minimo = zero); su un massimo
# può voler dire zero («grandi imprese: nessuna», cioè escluse): lì resta non
# leggibile, quindi da verificare.
_ASSENZA_SOLO_MINIMO = frozenset(["nessuno", "nessuna", "nessun", "none"])
# Trattini isolati («-», «–», «—»): il segno di «nessun valore» nelle tabelle.
_TRATTINI = frozenset("-‐‑‒–—―−")
_MAX_MARCATORE = 40


def _assenza_scritta(testo: str, *, minimo: bool = False) -> bool:
    """True se `testo` (non vuoto) è un marcatore di assenza (`minimo`: il
    valore è un minimo, dove anche «nessuno» vale assente)."""
    testo = testo.strip()
    if not testo or len(testo) > _MAX_MARCATORE:
        return False
    if all(carattere in _TRATTINI for carattere in testo):
        return True
    ascii_ = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode()
    chiave = re.sub(r"[\s.,;:/_'\"-]", "", ascii_.casefold())
    return chiave in _ASSENZA_NUMERO or (minimo and chiave in _ASSENZA_SOLO_MINIMO)


def _numero(valore: Any, *, minimo: bool = False) -> tuple[float | None, bool]:
    """(numero, leggibile): "" o un marcatore di assenza («n.d.», «non
    indicato», «-»; «nessuno» solo se `minimo`) → (None, True), cioè assente e
    non un errore."""
    if _vuoto(valore) or _assenza_scritta(valore, minimo=minimo):
        return None, True
    testo = valore.strip().removesuffix("%").strip()
    if not _NUMERO.fullmatch(testo) or _AMBIGUO.fullmatch(testo):
        return None, False
    return float(testo.replace(",", ".")), True


def _intero(valore: Any, *, minimo: bool = False) -> tuple[int | None, bool]:
    numero, leggibile = _numero(valore, minimo=minimo)
    if numero is None:
        return None, leggibile
    if not numero.is_integer():
        return None, False
    return int(numero), True


def _documenti(fonti: Any) -> dict[int, dict]:
    """Fonti (voci di `fonti_usate` o oggetti con n/etichetta/url) per numero."""
    per_numero: dict[int, dict] = {}
    for fonte in fonti or []:
        if isinstance(fonte, dict):
            n = fonte.get("n")
            voce = fonte
        else:
            n = getattr(fonte, "n", None)
            voce = {"etichetta": getattr(fonte, "etichetta", None), "url": getattr(fonte, "url", None)}
        if isinstance(n, int) and not isinstance(n, bool):
            per_numero[n] = voce
    return per_numero


# La scheda del catalogo (META, S1…) e le junction dei beneficiari sono testo
# GENERATO o CLASSIFICATO dal produttore del catalogo, non estratto dall'atto:
# la generazione SEO ha inserito «in forma singola o associata» e
# «partenariato pubblico-privato» in schede i cui atti non lo dicono. Una
# citazione ritrovata lì resta visibile («Scheda del bando»), ma la voce resta
# da verificare: fa fede solo il testo dei documenti ufficiali.
AVVISO_SCHEDA = "Dalla scheda del catalogo: da verificare sul bando ufficiale"


def _da_fonte_ufficiale(citazione: Citazione | None) -> bool:
    """True se la citazione punta a una pagina di un documento ufficiale
    («D1-p3» nel formato di `normalizza_sezione`, che accetta anche «[D1-P3]»,
    «D1 pag. 3»…). La scheda del catalogo (META, S1…) e ogni altra sezione
    non lo sono."""
    if citazione is None or not isinstance(citazione.sezione, str):
        return False
    return _DOCUMENTO.fullmatch(normalizza_sezione(citazione.sezione)) is not None


def _citazione(
    citazione: Citazione | None,
    sezioni: dict[str, str],
    documenti: dict[int, dict],
    avvisi: list[str],
):
    """(CitazioneRegolaOut | None, verificata). Sezione e testo entrambi ""
    = citazione assente. `verificata` (per la voce) = ritrovata nel testo E su
    un documento ufficiale (`_da_fonte_ufficiale`); la citazione in uscita
    dice solo se è stata ritrovata alla lettera. Una citazione della scheda del
    catalogo aggiunge `AVVISO_SCHEDA` ad `avvisi`: la voce è da verificare."""
    if citazione is None:
        return None, False
    grezza = citazione.sezione if isinstance(citazione.sezione, str) else ""
    testo = citazione.testo if isinstance(citazione.testo, str) else ""
    if not grezza.strip() and not testo.strip():
        return None, False
    sezione = normalizza_sezione(grezza) or grezza.strip()[:40]
    ritrovata = verifica_citazione(grezza, testo, sezioni)
    url = pagina = None
    corrispondenza = _DOCUMENTO.fullmatch(sezione)
    if corrispondenza:
        n, pagina = int(corrispondenza.group(1)), int(corrispondenza.group(2))
        doc = documenti.get(n) or {}
        etichetta = _testo(doc.get("etichetta"), 200) or f"Documento {n}"
        fonte = f"{etichetta} — pag. {pagina}"
        url = _url_https(doc.get("url"))
    elif sezione == "META" or _SEZIONE_SCHEDA.fullmatch(sezione):
        fonte = "Scheda del bando"
        avvisi.append(AVVISO_SCHEDA)
    else:
        fonte = "Fonte non riconosciuta"
    return (
        CitazioneRegolaOut(
            sezione=sezione[:40],
            fonte_etichetta=fonte,
            testo=_testo(testo) or "",
            verificata=ritrovata,
            url_documento=url,
            pagina=pagina,
        ),
        ritrovata and _da_fonte_ufficiale(citazione),
    )


# ------------------------------------------------------------ regioni


def _chiave_regione(nome: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", ascii_.casefold())


def _indice_regioni(lookups: Any) -> list[tuple[str, int, str]] | None:
    """[(chiave normalizzata, id, nome del catalogo)] dalle lookups (oggetto
    con `.regioni` o dict); None se non disponibili."""
    if lookups is None:
        return None
    regioni = lookups.get("regioni") if isinstance(lookups, dict) else getattr(lookups, "regioni", None)
    if not regioni:
        return None
    indice: list[tuple[str, int, str]] = []
    for voce in regioni:
        vid = voce.get("id") if isinstance(voce, dict) else getattr(voce, "id", None)
        nome = voce.get("nome") if isinstance(voce, dict) else getattr(voce, "nome", None)
        if isinstance(vid, int) and isinstance(nome, str) and nome.strip():
            indice.append((_chiave_regione(nome), vid, nome))
    return indice or None


def mappa_regioni(
    nomi: list[str], indice: list[tuple[str, int, str]] | None
) -> tuple[list[str], list[int], list[str]]:
    """(nomi del catalogo riconosciuti, id, nomi scartati). Confronto senza
    accenti, spazi e punteggiatura; poi per inizio del nome (≥ 5 lettere)."""
    riconosciuti: list[str] = []
    ids: list[int] = []
    scartati: list[str] = []
    for nome in nomi or []:
        if not isinstance(nome, str) or not nome.strip():
            continue
        chiave = _chiave_regione(nome)
        trovata = None
        if indice and chiave:
            trovata = next((voce for voce in indice if voce[0] == chiave), None)
            if trovata is None and len(chiave) >= _MIN_PREFISSO_REGIONE:
                simili = [v for v in indice if v[0].startswith(chiave) or chiave.startswith(v[0])]
                trovata = simili[0] if len(simili) == 1 else None
        if trovata is None:
            scartati.append(_testo(nome, 80) or "")
            continue
        if trovata[1] not in ids:
            ids.append(trovata[1])
            riconosciuti.append(trovata[2])
    return riconosciuti, ids, [s for s in scartati if s]


# ------------------------------------------------------------ «non ammesso»

# «Non ammesso» toglie il bando dal filtro «Ammette partenariato» e blocca le
# call: vale solo se il passaggio citato contiene un'ESCLUSIONE esplicita e la
# sua frase, ritrovata ALLA LETTERA nel testo inviato, non ammette la forma
# associata. Due specie di esclusione:
# - la NEGAZIONE dell'aggregazione («non sono ammessi raggruppamenti»), che
#   vale solo se l'aggregazione negata non è qualificata da ciò che segue
#   («raggruppamenti tra imprese collegate», «partenariato minimo», «con
#   soggetti fuori regione» non escludono l'aggregazione: la regolano);
# - l'OBBLIGO della forma singola, retto da un avverbio restrittivo
#   («esclusivamente in forma singola»), da un predicato prescrittivo non
#   negato («la domanda è presentata in forma singola», «deve essere
#   presentata singolarmente») o da «presentati da singole imprese»; vale solo
#   se la frase non nomina alcuna aggregazione («ciascuna impresa aderente alla
#   rete presenta singolarmente la propria domanda» la presuppone).
# «In forma singola», «singolarmente», «a titolo individuale» da soli («in caso
# di partecipazione in forma singola», «il proponente in forma singola deve
# avere sede…») non escludono nulla. Un elenco di beneficiari («possono
# presentare domanda le PMI»), anche «in forma singola», o «una sola domanda
# per impresa» nemmeno: il bando è muto, quindi non determinabile.
# Le regex lavorano sul testo di `normalizza_testo` (minuscolo, spazi
# compattati, tipografia in ASCII) con ripetizioni LIMITATE, in tempo lineare:
# la citazione è output del modello, la frase testo del PDF.
_MAX_CITAZIONE_ESCLUSIONE = 4000
# Quanto testo prima e dopo la citazione si guarda per ritrovarne la frase.
_CONTESTO_FRASE = 300
# Quanto testo dopo un'aggregazione negata si guarda per un qualificatore.
_CODA_ESCLUSIONE = 120
# Candidati di fine frase: «.», «;», «!», «?» seguiti da uno spazio. Il punto
# delle abbreviazioni non chiude la frase («Reg. (UE) n. 651/2014», «art. 3»,
# «D.Lgs. 123», «c.c.»): una lettera sola, una parola con lettere e un punto
# interno o una di `_ABBREVIAZIONI` (non una cifra: «punto 3. Le imprese»).
_CANDIDATO_FINE = re.compile(r"[.;!?](?=\s)")
_CANDIDATO_FINE_PROPOSIZIONE = re.compile(r"[.;:!?](?=\s)")
_ABBREVIAZIONI = frozenset({
    "art", "artt", "reg", "regg", "lett", "par", "pag", "pagg", "all", "ecc", "cfr", "co",
    "num", "nn", "nr", "sig", "dott", "prof", "ing", "cap", "tab", "fig", "vol", "es", "rif",
    "prot", "ss", "segg", "succ", "mod", "int", "ult", "tit", "sez", "cod", "cit", "dlgs",
    "dpr", "dm", "dl", "del", "delib", "det", "disp", "approv", "pp",
})
_MAX_ABBREVIAZIONE = 20
_I = re.IGNORECASE

_FORMA_AGGREGATA = r"(?:associat|aggregat|congiunt|collettiv|raggruppat|collaborativ)\w*"
_SIGLE = r"(?<![\w.])(?:a\.?t\.?[is]|r\.?t\.?i)\.?(?!\w)"
_ELISIONE = r"(?:(?:l|d|un|all|dell|dall|nell|sull)')?"
# Che cosa un'esclusione esclude: la forma associata o un tipo di aggregazione.
_AGGREGAZIONE = (
    _ELISIONE
    + r"(?:in\s+forma\s+" + _FORMA_AGGREGATA
    + r"|form[ae]\s+" + _FORMA_AGGREGATA
    + r"|(?:domand|istanz|candidatur|progett|propost|partecipazion|richiest)\w*\s+"
    r"(?:presentat\w*\s+)?(?:in\s+forma\s+)?" + _FORMA_AGGREGATA
    + r"|congiuntamente|collettivamente|raggruppament\w*|aggregazion\w*"
    r"|associazion[ei]\s+temporane\w*|" + _SIGLE
    + r"|consorz\w*|ret[ei]\s+(?:di\s+|d')\s*impres\w*|contratt[oi]\s+di\s+rete"
    r"|partenariat\w*|co-?proponent\w*)"
)
# Parole ammesse tra la negazione e l'aggregazione esclusa: articoli,
# preposizioni e i nomi e verbi della domanda. Un elenco CHIUSO: «non sono
# ammesse modifiche del raggruppamento», «non è ammessa la partecipazione a
# più raggruppamenti», «non prevede un numero massimo di partner nel
# raggruppamento» non escludono l'aggregazione, la presuppongono.
_TRA = (
    r"(?:" + _ELISIONE
    + r"(?:il|lo|la|i|gli|le|un|uno|una|di|da|in|a|ad|con|per|tra|fra|del|dello|della|dei"
    r"|degli|delle|dal|dallo|dalla|dai|dagli|dalle|al|allo|alla|ai|agli|alle|nel|nello|nella"
    r"|nei|negli|nelle|parte|sotto|tramite|mediante|attraverso|quale|quali|come|forma|forme"
    r"|modalit\w*|impres[ae]|soggett\w*|aziend\w*|operator\w*|domand\w*|istanz\w*"
    r"|candidatur\w*|partecip\w*|presentaz\w*|present\w*|propost\w*|progett\w*|richiest\w*"
    r"|costituit\w*|riunit\w*|organizzat\w*|candidar\w*|concorr\w*)\s+)"
)
_NEGA_AMMISSIONE = (
    r"(?:(?:è|e'|sono|sarà|saranno|risulta|risultano)\s+"
    r"(?:ammess|consentit|possibil|previst|ammissibil|accettat|ricevibil|finanziabil"
    r"|autorizzat)\w*"
    r"|(?:può|possono|potrà|potranno|puo')\s+(?:essere\s+)?"
    r"(?:partecip|present|candid|concorr|acced|richied|ammess|finanzi|propost|propo|inoltr"
    r"|costitu|riun)\w*)"
)
# Negazioni dell'aggregazione. Le formule «nega» FORTI del pre-classificatore
# (stesse regex, non copiate) ne fanno parte; quella debole («in forma
# singola» ovunque compaia) no.
_ESCLUSIONI_NEGATE = tuple(
    p.regex for p in PATTERN_PRECLASSIFICATORE if p.categoria == "nega" and p.forte
) + tuple(
    re.compile(regex, _I)
    for regex in (
        # «non sono ammesse domande in forma associata», «non è possibile
        # presentare domanda da parte di raggruppamenti»
        r"\bnon\s+" + _NEGA_AMMISSIONE + r"\s+" + _TRA + r"{0,8}?" + _AGGREGAZIONE,
        # «non prevede la partecipazione in forma aggregata»: verbi generici,
        # l'aggregazione deve seguire da vicino.
        r"\bnon\s+(?:si\s+)?(?:ammett|consent|accett|preved|contempl)\w*\s+" + _TRA + r"{0,3}?"
        + _AGGREGAZIONE,
        # «le forme associate non sono ammesse», «i raggruppamenti sono esclusi»
        # (l'aggregazione è il soggetto: articolo o inizio di frase davanti)
        r"(?:^|[.;:,(]\s*|\b(?:le|i|gli|la|il|l')\s+)" + _AGGREGAZIONE + r"\s+" + _TRA
        + r"{0,6}?(?:non\s+(?:è|e'|sono|sarà|saranno)\s+(?:ammess|ammissibil|consentit"
        r"|finanziabil|ricevibil)\w*|(?:è|e'|sono|sarà|saranno)\s+(?:esclus|inammissibil)\w*"
        r"|non\s+(?:può|possono|potrà|potranno)\s+essere\s+(?:ammess|finanziat)\w*)",
        # «è esclusa la partecipazione in forma associata», «con esclusione delle
        # ATI» (MAI «esclusi dal raggruppamento»: lì si esclude da, non si esclude)
        r"\besclus[aeio]\s+(?:(?:la|le|il|i|gli)\s+|l')?"
        r"(?:(?:possibilit\w*|facolt\w*)\s+di\s+\w+\s+)?" + _AGGREGAZIONE,
        r"\b(?:ad|con)\s+esclusione\s+(?:di|dei|delle|degli|del|della|dell')\s*" + _AGGREGAZIONE,
        r"\binammissibil\w*\s+" + _TRA + r"{0,6}?" + _AGGREGAZIONE,
        r"\bnon\s+(?:può|possono|potrà|potranno)\s+(?:associarsi|aggregarsi|raggrupparsi"
        r"|riunirsi|consorziarsi)",
        # inglese dei bandi UE
        r"\b(?:consorti(?:a|um)|partnerships?|joint\s+(?:applications?|proposals?|projects?)"
        r"|groups?\s+of\s+applicants?)\s+(?:are|is|will)\s+not\s+(?:be\s+)?(?:eligible"
        r"|accepted|allowed|admissible|permitted|funded)",
        r"\bnot\s+(?:be\s+)?(?:eligible|accepted|allowed|admissible|permitted)\s+"
        r"(?:to\s+apply\s+)?(?:as|in|for)\s+(?:a\s+)?(?:consorti|partnership|joint|group)",
    )
)
# Ciò che, subito dopo un'aggregazione negata, la QUALIFICA: la negazione
# allora colpisce solo quel tipo di aggregazione («raggruppamenti tra imprese
# collegate», «partenariato minimo», «con soggetti aventi sede fuori regione»,
# «per la linea A»). «Tra loro» è reciproco, non qualifica.
_QUALIFICATORE = re.compile(
    r"\b(?:(?:tra|fra)(?!\s+(?:di\s+)?loro\b)|con|che|cui|avent\w*|compost\w*|costituit\w*\s+da"
    r"|format\w*\s+da|oltre|superior\w*|inferior\w*|più\s+di|meno\s+di|minim\w*|massim\w*"
    r"|eccedent\w*|collegat\w*|controllat\w*|appartenent\w*|fuori|estern\w*|ester[oiae]"
    r"|divers[oiae]|salvo|tranne|eccetto|eccezione|se\s+non|qualora|nel\s+caso|in\s+caso"
    r"|limitatament\w*|successiv\w*|dopo|per\s+(?:la|le|il|i|gli|l')\s*(?:line|misur|azion"
    r"|tipologi|intervent|sportell|fas|lott)\w*|nell'ambito"
    r"|between|among|with|whose|that|which|where|composed|consisting|including|involving"
    r"|more\s+than|fewer\s+than|less\s+than|exceeding|above|below|other\s+than|except|unless"
    r"|outside)\b",
    _I,
)
# Obblighi della forma singola: da soli bastano solo se la frase non nomina
# alcuna aggregazione (`_MENZIONE_AGGREGAZIONE`).
_SINGOLO = (
    r"(?:in\s+forma\s+(?:singola|individuale)|singolarmente|individualmente"
    r"|a\s+titolo\s+individuale|individually)\b"
)
_PRESCRITTIVO = (
    r"(?<!non )\b(?:dev(?:e|ono|rà|ranno)\s+(?:essere\s+)?(?:partecip|present|propost|candid"
    r"|concorr|realizzat|inoltrat|richiest)\w*"
    r"|partecipa|partecipano|concorre|concorrono|presenta|presentano|propone|propongono"
    r"|avviene|avvengono|si\s+candida(?:no)?"
    r"|(?:è|sono)\s+(?:presentat|propost|inoltrat|candidat)\w*"
    r"|(?:must|shall)\s+(?:be\s+submitted|apply|be\s+presented))\b"
)
_OBBLIGHI_FORMA_SINGOLA = tuple(
    re.compile(regex, _I)
    for regex in (
        # «la domanda è presentata in forma singola», «ciascuna impresa
        # partecipa singolarmente», «must be submitted individually»
        _PRESCRITTIVO + r"(?:\s+(?!non\b|o\b|e\b|oppure\b|anche\b|or\b)[^\s.;:!?,]+){0,4}?\s+"
        + _SINGOLO,
        # «esclusivamente in forma singola», «solo singole imprese»
        r"(?<!non )\b(?:esclusivamente|solo|soltanto|unicamente)\s+(?:(?:da|le|i|gli|dalle|dai"
        r"|dagli|imprese|soggetti|aziende|parte|di|una|un|uno|in|forma)\s+){0,3}"
        r"(?:singol|individual)\w*",
        # «progetti presentati da singole imprese»
        r"\b(?:presentat|propost|realizzat|candidat|richiest|inoltrat)\w*\s+"
        r"(?:(?:esclusivamente|solo|soltanto|unicamente)\s+)?da\s+"
        r"(?:(?:un|una|uno|ciascun|ciascuna)\s+)?singol[aeio]\s+(?:impres|soggett|aziend"
        r"|beneficiar|richiedent|proponent|operator|ent[ei]\b|imprenditor|professionist"
        r"|organizzazion|organism)",
        # inglese dei bandi UE
        r"\bonly\s+(?:(?:by|from|a|an|one|legal)\s+){0,3}(?:single|individual|one)\s+"
        r"(?:legal\s+)?(?:applicant|entit|organi[sz]ation|compan|beneficiar|participant"
        r"|enterprise|sme)",
        r"\b(?:must|shall)\s+(?:be\s+submitted|apply|be\s+presented)\s+by\s+(?:a\s+)?single\s+"
        r"(?:applicant|entit|organi[sz]ation|compan|legal)",
    )
)
_MENZIONE_AGGREGAZIONE = re.compile(
    r"partner|partenariat|capofila|mandatari|mandant|compagin|raggruppa|aggrega|associat"
    r"|associazion[ei]\s+temporane|congiunt|collettiv|collaborativ|consorz|consorti(?:um|a)\b"
    r"|\bret[ei]\b|retist|aderent|co-?proponent|" + _SIGLE
    + r"|\bjoint\b|\bgroups?\s+of\b|coordinator|lead\s+(?:applicant|beneficiar)",
    _I,
)

_AGGREGAZIONE_AMMESSA = (
    _ELISIONE
    + r"(?:(?:associat|aggregat|congiunt|collettiv|raggruppat|collaborativ|partenariat"
    r"|raggruppament|aggregazion|associazion|consorz|consorti|joint|partnership|group)\w*"
    r"|" + _SIGLE + r"|ret[ei]\b|contratt\w*\s+di\s+rete)"
)
_NON_NEGATO = r"(?!non\b)[^\s.;:!?]+"
# Formule che ammettono la forma associata anche dentro un'esclusione: si
# cercano sulla frase intera.
_AMMISSIONI = tuple(
    re.compile(regex, _I)
    for regex in (
        # «in forma singola o associata», «in forma singola e associata»,
        # «singole o riunite in ATS», «sia in forma singola, sia in forma
        # collaborativa», «individually or as a consortium», «by a single
        # applicant or by a consortium»
        r"\b(?:singol|single|individual)\w*(?:\s+" + _NON_NEGATO + r"){0,3}?\s*,?\s+"
        r"(?:o|oppure|ovvero|e/o|e|sia|che|nonché|or|and/or|and)\s+(?:" + _NON_NEGATO
        + r"\s+){0,4}?" + _AGGREGAZIONE_AMMESSA,
        # «anche in forma congiunta», «anche riunite in ATS», «also as a consortium»
        r"\b(?:anche|pure|also)\s+(?:" + _NON_NEGATO + r"\s+){0,2}?"
        r"(?:(?:in|come|tramite|mediante|attraverso|quale|sotto\s+forma\s+di|as|by)\s+"
        r"(?:(?:forma|a|an)\s+)?)?" + _AGGREGAZIONE_AMMESSA,
    )
)
# Formule che ammettono la forma associata ma compaiono anche dentro le
# esclusioni («non sono ammesse domande in forma associata»): si cercano sulla
# frase senza le esclusioni valide.
_AMMISSIONI_FUORI_ESCLUSIONE = tuple(
    re.compile(regex, _I)
    for regex in (
        r"\bin\s+forma\s+" + _FORMA_AGGREGATA,
        r"\b(?:(?:è|e'|sono)\s+(?:ammess|ammissibil|consentit|previst)\w*"
        r"|(?:possono|può|puo')\s+(?:partecip|present|candid|concorr|acced)\w*)\s+" + _TRA
        + r"{0,6}?" + _AGGREGAZIONE,
        r"\b(?:consorti(?:a|um)|partnerships?|joint\s+(?:applications?|proposals?))\s+"
        r"(?:are|is)\s+(?:also\s+)?(?:eligible|allowed|accepted|admissible)",
    )
)


# La stessa pagina si rilegge per molte citazioni (modalità, quote): la sua
# forma normalizzata si calcola una volta.
_pagina_normalizzata = lru_cache(maxsize=32)(normalizza_testo)


def _fine_frase(testo: str, pos: int, punto_e_virgola: bool) -> bool:
    """True se il segno in `testo[pos]` (un candidato di fine frase) chiude
    davvero la frase: non il punto di un'abbreviazione, né il «;» quando non
    lo si chiede."""
    segno = testo[pos]
    if segno == ";":
        return punto_e_virgola
    if segno != ".":
        return True
    spazio = testo.rfind(" ", max(0, pos - _MAX_ABBREVIAZIONE), pos)
    if spazio < 0 and pos > _MAX_ABBREVIAZIONE:
        return True  # parola lunga: non è un'abbreviazione
    # «dell'art.» → «art»
    parola = testo[spazio + 1 : pos].rsplit("'", 1)[-1].lstrip("([\"")
    lettere = any(carattere.isalpha() for carattere in parola)
    return not (
        (len(parola) == 1 and lettere) or ("." in parola and lettere) or parola in _ABBREVIAZIONI
    )


def _rimuovi(testo: str, regex: re.Pattern) -> tuple[str, list[int]]:
    """(testo senza le corrispondenze di `regex`, mappa): `mappa[k]` è la
    posizione in `testo` del carattere k del risultato. Stesso risultato di
    `regex.sub("", testo)`."""
    pezzi: list[str] = []
    mappa: list[int] = []
    pos = 0
    for corrispondenza in regex.finditer(testo):
        pezzi.append(testo[pos : corrispondenza.start()])
        mappa.extend(range(pos, corrispondenza.start()))
        pos = corrispondenza.end()
    pezzi.append(testo[pos:])
    mappa.extend(range(pos, len(testo)))
    return "".join(pezzi), mappa


@lru_cache(maxsize=32)
def _forme_compatte(pagliaio: str) -> tuple[str, list[int], str, list[int]]:
    """Le forme del testo normalizzato che usa `citazioni` per la verifica,
    con la mappa verso le posizioni del testo normalizzato: senza trattini tra
    parole (`_TRATTINO_TRA_PAROLE`) e senza spazi (`citazioni._senza_spazi`)."""
    compatto, mappa_compatto = _rimuovi(pagliaio, _TRATTINO_TRA_PAROLE)
    senza_spazi, mappa_spazi = _rimuovi(pagliaio, _SPAZIO_NON_TRA_CIFRE)
    nudo, mappa_nudo = _rimuovi(senza_spazi, _TRATTINO_NON_TRA_CIFRE)
    return compatto, mappa_compatto, nudo, [mappa_spazi[k] for k in mappa_nudo]


def _cerca(ago: str, pagliaio: str, da: int = 0) -> tuple[int, int] | None:
    """(inizio, fine) nel testo normalizzato dell'ago (normalizzato), da `da`
    in poi, con le tolleranze della verifica e nello stesso ordine: alla
    lettera, le varianti di `_varianti_ago` (bordi, punteggiatura finale,
    sillabazione), senza trattini tra parole, e infine senza spazi (solo aghi
    di almeno `MIN_CARATTERI_SENZA_SPAZI` caratteri)."""
    varianti = _varianti_ago(ago)
    for variante in varianti:
        inizio = pagliaio.find(variante, da)
        if inizio >= 0:
            return inizio, inizio + len(variante)
    compatto, mappa_compatto, nudo, mappa_nudo = _forme_compatte(pagliaio)
    for testo, mappa, forma, minimo in (
        (compatto, mappa_compatto, lambda v: _TRATTINO_TRA_PAROLE.sub("", v), 1),
        (nudo, mappa_nudo, _senza_spazi, MIN_CARATTERI_SENZA_SPAZI),
    ):
        partenza = bisect_left(mappa, da)
        for variante in varianti:
            cercato = forma(variante)
            if len(variante) < minimo or not cercato:
                continue
            k = testo.find(cercato, partenza)
            if k >= 0:
                return mappa[k], mappa[k + len(cercato) - 1] + 1
    return None


# Nel testo dei PDF anche gli a capo separano le frasi: una voce di elenco
# («•», «-», «*», «a)», «1.», «1)») e la riga dopo un titolo senza
# punteggiatura finale («Art. 6 - Contributo e ripartizione tra i partner»,
# una riga tutta maiuscola, «2.1 Beneficiari») che inizia con la maiuscola
# aprono una frase nuova anche senza punto. Solo i titoli riconoscibili: una
# riga di prosa che va a capo prima di un nome proprio («…richiesta dal
# \nCapofila…») resta nella stessa frase.
_MARCATORE_ELENCO = re.compile(r"(?:[•·▪◦‣∙●○■□►▶*-]|[A-Za-z]\)|\d{1,3}[.)])\s")
_RIGA_TITOLO = re.compile(
    r"(?:[Aa]rt(?:icolo)?\.?\s*\d+"
    r"|(?:[Cc]apo|[Tt]itolo|[Ss]ezione|[Pp]aragrafo|[Aa]llegato)\s+[\dIVXLCivxlc]+\b"
    r"|\d+(?:\.\d+)*[.)]?\s+[A-ZÀ-Ý])"
)


def _titolo(riga: str) -> bool:
    """True se `riga` (spazi compattati, maiuscole conservate) è un titolo:
    senza punteggiatura finale, numerata o tutta maiuscola."""
    if riga[-1] in ".;:,!?":
        return False
    lettere = [carattere for carattere in riga if carattere.isalpha()]
    return bool(_RIGA_TITOLO.match(riga)) or (
        len(lettere) >= 3 and all(carattere.isupper() for carattere in lettere)
    )


@lru_cache(maxsize=32)
def _confini_di_riga(candidato: str) -> tuple[int, ...]:
    """Le posizioni, nel testo normalizzato di `candidato`
    (`_pagina_normalizzata`), dove una riga apre una frase nuova (voce di
    elenco, riga dopo un titolo). Si calcolano sul testo che conserva gli a
    capo; le righe normalizzate, unite da uno spazio, ridanno esattamente il
    testo normalizzato (se no, per prudenza, nessun confine)."""
    testo = normalizza_caratteri(candidato).replace("**", "")
    confini: list[int] = []
    righe: list[str] = []
    pos = 0
    precedente: str | None = None
    for grezza in testo.split("\n"):
        riga = " ".join(grezza.split())
        if not riga:
            continue
        inizio = pos + 1 if righe else 0
        if precedente is not None and (
            _MARCATORE_ELENCO.match(riga) or (_titolo(precedente) and riga[0].isupper())
        ):
            confini.append(inizio)
        righe.append(riga.casefold())
        pos = inizio + len(righe[-1])
        precedente = riga
    if " ".join(righe) != _pagina_normalizzata(candidato):
        return ()
    return tuple(confini)


def _posizione_tollerante(
    ago: str, pagliaio: str, punto_e_virgola: bool, confini: tuple[int, ...] = ()
) -> tuple[int, int] | None:
    """(inizio, fine) della citazione con le tolleranze della verifica. Con
    un'ellissi: una sola iniziale o finale si toglie; con un'ellissi interna i
    frammenti (ciascuno di almeno `MIN_FRAMMENTO_ELLISSI` caratteri, in
    ordine) devono stare nella STESSA frase del documento (né un segno di fine
    frase né un confine di riga, `confini`, tra il primo e l'ultimo),
    altrimenti None."""
    trovata = _cerca(ago, pagliaio)
    if trovata is not None or not _ELLISSI.search(ago):
        return trovata
    frammenti = [f.strip(_BORDI_AGO) for f in _ELLISSI.split(ago)]
    frammenti = [f for f in frammenti if f]
    if not frammenti or any(len(f) < MIN_FRAMMENTO_ELLISSI for f in frammenti):
        return None
    posizioni: list[tuple[int, int]] = []
    da = 0
    for frammento in frammenti:
        posizione = _cerca(frammento, pagliaio, da)
        if posizione is None:
            return None
        posizioni.append(posizione)
        da = posizione[1]
    inizio, fine = posizioni[0][0], posizioni[-1][1]
    if any(inizio < confine < fine for confine in confini) or any(
        _fine_frase(pagliaio, taglio.start(), punto_e_virgola)
        for taglio in _CANDIDATO_FINE.finditer(pagliaio, inizio, fine - 1)
    ):
        return None
    return inizio, fine


def _trova_frase(
    citazione: Citazione,
    sezioni: dict[str, str],
    *,
    punto_e_virgola: bool,
    tollerante: bool = False,
) -> tuple[str, int, int] | None:
    """(frase, inizio, fine): la frase del testo inviato (normalizzato) che
    contiene la citazione, fino a `_CONTESTO_FRASE` caratteri per lato, e la
    posizione della citazione dentro la frase. Una citazione troncata («le
    imprese in forma singola») non nasconde così il seguito («o associata»).
    La citazione si cerca nella pagina indicata o a cavallo con la successiva
    o la precedente:
    - di default ALLA LETTERA (a meno di `normalizza_testo`), come vuole la
      modalità: con le tolleranze della verifica (spazi del PDF, ellissi) la
      frase vera non si ricostruisce, e chi decide non la indovina;
    - con `tollerante` (le quote) con le stesse tolleranze della verifica
      (`_posizione_tollerante`): così esclusioni e percentuale si controllano
      sulla frase intera anche quando la citazione ha uno spazio in più o in
      meno, la punteggiatura o le virgolette ai bordi.
    None se non si ritrova."""
    ago = normalizza_testo(citazione.testo[:_MAX_CITAZIONE_ESCLUSIONE])
    if not ago:
        return None
    per_chiave: dict[str, str] = {}
    for chiave, testo in (sezioni or {}).items():
        if isinstance(testo, str) and testo:
            per_chiave.setdefault(normalizza_sezione(chiave), testo)
    chiave = normalizza_sezione(citazione.sezione)
    testo = per_chiave.get(chiave)
    if not testo:
        return None
    candidati = [testo]
    pagina = _DOCUMENTO.fullmatch(chiave)
    if pagina:
        documento, numero = pagina.group(1), int(pagina.group(2))
        successiva = per_chiave.get(f"D{documento}-p{numero + 1}")
        precedente = per_chiave.get(f"D{documento}-p{numero - 1}")
        if successiva:
            candidati.append(testo + "\n" + successiva)
        if precedente:
            candidati.append(precedente + "\n" + testo)
    confini: tuple[int, ...] = ()
    for candidato in candidati:
        pagliaio = _pagina_normalizzata(candidato)
        if tollerante:
            confini = _confini_di_riga(candidato)
            posizione = _posizione_tollerante(ago, pagliaio, punto_e_virgola, confini)
        else:
            inizio = pagliaio.find(ago)
            posizione = (inizio, inizio + len(ago)) if inizio >= 0 else None
        if posizione is not None:
            inizio, fine = posizione
            break
    else:
        return None
    da = max(0, inizio - _CONTESTO_FRASE)
    for taglio in _CANDIDATO_FINE.finditer(pagliaio, da, inizio):
        if _fine_frase(pagliaio, taglio.start(), punto_e_virgola):
            da = taglio.end()
    a = min(len(pagliaio), fine + _CONTESTO_FRASE)
    # Dall'ultimo carattere della citazione: se chiude la frase («… in forma
    # singola.»), la frase finisce lì e la successiva non conta.
    for taglio in _CANDIDATO_FINE.finditer(pagliaio, max(inizio, fine - 1), a):
        if _fine_frase(pagliaio, taglio.start(), punto_e_virgola):
            a = taglio.end()
            break
    # I confini di riga (solo `tollerante`): l'ultimo prima della citazione e
    # il primo dopo, fuori dalla citazione.
    da = max([da, *(c for c in confini if c <= inizio)])
    a = min([a, *(c for c in confini if c >= fine)])
    return pagliaio[da:a], inizio - da, fine - da


def _frase_della_citazione(citazione: Citazione, sezioni: dict[str, str]) -> tuple[str, bool]:
    """(frase, ricostruita): la frase della citazione (con il «;» come fine di
    frase), cercata con le tolleranze della verifica, e True; se non si
    ricostruisce (frammenti di un'ellissi in frasi diverse, citazione non
    ritrovata), la sola citazione normalizzata e False."""
    trovata = _trova_frase(citazione, sezioni, punto_e_virgola=True, tollerante=True)
    if trovata is None:
        return normalizza_testo(citazione.testo[:_MAX_CITAZIONE_ESCLUSIONE]), False
    return trovata[0], True


def _qualificata(testo: str, pos: int) -> bool:
    """True se ciò che segue `pos` (fino alla fine della proposizione, al più
    `_CODA_ESCLUSIONE` caratteri) qualifica l'aggregazione appena negata."""
    fine = min(len(testo), pos + _CODA_ESCLUSIONE)
    for taglio in _CANDIDATO_FINE_PROPOSIZIONE.finditer(testo, pos, fine):
        if taglio.group() != "." or _fine_frase(testo, taglio.start(), True):
            fine = taglio.start()
            break
    return _QUALIFICATORE.search(testo, pos, fine) is not None


def _senza_esclusioni(frase: str) -> str:
    """La frase con le negazioni NON qualificate sostituite da «§»: quelle
    qualificate restano, perché presuppongono l'aggregazione."""
    for regex in _ESCLUSIONI_NEGATE:
        frase = regex.sub(
            lambda m: m.group(0) if _qualificata(m.string, m.end()) else " § ", frase
        )
    return frase


def esclusione_esplicita(citazione: Citazione | None, sezioni: dict[str, str]) -> bool:
    """True se la citazione, ritrovata alla lettera, ESCLUDE espressamente la
    forma associata (negazione non qualificata) o ne impone quella singola
    (con una frase che non nomina aggregazioni), e la sua frase non la ammette
    («in forma singola o associata», «anche in ATS», …). È la condizione
    perché «non ammesso» valga: nel dubbio False (non determinabile)."""
    if citazione is None or not isinstance(citazione.testo, str):
        return False
    trovata = _trova_frase(citazione, sezioni, punto_e_virgola=False)
    if trovata is None:
        return False
    frase, inizio, fine = trovata
    ago = frase[inizio:fine]
    negata = any(
        not _qualificata(frase, inizio + m.end())
        for regex in _ESCLUSIONI_NEGATE
        for m in regex.finditer(ago)
    )
    if not negata and not any(regex.search(ago) for regex in _OBBLIGHI_FORMA_SINGOLA):
        return False
    if any(regex.search(frase) for regex in _AMMISSIONI):
        return False
    resto = _senza_esclusioni(frase)
    if any(regex.search(resto) for regex in _AMMISSIONI_FUORI_ESCLUSIONE):
        return False
    # Solo l'obbligo della forma singola: la frase non deve parlare di
    # aggregazioni (le negazioni valide sono già tolte).
    return negata or _MENZIONE_AGGREGAZIONE.search(resto) is None


# ------------------------------------------------------------ voci


def _citazione_probante(citazione: Citazione | None) -> bool:
    """Abbastanza lunga da fondare la modalità (non un frammento qualsiasi)."""
    testo = normalizza_testo(citazione.testo or "") if citazione else ""
    return (
        len(testo) >= MIN_CARATTERI_CITAZIONE_MODALITA
        and len(testo.split()) >= MIN_PAROLE_CITAZIONE_MODALITA
    )


def _modalita(
    estrazione: PartenariatoEstrazione,
    partner_min: int | None,
    partner_max: int | None,
    sezioni,
    documenti,
) -> ModalitaOut:
    avvisi: list[str] = []
    citazione, verificata = _citazione(estrazione.modalita_citazione, sezioni, documenti, avvisi)
    dichiarata = estrazione.modalita
    if (
        dichiarata != "non_determinabile"
        and citazione is not None
        and not _citazione_probante(estrazione.modalita_citazione)
    ):
        avvisi.append("Il passaggio citato è troppo breve per confermare la modalità")
    if (
        dichiarata == "non_ammesso"
        and citazione is not None
        and not esclusione_esplicita(estrazione.modalita_citazione, sezioni)
    ):
        avvisi.append("Manca un'esclusione esplicita della forma associata nel passaggio citato")
    if dichiarata == "non_ammesso" and estrazione.forme_ammesse:
        avvisi.append("«Non ammesso» contraddice le forme di aggregazione indicate")
    if dichiarata == "non_ammesso" and (partner_min or 0) > 1:
        avvisi.append("«Non ammesso» contraddice il numero minimo di partner indicato")
    if dichiarata == "obbligatorio" and partner_max == 1:
        avvisi.append("«Obbligatorio» contraddice un massimo di un solo soggetto")
    # Il conteggio che potrebbe contraddire la modalità c'è ma non si legge
    # («almeno 2»): la coerenza non si può controllare, la modalità non vale.
    if dichiarata == "non_ammesso" and not _intero(estrazione.partner_min, minimo=True)[1]:
        avvisi.append("«Non ammesso» non verificabile: numero minimo di partner non leggibile")
    if dichiarata == "obbligatorio" and not _intero(estrazione.partner_max)[1]:
        avvisi.append("«Obbligatorio» non verificabile: numero massimo di partner non leggibile")
    if dichiarata != "non_determinabile" and citazione is None:
        avvisi.append("Manca il passaggio del bando che lo stabilisce")
    effettiva = (
        dichiarata
        if dichiarata != "non_determinabile" and verificata and not avvisi
        else "non_determinabile"
    )
    return ModalitaOut(
        valore=dichiarata,
        effettiva=effettiva,
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


def _conteggi(estrazione: PartenariatoEstrazione, sezioni, documenti):
    minimo, leggibile_min = _intero(estrazione.partner_min, minimo=True)
    massimo, leggibile_max = _intero(estrazione.partner_max)
    avvisi_min: list[str] = []
    avvisi_max: list[str] = []
    if not leggibile_min:
        avvisi_min.append("Numero minimo di partner non leggibile")
    if not leggibile_max:
        avvisi_max.append("Numero massimo di partner non leggibile")
    if minimo is not None and minimo < 1:
        avvisi_min.append("Numero minimo di partner non plausibile")
    if massimo is not None and massimo < 1:
        avvisi_max.append("Numero massimo di partner non plausibile")
    if minimo is not None and massimo is not None and minimo > massimo:
        avvisi_min.append("Il minimo supera il massimo")
        avvisi_max.append("Il minimo supera il massimo")
    cit_min, ver_min = _citazione(
        estrazione.partner_min_citazione, sezioni, documenti, avvisi_min
    )
    cit_max, ver_max = _citazione(
        estrazione.partner_max_citazione, sezioni, documenti, avvisi_max
    )
    return (
        ConteggioOut(valore=minimo, stato=_stato(ver_min, avvisi_min), citazione=cit_min,
                     avvisi=avvisi_min),
        ConteggioOut(valore=massimo, stato=_stato(ver_max, avvisi_max), citazione=cit_max,
                     avvisi=avvisi_max),
    )


def _tipo_soggetto(valore: str, testo: str | None, avvisi: list[str]) -> tuple[str, str | None]:
    """(codice, testo descrittivo). "" = «altro»; un tipo ignoto diventa
    «altro» con un avviso e, se manca la descrizione, il testo del modello."""
    codice = _codice(valore, _TIPI)
    if codice is None and not _vuoto(valore):
        avvisi.append(f"Tipo di soggetto non riconosciuto: {_testo(valore, 80) or '?'}")
        testo = testo or _testo(valore, 300)
    return codice or "altro", testo


def _composizione(
    voce: ComposizioneVoce, sezioni, documenti, indice_regioni
) -> ComposizioneOut:
    avvisi: list[str] = []
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti, avvisi)
    minimo, leggibile_min = _intero(voce.minimo, minimo=True)
    massimo, leggibile_max = _intero(voce.massimo)
    if not leggibile_min:
        avvisi.append("Numero minimo non leggibile")
    if not leggibile_max:
        avvisi.append("Numero massimo non leggibile")
    if minimo is not None and minimo < 0:
        avvisi.append("Numero minimo non plausibile")
    if massimo is not None and massimo < 0:
        avvisi.append("Numero massimo non plausibile")
    if minimo is not None and massimo is not None and minimo > massimo:
        avvisi.append("Il minimo supera il massimo")
    tipo_soggetto, testo_tipo = _tipo_soggetto(
        voce.tipo_soggetto, _testo(voce.tipo_soggetto_testo, 300), avvisi
    )
    if tipo_soggetto == "altro" and not testo_tipo:
        avvisi.append("Tipo di soggetto non specificato")
    regioni, regioni_ids, scartate = mappa_regioni(voce.regioni, indice_regioni)
    if voce.regioni and indice_regioni is None:
        avvisi.append("Regioni non verificabili sul catalogo")
    for nome in scartate:
        avvisi.append(f"Regione non riconosciuta: {nome}")
    return ComposizioneOut(
        id=_testo(voce.id, 20) or "",
        tipo_soggetto=tipo_soggetto,
        tipo_soggetto_etichetta=TIPI_SOGGETTO[tipo_soggetto].etichetta,
        tipo_soggetto_testo=testo_tipo,
        beneficiari=beneficiari_per_tipo(tipo_soggetto),
        minimo=minimo,
        massimo=massimo,
        ruolo=voce.ruolo,
        regioni=regioni_ids,
        regioni_nomi=regioni,
        paesi=[p for p in (_testo(x, 80) for x in voce.paesi) if p],
        vincolo_territoriale=_testo(voce.vincolo_territoriale, 500),
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


# Una quota di partenariato ripartisce il costo o il budget del PROGETTO tra i
# soggetti del partenariato. Il passaggio citato deve nominare il partenariato
# (partner, capofila, raggruppamento…) o almeno chi sostiene la quota
# («ciascun soggetto», «da sola», «le PMI devono sostenere», «attività svolte
# da»); se non lo fa, o parla di aiuti e finanziamenti senza nominare il
# partenariato, è probabilmente un'intensità di aiuto, un limite di spesa o una
# riserva della dotazione. Non si scarta (potrebbe essere una quota scritta in
# modo insolito): resta visibile, con un avviso, da verificare.
_QUOTA_PARTENARIATO = re.compile(
    r"partner|partenariat|capofila|mandatari|mandant|compagin|raggruppament|aggregazion"
    r"|associazion[ei]\s+temporane|" + _SIGLE + r"|consorzi|contratt[oi]\s+di\s+rete"
    r"|ret[ei]\s+(?:di\s+|d')\s*impres|retist|aderent|co-?proponent|consorti(?:um|a)\b"
    r"|coordinator|lead\s+(?:applicant|beneficiar)",
    _I,
)
_QUOTA_CHI_SOSTIENE = re.compile(
    r"\b(?:ciascun[oa]?|ogni|nessun[oa]?|ognun[oa])\s+(?:(?:de[il]|degli|delle)\s+)?"
    r"(?:soggett|impres|beneficiar|partecipant|component|membr|proponent|ent[ei]\b|organism"
    r"|aziend)"
    r"|\bda\s+sol[oaie]\b|\bsost(?:iene|engono|enere|enga|engano)\b|\ba\s+carico\s+d"
    r"|\bquot[ae]\s+di\s+partecipazione|\battivit[aà]\s+(?:svolt|realizzat|condott)\w*\s+"
    r"(?:\w+mente\s+)?da"
    r"|\b(?:each|every|no|any)\s+(?:single\s+)?(?:beneficiar|participant|member|applicant"
    r"|entit)|\bborne\s+by|\bcarried\s+out\s+by",
    _I,
)
_QUOTA_AIUTO = re.compile(
    r"intensit|contribut|agevolazion|co-?finanzia|finanziament|maggiora|premialit"
    r"|punti\s+percentuali|\brisorse\b|dotazion|fondo\s+perduto|sostegno\s+a\s+terzi"
    r"|funding|co-?financ|\bgrants?\b|\baid\b|support\s+to\s+third",
    _I,
)
# Esclusioni forti: una quota di adesione, d'iscrizione o associativa, un
# cofinanziamento o un'intensità di aiuto non ripartiscono il costo del
# progetto tra i partner, anche quando la frase nomina il partenariato
# («quota di adesione richiesta alle imprese aderenti»): si valutano PRIMA di
# `_QUOTA_PARTENARIATO`. La «quota di partecipazione» resta una quota.
_QUOTA_NON_RIPARTIZIONE = re.compile(
    r"\bquot[ae]\s+(?:di\s+adesione|d['’]\s*iscrizione|di\s+iscrizione|associativ)"
    r"|\bco-?finanziament"
    # il contributo pubblico: «contributo (pubblico) (è/viene) concesso», «a
    # fondo perduto»
    r"|\bcontribut[oi]\s+(?:pubblic[oi]\s+)?"
    r"(?:(?:è|e'|viene|verr[aà]|sar[aà]|sono|vengono)\s+)?concess"
    r"|\bcontribut[oi]\s+a\s+fondo\s+perduto"
    # anche con un aggettivo in mezzo («intensità massima dell'aiuto») o con
    # l'accento scritto come apostrofo («intensita'»)
    r"|\bintensit(?:[aà]|a['’])(?:\s+\w+){0,2}?\s+d(?:i\s+|ell['’]\s*)(?:\w+\s+){0,2}?aiut",
    _I,
)
_AVVISO_NON_QUOTA = (
    "Il passaggio citato non sembra ripartire il costo del progetto tra i partner: "
    "potrebbe essere un'intensità di aiuto o un limite di spesa"
)


def _sembra_quota(frase: str) -> bool:
    """Sulla frase della citazione (`_frase_della_citazione`): una citazione
    breve («almeno il 10% dei costi») prende il soggetto dal resto della frase
    («ciascun partner…»)."""
    if _QUOTA_NON_RIPARTIZIONE.search(frase):
        return False
    if _QUOTA_PARTENARIATO.search(frase):
        return True
    return not _QUOTA_AIUTO.search(frase) and bool(_QUOTA_CHI_SOSTIENE.search(frase))


# Frazioni ammesse per le percentuali delle quote, convertite a due decimali
# (come numeric(5,2)): il bando scrive «due terzi», il codice converte. Solo
# per min/max delle quote: altrove una frazione resta non leggibile.
_FRAZIONI = {
    "1/2": 50.0, "1/3": 33.33, "2/3": 66.67, "1/4": 25.0, "3/4": 75.0, "1/5": 20.0,
    "metà": 50.0, "un terzo": 33.33, "due terzi": 66.67, "un quarto": 25.0,
    "tre quarti": 75.0, "un quinto": 20.0,
}
# Una frazione, nel valore del modello o nel testo normalizzato (minuscolo,
# apostrofi dritti): la chiave di `_FRAZIONI` si ricava dal gruppo
# `frazione`; «3/5» o «due quarti» corrispondono qui ma non sono in tabella.
# Non sono frazioni l'ordinale seguito da un soggetto o da una sequenza («un
# terzo soggetto», «un quarto partner», «un quinto lotto»; «un terzo entro…»
# sì) e la metà in senso temporale («a metà del periodo»).
_ORDINALE_DI = (
    r"soggett|part|fornitor|ent[ei]\b|organism|impres|operator|beneficiar|component|membr"
    r"|lott|fase|livell|anno|annualit|comma|punto|articol|paragraf|rata|tranche|sportell"
    r"|avvis|bando|grado|posto|classificat"
)
_META_TEMPORALE = (
    r"period|anno|annualit|mes[ei]\b|durat|semestr|trimestr|bienni|trienni|esercizi|percors"
    r"|giorn|settiman|svolgiment|realizzazion"
)
_FORME_FRAZIONE = (
    r"[1-5]\s*/\s*[1-5]"
    r"|met(?:à|a')(?!\s+(?:del|dello|della|dell'|dei|degli|delle)\s*(?:" + _META_TEMPORALE + "))"
    r"|(?:un|due|tre)\s+(?:terz|quart|quint)(?:i|o(?!\s+(?:" + _ORDINALE_DI + ")))"
)
_FINE_FRAZIONE = r"(?![\w/])"
_FRAZIONE = re.compile(
    r"(?<![\w/.,])(?:la\s+)?(?P<frazione>" + _FORME_FRAZIONE + ")" + _FINE_FRAZIONE
)
# Una percentuale scritta in cifre nel testo: «30%», «12,5 %», «10 per cento»,
# «30 (trenta) per cento».
_NUMERO_TESTO = r"\d{1,3}(?:[.,]\d{1,6})?"
_SEGNO_PERCENTUALE = r"\s*(?:\([^()\d]{1,40}\)\s*)?(?:%|per\s*cento\b)"
_PERCENTUALE_TESTO = re.compile(
    r"(?<![\w.,])(" + _NUMERO_TESTO + ")" + _SEGNO_PERCENTUALE, _I
)
# Un intervallo vale per entrambi i numeri: «tra il 20 e (ed) il 40%», «dal 20
# al 40%», «20-40%» (anche con il segno sul primo numero).
# (gli unici gruppi della regex sono i due numeri di ciascuna alternativa)
_PRIMO = r"(" + _NUMERO_TESTO + r")\s*%?"
_SECONDO = r"(" + _NUMERO_TESTO + ")" + _SEGNO_PERCENTUALE
_INTERVALLO_TESTO = re.compile(
    r"\b(?:tra|fra)\s+(?:il\s+|l')?" + _PRIMO + r"\s+ed?\s+(?:il\s+|l')?" + _SECONDO
    + r"|\bda(?:l|ll')?\s*" + _PRIMO + r"\s+a(?:l|ll')?\s*" + _SECONDO
    + r"|(?<![\w.,])" + _PRIMO + r"\s*[-–—]\s*" + _SECONDO,
    _I,
)
_TOLLERANZA_CIFRE = 0.01
_TOLLERANZA_FRAZIONI = 0.5  # 66.67 per «due terzi»: «66» non basta
_AVVISO_PERCENTUALE_ASSENTE = "La percentuale non compare nel passaggio citato"
_AVVISO_PERCENTUALE_ILLEGGIBILE = "Percentuale non leggibile"
_AVVISO_NON_RICOSTRUIBILE = "Passaggio citato non ricostruibile per intero"


def _valore_frazione(corrispondenza: re.Match) -> float | None:
    chiave = re.sub(r"\s*/\s*", "/", " ".join(corrispondenza.group("frazione").split()))
    return _FRAZIONI.get("metà" if chiave == "meta'" else chiave)


# Un articolo iniziale nel valore del modello («i due terzi», «l'80%»): il
# prompt chiede la frazione senza articolo, ma non è detto che lo rispetti.
_ARTICOLO_INIZIALE = re.compile(r"(?:il|lo|la|i)\s+|l'\s*")


def _percentuale(valore: Any, *, minimo: bool = False) -> tuple[float | None, bool]:
    """Come `_numero`, ma accetta anche le frazioni di `_FRAZIONI` («due
    terzi», «2/3» → 66.67) e un articolo iniziale («i due terzi», «il
    30%»): solo per le percentuali delle quote."""
    if isinstance(valore, str) and len(valore) <= _MAX_MARCATORE:
        testo = normalizza_testo(valore)
        articolo = _ARTICOLO_INIZIALE.match(testo)
        if articolo:
            testo = testo[articolo.end() :]
        corrispondenza = _FRAZIONE.fullmatch(testo)
        if corrispondenza:
            percentuale = _valore_frazione(corrispondenza)
            if percentuale is not None:
                return percentuale, True
        if articolo and testo:
            return _numero(testo, minimo=minimo)
    return _numero(valore, minimo=minimo)


def _percentuale_nel_testo(valore: float, frase: str) -> bool:
    """True se `valore` compare nella frase: in cifre seguite da «%» o «per
    cento» (tolleranza 0,01) o come frazione di `_FRAZIONI` (tolleranza 0,5).
    Un «0.3» scritto al posto di 30 o un numero inventato con una citazione
    vera non passano."""
    cifre = [m.group(1) for m in _PERCENTUALE_TESTO.finditer(frase)]
    for intervallo in _INTERVALLO_TESTO.finditer(frase):
        cifre += [numero for numero in intervallo.groups() if numero]
    for numero in cifre:
        if abs(valore - float(numero.replace(",", "."))) <= _TOLLERANZA_CIFRE + 1e-9:
            return True
    for corrispondenza in _FRAZIONE.finditer(frase):
        frazione = _valore_frazione(corrispondenza)
        if frazione is not None and abs(valore - frazione) <= _TOLLERANZA_FRAZIONI + 1e-9:
            return True
    return False


# Con ambito «per_partner» e «capofila» la quota vale per ogni partner o per il
# capofila: un ruolo scritto al posto della categoria («qualsiasi»,
# «capofila») non è un tipo di soggetto ignoto ma nessuna categoria.
_CATEGORIA_DEL_RUOLO = {
    "per_partner": frozenset({
        "qualsiasi", "tutti", "partner", "tutti_i_partner", "ciascun_partner", "ogni_partner",
        "partecipante", "partecipanti", "ciascun_partecipante",
    }),
    "capofila": frozenset({"capofila", "mandataria", "mandatario", "capogruppo"}),
}


def _quota(voce, sezioni, documenti) -> QuotaOut:
    avvisi: list[str] = []
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti, avvisi)
    # Senza citazione nessun giudizio sul testo: la voce è già da verificare.
    frase = None
    if voce.citazione.testo.strip():
        frase, ricostruita = _frase_della_citazione(voce.citazione, sezioni)
        # Citazione ritrovata ma frase non ricostruita (i frammenti di
        # un'ellissi in frasi diverse): esclusioni e percentuale si
        # controllerebbero solo sul frammento, che può saltare proprio
        # l'esclusione o il soggetto.
        if not ricostruita and citazione is not None and citazione.verificata:
            avvisi.append(_AVVISO_NON_RICOSTRUIBILE)
    if frase is not None and not _sembra_quota(frase):
        avvisi.append(_AVVISO_NON_QUOTA)
    minimo, leggibile_min = _percentuale(voce.min_percentuale, minimo=True)
    massimo, leggibile_max = _percentuale(voce.max_percentuale)
    if not (leggibile_min and leggibile_max):
        avvisi.append(_AVVISO_PERCENTUALE_ILLEGGIBILE)
    for valore in (minimo, massimo):
        if valore is not None and (not _finito(valore) or not 0 <= valore <= 100):
            avvisi.append("Percentuale fuori dall'intervallo 0-100")
            break
    if _finito(minimo) and _finito(massimo) and minimo > massimo:
        avvisi.append("La percentuale minima supera la massima")
    if minimo is None and massimo is None and leggibile_min and leggibile_max:
        avvisi.append("Quota senza percentuali")
    if frase is not None and any(
        _finito(valore) and not _percentuale_nel_testo(valore, frase)
        for valore in (minimo, massimo)
    ):
        avvisi.append(_AVVISO_PERCENTUALE_ASSENTE)
    categoria = None
    if not _vuoto(voce.categoria) and _chiave(voce.categoria) not in _CATEGORIA_DEL_RUOLO.get(
        voce.ambito, ()
    ):
        categoria, _ = _tipo_soggetto(voce.categoria, None, avvisi)
    if voce.ambito == "per_categoria" and categoria is None:
        avvisi.append("Quota per categoria senza categoria")
    return QuotaOut(
        id=_testo(voce.id, 20) or "",
        ambito=voce.ambito,
        categoria=categoria,
        min_percentuale=minimo if _finito(minimo) else None,
        max_percentuale=massimo if _finito(massimo) else None,
        base_calcolo=_codice_o_residuo(
            voce.base_calcolo, _BASI, "non_indicata", "Base di calcolo non riconosciuta", avvisi
        ),
        effetto_violazione=_codice_o_residuo(
            voce.effetto_violazione, _EFFETTI, "non_indicato",
            "Effetto della violazione non riconosciuto", avvisi,
        ),
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


def _momento(valore: str, avvisi: list[str]) -> str:
    return _codice_o_residuo(valore, _MOMENTI, "non_indicato", "Momento non riconosciuto", avvisi)


def _vincolo(voce, sezioni, documenti) -> VincoloOut:
    avvisi: list[str] = []
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti, avvisi)
    tipo = _codice_o_residuo(voce.tipo, _TIPI_VINCOLO, "altro", "Tipo di vincolo non riconosciuto",
                             avvisi)
    descrizione = _testo(voce.descrizione) or ""
    if tipo == "altro" and not descrizione:
        avvisi.append("Vincolo non descritto")
    parametro, leggibile = _numero(voce.parametro)
    if not leggibile:
        avvisi.append("Parametro non leggibile")
    if _vuoto(voce.tipo) and not _vuoto(voce.parametro):
        # Il parametro conta paesi o giorni secondo il tipo: senza tipo non
        # si sa che cosa misuri.
        avvisi.append("Tipo di vincolo non indicato: il parametro non si può interpretare")
    if parametro is not None:
        if not _finito(parametro) or parametro < 0:
            avvisi.append("Parametro non plausibile")
        elif tipo == "paesi_distinti" and parametro < 2:
            avvisi.append("Numero di paesi non plausibile")
    return VincoloOut(
        id=_testo(voce.id, 20) or "",
        tipo=tipo,
        descrizione=descrizione,
        parametro=parametro if _finito(parametro) else None,
        momento=_momento(voce.momento, avvisi),
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


def _tupla_quota(quota: QuotaOut) -> tuple:
    return quota.ambito, quota.categoria, quota.min_percentuale, quota.max_percentuale


# Base di calcolo ed effetto «non indicati» (o non riconosciuti): compatibili
# con qualunque valore.
_NON_INDICATI = frozenset({"non_indicata", "non_indicato"})


def _stessa_regola(a: QuotaOut, b: QuotaOut) -> bool:
    """Stessa tupla (`_tupla_quota`) e base di calcolo ed effetto compatibili:
    uguali, oppure non indicati in una delle due. Con la stessa soglia, una
    base o un effetto indicati e diversi (inammissibilità contro perdita della
    maggiorazione) sono due regole."""
    return _tupla_quota(a) == _tupla_quota(b) and all(
        x == y or x in _NON_INDICATI or y in _NON_INDICATI
        for x, y in ((a.base_calcolo, b.base_calcolo),
                     (a.effetto_violazione, b.effetto_violazione))
    )


def _preferenza(quota: QuotaOut) -> tuple[bool, int]:
    """Tra due voci della stessa regola: prima la verificata, poi quella con
    più campi indicati (base di calcolo, effetto)."""
    indicati = sum(
        valore not in _NON_INDICATI for valore in (quota.base_calcolo, quota.effetto_violazione)
    )
    return quota.stato == "verificata", indicati


def _confrontabile(quota: QuotaOut) -> bool:
    """True se la tupla della quota la descrive per intero: almeno una
    percentuale letta, nessuna percentuale illeggibile, una categoria nota
    quando c'è (e sempre per «per_categoria»). Due quote illeggibili o senza
    percentuali hanno la stessa tupla ma possono essere regole diverse: non si
    confrontano, restano entrambe."""
    return (
        (quota.min_percentuale is not None or quota.max_percentuale is not None)
        and _AVVISO_PERCENTUALE_ILLEGGIBILE not in quota.avvisi
        and quota.categoria != "altro"
        and not (quota.ambito == "per_categoria" and quota.categoria is None)
    )


# Avvisi globali per l'utente (senza gli id interni delle voci), una volta sola.
_AVVISO_QUOTA_RIPETUTA = "Una quota compariva in più documenti: è mostrata una volta sola."
_AVVISO_QUOTA_DA_VINCOLO = (
    "Una quota è stata ricavata da un vincolo del bando (limite per singolo partner)."
)
# Sulla voce recuperata: la rende da verificare (mai preselezionata).
_AVVISO_RICAVATA = "Ricavata da un vincolo: da confermare"


def _senza_quote_ripetute(quote: list[QuotaOut]) -> tuple[list[QuotaOut], list[str]]:
    """(quote, avvisi globali): le quote confrontabili (`_confrontabile`)
    della stessa regola si raggruppano, e di ogni gruppo resta una voce sola,
    al posto della prima: la preferita (`_preferenza`: la verificata, poi la
    più completa), a parità la prima. È lo stesso passaggio ripetuto in più
    documenti (un avviso e il suo testo consolidato): due voci uguali nella
    call sarebbero un doppione. Una quota entra in un gruppo solo se è
    `_stessa_regola` con TUTTI i membri: la compatibilità con «non indicato»
    non è transitiva (una base non indicata è compatibile sia con «budget del
    partner» sia con «contributo», che però sono regole diverse e restano
    entrambe). Le quote non confrontabili restano tutte."""
    gruppi: list[list[QuotaOut]] = []
    for quota in quote:
        gruppo = None
        if _confrontabile(quota):
            gruppo = next(
                (g for g in gruppi
                 if _confrontabile(g[0]) and all(_stessa_regola(m, quota) for m in g)),
                None,
            )
        if gruppo is None:
            gruppi.append([quota])
        else:
            gruppo.append(quota)
    # max restituisce il primo a parità di preferenza
    tenute = [max(gruppo, key=_preferenza) for gruppo in gruppi]
    ripetute = len(tenute) < len(quote)
    return tenute, [_AVVISO_QUOTA_RIPETUTA] if ripetute else []


def _quasi_uguali(a: float | None, b: float | None) -> bool:
    """Stessa percentuale a meno della tolleranza della metrica (0,5 punti:
    66.67 e 66.66 sono la stessa quota); None solo con None."""
    if a is None or b is None:
        return a is b
    return abs(a - b) <= _TOLLERANZA_FRAZIONI + 1e-9


def _gia_presente(quota: QuotaOut, quote: list[QuotaOut]) -> bool:
    return any(
        altra.ambito == quota.ambito
        and altra.categoria == quota.categoria
        and _quasi_uguali(altra.min_percentuale, quota.min_percentuale)
        and _quasi_uguali(altra.max_percentuale, quota.max_percentuale)
        for altra in quote
    )


def _id_libero(base: str, usati: set[str]) -> str:
    """`base` tagliato a 20 caratteri (come ogni id in uscita), con un suffisso
    «-2», «-3»… se è già usato."""
    candidato = base[:20].rstrip()
    numero = 2
    while candidato in usati:
        suffisso = f"-{numero}"
        candidato = base[: 20 - len(suffisso)].rstrip() + suffisso
        numero += 1
    return candidato


# Una quota scritta come vincolo: il modello a volte mette «nessuna impresa
# beneficiaria sostiene da sola più di due terzi del totale delle spese» tra i
# vincoli di tipo «altro». Si recupera come quota per partner (massimo) SOLO in
# questa forma stretta, in una frase che parla di spese o costi, senza parole
# di aiuto né esclusioni forti: la frazione o la percentuale da sole sono
# rumorose («almeno i due terzi dei soci», «aumentata da un terzo alla metà»).
# Quantificatori limitati: tempo lineare anche su frasi ostili senza punti.
_VALORE_QUOTA = (
    _NUMERO_TESTO + _SEGNO_PERCENTUALE + "|(?:" + _FORME_FRAZIONE + ")" + _FINE_FRAZIONE
)
# Tra «nessun…» e «sostiene» nessun altro soggetto distributivo: «nessuna
# impresa partecipa a più progetti e ciascun partner sostiene più del 10%» è
# un minimo di un'altra proposizione, non un massimo.
_QUOTA_DA_VINCOLO = re.compile(
    r"\bnessun[oa]?\s+(?:impres[ae]|partner|soggett[oi]|beneficiari[oa]?|partecipant[ei]"
    r"|component[ei]|membr[oi])\b"
    r"(?:(?!\b(?:ciascun[oa]?|ogni|ognun[oa]|tutt[ie])\b)[^.;]){0,120}?"
    r"\bsost(?:iene|enere|enga)\s+"
    r"(?:da\s+sol[oa]\s+)?(?:pi(?:ù|u'?)\s+d(?:i|el|ella|ei)|oltre(?:\s+(?:il|la|i))?)\s+"
    r"(?P<valore>" + _VALORE_QUOTA + r")",
    _I,
)
_SPESE_O_COSTI = re.compile(r"\b(?:spes[ae]|cost[oi]|budget)\b", _I)
# Dopo il valore, la base deve essere il costo o le spese COMPLESSIVE del
# progetto («del totale delle spese», «dei costi ammissibili», «del budget»),
# non una singola voce di spesa («dei costi per consulenze esterne», «delle
# spese di personale»): quello è un limite di spesa, non una quota.
_CODA_BASE = 200
_BASE_COMPLESSIVA = re.compile(
    r"\b(?:total\w*|complessiv\w*|ammissibil\w*|progett\w*|budget)\b", _I
)
_VOCE_DI_SPESA = re.compile(
    r"\b(?:consulenz|personale|subappalt|subaffid|attrezzatur|macchinar|impiant|strument"
    r"|progettazion|viagg|trasfert|missioni|material|brevett|licenz|promozion|pubblicit"
    r"|formazion|general[ie]\b|indirett|forfettar|investiment|opere\b|lavori\b|immobil"
    r"|software|servizi|forniture)",
    _I,
)
# Un'eccezione («nessun partner, salvo il capofila, …») o un obbligo negato
# («nessun partner è tenuto a sostenere più di…», che non è un tetto) tra
# «nessun…» e il valore: niente recupero. L'eccezione conta anche dopo il
# valore («…, tranne il capofila»).
_ECCEZIONE = re.compile(r"\b(?:ad\s+eccezione|salv[oaie]\b|tranne|eccett[oaui]\b)", _I)
_OBBLIGO_NEGATO = re.compile(
    r"\b(?:(?:è|e'|sar[aà]|sono|saranno)\s+tenut[oaie]\b|obbligat)", _I
)


def _valore_scritto(testo: str) -> float | None:
    """Il valore di `_VALORE_QUOTA`: «30%», «10 per cento», «due terzi»."""
    cifre = _PERCENTUALE_TESTO.fullmatch(testo)
    if cifre:
        return float(cifre.group(1).replace(",", "."))
    frazione = _FRAZIONE.fullmatch(testo)
    return _valore_frazione(frazione) if frazione else None


def _quote_dai_vincoli(
    estrazione: PartenariatoEstrazione,
    vincoli: list[VincoloOut],
    quote: list[QuotaOut],
    sezioni: dict[str, str],
    documenti: dict[int, dict],
) -> tuple[list[QuotaOut], list[str]]:
    """(quote recuperate, avvisi globali) dai vincoli di tipo «altro» con
    citazione verificata su un documento ufficiale (la verifica della voce di
    `_citazione`: una citazione della scheda del catalogo non basta) e frase
    nella forma di `_QUOTA_DA_VINCOLO`, sul costo complessivo del progetto (non
    su una singola voce di spesa). La quota (per_partner, nessuna categoria,
    massimo) nasce con la citazione del vincolo e passa da `_quota` come le
    altre, ma resta SEMPRE da verificare (`_AVVISO_RICAVATA`): è il codice a
    leggerla nel testo, non il modello, quindi si mostra e il creatore la può
    confermare come «modificata», ma non si preseleziona mai. Il vincolo resta
    com'è. Nessun recupero se c'è già una quota uguale a meno di 0,5 punti.
    L'id («Q-<vincolo>») è unico anche dopo il taglio a 20 caratteri. In più un
    avviso globale, una volta sola."""
    usati = {quota.id for quota in quote}
    recuperate: list[QuotaOut] = []
    for numero, (voce, vincolo) in enumerate(zip(estrazione.vincoli, vincoli, strict=True), 1):
        if vincolo.tipo != "altro" or not voce.citazione.testo.strip():
            continue
        if not _citazione(voce.citazione, sezioni, documenti, [])[1]:
            continue
        # Solo sulla frase ricostruita dal documento: con un'ellissi i
        # frammenti possono unire frasi diverse (la soglia di una voce di
        # spesa e la base complessiva di un'altra frase).
        frase, ricostruita = _frase_della_citazione(voce.citazione, sezioni)
        if not ricostruita:
            continue
        forma = _QUOTA_DA_VINCOLO.search(frase)
        if (
            forma is None
            or not _SPESE_O_COSTI.search(frase)
            or _QUOTA_AIUTO.search(frase)
            or _QUOTA_NON_RIPARTIZIONE.search(frase)
        ):
            continue
        base = frase[forma.end() : forma.end() + _CODA_BASE]
        if not _BASE_COMPLESSIVA.search(base) or _VOCE_DI_SPESA.search(base):
            continue
        prima_del_valore = frase[forma.start() : forma.start("valore")]
        if (
            _ECCEZIONE.search(prima_del_valore)
            or _OBBLIGO_NEGATO.search(prima_del_valore)
            or _ECCEZIONE.search(base)
        ):
            continue
        valore = _valore_scritto(forma.group("valore"))
        if valore is None:
            continue
        quota = _quota(
            QuotaVoce(
                id=_id_libero(f"Q-{vincolo.id or f'V{numero}'}", usati),
                ambito="per_partner", categoria="",
                # due decimali: «33.333» per `_numero` sarebbe ambiguo (migliaia)
                min_percentuale="", max_percentuale=f"{round(valore, 2):g}",
                base_calcolo="", effetto_violazione="", citazione=voce.citazione,
            ),
            sezioni,
            documenti,
        )
        if _gia_presente(quota, quote + recuperate):
            continue
        usati.add(quota.id)
        recuperate.append(
            quota.model_copy(
                update={"avvisi": [*quota.avvisi, _AVVISO_RICAVATA], "stato": "da_verificare"}
            )
        )
    return recuperate, [_AVVISO_QUOTA_DA_VINCOLO] if recuperate else []


def _regola_finanziaria(
    voce: RegolaFinanziariaEstratta, sezioni, documenti
) -> tuple[RegolaFinanziariaOut | None, str | None]:
    """(regola, None) oppure (None, avviso globale) se una variabile è ignota:
    il contratto WP1 non può rappresentarla, e una regola con un termine
    sbagliato sarebbe peggio di nessuna regola."""
    id_ = _testo(voce.id, 20) or ""
    descrizione = _testo(voce.descrizione) or ""
    variabili: dict[str, str | None] = {}
    ignote: list[str] = []
    for campo in ("numeratore", "denominatore", "soglia_variabile"):
        valore = getattr(voce, campo)
        variabili[campo] = _codice(valore, _VARIABILI)
        if variabili[campo] is None and (campo == "numeratore" or not _vuoto(valore)):
            ignote.append(_testo(valore, 80) or "(vuota)")
    if ignote:
        nome = f"Regola finanziaria {id_}" if id_ else "Regola finanziaria"
        avviso = f"{nome} non riconosciuta (variabile {', '.join(ignote)})"
        return None, f"{avviso}: {descrizione}" if descrizione else avviso
    avvisi: list[str] = []
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti, avvisi)
    unita = _codice(voce.unita, _UNITA)
    if unita is None:
        avvisi.append(f"Unità non riconosciuta: {_testo(voce.unita, 40) or '(vuota)'}")
        # La più plausibile, come la calcola `bilanci_indicatori.valida_regola`.
        if variabili["denominatore"] is not None:
            unita = "rapporto"
        else:
            unita = "numero" if variabili["numeratore"] in _CONTEGGI else "euro"
    # «100.000» per `_leggi_decimale` (WP1) vale 100: come per gli altri
    # numeri, un solo separatore seguito da tre cifre è ambiguo e la regola
    # resta da verificare (il valore si mostra com'è).
    for campo, nome in (
        ("soglia", "Soglia ambigua"), ("soglia_coefficiente", "Coefficiente ambiguo")
    ):
        valore = getattr(voce, campo)
        if not _vuoto(valore) and _AMBIGUO.fullmatch(valore.strip()):
            avvisi.append(f"{nome} (separatore delle migliaia?): {_testo(valore, 40) or '?'}")
    campi = {
        "id": id_,
        "descrizione": descrizione,
        "ambito": voce.ambito,
        "numeratore": variabili["numeratore"],
        "denominatore": variabili["denominatore"],
        "operatore": voce.operatore,
        "soglia": None if _vuoto(voce.soglia) else voce.soglia,
        "soglia_variabile": variabili["soglia_variabile"],
        "soglia_coefficiente": (
            None if _vuoto(voce.soglia_coefficiente) else voce.soglia_coefficiente
        ),
        "unita": unita,
    }
    avvisi += [
        f"Regola non coerente: {errore}" for errore in valida_regola(RegolaFinanziaria(**campi))
    ]
    return (
        RegolaFinanziariaOut(
            **campi, stato=_stato(verificata, avvisi), citazione=citazione, avvisi=avvisi
        ),
        None,
    )


# ------------------------------------------------------------ ingresso


def post_elabora(
    estrazione: PartenariatoEstrazione | dict,
    sezioni: dict[str, str],
    fonti: Any,
    lookups: Any,
) -> dict:
    """Regole post-elaborate (forma `RegolePartenariatoOut`, JSON) dall'output
    del modello. `sezioni`: indice→testo ESATTAMENTE come inviato; `fonti`:
    le voci di `fonti_usate` (n, etichetta, url); `lookups`: le lookup del
    catalogo (serve `regioni`), None se non disponibili."""
    if not isinstance(estrazione, PartenariatoEstrazione):
        estrazione = PartenariatoEstrazione.model_validate(estrazione)
    documenti = _documenti(fonti)
    indice_regioni = _indice_regioni(lookups)

    partner_min, partner_max = _conteggi(estrazione, sezioni, documenti)
    modalita = _modalita(
        estrazione, partner_min.valore, partner_max.valore, sezioni, documenti
    )

    avvisi_cost: list[str] = []
    cit_cost, ver_cost = _citazione(
        estrazione.costituzione_citazione, sezioni, documenti, avvisi_cost
    )
    valore_cost = _codice_o_residuo(
        estrazione.costituzione, _COSTITUZIONE, "non_indicato",
        "Costituzione non riconosciuta", avvisi_cost,
    )
    if valore_cost != "non_indicato" and cit_cost is None:
        avvisi_cost.append("Manca il passaggio del bando che lo stabilisce")
    costituzione = CostituzioneOut(
        valore=valore_cost,
        stato=_stato(ver_cost, avvisi_cost),
        citazione=cit_cost,
        avvisi=avvisi_cost,
    )

    forme: list[FormaAmmessaOut] = []
    for voce in estrazione.forme_ammesse:
        avvisi: list[str] = []
        citazione, verificata = _citazione(voce.citazione, sezioni, documenti, avvisi)
        note = _testo(voce.note, 500)
        codice = _codice(voce.forma, _FORME)
        if codice is None:
            # Forma ignota (o vuota): «altra», descritta dal testo del modello.
            if not _vuoto(voce.forma):
                avvisi.append(f"Forma non riconosciuta: {_testo(voce.forma, 80) or '?'}")
                note = note or _testo(voce.forma, 500)
            codice = "altra"
        if codice == "altra" and not note:
            avvisi.append("Forma non descritta")
        forme.append(
            FormaAmmessaOut(
                forma=codice,
                etichetta=FORME[codice].etichetta,
                note=note,
                stato=_stato(verificata, avvisi),
                citazione=citazione,
                avvisi=avvisi,
            )
        )

    documenti_richiesti: list[DocumentoRichiestoOut] = []
    for voce in estrazione.documenti_richiesti:
        avvisi = []
        citazione, verificata = _citazione(voce.citazione, sezioni, documenti, avvisi)
        tipo = _codice_o_residuo(
            voce.tipo, _DOCUMENTI, "altro", "Tipo di documento non riconosciuto", avvisi
        )
        descrizione = _testo(voce.descrizione) or ""
        if tipo == "altro" and not descrizione:
            avvisi.append("Documento non descritto")
        momento = _momento(voce.momento, avvisi)
        documenti_richiesti.append(
            DocumentoRichiestoOut(
                id=_testo(voce.id, 20) or "",
                tipo=tipo,
                descrizione=descrizione,
                momento=momento,
                stato=_stato(verificata, avvisi),
                citazione=citazione,
                avvisi=avvisi,
            )
        )

    regole_finanziarie: list[RegolaFinanziariaOut] = []
    avvisi_regole: list[str] = []
    for voce in estrazione.regole_finanziarie:
        regola, avviso = _regola_finanziaria(voce, sezioni, documenti)
        if regola is not None:
            regole_finanziarie.append(regola)
        if avviso:
            avvisi_regole.append(_testo(avviso, 500) or "")

    vincoli = [_vincolo(voce, sezioni, documenti) for voce in estrazione.vincoli]
    quote, avvisi_quote = _senza_quote_ripetute(
        [_quota(voce, sezioni, documenti) for voce in estrazione.quote]
    )
    recuperate, avvisi_recupero = _quote_dai_vincoli(
        estrazione, vincoli, quote, sezioni, documenti
    )

    regole = RegolePartenariatoOut(
        modalita=modalita,
        modalita_effettiva=modalita.effettiva,
        forme_ammesse=forme,
        costituzione=costituzione,
        partner_min=partner_min,
        partner_max=partner_max,
        conteggio_note=_testo(estrazione.conteggio_note, 1000),
        composizione=[
            _composizione(voce, sezioni, documenti, indice_regioni)
            for voce in estrazione.composizione
        ],
        quote=quote + recuperate,
        vincoli=vincoli,
        regole_finanziarie=regole_finanziarie,
        documenti_richiesti=documenti_richiesti,
        fonti_insufficienti=bool(estrazione.fonti_insufficienti),
        note=_testo(estrazione.note),
        # Le incoerenze della modalità valgono per tutto il risultato; le
        # regole finanziarie non rappresentabili e le voci che la convalida
        # tollerante ha scartato o declassato restano visibili solo qui, come
        # le quote ripetute tolte e quelle ricavate dai vincoli.
        avvisi=[a for a in modalita.avvisi if a.startswith("«")] + avvisi_regole + [
            _testo(a, 500) or "" for a in avvisi_convalida(estrazione)
        ] + avvisi_quote + avvisi_recupero,
    )
    # Ultima rete: nessun rimando ai domini esclusi, in nessun campo.
    return scrub_menzioni(regole.model_dump(mode="json"))
