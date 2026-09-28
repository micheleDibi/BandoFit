"""Bilanci per esercizio dalle fonti openapi (WP1): mapping PURO, nessun I/O.

Ogni fonte produce righe normalizzate (`RigaFonte`, una per esercizio) che il
servizio registra con la RPC `fn_bilanci_registra_fonte`; la riga fusa per
esercizio la calcola il DB (`fn_bilanci_ricalcola_anno`) con la regola «per
ogni campo vince il primo valore non nullo per rango di fonte». `unisci_fonti`
è il gemello Python di quella regola: la tabella di casi
tests/fixtures/bilanci/precedenza_casi.json la verifica su entrambi.

Fonti e trappole note (verificate sugli esempi OAS, NON ancora in sandbox):
- IT-full descrive UN esercizio. Anno = anno di `ecofin.balanceSheetDate`, a
  cui si riferiscono voci CEE e `operatingResults`. `ecofin.turnoverYear` può
  venire da un'altra base dati: se è diverso, `ecofin.turnover` NON entra
  nella riga (finirebbe nell'anno sbagliato) e si scrive un avviso.
- IT-advanced ha lo storico (fino a ~7 anni) ma il suo `netWorth` è l'UTILE
  dell'esercizio («annual profit»), non il patrimonio netto: va in
  `risultato_esercizio`. Il patrimonio netto sta solo in IT-full.
- Le righe recenti di IT-advanced possono essere segnaposto (data, fatturato e
  utile nulli): si scartano, mai trattate come zero.

Tutti i numeri passano da `Decimal(str(x))`, mai da `Decimal(float)`, e sono
arrotondati al centesimo come le colonne numeric(…, 2) del DB.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.openapi_mapping import (
    CODICI_TOTALE_ATTIVO,
    CODICI_VALORE_PRODUZIONE,
    _get,
    _numero,
    _voce_cee,
    data_chiusura_bilancio,
    parse_anno,
    parse_openapi_date,
    utile_esercizio,
)

logger = logging.getLogger("bandofit.bilanci")

# Ordine FISSO, identico a v_campi della migration 0032 (il test DB li confronta).
CAMPI_BILANCIO: tuple[str, ...] = (
    "fatturato",
    "valore_produzione",
    "risultato_esercizio",
    "patrimonio_netto",
    "capitale_sociale",
    "totale_attivo",
    "debiti_totali",
    "disponibilita_liquide",
    "ebitda",
    "ebit",
    "cash_flow",
    "oneri_finanziari",
    "dipendenti",
    "costo_personale",
    "retribuzione_media_lorda",
)

# Almeno uno di questi deve essere valorizzato perché un esercizio esista
# (CHECK cf_non_segnaposto di company_financials).
CAMPI_CORE: tuple[str, ...] = (
    "fatturato",
    "valore_produzione",
    "risultato_esercizio",
    "patrimonio_netto",
    "totale_attivo",
)

RANGO_FONTE: dict[str, int] = {"xbrl": 3, "it_full": 2, "it_advanced": 1}
TIPI_BILANCIO: tuple[str, ...] = ("ordinario", "abbreviato", "micro", "ignoto")
RUOLI: tuple[str, ...] = ("corrente", "comparativo")

# Da incrementare a ogni correzione del mapping: la lettura dei bilanci
# rimappa gratis dai payload conservati le aziende con versione più vecchia.
MAPPING_BILANCI_VERSIONE = 1

ANNO_MIN = 1990
ANNO_MAX = 2100

# Voci CEE di IT-full per cui il codice openapi esatto NON è ancora verificato
# (serve la fixture sandbox, gate G1): finché restano qui i campi valgono None,
# meglio un dato assente che un dato sbagliato.
_CODICI_CEE_DA_VERIFICARE: dict[str, str] = {
    "debiti_totali": "D) totale debiti",
    "disponibilita_liquide": "C.IV) totale disponibilità liquide",
    "oneri_finanziari": "C.17) interessi e altri oneri finanziari",
    "costo_personale": "B.9) totale costi per il personale",
}

# Valori di segno impossibile: diventano None con un avviso. Nessun CHECK di
# segno in DB, che farebbe fallire l'intera registrazione della fonte.
_CAMPI_NON_NEGATIVI = frozenset(
    {
        "fatturato",
        "capitale_sociale",
        "totale_attivo",
        "debiti_totali",
        "disponibilita_liquide",
        "dipendenti",
        "costo_personale",
        "retribuzione_media_lorda",
    }
)

# Oltre questi valori assoluti le colonne numeric del DB andrebbero in
# overflow e la RPC fallirebbe per tutte le righe: il valore è insensato.
_LIMITE_DEFAULT = Decimal("1e16")  # numeric(18,2)
_LIMITI = {
    "dipendenti": Decimal("1e8"),  # numeric(10,2)
    "retribuzione_media_lorda": Decimal("1e10"),  # numeric(12,2)
}

_CENTESIMO = Decimal("0.01")


@dataclass
class RigaFonte:
    """Valori di UN esercizio secondo UNA fonte, pronti per la RPC."""

    anno: int
    data_chiusura: date | None
    tipo_bilancio: str
    ruolo: str
    valori: dict[str, Decimal | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tipo_bilancio not in TIPI_BILANCIO:
            raise ValueError(f"tipo_bilancio non valido: {self.tipo_bilancio!r}")
        if self.ruolo not in RUOLI:
            raise ValueError(f"ruolo non valido: {self.ruolo!r}")
        ignoti = set(self.valori) - set(CAMPI_BILANCIO)
        if ignoti:
            raise ValueError(f"campi di bilancio sconosciuti: {sorted(ignoti)}")


# ------------------------------------------------------------------ utilità

def _valori_vuoti() -> dict[str, Decimal | None]:
    return dict.fromkeys(CAMPI_BILANCIO)


def _ha_core(valori: dict[str, Decimal | None]) -> bool:
    return any(valori.get(campo) is not None for campo in CAMPI_CORE)


def _anno_valido(anno: int | None) -> bool:
    return anno is not None and ANNO_MIN <= anno <= ANNO_MAX


def _decimale(valore: Any, campo: str, contesto: str) -> Decimal | None:
    """Importo del provider → Decimal al centesimo, oppure None se assente o
    insensato. Nel log finiscono solo campo e contesto, mai il valore."""
    numero = _numero(valore)
    if numero is None:
        return None
    grezzo = Decimal(str(numero))
    # Il limite si confronta DOPO l'arrotondamento, come fa la RPC; il primo
    # controllo evita solo che quantize vada in overflow su valori assurdi.
    limite = _LIMITI.get(campo, _LIMITE_DEFAULT)
    valore = (
        grezzo.quantize(_CENTESIMO, rounding=ROUND_HALF_UP)
        if abs(grezzo) < limite * 10
        else None
    )
    if valore is None or abs(valore) >= limite:
        logger.warning("bilanci %s: %s fuori scala, scartato", contesto, campo)
        return None
    if campo in _CAMPI_NON_NEGATIVI and valore < 0:
        logger.warning("bilanci %s: %s negativo, scartato", contesto, campo)
        return None
    return valore


def _stringa_decimale(valore: Decimal) -> str:
    """Decimale in notazione fissa: `str(Decimal)` può dare `1E+3`, che la
    validazione della RPC («stringa numerica») rifiuterebbe."""
    return format(valore, "f")


# ------------------------------------------------------------------ IT-full

def da_it_full(payload: dict) -> list[RigaFonte]:
    """Riga dell'unico esercizio descritto da IT-full (0 o 1 riga).

    `payload` è il `data` di IT-full, lo stesso di `company_data.raw`. Non si
    mappano `employees.employee` (organico corrente, non dell'esercizio), gli
    `*L2Y` (semantica non verificata) e le voci di `_CODICI_CEE_DA_VERIFICARE`.
    """
    if not isinstance(payload, dict):
        return []
    ecofin = payload.get("ecofin") if isinstance(payload.get("ecofin"), dict) else {}
    chiusura = data_chiusura_bilancio(payload)
    anno_fatturato = parse_anno(ecofin.get("turnoverYear"))
    valori = _valori_vuoti()

    if chiusura is not None:
        anno = chiusura.year
        contesto = f"IT-full {anno}"
        valori["patrimonio_netto"] = _decimale(ecofin.get("netWorth"), "patrimonio_netto", contesto)
        valori["capitale_sociale"] = _decimale(
            ecofin.get("shareCapital"), "capitale_sociale", contesto
        )
        risultati = _get(payload, "operatingResults")
        valori["ebitda"] = _decimale(_get(risultati, "ebitda"), "ebitda", contesto)
        valori["ebit"] = _decimale(_get(risultati, "ebit"), "ebit", contesto)
        valori["cash_flow"] = _decimale(_get(risultati, "cashFlow"), "cash_flow", contesto)
        valori["risultato_esercizio"] = _decimale(
            utile_esercizio(payload), "risultato_esercizio", contesto
        )
        valori["valore_produzione"] = _decimale(
            _voce_cee(payload, CODICI_VALORE_PRODUZIONE), "valore_produzione", contesto
        )
        valori["totale_attivo"] = _decimale(
            _voce_cee(payload, CODICI_TOTALE_ATTIVO), "totale_attivo", contesto
        )
        if anno_fatturato is None or anno_fatturato == anno:
            valori["fatturato"] = _decimale(ecofin.get("turnover"), "fatturato", contesto)
        elif ecofin.get("turnover") is not None:
            logger.warning(
                "bilanci IT-full: turnoverYear %s diverso dall'anno di chiusura %s, "
                "fatturato escluso dalla riga",
                anno_fatturato,
                anno,
            )
    elif anno_fatturato is not None:
        # Senza data di chiusura l'esercizio delle voci CEE è ignoto: resta
        # solo il fatturato, che ha il suo anno dichiarato.
        anno = anno_fatturato
        valori["fatturato"] = _decimale(ecofin.get("turnover"), "fatturato", f"IT-full {anno}")
        logger.info("bilanci IT-full senza balanceSheetDate: solo il fatturato di turnoverYear")
    else:
        return []

    if not _anno_valido(anno):
        logger.warning("bilanci IT-full: anno %s fuori intervallo, riga scartata", anno)
        return []
    if not _ha_core(valori):
        return []
    return [
        RigaFonte(
            anno=anno,
            data_chiusura=chiusura,
            tipo_bilancio="ignoto",
            ruolo="corrente",
            valori=valori,
        )
    ]


# -------------------------------------------------------------- IT-advanced

# Campo openapi → colonna. ATTENZIONE: `netWorth` di IT-advanced è l'UTILE.
_MAPPA_ADVANCED: tuple[tuple[str, str], ...] = (
    ("turnover", "fatturato"),
    ("netWorth", "risultato_esercizio"),
    ("totalAssets", "totale_attivo"),
    ("employees", "dipendenti"),
    ("shareCapital", "capitale_sociale"),
    ("totalStaffCost", "costo_personale"),
    ("avgGrossSalary", "retribuzione_media_lorda"),
)


def _righe_grezze_advanced(dato: dict) -> list[Any]:
    tutte = _get(dato, "balanceSheets", "all")
    if isinstance(tutte, list) and tutte:
        return tutte
    ultima = _get(dato, "balanceSheets", "last")
    return [ultima] if isinstance(ultima, dict) else []


def _valorizzati(riga: RigaFonte) -> int:
    return sum(1 for v in riga.valori.values() if v is not None)


def da_it_advanced(dato: dict) -> list[RigaFonte]:
    """Righe per esercizio dallo storico di IT-advanced, anno crescente.

    `dato` è `data[0]` della risposta. Si legge `balanceSheets.all` con
    ripiego su `[balanceSheets.last]`. Anno = anno di `balanceSheetDate`, con
    ripiego su `year` (se discordano vince la data, con un avviso). Scartate:
    righe segnaposto, righe senza alcun campo CORE, anni fuori intervallo.
    """
    if not isinstance(dato, dict):
        return []
    per_anno: dict[int, RigaFonte] = {}
    for grezza in _righe_grezze_advanced(dato):
        if not isinstance(grezza, dict):
            continue
        if all(grezza.get(k) is None for k in ("balanceSheetDate", "turnover", "netWorth")):
            continue  # segnaposto: esercizio non (ancora) depositato
        chiusura = parse_openapi_date(grezza.get("balanceSheetDate"))
        anno_dichiarato = parse_anno(grezza.get("year"))
        if chiusura is not None:
            anno = chiusura.year
            if anno_dichiarato is not None and anno_dichiarato != anno:
                logger.warning(
                    "bilanci IT-advanced: year %s diverso dalla data di chiusura (%s), "
                    "vale la data",
                    anno_dichiarato,
                    anno,
                )
        else:
            anno = anno_dichiarato
        if not _anno_valido(anno):
            logger.warning("bilanci IT-advanced: anno %s non valido, riga scartata", anno)
            continue
        contesto = f"IT-advanced {anno}"
        valori = _valori_vuoti()
        for chiave, campo in _MAPPA_ADVANCED:
            valori[campo] = _decimale(grezza.get(chiave), campo, contesto)
        if not _ha_core(valori):
            continue
        riga = RigaFonte(
            anno=anno,
            data_chiusura=chiusura,
            tipo_bilancio="ignoto",
            ruolo="corrente",
            valori=valori,
        )
        precedente = per_anno.get(anno)
        if precedente is not None:
            # La PK (azienda, anno, fonte) ammette una riga per anno: si tiene
            # la più completa (a parità, la prima).
            logger.warning("bilanci IT-advanced: più righe per l'anno %s, tenuta una", anno)
            if _valorizzati(riga) <= _valorizzati(precedente):
                continue
        per_anno[anno] = riga
    return [per_anno[anno] for anno in sorted(per_anno)]


def ids_advanced(dato: dict) -> set[str]:
    """Identificativi dell'impresa in una risposta IT-advanced (`vatCode` e
    `taxCode`, di primo livello o sotto `companyDetails`), normalizzati. La
    forma esatta va confermata sulla fixture sandbox: si guardano entrambe."""
    ids: set[str] = set()
    if not isinstance(dato, dict):
        return ids
    for contenitore in (dato, dato.get("companyDetails")):
        if not isinstance(contenitore, dict):
            continue
        for chiave in ("vatCode", "taxCode"):
            valore = contenitore.get(chiave)
            if valore is None or isinstance(valore, bool):
                continue
            testo = str(valore).strip().upper().replace(" ", "")
            if not testo:
                continue
            ids.add(testo)
            if testo.startswith("IT") and testo[2:].isdigit():
                ids.add(testo[2:])
    return ids


# ------------------------------------------------------------------ fusione

def unisci_fonti(
    fonti: dict[str, RigaFonte],
) -> tuple[dict[str, Decimal | None], dict[str, str]]:
    """Riga fusa di UN esercizio (gemello di fn_bilanci_ricalcola_anno).

    Per ogni campo vince il primo valore non nullo in ordine di rango
    (xbrl > it_full > it_advanced): una fonte di rango inferiore non copre
    mai un valore di rango superiore, e un null non cancella nulla. Ritorna
    `(valori, fonte_per_campo)`: `valori` ha tutti i campi, `fonte_per_campo`
    solo quelli valorizzati."""
    for fonte in fonti:
        if fonte not in RANGO_FONTE:
            raise ValueError(f"fonte non valida: {fonte!r}")
    ordinate = sorted(fonti.items(), key=lambda voce: RANGO_FONTE[voce[0]], reverse=True)
    valori: dict[str, Decimal | None] = {}
    fonte_per_campo: dict[str, str] = {}
    for campo in CAMPI_BILANCIO:
        valori[campo] = None
        for fonte, riga in ordinate:
            valore = riga.valori.get(campo)
            if valore is not None:
                valori[campo] = valore
                fonte_per_campo[campo] = fonte
                break
    return valori, fonte_per_campo


def a_payload_rpc(righe: Iterable[RigaFonte]) -> list[dict]:
    """Righe nel formato di `p_righe` di fn_bilanci_registra_fonte: date ISO,
    decimali come STRINGHE in notazione fissa, campi nulli omessi."""
    payload: list[dict] = []
    for riga in righe:
        ignoti = set(riga.valori) - set(CAMPI_BILANCIO)
        if ignoti:
            raise ValueError(f"campi di bilancio sconosciuti: {sorted(ignoti)}")
        payload.append(
            {
                "anno": riga.anno,
                "data_chiusura": riga.data_chiusura.isoformat() if riga.data_chiusura else None,
                "tipo_bilancio": riga.tipo_bilancio,
                "ruolo": riga.ruolo,
                "valori": {
                    campo: _stringa_decimale(riga.valori[campo])
                    for campo in CAMPI_BILANCIO
                    if riga.valori.get(campo) is not None
                },
            }
        )
    return payload
