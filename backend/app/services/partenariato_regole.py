"""Post-elaborazione deterministica delle regole di partenariato (WP3, PURO).

Il modello estrae, il codice decide che cosa vale. Per ogni voce:
- la citazione si verifica sul testo inviato (`citazioni.verifica_citazione`,
  pagine adiacenti ed ellissi comprese); non ritrovata → `da_verificare`;
- i controlli di coerenza e di RANGE (partner_min ≤ partner_max, percentuali
  0–100, minimo ≤ massimo, «non ammesso» con forme ammesse, regole
  finanziarie con `bilanci_indicatori.valida_regola`) declassano la voce a
  `da_verificare` con un avviso: MAI un'eccezione, la chiamata è già pagata;
- `modalita_effettiva` = modalità dichiarata SOLO se la sua citazione è
  verificata e coerente, altrimenti `non_determinabile` (è ciò che usa il
  filtro «Ammette partenariato»);
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
- numeri: cifre con punto (o virgola) decimale; una stringa non leggibile
  vale assente con un avviso (`da_verificare`), mai un'eccezione.

Le voci `da_verificare` restano visibili all'utente (con il badge) ma sono
escluse dagli usi deterministici del modulo.
"""

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
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
from app.services.citazioni import normalizza_sezione, normalizza_testo, verifica_citazione
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


def _numero(valore: Any) -> tuple[float | None, bool]:
    """(numero, leggibile): "" → (None, True), cioè assente e non un errore."""
    if _vuoto(valore):
        return None, True
    testo = valore.strip().removesuffix("%").strip()
    if not _NUMERO.fullmatch(testo) or _AMBIGUO.fullmatch(testo):
        return None, False
    return float(testo.replace(",", ".")), True


def _intero(valore: Any) -> tuple[int | None, bool]:
    numero, leggibile = _numero(valore)
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


def _citazione(
    citazione: Citazione | None, sezioni: dict[str, str], documenti: dict[int, dict]
):
    """(CitazioneRegolaOut | None, verificata). Sezione e testo entrambi ""
    = citazione assente."""
    if citazione is None:
        return None, False
    grezza = citazione.sezione if isinstance(citazione.sezione, str) else ""
    testo = citazione.testo if isinstance(citazione.testo, str) else ""
    if not grezza.strip() and not testo.strip():
        return None, False
    sezione = normalizza_sezione(grezza) or grezza.strip()[:40]
    verificata = verifica_citazione(grezza, testo, sezioni)
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
    else:
        fonte = "Fonte non riconosciuta"
    return (
        CitazioneRegolaOut(
            sezione=sezione[:40],
            fonte_etichetta=fonte,
            testo=_testo(testo) or "",
            verificata=verificata,
            url_documento=url,
            pagina=pagina,
        ),
        verificata,
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
    citazione, verificata = _citazione(estrazione.modalita_citazione, sezioni, documenti)
    avvisi: list[str] = []
    dichiarata = estrazione.modalita
    if (
        dichiarata != "non_determinabile"
        and citazione is not None
        and not _citazione_probante(estrazione.modalita_citazione)
    ):
        avvisi.append("Il passaggio citato è troppo breve per confermare la modalità")
    if dichiarata == "non_ammesso" and estrazione.forme_ammesse:
        avvisi.append("«Non ammesso» contraddice le forme di aggregazione indicate")
    if dichiarata == "non_ammesso" and (partner_min or 0) > 1:
        avvisi.append("«Non ammesso» contraddice il numero minimo di partner indicato")
    if dichiarata == "obbligatorio" and partner_max == 1:
        avvisi.append("«Obbligatorio» contraddice un massimo di un solo soggetto")
    # Il conteggio che potrebbe contraddire la modalità c'è ma non si legge
    # («almeno 2»): la coerenza non si può controllare, la modalità non vale.
    if dichiarata == "non_ammesso" and not _intero(estrazione.partner_min)[1]:
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
    minimo, leggibile_min = _intero(estrazione.partner_min)
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
    cit_min, ver_min = _citazione(estrazione.partner_min_citazione, sezioni, documenti)
    cit_max, ver_max = _citazione(estrazione.partner_max_citazione, sezioni, documenti)
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
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
    avvisi: list[str] = []
    minimo, leggibile_min = _intero(voce.minimo)
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


def _quota(voce, sezioni, documenti) -> QuotaOut:
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
    avvisi: list[str] = []
    minimo, leggibile_min = _numero(voce.min_percentuale)
    massimo, leggibile_max = _numero(voce.max_percentuale)
    if not (leggibile_min and leggibile_max):
        avvisi.append("Percentuale non leggibile")
    for valore in (minimo, massimo):
        if valore is not None and (not _finito(valore) or not 0 <= valore <= 100):
            avvisi.append("Percentuale fuori dall'intervallo 0-100")
            break
    if _finito(minimo) and _finito(massimo) and minimo > massimo:
        avvisi.append("La percentuale minima supera la massima")
    if minimo is None and massimo is None and leggibile_min and leggibile_max:
        avvisi.append("Quota senza percentuali")
    categoria = None
    if not _vuoto(voce.categoria):
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
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
    avvisi: list[str] = []
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
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
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

    cit_cost, ver_cost = _citazione(estrazione.costituzione_citazione, sezioni, documenti)
    avvisi_cost: list[str] = []
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
        citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
        note = _testo(voce.note, 500)
        avvisi: list[str] = []
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
        citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
        avvisi = []
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
        quote=[_quota(voce, sezioni, documenti) for voce in estrazione.quote],
        vincoli=[_vincolo(voce, sezioni, documenti) for voce in estrazione.vincoli],
        regole_finanziarie=regole_finanziarie,
        documenti_richiesti=documenti_richiesti,
        fonti_insufficienti=bool(estrazione.fonti_insufficienti),
        note=_testo(estrazione.note),
        # Le incoerenze della modalità valgono per tutto il risultato; le
        # regole finanziarie non rappresentabili e le voci che la convalida
        # tollerante ha scartato o declassato restano visibili solo qui.
        avvisi=[a for a in modalita.avvisi if a.startswith("«")] + avvisi_regole + [
            _testo(a, 500) or "" for a in avvisi_convalida(estrazione)
        ],
    )
    # Ultima rete: nessun rimando ai domini esclusi, in nessun campo.
    return scrub_menzioni(regole.model_dump(mode="json"))
