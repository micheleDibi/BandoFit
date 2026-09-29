"""Indicatori, fasce e regole finanziarie sui bilanci per esercizio (WP1).

Modulo PURO (nessun I/O, nessun LLM), tutto in `Decimal`.

- `calcola_indicatori`: indicatori già calcolati per UI, PDF e AI-check (il
  modello non fa aritmetica). Nei rapporti numeratore e denominatore vengono
  dallo STESSO esercizio: il più recente in cui esistono entrambi.
- `calcola_fasce`: fasce (codici, mai importi) di fatturato, patrimonio
  netto, dipendenti e trend: sono ciò che dei bilanci può uscire verso terzi.
- `valuta_regola_finanziaria`: esito deterministico di una
  `RegolaFinanziaria` (contratto in schemas/regole_finanziarie.py).

Valutazione su INTERVALLI. Ogni termine della regola è un intervallo: un
punto per i valori esatti (vista «proprio»), l'intervallo della sua FASCIA
per i bilanci in vista «terzi», il `costo_quota` così come arriva (può già
essere un intervallo di budget). La regola `num / den OP soglia` si valuta
come segno di `num − soglia × den` (con den > 0), senza divisioni: il
confronto è esatto anche al bordo (0,6 ≤ 0,6 è soddisfatto). L'esito è
`soddisfatto` solo se la regola vale in TUTTO il box, `non_soddisfatto` solo
se non vale in NESSUN punto, altrimenti `dato_mancante`. Gli estremi delle
fasce hanno la loro apertura (il fatturato 100k_500k esclude 100.000), così
l'esito sui terzi è esatto anche ai bordi.

Perché la vista terzi: con i valori esatti, chi controlla budget o quote
potrebbe ricavare per bisezione il fatturato di un'altra azienda rileggendo
gli esiti (CGC 7.3, decisione Q11). Sulla fascia, l'esito non rivela più
della fascia stessa.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from app.schemas.regole_finanziarie import RegolaFinanziaria
from app.services.bilanci_mapping import CAMPI_BILANCIO, CAMPI_CORE
from app.services.openapi_mapping import _FASCE, codice_fascia_fatturato

Esito = Literal["soddisfatto", "non_soddisfatto", "dato_mancante"]
Vista = Literal["proprio", "terzi"]

TREND_SOGLIA_PCT = 5

MOTIVO_DIPENDE_BUDGET = "dipende dal budget"
MOTIVO_DIPENDE_FASCIA = "dipende dalla fascia"
MOTIVO_STORICO_INCOMPLETO = "storico dei bilanci incompleto"

_INF = Decimal("Infinity")
_ZERO = Decimal(0)
_CENTO = Decimal(100)


# ---------------------------------------------------------------- esercizi

@dataclass
class EsercizioBilancio:
    """Riga fusa di un esercizio (company_financials) in Decimal."""

    anno: int
    data_chiusura: date | None = None
    valori: dict[str, Decimal | None] = field(default_factory=dict)
    fonti: dict[str, str] = field(default_factory=dict)
    tipo_bilancio: str = "ignoto"

    def valore(self, campo: str) -> Decimal | None:
        return self.valori.get(campo)

    @property
    def ha_core(self) -> bool:
        return any(self.valori.get(campo) is not None for campo in CAMPI_CORE)

    @classmethod
    def da_riga(cls, riga: dict) -> "EsercizioBilancio":
        """Da una riga di company_financials letta via PostgREST, dove i
        numeric arrivano come numeri JSON o stringhe."""
        valori: dict[str, Decimal | None] = {}
        for campo in CAMPI_BILANCIO:
            grezzo = riga.get(campo)
            valori[campo] = (
                None
                if grezzo is None or isinstance(grezzo, bool)
                else Decimal(str(grezzo))
            )
        chiusura = riga.get("data_chiusura")
        if isinstance(chiusura, str) and chiusura:
            chiusura = date.fromisoformat(chiusura[:10])
        elif not isinstance(chiusura, date):
            chiusura = None
        return cls(
            anno=int(riga["anno"]),
            data_chiusura=chiusura,
            valori=valori,
            fonti=dict(riga.get("fonte_per_campo") or {}),
            tipo_bilancio=riga.get("tipo_bilancio") or "ignoto",
        )


def _per_anno(esercizi) -> dict[int, EsercizioBilancio]:
    return {e.anno: e for e in esercizi}


def _media_fatturato(per_anno: dict[int, EsercizioBilancio], anno: int, n: int) -> Decimal | None:
    valori = []
    for a in range(anno - n + 1, anno + 1):
        esercizio = per_anno.get(a)
        valore = esercizio.valore("fatturato") if esercizio else None
        if valore is None:
            return None
        valori.append(valore)
    return sum(valori, _ZERO) / n


def _anni_con(per_anno: dict[int, EsercizioBilancio], campo: str) -> list[int]:
    return sorted(a for a, e in per_anno.items() if e.valore(campo) is not None)


def _q(valore: Decimal, cifre: str) -> Decimal:
    return valore.quantize(Decimal(cifre), rounding=ROUND_HALF_UP)


# -------------------------------------------------------------- indicatori

@dataclass
class Indicatore:
    """Stessi campi di schemas.bilanci.IndicatoreOut."""

    chiave: str
    etichetta: str
    valore: Decimal | None
    unita: str  # percentuale | euro | rapporto
    anni: list[int]
    formula: str
    motivo_mancanza: str | None


def _crescita(per_anno) -> Indicatore:
    base = dict(
        chiave="crescita_fatturato_pct",
        etichetta="Crescita del fatturato",
        unita="percentuale",
        formula="(fatturato − fatturato dell'esercizio precedente) / "
        "fatturato dell'esercizio precedente × 100",
    )
    anni = _anni_con(per_anno, "fatturato")
    for anno in reversed(anni):
        if anno - 1 in anni:
            corrente = per_anno[anno].valore("fatturato")
            precedente = per_anno[anno - 1].valore("fatturato")
            if precedente <= 0:
                return Indicatore(
                    **base, valore=None, anni=[anno - 1, anno],
                    motivo_mancanza="fatturato dell'esercizio precedente nullo o negativo",
                )
            valore = _q((corrente - precedente) / precedente * _CENTO, "0.01")
            return Indicatore(**base, valore=valore, anni=[anno - 1, anno], motivo_mancanza=None)
    motivo = (
        "servono i fatturati di due esercizi"
        if len(anni) < 2
        else "esercizi non consecutivi"
    )
    return Indicatore(**base, valore=None, anni=[], motivo_mancanza=motivo)


def _media(per_anno, n: int) -> Indicatore:
    base = dict(
        chiave=f"fatturato_medio_{n}",
        etichetta=f"Fatturato medio ({n} esercizi)",
        unita="euro",
        formula=f"media del fatturato degli ultimi {n} esercizi consecutivi",
    )
    anni = _anni_con(per_anno, "fatturato")
    for anno in reversed(anni):
        media = _media_fatturato(per_anno, anno, n)
        if media is not None:
            return Indicatore(
                **base, valore=_q(media, "0.01"),
                anni=list(range(anno - n + 1, anno + 1)), motivo_mancanza=None,
            )
    motivo = (
        f"servono i fatturati di {n} esercizi"
        if len(anni) < n
        else "esercizi non consecutivi"
    )
    return Indicatore(**base, valore=None, anni=[], motivo_mancanza=motivo)


def _rapporto(
    per_anno, *, chiave: str, etichetta: str, formula: str, num: str, den: str,
    unita: str, etichetta_den: str, etichette_mancanti: str,
) -> Indicatore:
    base = dict(chiave=chiave, etichetta=etichetta, unita=unita, formula=formula)
    for anno in sorted(per_anno, reverse=True):
        numeratore = per_anno[anno].valore(num)
        denominatore = per_anno[anno].valore(den)
        if numeratore is None or denominatore is None:
            continue
        if denominatore <= 0:
            return Indicatore(
                **base, valore=None, anni=[anno],
                motivo_mancanza=f"{etichetta_den} nullo o negativo",
            )
        rapporto = numeratore / denominatore
        if unita == "percentuale":
            valore = _q(rapporto * _CENTO, "0.01")
        else:
            valore = _q(rapporto, "0.0001")
        return Indicatore(**base, valore=valore, anni=[anno], motivo_mancanza=None)
    return Indicatore(
        **base, valore=None, anni=[],
        motivo_mancanza=f"servono {etichette_mancanti} dello stesso esercizio",
    )


CHIAVI_INDICATORI = (
    "crescita_fatturato_pct",
    "fatturato_medio_2",
    "fatturato_medio_3",
    "indipendenza_finanziaria",
    "mol_su_fatturato",
    "oneri_finanziari_su_fatturato",
    "copertura_immobilizzazioni",
)


def calcola_indicatori(esercizi) -> list[Indicatore]:
    """Indicatori nell'ordine di CHIAVI_INDICATORI. Un indicatore non
    calcolabile ha `valore=None` e il motivo in `motivo_mancanza`."""
    per_anno = _per_anno(esercizi)
    return [
        _crescita(per_anno),
        _media(per_anno, 2),
        _media(per_anno, 3),
        _rapporto(
            per_anno, chiave="indipendenza_finanziaria", etichetta="Indipendenza finanziaria",
            formula="patrimonio netto / totale attivo", num="patrimonio_netto",
            den="totale_attivo", unita="rapporto", etichetta_den="totale attivo",
            etichette_mancanti="patrimonio netto e totale attivo",
        ),
        _rapporto(
            per_anno, chiave="mol_su_fatturato", etichetta="MOL su fatturato",
            formula="MOL (EBITDA) / fatturato × 100", num="ebitda", den="fatturato",
            unita="percentuale", etichetta_den="fatturato",
            etichette_mancanti="MOL e fatturato",
        ),
        _rapporto(
            per_anno, chiave="oneri_finanziari_su_fatturato",
            etichetta="Oneri finanziari su fatturato",
            formula="oneri finanziari / fatturato × 100", num="oneri_finanziari",
            den="fatturato", unita="percentuale", etichetta_den="fatturato",
            etichette_mancanti="oneri finanziari e fatturato",
        ),
        Indicatore(
            chiave="copertura_immobilizzazioni",
            etichetta="Copertura delle immobilizzazioni",
            valore=None,
            unita="rapporto",
            anni=[],
            formula="patrimonio netto / totale immobilizzazioni",
            motivo_mancanza="il totale delle immobilizzazioni non è ancora disponibile "
            "dalle fonti usate",
        ),
    ]


# ------------------------------------------------------------------- fasce

# Ogni fascia: (codice, estremo inferiore, incluso?, estremo superiore, incluso?).
# None = illimitato. Il fatturato riusa l'enum di company_profiles.fascia_fatturato
# (openapi_mapping._FASCE: «≤ limite», quindi inferiore escluso e superiore incluso).
_Fascia = tuple[str, Decimal | None, bool, Decimal | None, bool]


def _fasce_fatturato() -> tuple[_Fascia, ...]:
    fasce: list[_Fascia] = []
    precedente: Decimal | None = None
    for limite, codice in _FASCE:
        fasce.append(
            (codice, precedente or _ZERO, precedente is None, Decimal(limite), True)
        )
        precedente = Decimal(limite)
    fasce.append(("oltre_50m", precedente, False, None, False))
    return tuple(fasce)


FASCE_FATTURATO: tuple[_Fascia, ...] = _fasce_fatturato()

FASCE_PATRIMONIO_NETTO: tuple[_Fascia, ...] = (
    ("negativo", None, False, _ZERO, False),
    ("fino_100k", _ZERO, True, Decimal(100_000), True),
    ("100k_500k", Decimal(100_000), False, Decimal(500_000), True),
    ("500k_2m", Decimal(500_000), False, Decimal(2_000_000), True),
    ("2m_10m", Decimal(2_000_000), False, Decimal(10_000_000), True),
    ("oltre_10m", Decimal(10_000_000), False, None, False),
)

# Il numero medio di dipendenti può essere frazionario (ULA): 0 < d < 10 → 1_9.
FASCE_DIPENDENTI: tuple[_Fascia, ...] = (
    ("0", _ZERO, True, _ZERO, True),
    ("1_9", _ZERO, False, Decimal(10), False),
    ("10_49", Decimal(10), True, Decimal(50), False),
    ("50_249", Decimal(50), True, Decimal(250), False),
    ("250_oltre", Decimal(250), True, None, False),
)

# Scala di fascia per ogni variabile di bilancio (vista terzi). Gli importi
# non negativi usano le fasce del fatturato, quelli con segno quelle del PN.
_SCALA_VARIABILE: dict[str, tuple[_Fascia, ...]] = {
    "fatturato": FASCE_FATTURATO,
    "fatturato_medio_2": FASCE_FATTURATO,
    "fatturato_medio_3": FASCE_FATTURATO,
    "valore_produzione": FASCE_FATTURATO,
    "capitale_sociale": FASCE_FATTURATO,
    "totale_attivo": FASCE_FATTURATO,
    "debiti_totali": FASCE_FATTURATO,
    "disponibilita_liquide": FASCE_FATTURATO,
    "oneri_finanziari": FASCE_FATTURATO,
    "costo_personale": FASCE_FATTURATO,
    "patrimonio_netto": FASCE_PATRIMONIO_NETTO,
    "risultato_esercizio": FASCE_PATRIMONIO_NETTO,
    "mol": FASCE_PATRIMONIO_NETTO,
    "ebit": FASCE_PATRIMONIO_NETTO,
    "dipendenti": FASCE_DIPENDENTI,
}


def _contiene(fascia: _Fascia, valore: Decimal) -> bool:
    _codice, lo, lo_incluso, hi, hi_incluso = fascia
    if lo is not None and (valore < lo or (valore == lo and not lo_incluso)):
        return False
    if hi is not None and (valore > hi or (valore == hi and not hi_incluso)):
        return False
    return True


def _codice_fascia(scala: tuple[_Fascia, ...], valore: Decimal | None) -> str | None:
    if valore is None:
        return None
    return next((f[0] for f in scala if _contiene(f, valore)), None)


def intervallo_fascia(variabile: str, codice_fascia: str) -> tuple[Decimal | None, Decimal | None]:
    """Estremi `(minimo, massimo)` della fascia (None = illimitato).

    L'inclusione degli estremi segue le tabelle FASCE_*: per il fatturato
    l'estremo inferiore è escluso e il superiore incluso («≤ limite»), come
    l'enum di company_profiles. ValueError su variabile o codice ignoti."""
    scala = _SCALA_VARIABILE.get(variabile)
    if scala is None:
        raise ValueError(f"variabile senza fasce: {variabile!r}")
    for codice, lo, _lo_incl, hi, _hi_incl in scala:
        if codice == codice_fascia:
            return lo, hi
    raise ValueError(f"fascia sconosciuta per {variabile}: {codice_fascia!r}")


