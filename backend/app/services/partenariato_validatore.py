"""Validatore deterministico del consorzio della call (WP8, docs/partenariati.md
V2-V3, Q11, Q17, Q20).

Modulo PURO: nessun I/O, nessun modello. Legge SOLO lo snapshot delle regole
confermate dal creatore (`partner_calls.regole_partenariato`), i requisiti
della call e i membri già letti dal servizio. Coperture, regole finanziarie e
collegamenti li decidono le funzioni esistenti, qui non si duplicano:
`partenariato_criteri.valuta_criterio` (tipi di soggetto, sedi con la regola
«tutte le sedi», requisiti), `bilanci_indicatori.valuta_regola_finanziaria`
(vista «proprio» sui valori esatti, «terzi» sulle fasce) e
`partenariato_collegamenti.valuta_collegamento` (soglie Q17).

`valida_consorzio` produce una checklist di voci verde / rossa / grigia, ogni
voce con la regola di origine (voce dello snapshot confermata con la sua
citazione → «bando»; modificata, aggiunta o scelta del creatore →
«creatore»; nessuna regola per i controlli di coerenza). Una voce esiste se
la regola c'è nello snapshot o se è un controllo sempre valido (somma delle
quote, indipendenza, requisiti «di ogni membro»); se la regola c'è ma il
bando non ne indica il valore decisivo (quanti soggetti, quanti paesi, se i
partner devono essere indipendenti) la voce è grigia «Il bando non lo
indica». Controlli:
- `numero_partner`: min/max su capofila e partner (entità affiliate e partner
  associati non contano, appendice A); senza un minimo nel bando, controllo
  di coerenza «almeno 2 tra capofila e partner»;
- `composizione`: per voce, i membri col tipo di soggetto, il ruolo e il
  vincolo territoriale; ogni membro è sì / no / da verificare, e l'esito è
  certo solo se lo è con qualunque scelta dei «da verificare». Gli esterni
  contano con i tipi dichiarati dal creatore, e la voce si marca
  «dichiarato» come per i tipi dichiarati nel profilo partner;
- `somma_quote` = 100 con tolleranza 0,01 (una quota mancante → grigio) sui
  membri che ricevono budget: i partner associati non contano (la loro quota,
  se c'è, si ignora e confermano anche senza);
- `quota_partner` (capofila e partner), `quota_categoria` (somma delle quote
  dei membri della categoria, esclusi i partner associati), `quota_capofila`;
  se la violazione fa perdere solo una maggiorazione il rosso diventa grigio
  (il consorzio resta ammissibile). Con la categoria, `quota_partner` vale
  solo per i membri di quel tipo (nessuno → verde; un membro di tipo incerto
  dà grigio solo se la quota manca o viola i limiti) e `quota_capofila` è
  grigia se il capofila non è di quel tipo o se il suo tipo è incerto, anche
  con la quota nei limiti;
- `indipendenza` su TUTTE le coppie di membri in piattaforma tranne le
  entità affiliate (collegate per definizione al beneficiario):
  collegamento certo → rosso (grigio se il bando non chiede
  l'indipendenza), possibile → grigio; chiavi non calcolate (marker non
  valido) o membri esterni → grigio «da verificare con visura». Il
  dettaglio è sempre generico: mai quale socio o esponente collega due
  membri;
- `paesi_distinti` (vincolo con il numero di paesi);
- `regola_finanziaria`: `ciascun_partner` sulla quota di ogni beneficiario
  (capofila, partner, entità affiliate) e `capofila`, con costo della quota =
  quota × budget (esatto se c'è, altrimenti l'intervallo della fascia);
  `partenariato_totale` → grigio (da verificare a mano);
- `media_pesata`: solo se ogni membro ha la quota, altrimenti grigio; verso
  tutti SOLO l'esito. Senza i valori esatti degli altri la media non si
  calcola: si usa la convessità della media pesata. Se la regola vale per
  OGNI membro su tutto il suo intervallo, vale per qualunque media pesata;
  se non vale per nessuno, non vale per la media; altrimenti grigio;
- `esclusivita`: un membro in piattaforma con un impegno su un'altra call
  dello stesso bando, quando questa o quella è esclusiva (stessa regola di
  `fn_partner_esclusivita_violata`); con la call esclusiva gli esterni → grigio;
- `vincolo_membro`: requisiti della call con ambito `ogni_membro` su ogni
  beneficiario; quelli finanziari si saltano solo se la stessa regola è
  nello snapshot (già valutata lì), altrimenti si valutano qui come regola
  finanziaria;
- `vincolo_da_verificare`: vincoli del bando solo testuali
  (`requisito_capofila`, `altro`) e la sede in una regione
  (`sede_operativa_regione`) quando nessun requisito «di ogni membro» sulla
  regione lo traduce; `costituzione_entro` no: è una scadenza, non una
  regola sulla composizione (la ricorda la checklist dei documenti);
- `membri_attivi`: un membro in piattaforma non più attivo (azienda
  eliminata o archiviata, titolare disattivato) → grigio, e sui suoi dati non
  si valuta nulla.
Esito complessivo: rosso se c'è un rosso, altrimenti grigio se c'è un grigio,
altrimenti verde.

PROIEZIONE PER DESTINATARIO (Q11, T3). Le voci che si valutano membro per
membro conservano per ciascuno l'esito in vista «terzi» (fasce: lo vedono
tutti) e in vista «proprio» (valori esatti: solo il membro stesso);
`proietta_validazione` ricompone esito, dettaglio e membri coinvolti per chi
guarda, e gli dà solo il PROPRIO `dettaglio_privato`. Così nessun esito
visto da un altro dipende da valori esatti che non sono suoi, e il
creatore, che controlla budget e quote, non può ricavare per bisezione più
della fascia di un membro. `Validazione.esito` è l'esito nella vista del
creatore: è quello da persistere in `partner_calls.validazione_esito`. Per
indipendenza ed esclusività chi non ha creato la call vede tra i coinvolti
solo sé stesso.

Matrice di copertura (V3): `matrice_copertura` valuta ogni requisito della
call su ogni membro con la stessa `valuta_criterio`; `proietta_matrice` dà la
vista «proprio» nella colonna del destinatario e «terzi» nelle altre.
`MatriceCopertura.copertura_gap_ratio` (vista del creatore) va in
`partner_calls.copertura_gap_ratio`.
"""

import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Literal

from pydantic import ValidationError

from app.core.errors import AppError
from app.schemas.partenariato_consorzio import (
    TOLLERANZA_QUOTE,
    CellaMatriceOut,
    CodiceVoce,
    EsitoMembroOut,
    EsitoVoce,
    FonteRegola,
    MatriceOut,
    RegolaOrigineOut,
    RiepilogoValidazioneOut,
    RigaMatriceOut,
    ValidazioneOut,
    VoceOut,
)
from app.schemas.partenariato_criteri import (
    CriterioManuale,
    CriterioPaese,
    CriterioPartner,
    CriterioRegione,
    CriterioRegolaFinanziaria,
    CriterioTipoSoggetto,
    EsitoCriterio,
    Intervallo,
    ProfiloCandidato,
)
from app.schemas.partner_call import (
    CitazioneIn,
    ComposizioneSnapshot,
    QuotaSnapshot,
    RegolaFinanziariaSnapshot,
    RegoleCallSnapshot,
    VincoloSnapshot,
)
from app.schemas.partner_profile import PAESI_ISO2
from app.schemas.regole_finanziarie import RegolaFinanziaria
from app.services import partenariato_vocabolario as voc
from app.services.bilanci_indicatori import valuta_regola_finanziaria
from app.services.partenariato_collegamenti import (
    TESTO_COLLEGAMENTO,
    ChiaveCollegamento,
    chiavi_da_righe,
    valuta_collegamento,
)
from app.services.partenariato_criteri import (
    criterio_da_json,
    intervallo_costo_quota,
    testo_pubblico,
    valuta_criterio,
)

logger = logging.getLogger("bandofit.partenariati")

