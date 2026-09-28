"""Bilancio ufficiale (WP2): allegati del bilancio ottico e istanza XBRL.

Modulo PURO: nessun I/O, nessuna rete, nessun DB. Il servizio
(`bilancio_ufficiale_service`) scarica lo ZIP, poi chiama in ordine
`estrai_allegati` → `parse_xbrl` (in `asyncio.to_thread`) → `righe_da_istanza`
e registra le righe come fonte `xbrl`.

Input NON fidato, quindi difese esplicite:
- ZIP letto solo in memoria, con tetti su dimensione, numero di membri, somma
  decompressa e rapporto di compressione; ogni membro si legge con
  `read(massimo + 1)`, così una `file_size` falsa non fa leggere di più.
- XML con la sola stdlib: il target rifiuta qualunque DOCTYPE (niente DTD,
  XXE, billion laughs). Il prologo si passa al parser a pezzi di pochi byte:
  un'eccezione nel target NON ferma expat sul blocco già passato, quindi un
  DOCTYPE in un blocco grande verrebbe comunque espanso da expat. Così
  expat non vede mai le dichiarazioni e i riferimenti che seguono il DOCTYPE,
  qualunque sia la sua versione (in locale 2.5.0, in produzione 2.8.x).
- Deadline sul parsing, controllata tra un blocco e l'altro e in `start()`.

Tassonomia (PCI 2018-11-04, stessi concept nelle versioni 2015 e 2017):
ordinario, abbreviato, micro e consolidato usano gli STESSI concept nel
namespace `itcc-ci`; la forma si ricava solo dall'entry point in `schemaRef`.
Lo stesso concept d'istante compare anche a inizio esercizio (rendiconto
finanziario e nota integrativa): si prende solo l'istante uguale alla
`endDate` dell'esercizio. Le tuple della nota integrativa non si leggono:
contano solo i figli diretti della radice. Importi sempre `Decimal`.
"""

import codecs
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.services.bilanci_mapping import (
    _CAMPI_NON_NEGATIVI,
    _LIMITE_DEFAULT,
    _LIMITI,
    ANNO_MAX,
    ANNO_MIN,
    CAMPI_BILANCIO,
    CAMPI_CORE,
    RigaFonte,
)

logger = logging.getLogger("bandofit.bilanci")

# ------------------------------------------------------------------ tetti

MAX_ZIP_BYTES = 20_000_000
MAX_MEMBRI = 10
MAX_PDF_BYTES = 8_388_608  # = CHECK di company_bilancio_documenti (pdf)
MAX_XBRL_BYTES = 10_485_760  # = CHECK di company_bilancio_documenti (xbrl)
MAX_TOTALE_DECOMPRESSO = 40_000_000
MAX_RAPPORTO = 100
XBRL_DEADLINE_SECONDS = 5.0
CHUNK = 65_536

# Prologo (tutto ciò che precede l'apertura della radice) passato a expat a
# pezzi minimi, con un tetto: un'istanza vera ha qui un paio di KB.
_PEZZO_PROLOGO = 16
_MAX_PROLOGO = 32_768
# Sotto questa dimensione dichiarata il rapporto di compressione non conta:
# un file piccolo e molto comprimibile non è una bomba.
_SOGLIA_RAPPORTO = CHUNK
_MAX_TESTO = 256  # caratteri conservati per fatto, periodo o misura
_MAX_NOME = 120
_PREFISSO_SNIFF = 1024

# Controllo TEBENI XZ: durata massima di un esercizio, in giorni.
_XZ_DUE_ANNUALITA = 425
_XZ_UNA_ANNUALITA = 731
_MAX_ANNUALITA = 2

FormaXbrl = Literal["ordinario", "abbreviato", "micro", "consolidato", "ignoto"]

# ------------------------------------------------------------------ errori


class ErroreContenutoBilancio(Exception):
    """Contenuto del bilancio non utilizzabile. Non si ritenta: la richiesta
    si chiude `completata` con `xbrl_esito = esito`."""

    esito = "non_valido"

    def __init__(self, codice: str, esito: str | None = None) -> None:
        super().__init__(codice)
        self.codice = codice
        if esito is not None:
            self.esito = esito


class AllegatiNonValidi(ErroreContenutoBilancio):
    """Archivio illeggibile o sospetto (bomba, troppi membri, troppo grande)."""


class XbrlNonValido(ErroreContenutoBilancio):
    """Istanza malformata, con DOCTYPE o con una radice diversa da xbrli:xbrl."""


class XbrlTroppoGrande(XbrlNonValido):
    esito = "troppo_grande"


class XbrlTimeout(ErroreContenutoBilancio):
    """Parsing oltre la deadline."""