@dataclass
class Fasce:
    """Stessi campi di schemas.bilanci.FasceOut."""

    fatturato: str | None
    patrimonio_netto: str | None
    dipendenti: str | None
    trend_fatturato: str | None
    anno_riferimento: int | None


def _ultimo(per_anno, campo: str) -> tuple[int | None, Decimal | None]:
    anni = _anni_con(per_anno, campo)
    if not anni:
        return None, None
    return anni[-1], per_anno[anni[-1]].valore(campo)


def calcola_fasce(esercizi) -> Fasce:
    """Fasce sull'ultimo esercizio che ha il dato. `anno_riferimento` è
    l'anno della fascia di fatturato (in mancanza, il più recente tra quelli
    usati dalle altre fasce). Trend: crescita del fatturato oltre
    ±TREND_SOGLIA_PCT tra l'anno della fascia di fatturato e il precedente,
    altrimenti stabile; nessun trend se quella coppia non c'è (una crescita
    di anni prima non descrive l'andamento attuale)."""
    per_anno = _per_anno(esercizi)
    anno_fatt, fatturato = _ultimo(per_anno, "fatturato")
    anno_pn, patrimonio = _ultimo(per_anno, "patrimonio_netto")
    anno_dip, dipendenti = _ultimo(per_anno, "dipendenti")

    indicatore_crescita = _crescita(per_anno)
    crescita = (
        indicatore_crescita.valore
        if indicatore_crescita.anni and indicatore_crescita.anni[-1] == anno_fatt
        else None
    )
    trend = None
    if crescita is not None:
        if crescita > TREND_SOGLIA_PCT:
            trend = "crescita"
        elif crescita < -TREND_SOGLIA_PCT:
            trend = "calo"
        else:
            trend = "stabile"

    fasce = Fasce(
        fatturato=codice_fascia_fatturato(fatturato),
        patrimonio_netto=_codice_fascia(FASCE_PATRIMONIO_NETTO, patrimonio),
        dipendenti=_codice_fascia(FASCE_DIPENDENTI, dipendenti),
        trend_fatturato=trend,
        anno_riferimento=None,
    )
    if fasce.fatturato is not None:
        fasce.anno_riferimento = anno_fatt
    else:
        anni = [
            a for a, codice in ((anno_pn, fasce.patrimonio_netto), (anno_dip, fasce.dipendenti))
            if codice is not None
        ]
        fasce.anno_riferimento = max(anni) if anni else None
    return fasce