# Chi conta per numero di partner, quote per partner e paesi (appendice A:
# entità affiliate e partner associati non contano).
RUOLI_CONTEGGIO: frozenset[str] = frozenset({"capofila", "partner"})
# A chi si applicano somma delle quote, quote per categoria, regole
# finanziarie per partner e requisiti «di ogni membro»: i partner associati
# non ricevono budget.
RUOLI_BENEFICIARI: frozenset[str] = frozenset({"capofila", "partner", "affiliated_entity"})
# Indipendenza: tutte le coppie tranne le entità affiliate, collegate per
# definizione al beneficiario a cui sono affiliate.
RUOLI_INDIPENDENZA: frozenset[str] = frozenset({"capofila", "partner", "associated_partner"})
# Senza un minimo nel bando, un consorzio ha almeno due tra capofila e partner.
MINIMO_COERENZA = 2
_CAMPI_REGOLA = ("id", "descrizione", "ambito", "numeratore", "denominatore", "operatore",
                 "soglia", "soglia_variabile", "soglia_coefficiente", "unita")

TESTO_NON_INDICA = "Il bando non lo indica"

_ALIAS_PAESI = {"EL": "GR", "UK": "GB"}
_MILLESIMI = Decimal("0.001")

Aggregazione = Literal["ciascuno", "media"]
_Tri = Literal["si", "no", "forse"]


# ------------------------------------------------------------------ input


@dataclass(frozen=True)
class RegolaOrigine:
    """Da dove viene la regola di una voce (vedi `RegolaOrigineOut`)."""

    fonte: FonteRegola
    citazione: CitazioneIn | None = None


REGOLA_CREATORE = RegolaOrigine("creatore")


@dataclass(frozen=True)
class MembroSnapshot:
    """Un membro NON uscito (gli usciti si ignorano) come lo legge il
    servizio. Nessun `company_profile_id`: il validatore ragiona per id del
    membro, l'unico handle che esce.

    - `profilo`: dati deterministici dell'azienda
      (`partenariato_criteri.profilo_candidato_da`, con gli esercizi ESATTI:
      le fasce le calcola la vista «terzi»); None per gli esterni e per un
      membro in piattaforma i cui dati non si sono potuti leggere (tutto
      grigio);
    - esterni (Q20): paese e tipi di soggetto dichiarati dal creatore;
    - `collegamenti`: chiavi di `company_collegamenti` se il marker è valido
      (`partenariato_collegamenti.marker_aggiornato`), None se non calcolate;
    - `impegni_altrove`: per ogni ALTRO impegno dell'azienda sullo stesso
      bando (call pubblicata propria, o non annullata con altri membri;
      candidatura accettata su una call non annullata, membro non uscito di
      un'altra call; mai questa call), se quella call è esclusiva. None =
      non noto;
    - `attivo`: falso per un'azienda in piattaforma non più viva (il servizio
      allora non passa né profilo né chiavi)."""

    id: str
    ruolo: str
    stato: str = "proposto"
    quota: Decimal | None = None
    creatore: bool = False
    esterno: bool = False
    profilo: ProfiloCandidato | None = None
    esterno_paese: str | None = None
    esterno_tipi: tuple[str, ...] = ()
    collegamenti: tuple[ChiaveCollegamento, ...] | None = None
    impegni_altrove: tuple[bool, ...] | None = ()
    posizione_id: str | None = None
    attivo: bool = True


@dataclass(frozen=True)
class RequisitoConsorzio:
    """Un requisito della call. `etichetta` e `testo` sono già ripuliti per
    chi vede il consorzio (il servizio passa `partenariato_accesso.
    testo_pubblico` del creatore): finiscono in voci e matrice."""

    id: str
    etichetta: str
    testo: str | None = None
    criterio: CriterioPartner | None = None
    ambito: str = "consorzio"
    cercato: bool = False
    regola: RegolaOrigine | None = None


@dataclass(frozen=True)
class CallConsorzio:
    id: str
    esclusivita: bool = False
    requisiti: tuple[RequisitoConsorzio, ...] = ()


def _testo(valore: Any) -> str | None:
    return valore.strip() if isinstance(valore, str) and valore.strip() else None


def _n(numero: int, singolare: str, plurale: str) -> str:
    """«1 membro», «3 membri»: i testi delle voci li legge l'utente."""
    return f"{numero} {singolare if numero == 1 else plurale}"


def _decimale(valore: Any) -> Decimal | None:
    if valore is None or isinstance(valore, bool):
        return None
    try:
        numero = Decimal(str(valore))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() else None


def _citazione(dato: Any) -> CitazioneIn | None:
    if isinstance(dato, CitazioneIn):
        return dato
    if not isinstance(dato, Mapping):
        return None
    try:
        return CitazioneIn.model_validate(dato)
    except ValidationError:
        return None


def membro_da_riga(
    riga: Mapping,
    *,
    creatore_company_id: Any,
    profilo: ProfiloCandidato | None = None,
    collegamenti: Iterable[ChiaveCollegamento | Mapping] | None = None,
    impegni_altrove: Iterable[bool] | None = (),
    attivo: bool = True,
) -> MembroSnapshot:
    """`MembroSnapshot` da una riga di `partner_call_membri` (funzione pura).
    `collegamenti` accetta anche le righe di `company_collegamenti`; None =
    non calcolati (marker assente o vecchio)."""
    company = riga.get("company_profile_id")
    esterno = company is None
    chiavi: tuple[ChiaveCollegamento, ...] | None = None
    if collegamenti is not None:
        lista = list(collegamenti)
        if any(isinstance(c, Mapping) for c in lista):
            chiavi = chiavi_da_righe(lista)
        else:
            chiavi = tuple(c for c in lista if isinstance(c, ChiaveCollegamento))
    paese = _testo(riga.get("esterno_paese"))
    return MembroSnapshot(
        id=str(riga["id"]),
        ruolo=str(riga.get("ruolo") or "partner"),
        stato=str(riga.get("stato") or "proposto"),
        quota=_decimale(riga.get("quota_percentuale")),
        creatore=not esterno and str(company) == str(creatore_company_id),
        esterno=esterno,
        profilo=None if esterno or not attivo else profilo,
        esterno_paese=paese.upper() if paese else None,
        esterno_tipi=tuple(
            t for t in riga.get("esterno_tipi_soggetto") or () if t in voc.TIPI_SOGGETTO
        ),
        collegamenti=None if esterno or not attivo else chiavi,
        impegni_altrove=None if impegni_altrove is None else tuple(impegni_altrove),
        posizione_id=str(riga["posizione_id"]) if riga.get("posizione_id") else None,
        attivo=esterno or attivo,
    )


def _origine_requisito(riga: Mapping) -> RegolaOrigine:
    citazione = _citazione(riga.get("citazione"))
    if citazione is not None and citazione.verificata:
        return RegolaOrigine("bando", citazione)
    if riga.get("origine") == "precheck":
        return RegolaOrigine("bando")  # dati del catalogo del bando, senza citazione
    return REGOLA_CREATORE


def requisito_da_riga(
    riga: Mapping, *, pubblico: Callable[[Any], str | None] | None = None
) -> RequisitoConsorzio:
    """`RequisitoConsorzio` da una riga di `partner_call_requisiti`. Un
    criterio illeggibile diventa `manuale` (`criterio_da_json`)."""
    pulisci = pubblico or _testo
    return RequisitoConsorzio(
        id=str(riga["id"]),
        etichetta=pulisci(riga.get("etichetta")) or "?",
        testo=pulisci(riga.get("testo")),
        criterio=criterio_da_json(riga.get("criterio")),
        ambito="ogni_membro" if riga.get("ambito") == "ogni_membro" else "consorzio",
        cercato=riga.get("cercato") is True,
        regola=_origine_requisito(riga),
    )


def call_consorzio_da(
    call: Mapping,
    requisiti: Iterable[Mapping],
    *,
    pubblico: Callable[[Any], str | None] | None = None,
) -> CallConsorzio:
    """`CallConsorzio` dalle righe di `partner_calls` e dei requisiti,
    nell'ordine dei requisiti (`ordine`)."""
    righe = sorted(
        (r for r in requisiti if isinstance(r, Mapping) and r.get("id") is not None),
        key=lambda r: r.get("ordine") if isinstance(r.get("ordine"), int) else 0,
    )
    return CallConsorzio(
        id=str(call["id"]),
        esclusivita=call.get("esclusivita") is True,
        requisiti=tuple(requisito_da_riga(r, pubblico=pubblico) for r in righe),
    )