# ------------------------------------------------------------------ allegati


@dataclass
class AllegatiBilancio:
    pdf: bytes | None = None
    xbrl: bytes | None = None
    firmati: list[str] = field(default_factory=list)  # nomi dei .p7m (non letti)
    ignorati: list[str] = field(default_factory=list)  # verbale, PDF successivi, altro
    avvisi: list[str] = field(default_factory=list)  # codici, mai nomi del provider


def _nome_sicuro(nome: str) -> str:
    """Solo il basename, senza caratteri di controllo e troncato: i nomi
    arrivano dal provider e finiscono al più nei log."""
    base = nome.replace("\\", "/").rsplit("/", 1)[-1]
    pulito = "".join(c for c in base if c.isprintable()).strip()
    return pulito[:_MAX_NOME] or "(senza nome)"


def _sembra_pdf(dati: bytes) -> bool:
    return dati.startswith(b"%PDF-")


def _sembra_xml(prefisso: bytes) -> bool:
    """Inizia (dopo BOM e spazi) con `<?xml` o `<xbrli:xbrl`."""
    if prefisso.startswith(codecs.BOM_UTF8):
        testo = prefisso[len(codecs.BOM_UTF8):].decode("utf-8", "ignore")
    elif prefisso.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        testo = prefisso.decode("utf-16", "ignore")
    else:
        testo = prefisso.decode("latin-1")
    testo = testo.lstrip(" \t\r\n")
    return testo.startswith(("<?xml", "<xbrli:xbrl"))


class _Lettore:
    """Letture limitate dai membri di UNO ZIP. La somma decompressa è già
    limitata dal controllo sulle `file_size` dichiarate: zipfile non
    restituisce mai più byte di quelli dichiarati (e qui mai più del tetto)."""

    def __init__(self, zf: zipfile.ZipFile, avvisi: list[str]) -> None:
        self._zf = zf
        self._avvisi = avvisi

    def prefisso(self, info: zipfile.ZipInfo) -> bytes | None:
        try:
            with self._zf.open(info) as flusso:
                return flusso.read(_PREFISSO_SNIFF)
        except Exception:  # archivio non fidato: qualunque errore = illeggibile
            self._avvisi.append("membro_illeggibile")
            return None

    def tutto(self, info: zipfile.ZipInfo, massimo: int) -> bytes | None:
        """Contenuto del membro, mai oltre `massimo` byte: `read(massimo + 1)`
        smaschera una `file_size` dichiarata più piccola del vero."""
        try:
            with self._zf.open(info) as flusso:
                dati = flusso.read(massimo + 1)
        except Exception:  # CRC errato, cifrato, compressione ignota, troncato
            self._avvisi.append("membro_illeggibile")
            return None
        if len(dati) > massimo or len(dati) != info.file_size:
            self._avvisi.append("membro_dimensione_falsa")
            return None
        return dati


def _allegato_diretto(dati: bytes) -> AllegatiBilancio | None:
    """Il provider ha restituito il documento senza ZIP: lo si tiene lo
    stesso (mai perdere un documento pagato), con un avviso."""
    testa = dati[:_PREFISSO_SNIFF]
    if _sembra_pdf(testa):
        allegati = AllegatiBilancio(avvisi=["allegato_non_zip"])
        if len(dati) > MAX_PDF_BYTES:
            allegati.avvisi.append("pdf_troppo_grande")
        else:
            allegati.pdf = dati
        return allegati
    if _sembra_xml(testa):
        allegati = AllegatiBilancio(avvisi=["allegato_non_zip"])
        if len(dati) > MAX_XBRL_BYTES:
            allegati.avvisi.append("xbrl_troppo_grande")
        else:
            allegati.xbrl = dati
        return allegati
    return None


def _pdf_del_bilancio(candidati: list[zipfile.ZipInfo]) -> zipfile.ZipInfo | None:
    """Il PDF da conservare tra quelli che non sono il verbale: il primo con
    «bilancio» nel nome, altrimenti il più grande (con nomi opachi come
    `<id>_0.pdf`/`<id>_1.pdf` il bilancio con la nota integrativa supera il
    verbale). A pari dimensione vince il primo."""
    if not candidati:
        return None
    for info in candidati:
        if "bilancio" in _nome_sicuro(info.filename).lower():
            return info
    return max(candidati, key=lambda info: info.file_size)