# ------------------------------------------------------ aritmetica intervalli

# Intervallo: (lo, lo_incluso, hi, hi_incluso), estremi Decimal (±Infinity =
# illimitato, mai incluso). Serve solo per regole multilineari in cui ogni
# variabile compare una volta: gli estremi sono esatti ai vertici del box.
_Intervallo = tuple[Decimal, bool, Decimal, bool]


def _punto(valore: Decimal) -> _Intervallo:
    return (valore, True, valore, True)


def _da_fascia(fascia: _Fascia) -> _Intervallo:
    _codice, lo, lo_incl, hi, hi_incl = fascia
    return (
        -_INF if lo is None else lo,
        lo is not None and lo_incl,
        _INF if hi is None else hi,
        hi is not None and hi_incl,
    )


def _prodotto_estremi(a: Decimal, a_incl: bool, b: Decimal, b_incl: bool) -> tuple[Decimal, bool]:
    if (a == 0 and a_incl) or (b == 0 and b_incl):
        return _ZERO, True
    if a == 0 or b == 0:
        # 0 escluso per ∞: il limite è 0, non raggiunto.
        return _ZERO, False
    return a * b, a_incl and b_incl and a.is_finite() and b.is_finite()


def _mul(x: _Intervallo, y: _Intervallo) -> _Intervallo:
    vertici = [
        _prodotto_estremi(x[0], x[1], y[0], y[1]),
        _prodotto_estremi(x[0], x[1], y[2], y[3]),
        _prodotto_estremi(x[2], x[3], y[0], y[1]),
        _prodotto_estremi(x[2], x[3], y[2], y[3]),
    ]
    lo = min(v for v, _ in vertici)
    hi = max(v for v, _ in vertici)
    return (
        lo,
        any(incl for v, incl in vertici if v == lo),
        hi,
        any(incl for v, incl in vertici if v == hi),
    )