# ------------------------------------------------------------------ voci


@dataclass(frozen=True)
class EsitoMembro:
    """Esito di una voce su un membro: `esito` in vista «terzi» (lo vedono
    tutti), `esito_proprio` in vista «proprio» (solo il membro stesso)."""

    esito: EsitoVoce
    esito_proprio: EsitoVoce
    dichiarato: bool = False


@dataclass(frozen=True)
class Voce:
    """Una voce della checklist. Per le voci valutate membro per membro
    (`per_membro` non vuoto) esito, dettaglio e coinvolti si ricompongono per
    destinatario (`proietta_voce`); `esito`, `dettaglio_pubblico` e
    `membri_coinvolti` qui sono quelli della vista neutra (tutti «terzi»).
    `dettaglio_privato_per[membro]` lo vede solo quel membro."""

    id: str
    codice: CodiceVoce
    titolo: str
    esito: EsitoVoce
    dettaglio_pubblico: str
    regola: RegolaOrigine | None = None
    membri_coinvolti: tuple[str, ...] = ()
    dettaglio_privato_per: Mapping[str, str] = field(default_factory=dict)
    per_membro: Mapping[str, EsitoMembro] = field(default_factory=dict)
    aggregazione: Aggregazione = "ciascuno"
    descrizione: str = ""
    riservata: bool = False
    solo_esito: bool = False
    dichiarato: bool = False


def esito_complessivo(voci: Iterable[Any]) -> EsitoVoce:
    """Rosso se c'è un rosso, altrimenti grigio se c'è un grigio, altrimenti
    verde. Accetta voci (con `.esito`) o esiti."""
    esiti = {v if isinstance(v, str) else v.esito for v in voci}
    if "rosso" in esiti:
        return "rosso"
    if "grigio" in esiti:
        return "grigio"
    return "verde"


def _aggrega(esiti: Iterable[EsitoVoce], aggregazione: Aggregazione) -> EsitoVoce:
    lista = list(esiti)
    if not lista:
        return "grigio"
    if aggregazione == "media":
        # Convessità: certo solo se tutti i membri sono dalla stessa parte.
        if all(e == "verde" for e in lista):
            return "verde"
        if all(e == "rosso" for e in lista):
            return "rosso"
        return "grigio"
    return esito_complessivo(lista)


def _riepilogo_membri(esiti: Sequence[EsitoVoce]) -> str:
    n = len(esiti)
    if not n:
        return "Nessun membro a cui si applica."
    verdi, rossi = esiti.count("verde"), esiti.count("rosso")
    grigi = n - verdi - rossi
    if verdi == n:
        return f"In regola tutti i membri ({n})." if n > 1 else "In regola l'unico membro."
    testo = f"In regola {_n(verdi, 'membro', 'membri')} su {n}"
    if rossi:
        testo += f"; fuori regola: {rossi}"
    if grigi:
        testo += f"; da verificare: {grigi}"
    return testo + "."


def _stato_voce(
    voce: Voce, viewer: str | None
) -> tuple[EsitoVoce, str, tuple[str, ...], dict[str, EsitoVoce], bool]:
    """(esito, dettaglio, coinvolti, esiti per membro, dichiarato) per chi
    guarda: la sua colonna in vista «proprio», le altre in vista «terzi»."""
    if not voce.per_membro:
        return voce.esito, voce.dettaglio_pubblico, voce.membri_coinvolti, {}, voce.dichiarato
    effettivi = {
        mid: (e.esito_proprio if mid == viewer else e.esito) for mid, e in voce.per_membro.items()
    }
    esito = _aggrega(effettivi.values(), voce.aggregazione)
    dichiarato = voce.dichiarato or any(
        voce.per_membro[mid].dichiarato and e == "verde" for mid, e in effettivi.items()
    )
    if voce.solo_esito:
        return esito, voce.descrizione, (), {}, dichiarato
    coinvolti = tuple(mid for mid, e in effettivi.items() if e != "verde")
    dettaglio = f"{voce.descrizione} {_riepilogo_membri(list(effettivi.values()))}".strip()
    return esito, dettaglio, coinvolti, effettivi, dichiarato


def _voce_per_membro(
    *,
    id: str,
    codice: CodiceVoce,
    titolo: str,
    descrizione: str,
    regola: RegolaOrigine | None,
    per_membro: Mapping[str, EsitoMembro],
    privati: Mapping[str, str] | None = None,
    aggregazione: Aggregazione = "ciascuno",
    solo_esito: bool = False,
) -> Voce:
    base = Voce(
        id=id, codice=codice, titolo=titolo, esito="grigio", dettaglio_pubblico=descrizione,
        regola=regola, dettaglio_privato_per=dict(privati or {}), per_membro=dict(per_membro),
        aggregazione=aggregazione, descrizione=descrizione, solo_esito=solo_esito,
    )
    esito, dettaglio, coinvolti, _effettivi, dichiarato = _stato_voce(base, None)
    return Voce(
        id=id, codice=codice, titolo=titolo, esito=esito, dettaglio_pubblico=dettaglio,
        regola=regola, membri_coinvolti=coinvolti, dettaglio_privato_per=dict(privati or {}),
        per_membro=dict(per_membro), aggregazione=aggregazione, descrizione=descrizione,
        solo_esito=solo_esito, dichiarato=dichiarato,
    )


@dataclass(frozen=True)
class Validazione:
    voci: tuple[Voce, ...]
    creatore_membro_id: str | None = None

    def esito_per(self, viewer_membro_id: str | None) -> EsitoVoce:
        return esito_complessivo(_stato_voce(v, viewer_membro_id)[0] for v in self.voci)

    @property
    def esito(self) -> EsitoVoce:
        """Esito complessivo nella vista del creatore (da persistere)."""
        return self.esito_per(self.creatore_membro_id)


# ------------------------------------------------------------ valutazioni


def _colore_criterio(esito: str) -> EsitoVoce:
    return {"coperto": "verde", "non_coperto": "rosso"}.get(esito, "grigio")


def _colore_regola(esito: str) -> EsitoVoce:
    return {"soddisfatto": "verde", "non_soddisfatto": "rosso"}.get(esito, "grigio")


def _mancante(criterio: CriterioPartner | None, motivo: str | None = None) -> EsitoCriterio:
    if criterio is None or isinstance(criterio, CriterioManuale):
        return valuta_criterio(criterio, ProfiloCandidato(), vista="terzi")
    return EsitoCriterio(
        esito="dato_mancante",
        fonte=None,
        testo_pubblico=testo_pubblico(criterio.tipo, "dato_mancante", None, motivo),
    )


def _valuta_esterno(criterio: CriterioPartner | None, m: MembroSnapshot) -> EsitoCriterio:
    """Un membro esterno non ha registro, profilo partner né bilanci: contano
    solo il paese e i tipi di soggetto DICHIARATI dal creatore (Q20)."""
    if isinstance(criterio, CriterioTipoSoggetto):
        esito = "coperto" if set(criterio.valori) & set(m.esterno_tipi) else "non_coperto"
        return EsitoCriterio(
            esito=esito,
            fonte="dichiarato",
            testo_pubblico=testo_pubblico("tipo_soggetto", esito, "dichiarato"),
        )
    if isinstance(criterio, CriterioPaese):
        base = valuta_criterio(criterio, ProfiloCandidato(paese=m.esterno_paese), vista="terzi")
        return EsitoCriterio(
            esito=base.esito,
            fonte="dichiarato",
            testo_pubblico=testo_pubblico("paese", base.esito, "dichiarato"),
        )
    return _mancante(criterio, "membro esterno")


def _valuta(
    criterio: CriterioPartner | None, m: MembroSnapshot, *, costo: Intervallo | None = None
) -> tuple[EsitoCriterio, EsitoCriterio]:
    """(vista «terzi», vista «proprio») del criterio sul membro."""
    if m.esterno:
        esito = _valuta_esterno(criterio, m)
        return esito, esito
    if m.profilo is None:
        esito = _mancante(criterio)
        return esito, esito
    try:
        return (
            valuta_criterio(criterio, m.profilo, costo_quota=costo, vista="terzi"),
            valuta_criterio(criterio, m.profilo, costo_quota=costo, vista="proprio"),
        )
    except ArithmeticError:
        logger.warning("partenariati: criterio non valutabile nel validatore (aritmetica)")
        esito = _mancante(criterio)
        return esito, esito