def estrai_allegati(zip_bytes: bytes) -> AllegatiBilancio:
    """PDF del bilancio e istanza XBRL dallo ZIP del bilancio ottico.

    Solleva `AllegatiNonValidi` se l'archivio è illeggibile o sospetto
    (troppo grande, troppi membri, somma decompressa oltre il tetto, bomba).
    I problemi di un singolo membro diventano avvisi: il documento buono
    resta. Il verbale (nome con «verbale» o «assemblea») si scarta; degli
    altri PDF si tiene quello del bilancio (`_pdf_del_bilancio`) e gli altri
    finiscono in `ignorati`; i `.p7m` (firmati, non supportati in v1)
    finiscono in `firmati` senza essere letti.
    """
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise AllegatiNonValidi("zip_troppo_grande", "troppo_grande")
    diretto = _allegato_diretto(zip_bytes)
    if diretto is not None:
        return diretto
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        voci = zf.infolist()
    except Exception as exc:  # archivio non fidato: qualunque errore = non valido
        raise AllegatiNonValidi("zip_non_valido") from exc

    with zf:
        if len(voci) > MAX_MEMBRI:
            raise AllegatiNonValidi("zip_troppi_membri")
        membri = [info for info in voci if not info.is_dir()]
        if sum(info.file_size for info in membri) > MAX_TOTALE_DECOMPRESSO:
            raise AllegatiNonValidi("zip_decompresso_eccessivo", "troppo_grande")
        for info in membri:
            if (
                info.file_size > _SOGLIA_RAPPORTO
                and info.file_size > MAX_RAPPORTO * max(info.compress_size, 1)
            ):
                raise AllegatiNonValidi("zip_bomba")

        allegati = AllegatiBilancio()
        lettore = _Lettore(zf, allegati.avvisi)
        pdf_candidati: list[zipfile.ZipInfo] = []
        xml_xbrl: list[zipfile.ZipInfo] = []
        xml_altri: list[zipfile.ZipInfo] = []
        for info in membri:
            nome = _nome_sicuro(info.filename)
            minuscolo = nome.lower()
            if minuscolo.endswith(".p7m"):
                allegati.firmati.append(nome)
            elif info.flag_bits & 0x1:
                allegati.ignorati.append(nome)
                allegati.avvisi.append("membro_cifrato")
            elif minuscolo.endswith(".pdf"):
                if "verbale" in minuscolo or "assemblea" in minuscolo:
                    allegati.ignorati.append(nome)
                else:
                    pdf_candidati.append(info)
            elif minuscolo.endswith(".xbrl"):
                xml_xbrl.append(info)
            elif minuscolo.endswith(".xml"):
                xml_altri.append(info)
            else:
                allegati.ignorati.append(nome)

        pdf_scelto = _pdf_del_bilancio(pdf_candidati)
        allegati.ignorati.extend(
            _nome_sicuro(info.filename) for info in pdf_candidati if info is not pdf_scelto
        )

        if pdf_scelto is not None:
            if pdf_scelto.file_size > MAX_PDF_BYTES:
                allegati.avvisi.append("pdf_troppo_grande")
            else:
                dati = lettore.tutto(pdf_scelto, MAX_PDF_BYTES)
                if dati is None:
                    allegati.avvisi.append("pdf_illeggibile")
                elif _sembra_pdf(dati):
                    allegati.pdf = dati
                else:
                    allegati.avvisi.append("pdf_non_valido")

        # Estensione .xbrl prima di .xml: uno .xml qualsiasi (metadati) che
        # inizia con <?xml non deve passare davanti all'istanza.
        scelto = False
        for info in xml_xbrl + xml_altri:
            nome = _nome_sicuro(info.filename)
            if scelto:
                allegati.ignorati.append(nome)
                continue
            prefisso = lettore.prefisso(info)
            if prefisso is None:
                allegati.ignorati.append(nome)
                allegati.avvisi.append("xbrl_illeggibile")
                continue
            if not _sembra_xml(prefisso):
                allegati.ignorati.append(nome)
                continue
            scelto = True
            if info.file_size > MAX_XBRL_BYTES:
                allegati.avvisi.append("xbrl_troppo_grande")
                continue
            allegati.xbrl = lettore.tutto(info, MAX_XBRL_BYTES)
            if allegati.xbrl is None:
                allegati.avvisi.append("xbrl_illeggibile")

    allegati.avvisi = list(dict.fromkeys(allegati.avvisi))
    return allegati


def esito_senza_xbrl(allegati: AllegatiBilancio) -> str:
    """`xbrl_esito` quando `estrai_allegati` non ha dato un XBRL leggibile."""
    if "xbrl_troppo_grande" in allegati.avvisi:
        return "troppo_grande"
    if "xbrl_illeggibile" in allegati.avvisi:
        return "non_valido"
    if any(not nome.lower().endswith(".pdf.p7m") for nome in allegati.firmati):
        return "firmato_non_leggibile"
    return "assente"


# ------------------------------------------------------------------ XBRL