def _differenza(a: Decimal, b: Decimal, *, infimo: bool) -> Decimal:
    """`a − b` per un estremo dell'intervallo. ∞ − ∞ (stesso segno) non è
    determinato: capita solo valutando come punto un estremo illimitato (un
    budget «oltre 5 milioni» contro una fascia aperta). L'estremo diventa
    illimitato nella direzione prudente: l'intervallo si allarga, quindi
    l'esito non può diventare falsamente certo (resta `dato_mancante`)."""
    if a.is_infinite() and b.is_infinite() and a == b:
        return -_INF if infimo else _INF
    return a - b


def _sub(x: _Intervallo, y: _Intervallo) -> _Intervallo:
    lo = _differenza(x[0], y[2], infimo=True)
    hi = _differenza(x[2], y[0], infimo=False)
    return (lo, x[1] and y[3] and lo.is_finite(), hi, x[3] and y[1] and hi.is_finite())


def _solo_positivo(x: _Intervallo) -> bool:
    return x[0] > 0 or (x[0] == 0 and not x[1])


def _nessun_positivo(x: _Intervallo) -> bool:
    return x[2] <= 0


def _decidi(g: _Intervallo, operatore: str) -> Esito:
    """Esito di `g OP 0` su tutto l'intervallo di g."""
    lo, lo_incl, hi, hi_incl = g
    if operatore == "le":
        sempre, mai = hi <= 0, lo > 0 or (lo == 0 and not lo_incl)
    elif operatore == "lt":
        sempre, mai = hi < 0 or (hi == 0 and not hi_incl), lo >= 0
    elif operatore == "ge":
        sempre, mai = lo >= 0, hi < 0 or (hi == 0 and not hi_incl)
    else:  # gt
        sempre, mai = lo > 0 or (lo == 0 and not lo_incl), hi <= 0
    if sempre:
        return "soddisfatto"
    if mai:
        return "non_soddisfatto"
    return "dato_mancante"