def _tri(esito: EsitoCriterio) -> _Tri:
    return {"coperto": "si", "non_coperto": "no"}.get(esito.esito, "forse")


def _e(*stati: _Tri) -> _Tri:
    if "no" in stati:
        return "no"
    if all(s == "si" for s in stati):
        return "si"
    return "forse"


def _paese(m: MembroSnapshot) -> str | None:
    if m.esterno:
        return m.esterno_paese
    return m.profilo.paese if m.profilo is not None else None


def _codici_paesi(paesi: Iterable[str]) -> tuple[set[str], bool]:
    """Codici ISO2 riconosciuti dal testo dell'estrazione, e se ne resta
    qualcuno non riconosciuto (nome del paese, gruppo di paesi…)."""
    codici: set[str] = set()
    ignoti = False
    for testo in paesi:
        codice = testo.strip().upper()
        codice = _ALIAS_PAESI.get(codice, codice)
        if codice in PAESI_ISO2:
            codici.add(codice)
        else:
            ignoti = True
    return codici, ignoti


def _pct(valore: Any) -> str:
    numero = Decimal(str(valore)).normalize()
    testo = format(numero, "f")
    return testo.replace(".", ",") + "%"


def _origine_voce(voce: Any) -> RegolaOrigine:
    citazione = getattr(voce, "citazione", None)
    if (
        getattr(voce, "origine_voce", None) == "confermata"
        and citazione is not None
        and citazione.verificata
    ):
        return RegolaOrigine("bando", citazione)
    return REGOLA_CREATORE


def _ordinati(membri: Sequence[MembroSnapshot], ids: Iterable[str]) -> tuple[str, ...]:
    scelti = set(ids)
    return tuple(m.id for m in membri if m.id in scelti)


# ------------------------------------------------------------- controlli


def _numero_partner(s: RegoleCallSnapshot | None, membri: Sequence[MembroSnapshot]
                    ) -> list[Voce]:
    n = sum(1 for m in membri if m.ruolo in RUOLI_CONTEGGIO)
    if n == 0:
        presenti = "Nel consorzio non ci sono ancora né capofila né partner"
    elif n == 1:
        presenti = "Nel consorzio c'è 1 partner, capofila compreso"
    else:
        presenti = f"Nel consorzio ci sono {n} partner, capofila compreso"
    dettaglio = f"{presenti} (entità affiliate e partner associati non contano)."
    voci = []
    if s is None or s.partner_min is None:
        # Controllo di coerenza: un consorzio ha almeno due soggetti che
        # ricevono budget (senza un minimo nel bando, o senza regole).
        voci.append(Voce(
            id="numero_partner:coerenza", codice="numero_partner",
            titolo=f"Almeno {MINIMO_COERENZA} partner, capofila compreso",
            esito="verde" if n >= MINIMO_COERENZA else "rosso",
            dettaglio_pubblico=dettaglio,
        ))
    if s is None:
        return voci
    if s.partner_min is not None:
        minimo = s.partner_min.valore
        voci.append(Voce(
            id="numero_partner:min", codice="numero_partner",
            titolo=f"Almeno {minimo} partner", esito="verde" if n >= minimo else "rosso",
            dettaglio_pubblico=dettaglio, regola=_origine_voce(s.partner_min),
        ))
    if s.partner_max is not None:
        massimo = s.partner_max.valore
        voci.append(Voce(
            id="numero_partner:max", codice="numero_partner",
            titolo=f"Al massimo {massimo} partner", esito="verde" if n <= massimo else "rosso",
            dettaglio_pubblico=dettaglio, regola=_origine_voce(s.partner_max),
        ))
    return voci


# Ruolo della voce di composizione → ruoli dei membri che contano di sicuro e
# che contano forse (il capofila tra i «partner», affiliati e associati tra i
# soggetti «qualsiasi»: dipende dal bando).
_RUOLI_COMPOSIZIONE: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "capofila": (frozenset({"capofila"}), frozenset()),
    "partner": (frozenset({"partner"}), frozenset({"capofila"})),
    "qualsiasi": (RUOLI_CONTEGGIO, frozenset({"affiliated_entity", "associated_partner"})),
    "affiliato": (frozenset({"affiliated_entity"}), frozenset()),
    "partner_associato": (frozenset({"associated_partner"}), frozenset()),
}
_QUALIFICA_RUOLO = {
    "capofila": " come capofila",
    "partner": " come partner",
    "qualsiasi": "",
    "affiliato": " come entità affiliata",
    "partner_associato": " come partner associato",
}


def _ha_tipo(tipo: str, m: MembroSnapshot) -> tuple[_Tri, bool]:
    """(sì/no/forse, dichiarato) del tipo di soggetto sul membro."""
    if tipo == "altro":
        return "forse", False
    esito = _valuta(CriterioTipoSoggetto(valori=[tipo]), m)[0]
    stato = _tri(esito)
    return stato, stato == "si" and esito.fonte == "dichiarato"


def _territorio(voce: ComposizioneSnapshot, m: MembroSnapshot) -> _Tri:
    stati: list[_Tri] = []
    if voce.regioni:
        criterio = CriterioRegione(regioni_ids=list(voce.regioni), modalita="sede_attuale")
        stati.append(_tri(_valuta(criterio, m)[0]))
    if voce.paesi:
        codici, ignoti = _codici_paesi(voce.paesi)
        paese = _paese(m)
        if paese is None:
            stati.append("forse")
        elif paese in codici:
            stati.append("si")
        else:
            stati.append("forse" if ignoti else "no")
    if not stati:
        # Solo un vincolo a testo: non si verifica in automatico.
        return "forse" if voce.vincolo_territoriale else "si"
    if len(stati) == 2 and set(stati) == {"si", "no"}:
        return "forse"  # regioni e paesi insieme: non si sa se valgono in alternativa
    return _e(*stati)


def _numeri(minimo: int | None, massimo: int | None) -> str:
    if minimo is not None and massimo is not None:
        return f"Ne servono tra {minimo} e {massimo}." if minimo != massimo else (
            f"Ne servono esattamente {minimo}."
        )
    if minimo is not None:
        return f"Ne servono almeno {minimo}."
    if massimo is not None:
        return f"Al massimo {massimo}."
    return f"{TESTO_NON_INDICA} quanti ne servono."


def _conteggio(minimo: int | None, massimo: int | None, certi: int, possibili: int) -> EsitoVoce:
    esiti: list[EsitoVoce] = []
    if minimo is not None:
        if certi >= minimo:
            esiti.append("verde")
        elif certi + possibili < minimo:
            esiti.append("rosso")
        else:
            esiti.append("grigio")
    if massimo is not None:
        if certi + possibili <= massimo:
            esiti.append("verde")
        elif certi > massimo:
            esiti.append("rosso")
        else:
            esiti.append("grigio")
    return esito_complessivo(esiti) if esiti else "grigio"


def _composizione(
    voce: ComposizioneSnapshot,
    membri: Sequence[MembroSnapshot],
    pulisci: Callable[[Any], str | None],
) -> Voce:
    sicuri, forse_ruolo = _RUOLI_COMPOSIZIONE[voce.ruolo]
    si: list[str] = []
    forse: list[str] = []
    dichiarato = False
    for m in membri:
        if m.ruolo in sicuri:
            ruolo: _Tri = "si"
        elif m.ruolo in forse_ruolo:
            ruolo = "forse"
        else:
            continue
        tipo, tipo_dichiarato = _ha_tipo(voce.tipo_soggetto, m)
        stato = _e(ruolo, tipo, _territorio(voce, m))
        if stato == "si":
            si.append(m.id)
            dichiarato = dichiarato or tipo_dichiarato or m.esterno
        elif stato == "forse":
            forse.append(m.id)
    etichetta = voc.TIPI_SOGGETTO[voce.tipo_soggetto].etichetta
    if voce.tipo_soggetto == "altro":
        etichetta = pulisci(voce.tipo_soggetto_testo) or etichetta
    titolo = f"Composizione: {etichetta}{_QUALIFICA_RUOLO[voce.ruolo]}"
    if voce.regioni or voce.paesi or voce.vincolo_territoriale:
        titolo += ", con vincolo territoriale"
    dettaglio = f"{_numeri(voce.minimo, voce.massimo)} Nel consorzio: {len(si)}"
    if forse:
        dettaglio += f", più {len(forse)} da verificare"
    dettaglio += "."
    if dichiarato:
        dettaglio += " Tipo di soggetto dichiarato, non risulta dal Registro Imprese."
    return Voce(
        id=f"composizione:{voce.id}", codice="composizione", titolo=titolo,
        esito=_conteggio(voce.minimo, voce.massimo, len(si), len(forse)),
        dettaglio_pubblico=dettaglio, regola=_origine_voce(voce),
        membri_coinvolti=_ordinati(membri, (*si, *forse)), dichiarato=dichiarato,
    )