NS_XBRLI = "http://www.xbrl.org/2003/instance"
NS_LINK = "http://www.xbrl.org/2003/linkbase"
NS_XLINK = "http://www.w3.org/1999/xlink"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_ISO4217 = "http://www.xbrl.org/2003/iso4217"

# Namespace dei concept: versione della tassonomia NON fissata in codice.
_RE_NS_ITCC = re.compile(r"^http://www\.infocamere\.it/itnn/fr/itcc/ci/\d{4}-\d{2}-\d{2}$")
_RE_SCHEMA = re.compile(r"^itcc-ci-(ese|abb|micr|cons)-(\d{4}-\d{2}-\d{2})\.xsd$", re.IGNORECASE)
_RE_DECIMALE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_FORME: dict[str, FormaXbrl] = {
    "ese": "ordinario",
    "abb": "abbreviato",
    "micr": "micro",
    "cons": "consolidato",
}

_ISTANTE = "istante"
_DURATA = "durata"

# Campi interni, solo per i controlli TEBENI: non sono colonne di bilancio.
_UTILE_SP = "_utile_sp"
_TOTALE_PASSIVO = "_totale_passivo"

# Mappa CHIUSA local-name → (campo, tipo di periodo). L'EBITDA NON si deriva
# dall'XBRL in v1: mescolerebbe due definizioni di MOL con IT-full.
_CONCETTI: dict[str, tuple[str, str]] = {
    "ValoreProduzioneRicaviVenditePrestazioni": ("fatturato", _DURATA),
    "TotaleValoreProduzione": ("valore_produzione", _DURATA),
    "UtilePerditaEsercizio": ("risultato_esercizio", _DURATA),
    "PatrimonioNettoUtilePerditaEsercizio": (_UTILE_SP, _ISTANTE),
    "TotalePatrimonioNetto": ("patrimonio_netto", _ISTANTE),
    "PatrimonioNettoCapitale": ("capitale_sociale", _ISTANTE),
    "TotaleAttivo": ("totale_attivo", _ISTANTE),
    "TotalePassivo": (_TOTALE_PASSIVO, _ISTANTE),
    "TotaleDebiti": ("debiti_totali", _ISTANTE),
    "TotaleDisponibilitaLiquide": ("disponibilita_liquide", _ISTANTE),
    "ProventiOneriFinanziariInteressiAltriOneriFinanziariTotaleInteressiAltriOneriFinanziari": (
        "oneri_finanziari",
        _DURATA,
    ),
    "CostiProduzionePersonaleTotaleCostiPersonale": ("costo_personale", _DURATA),
    "DifferenzaValoreCostiProduzione": ("ebit", _DURATA),
    # decimalItemType, unità xbrli:pure; solo in ordinario e abbreviato.
    "TotaleDipendentiNumeroMedio": ("dipendenti", _ISTANTE),
}
_ANAGRAFICA: dict[str, str] = {
    "DatiAnagraficiCodiceFiscale": "cf",
    "DatiAnagraficiPartitaIva": "piva",
}
_CAMPI_NON_MONETARI = frozenset({"dipendenti"})

_CENTESIMO = Decimal("0.01")


@dataclass
class EsercizioXbrl:
    anno: int
    inizio: date
    fine: date
    valori: dict[str, Decimal | None]  # i 15 CAMPI_BILANCIO, None se assenti
    ruolo: str  # 'corrente' (il più recente) | 'comparativo'


@dataclass
class IstanzaXbrl:
    forma: FormaXbrl
    tassonomia: str | None  # versione, es. '2018-11-04'
    cf: str | None
    piva: str | None
    esercizi: list[EsercizioXbrl] = field(default_factory=list)  # più recente prima
    avvisi: list[str] = field(default_factory=list)


@dataclass
class _Contesto:
    id: str
    inizio: str | None = None
    fine: str | None = None
    istante: str | None = None
    dimensioni: bool = False


@dataclass
class _Unita:
    id: str
    misure: list[tuple[str, str]] = field(default_factory=list)
    composta: bool = False  # divide/unitNumerator: mai un importo in euro


@dataclass
class _Fatto:
    concetto: str
    contesto: str | None
    unita: str | None
    nil: bool
    testo: str | None = None
    annidato: bool = False


def _adesso() -> float:
    return time.monotonic()