# ------------------------------------------------------------------ regole

_ETICHETTE_VARIABILI: dict[str, str] = {
    "fatturato": "fatturato",
    "fatturato_medio_2": "fatturato medio (2 esercizi)",
    "fatturato_medio_3": "fatturato medio (3 esercizi)",
    "valore_produzione": "valore della produzione",
    "risultato_esercizio": "risultato d'esercizio",
    "patrimonio_netto": "patrimonio netto",
    "capitale_sociale": "capitale sociale",
    "totale_attivo": "totale attivo",
    "debiti_totali": "debiti totali",
    "disponibilita_liquide": "disponibilità liquide",
    "mol": "MOL",
    "ebit": "EBIT",
    "oneri_finanziari": "oneri finanziari",
    "costo_personale": "costo del personale",
    "dipendenti": "dipendenti",
    "bilanci_approvati_n": "bilanci approvati",
    "costo_quota": "costo della quota",
    "contributo_quota": "contributo della quota",
    "costo_progetto_totale": "costo totale del progetto",
}

_SIMBOLI = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥"}

# Variabile → colonna di company_financials (per esercizio).
_COLONNA_ANNUALE: dict[str, str] = {
    "fatturato": "fatturato",
    "valore_produzione": "valore_produzione",
    "risultato_esercizio": "risultato_esercizio",
    "patrimonio_netto": "patrimonio_netto",
    "capitale_sociale": "capitale_sociale",
    "totale_attivo": "totale_attivo",
    "debiti_totali": "debiti_totali",
    "disponibilita_liquide": "disponibilita_liquide",
    "mol": "ebitda",
    "ebit": "ebit",
    "oneri_finanziari": "oneri_finanziari",
    "costo_personale": "costo_personale",
    "dipendenti": "dipendenti",
}
_MEDIE: dict[str, int] = {"fatturato_medio_2": 2, "fatturato_medio_3": 3}
_CONTEGGI = frozenset({"dipendenti", "bilanci_approvati_n"})
# Parametri del partenariato che questa valutazione non riceve.
_NON_SUPPORTATE = frozenset({"contributo_quota", "costo_progetto_totale"})


def _leggi_decimale(testo: Any) -> Decimal | None:
    """Decimale di una regola: formato macchina (`"0.6"`), tollerata la sola
    virgola decimale (`"0,6"`). Separatori delle migliaia e valori non finiti
    sono rifiutati: meglio una regola non valida che una soglia sbagliata."""
    if not isinstance(testo, str):
        return None
    pulito = testo.strip()
    if "," in pulito:
        if "." in pulito or pulito.count(",") > 1:
            return None
        pulito = pulito.replace(",", ".")
    try:
        valore = Decimal(pulito)
    except InvalidOperation:
        return None
    return valore if valore.is_finite() else None