def _somma_quote(tutti: Sequence[MembroSnapshot]) -> Voce:
    titolo = "Somma delle quote pari al 100%"
    # I partner associati non ricevono budget: la loro quota non si somma.
    membri = [m for m in tutti if m.ruolo in RUOLI_BENEFICIARI]
    mancanti = [m.id for m in membri if m.quota is None]
    if not membri:
        return Voce(id="somma_quote", codice="somma_quote", titolo=titolo, esito="grigio",
                    dettaglio_pubblico="Nel consorzio non ci sono membri che ricevono budget.")
    if mancanti:
        return Voce(
            id="somma_quote", codice="somma_quote", titolo=titolo, esito="grigio",
            dettaglio_pubblico=(
                f"Quota mancante per {_n(len(mancanti), 'membro', 'membri')}: la somma non si "
                "può verificare."
            ),
            membri_coinvolti=tuple(mancanti),
        )
    somma = sum((m.quota for m in membri), Decimal(0))
    if abs(somma - 100) <= TOLLERANZA_QUOTE:
        return Voce(id="somma_quote", codice="somma_quote", titolo=titolo, esito="verde",
                    dettaglio_pubblico="La somma delle quote è 100%.")
    return Voce(
        id="somma_quote", codice="somma_quote", titolo=titolo, esito="rosso",
        dettaglio_pubblico=f"La somma delle quote è {_pct(somma)}: deve essere 100%.",
    )


def _limiti(minimo: Decimal | None, massimo: Decimal | None) -> str:
    parti = []
    if minimo is not None:
        parti.append(f"quota minima {_pct(minimo)}")
    if massimo is not None:
        parti.append(f"quota massima {_pct(massimo)}")
    testo = " e ".join(parti)
    return testo[:1].upper() + testo[1:]


def _nei_limiti(quota: Decimal | None, minimo: Decimal | None, massimo: Decimal | None
                ) -> EsitoVoce:
    if quota is None:
        return "grigio"
    if (minimo is not None and quota < minimo) or (massimo is not None and quota > massimo):
        return "rosso"
    return "verde"


_TESTO_MAGGIORAZIONE = (
    " Se non è rispettata si perde solo la maggiorazione: il progetto resta ammissibile."
)


def _quota(q: QuotaSnapshot, membri: Sequence[MembroSnapshot]) -> Voce:
    minimo = _decimale(q.min_percentuale)
    massimo = _decimale(q.max_percentuale)
    limiti = _limiti(minimo, massimo)
    regola = _origine_voce(q)
    # Una violazione che costa solo la maggiorazione non rende il consorzio
    # inammissibile: il rosso diventa grigio (da valutare), con la spiegazione.
    maggiorazione = q.effetto_violazione == "perdita_maggiorazione"
    nota = _TESTO_MAGGIORAZIONE if maggiorazione else ""

    def colore(esito: EsitoVoce) -> EsitoVoce:
        return "grigio" if maggiorazione and esito == "rosso" else esito

    # per_partner e capofila con categoria: la quota vale solo per i membri di
    # quel tipo. Per ogni partner un tipo incerto dà grigio solo se la quota
    # manca o viola i limiti (se li rispetta, la regola è soddisfatta comunque);
    # per il capofila un tipo incerto dà sempre grigio, perché la regola può
    # presupporre un capofila di quel tipo.
    tipo = voc.TIPI_SOGGETTO[q.categoria].etichetta if q.categoria else None
    if q.ambito == "per_partner":
        per_membro: dict[str, EsitoMembro] = {}
        conteggiati = False
        for m in membri:
            if m.ruolo not in RUOLI_CONTEGGIO:
                continue
            conteggiati = True
            esito = colore(_nei_limiti(m.quota, minimo, massimo))
            dichiarato = False
            if q.categoria is not None:
                stato, dichiarato = _ha_tipo(q.categoria, m)
                if stato == "no":
                    continue
                if stato == "forse" and esito != "verde":
                    esito = "grigio"
            per_membro[m.id] = EsitoMembro(esito, esito, dichiarato)
        if tipo is None:
            titolo, descrizione = "Quota di ogni partner", f"{limiti} per ogni partner.{nota}"
        else:
            titolo = f"Quota di ogni partner: {tipo}"
            descrizione = f"{limiti} per ogni partner di tipo «{tipo}».{nota}"
            if conteggiati and not per_membro:
                return Voce(
                    id=f"quota:{q.id}", codice="quota_partner", titolo=titolo, esito="verde",
                    dettaglio_pubblico=(
                        f"{limiti}. Nel consorzio non ci sono partner di tipo «{tipo}».{nota}"
                    ),
                    regola=regola,
                )
        return _voce_per_membro(
            id=f"quota:{q.id}", codice="quota_partner", titolo=titolo,
            descrizione=descrizione, regola=regola, per_membro=per_membro,
        )
    if q.ambito == "capofila":
        capofila = [m for m in membri if m.ruolo == "capofila"]
        titolo = "Quota del capofila" if tipo is None else f"Quota del capofila: {tipo}"
        if not capofila:
            return Voce(
                id=f"quota:{q.id}", codice="quota_capofila", titolo=titolo, esito="grigio",
                dettaglio_pubblico=f"{limiti}. Il consorzio non ha ancora un capofila.{nota}",
                regola=regola,
            )
        m = capofila[0]
        esito = colore(_nei_limiti(m.quota, minimo, massimo))
        sul_tipo = ""
        dichiarato = False
        if q.categoria is not None:
            stato, dichiarato = _ha_tipo(q.categoria, m)
            if stato == "no":
                esito = "grigio"
                sul_tipo = f" Il capofila non risulta di tipo «{tipo}»."
            elif stato == "forse":
                esito = "grigio"
                sul_tipo = f" Da verificare se il capofila è di tipo «{tipo}»."
        dettaglio = f"{limiti}. Quota del capofila: " + (
            _pct(m.quota) if m.quota is not None else "non indicata"
        ) + f".{sul_tipo}{nota}"
        return Voce(
            id=f"quota:{q.id}", codice="quota_capofila", titolo=titolo, esito=esito,
            dettaglio_pubblico=dettaglio, regola=regola,
            membri_coinvolti=() if esito == "verde" else (m.id,), dichiarato=dichiarato,
        )
    # per_categoria: somma delle quote dei membri della categoria che ricevono
    # budget (i partner associati no).
    categoria = q.categoria or "altro"
    etichetta = voc.TIPI_SOGGETTO[categoria].etichetta
    si: list[MembroSnapshot] = []
    forse: list[MembroSnapshot] = []
    dichiarato = False
    for m in membri:
        if m.ruolo not in RUOLI_BENEFICIARI:
            continue
        stato, tipo_dichiarato = _ha_tipo(categoria, m)
        if stato == "si":
            si.append(m)
            dichiarato = dichiarato or tipo_dichiarato or m.esterno
        elif stato == "forse":
            forse.append(m)
    titolo = f"Quota complessiva: {etichetta}"
    coinvolti = _ordinati(membri, (m.id for m in (*si, *forse)))
    senza_quota = [m.id for m in (*si, *forse) if m.quota is None]
    if senza_quota:
        return Voce(
            id=f"quota:{q.id}", codice="quota_categoria", titolo=titolo, esito="grigio",
            dettaglio_pubblico=(
                f"{limiti}. Quota mancante per {_n(len(senza_quota), 'membro', 'membri')}.{nota}"
            ),
            regola=regola, membri_coinvolti=tuple(senza_quota), dichiarato=dichiarato,
        )
    certa = sum((m.quota for m in si), Decimal(0))
    possibile = certa + sum((m.quota for m in forse), Decimal(0))
    esiti: list[EsitoVoce] = []
    if minimo is not None:
        esiti.append("verde" if certa >= minimo else "rosso" if possibile < minimo else "grigio")
    if massimo is not None:
        esiti.append(
            "verde" if possibile <= massimo else "rosso" if certa > massimo else "grigio"
        )
    dettaglio = f"{limiti}. Nel consorzio: {_pct(certa)}"
    if possibile != certa:
        dettaglio += f", fino a {_pct(possibile)} con i membri da verificare"
    dettaglio += f".{nota}"
    return Voce(
        id=f"quota:{q.id}", codice="quota_categoria", titolo=titolo,
        esito=colore(esito_complessivo(esiti)) if esiti else "grigio",
        dettaglio_pubblico=dettaglio, regola=regola, membri_coinvolti=coinvolti,
        dichiarato=dichiarato,
    )