class _XbrlTarget:
    """Target di `ET.XMLParser`: conserva solo schemaRef, contesti, unità e
    i fatti della mappa chiusa che sono figli DIRETTI della radice. Il testo
    di tutto il resto (textBlock, tuple) si scarta senza accumularlo."""

    def __init__(self, scadenza: float) -> None:
        self._scadenza = scadenza
        self._profondita = 0
        self.radice_aperta = False
        self._ns: dict[str, list[str]] = {}
        self._tag: dict[str, tuple[str, str]] = {}
        self.schema_ref: list[str] = []
        self.contesti: dict[str, _Contesto] = {}
        self.contesti_duplicati: set[str] = set()
        self.unita: dict[str, _Unita] = {}
        self.fatti: list[_Fatto] = []
        self.versioni_ns: set[str] = set()
        self._contesto: _Contesto | None = None
        self._unita: _Unita | None = None
        self._fatto: _Fatto | None = None
        # testo in raccolta: (destinazione, profondità dell'elemento)
        self._dest: str | None = None
        self._dest_profondita = 0
        self._testo: list[str] = []
        self._lunghezza = 0

    # -- sicurezza
    def doctype(self, name, pubid, system) -> None:
        raise XbrlNonValido("doctype")

    # -- namespace (servono per risolvere il QName di xbrli:measure)
    def start_ns(self, prefix: str, uri: str) -> None:
        self._ns.setdefault(prefix, []).append(uri)

    def end_ns(self, prefix: str) -> None:
        pila = self._ns.get(prefix)
        if pila:
            pila.pop()

    def _risolvi_qname(self, testo: str) -> tuple[str, str] | None:
        prefisso, _, locale = testo.strip().rpartition(":")
        pila = self._ns.get(prefisso)
        if not pila or not locale:
            return None
        return pila[-1], locale

    def _dividi(self, tag: str) -> tuple[str, str]:
        diviso = self._tag.get(tag)
        if diviso is None:
            if tag.startswith("{"):
                ns, _, locale = tag[1:].partition("}")
            else:
                ns, locale = "", tag
            diviso = (ns, locale)
            self._tag[tag] = diviso
        return diviso

    # -- testo
    def _raccogli(self, destinazione: str) -> None:
        self._dest = destinazione
        self._dest_profondita = self._profondita
        self._testo = []
        self._lunghezza = 0

    def data(self, testo: str) -> None:
        if self._dest is None or self._profondita != self._dest_profondita:
            return
        self._lunghezza += len(testo)
        if self._lunghezza <= _MAX_TESTO:
            self._testo.append(testo)

    def _testo_raccolto(self) -> str | None:
        testo = "".join(self._testo) if self._lunghezza <= _MAX_TESTO else None
        self._dest = None
        self._testo = []
        return testo

    # -- elementi
    def start(self, tag: str, attrib: dict[str, str]) -> None:
        if _adesso() > self._scadenza:
            raise XbrlTimeout("timeout")
        self._profondita += 1
        ns, locale = self._dividi(tag)
        if self._profondita == 1:
            if ns != NS_XBRLI or locale != "xbrl":
                raise XbrlNonValido("radice_non_xbrl")
            self.radice_aperta = True
        elif self._profondita == 2:
            self._apri_figlio(ns, locale, attrib)
        elif self._contesto is not None:
            if ns == NS_XBRLI and locale in ("segment", "scenario"):
                self._contesto.dimensioni = True
            elif ns == NS_XBRLI and locale in ("startDate", "endDate", "instant"):
                self._raccogli(locale)
        elif self._unita is not None:
            if ns == NS_XBRLI and locale == "measure":
                self._raccogli("measure")
            elif ns == NS_XBRLI and locale == "divide":
                self._unita.composta = True
        elif self._fatto is not None:
            self._fatto.annidato = True  # un item non ha figli: fatto scartato

    def _apri_figlio(self, ns: str, locale: str, attrib: dict[str, str]) -> None:
        if ns == NS_XBRLI and locale == "context":
            self._contesto = _Contesto(id=attrib.get("id", ""))
        elif ns == NS_XBRLI and locale == "unit":
            self._unita = _Unita(id=attrib.get("id", ""))
        elif ns == NS_LINK and locale == "schemaRef":
            href = attrib.get(f"{{{NS_XLINK}}}href")
            if href:
                self.schema_ref.append(href[:_MAX_TESTO])
        elif (locale in _CONCETTI or locale in _ANAGRAFICA) and _RE_NS_ITCC.match(ns):
            self.versioni_ns.add(ns.rsplit("/", 1)[-1])
            self._fatto = _Fatto(
                concetto=locale,
                contesto=attrib.get("contextRef"),
                unita=attrib.get("unitRef"),
                nil=attrib.get(f"{{{NS_XSI}}}nil", "").strip() in ("true", "1"),
            )
            self._raccogli("fatto")

    def end(self, tag: str) -> None:
        if self._dest is not None and self._profondita == self._dest_profondita:
            destinazione = self._dest
            testo = self._testo_raccolto()
            if destinazione == "fatto" and self._fatto is not None:
                self._fatto.testo = testo
            elif destinazione == "measure" and self._unita is not None:
                misura = self._risolvi_qname(testo) if testo else None
                if misura is None:
                    self._unita.composta = True
                else:
                    self._unita.misure.append(misura)
            elif self._contesto is not None and testo is not None:
                testo = testo.strip()
                if destinazione == "startDate":
                    self._contesto.inizio = testo
                elif destinazione == "endDate":
                    self._contesto.fine = testo
                else:
                    self._contesto.istante = testo
        if self._profondita == 2:
            self._chiudi_figlio()
        self._profondita -= 1

    def _chiudi_figlio(self) -> None:
        if self._contesto is not None:
            cid = self._contesto.id
            if cid in self.contesti or cid in self.contesti_duplicati:
                # id ambiguo: nessuno dei due contesti è affidabile
                self.contesti.pop(cid, None)
                self.contesti_duplicati.add(cid)
            else:
                self.contesti[cid] = self._contesto
            self._contesto = None
        elif self._unita is not None:
            self.unita[self._unita.id] = self._unita
            self._unita = None
        elif self._fatto is not None:
            self.fatti.append(self._fatto)
            self._fatto = None

    def close(self) -> None:
        return None