def valida_regola(regola: RegolaFinanziaria) -> list[str]:
    """Errori di coerenza della regola (lista vuota = valida)."""
    errori: list[str] = []
    if (regola.soglia is None) == (regola.soglia_variabile is None):
        errori.append("serve esattamente una tra soglia e soglia_variabile")
    if regola.soglia is not None and _leggi_decimale(regola.soglia) is None:
        errori.append("soglia non è un numero decimale")
    if regola.soglia_coefficiente is not None:
        if regola.soglia_variabile is None:
            errori.append("soglia_coefficiente ha senso solo con soglia_variabile")
        coefficiente = _leggi_decimale(regola.soglia_coefficiente)
        if coefficiente is None:
            errori.append("soglia_coefficiente non è un numero decimale")
        elif coefficiente <= 0:
            errori.append("soglia_coefficiente deve essere positivo")
    if regola.denominatore is not None and regola.denominatore == regola.numeratore:
        errori.append("numeratore e denominatore coincidono")
    if regola.soglia_variabile is not None and regola.soglia_variabile in (
        regola.numeratore,
        regola.denominatore,
    ):
        errori.append("soglia_variabile coincide con un termine del rapporto")
    if regola.denominatore is not None:
        attesa = "rapporto"
    elif regola.numeratore in _CONTEGGI:
        attesa = "numero"
    else:
        attesa = "euro"
    if regola.unita != attesa:
        errori.append(f"unità incoerente: attesa «{attesa}»")
    return errori


@dataclass
class EsitoRegola:
    esito: Esito
    motivo: str | None
    spiegazione_titolare: str | None
    spiegazione_terzi: str
    anni_usati: list[int]


def _fmt(valore: Decimal, euro: bool = False) -> str:
    """Numero all'italiana (1.234.567,5), per la sola spiegazione al titolare."""
    arrotondato = _q(valore, "0.01") if euro else _q(valore, "0.0001")
    segno = "-" if arrotondato < 0 else ""
    intero, _, decimali = format(abs(arrotondato), "f").partition(".")
    intero = f"{int(intero):,}".replace(",", ".")
    decimali = decimali.rstrip("0")
    testo = segno + intero + ("," + decimali if decimali else "")
    return testo + (" €" if euro else "")


def _fmt_intervallo(intervallo: _Intervallo, euro: bool) -> str:
    lo, _li, hi, _hi = intervallo
    if not lo.is_finite() and not hi.is_finite():
        return "non determinato"
    if lo == hi:
        return _fmt(lo, euro)
    if not hi.is_finite():
        return f"almeno {_fmt(lo, euro)}"
    if not lo.is_finite():
        return f"al massimo {_fmt(hi, euro)}"
    return f"tra {_fmt(lo, euro)} e {_fmt(hi, euro)}"


def _anni_testo(anni: list[int]) -> str:
    if not anni:
        return ""
    if len(anni) == 1:
        return f" (esercizio {anni[0]})"
    return f" (esercizi {anni[0]}–{anni[-1]})"


_TESTO_ESITO = {
    "soddisfatto": "soddisfatto",
    "non_soddisfatto": "non soddisfatto",
    "dato_mancante": "dato mancante",
}


def _esito_mancante(
    motivo: str, anni: list[int] | None = None, vista: Vista = "proprio"
) -> EsitoRegola:
    anni = anni or []
    return EsitoRegola(
        esito="dato_mancante",
        motivo=motivo,
        spiegazione_titolare=(
            f"Dato mancante: {motivo}{_anni_testo(anni)}." if vista == "proprio" else None
        ),
        spiegazione_terzi=f"Esito: dato mancante ({motivo}).",
        anni_usati=anni,
    )


def _intervallo_costo(costo_quota) -> _Intervallo | None:
    """Il costo della quota come intervallo, None se non è usabile.

    Il minimo deve essere un numero finito; il massimo può essere +Infinity
    (fascia di budget senza tetto, es. `oltre_5m`) e allora è ESCLUSO, come
    ogni estremo illimitato. Estremi non numerici o NaN, un minimo infinito o
    entrambi gli estremi infiniti valgono come costo non noto: meglio un
    «dato mancante» che un esito su un costo che non esiste."""
    try:
        lo, hi = sorted(Decimal(str(estremo)) for estremo in costo_quota)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not lo.is_finite() or hi.is_nan():
        return None
    return (lo, True, hi, hi.is_finite())


def _anno_ancora(per_anno, variabili: list[str]) -> int | None:
    """Esercizio più recente in cui TUTTI i termini di bilancio esistono."""
    for anno in sorted(per_anno, reverse=True):
        ok = True
        for variabile in variabili:
            if variabile in _MEDIE:
                ok = _media_fatturato(per_anno, anno, _MEDIE[variabile]) is not None
            else:
                ok = per_anno[anno].valore(_COLONNA_ANNUALE[variabile]) is not None
            if not ok:
                break
        if ok:
            return anno
    return None