def _indipendenza(
    membri: Sequence[MembroSnapshot], vincolo: VincoloSnapshot | None
) -> Voce:
    contano = [m for m in membri if m.ruolo in RUOLI_INDIPENDENZA]
    certi: set[str] = set()
    possibili: set[str] = set()
    senza_chiavi: set[str] = set()
    esterni: set[str] = set()
    for a, b in combinations(contano, 2):
        if a.esterno or b.esterno:
            esterni.update(m.id for m in (a, b) if m.esterno)
            continue
        if a.collegamenti is None or b.collegamenti is None:
            senza_chiavi.update(m.id for m in (a, b) if m.collegamenti is None)
            continue
        grado = valuta_collegamento(a.collegamenti, b.collegamenti)
        if grado == "certo":
            certi.update((a.id, b.id))
        elif grado == "possibile":
            possibili.update((a.id, b.id))
    righe: list[str] = []
    privati: dict[str, str] = {}
    if certi:
        riga = "Collegamento societario tra membri del consorzio."
        if vincolo is None:
            riga += f" {TESTO_NON_INDICA} se i partner devono essere indipendenti."
        righe.append(riga)
        for mid in certi:
            privati[mid] = (
                "Risulta un collegamento societario tra la tua azienda e un altro membro: "
                "verifica con visura."
            )
    if possibili - certi:
        righe.append(TESTO_COLLEGAMENTO[:1].upper() + TESTO_COLLEGAMENTO[1:] + ".")
        for mid in possibili - certi:
            privati[mid] = (
                "Risulta un possibile collegamento societario tra la tua azienda e un altro "
                "membro: verifica con visura."
            )
    if senza_chiavi:
        righe.append("Collegamenti societari non ancora verificati per alcuni membri.")
        for mid in senza_chiavi:
            privati.setdefault(
                mid, "I collegamenti societari della tua azienda non sono ancora verificati."
            )
    if esterni:
        righe.append("Membri esterni: indipendenza da verificare con visura.")
    if certi:
        esito: EsitoVoce = "rosso" if vincolo is not None else "grigio"
    elif possibili or senza_chiavi or esterni:
        esito = "grigio"
    else:
        esito = "verde"
        righe.append("Nessun collegamento societario tra i membri in piattaforma.")
    return Voce(
        id="indipendenza", codice="indipendenza",
        titolo="Indipendenza tra i partner", esito=esito,
        dettaglio_pubblico=" ".join(righe),
        regola=_origine_voce(vincolo) if vincolo is not None else None,
        membri_coinvolti=_ordinati(membri, certi | possibili | senza_chiavi | esterni),
        dettaglio_privato_per=privati, riservata=True,
    )


def _paesi_distinti(v: VincoloSnapshot, membri: Sequence[MembroSnapshot]) -> Voce:
    titolo = "Partner di paesi diversi"
    regola = _origine_voce(v)
    contano = [m for m in membri if m.ruolo in RUOLI_CONTEGGIO]
    paesi = {p for m in contano if (p := _paese(m))}
    ignoti = [m.id for m in contano if not _paese(m)]
    elenco = ", ".join(sorted(paesi)) or "nessuno"
    presenti = f"Paesi dei partner: {elenco}"
    if ignoti:
        presenti += f"; paese non noto per {_n(len(ignoti), 'membro', 'membri')}"
    if v.parametro is None:
        return Voce(
            id=f"paesi:{v.id}", codice="paesi_distinti", titolo=titolo, esito="grigio",
            dettaglio_pubblico=f"{TESTO_NON_INDICA} quanti paesi servono. {presenti}.",
            regola=regola, membri_coinvolti=tuple(ignoti),
        )
    richiesti = max(1, math.ceil(v.parametro))
    if len(paesi) >= richiesti:
        esito: EsitoVoce = "verde"
    elif len(paesi) + len(ignoti) < richiesti:
        esito = "rosso"
    else:
        esito = "grigio"
    return Voce(
        id=f"paesi:{v.id}", codice="paesi_distinti", titolo=titolo, esito=esito,
        dettaglio_pubblico=(
            f"Servono partner di almeno {_n(richiesti, 'paese', 'paesi diversi')}. {presenti}."
        ),
        regola=regola, membri_coinvolti=tuple(ignoti) if esito == "grigio" else (),
    )


def _regola_membro(
    regola: RegolaFinanziaria, m: MembroSnapshot, costo: Intervallo | None
) -> tuple[EsitoMembro, str | None]:
    """(esiti «terzi»/«proprio», spiegazione per il solo membro)."""
    if m.esterno:
        return EsitoMembro("grigio", "grigio"), None
    if m.profilo is None:
        return EsitoMembro("grigio", "grigio"), None
    try:
        terzi = valuta_regola_finanziaria(
            regola, list(m.profilo.esercizi), costo, vista="terzi",
            storico_completo=m.profilo.storico_completo,
        )
        proprio = valuta_regola_finanziaria(
            regola, list(m.profilo.esercizi), costo, vista="proprio",
            storico_completo=m.profilo.storico_completo,
        )
    except ArithmeticError:
        logger.warning("partenariati: regola finanziaria non valutabile nel validatore")
        return EsitoMembro("grigio", "grigio"), None
    return (
        EsitoMembro(_colore_regola(terzi.esito), _colore_regola(proprio.esito)),
        proprio.spiegazione_titolare,
    )


def _regola_finanziaria(
    r: RegolaFinanziaria,
    membri: Sequence[MembroSnapshot],
    budget: Intervallo | None,
    pulisci: Callable[[Any], str | None],
    *,
    origine: RegolaOrigine | None = None,
    id_voce: str | None = None,
) -> Voce:
    """Una regola finanziaria dello snapshot (`finanziaria:<id>`) o, con
    `origine` e `id_voce`, quella di un requisito «di ogni membro» che nello
    snapshot non c'è (difesa in profondità: in produzione i requisiti
    finanziari nascono solo dalle regole confermate)."""
    regola = r.regola() if isinstance(r, RegolaFinanziariaSnapshot) else r
    titolo = f"Regola finanziaria: {pulisci(r.descrizione) or r.id}"
    origine = origine or _origine_voce(r)
    id_voce = id_voce or f"finanziaria:{r.id}"
    media = r.ambito == "media_pesata_quote"
    codice: CodiceVoce = "media_pesata" if media else "regola_finanziaria"

    def fissa(testo: str) -> Voce:
        return Voce(id=id_voce, codice=codice, titolo=titolo, esito="grigio",
                    dettaglio_pubblico=testo, regola=origine)

    if r.ambito == "partenariato_totale":
        return fissa("Regola sul partenariato nel suo insieme: verificala a mano.")
    if r.ambito == "capofila":
        applicati = [m for m in membri if m.ruolo == "capofila"]
        if not applicati:
            return fissa("Il consorzio non ha ancora un capofila.")
        descrizione = "Si applica al capofila, sulla sua quota."
    else:
        applicati = [m for m in membri if m.ruolo in RUOLI_BENEFICIARI]
        descrizione = "Si applica a ogni partner, sulla sua quota."
    if media:
        descrizione = "Vale sulla media delle quote dei partner: si mostra solo l'esito."
        if any(m.quota is None for m in applicati):
            return fissa("Quota mancante per alcuni membri: la media non si può verificare.")
    per_membro: dict[str, EsitoMembro] = {}
    privati: dict[str, str] = {}
    for m in applicati:
        costo = intervallo_costo_quota(budget, m.quota)
        esito, privato = _regola_membro(regola, m, costo)
        per_membro[m.id] = esito
        if privato:
            privati[m.id] = f"Il tuo contributo alla media. {privato}" if media else privato
    return _voce_per_membro(
        id=id_voce, codice=codice, titolo=titolo, descrizione=descrizione, regola=origine,
        per_membro=per_membro, privati=privati,
        aggregazione="media" if media else "ciascuno", solo_esito=media,
    )