def _controlla_scadenza(scadenza: float) -> None:
    if _adesso() > scadenza:
        raise XbrlTimeout("timeout")


def _leggi_istanza(data: bytes, scadenza: float) -> _XbrlTarget:
    target = _XbrlTarget(scadenza)
    parser = ET.XMLParser(target=target)
    pos = 0
    totale = len(data)
    try:
        # Prologo a pezzi minimi: il DOCTYPE (se c'è) sta per forza prima
        # della radice, e il target lo rifiuta prima che expat veda le
        # dichiarazioni e i riferimenti successivi.
        while pos < totale and not target.radice_aperta:
            if pos >= _MAX_PROLOGO:
                raise XbrlNonValido("prologo_troppo_lungo")
            parser.feed(data[pos:pos + _PEZZO_PROLOGO])
            pos += _PEZZO_PROLOGO
        while pos < totale:
            _controlla_scadenza(scadenza)
            parser.feed(data[pos:pos + CHUNK])
            pos += CHUNK
        _controlla_scadenza(scadenza)
        parser.close()
    except (ET.ParseError, LookupError, UnicodeError) as exc:
        # LookupError/UnicodeError: encoding dichiarato sconosciuto o inservibile
        raise XbrlNonValido("xml_malformato") from exc
    return target


def _data(testo: str | None) -> date | None:
    if not testo or not _RE_DATA.match(testo):
        return None
    try:
        return date.fromisoformat(testo)
    except ValueError:
        return None


def _periodo(contesto: _Contesto) -> tuple | None:
    """("istante", data) | ("durata", inizio, fine) | None se non valido."""
    if contesto.istante is not None:
        if contesto.inizio is not None or contesto.fine is not None:
            return None
        giorno = _data(contesto.istante)
        return (_ISTANTE, giorno) if giorno else None
    inizio, fine = _data(contesto.inizio), _data(contesto.fine)
    if inizio is None or fine is None or fine < inizio:
        return None
    return (_DURATA, inizio, fine)


def _importo(testo: str | None, campo: str) -> tuple[Decimal | None, str | None]:
    """Valore del fatto → Decimal al centesimo (come le colonne numeric del
    DB) oppure (None, motivo). Stesse soglie e segni di bilanci_mapping."""
    testo = (testo or "").strip()
    if not _RE_DECIMALE.match(testo):
        return None, "valore_non_valido"
    grezzo = Decimal(testo)
    limite = _LIMITI.get(campo, _LIMITE_DEFAULT)
    if abs(grezzo) >= limite * 10:
        return None, "valore_fuori_scala"
    valore = grezzo.quantize(_CENTESIMO, rounding=ROUND_HALF_UP)
    if abs(valore) >= limite:
        return None, "valore_fuori_scala"
    if campo in _CAMPI_NON_NEGATIVI and valore < 0:
        return None, "valore_negativo"
    return valore, None


def _normalizza_id(valore: object) -> str | None:
    """CF/P.IVA confrontabili: maiuscolo, senza spazi, senza prefisso IT."""
    if valore is None:
        return None
    testo = str(valore).strip().upper().replace(" ", "")
    if testo.startswith("IT") and len(testo) == 13 and testo[2:].isdigit():
        testo = testo[2:]
    return testo or None


def _forma_e_tassonomia(target: _XbrlTarget) -> tuple[FormaXbrl, str | None]:
    for href in target.schema_ref:
        nome = href.strip().replace("\\", "/").rsplit("/", 1)[-1]
        trovato = _RE_SCHEMA.match(nome)
        if trovato:
            return _FORME[trovato.group(1).lower()], trovato.group(2)
    versione = max(target.versioni_ns) if target.versioni_ns else None
    return "ignoto", versione


