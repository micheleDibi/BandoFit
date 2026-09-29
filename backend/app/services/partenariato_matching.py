"""Matching deterministico bidirezionale call ↔ aziende (WP6, docs/partenariati.md
M1-M3, Q11, Q12, Q17, Q25).

Modulo PURO: nessun I/O, nessun LLM (nemmeno nelle spiegazioni), niente
pgvector. Lo usano l'indice in-process (`partenariato_indice`), «Per te», i
suggeriti per il creatore, la bacheca e le notifiche proattive. La copertura
di ogni criterio la decide SOLO `partenariato_criteri.valuta_criterio` (WP5):
qui non si duplica nessuna regola di copertura.

Tutte le valutazioni sono in vista «terzi» (Q11): le regole finanziarie si
valutano sulla FASCIA di bilancio del candidato, così l'esito non rivela più
della fascia a chi controlla budget e quote. Il budget della call è sempre la
sua fascia PUBBLICA (`intervallo_budget(budget_fascia)`), mai
`budget_progetto_eur` (riservato al creatore): anche il candidato che guarda il
proprio match non può ricavarlo.

Filtri rigidi, IN ORDINE; escludono solo su esito certo (M1) e restituiscono
un codice interno mai mostrato al creatore per le aziende di terzi:
  0. `call_non_attiva`: call non pubblicata, sospesa, scaduta (call o bando) o
     del creatore non più vivo;
  1. `non_visibile` (niente opt-in; non vale per «Per te», che è solo
     scoperta, Q25), `sospeso`, `non_viva`, `cessata` (stato del registro noto
     e diverso da «Attiva»);
  2. `stesso_owner` (anche la stessa azienda);
  3. `collegamenti_non_calcolati` (fail-closed, M2), `collegata` (certo o
     possibile, Q17: l'insieme `collegate` lo calcola l'indice con
     `partenariato_collegamenti.valuta_collegamento`);
  4. `categoria_esclusa` (tipologia del bando tra le `categorie_bando_escluse`
     del candidato), `forma_non_accettata`;
  5. `nessuna_posizione_compatibile` (ruolo disponibile e tipo di soggetto);
  6. `territorio`, `paese`, `dimensione` (requisiti `ogni_membro`, poi vincoli
     delle posizioni; regola «tutte le sedi» di `valuta_criterio`). Gli altri
     requisiti `ogni_membro` (tag, certificazioni, esperienze, ATECO, settore,
     tipo di soggetto) NON escludono (elenco chiuso di M1): per i dati
     dichiarati «non coperto» vuol dire solo «non dichiarato», quindi
     diventano voci «attenzione» con la penalità;
  7. `finanziaria_non_soddisfatta` (su OGNI posizione rimasta: le regole per
     ciascun partner sempre, quelle del capofila solo sulle posizioni da
     capofila; le regole aggregate sono del validatore, WP8);
  8. `esclusivita` (call esclusiva e candidato già impegnato sullo stesso
     bando);
  9. `solo_invitati`: verso il candidato («Per te») la call compare solo con
     un invito (WP7; nel WP6 mai); tra i suggeriti del creatore sparisce chi
     non accetta inviti.
`dato_mancante` e `incerto` non escludono: diventano voci «attenzione» e
penalità.

Punteggio (M3), con i pesi `PesiMatching` (dalle Settings):
  punteggio = arrotonda(100·(p_cop·copertura_gap + p_aff·affinita
              + p_compl·complementarita + p_compl_prof·completezza
              + p_rot·rotazione)) − min(penalita_max, penalita·n_attenzione),
  limitato a 0..100, arrotondamento half-up su frazioni esatte, dove
  - copertura_gap = requisiti cercati coperti / cercati (senza requisiti
    cercati: 1 se una posizione è coperta, altrimenti 0);
  - affinita = (programma + settore_o_ateco + regioni) / 3, tre indicatori
    0/1: esperienza dichiarata nel programma del bando; una divisione ATECO in
    comune con il creatore o un settore (proprio o d'interesse) uguale al suo;
    una regione (sede o d'interesse) tra quelle della call (sedi del creatore,
    regioni dei requisiti e delle posizioni);
  - complementarita = competenze del candidato che il creatore non ha /
    competenze del candidato (0 senza competenze);
  - completezza = completezza del profilo partner / 100;
  - rotazione = max(0, 1 − esposizioni degli ultimi 7 giorni / esposizioni_max).
Ordine: requisiti cercati coperti (desc), punteggio (desc), poi
`sha256(f"{call_id}:{codice_pubblico}:{settimana_iso}")`: deterministico e
diverso ogni settimana. Al massimo `max_per_owner_pagina` aziende dello stesso
owner per pagina (`impagina`).

Spiegazioni da template: «Copri «A» e «C», che mancano al capofila.»
(«…al proponente.» se il creatore cerca un capofila). Verso terzi solo fasce
ed esiti (Q11); profili anonimi con la sola fascia di fatturato (Q12).
"""