def _esclusivita(
    call: CallConsorzio, membri: Sequence[MembroSnapshot], vincolo: VincoloSnapshot | None
) -> Voce | None:
    esclusiva = call.esclusivita or vincolo is not None
    violazioni = [
        m.id for m in membri
        if not m.esterno and m.impegni_altrove and any(esclusiva or e for e in m.impegni_altrove)
    ]
    ignoti = [m.id for m in membri if not m.esterno and m.impegni_altrove is None]
    esterni = [m.id for m in membri if m.esterno]
    if not (esclusiva or violazioni or ignoti):
        return None
    if vincolo is not None:
        regola: RegolaOrigine | None = _origine_voce(vincolo)
    elif call.esclusivita:
        regola = REGOLA_CREATORE
    else:
        regola = None
    privati = {
        mid: "La tua azienda ha già un impegno su un'altra call di partenariato per lo stesso "
             "bando."
        for mid in violazioni
    }
    if violazioni:
        esito: EsitoVoce = "rosso"
        testo = (
            "Un membro ha già un impegno su un'altra call di partenariato per lo stesso bando, "
            "e una delle due è esclusiva."
        )
        coinvolti = violazioni
    elif ignoti:
        esito, testo, coinvolti = (
            "grigio", "Impegni sullo stesso bando non verificati per alcuni membri.", ignoti
        )
    elif esclusiva and esterni:
        esito = "grigio"
        testo = (
            "Verifica che i membri esterni non partecipino ad altri partenariati sullo stesso "
            "bando."
        )
        coinvolti = esterni
    else:
        esito, testo, coinvolti = (
            "verde", "Nessun membro ha impegni in conflitto sullo stesso bando.", []
        )
    return Voce(
        id="esclusivita", codice="esclusivita", titolo="Esclusività sul bando", esito=esito,
        dettaglio_pubblico=testo, regola=regola,
        membri_coinvolti=_ordinati(membri, coinvolti), dettaglio_privato_per=privati,
        riservata=True,
    )


def _vincolo_membro(r: RequisitoConsorzio, membri: Sequence[MembroSnapshot]) -> Voce:
    per_membro: dict[str, EsitoMembro] = {}
    privati: dict[str, str] = {}
    for m in membri:
        if m.ruolo not in RUOLI_BENEFICIARI:
            continue
        terzi, proprio = _valuta(r.criterio, m)
        per_membro[m.id] = EsitoMembro(
            _colore_criterio(terzi.esito),
            _colore_criterio(proprio.esito),
            dichiarato=terzi.esito == "coperto" and terzi.fonte == "dichiarato",
        )
        if proprio.testo_privato:
            privati[m.id] = proprio.testo_privato
    titolo = f"Requisito «{r.etichetta}» per ogni membro"
    if r.testo:
        titolo += f": {r.testo}"
    return _voce_per_membro(
        id=f"vincolo_membro:{r.id}", codice="vincolo_membro", titolo=titolo,
        descrizione="Deve valere per ogni partner.", regola=r.regola or REGOLA_CREATORE,
        per_membro=per_membro, privati=privati,
    )


def _snapshot(regole: Any) -> RegoleCallSnapshot | None:
    if regole is None or isinstance(regole, RegoleCallSnapshot):
        return regole
    if not isinstance(regole, Mapping):
        return None
    try:
        return RegoleCallSnapshot.model_validate(regole)
    except (ValidationError, AppError, ValueError):
        logger.warning("partenariati: snapshot delle regole non leggibile nel validatore")
        return None


def valida_consorzio(
    regole: RegoleCallSnapshot | Mapping | None,
    call: CallConsorzio,
    membri: Iterable[MembroSnapshot],
    *,
    budget: Intervallo | None,
    pubblico: Callable[[Any], str | None] | None = None,
) -> Validazione:
    """Checklist del consorzio (docstring del modulo).

    - `regole`: lo snapshot confermato (`partner_call_service.snapshot_regole`)
      o la sua forma jsonb; None = regole non confermate (voce grigia
      `regole` e soli controlli di coerenza);
    - `membri`: i membri della call (gli usciti si ignorano);
    - `budget`: `partner_call_gap.intervallo_budget(budget_fascia,
      budget_progetto_eur)`: il punto esatto se il creatore l'ha indicato,
      altrimenti la fascia; None se mancano entrambi (le regole sul costo
      della quota diventano grigie);
    - `pubblico`: ripulisce i testi del creatore che finiscono nelle voci
      (descrizioni delle regole), come per i requisiti."""
    pulisci = pubblico or _testo
    attivi = [m for m in membri if m.stato != "uscito"]
    snapshot = _snapshot(regole)
    vincoli = list(snapshot.vincoli) if snapshot is not None else []

    def vincolo(tipo: str) -> VincoloSnapshot | None:
        return next((v for v in vincoli if v.tipo == tipo), None)

    voci: list[Voce] = []
    if snapshot is None:
        voci.append(Voce(
            id="regole", codice="regole", titolo="Regole del bando", esito="grigio",
            dettaglio_pubblico=(
                "Le regole del bando non sono confermate: i controlli che ne dipendono non si "
                "possono fare."
            ),
        ))
    voci.extend(_numero_partner(snapshot, attivi))
    if snapshot is not None:
        voci.extend(_composizione(v, attivi, pulisci) for v in snapshot.composizione)
    voci.append(_somma_quote(attivi))
    if snapshot is not None:
        voci.extend(_quota(q, attivi) for q in snapshot.quote)
    voci.append(_indipendenza(attivi, vincolo("indipendenza")))
    voci.extend(_paesi_distinti(v, attivi) for v in vincoli if v.tipo == "paesi_distinti")
    if snapshot is not None:
        voci.extend(
            _regola_finanziaria(r, attivi, budget, pulisci) for r in snapshot.regole_finanziarie
        )
    esclusivita = _esclusivita(call, attivi, vincolo("esclusivita_partenariato"))
    if esclusivita is not None:
        voci.append(esclusivita)
    confermate = [
        r.model_dump(include=set(_CAMPI_REGOLA))
        for r in (snapshot.regole_finanziarie if snapshot is not None else ())
    ]
    for r in call.requisiti:
        if r.ambito != "ogni_membro":
            continue
        if isinstance(r.criterio, CriterioRegolaFinanziaria):
            # Già valutata dalla voce dello snapshot se è la stessa regola;
            # altrimenti si valuta qui (mai un requisito ignorato).
            if r.criterio.regola.model_dump(include=set(_CAMPI_REGOLA)) not in confermate:
                voci.append(_regola_finanziaria(
                    r.criterio.regola, attivi, budget, pulisci,
                    origine=r.regola or REGOLA_CREATORE, id_voce=f"vincolo_membro:{r.id}",
                ))
            continue
        voci.append(_vincolo_membro(r, attivi))
    regione_per_membro = any(
        r.ambito == "ogni_membro" and isinstance(r.criterio, CriterioRegione)
        for r in call.requisiti
    )
    for v in vincoli:
        if v.tipo in ("requisito_capofila", "altro") or (
            v.tipo == "sede_operativa_regione" and not regione_per_membro
        ):
            voci.append(Voce(
                id=f"vincolo:{v.id}", codice="vincolo_da_verificare",
                titolo=pulisci(v.descrizione) or "Regola del bando", esito="grigio",
                dettaglio_pubblico=(
                    "Regola descritta a testo: verificala a mano sul bando."
                    if v.tipo != "sede_operativa_regione" else
                    "Sede dei partner in una regione: nessun requisito della call la traduce "
                    "per ogni membro, verificala a mano sul bando."
                ),
                regola=_origine_voce(v),
            ))
    non_attivi = [m.id for m in attivi if not m.esterno and not m.attivo]
    if non_attivi:
        voci.append(Voce(
            id="membri_attivi", codice="membri_attivi",
            titolo="Membri ancora attivi in piattaforma", esito="grigio",
            dettaglio_pubblico=(
                "Un'azienda del consorzio non è più attiva in piattaforma: i suoi dati non si "
                "valutano. Verifica che partecipi ancora."
            ),
            membri_coinvolti=_ordinati(attivi, non_attivi), riservata=True,
        ))
    creatore = next((m.id for m in attivi if m.creatore), None)
    return Validazione(tuple(voci), creatore)