def _costruisci_istanza(target: _XbrlTarget) -> IstanzaXbrl:
    avvisi: list[str] = []
    forma, tassonomia = _forma_e_tassonomia(target)
    if forma == "ignoto":
        avvisi.append("forma_non_riconosciuta")

    periodi: dict[str, tuple] = {}
    for contesto in target.contesti.values():
        if contesto.dimensioni:
            avvisi.append("contesto_con_dimensioni")
            continue
        periodo = _periodo(contesto)
        if periodo is None:
            avvisi.append("periodo_non_valido")
            continue
        periodi[contesto.id] = periodo
    if target.contesti_duplicati:
        avvisi.append("contesto_duplicato")

    def misura_unica(uid: str | None) -> tuple[str, str] | None:
        unita = target.unita.get(uid or "")
        if unita is None or unita.composta or len(unita.misure) != 1:
            return None
        return unita.misure[0]

    # (campo, periodo) → valori trovati; anagrafica: campo → [(data, valore)]
    grezzi: dict[tuple[str, tuple], list[Decimal]] = {}
    anagrafica: dict[str, list[tuple[date, str]]] = {}
    for fatto in target.fatti:
        periodo = periodi.get(fatto.contesto or "")
        if periodo is None or fatto.nil:
            continue  # contesto assente/ignorato; xsi:nil = valore assente
        if fatto.annidato:
            avvisi.append(f"valore_non_valido: {fatto.concetto}")
            continue
        if fatto.concetto in _ANAGRAFICA:
            valore = _normalizza_id(fatto.testo)
            if valore:
                anagrafica.setdefault(_ANAGRAFICA[fatto.concetto], []).append(
                    (periodo[-1], valore)
                )
            continue
        campo, tipo = _CONCETTI[fatto.concetto]
        if periodo[0] != tipo:
            avvisi.append(f"periodo_incoerente: {fatto.concetto}")
            continue
        misura = misura_unica(fatto.unita)
        attesa = (NS_XBRLI, "pure") if campo in _CAMPI_NON_MONETARI else (NS_ISO4217, "EUR")
        if misura != attesa:
            codice = "unita_non_valida" if campo in _CAMPI_NON_MONETARI else "unita_non_eur"
            avvisi.append(f"{codice}: {fatto.concetto}")
            continue
        valore, motivo = _importo(fatto.testo, campo)
        if valore is None:
            avvisi.append(f"{motivo}: {fatto.concetto}")
            continue
        grezzi.setdefault((campo, periodo), []).append(valore)

    # Esercizi = durate usate dai fatti di conto economico; per ogni data di
    # chiusura quella con più fatti.
    conteggi: dict[tuple, int] = {}
    for (_campo, periodo), valori in grezzi.items():
        if periodo[0] == _DURATA:
            conteggi[periodo] = conteggi.get(periodo, 0) + len(valori)
    per_fine: dict[date, tuple] = {}
    for periodo, n in sorted(conteggi.items(), key=lambda voce: (voce[0][2], voce[0][1])):
        attuale = per_fine.get(periodo[2])
        if attuale is None or n > conteggi[attuale]:
            per_fine[periodo[2]] = periodo
    durate = sorted(per_fine.values(), key=lambda periodo: periodo[2], reverse=True)
    scelte: list[tuple] = []
    for periodo in durate:
        anno = periodo[2].year
        if not ANNO_MIN <= anno <= ANNO_MAX:
            avvisi.append("anno_fuori_intervallo")
        elif any(scelto[2].year == anno for scelto in scelte):
            avvisi.append("anno_duplicato")
        else:
            scelte.append(periodo)
    if len(scelte) > _MAX_ANNUALITA:
        avvisi.append("annualita_eccedenti")
        scelte = scelte[:_MAX_ANNUALITA]

    limite_xz = _XZ_DUE_ANNUALITA if len(scelte) > 1 else _XZ_UNA_ANNUALITA
    esercizi: list[EsercizioXbrl] = []
    for indice, (_tipo, inizio, fine) in enumerate(scelte):
        chiavi = {_DURATA: (_DURATA, inizio, fine), _ISTANTE: (_ISTANTE, fine)}
        trovati: dict[str, Decimal] = {}
        for concetto, (campo, tipo) in _CONCETTI.items():
            valori = grezzi.get((campo, chiavi[tipo]))
            if not valori:
                continue
            if len(set(valori)) > 1:
                # stesso concept e periodo con valori diversi: meglio None
                avvisi.append(f"fatto_discordante: {concetto} {fine.year}")
                continue
            trovati[campo] = valori[0]
        valori_campi: dict[str, Decimal | None] = {
            campo: trovati.get(campo) for campo in CAMPI_BILANCIO
        }
        if valori_campi["risultato_esercizio"] is None:
            valori_campi["risultato_esercizio"] = trovati.get(_UTILE_SP)

        utile_ce, utile_sp = trovati.get("risultato_esercizio"), trovati.get(_UTILE_SP)
        if utile_ce is not None and utile_sp is not None and utile_ce != utile_sp:
            avvisi.append(
                f"X8 {fine.year}: utile dello stato patrimoniale diverso da quello "
                "del conto economico"
            )
        attivo, passivo = trovati.get("totale_attivo"), trovati.get(_TOTALE_PASSIVO)
        if attivo is not None and passivo is not None and attivo != passivo:
            avvisi.append(f"X9 {fine.year}: totale attivo diverso dal totale passivo")
        giorni = (fine - inizio).days
        if giorni > limite_xz:
            avvisi.append(f"XZ {fine.year}: esercizio di {giorni} giorni (massimo {limite_xz})")

        esercizi.append(
            EsercizioXbrl(
                anno=fine.year,
                inizio=inizio,
                fine=fine,
                valori=valori_campi,
                ruolo="corrente" if indice == 0 else "comparativo",
            )
        )

    identificativi: dict[str, str | None] = {}
    for chiave in ("cf", "piva"):
        voci = anagrafica.get(chiave, [])
        if len({valore for _giorno, valore in voci}) > 1:
            avvisi.append("anagrafica_discordante")
        # il valore del periodo più recente (a parità, il primo)
        identificativi[chiave] = (
            max(voci, key=lambda voce: voce[0])[1] if voci else None
        )

    return IstanzaXbrl(
        forma=forma,
        tassonomia=tassonomia,
        cf=identificativi["cf"],
        piva=identificativi["piva"],
        esercizi=esercizi,
        avvisi=list(dict.fromkeys(avvisi)),
    )


