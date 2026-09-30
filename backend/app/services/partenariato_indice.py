"""Indice in-process del matching dei partenariati (WP6, docs/partenariati.md
M4, T3, T8).

Tiene in memoria, per il solo processo uvicorn, ciò che serve al matching
puro (`partenariato_matching`): le call PUBBLICATE su bandi ancora aperti
(`CallSnapshot`) e le aziende candidabili (opt-in visibile) più i creatori
delle call (`ProfiloMatching`), con i dati per le card della bacheca.
Niente riga di versione condivisa (niente hot row): TTL di
`partenariato_indice_ttl_seconds` (60 s) e `invalida()` chiamata dai
servizi del modulo (consenso e profilo, call, collegamenti, import). La revoca
resta immediata perché OGNI pagina restituita passa dal ricontrollo live
(`ricontrollo_live` per le aziende, `ricontrollo_call_live` per le call)
prima di uscire.

Caricamento (`_ricarica`, sotto `asyncio.Lock`, una sola ricarica alla
volta), letture paginate a keyset (pagine da 1000, il max-rows di PostgREST)
e `in_` a blocchi di 100 id:
1. call pubblicate (senza budget esatto, riservati, quota del creatore);
2. requisiti e posizioni delle call (solo le colonne del matching);
3. profili partner visibili (i candidati) e quelli dei creatori;
4. aziende a blocchi, con gli embed dell'owner (`is_active`), dei bilanci
   fusi, dello stato dello storico, delle chiavi HMAC e del marker dei
   collegamenti;
5. dati del registro (`derived`, stato impresa, `fetched_at`) più pochi
   sotto-alberi di `raw` per il dossier (forma giuridica, flag delle sezioni
   speciali, ATECO) e, SOLO per i creatori, P.IVA e sito del registro per
   ripulire i testi delle loro call: mai `raw` intero, mai il CF di una
   persona, nulla di questo resta nell'indice;
6. esposizioni degli ultimi 7 giorni: notifiche proattive e, dal WP7, inviti
   ricevuti;
7. stato LIVE dei bandi dal secondario (cache di 10 minuti): una call su un
   bando non aperto (o sparito dal catalogo) non entra nell'indice. Un id
   assente da `bando_pubblico` si controlla su `bando_fusione`: per un
   doppione fuso, o se `bando_fusione` non si legge, vale lo snapshot della
   call (un fuso o un errore non valgono «sparito»);
8. (WP7) candidature e inviti ATTIVI (`inviata` o `accettata`) in una sola
   lettura: gli inviti in attesa non scaduti e quelli accettati aprono le call
   solo su invito all'azienda invitata («Per te», `IndiceMatching.inviti`);
   le candidature accettate sono impegni sul bando (esclusività, con la
   stessa regola simmetrica di `fn_partner_esclusivita_violata`: non su una
   call annullata). WP8: nella stessa lettura (embed, nessuna query in più)
   la riga del consorzio nata dalla candidatura: se c'è prevale, e un membro
   USCITO non è più impegnato (le righe dei membri nascono solo dalle
   accettazioni, 0040); gli inviti di questa lettura creati negli ultimi 7
   giorni contano nelle esposizioni. Gli inviti già chiusi (rifiutati,
   ritirati, scaduti) negli ultimi 7 giorni NON contano: leggerli costerebbe
   un'altra query oltre il budget. Il bando di una call non pubblicata con
   una candidatura accettata si legge a parte, solo se ce n'è una.
Budget: ≤ 20 query per 500 aziende e 200 call (`Indice.query`).

T5: i dati del registro valgono solo se sono dell'azienda
(`piva_fetched` = `partita_iva`); il confronto avviene qui e la P.IVA non
entra nell'indice. Collegamenti (M2): chiavi e marker di
`partenariato_collegamenti`; le coppie collegate le calcola
`collegamenti_tra` (solo coppie con una chiave in comune).

Log: solo id e codici; mai testi, P.IVA, CF, nomi o importi.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.services import bando_fonti_service, partenariato_collegamenti
from app.services import partenariato_matching as pm
from app.services.openapi_mapping import build_dossier
from app.services.partenariato_accesso import (
    LUNGHEZZA_PSEUDONIMO,
    pseudonimo,
    testo_pubblico,
)
from app.services.partenariato_anonimato import identificativi_azienda
from app.services.partenariato_collegamenti import ChiaveCollegamento
from app.services.partenariato_criteri import profilo_candidato_da

logger = logging.getLogger("bandofit.partenariati")

PAGINA = 1000
BLOCCO_ID = 100
STATO_BANDI_TTL_SECONDI = 600
FINESTRA_ESPOSIZIONI = timedelta(days=7)
STATI_BANDO_APERTI = frozenset({"aperto", "in apertura prossimamente"})

CALL_INDICE_SELECT = (
    "id,company_profile_id,family_parent_id,bando_id,bando_slug,bando_titolo,bando_scadenza,"
    "bando_programma_id,bando_tipologia_id,bando_stato_effettivo,ruolo_creatore,"
    "forma_aggregazione_prevista,titolo,budget_fascia,scadenza_call,visibilita,stato,"
    "esclusivita,pubblicata_at,sospesa_at"
)
REQUISITO_INDICE_SELECT = "id,call_id,etichetta,criterio,ambito,cercato,ordine"
# Candidature e inviti attivi (WP7): mai messaggi, valutazioni né utenti. WP8:
# lo stato della riga del consorzio nata dalla candidatura (uscito = libero).
CANDIDATURA_INDICE_SELECT = (
    "id,partner_call_id,company_profile_id,tipo,stato,scade_at,created_at,"
    "partner_call_membri(stato)"
)
STATI_CANDIDATURA_ATTIVI = ("inviata", "accettata")
POSIZIONE_INDICE_SELECT = (
    "id,call_id,titolo,ruolo,tipi_soggetto,competenze,ateco_divisioni,regioni,"
    "territorio_modalita,paesi,dimensioni,quota_ipotizzata_pct,numero,ordine"
)
PROFILO_INDICE_SELECT = (
    "company_profile_id,codice_pubblico,visibile_come_partner,anonimo,accetta_inviti,"
    "sospeso_at,competenze,tipi_soggetto,certificazioni,esperienze,ruoli_disponibili,"
    "forme_accettate,categorie_bando_escluse,regioni_interesse,settori_interesse,completezza"
)
_CAMPI_BILANCIO = (
    "fatturato,valore_produzione,risultato_esercizio,patrimonio_netto,capitale_sociale,"
    "totale_attivo,debiti_totali,disponibilita_liquide,ebitda,ebit,cash_flow,"
    "oneri_finanziari,dipendenti,costo_personale,retribuzione_media_lorda"
)
_AZIENDA_EMBED = (
    "profiles(is_active),"
    f"company_financials(anno,data_chiusura,tipo_bilancio,fonte_per_campo,{_CAMPI_BILANCIO}),"
    "company_financials_stato(advanced_esito),"
    "company_collegamenti(tipo,chiave,quota),"
    "company_collegamenti_stato(algoritmo_versione,fonte_fetched_at)"
)
# P.IVA e ragione sociale: transitorie, per il confronto T5 e per verificare
# che le chiavi d'identità salvate siano quelle attuali (marker dei
# collegamenti); il codice fiscale no (per una ditta individuale è di una
# persona: lo confronta solo il backfill dei collegamenti).
AZIENDA_INDICE_SELECT = (
    f"id,parent_id,settore_id,partita_iva,ragione_sociale,deleted_at,archived_at,{_AZIENDA_EMBED}"
)
# Creatori: anche il sito, per ripulire i testi delle loro call.
AZIENDA_CREATORE_SELECT = f"{AZIENDA_INDICE_SELECT},sito_web"
# Sotto-alberi di `raw` per il dossier: mai `raw` intero.
_RAW_DOSSIER = (
    "r_forma:raw->legalForm,r_innovative:raw->innovativeSmeAndSu,"
    "r_artigiana:raw->artisanBusinessRegistry,r_soa:raw->soaCertification,"
    "r_ateco:raw->atecoClassification"
)
DATI_INDICE_SELECT = (
    f"company_profile_id,derived,piva_fetched,stato_impresa,fetched_at,{_RAW_DOSSIER}"
)
DATI_CREATORE_SELECT = (
    f"{DATI_INDICE_SELECT},denominazione,r_piva:raw->companyDetails->>vatCode,"
    "r_sito:raw->webAndSocial->>website"
)
_RAMI_DOSSIER = {
    "r_forma": "legalForm",
    "r_innovative": "innovativeSmeAndSu",
    "r_artigiana": "artisanBusinessRegistry",
    "r_soa": "soaCertification",
    "r_ateco": "atecoClassification",
}
# Campi di `derived` che servono alle vetrine (regione, sezione ATECO, classe).
_DERIVED_VETRINA = ("regione_id", "regione_nome", "ateco_principale", "ateco_divisione",
                    "classe_dimensionale")


# ------------------------------------------------------------------ dati


@dataclass(frozen=True)
class Vetrina:
    """Ciò che del creatore di una call compare sulla card, tutto dal
    registro (T5) e solo se il registro è dell'azienda: `registro` =
    `{"derived": {...}}` con i soli campi pubblici, `dossier` = sola attività
    ATECO. Per `partenariato_accesso.creatore_pubblico`."""

    registro: Mapping | None = None
    dossier: Mapping | None = None


@dataclass(frozen=True)
class CallIndicizzata:
    """Una call della bacheca: le sole colonne pubbliche della riga (il
    titolo già ripulito), i contatori e ciò che serve ai filtri."""

    riga: Mapping
    posizioni_n: int = 0
    posti: int = 0
    requisiti_cercati_n: int = 0
    regioni_ids: frozenset[int] = frozenset()
    ruoli: frozenset[str] = frozenset()

    @property
    def id(self) -> str:
        return str(self.riga["id"])

    @property
    def company_id(self) -> str:
        return str(self.riga["company_profile_id"])

    @property
    def owner_id(self) -> str:
        return str(self.riga["family_parent_id"])


@dataclass(frozen=True)
class Indice:
    """Istantanea dell'indice. `matching` è l'input dei moduli puri (call
    attive su bandi aperti, candidati con opt-in visibile); `profili` ha
    anche i creatori (per «Per te» di un'azienda senza opt-in); `chiavi` e
    `per_chiave` sono solo HMAC dei collegamenti. `query` = letture fatte
    dalla ricarica (budget)."""

    matching: pm.IndiceMatching = field(default_factory=pm.IndiceMatching)
    bacheca: Mapping[str, CallIndicizzata] = field(default_factory=dict)
    profili: Mapping[str, pm.ProfiloMatching] = field(default_factory=dict)
    vetrine: Mapping[str, Vetrina] = field(default_factory=dict)
    chiavi: Mapping[str, tuple[ChiaveCollegamento, ...]] = field(default_factory=dict)
    per_chiave: Mapping[str, frozenset[str]] = field(default_factory=dict)
    impegni: Mapping[str, frozenset[int]] = field(default_factory=dict)
    impegni_esclusivi: Mapping[str, frozenset[int]] = field(default_factory=dict)
    esposizioni: Mapping[str, int] = field(default_factory=dict)
    query: int = 0


# ------------------------------------------------------------- utilità


def _uno(valore: Any) -> dict | None:
    """Embed uno-a-uno: PostgREST lo restituisce come oggetto (o come lista
    nelle versioni vecchie)."""
    if isinstance(valore, list):
        valore = valore[0] if valore else None
    return valore if isinstance(valore, dict) else None


def _molti(valore: Any) -> list[dict]:
    if isinstance(valore, dict):
        return [valore]
    if isinstance(valore, list):
        return [v for v in valore if isinstance(v, dict)]
    return []


def _blocchi(ids: Sequence[str]) -> Iterable[list[str]]:
    for inizio in range(0, len(ids), BLOCCO_ID):
        yield list(ids[inizio : inizio + BLOCCO_ID])


def _data_iso(valore: Any) -> str | None:
    return str(valore)[:10] if valore else None


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


class _Contatore:
    """Conta le letture della ricarica (budget ≤ 20 query)."""

    def __init__(self) -> None:
        self.n = 0

    async def esegui(self, query):
        self.n += 1
        return await query.execute()


async def _tutte(cont: _Contatore, costruisci: Callable[[], Any], chiave: str) -> list[dict]:
    """Tutte le righe a keyset su `chiave`, a pagine di PAGINA."""
    righe: list[dict] = []
    ultimo: Any = None
    while True:
        query = costruisci()
        if ultimo is not None:
            query = query.gt(chiave, ultimo)
        resp = await cont.esegui(query.order(chiave).limit(PAGINA))
        dati = [r for r in resp.data or [] if isinstance(r, dict)]
        righe.extend(dati)
        if len(resp.data or []) < PAGINA or not dati:
            return righe
        ultimo = dati[-1][chiave]


async def _per_blocchi(
    cont: _Contatore, ids: Iterable[str], costruisci: Callable[[list[str]], Any], chiave: str
) -> list[dict]:
    """Righe per `in_` a blocchi di BLOCCO_ID id, ogni blocco a keyset."""
    unici = sorted({str(i) for i in ids})
    righe: list[dict] = []
    for blocco in _blocchi(unici):
        righe.extend(await _tutte(cont, lambda b=blocco: costruisci(b), chiave))
    return righe


# ------------------------------------------------------- pseudonimi (T3)


def risolvi_pseudonimo(indice: Indice, call_id: Any, valore: Any) -> str | None:
    """L'azienda candidata dietro uno pseudonimo della call (None = nessuna).
    Per il server (inviti del WP7): l'id non esce mai."""
    if not isinstance(valore, str) or len(valore) != LUNGHEZZA_PSEUDONIMO:
        return None
    for cid, profilo in indice.matching.candidati.items():
        if profilo.codice_pubblico and pseudonimo(call_id, profilo.codice_pubblico) == valore:
            return cid
    return None


# ------------------------------------------------------- stato dei bandi

# bando_id → (istante della lettura, stato; None = assente dal catalogo,
# `_SNAPSHOT` = doppione fuso)
_STATI_BANDI: dict[int, tuple[float, Any]] = {}
# Stato «vale lo snapshot della call»: bando fuso in un master (assente da
# `bando_pubblico` ma in `bando_fusione`, come la call «congelata» dello
# scheduler) o assenza non verificabile (`bando_fusione` non leggibile).
_SNAPSHOT = object()


async def _stati_bandi(secondary, ids: Iterable[int], cont: _Contatore) -> dict[int, Any] | None:
    """Stato LIVE dei bandi (`bando_pubblico`) con cache di 10 minuti. None
    se il catalogo non si legge: il chiamante ripiega sullo snapshot della
    call (un errore non è un'assenza). Gli id assenti si controllano su
    `bando_fusione`: un doppione fuso vale `_SNAPSHOT`, non «sparito»; se
    `bando_fusione` non si legge, gli assenti valgono `_SNAPSHOT` senza
    entrare in cache (si riprova alla ricarica dopo)."""
    ora = time.monotonic()
    unici = sorted({int(i) for i in ids})
    da_leggere = [
        i for i in unici
        if i not in _STATI_BANDI or ora - _STATI_BANDI[i][0] >= STATO_BANDI_TTL_SECONDI
    ]
    non_verificati: set[int] = set()
    if da_leggere:
        try:
            cont.n += -(-len(da_leggere) // bando_fonti_service.BLOCCO_STATI)
            letti: dict[int, Any] = await bando_fonti_service.leggi_stato_bandi(
                secondary, da_leggere)
        except Exception as exc:  # noqa: BLE001 — ripiego sullo snapshot
            logger.warning("partenariati: stato dei bandi non leggibile per l'indice (%s)",
                           getattr(exc, "code", None) or type(exc).__name__)
            return None
        assenti = [i for i in da_leggere if i not in letti]
        if assenti:
            try:
                cont.n += -(-len(assenti) // bando_fonti_service.BLOCCO_FUSIONI)
                fusi = await bando_fonti_service.leggi_fusioni(secondary, assenti)
            except Exception as exc:  # noqa: BLE001 — un errore non vale «sparito»
                logger.warning("partenariati: bando_fusione non leggibile per l'indice (%s)",
                               getattr(exc, "code", None) or type(exc).__name__)
                non_verificati = set(assenti)
            else:
                letti.update({i: _SNAPSHOT for i in fusi})
        for i in da_leggere:
            if i not in non_verificati:
                _STATI_BANDI[i] = (ora, letti.get(i))
    return {i: _SNAPSHOT if i in non_verificati else _STATI_BANDI[i][1] for i in unici}


def _bando_aperto(call: Mapping, stati: Mapping[int, Any] | None) -> tuple[bool, Any]:
    """(la call resta nell'indice, scadenza del bando da usare)."""
    stato = None if stati is None else stati.get(int(call["bando_id"]))
    if stati is None or stato is _SNAPSHOT:
        effettivo = str(call.get("bando_stato_effettivo") or "").strip().lower()
        return effettivo in STATI_BANDO_APERTI, call.get("bando_scadenza")
    if stato is None:  # sparito dal catalogo: non si propone
        return False, None
    effettivo = str(getattr(stato, "stato_effettivo", None) or "").strip().lower()
    scadenza = getattr(stato, "data_scadenza", None)
    return effettivo in STATI_BANDO_APERTI, (
        scadenza.isoformat() if scadenza else call.get("bando_scadenza")
    )


# ------------------------------------------------------------ aziende


@dataclass
class _Azienda:
    """Righe di un'azienda lette dalla ricarica (transitorie)."""

    id: str
    riga: dict
    dati: dict | None = None
    profilo: dict | None = None


def _coerente(riga: Mapping, dati: Mapping | None) -> bool:
    """I dati del registro sono di QUESTA azienda (T5)."""
    piva = riga.get("partita_iva")
    return bool(dati and piva and dati.get("piva_fetched") == piva)


def _dossier(dati: Mapping) -> dict | None:
    """Dossier dai soli sotto-alberi di `raw` letti (forma giuridica, flag,
    ATECO): lo stesso `build_dossier` dell'import, su un payload ridotto."""
    ridotto = {
        ramo: dati.get(alias)
        for alias, ramo in _RAMI_DOSSIER.items()
        if isinstance(dati.get(alias), dict)
    }
    if not ridotto:
        return None
    try:
        return build_dossier(ridotto)
    except Exception:  # noqa: BLE001 — un dossier illeggibile vale «ignoto»
        logger.warning("partenariati: dossier ridotto non leggibile")
        return None


def _vetrina(az: _Azienda) -> Vetrina:
    if not _coerente(az.riga, az.dati):
        return Vetrina()
    derived = az.dati.get("derived") if isinstance(az.dati.get("derived"), dict) else {}
    dossier = _dossier(az.dati) or {}
    attivita = dossier.get("attivita") if isinstance(dossier.get("attivita"), dict) else None
    return Vetrina(
        registro={"derived": {k: derived.get(k) for k in _DERIVED_VETRINA}},
        dossier={"attivita": {"ateco": (attivita or {}).get("ateco")}} if attivita else None,
    )


def _viva(riga: Mapping) -> bool:
    if riga.get("deleted_at") or riga.get("archived_at"):
        return False
    owner = _uno(riga.get("profiles"))
    return bool(owner and owner.get("is_active") is True)


def _chiavi(riga: Mapping) -> tuple[ChiaveCollegamento, ...]:
    return partenariato_collegamenti.chiavi_da_righe(_molti(riga.get("company_collegamenti")))


def _marker_ok(az: _Azienda) -> bool:
    """Marker aggiornato e chiavi d'identità salvate uguali a quelle che P.IVA
    e ragione sociale attuali generano con la chiave HMAC corrente (identità
    cambiata senza import o chiave ruotata → non calcolati, fail-closed)."""
    marker = _uno(az.riga.get("company_collegamenti_stato"))
    return partenariato_collegamenti.marker_aggiornato(
        marker,
        (az.dati or {}).get("fetched_at"),
        azienda={k: az.riga.get(k) for k in ("partita_iva", "ragione_sociale")},
        chiavi=_chiavi(az.riga),
    )


def _profilo_matching(
    az: _Azienda,
    *,
    collegate: Iterable[str],
    impegni: Iterable[int],
    esposizioni: int,
    impegni_esclusivi: Iterable[int] = (),
) -> pm.ProfiloMatching:
    coerente = _coerente(az.riga, az.dati)
    dati = az.dati or {}
    derived = dati.get("derived") if isinstance(dati.get("derived"), dict) else {}
    stato = _uno(az.riga.get("company_financials_stato")) or {}
    base = profilo_candidato_da(
        company=az.riga,
        derived=derived if coerente else None,
        dossier=_dossier(dati) if coerente else None,
        profilo_partner=az.profilo,
        esercizi=_molti(az.riga.get("company_financials")),
        storico_completo=stato.get("advanced_esito") == "ok",
    )
    return pm.profilo_matching_da(
        base,
        company_id=az.id,
        owner_id=str(az.riga.get("parent_id") or ""),
        viva=_viva(az.riga),
        profilo_partner=az.profilo,
        stato_impresa=dati.get("stato_impresa") if coerente else None,
        collegate=collegate,
        collegamenti_ok=_marker_ok(az),
        impegni_bando=impegni,
        impegni_esclusivi=impegni_esclusivi,
        esposizioni_7g=esposizioni,
    )


async def _carica_aziende(
    cont: _Contatore,
    primary,
    ids: Iterable[str],
    *,
    creatori: set[str],
    profili: Mapping[str, dict],
) -> dict[str, _Azienda]:
    """Righe delle aziende (vive o no: il matching lo sa da `viva`), con i
    loro embed, i dati del registro e il profilo partner già letto."""
    tutte = sorted({str(i) for i in ids})
    di_creatori = [i for i in tutte if i in creatori]
    altre = [i for i in tutte if i not in creatori]
    aziende: dict[str, _Azienda] = {}
    for gruppo, sel_azienda, sel_dati in (
        (di_creatori, AZIENDA_CREATORE_SELECT, DATI_CREATORE_SELECT),
        (altre, AZIENDA_INDICE_SELECT, DATI_INDICE_SELECT),
    ):
        for blocco in _blocchi(gruppo):
            resp = await cont.esegui(
                primary.table("company_profiles").select(sel_azienda).in_("id", blocco)
            )
            for riga in resp.data or []:
                if isinstance(riga, dict) and riga.get("id"):
                    cid = str(riga["id"])
                    aziende[cid] = _Azienda(id=cid, riga=riga, profilo=profili.get(cid))
            dati = await cont.esegui(
                primary.table("company_data").select(sel_dati).in_("company_profile_id", blocco)
            )
            for riga in dati.data or []:
                cid = str(riga.get("company_profile_id"))
                if cid in aziende:
                    aziende[cid].dati = riga
    return aziende


def _ident_creatore(az: _Azienda):
    """Identificativi del creatore per ripulire i testi della call (difesa
    in profondità: al salvataggio sono già bloccanti). Transitori."""
    dati = az.dati or {}
    raw = {"companyDetails": {"vatCode": dati.get("r_piva")},
           "webAndSocial": {"website": dati.get("r_sito")}}
    return identificativi_azienda(
        {k: az.riga.get(k) for k in ("ragione_sociale", "partita_iva", "sito_web")},
        {"denominazione": dati.get("denominazione"), "piva_fetched": dati.get("piva_fetched"),
         "raw": raw},
        None,
    )


def _regioni_call(call: pm.CallSnapshot, vetrina: Vetrina | None) -> frozenset[int]:
    """Regioni della call per il filtro della bacheca: solo ciò che la
    proiezione pubblica mostra, cioè la regione della SEDE del creatore
    (quella della card, non tutte le sue sedi), le regioni dei requisiti
    VISIBILI ai terzi (cercati o di ogni membro, come
    `partenariato_accesso.requisito_visibile`: mai le coperture del creatore)
    e quelle delle posizioni. Un filtro non deve rivelare ciò che la card
    nasconde."""
    regioni: set[int] = set()
    derived = ((vetrina.registro or {}).get("derived") or {}) if vetrina else {}
    if isinstance(derived.get("regione_id"), int):
        regioni.add(derived["regione_id"])
    visibili = [r for r in call.requisiti if r.cercato or r.ambito == "ogni_membro"]
    criteri = [r.criterio for r in visibili] + [c for p in call.posizioni for c in p.criteri]
    for criterio in criteri:
        ids = getattr(criterio, "regioni_ids", None)
        if getattr(criterio, "tipo", None) == "regione" and ids:
            regioni.update(i for i in ids if isinstance(i, int))
    return frozenset(regioni)


async def _esposizioni(cont: _Contatore, primary) -> dict[str, int]:
    """Notifiche proattive degli ultimi 7 giorni per azienda (rotazione)."""
    da = (_adesso() - FINESTRA_ESPOSIZIONI).isoformat()
    righe = await _tutte(
        cont,
        lambda: primary.table("partner_notifiche_proattive")
        .select("id,company_profile_id")
        .gte("created_at", da),
        "id",
    )
    conteggi: dict[str, int] = {}
    for riga in righe:
        cid = str(riga.get("company_profile_id"))
        conteggi[cid] = conteggi.get(cid, 0) + 1
    return conteggi


def _ts(valore: Any) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not valore:
        return None
    try:
        letto = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return None
    return letto if letto.tzinfo else letto.replace(tzinfo=timezone.utc)


@dataclass
class _Candidature:
    """Ciò che l'indice ricava da candidature e inviti attivi (WP7)."""

    inviti: dict[str, set[str]] = field(default_factory=dict)
    accettate: list[tuple[str, str]] = field(default_factory=list)  # (azienda, call)
    esposizioni: dict[str, int] = field(default_factory=dict)


async def _candidature_attive(cont: _Contatore, primary) -> _Candidature:
    """Una lettura (a keyset) delle righe `inviata` e `accettata` di
    `partner_candidature`: inviti in attesa NON scaduti e accettati per le
    call solo su invito, candidature accettate (impegni), inviti degli ultimi
    7 giorni (esposizioni)."""
    righe = await _tutte(
        cont,
        lambda: primary.table("partner_candidature").select(CANDIDATURA_INDICE_SELECT)
        .in_("stato", list(STATI_CANDIDATURA_ATTIVI)),
        "id",
    )
    adesso = _adesso()
    da = adesso - FINESTRA_ESPOSIZIONI
    esito = _Candidature()
    for riga in righe:
        azienda, call = str(riga.get("company_profile_id")), str(riga.get("partner_call_id"))
        stato, tipo = riga.get("stato"), riga.get("tipo")
        membro = _uno(riga.get("partner_call_membri"))
        if stato == "accettata" and (membro or {}).get("stato") != "uscito":
            # WP8: la riga del consorzio, se c'è, prevale sulla candidatura
            # accettata (stessa regola di fn_partner_esclusivita_violata).
            esito.accettate.append((azienda, call))
        if tipo != "invito":
            continue
        scade = _ts(riga.get("scade_at"))
        if stato == "accettata" or (scade is not None and scade > adesso):
            esito.inviti.setdefault(call, set()).add(azienda)
        creata = _ts(riga.get("created_at"))
        if creata is not None and creata >= da:
            esito.esposizioni[azienda] = esito.esposizioni.get(azienda, 0) + 1
    return esito


async def _impegni_da_accettate(
    cont: _Contatore,
    primary,
    accettate: list[tuple[str, str]],
    note: Mapping[str, Mapping],
    impegni: dict[str, set[int]],
    esclusivi: dict[str, set[int]],
) -> None:
    """Le candidature accettate come impegni sul bando della loro call, con
    la stessa regola della RPC (`fn_partner_esclusivita_violata`): in
    qualunque stato della call tranne `chiusa_annullata` (il progetto non
    c'è più e l'accettata non si ritira). WP8: una call non pubblicata e non
    annullata con un membro accettato impegna anche il suo creatore (il
    partenariato può essere andato avanti); le pubblicate lo impegnano già.
    Un consorzio con soli membri esterni qui non si vede (servirebbe una
    lettura in più): lo ferma la RPC. Le call già lette (pubblicate) non si
    rileggono; le altre a blocchi, solo se ce ne sono."""
    mancanti = sorted({call for _, call in accettate if call not in note})
    lette = dict(note)
    for riga in await _per_blocchi(
        cont, mancanti,
        lambda b: primary.table("partner_calls")
        .select("id,company_profile_id,bando_id,esclusivita,stato")
        .in_("id", b),
        "id",
    ) if mancanti else []:
        lette[str(riga["id"])] = riga
    for azienda, call in accettate:
        riga = lette.get(call)
        if (riga is None or riga.get("bando_id") is None
                or riga.get("stato") == "chiusa_annullata"):
            continue
        bando = int(riga["bando_id"])
        impegnate = [azienda]
        creatore = riga.get("company_profile_id")
        if creatore and riga.get("stato") not in ("pubblicata", "bozza"):
            impegnate.append(str(creatore))
        for impegnata in impegnate:
            impegni.setdefault(impegnata, set()).add(bando)
            if riga.get("esclusivita") is True:
                esclusivi.setdefault(impegnata, set()).add(bando)


def _collegate(chiavi: Mapping[str, tuple[ChiaveCollegamento, ...]]) -> dict[str, set[str]]:
    grado = partenariato_collegamenti.collegamenti_tra(chiavi)
    return {cid: set(altre) for cid, altre in grado.items()}


async def _ricarica(primary, secondary) -> Indice:
    cont = _Contatore()
    righe_call = await _tutte(
        cont,
        lambda: primary.table("partner_calls").select(CALL_INDICE_SELECT)
        .eq("stato", "pubblicata"),
        "id",
    )
    stati = await _stati_bandi(secondary, [int(c["bando_id"]) for c in righe_call], cont)
    candidature = await _candidature_attive(cont, primary)
    impegni: dict[str, set[int]] = {}
    impegni_esclusivi: dict[str, set[int]] = {}
    attive: list[dict] = []
    for call in righe_call:
        impegni.setdefault(str(call["company_profile_id"]), set()).add(int(call["bando_id"]))
        if call.get("esclusivita") is True:
            impegni_esclusivi.setdefault(str(call["company_profile_id"]), set()).add(
                int(call["bando_id"]))
        aperto, scadenza = _bando_aperto(call, stati)
        if aperto:
            attive.append({**call, "bando_scadenza": scadenza})
    ids_call = [str(c["id"]) for c in attive]
    requisiti = await _per_blocchi(
        cont, ids_call,
        lambda b: primary.table("partner_call_requisiti").select(REQUISITO_INDICE_SELECT)
        .in_("call_id", b),
        "id",
    ) if ids_call else []
    posizioni = await _per_blocchi(
        cont, ids_call,
        lambda b: primary.table("partner_call_posizioni").select(POSIZIONE_INDICE_SELECT)
        .in_("call_id", b),
        "id",
    ) if ids_call else []

    visibili = await _tutte(
        cont,
        lambda: primary.table("company_partner_profiles").select(PROFILO_INDICE_SELECT)
        .eq("visibile_come_partner", True),
        "company_profile_id",
    )
    profili = {str(r["company_profile_id"]): r for r in visibili}
    creatori = {str(c["company_profile_id"]) for c in attive}
    senza_profilo = sorted(creatori - set(profili))
    if senza_profilo:
        for riga in await _per_blocchi(
            cont, senza_profilo,
            lambda b: primary.table("company_partner_profiles").select(PROFILO_INDICE_SELECT)
            .in_("company_profile_id", b),
            "company_profile_id",
        ):
            profili[str(riga["company_profile_id"])] = riga

    aziende = await _carica_aziende(
        cont, primary, set(profili) | creatori, creatori=creatori, profili=profili
    )
    esposizioni = await _esposizioni(cont, primary)
    for cid, inviti in candidature.esposizioni.items():
        esposizioni[cid] = esposizioni.get(cid, 0) + inviti
    await _impegni_da_accettate(
        cont, primary, candidature.accettate, {str(c["id"]): c for c in righe_call}, impegni,
        impegni_esclusivi,
    )

    chiavi = {cid: _chiavi(az.riga) for cid, az in aziende.items()}
    chiavi = {cid: c for cid, c in chiavi.items() if c}
    collegate = _collegate(chiavi)
    per_chiave: dict[str, set[str]] = {}
    for cid, voci in chiavi.items():
        for voce in voci:
            per_chiave.setdefault(voce.chiave, set()).add(cid)

    profili_matching = {
        cid: _profilo_matching(
            az,
            collegate=collegate.get(cid, ()),
            impegni=impegni.get(cid, ()),
            esposizioni=esposizioni.get(cid, 0),
            impegni_esclusivi=impegni_esclusivi.get(cid, ()),
        )
        for cid, az in aziende.items()
    }
    vetrine = {cid: _vetrina(az) for cid, az in aziende.items()}

    per_call_req: dict[str, list[dict]] = {}
    for riga in requisiti:
        per_call_req.setdefault(str(riga["call_id"]), []).append(riga)
    per_call_pos: dict[str, list[dict]] = {}
    for riga in posizioni:
        per_call_pos.setdefault(str(riga["call_id"]), []).append(riga)

    snapshots: list[pm.CallSnapshot] = []
    bacheca: dict[str, CallIndicizzata] = {}
    for call in attive:
        cid = str(call["company_profile_id"])
        az = aziende.get(cid)
        creatore = profili_matching.get(cid)
        if az is None or creatore is None:
            continue
        ident = _ident_creatore(az)

        def pubblico(testo, _ident=ident):
            return testo_pubblico(testo, _ident)

        req = per_call_req.get(str(call["id"]), [])
        pos = per_call_pos.get(str(call["id"]), [])
        snapshot = pm.call_snapshot_da(call, req, pos, creatore, pubblico=pubblico)
        snapshots.append(snapshot)
        riga_pubblica = {
            chiave: call.get(chiave)
            for chiave in (
                "id", "company_profile_id", "family_parent_id", "bando_id", "bando_slug",
                "bando_titolo", "bando_scadenza", "ruolo_creatore", "forma_aggregazione_prevista",
                "budget_fascia", "scadenza_call", "visibilita", "stato", "esclusivita",
                "pubblicata_at", "sospesa_at",
            )
        }
        riga_pubblica["titolo"] = pubblico(call.get("titolo"))
        ruoli = {p.ruolo for p in snapshot.posizioni}
        if snapshot.ruolo_creatore == "cerco_capofila":
            ruoli.add("capofila")
        bacheca[snapshot.id] = CallIndicizzata(
            riga=riga_pubblica,
            posizioni_n=len(pos),
            posti=sum(
                p.get("numero") if isinstance(p.get("numero"), int) and p.get("numero") > 0
                else 1
                for p in pos
            ),
            requisiti_cercati_n=sum(1 for r in req if r.get("cercato") is True),
            regioni_ids=_regioni_call(snapshot, vetrine.get(cid)),
            ruoli=frozenset(ruoli),
        )

    candidati = [
        profili_matching[cid]
        for cid, riga in profili.items()
        if riga.get("visibile_come_partner") is True and cid in profili_matching
    ]
    return Indice(
        matching=pm.IndiceMatching.da_liste(snapshots, candidati, candidature.inviti),
        bacheca=bacheca,
        profili=profili_matching,
        vetrine=vetrine,
        chiavi=chiavi,
        per_chiave={k: frozenset(v) for k, v in per_chiave.items()},
        impegni={k: frozenset(v) for k, v in impegni.items()},
        impegni_esclusivi={k: frozenset(v) for k, v in impegni_esclusivi.items()},
        esposizioni=esposizioni,
        query=cont.n,
    )


# ------------------------------------------------ ciclo di vita dell'indice


@dataclass
class _Stato:
    indice: Indice | None = None
    caricato_at: float = 0.0
    generazione_caricata: int = -1
    generazione: int = 0
    lock: asyncio.Lock | None = None
    loop: Any = None


_STATO = _Stato()


def invalida() -> None:
    """L'indice va ricaricato alla prossima richiesta (chiamata dai servizi
    del modulo dopo ogni scrittura che cambia il matching). Una ricarica già
    in corso non vale: la sua istantanea potrebbe precedere la scrittura."""
    _STATO.generazione += 1


def reset() -> None:
    """Svuota indice e cache (test)."""
    global _STATO
    _STATO = _Stato()
    _STATI_BANDI.clear()


def _lock() -> asyncio.Lock:
    """Un lock per event loop (i test ne creano uno per test)."""
    loop = asyncio.get_running_loop()
    if _STATO.lock is None or _STATO.loop is not loop:
        _STATO.lock, _STATO.loop = asyncio.Lock(), loop
    return _STATO.lock


def _fresco() -> bool:
    ttl = max(0, int(get_settings().partenariato_indice_ttl_seconds))
    return (
        _STATO.indice is not None
        and _STATO.generazione_caricata == _STATO.generazione
        and time.monotonic() - _STATO.caricato_at < ttl
    )


async def indice(primary, secondary) -> Indice:
    """L'indice corrente: ricaricato se scaduto (TTL) o invalidato, una sola
    ricarica alla volta. Gli errori di lettura propagano (nessun indice a
    metà)."""
    if _fresco():
        return _STATO.indice
    async with _lock():
        if _fresco():
            return _STATO.indice
        generazione = _STATO.generazione
        nuovo = await _ricarica(primary, secondary)
        _STATO.indice, _STATO.caricato_at = nuovo, time.monotonic()
        _STATO.generazione_caricata = generazione
        logger.info("partenariati: indice ricaricato (%d call, %d candidati, %d query)",
                    len(nuovo.matching.calls), len(nuovo.matching.candidati), nuovo.query)
        return nuovo


async def profilo_azienda(primary, idx: Indice, company_id: str) -> pm.ProfiloMatching | None:
    """Il profilo di matching di UN'azienda: dall'indice se c'è (candidati e
    creatori), altrimenti letto ora (azienda senza opt-in, per «Per te»:
    Q25). Collegate calcolate con le chiavi dell'indice; None se l'azienda
    non esiste."""
    cid = str(company_id)
    if cid in idx.profili:
        return idx.profili[cid]
    cont = _Contatore()
    resp = await cont.esegui(
        primary.table("company_partner_profiles").select(PROFILO_INDICE_SELECT)
        .eq("company_profile_id", cid).limit(1)
    )
    profilo = resp.data[0] if resp.data else None
    aziende = await _carica_aziende(
        cont, primary, [cid], creatori=set(), profili={cid: profilo} if profilo else {}
    )
    az = aziende.get(cid)
    if az is None:
        return None
    proprie = _chiavi(az.riga)
    vicine = set().union(*(idx.per_chiave.get(c.chiave, frozenset()) for c in proprie)) \
        if proprie else set()
    collegate = {
        altra for altra in vicine - {cid}
        if partenariato_collegamenti.valuta_collegamento(proprie, idx.chiavi.get(altra, ()))
    }
    return _profilo_matching(
        az,
        collegate=collegate,
        impegni=idx.impegni.get(cid, ()),
        esposizioni=idx.esposizioni.get(cid, 0),
        impegni_esclusivi=idx.impegni_esclusivi.get(cid, ()),
    )


# ------------------------------------------------------ membri (WP8)

# Profilo partner di un membro: le colonne del matching più quelle della
# proiezione pubblica del WP4 (`partner_profilo_pubblico.profilo_pubblico`).
PROFILO_MEMBRO_SELECT = (
    f"{PROFILO_INDICE_SELECT},descrizione_competenze,competenze_libere,paesi_interesse,"
    "infrastrutture"
)


@dataclass(frozen=True)
class AziendaMembro:
    """Un'azienda in piattaforma membro di un consorzio (WP8), letta ORA (non
    dall'istantanea: bilanci e chiavi aggiornati). Transitoria, per il
    validatore e le proiezioni del consorzio; nulla resta nell'indice.

    - `profilo`: `ProfiloMatching` con gli esercizi ESATTI (vista «proprio»
      del validatore; verso gli altri escono solo fasce ed esiti);
    - `chiavi`: chiavi HMAC dei collegamenti se il marker è valido
      (`_marker_ok`), altrimenti None (non calcolati: «da verificare»);
    - `ident`: identificativi dell'azienda (per ripulire i suoi testi);
    - `registro` / `dossier`: dati del registro SOLO se sono dell'azienda
      (T5), per la proiezione pubblica del profilo;
    - `profilo_partner`: la riga del profilo partner (None se manca);
    - `nome`: ragione sociale, SOLO per la propria azienda."""

    profilo: pm.ProfiloMatching
    chiavi: tuple[ChiaveCollegamento, ...] | None
    ident: Any = None
    registro: Mapping | None = None
    dossier: Mapping | None = None
    profilo_partner: Mapping | None = None
    nome: str | None = None


async def carica_membri(primary, company_ids: Iterable[str]) -> dict[str, AziendaMembro]:
    """Le aziende membro di un consorzio in tre letture a blocchi (profili
    partner, aziende con gli embed dell'indice, registro), con gli stessi
    costruttori dell'indice (`_profilo_matching`, `_marker_ok`). Un'azienda
    sparita manca dal risultato."""
    ids = sorted({str(c) for c in company_ids if c})
    if not ids:
        return {}
    cont = _Contatore()
    profili = {
        str(r["company_profile_id"]): r
        for r in await _per_blocchi(
            cont, ids,
            lambda b: primary.table("company_partner_profiles").select(PROFILO_MEMBRO_SELECT)
            .in_("company_profile_id", b),
            "company_profile_id",
        )
    }
    aziende = await _carica_aziende(cont, primary, ids, creatori=set(ids), profili=profili)
    uscita: dict[str, AziendaMembro] = {}
    for cid, az in aziende.items():
        profilo = _profilo_matching(az, collegate=(), impegni=(), esposizioni=0)
        coerente = _coerente(az.riga, az.dati)
        nome = az.riga.get("ragione_sociale") or (az.dati or {}).get("denominazione")
        uscita[cid] = AziendaMembro(
            profilo=profilo,
            chiavi=_chiavi(az.riga) if profilo.collegamenti_ok else None,
            ident=_ident_creatore(az),
            registro=az.dati if coerente else None,
            dossier=_dossier(az.dati) if coerente else None,
            profilo_partner=az.profilo,
            nome=nome.strip() if isinstance(nome, str) and nome.strip() else None,
        )
    return uscita


# ------------------------------------------------------- ricontrollo live


async def ricontrollo_live(primary, company_ids: Iterable[str]) -> dict[str, dict]:
    """Le aziende che OGGI sono ancora candidabili: opt-in visibile, profilo
    non sospeso, azienda viva, owner attivo. → `{id: {"accetta_inviti":
    bool}}` (serve alle call solo su invito). Si applica a ogni pagina
    restituita, anche con l'indice fresco (revoca immediata, M4)."""
    ids = sorted({str(c) for c in company_ids})
    if not ids:
        return {}
    profili: dict[str, dict] = {}
    for blocco in _blocchi(ids):
        resp = (
            await primary.table("company_partner_profiles")
            .select("company_profile_id,accetta_inviti")
            .in_("company_profile_id", blocco)
            .eq("visibile_come_partner", True)
            .is_("sospeso_at", "null")
            .execute()
        )
        for riga in resp.data or []:
            profili[str(riga["company_profile_id"])] = {
                "accetta_inviti": riga.get("accetta_inviti") is not False
            }
    vive = await aziende_vive(primary, list(profili))
    return {cid: dati for cid, dati in profili.items() if cid in vive}


async def aziende_vive(primary, company_ids: Iterable[str]) -> set[str]:
    """Aziende non cancellate né archiviate con l'owner attivo."""
    ids = sorted({str(c) for c in company_ids})
    vive: set[str] = set()
    for blocco in _blocchi(ids):
        resp = (
            await primary.table("company_profiles")
            .select("id,deleted_at,archived_at,profiles(is_active)")
            .in_("id", blocco)
            .execute()
        )
        vive.update(str(r["id"]) for r in resp.data or [] if isinstance(r, dict) and _viva(r))
    return vive


async def ricontrollo_call_live(
    primary, call_ids: Iterable[str], *, oggi, solo_pubbliche: bool = True
) -> set[str]:
    """Le call che OGGI sono ancora pubblicate, non sospese né scadute, di
    un'azienda viva (e visibili a tutti, se `solo_pubbliche`)."""
    ids = sorted({str(c) for c in call_ids})
    if not ids:
        return set()
    righe: list[dict] = []
    for blocco in _blocchi(ids):
        query = (
            primary.table("partner_calls")
            .select("id,company_profile_id,visibilita,scadenza_call")
            .in_("id", blocco)
            .eq("stato", "pubblicata")
            .is_("sospesa_at", "null")
        )
        resp = await query.execute()
        righe.extend(r for r in resp.data or [] if isinstance(r, dict))
    oggi_iso = oggi.isoformat()
    righe = [
        r for r in righe
        if (not solo_pubbliche or r.get("visibilita") == "pubblica")
        and not (_data_iso(r.get("scadenza_call")) and _data_iso(r["scadenza_call"]) < oggi_iso)
    ]
    vive = await aziende_vive(primary, [str(r["company_profile_id"]) for r in righe])
    return {str(r["id"]) for r in righe if str(r["company_profile_id"]) in vive}


# -------------------------------------------------- collegamenti (hook)


async def ricostruisci_collegamenti(primary, company_id: Any) -> None:
    """Ricalcolo best-effort delle chiavi dei collegamenti di un'azienda
    (`partenariato_collegamenti.ricostruisci`: solo con il flag e solo per
    un'azienda idonea) e invalidazione dell'indice. Non solleva mai: un
    errore lascia l'azienda senza marker (esclusa, fail-closed) finché il
    backfill dello scheduler non passa. Log con il solo id."""
    cid = str(company_id)
    try:
        await partenariato_collegamenti.ricostruisci(primary, cid)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("partenariati: collegamenti non ricalcolati (azienda %s, %s)", cid,
                       getattr(exc, "code", None) or type(exc).__name__)
    finally:
        invalida()


async def rimuovi_collegamenti_se_non_idonea(primary, company_id: Any) -> None:
    """Dopo la revoca dell'opt-in: se l'azienda non ha più call non chiuse
    le sue chiavi e il marker si cancellano (M2). Best-effort, come sopra."""
    cid = str(company_id)
    try:
        if cid not in await partenariato_collegamenti.idonee(primary, [cid]):
            await partenariato_collegamenti.rimuovi(primary, cid)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("partenariati: collegamenti non rimossi (azienda %s, %s)", cid,
                       getattr(exc, "code", None) or type(exc).__name__)
    finally:
        invalida()