# ------------------------------------------------------------ proiezione


def _regola_out(regola: RegolaOrigine | None) -> RegolaOrigineOut | None:
    if regola is None:
        return None
    return RegolaOrigineOut(fonte=regola.fonte, citazione=regola.citazione)


def proietta_voce(voce: Voce, *, viewer_membro_id: str | None, creatore: bool) -> VoceOut:
    """La voce come la vede il destinatario: la propria colonna in vista
    «proprio», le altre in vista «terzi»; solo il PROPRIO dettaglio privato;
    per le voci riservate chi non ha creato la call vede tra i coinvolti solo
    sé stesso."""
    viewer = str(viewer_membro_id) if viewer_membro_id is not None else None
    esito, dettaglio, coinvolti, effettivi, dichiarato = _stato_voce(voce, viewer)
    if voce.riservata and not creatore:
        coinvolti = tuple(mid for mid in coinvolti if mid == viewer)
    return VoceOut(
        id=voce.id,
        codice=voce.codice,
        esito=esito,
        titolo=voce.titolo,
        dettaglio_pubblico=dettaglio,
        dettaglio_privato=voce.dettaglio_privato_per.get(viewer) if viewer else None,
        regola=_regola_out(voce.regola),
        membri_coinvolti=list(coinvolti),
        esiti_membri=[
            EsitoMembroOut(membro_id=mid, esito=e, dichiarato=voce.per_membro[mid].dichiarato)
            for mid, e in effettivi.items()
        ],
        dichiarato=dichiarato,
    )


def proietta_validazione(
    v: Validazione, *, viewer_membro_id: str | None, creatore: bool
) -> ValidazioneOut:
    """`ValidazioneOut` per il destinatario: `viewer_membro_id` = il suo
    membro (None se non ne ha uno: tutto in vista «terzi», nessun dettaglio
    privato); `creatore` = ha creato la call (vede tutti i coinvolti)."""
    voci = [
        proietta_voce(voce, viewer_membro_id=viewer_membro_id, creatore=creatore)
        for voce in v.voci
    ]
    esiti = [voce.esito for voce in voci]
    return ValidazioneOut(
        esito=esito_complessivo(esiti),
        voci=voci,
        riepilogo=RiepilogoValidazioneOut(
            verde=esiti.count("verde"), rosso=esiti.count("rosso"), grigio=esiti.count("grigio")
        ),
    )


# ------------------------------------------------------------- matrice


@dataclass(frozen=True)
class CellaCopertura:
    terzi: EsitoCriterio
    proprio: EsitoCriterio
    si_applica: bool = True


@dataclass(frozen=True)
class RigaCopertura:
    requisito: RequisitoConsorzio
    celle: Mapping[str, CellaCopertura]


_AMBITI_AGGREGATI = frozenset({"media_pesata_quote", "partenariato_totale"})


def _ambito_regola(requisito: RequisitoConsorzio) -> str | None:
    criterio = requisito.criterio
    return criterio.regola.ambito if isinstance(criterio, CriterioRegolaFinanziaria) else None


def _si_applica(requisito: RequisitoConsorzio, m: MembroSnapshot) -> bool:
    if _ambito_regola(requisito) == "capofila":
        return m.ruolo == "capofila"
    if requisito.ambito == "ogni_membro":
        return m.ruolo in RUOLI_BENEFICIARI
    return True


def _esito_riga(riga: RigaCopertura, effettivi: Mapping[str, str]) -> EsitoVoce:
    esiti = [effettivi[mid] for mid, cella in riga.celle.items() if cella.si_applica]
    if not esiti or _ambito_regola(riga.requisito) in _AMBITI_AGGREGATI:
        return "grigio"  # regole aggregate: le valuta il validatore
    if riga.requisito.ambito == "ogni_membro" or _ambito_regola(riga.requisito) == "capofila":
        if "non_coperto" in esiti:
            return "rosso"
        return "verde" if all(e == "coperto" for e in esiti) else "grigio"
    if "coperto" in esiti:
        return "verde"
    return "rosso" if all(e == "non_coperto" for e in esiti) else "grigio"


def _effettivi(riga: RigaCopertura, viewer: str | None) -> dict[str, str]:
    return {
        mid: (c.proprio if mid == viewer else c.terzi).esito for mid, c in riga.celle.items()
    }


@dataclass(frozen=True)
class MatriceCopertura:
    membri: tuple[str, ...]
    righe: tuple[RigaCopertura, ...]
    creatore_membro_id: str | None = None

    def copertura_per(self, viewer_membro_id: str | None) -> Decimal | None:
        cercati = [r for r in self.righe if r.requisito.cercato]
        if not cercati:
            return None
        coperti = sum(
            1 for r in cercati if _esito_riga(r, _effettivi(r, viewer_membro_id)) == "verde"
        )
        return (Decimal(coperti) / Decimal(len(cercati))).quantize(
            _MILLESIMI, rounding=ROUND_HALF_UP
        )

    @property
    def copertura_gap_ratio(self) -> Decimal | None:
        """Requisiti cercati coperti da almeno un membro / cercati, nella vista
        del creatore (da persistere); None senza requisiti cercati."""
        return self.copertura_per(self.creatore_membro_id)


def matrice_copertura(
    requisiti: Iterable[RequisitoConsorzio],
    membri: Iterable[MembroSnapshot],
    *,
    budget: Intervallo | None = None,
) -> MatriceCopertura:
    """Requisiti della call × membri non usciti, con `valuta_criterio` in
    vista «terzi» e «proprio» per ogni cella (le regole finanziarie sul costo
    della quota di ciascun membro)."""
    attivi = [m for m in membri if m.stato != "uscito"]
    righe = []
    for requisito in requisiti:
        celle: dict[str, CellaCopertura] = {}
        finanziaria = isinstance(requisito.criterio, CriterioRegolaFinanziaria)
        for m in attivi:
            costo = intervallo_costo_quota(budget, m.quota) if finanziaria else None
            terzi, proprio = _valuta(requisito.criterio, m, costo=costo)
            celle[m.id] = CellaCopertura(terzi, proprio, _si_applica(requisito, m))
        righe.append(RigaCopertura(requisito, celle))
    creatore = next((m.id for m in attivi if m.creatore), None)
    return MatriceCopertura(tuple(m.id for m in attivi), tuple(righe), creatore)


def proietta_matrice(m: MatriceCopertura, *, viewer_membro_id: str | None) -> MatriceOut:
    """La matrice per il destinatario: vista «proprio» e testo privato solo
    nella sua colonna."""
    viewer = str(viewer_membro_id) if viewer_membro_id is not None else None
    righe = []
    for riga in m.righe:
        celle = []
        for mid, cella in riga.celle.items():
            esito = cella.proprio if mid == viewer else cella.terzi
            celle.append(CellaMatriceOut(
                membro_id=mid,
                esito=esito.esito,
                fonte=esito.fonte,
                testo=esito.testo_pubblico,
                testo_privato=cella.proprio.testo_privato if mid == viewer else None,
                si_applica=cella.si_applica,
            ))
        righe.append(RigaMatriceOut(
            requisito_id=riga.requisito.id,
            etichetta=riga.requisito.etichetta,
            testo=riga.requisito.testo,
            ambito="ogni_membro" if riga.requisito.ambito == "ogni_membro" else "consorzio",
            cercato=riga.requisito.cercato,
            esito=_esito_riga(riga, _effettivi(riga, viewer)),
            celle=celle,
        ))
    rapporto = m.copertura_per(viewer)
    return MatriceOut(
        membri=list(m.membri),
        righe=righe,
        copertura_gap_ratio=float(rapporto) if rapporto is not None else None,
    )