def parse_xbrl(data: bytes, *, deadline_s: float = XBRL_DEADLINE_SECONDS) -> IstanzaXbrl:
    """Istanza XBRL (tassonomia itcc-ci) → forma, identificativi ed esercizi.

    Solleva `XbrlTroppoGrande` oltre `MAX_XBRL_BYTES`, `XbrlNonValido` per
    DOCTYPE, XML malformato o radice diversa da xbrli:xbrl, `XbrlTimeout`
    oltre la deadline. Le incoerenze (TEBENI X8/X9/XZ, unità non in euro,
    contesti con dimensioni, valori scartati) sono avvisi, non errori.
    """
    if len(data) > MAX_XBRL_BYTES:
        raise XbrlTroppoGrande("troppo_grande")
    scadenza = _adesso() + deadline_s
    target = _leggi_istanza(data, scadenza)
    return _costruisci_istanza(target)


def righe_da_istanza(
    ist: IstanzaXbrl, identificativi_attesi: Iterable[str]
) -> tuple[list[RigaFonte], str]:
    """Righe per `bilanci_service.registra_fonte('xbrl', ...)` e `xbrl_esito`.

    - consolidato → nessuna riga, `consolidato` (non è il bilancio d'esercizio);
    - CF e P.IVA dell'istanza assenti o non tra `identificativi_attesi` →
      nessuna riga, `cf_non_corrispondente`;
    - nessun esercizio con almeno un campo CORE → nessuna riga, `non_valido`;
    - altrimenti `ok`: l'esercizio più recente con `ruolo='corrente'`,
      l'altro `comparativo`, in ordine di anno crescente.
    """
    if ist.forma == "consolidato":
        return [], "consolidato"
    attesi = {n for n in (_normalizza_id(v) for v in identificativi_attesi) if n}
    propri = {n for n in (_normalizza_id(ist.cf), _normalizza_id(ist.piva)) if n}
    if not propri or not (propri & attesi):
        return [], "cf_non_corrispondente"
    tipo_bilancio = ist.forma if ist.forma in ("ordinario", "abbreviato", "micro") else "ignoto"
    righe: list[RigaFonte] = []
    for esercizio in ist.esercizi:
        valori = {campo: esercizio.valori.get(campo) for campo in CAMPI_BILANCIO}
        if all(valori[campo] is None for campo in CAMPI_CORE):
            continue
        righe.append(
            RigaFonte(
                anno=esercizio.anno,
                data_chiusura=esercizio.fine,
                tipo_bilancio=tipo_bilancio,
                ruolo=esercizio.ruolo,
                valori=valori,
            )
        )
    if not righe:
        return [], "non_valido"
    righe.sort(key=lambda riga: riga.anno)
    return righe, "ok"