def _motivo_ancora_mancante(per_anno, variabili: list[str]) -> str:
    medie = [v for v in variabili if v in _MEDIE]
    if medie and len(_anni_con(per_anno, "fatturato")) >= max(_MEDIE[v] for v in medie):
        if all(v in _MEDIE for v in variabili):
            return "esercizi non consecutivi"
    etichette = " e ".join(_ETICHETTE_VARIABILI[v] for v in variabili)
    if len(variabili) > 1:
        return f"manca un esercizio con {etichette}"
    return f"manca il dato: {etichette}"


def valuta_regola_finanziaria(
    regola: RegolaFinanziaria,
    esercizi,
    costo_quota: tuple[Decimal, Decimal] | None,
    *,
    vista: Vista,
    storico_completo: bool = True,
) -> EsitoRegola:
    """Esito deterministico di una regola per UNA azienda.

    - `esercizi`: righe fuse (EsercizioBilancio) dell'azienda valutata;
    - `costo_quota`: intervallo `(minimo, massimo)` del costo della quota
      (un punto se il budget è esatto), None se non noto; il massimo può
      essere `Decimal("Infinity")` (fascia di budget senza tetto), il minimo
      deve essere finito (altrimenti vale come costo non noto);
    - `vista`: «proprio» valuta sui valori esatti (per il titolare); «terzi»
      sostituisce ogni variabile di bilancio con l'intervallo della sua
      fascia (per le medie, la fascia della media stessa; `bilanci_approvati_n`
      resta esatto: è un conteggio, non un importo) e non produce
      `spiegazione_titolare`;
    - `storico_completo`: `bilanci_approvati_n` conta gli esercizi PRESENTI.
      Se lo storico non è stato recuperato (es. solo IT-full perché
      IT-advanced è saltato), il conteggio è solo un minimo: con False vale
      come intervallo [n, ∞) e «≥ 2 bilanci» non diventa mai un falso
      `non_soddisfatto`.

    L'`ambito` della regola non entra qui: l'aggregazione tra partner è del
    validatore (WP8). In vista «proprio» `spiegazione_terzi` riflette l'esito
    sui valori esatti: ai terzi va mostrato solo l'esito della vista «terzi».
    """
    if valida_regola(regola):
        return _esito_mancante("regola non valida", vista=vista)

    variabili = [regola.numeratore]
    if regola.denominatore is not None:
        variabili.append(regola.denominatore)
    if regola.soglia_variabile is not None:
        variabili.append(regola.soglia_variabile)

    for variabile in variabili:
        if variabile in _NON_SUPPORTATE:
            return _esito_mancante(
                f"variabile non supportata: {_ETICHETTE_VARIABILI[variabile]}", vista=vista
            )
    costo: _Intervallo | None = None
    if "costo_quota" in variabili:
        costo = _intervallo_costo(costo_quota) if costo_quota is not None else None
        if costo is None:
            return _esito_mancante("manca il costo della quota", vista=vista)

    per_anno = {e.anno: e for e in esercizi if e.ha_core}
    if "bilanci_approvati_n" in variabili and not per_anno:
        return _esito_mancante("nessun bilancio disponibile", vista=vista)
    di_bilancio = [v for v in variabili if v in _COLONNA_ANNUALE or v in _MEDIE]
    anno = None
    anni_usati: set[int] = set()
    if di_bilancio:
        anno = _anno_ancora(per_anno, di_bilancio)
        if anno is None:
            return _esito_mancante(_motivo_ancora_mancante(per_anno, di_bilancio), vista=vista)

    esatti: dict[str, Decimal] = {}
    intervalli: dict[str, _Intervallo] = {}
    fasce_usate: list[str] = []
    for variabile in variabili:
        if variabile == "costo_quota":
            intervalli[variabile] = costo
            continue
        if variabile == "bilanci_approvati_n":
            esatti[variabile] = Decimal(len(per_anno))
            anni_usati.update(per_anno)
            if not storico_completo:
                intervalli[variabile] = (esatti[variabile], True, _INF, False)
                continue
        elif variabile in _MEDIE:
            n = _MEDIE[variabile]
            esatti[variabile] = _media_fatturato(per_anno, anno, n)
            anni_usati.update(range(anno - n + 1, anno + 1))
        else:
            esatti[variabile] = per_anno[anno].valore(_COLONNA_ANNUALE[variabile])
            anni_usati.add(anno)
        scala = _SCALA_VARIABILE.get(variabile)
        if vista == "terzi" and scala is not None:
            codice = _codice_fascia(scala, esatti[variabile])
            fascia = next((f for f in scala if f[0] == codice), None)
            intervalli[variabile] = (
                _da_fascia(fascia) if fascia else (-_INF, False, _INF, False)
            )
            etichetta = _ETICHETTE_VARIABILI[variabile]
            fasce_usate.append(f"{etichetta} {codice or 'non classificabile'}")
        else:
            intervalli[variabile] = _punto(esatti[variabile])
    anni = sorted(anni_usati)

    def esito_su(intervalli_eff: dict[str, _Intervallo]) -> Esito | str:
        num = intervalli_eff[regola.numeratore]
        if regola.soglia is not None:
            soglia = _punto(_leggi_decimale(regola.soglia))
        else:
            coefficiente = _leggi_decimale(regola.soglia_coefficiente or "1")
            soglia = _mul(_punto(coefficiente), intervalli_eff[regola.soglia_variabile])
        if regola.denominatore is None:
            return _decidi(_sub(num, soglia), regola.operatore)
        den = intervalli_eff[regola.denominatore]
        if _nessun_positivo(den):
            return "denominatore_non_positivo"
        if not _solo_positivo(den):
            return "denominatore_incerto"
        return _decidi(_sub(num, _mul(soglia, den)), regola.operatore)

    esito = esito_su(intervalli)
    motivo: str | None = None
    if esito == "denominatore_non_positivo":
        motivo = "denominatore nullo o negativo"
        esito = "dato_mancante"
    elif esito in ("denominatore_incerto", "dato_mancante"):
        esito = "dato_mancante"
        motivo = _motivo_incertezza(esito_su, intervalli, vista)

    return EsitoRegola(
        esito=esito,
        motivo=motivo,
        spiegazione_titolare=(
            _spiegazione_titolare(regola, esito, motivo, esatti, intervalli, anni)
            if vista == "proprio"
            else None
        ),
        spiegazione_terzi=_spiegazione_terzi(esito, motivo, fasce_usate, anni),
        anni_usati=anni,
    )