import hashlib
import logging
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.schemas.partenariato_criteri import (
    CriterioDimensione,
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
from app.schemas.partner_profile import FORME_PROFILO
from app.services import partenariato_vocabolario as voc
from app.services.bilanci_indicatori import Fasce
from app.services.partenariato_criteri import (
    criteri_da_posizione,
    criterio_da_json,
    intervallo_costo_quota,
    valuta_criterio,
    valuta_criterio_dettaglio,
)
from app.services.partner_call_gap import intervallo_budget

logger = logging.getLogger("bandofit.partenariati")

Direzione = Literal["suggeriti", "per_te"]
Vista = Literal["proprio", "terzi"]

STATO_IMPRESA_ATTIVA = "Attiva"
ESPOSIZIONI_MAX = 5
MAX_PER_OWNER_PAGINA = 2

# Codici dei filtri rigidi nell'ordine in cui si applicano (M1).
CODICI_ESCLUSIONE: tuple[str, ...] = (
    "call_non_attiva",
    "non_visibile",
    "sospeso",
    "non_viva",
    "cessata",
    "stesso_owner",
    "collegamenti_non_calcolati",
    "collegata",
    "categoria_esclusa",
    "forma_non_accettata",
    "nessuna_posizione_compatibile",
    "territorio",
    "paese",
    "dimensione",
    "finanziaria_non_soddisfatta",
    "esclusivita",
    "solo_invitati",
)

# Vincoli di ogni membro che escludono con un codice proprio, in quest'ordine.
_VINCOLI_MEMBRO: tuple[tuple[str, type], ...] = (
    ("territorio", CriterioRegione),
    ("paese", CriterioPaese),
    ("dimensione", CriterioDimensione),
)
_ESITI_DUBBI = frozenset({"dato_mancante", "incerto"})
# Preferenza tra gli esiti di una regola su posizioni diverse.
_MIGLIORE = {"coperto": 0, "incerto": 1, "dato_mancante": 2, "non_valutabile": 3, "non_coperto": 4}

# Voci «attenzione» (dato mancante o incerto su un filtro): codice → template.
_ATTENZIONE_CRITERIO = {
    "regione": "territorio_da_verificare",
    "paese": "paese_da_verificare",
    "dimensione": "dimensione_da_verificare",
    "tipo_soggetto": "tipo_soggetto_da_verificare",
}
_ATTENZIONE_FINANZA = {
    "bilanci_non_disponibili": "Bilanci non disponibili: la regola «{e}» non si può verificare",
    "dipende_fascia": "Regola «{e}»: l'esito dipende dalla fascia di bilancio",
    "dipende_budget": "Regola «{e}»: l'esito dipende dal budget del progetto",
    "costo_quota_mancante": "Regola «{e}»: senza la quota della posizione non si può verificare",
    "storico_incompleto": "Regola «{e}»: lo storico dei bilanci è incompleto",
    "finanziaria_dato_mancante": "Regola «{e}»: manca un dato di bilancio",
}
_CODICI_MOTIVO_FINANZA = frozenset(
    {"dipende_fascia", "dipende_budget", "costo_quota_mancante", "storico_incompleto"}
)


# ------------------------------------------------------------------ pesi


@dataclass(frozen=True)
class PesiMatching:
    """Pesi e parametri del punteggio (M3, Q17). I default sono quelli
    documentati; in produzione si leggono dalle Settings (`da_settings`)."""

    copertura: float = 0.50
    affinita: float = 0.20
    complementarita: float = 0.10
    completezza: float = 0.10
    rotazione: float = 0.10
    penalita_dato_mancante: int = 5
    penalita_max: int = 20
    esposizioni_max: int = ESPOSIZIONI_MAX
    max_per_owner_pagina: int = MAX_PER_OWNER_PAGINA

    @classmethod
    def da_settings(cls, settings: Any) -> "PesiMatching":
        return cls(
            copertura=settings.partenariato_peso_copertura,
            affinita=settings.partenariato_peso_affinita,
            complementarita=settings.partenariato_peso_complementarita,
            completezza=settings.partenariato_peso_completezza,
            rotazione=settings.partenariato_peso_rotazione,
            penalita_dato_mancante=settings.partenariato_penalita_dato_mancante,
            penalita_max=settings.partenariato_penalita_max,
        )


PESI_PREDEFINITI = PesiMatching()


# ------------------------------------------------------------ candidato


@dataclass(frozen=True)
class ProfiloMatching(ProfiloCandidato):
    """`ProfiloCandidato` (WP5) esteso con ciò che serve al matching: resta
    un `ProfiloCandidato`, quindi `valuta_criterio` lo accetta così com'è.

    I default sono quelli fail-closed (azienda non viva, non visibile,
    collegamenti non calcolati): chi costruisce il profilo deve dirli.
    `company_id` e `owner_id` sono interni: non escono mai verso terzi.
    `collegate` = aziende collegate (certo o possibile, Q17) secondo
    `partenariato_collegamenti`; `ruoli_disponibili` None = nessun profilo
    partner (non si esclude per ruolo); `impegni_bando` = bandi su cui
    l'azienda è già impegnata (call pubblicate; WP7-WP8 aggiungono candidature
    accettate e membri)."""

    company_id: str = ""
    owner_id: str = ""
    codice_pubblico: str | None = None
    viva: bool = False
    visibile: bool = False
    sospeso: bool = False
    anonimo: bool = True
    stato_impresa: str | None = None
    accetta_inviti: bool = True
    ruoli_disponibili: tuple[str, ...] | None = None
    forme_accettate: tuple[str, ...] = ()
    categorie_bando_escluse: frozenset[int] = frozenset()
    regioni_interesse_ids: frozenset[int] = frozenset()
    settori_interesse_ids: frozenset[int] = frozenset()
    completezza: int = 0
    collegate: frozenset[str] = frozenset()
    collegamenti_ok: bool = False
    impegni_bando: frozenset[int] = frozenset()
    esposizioni_7g: int = 0


def _interi(valori: Any) -> frozenset[int]:
    if not isinstance(valori, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(v for v in valori if isinstance(v, int) and not isinstance(v, bool))


def _codici(valori: Any, ammessi: Iterable[str]) -> tuple[str, ...]:
    if not isinstance(valori, (list, tuple)):
        return ()
    validi = set(ammessi)
    return tuple(dict.fromkeys(v for v in valori if isinstance(v, str) and v in validi))


def profilo_matching_da(
    base: ProfiloCandidato,
    *,
    company_id: str,
    owner_id: str,
    viva: bool,
    profilo_partner: Mapping | None = None,
    stato_impresa: str | None = None,
    collegate: Iterable[str] = (),
    collegamenti_ok: bool = False,
    impegni_bando: Iterable[int] = (),
    esposizioni_7g: int = 0,
) -> ProfiloMatching:
    """`ProfiloMatching` da un `ProfiloCandidato` (`profilo_candidato_da`) e
    dalla riga di `company_partner_profiles` (o None). Funzione pura: i dati
    di stato (azienda viva, registro, collegamenti, impegni, esposizioni) li
    passa chi ha letto le righe."""
    presente = isinstance(profilo_partner, Mapping)
    riga: Mapping = profilo_partner if presente else {}
    codice = riga.get("codice_pubblico")
    ruoli = _codici(riga.get("ruoli_disponibili"), voc.RUOLI)
    completezza = riga.get("completezza")
    return ProfiloMatching(
        **{f.name: getattr(base, f.name) for f in fields(ProfiloCandidato)},
        company_id=str(company_id),
        owner_id=str(owner_id),
        codice_pubblico=str(codice) if codice else None,
        viva=bool(viva),
        visibile=riga.get("visibile_come_partner") is True,
        sospeso=riga.get("sospeso_at") is not None,
        anonimo=riga.get("anonimo") is not False,
        stato_impresa=stato_impresa if isinstance(stato_impresa, str) else None,
        accetta_inviti=riga.get("accetta_inviti") is not False,
        ruoli_disponibili=(ruoli or None) if presente else None,
        forme_accettate=_codici(riga.get("forme_accettate"), FORME_PROFILO),
        categorie_bando_escluse=_interi(riga.get("categorie_bando_escluse")),
        regioni_interesse_ids=_interi(riga.get("regioni_interesse")),
        settori_interesse_ids=_interi(riga.get("settori_interesse")),
        completezza=completezza
        if isinstance(completezza, int) and not isinstance(completezza, bool)
        else 0,
        collegate=frozenset(str(c) for c in collegate),
        collegamenti_ok=bool(collegamenti_ok),
        impegni_bando=_interi(list(impegni_bando)),
        esposizioni_7g=max(0, int(esposizioni_7g or 0)),
    )


# ------------------------------------------------------------------ call


@dataclass(frozen=True)
class RequisitoMatching:
    """Un requisito della call. `etichetta` è già quella PUBBLICA (ripulita
    come nelle proiezioni verso terzi): finisce in spiegazioni e MatchOut."""

    id: str
    etichetta: str
    criterio: CriterioPartner | None = None
    ambito: str = "consorzio"
    cercato: bool = False
    ordine: int = 0


@dataclass(frozen=True)
class PosizioneMatching:
    """Una posizione cercata: `criteri` = `criteri_da_posizione` (WP5).
    `titolo` è già quello PUBBLICO."""

    id: str
    titolo: str
    ruolo: str = "partner"
    quota_pct: Decimal | None = None
    criteri: tuple[CriterioPartner, ...] = ()
    ordine: int = 0


@dataclass(frozen=True)
class CallSnapshot:
    """Dati deterministici di una call per il matching. `budget` è SOLO la
    fascia pubblica come intervallo (mai il budget esatto)."""

    id: str
    company_id: str
    owner_id: str
    bando_id: int
    creatore: ProfiloMatching
    stato: str = "pubblicata"
    visibilita: str = "pubblica"
    sospesa: bool = False
    ruolo_creatore: str = "capofila"
    forma: str | None = None
    esclusivita: bool = False
    programma_id: int | None = None
    tipologia_bando_id: int | None = None
    scadenza_call: date | None = None
    bando_scadenza: date | None = None
    budget: Intervallo | None = None
    requisiti: tuple[RequisitoMatching, ...] = ()
    posizioni: tuple[PosizioneMatching, ...] = ()


def _data(valore: Any) -> date | None:
    if isinstance(valore, date):
        return valore
    if isinstance(valore, str) and valore:
        try:
            return date.fromisoformat(valore[:10])
        except ValueError:
            return None
    return None


def _intero(valore: Any) -> int | None:
    return valore if isinstance(valore, int) and not isinstance(valore, bool) else None


def _testo_semplice(valore: Any) -> str | None:
    return valore.strip() if isinstance(valore, str) and valore.strip() else None


def _quota(valore: Any) -> Decimal | None:
    if valore is None or isinstance(valore, bool):
        return None
    try:
        quota = Decimal(str(valore))
    except (InvalidOperation, ValueError):
        return None
    return quota if quota.is_finite() and quota > 0 else None


def call_snapshot_da(
    call: Mapping,
    requisiti: Iterable[Mapping],
    posizioni: Iterable[Mapping],
    creatore: ProfiloMatching,
    *,
    pubblico: Callable[[Any], str | None] | None = None,
) -> CallSnapshot:
    """`CallSnapshot` dalle righe di `partner_calls`, `partner_call_requisiti`
    e `partner_call_posizioni` (funzione pura, per l'indice).

    `pubblico` ripulisce i testi che escono verso terzi (etichette dei
    requisiti, titoli delle posizioni): l'indice passa
    `lambda t: partenariato_accesso.testo_pubblico(t, ident)` del creatore.
    Il budget è la sola fascia pubblica: `budget_progetto_eur` non si legge."""
    pulisci = pubblico or _testo_semplice
    stato = str(call.get("stato") or "")
    lista_requisiti = [
        RequisitoMatching(
            id=str(riga["id"]),
            etichetta=pulisci(riga.get("etichetta")) or "?",
            criterio=criterio_da_json(riga.get("criterio")),
            ambito="ogni_membro" if riga.get("ambito") == "ogni_membro" else "consorzio",
            cercato=riga.get("cercato") is True,
            ordine=_intero(riga.get("ordine")) or 0,
        )
        for riga in requisiti
        if isinstance(riga, Mapping) and riga.get("id") is not None
    ]
    lista_posizioni: list[PosizioneMatching] = []
    for riga in posizioni:
        if not isinstance(riga, Mapping) or riga.get("id") is None:
            continue
        try:
            criteri = tuple(criteri_da_posizione(riga))
        except (ValidationError, ValueError, TypeError):
            # Fail-closed: una posizione con vincoli illeggibili non si offre.
            logger.warning("partenariati: posizione con vincoli non validi esclusa dal matching")
            continue
        ruolo = riga.get("ruolo")
        lista_posizioni.append(
            PosizioneMatching(
                id=str(riga["id"]),
                titolo=pulisci(riga.get("titolo")) or "Posizione",
                ruolo=ruolo if ruolo in voc.RUOLI else "partner",
                quota_pct=_quota(riga.get("quota_ipotizzata_pct")),
                criteri=criteri,
                ordine=_intero(riga.get("ordine")) or 0,
            )
        )
    forma = call.get("forma_aggregazione_prevista")
    return CallSnapshot(
        id=str(call["id"]),
        company_id=str(call["company_profile_id"]),
        owner_id=str(call["family_parent_id"]),
        bando_id=int(call["bando_id"]),
        creatore=creatore,
        stato=stato,
        visibilita="solo_invitati" if call.get("visibilita") == "solo_invitati" else "pubblica",
        sospesa=call.get("sospesa_at") is not None or stato == "sospesa_moderazione",
        ruolo_creatore="cerco_capofila"
        if call.get("ruolo_creatore") == "cerco_capofila"
        else "capofila",
        forma=forma if forma in FORME_PROFILO else None,  # «altra»: nessun filtro
        esclusivita=call.get("esclusivita") is True,
        programma_id=_intero(call.get("bando_programma_id")),
        tipologia_bando_id=_intero(call.get("bando_tipologia_id")),
        scadenza_call=_data(call.get("scadenza_call")),
        bando_scadenza=_data(call.get("bando_scadenza")),
        budget=intervallo_budget(call.get("budget_fascia")),
        requisiti=tuple(sorted(lista_requisiti, key=lambda r: (r.ordine, r.etichetta))),
        posizioni=tuple(sorted(lista_posizioni, key=lambda p: p.ordine)),
    )


@dataclass(frozen=True)
class IndiceMatching:
    """Dati puri dell'indice: call pubblicate e aziende candidabili (opt-in
    visibile), per id. `inviti` = call → aziende invitate (WP7; vuoto nel
    WP6). Lo costruisce e lo tiene aggiornato `partenariato_indice`."""

    calls: Mapping[str, CallSnapshot] = field(default_factory=dict)
    candidati: Mapping[str, ProfiloMatching] = field(default_factory=dict)
    inviti: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def da_liste(
        cls,
        calls: Iterable[CallSnapshot],
        candidati: Iterable[ProfiloMatching],
        inviti: Mapping[str, Iterable[str]] | None = None,
    ) -> "IndiceMatching":
        return cls(
            calls={c.id: c for c in calls},
            candidati={c.company_id: c for c in candidati},
            inviti={k: frozenset(v) for k, v in (inviti or {}).items()},
        )


# -------------------------------------------------------------- risultato


@dataclass(frozen=True)
class RequisitoRif:
    id: str
    etichetta: str


@dataclass(frozen=True)
class Attenzione:
    codice: str
    testo: str


@dataclass(frozen=True)
class PosizioneMatch:
    id: str
    titolo: str
    coperta: bool


@dataclass(frozen=True)
class Componenti:
    """Componenti del punteggio, frazioni esatte in [0, 1]."""

    copertura_gap: Fraction
    affinita: Fraction
    complementarita: Fraction
    completezza: Fraction
    rotazione: Fraction


@dataclass(frozen=True)
class MatchInterno:
    """Esito interno di una coppia (call, azienda). `company_id` e gli owner
    servono solo al server (cap per owner, pseudonimo, notifiche): verso
    l'esterno esce solo `proietta_match`."""

    call_id: str
    company_id: str
    candidato_owner_id: str
    call_owner_id: str
    codice_pubblico: str | None
    anonimo: bool
    invitabile: bool
    ruolo_creatore: str
    cercati: int
    copre: tuple[RequisitoRif, ...]
    non_copre: tuple[RequisitoRif, ...]
    attenzione: tuple[Attenzione, ...]
    posizioni: tuple[PosizioneMatch, ...]
    componenti: Componenti
    penalita: int
    punteggio: int
    fasce: Fasce | None
    spiegazione: str = ""
    dettaglio_proprio: tuple[str, ...] = ()

    @property
    def coperti(self) -> int:
        return len(self.copre)

    @property
    def posizione_coperta(self) -> bool:
        return any(p.coperta for p in self.posizioni)

    @property
    def rilevante(self) -> bool:
        """Da proporre: copre almeno un requisito cercato o una posizione."""
        return self.coperti >= 1 or self.posizione_coperta


# ------------------------------------------------------------ proiezione


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoperturaOut(_Out):
    coperti: int
    cercati: int


class RequisitoMatchOut(_Out):
    requisito_id: str
    etichetta: str


class AttenzioneOut(_Out):
    codice: str
    testo: str


class PosizioneMatchOut(_Out):
    id: str
    titolo: str


class FasceMatchOut(_Out):
    fatturato: str | None = None
    patrimonio_netto: str | None = None
    dipendenti: str | None = None
    trend: str | None = None


class MatchOut(_Out):
    """Il match come esce dalle API. Vista «terzi»: solo fasce (anonimi: la
    sola fascia di fatturato, Q12) ed esiti, nessun importo. Vista «proprio»:
    tutte le proprie fasce e, in `dettaglio`, i propri valori.

    Il punteggio NON esce in nessuna vista: affinità, complementarità e
    rotazione usano dati della controparte che la sua proiezione non mostra
    (competenze e sedi del creatore, esposizioni del candidato), e un numero
    esatto che cambia al variare del proprio profilo li farebbe ricavare.
    Resta interno (ordinamento, soglia delle notifiche)."""

    copertura: CoperturaOut
    copre: list[RequisitoMatchOut]
    non_copre: list[RequisitoMatchOut]
    attenzione: list[AttenzioneOut]
    posizioni_compatibili: list[PosizioneMatchOut]
    fasce: FasceMatchOut | None
    spiegazione: str
    dettaglio: list[str] | None = None


def _fasce_out(fasce: Fasce | None, *, solo_fatturato: bool) -> FasceMatchOut | None:
    if fasce is None:
        return None
    if solo_fatturato:
        if fasce.fatturato is None:
            return None
        return FasceMatchOut(fatturato=fasce.fatturato)
    out = FasceMatchOut(
        fatturato=fasce.fatturato,
        patrimonio_netto=fasce.patrimonio_netto,
        dipendenti=fasce.dipendenti,
        trend=fasce.trend_fatturato,
    )
    return out if any(out.model_dump().values()) else None


def proietta_match(m: MatchInterno, *, vista: Vista) -> MatchOut:
    """Proiezione a whitelist. «terzi» = il creatore che guarda un candidato
    (o chiunque non sia il candidato); «proprio» = il candidato che guarda il
    proprio match con una call."""
    proprio = vista == "proprio"
    return MatchOut(
        copertura=CoperturaOut(coperti=m.coperti, cercati=m.cercati),
        copre=[RequisitoMatchOut(requisito_id=r.id, etichetta=r.etichetta) for r in m.copre],
        non_copre=[
            RequisitoMatchOut(requisito_id=r.id, etichetta=r.etichetta) for r in m.non_copre
        ],
        attenzione=[AttenzioneOut(codice=a.codice, testo=a.testo) for a in m.attenzione],
        posizioni_compatibili=[PosizioneMatchOut(id=p.id, titolo=p.titolo) for p in m.posizioni],
        fasce=_fasce_out(m.fasce, solo_fatturato=m.anonimo and not proprio),
        spiegazione=m.spiegazione,
        dettaglio=list(m.dettaglio_proprio) if proprio else None,
    )


# ------------------------------------------------------------ spiegazioni


def _elenco(etichette: Sequence[str]) -> str:
    voci = [f"«{e}»" for e in etichette]
    if len(voci) == 1:
        return voci[0]
    return ", ".join(voci[:-1]) + " e " + voci[-1]


def spiegazione(m: MatchInterno, *, ruolo_creatore: str | None = None) -> str:
    """Spiegazione deterministica (nessun LLM, M3). Parla al candidato ed è
    la stessa che vede il creatore: «Copri «A» e «C», che mancano al
    capofila.» («…al proponente.» se il creatore cerca un capofila)."""
    ruolo = ruolo_creatore or m.ruolo_creatore
    destinatario = "al proponente" if ruolo == "cerco_capofila" else "al capofila"
    if m.copre:
        verbo = "manca" if len(m.copre) == 1 else "mancano"
        return f"Copri {_elenco([r.etichetta for r in m.copre])}, che {verbo} {destinatario}."
    coperta = next((p for p in m.posizioni if p.coperta), None)
    if coperta is not None:
        return f"Corrispondi alla posizione «{coperta.titolo}»."
    return "Sei compatibile con la call, ma non copri requisiti mancanti."


# ------------------------------------------------------------- punteggio


def _arrotonda(valore: Fraction) -> int:
    """Half-up su frazione esatta (niente banker's rounding né float)."""
    return math.floor(valore + Fraction(1, 2))


def calcola_punteggio(
    componenti: Componenti, n_attenzione: int, pesi: PesiMatching = PESI_PREDEFINITI
) -> tuple[int, int]:
    """(punteggio 0..100, penalità applicata)."""
    somma = (
        Fraction(str(pesi.copertura)) * componenti.copertura_gap
        + Fraction(str(pesi.affinita)) * componenti.affinita
        + Fraction(str(pesi.complementarita)) * componenti.complementarita
        + Fraction(str(pesi.completezza)) * componenti.completezza
        + Fraction(str(pesi.rotazione)) * componenti.rotazione
    )
    penalita = min(pesi.penalita_max, pesi.penalita_dato_mancante * max(0, n_attenzione))
    return max(0, min(100, _arrotonda(100 * somma) - penalita)), penalita


def _regioni_call(call: CallSnapshot) -> frozenset[int]:
    regioni = set(call.creatore.regioni_ids)
    criteri = [r.criterio for r in call.requisiti] + [
        c for p in call.posizioni for c in p.criteri
    ]
    for criterio in criteri:
        if isinstance(criterio, CriterioRegione):
            regioni.update(criterio.regioni_ids)
    return frozenset(regioni)


def componenti_punteggio(
    call: CallSnapshot,
    cand: ProfiloMatching,
    *,
    coperti: int,
    cercati: int,
    posizione_coperta: bool,
    pesi: PesiMatching = PESI_PREDEFINITI,
) -> Componenti:
    """Le cinque componenti (formule nel docstring del modulo)."""
    if cercati:
        copertura = Fraction(coperti, cercati)
    else:
        copertura = Fraction(1 if posizione_coperta else 0)

    creatore = call.creatore
    programma = call.programma_id is not None and any(
        e.programma_id == call.programma_id for e in cand.esperienze
    )
    settore = bool(set(cand.ateco_divisioni) & set(creatore.ateco_divisioni)) or bool(
        (cand.settori_ids | cand.settori_interesse_ids) & creatore.settori_ids
    )
    regioni = bool((cand.regioni_ids | cand.regioni_interesse_ids) & _regioni_call(call))
    affinita = Fraction(int(programma) + int(settore) + int(regioni), 3)

    proprie = set(cand.competenze)
    complementarita = (
        Fraction(len(proprie - set(creatore.competenze)), len(proprie))
        if proprie
        else Fraction(0)
    )
    completezza = Fraction(min(100, max(0, cand.completezza)), 100)
    massimo = max(1, pesi.esposizioni_max)
    rotazione = max(Fraction(0), 1 - Fraction(max(0, cand.esposizioni_7g), massimo))
    return Componenti(
        copertura_gap=copertura,
        affinita=affinita,
        complementarita=complementarita,
        completezza=completezza,
        rotazione=rotazione,
    )


# -------------------------------------------------------------- analisi


@dataclass
class _Posizione:
    posizione: PosizioneMatching
    attenzione: list[Attenzione] = field(default_factory=list)
    coperta: bool = False


@dataclass
class _Analisi:
    posizioni: list[_Posizione]
    attenzione: list[Attenzione]
    copre: list[RequisitoRif]
    non_copre: list[RequisitoRif]
    cercati: int


def _call_attiva(call: CallSnapshot, oggi: date) -> bool:
    if call.stato != "pubblicata" or call.sospesa:
        return False
    if call.scadenza_call is not None and call.scadenza_call < oggi:
        return False
    if call.bando_scadenza is not None and call.bando_scadenza < oggi:
        return False
    return call.creatore.viva


def _cessata(cand: ProfiloMatching) -> bool:
    stato = cand.stato_impresa
    return isinstance(stato, str) and stato.strip().casefold() != STATO_IMPRESA_ATTIVA.casefold()


def _attenzione_criterio(
    criterio: CriterioPartner, esito: EsitoCriterio, prefisso: str
) -> Attenzione:
    codice = _ATTENZIONE_CRITERIO.get(criterio.tipo, "requisito_da_verificare")
    return Attenzione(codice=codice, testo=f"{prefisso}: {esito.testo_pubblico}")


def _valuta_regola(
    requisito: RequisitoMatching, cand: ProfiloMatching, costo: Intervallo | None, vista: Vista
):
    """Valutazione di una regola finanziaria; None se l'aritmetica degli
    intervalli non la regge (difesa: una coppia non deve fermare il ranking;
    si tratta come dato mancante)."""
    try:
        return valuta_criterio_dettaglio(
            requisito.criterio, cand, costo_quota=costo, vista=vista
        )
    except ArithmeticError:
        logger.warning("partenariati: regola finanziaria non valutabile (aritmetica)")
        return None


def _attenzione_finanza(
    requisito: RequisitoMatching, cand: ProfiloMatching, motivo: str | None
) -> Attenzione:
    if not cand.esercizi:
        codice = "bilanci_non_disponibili"
    elif motivo in _CODICI_MOTIVO_FINANZA:
        codice = motivo
    else:
        codice = "finanziaria_dato_mancante"
    return Attenzione(
        codice=codice, testo=_ATTENZIONE_FINANZA[codice].format(e=requisito.etichetta)
    )


def _regola_applicabile(
    requisito: RequisitoMatching, call: CallSnapshot, posizione: PosizioneMatching | None
) -> bool:
    ambito = requisito.criterio.regola.ambito
    if ambito == "ciascun_partner":
        return True
    if ambito == "capofila":
        if posizione is None:
            return call.ruolo_creatore == "cerco_capofila"
        return posizione.ruolo == "capofila"
    return False  # media pesata, partenariato totale: validatore (WP8)


def _costo(call: CallSnapshot, posizione: PosizioneMatching | None) -> Intervallo | None:
    if posizione is None:
        return None
    return intervallo_costo_quota(call.budget, posizione.quota_pct)


def _analizza(
    call: CallSnapshot,
    cand: ProfiloMatching,
    *,
    oggi: date,
    direzione: Direzione,
    invitati: frozenset[str],
) -> tuple[str | None, _Analisi | None]:
    """(codice di esclusione, None) oppure (None, analisi completa)."""
    # 0. la call
    if not _call_attiva(call, oggi):
        return "call_non_attiva", None
    # 1. stato del candidato («Per te» è scoperta: anche senza opt-in)
    scoperta = direzione == "per_te"
    if not cand.visibile and not scoperta:
        return "non_visibile", None
    if cand.sospeso:
        return "sospeso", None
    if not cand.viva:
        return "non_viva", None
    if _cessata(cand):
        return "cessata", None
    # 2. stesso owner (e stessa azienda)
    if cand.owner_id == call.owner_id or cand.company_id == call.company_id:
        return "stesso_owner", None
    # 3. collegamenti: del candidato solo se visibile (senza opt-in non si
    #    calcolano, M2); del creatore sempre (ha una call non chiusa)
    if not call.creatore.collegamenti_ok or (cand.visibile and not cand.collegamenti_ok):
        return "collegamenti_non_calcolati", None
    if cand.company_id in call.creatore.collegate or call.company_id in cand.collegate:
        return "collegata", None
    # 4. categoria del bando e forma
    if call.tipologia_bando_id is not None and (
        call.tipologia_bando_id in cand.categorie_bando_escluse
    ):
        return "categoria_esclusa", None
    if call.forma and cand.forme_accettate and call.forma not in cand.forme_accettate:
        return "forma_non_accettata", None

    # 5. posizioni compatibili per ruolo e tipo di soggetto
    compatibili: list[_Posizione] = []
    for posizione in call.posizioni:
        if cand.ruoli_disponibili is not None and posizione.ruolo not in cand.ruoli_disponibili:
            continue
        stato = _Posizione(posizione)
        tipo = next((c for c in posizione.criteri if isinstance(c, CriterioTipoSoggetto)), None)
        if tipo is not None:
            esito = valuta_criterio(tipo, cand, vista="terzi")
            if esito.esito == "non_coperto":
                continue
            if esito.esito in _ESITI_DUBBI:
                stato.attenzione.append(
                    _attenzione_criterio(tipo, esito, f"Posizione «{posizione.titolo}»")
                )
        compatibili.append(stato)
    if call.posizioni and not compatibili:
        return "nessuna_posizione_compatibile", None

    # 6. vincoli di ogni membro: requisiti, poi vincoli delle posizioni
    membro = [
        r
        for r in call.requisiti
        if r.ambito == "ogni_membro"
        and r.criterio is not None
        and not isinstance(r.criterio, (CriterioRegolaFinanziaria, CriterioManuale))
    ]
    esiti: dict[str, EsitoCriterio] = {
        r.id: valuta_criterio(r.criterio, cand, vista="terzi") for r in membro
    }
    for codice, tipo_criterio in _VINCOLI_MEMBRO:
        for r in membro:
            if isinstance(r.criterio, tipo_criterio) and esiti[r.id].esito == "non_coperto":
                return codice, None
    rimaste: list[_Posizione] = []
    codici_falliti: list[str] = []
    for stato in compatibili:
        fallito = None
        for codice, tipo_criterio in _VINCOLI_MEMBRO:
            for criterio in stato.posizione.criteri:
                if not isinstance(criterio, tipo_criterio):
                    continue
                esito = valuta_criterio(criterio, cand, vista="terzi")
                if esito.esito == "non_coperto":
                    fallito = fallito or codice
                elif esito.esito in _ESITI_DUBBI:
                    stato.attenzione.append(
                        _attenzione_criterio(
                            criterio, esito, f"Posizione «{stato.posizione.titolo}»"
                        )
                    )
        if fallito is None:
            rimaste.append(stato)
        else:
            codici_falliti.append(fallito)
    if compatibili and not rimaste:
        ordine = [c for c, _ in _VINCOLI_MEMBRO]
        return min(codici_falliti, key=ordine.index), None
    attenzione: list[Attenzione] = []
    for r in membro:
        esito = esiti[r.id]
        # Qui restano solo esiti non certi di territorio, paese e dimensione
        # (i «non coperto» hanno già escluso) e gli altri criteri: un loro
        # «non coperto» è un dato non dichiarato, da verificare (M1).
        if esito.esito in _ESITI_DUBBI or esito.esito == "non_coperto":
            attenzione.append(_attenzione_criterio(r.criterio, esito, f"Requisito «{r.etichetta}»"))

    # 7. regole finanziarie, sulla fascia, su ogni posizione rimasta
    regole = [r for r in call.requisiti if isinstance(r.criterio, CriterioRegolaFinanziaria)]
    candidate_pos: list[_Posizione | None] = list(rimaste) if call.posizioni else [None]
    superate: list[_Posizione | None] = []
    for stato in candidate_pos:
        posizione = stato.posizione if stato is not None else None
        costo = _costo(call, posizione)
        fallita = False
        dubbi: list[Attenzione] = []
        for r in regole:
            if not _regola_applicabile(r, call, posizione):
                continue
            valutazione = _valuta_regola(r, cand, costo, "terzi")
            if valutazione is None:
                dubbi.append(_attenzione_finanza(r, cand, None))
                continue
            if valutazione.esito.esito == "non_coperto":
                fallita = True
                break
            if valutazione.esito.esito in _ESITI_DUBBI:
                dubbi.append(_attenzione_finanza(r, cand, valutazione.motivo))
        if fallita:
            continue
        if stato is None:
            attenzione.extend(dubbi)
        else:
            stato.attenzione.extend(dubbi)
        superate.append(stato)
    if not superate:
        return "finanziaria_non_soddisfatta", None
    rimaste = [s for s in superate if s is not None]

    # 8. esclusività
    if call.esclusivita and call.bando_id in cand.impegni_bando:
        return "esclusivita", None
    # 9. call solo su invito
    if call.visibilita == "solo_invitati":
        if scoperta and cand.company_id not in invitati:
            return "solo_invitati", None
        if not scoperta and not cand.accetta_inviti:
            return "solo_invitati", None

    # Copertura dei requisiti cercati (gap) e delle posizioni.
    copre: list[RequisitoRif] = []
    non_copre: list[RequisitoRif] = []
    cercati = [r for r in call.requisiti if r.cercato]
    for r in cercati:
        if isinstance(r.criterio, CriterioRegolaFinanziaria):
            migliori = []
            for stato in rimaste or [None]:
                valutazione = _valuta_regola(
                    r, cand, _costo(call, stato.posizione if stato else None), "terzi"
                )
                migliori.append(valutazione.esito.esito if valutazione else "dato_mancante")
            esito = min(migliori, key=lambda e: _MIGLIORE.get(e, 5))
        elif r.id in esiti:
            esito = esiti[r.id].esito
        else:
            esito = valuta_criterio(r.criterio, cand, vista="terzi").esito
        (copre if esito == "coperto" else non_copre).append(RequisitoRif(r.id, r.etichetta))
    for stato in rimaste:
        criteri = stato.posizione.criteri
        stato.coperta = bool(criteri) and all(
            valuta_criterio(c, cand, vista="terzi").esito == "coperto" for c in criteri
        )
    return None, _Analisi(
        posizioni=rimaste,
        attenzione=attenzione,
        copre=copre,
        non_copre=non_copre,
        cercati=len(cercati),
    )


# ------------------------------------------------------------------- API


def esclusione(
    call: CallSnapshot,
    cand: ProfiloMatching,
    *,
    oggi: date,
    direzione: Direzione = "suggeriti",
    invitati: Iterable[str] = (),
) -> str | None:
    """Codice del primo filtro rigido che esclude la coppia (None = passa).
    Interno: MAI mostrato al creatore per le aziende di terzi."""
    codice, _ = _analizza(
        call, cand, oggi=oggi, direzione=direzione, invitati=frozenset(invitati)
    )
    return codice


def posizioni_compatibili(
    call: CallSnapshot,
    cand: ProfiloMatching,
    *,
    oggi: date,
    direzione: Direzione = "suggeriti",
) -> list[PosizioneMatch]:
    """Posizioni che il candidato può occupare dopo TUTTI i filtri (vuota se
    la coppia è esclusa)."""
    codice, analisi = _analizza(call, cand, oggi=oggi, direzione=direzione, invitati=frozenset())
    if codice is not None or analisi is None:
        return []
    return [PosizioneMatch(s.posizione.id, s.posizione.titolo, s.coperta) for s in analisi.posizioni]


def _dedup(voci: Iterable[Attenzione]) -> tuple[Attenzione, ...]:
    return tuple(dict.fromkeys(voci))


def _dettaglio_proprio(
    call: CallSnapshot, cand: ProfiloMatching, posizione: PosizioneMatching | None
) -> tuple[str, ...]:
    """Per il candidato stesso: i propri valori nelle regole finanziarie
    (vista «proprio»). Il costo della quota viene dalla fascia pubblica."""
    righe: list[str] = []
    for r in call.requisiti:
        if not isinstance(r.criterio, CriterioRegolaFinanziaria):
            continue
        if not _regola_applicabile(r, call, posizione) and not r.cercato:
            continue
        valutazione = _valuta_regola(r, cand, _costo(call, posizione), "proprio")
        if valutazione is not None and valutazione.esito.testo_privato:
            righe.append(f"«{r.etichetta}»: {valutazione.esito.testo_privato}")
    return tuple(righe)


def valuta_coppia(
    call: CallSnapshot,
    cand: ProfiloMatching,
    *,
    oggi: date,
    pesi: PesiMatching = PESI_PREDEFINITI,
    direzione: Direzione = "suggeriti",
    invitati: Iterable[str] = (),
    dettaglio_proprio: bool = False,
) -> MatchInterno | None:
    """Match della coppia, None se un filtro rigido la esclude.

    `direzione`: «suggeriti» (il creatore cerca aziende) o «per_te» (l'azienda
    cerca call: vale anche senza opt-in, Q25). `dettaglio_proprio`: aggiunge i
    valori propri del candidato (solo per la sua vista «proprio»)."""
    codice, analisi = _analizza(
        call, cand, oggi=oggi, direzione=direzione, invitati=frozenset(invitati)
    )
    if codice is not None or analisi is None:
        return None
    posizioni = [
        PosizioneMatch(s.posizione.id, s.posizione.titolo, s.coperta) for s in analisi.posizioni
    ]
    # Attenzione: vincoli di ogni membro + la posizione migliore (coperta,
    # poi meno dubbi, poi l'ordine della call).
    scelta = min(
        analisi.posizioni,
        key=lambda s: (not s.coperta, len(s.attenzione), s.posizione.ordine),
        default=None,
    )
    attenzione = _dedup([*analisi.attenzione, *(scelta.attenzione if scelta else [])])
    componenti = componenti_punteggio(
        call,
        cand,
        coperti=len(analisi.copre),
        cercati=analisi.cercati,
        posizione_coperta=any(p.coperta for p in posizioni),
        pesi=pesi,
    )
    punteggio, penalita = calcola_punteggio(componenti, len(attenzione), pesi)
    match = MatchInterno(
        call_id=call.id,
        company_id=cand.company_id,
        candidato_owner_id=cand.owner_id,
        call_owner_id=call.owner_id,
        codice_pubblico=cand.codice_pubblico,
        anonimo=cand.anonimo,
        invitabile=cand.accetta_inviti,
        ruolo_creatore=call.ruolo_creatore,
        cercati=analisi.cercati,
        copre=tuple(analisi.copre),
        non_copre=tuple(analisi.non_copre),
        attenzione=attenzione,
        posizioni=tuple(posizioni),
        componenti=componenti,
        penalita=penalita,
        punteggio=punteggio,
        fasce=cand.fasce,
        dettaglio_proprio=(
            _dettaglio_proprio(call, cand, scelta.posizione if scelta else None)
            if dettaglio_proprio
            else ()
        ),
    )
    return replace(match, spiegazione=spiegazione(match))


# --------------------------------------------------------------- ordine


def settimana_iso(oggi: date) -> str:
    anno, settimana, _ = oggi.isocalendar()
    return f"{anno}-W{settimana:02d}"


def chiave_rotazione(call_id: str, codice_pubblico: str | None, settimana: str) -> str:
    """Tie-break deterministico che cambia ogni settimana ISO."""
    return hashlib.sha256(f"{call_id}:{codice_pubblico or ''}:{settimana}".encode()).hexdigest()


def ordina(items: Iterable[MatchInterno], *, oggi: date) -> list[MatchInterno]:
    """Requisiti cercati coperti desc, punteggio desc, tie-break settimanale
    (poi gli id, solo perché l'ordine sia totale)."""
    settimana = settimana_iso(oggi)
    return sorted(
        items,
        key=lambda m: (
            -m.coperti,
            -m.punteggio,
            chiave_rotazione(m.call_id, m.codice_pubblico, settimana),
            m.call_id,
            m.company_id,
        ),
    )


def owner_candidato(m: MatchInterno) -> str:
    return m.candidato_owner_id


def owner_call(m: MatchInterno) -> str:
    return m.call_owner_id


def impagina(
    items: Sequence[MatchInterno],
    *,
    dimensione: int,
    max_per_owner: int = MAX_PER_OWNER_PAGINA,
    owner: Callable[[MatchInterno], str] = owner_candidato,
) -> list[list[MatchInterno]]:
    """Pagine nell'ordine dato con al massimo `max_per_owner` elementi dello
    stesso owner per pagina: chi non entra slitta alle pagine successive
    (conservando l'ordine). Una pagina si chiude piena o quando nessun
    elemento rimasto può entrarci."""
    dimensione = max(1, dimensione)
    massimo = max(1, max_per_owner)
    restanti = list(items)
    pagine: list[list[MatchInterno]] = []
    while restanti:
        pagina: list[MatchInterno] = []
        conteggi: Counter[str] = Counter()
        rinviati: list[MatchInterno] = []
        for m in restanti:
            chiave = owner(m)
            if len(pagina) < dimensione and conteggi[chiave] < massimo:
                pagina.append(m)
                conteggi[chiave] += 1
            else:
                rinviati.append(m)
        pagine.append(pagina)
        restanti = rinviati
    return pagine


def pagina(
    items: Sequence[MatchInterno],
    numero: int,
    *,
    dimensione: int,
    max_per_owner: int = MAX_PER_OWNER_PAGINA,
    owner: Callable[[MatchInterno], str] = owner_candidato,
) -> tuple[list[MatchInterno], bool]:
    """(elementi della pagina `numero` (da 1), ci sono altre pagine)."""
    pagine = impagina(items, dimensione=dimensione, max_per_owner=max_per_owner, owner=owner)
    if numero < 1 or numero > len(pagine):
        return [], False
    return pagine[numero - 1], numero < len(pagine)


# ------------------------------------------------------ viste sull'indice


@dataclass(frozen=True)
class FiltriSuggeriti:
    """Filtri facoltativi dei suggeriti (la bacheca e il fan-out li usano)."""

    posizione_id: str | None = None
    min_coperti: int = 0
    min_punteggio: int = 0


def suggeriti_per_call(
    indice: IndiceMatching,
    call_id: str,
    filtri: FiltriSuggeriti | None = None,
    *,
    oggi: date,
    pesi: PesiMatching = PESI_PREDEFINITI,
) -> list[MatchInterno]:
    """Aziende da suggerire al creatore della call, ordinate (senza il cap
    per owner: lo applica `impagina`). Solo match rilevanti."""
    call = indice.calls.get(str(call_id))
    if call is None:
        return []
    filtri = filtri or FiltriSuggeriti()
    risultati: list[MatchInterno] = []
    for cand in indice.candidati.values():
        m = valuta_coppia(call, cand, oggi=oggi, pesi=pesi, direzione="suggeriti")
        if m is None or not m.rilevante:
            continue
        if filtri.posizione_id is not None and all(
            p.id != filtri.posizione_id for p in m.posizioni
        ):
            continue
        if m.coperti < filtri.min_coperti or m.punteggio < filtri.min_punteggio:
            continue
        risultati.append(m)
    return ordina(risultati, oggi=oggi)


def per_te(
    indice: IndiceMatching,
    candidato: str | ProfiloMatching,
    *,
    oggi: date,
    pesi: PesiMatching = PESI_PREDEFINITI,
) -> list[MatchInterno]:
    """Call da proporre a un'azienda, ordinate: solo con almeno un requisito
    cercato coperto o una posizione coperta; mai le call della stessa azienda
    o dello stesso owner, mai quelle solo su invito senza invito.

    `candidato`: l'id di un'azienda dell'indice oppure il suo profilo già
    costruito (serve per l'azienda attiva senza opt-in, che nell'indice non
    c'è: Q25)."""
    cand = (
        indice.candidati.get(str(candidato)) if isinstance(candidato, str) else candidato
    )
    if cand is None:
        return []
    risultati: list[MatchInterno] = []
    for call in indice.calls.values():
        m = valuta_coppia(
            call,
            cand,
            oggi=oggi,
            pesi=pesi,
            direzione="per_te",
            invitati=indice.inviti.get(call.id, frozenset()),
        )
        if m is not None and m.rilevante:
            risultati.append(m)
    return ordina(risultati, oggi=oggi)