def _motivo_incertezza(esito_su, intervalli: dict[str, _Intervallo], vista: Vista) -> str:
    """Perché l'esito non è determinato:
    - «dipende dal budget» se, fissato il costo della quota a ciascuno dei
      suoi estremi, l'esito diventa determinato;
    - «storico dei bilanci incompleto» se il conteggio dei bilanci è solo un
      minimo;
    - altrimenti «dipende dalla fascia» (in vista «proprio» i bilanci sono
      esatti: resta il budget).
    Usa soltanto gli intervalli già calcolati, quindi in vista «terzi» non
    rivela nulla oltre alle fasce."""
    costo = intervalli.get("costo_quota")
    if costo is not None and costo[0] != costo[2]:
        estremi = [
            esito_su({**intervalli, "costo_quota": _punto(valore)})
            for valore in (costo[0], costo[2])
        ]
        if all(e in ("soddisfatto", "non_soddisfatto") for e in estremi):
            return MOTIVO_DIPENDE_BUDGET
    conteggio = intervalli.get("bilanci_approvati_n")
    if conteggio is not None and conteggio[0] != conteggio[2]:
        return MOTIVO_STORICO_INCOMPLETO
    if vista == "proprio":
        return MOTIVO_DIPENDE_BUDGET
    return MOTIVO_DIPENDE_FASCIA


def _spiegazione_titolare(
    regola: RegolaFinanziaria,
    esito: Esito,
    motivo: str | None,
    esatti: dict[str, Decimal],
    intervalli: dict[str, _Intervallo],
    anni: list[int],
) -> str:
    def termine(variabile: str) -> str:
        euro = variabile not in _CONTEGGI
        return f"{_ETICHETTE_VARIABILI[variabile]} {_fmt_intervallo(intervalli[variabile], euro)}"

    parti = termine(regola.numeratore)
    if regola.denominatore is not None:
        parti += f" / {termine(regola.denominatore)}"
        num, den = intervalli[regola.numeratore], intervalli[regola.denominatore]
        if (
            num[0] == num[2] and den[0] == den[2] and den[0] > 0
            and num[0].is_finite() and den[0].is_finite()
        ):
            parti += f" = {_fmt(num[0] / den[0])}"
    simbolo = _SIMBOLI[regola.operatore]
    if regola.soglia is not None:
        soglia = _fmt(_leggi_decimale(regola.soglia), euro=regola.unita == "euro")
    else:
        coefficiente = _leggi_decimale(regola.soglia_coefficiente or "1")
        soglia = f"{_fmt(coefficiente)} × {termine(regola.soglia_variabile)}"
    testo = f"{parti}; richiesto {simbolo} {soglia}"
    if esito == "dato_mancante":
        return f"Dato mancante ({motivo}): {testo}{_anni_testo(anni)}."
    return f"{_TESTO_ESITO[esito].capitalize()}: {testo}{_anni_testo(anni)}."


def _spiegazione_terzi(
    esito: Esito, motivo: str | None, fasce_usate: list[str], anni: list[int]
) -> str:
    """Solo esito, motivo generico e codici di fascia: MAI importi."""
    testo = f"Esito: {_TESTO_ESITO[esito]}"
    if motivo:
        testo += f" ({motivo})"
    testo += "."
    if fasce_usate:
        testo += f" Valutato sulle fasce: {', '.join(fasce_usate)}{_anni_testo(anni)}."
    return testo
