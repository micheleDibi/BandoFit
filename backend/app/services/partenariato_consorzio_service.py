"""Consorzio della call di partenariato (WP8, docs/partenariati.md V1-V3,
T3-T5, Q11, Q14, Q20).

Il consorzio è `partner_call_membri`: il creatore (riga nata alla
pubblicazione), le aziende accettate (righe nate da `fn_partner_decidi`) e
i membri ESTERNI inseriti dal creatore. Ogni transizione passa da una RPC
della 0040, che ricontrolla tutto sotto i lock (owner → azienda → call →
membro); qui si valida PRIMA (niente `parametri_non_validi` da input
dell'utente) e si proietta DOPO.

- Chi vede (T3): l'azienda creatrice (titolare e membri con visibilità, in
  lettura) e le controparti (candidatura accettata, riga del consorzio non
  uscita, call non sospesa); per chiunque altro 404. Un'azienda uscita (da
  sé o tolta) non vede più nulla del consorzio: la risposta alla sua uscita
  ha solo la sua riga. Membri non più vivi (azienda eliminata o archiviata,
  titolare disattivato): nessun profilo pubblico e nessuna valutazione sui
  loro dati (voci grigie e voce `membri_attivi`).
- Chi scrive (T4, Q14): solo il titolare dell'azienda attiva. Il creatore
  modifica ruoli, posizioni e quote, aggiunge e modifica gli esterni, toglie
  i membri, aggiorna budget e documenti (call pubblicata, scaduta o chiusa
  come completata); ogni azienda conferma la propria riga (il creatore anche
  quelle degli esterni) sui termini che ha visto ed esce da sé.
- Validazione (V2) e matrice (V3) con i moduli puri
  (`partenariato_validatore`), sui dati letti ORA: esercizi ESATTI di ogni
  membro (vista «proprio» solo per il destinatario, «terzi» sulle fasce per
  gli altri: il creatore, che controlla budget e quote, non ricava più della
  fascia di nessuno, rilievo WP6/WP8 sull'oracolo), chiavi dei collegamenti
  se il marker è valido, impegni sullo stesso bando con la regola di
  `fn_partner_esclusivita_violata` (0040), budget esatto (lo vedono tutti i
  destinatari ammessi: creatore e controparti accettate, come nel WP7).
- Proiezione per destinatario (Q11, T3): membri da
  `partenariato_accesso.proietta_membro` (la propria azienda col suo nome,
  le altre con lo pseudonimo della call e il profilo pubblico anonimo, mai
  `company_profile_id`); verso chi non ha creato la call la matrice ha solo
  i requisiti visibili ai terzi (cercati o di ogni membro: mai la copertura
  del creatore) e i testi del creatore escono ripuliti dai suoi
  identificativi.
- Dopo ogni mutazione: esito complessivo (vista del creatore) e copertura
  dei requisiti cercati salvati best-effort sulla call
  (`fn_partner_call_validazione_salva`); in lettura si salvano solo se sono
  cambiati (per esempio dopo un'accettazione o un nuovo bilancio) e se
  nessuna scrittura li ha salvati dopo l'inizio della lettura. Indice
  del matching invalidato dopo le scritture sui membri in piattaforma (gli
  impegni sul bando), notifica in-app `partenariato.consorzio_aggiornato`
  (dedup per scrittura) al membro a cui si chiede una nuova conferma e
  all'altra parte di un'uscita.

Log: solo id e codici; mai testi, nomi, P.IVA o importi.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, NoReturn
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.errors import AppError, ForbiddenError, NotFoundError, UpstreamError
from app.schemas.partenariato_consorzio import (
    BudgetIn,
    BudgetOut,
    ConsorzioOut,
    DocumentoStatoIn,
    EsternoIn,
    MembroAggiornaIn,
    MembroConfermaIn,
    ProfiloMembroOut,
    ValidazioneOut,
)
from app.services import lookup_service, partenariato_indice
from app.services import partenariato_candidature_service as candidature
from app.services import partenariato_validatore as pv
from app.services import partenariato_vocabolario as voc
from app.services import partner_call_service as pcs
from app.services.partenariato_accesso import (
    CALL_SELECT,
    MSG_CALL_NON_TROVATA,
    POSIZIONE_SELECT,
    REQUISITO_SELECT,
    RUOLI_AZIENDA,
    RUOLI_SCRITTURA,
    STATI_CONSORZIO_MODIFICABILE,
    carica_call_autorizzata,
    normalizza_id,
    proietta_membro,
    pseudonimo,
    requisito_visibile,
    testo_pubblico,
)
from app.services.partenariato_anonimato import (
    ETICHETTE_RILIEVO,
    Identificativi,
    trova_rilievi,
)
from app.services.partenariato_documenti import checklist, con_stato
from app.services.partenariato_errori import RPC_ERRORS, raise_from_rpc
from app.services.partner_call_gap import intervallo_budget
from app.services.partner_profilo_pubblico import profilo_pubblico

logger = logging.getLogger("bandofit.partenariati")

# Chi vede il consorzio: l'azienda creatrice (titolare e membri) e le
# controparti accettate. Chi agisce sulla propria riga: creatore e controparte.
RUOLI_CONSORZIO: frozenset[str] = frozenset({*RUOLI_AZIENDA, "controparte"})
RUOLI_MEMBRO: frozenset[str] = frozenset({*RUOLI_SCRITTURA, "controparte"})

TIPO_CONSORZIO_AGGIORNATO = "partenariato.consorzio_aggiornato"

MSG_SOLO_TITOLARE = "Il consorzio lo gestisce il titolare dell'azienda"
MSG_AZIENDA_MANCANTE = "Importa o crea prima la tua azienda"
MSG_MEMBRO_NON_TROVATO = "Membro del consorzio non trovato"

# Colonne dei membri lette dal servizio: mai `*_user_id` (non escono e non
# servono). `company_profile_id` e `candidatura_id` restano interni:
# `proietta_membro` non li mette in uscita.
MEMBRO_SELECT = (
    "id,partner_call_id,company_profile_id,candidatura_id,esterno_denominazione,esterno_paese,"
    "esterno_tipi_soggetto,posizione_id,ruolo,quota_percentuale,stato,confermato_at,"
    "created_at,updated_at"
)
DOCUMENTO_SELECT = "codice,stato,note,updated_at"
VALIDAZIONE_SELECT = "id,validazione_esito,validazione_at,copertura_gap_ratio"

# I detail comuni con profilo, call e candidature hanno lì un messaggio sul
# loro oggetto: qui si parla del consorzio. Il code resta quello della mappa.
_ERRORI_CONSORZIO: dict[str, tuple[int, str, str]] = {
    "attore_non_titolare": (403, "forbidden", MSG_SOLO_TITOLARE),
}
# Contatti che le note dei documenti non ammettono (le vedono le controparti).
_CONTATTI = frozenset({"email", "telefono", "url", "iban", "dominio_bloccato"})
_BLOCCO = 100
_CENTESIMI = Decimal("0.01")


# ------------------------------------------------------------------ utilità


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _istante(valore: Any) -> datetime | None:
    """Timestamp di PostgREST (ISO 8601) → datetime con fuso; None se non
    leggibile."""
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not isinstance(valore, str):
        return None
    try:
        istante = datetime.fromisoformat(valore.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return istante if istante.tzinfo else istante.replace(tzinfo=timezone.utc)


def _richiedi_titolare(active) -> None:
    if not active.editable:
        raise ForbiddenError(MSG_SOLO_TITOLARE)


def _richiedi_azienda(active) -> str:
    if not active.company_id:
        raise AppError(409, "azienda_mancante", MSG_AZIENDA_MANCANTE)
    return str(active.company_id)


def _errore_rpc(exc: APIError) -> NoReturn:
    detail = (exc.details or "").strip()
    if detail in _ERRORI_CONSORZIO:
        raise AppError(*_ERRORI_CONSORZIO[detail]) from exc
    raise_from_rpc(exc)


async def _rpc(primary, nome: str, parametri: dict) -> Any:
    try:
        resp = await primary.rpc(nome, parametri).execute()
    except APIError as exc:
        _errore_rpc(exc)
    return resp.data


def _normalizza_membro(valore: Any) -> str:
    """Uuid in forma canonica; malformato = membro inesistente (404, mai il
    22P02 → 502)."""
    try:
        return str(UUID(str(valore).strip()))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError(MSG_MEMBRO_NON_TROVATO) from None


def _decimale(valore: Any) -> Decimal | None:
    if valore is None or isinstance(valore, bool):
        return None
    try:
        numero = Decimal(str(valore))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() else None


def _blocchi(ids: list[str]) -> Iterable[list[str]]:
    for inizio in range(0, len(ids), _BLOCCO):
        yield ids[inizio : inizio + _BLOCCO]


def _parametri(active, user: Mapping) -> dict:
    return {
        "p_attore": str(user["id"]),
        "p_owner": str(active.owner_id),
        "p_company": str(active.company_id),
    }


def _messaggio_non_conforme(nome: str, tipo: str, coda: str) -> str:
    return f"{nome} contiene {ETICHETTE_RILIEVO.get(tipo, 'un dato non ammesso')}: {coda}"


def controlla_nome_esterno(nome: str, ident: Identificativi | None) -> None:
    """Il nome di un membro esterno lo vedono anche le controparti: niente
    contatti né identificativi dell'azienda che ha creato la call (anonima)
    → 400 `testo_non_conforme` (nomina il tipo, mai il dato)."""
    for rilievo in trova_rilievi(nome, ident, anonima=True):
        if rilievo.bloccante:
            raise AppError(400, "testo_non_conforme", _messaggio_non_conforme(
                "Il nome del membro esterno", rilievo.tipo,
                "scrivi solo il nome dell'ente, senza contatti né riferimenti alla tua azienda"))


def controlla_note(note: str | None) -> None:
    """Le note di un documento possono nominare l'azienda (verso le
    controparti escono senza i suoi identificativi), mai contatti diretti."""
    for rilievo in trova_rilievi(note, None, anonima=False):
        if rilievo.bloccante and rilievo.tipo in _CONTATTI:
            raise AppError(400, "testo_non_conforme", _messaggio_non_conforme(
                "Le note", rilievo.tipo, "i contatti si scambiano in chat"))


# ------------------------------------------------------------ letture DB


async def _membri(primary, call_id: str) -> list[dict]:
    resp = (
        await primary.table("partner_call_membri").select(MEMBRO_SELECT)
        .eq("partner_call_id", call_id).order("created_at").execute()
    )
    righe = [r for r in resp.data or [] if isinstance(r, dict)]
    return sorted(righe, key=lambda r: str(r.get("created_at") or ""))


async def _righe(primary, tabella: str, colonne: str, colonna: str, valore: str) -> list[dict]:
    resp = await primary.table(tabella).select(colonne).eq(colonna, valore).execute()
    return [r for r in resp.data or [] if isinstance(r, dict)]


async def _lookups(secondary):
    """Lookup del catalogo (solo nomi di regioni e programmi): None se non
    disponibili."""
    try:
        return await lookup_service.get_lookups(secondary)
    except Exception as exc:  # noqa: BLE001 — servono solo ai nomi
        logger.warning("consorzio: lookup del catalogo non disponibili (%s)",
                       type(exc).__name__)
        return None


async def _membro(primary, call: Mapping, membro_id: Any) -> dict:
    """La riga del membro se è di QUESTA call (404 altrimenti, anche per un
    membro di un'altra call dello stesso creatore)."""
    identificativo = _normalizza_membro(membro_id)
    resp = (
        await primary.table("partner_call_membri").select(MEMBRO_SELECT)
        .eq("id", identificativo).eq("partner_call_id", str(call["id"])).limit(1).execute()
    )
    riga = resp.data[0] if resp.data else None
    if not isinstance(riga, dict):
        raise NotFoundError(MSG_MEMBRO_NON_TROVATO)
    return riga


async def _tutte(costruisci, chiave: str = "id") -> list[dict]:
    """Righe a keyset su `chiave` (pagine sotto il max-rows 1000)."""
    righe: list[dict] = []
    ultimo: Any = None
    while True:
        query = costruisci()
        if ultimo is not None:
            query = query.gt(chiave, ultimo)
        resp = await query.order(chiave).limit(1000).execute()
        dati = [r for r in resp.data or [] if isinstance(r, dict)]
        righe.extend(dati)
        if len(dati) < 1000:
            return righe
        ultimo = dati[-1][chiave]


async def impegni_altrove(primary, call: Mapping, company_ids: Iterable[str]
                          ) -> dict[str, tuple[bool, ...] | None]:
    """Per ogni azienda membro gli ALTRI impegni sullo stesso bando, con la
    regola di `fn_partner_esclusivita_violata` (0040): creatrice di un'altra
    call pubblicata, o di un'altra call non annullata (completata, scaduta,
    sospesa) con almeno un altro membro non uscito; membro non uscito (non
    creatore) di un'altra call non annullata; candidatura accettata su
    un'altra call non annullata senza riga nel consorzio (dati precedenti al
    backfill). → per ciascun impegno se quella call è esclusiva (vuoto =
    nessuno). Da tre a quattro letture; un errore vale «non noto» (None: la
    voce del validatore diventa grigia)."""
    ids = sorted({str(c) for c in company_ids if c})
    if not ids:
        return {}
    cid = str(call["id"])
    try:
        altre = {
            str(r["id"]): r
            for r in await _tutte(
                lambda: primary.table("partner_calls")
                .select("id,company_profile_id,stato,esclusivita")
                .eq("bando_id", int(call["bando_id"]))
            )
            if str(r["id"]) != cid
        }
        # Call proprie non pubblicate e non annullate: impegnano il creatore
        # solo se hanno ancora un altro membro (in piattaforma o esterno).
        proprie = sorted(
            k for k, r in altre.items()
            if str(r.get("company_profile_id")) in ids
            and r.get("stato") not in ("pubblicata", "bozza", "chiusa_annullata")
        )
        con_altri: set[str] = set()
        for blocco in _blocchi(proprie):
            for riga in await _tutte(
                lambda b=blocco: primary.table("partner_call_membri")
                .select("id,partner_call_id,company_profile_id,stato")
                .in_("partner_call_id", b)
            ):
                altra = altre.get(str(riga.get("partner_call_id")))
                if altra is not None and riga.get("stato") != "uscito" and (
                    str(riga.get("company_profile_id")) != str(altra.get("company_profile_id"))
                ):
                    con_altri.add(str(altra["id"]))
        membri: list[dict] = []
        accettate: list[dict] = []
        altre_ids = sorted(altre)
        for blocco_call in _blocchi(altre_ids):
            for blocco in _blocchi(ids):
                membri += await _tutte(
                    lambda bc=blocco_call, b=blocco: primary.table("partner_call_membri")
                    .select("id,partner_call_id,company_profile_id,stato")
                    .in_("partner_call_id", bc).in_("company_profile_id", b)
                )
                accettate += await _tutte(
                    lambda bc=blocco_call, b=blocco: primary.table("partner_candidature")
                    .select("id,partner_call_id,company_profile_id")
                    .in_("partner_call_id", bc).in_("company_profile_id", b)
                    .eq("stato", "accettata")
                )
    except Exception as exc:  # noqa: BLE001 — «non noto» invece di un errore
        logger.warning("consorzio: impegni sul bando non letti (call %s, %s)", cid,
                       getattr(exc, "code", None) or type(exc).__name__)
        return {c: None for c in ids}
    impegni: dict[str, list[bool]] = {c: [] for c in ids}
    for altra in altre.values():
        azienda = str(altra.get("company_profile_id"))
        if azienda in impegni and (altra.get("stato") == "pubblicata"
                                   or str(altra["id"]) in con_altri):
            impegni[azienda].append(altra.get("esclusivita") is True)
    con_riga: set[tuple[str, str]] = set()
    for riga in membri:
        azienda, altra = str(riga.get("company_profile_id")), altre.get(
            str(riga.get("partner_call_id")))
        if altra is None or azienda not in impegni:
            continue
        con_riga.add((str(altra["id"]), azienda))
        if (riga.get("stato") != "uscito" and str(altra.get("company_profile_id")) != azienda
                and altra.get("stato") != "chiusa_annullata"):
            impegni[azienda].append(altra.get("esclusivita") is True)
    for riga in accettate:
        azienda, altra = str(riga.get("company_profile_id")), altre.get(
            str(riga.get("partner_call_id")))
        if (altra is None or azienda not in impegni or altra.get("stato") == "chiusa_annullata"
                or (str(altra["id"]), azienda) in con_riga):
            continue
        impegni[azienda].append(altra.get("esclusivita") is True)
    return {c: tuple(v) for c, v in impegni.items()}


# ------------------------------------------------------------ composizione


def _viva(az: partenariato_indice.AziendaMembro | None) -> bool:
    """Azienda membro ancora viva (non eliminata né archiviata, titolare
    attivo: `ProfiloMatching.viva`, la definizione dell'indice e della 0040)."""
    return az is not None and az.profilo.viva is True


def _profilo_membro(az: partenariato_indice.AziendaMembro | None, lookups
                    ) -> ProfiloMembroOut | None:  # type: ignore[valid-type]
    """Profilo pubblico del WP4 di un membro in piattaforma, SEMPRE anonimo
    (verso gli altri membri l'identità non si rivela, Q12) e senza
    `codice_pubblico`; solo se l'azienda è viva e il profilo è ancora
    visibile e non sospeso."""
    riga = az.profilo_partner if _viva(az) else None
    if (not isinstance(riga, Mapping) or riga.get("visibile_come_partner") is not True
            or riga.get("sospeso_at") is not None):
        return None
    pubblico = profilo_pubblico({**riga, "anonimo": True}, az.registro, az.dossier,
                                az.profilo.fasce, lookups, ident=az.ident)
    return ProfiloMembroOut(**pubblico.model_dump(exclude={"codice_pubblico"}))


def _pseudonimo(call_id: str, az: partenariato_indice.AziendaMembro | None) -> str | None:
    codice = az.profilo.codice_pubblico if az is not None else None
    return pseudonimo(call_id, codice) if codice else None


def _ordina(righe: list[dict], creatore_id: str) -> list[dict]:
    """Il creatore per primo, poi nell'ordine di ingresso."""
    return sorted(
        righe,
        key=lambda r: (str(r.get("company_profile_id")) != creatore_id,
                       str(r.get("created_at") or "")),
    )


class _Calcolo:
    """Ciò che serve a proiettare il consorzio per un destinatario."""

    def __init__(self, validazione: pv.Validazione, matrice: pv.MatriceCopertura) -> None:
        self.validazione = validazione
        self.matrice = matrice


async def _persisti(primary, call: Mapping, calcolo: _Calcolo, *, forza: bool,
                    letto_at: datetime | None = None) -> Any:
    """Esito complessivo (vista del creatore) e copertura dei requisiti
    cercati sulla call, best-effort. In lettura (`forza` falso) solo se sono
    cambiati e se nessuna scrittura li ha salvati dopo `letto_at` (l'inizio
    di questa lettura): quel valore è calcolato su dati più recenti e non si
    sovrascrive con uno vecchio. → istante dell'ultima validazione salvata
    (None se non nota)."""
    if call.get("stato") == "bozza":
        return None
    cid = str(call["id"])
    esito = calcolo.validazione.esito
    copertura = calcolo.matrice.copertura_gap_ratio
    try:
        if not forza:
            righe = await _righe(primary, "partner_calls", VALIDAZIONE_SELECT, "id", cid)
            attuale = righe[0] if righe else {}
            if (attuale.get("validazione_esito") == esito
                    and _decimale(attuale.get("copertura_gap_ratio")) == copertura):
                return attuale.get("validazione_at")
            salvata = _istante(attuale.get("validazione_at"))
            if letto_at is not None and salvata is not None and salvata > letto_at:
                return attuale.get("validazione_at")
        resp = await primary.rpc("fn_partner_call_validazione_salva", {
            "p_call": cid,
            "p_esito": esito,
            "p_copertura": str(copertura) if copertura is not None else None,
        }).execute()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("consorzio: validazione non salvata (call %s, %s)", cid,
                       getattr(exc, "code", None) or type(exc).__name__)
        return None
    return _adesso() if resp.data is True else None


async def _consorzio(primary, secondary, active, call: Mapping, ruolo: str, *,
                     dopo_scrittura: bool) -> ConsorzioOut:
    """Il consorzio per il destinatario (docstring del modulo)."""
    cid = str(call["id"])
    creatore_id = str(call["company_profile_id"])
    letto_at = _adesso()
    righe, requisiti, posizioni, documenti = await asyncio.gather(
        _membri(primary, cid),
        _righe(primary, "partner_call_requisiti", REQUISITO_SELECT, "call_id", cid),
        _righe(primary, "partner_call_posizioni", POSIZIONE_SELECT, "call_id", cid),
        _righe(primary, "partner_call_documenti", DOCUMENTO_SELECT, "partner_call_id", cid),
    )
    righe = _ordina(righe, creatore_id)
    attive = [r for r in righe if r.get("stato") != "uscito"]
    in_piattaforma = {str(r["company_profile_id"]) for r in righe if r.get("company_profile_id")}
    aziende = await partenariato_indice.carica_membri(primary, in_piattaforma | {creatore_id})
    impegni = await impegni_altrove(
        primary, call, {str(r["company_profile_id"]) for r in attive
                        if r.get("company_profile_id")})
    creatore = aziende.get(creatore_id)
    ident = creatore.ident if creatore is not None else None

    def pubblico(testo: Any) -> str | None:
        return testo_pubblico(testo, ident)

    membri = []
    for riga in attive:
        company = str(riga["company_profile_id"]) if riga.get("company_profile_id") else None
        az = aziende.get(company) if company else None
        # Un'azienda non più viva resta nella riga (la toglie il creatore), ma
        # i suoi dati non si valutano più: voci grigie e segnalazione.
        viva = _viva(az)
        membri.append(pv.membro_da_riga(
            riga,
            creatore_company_id=creatore_id,
            profilo=az.profilo if viva else None,
            collegamenti=az.chiavi if viva else None,
            impegni_altrove=impegni.get(company) if company else (),
            attivo=company is None or viva,
        ))
    budget = intervallo_budget(call.get("budget_fascia"), call.get("budget_progetto_eur"))
    call_c = pv.call_consorzio_da(call, requisiti, pubblico=pubblico)
    regole = pcs.snapshot_regole(call)
    calcolo = _Calcolo(
        pv.valida_consorzio(regole, call_c, membri, budget=budget, pubblico=pubblico),
        pv.matrice_copertura(call_c.requisiti, membri, budget=budget),
    )
    salvata_at = await _persisti(primary, call, calcolo, forza=dopo_scrittura,
                                 letto_at=letto_at)

    company_attiva = str(active.company_id) if active.company_id else None
    sei_creatore = ruolo in RUOLI_AZIENDA
    editable = bool(active.editable) and ruolo in RUOLI_MEMBRO
    propria = next((r for r in righe if company_attiva
                    and str(r.get("company_profile_id")) == company_attiva), None)
    viewer = str(propria["id"]) if propria is not None else None
    # Chi non ha creato la call e non è (più) nel consorzio vede solo la
    # propria riga: nessun altro membro, voce, requisito, documento né budget
    # esatto (risposta all'uscita; la GET per lui è già 404, vedi
    # `partenariato_accesso.carica_call_autorizzata`).
    fuori = not sei_creatore and (propria is None or propria.get("stato") == "uscito")
    if sei_creatore:
        matrice = calcolo.matrice
    elif fuori:
        matrice = pv.MatriceCopertura((), ())
    else:
        # Mai la copertura del creatore: solo i requisiti visibili ai terzi.
        visibili = {str(r.get("id")) for r in requisiti if requisito_visibile(r)}
        matrice = pv.matrice_copertura([r for r in call_c.requisiti if r.id in visibili],
                                       membri, budget=budget)

    lookups = None if fuori else await _lookups(secondary)
    per_posizione = {str(p["id"]): p for p in posizioni}
    if sei_creatore:
        mostrate = righe
    elif fuori:
        mostrate = [propria] if propria is not None else []
    else:
        mostrate = [r for r in righe if r.get("stato") != "uscito" or r is propria]
    # WP9: verso gli altri membri il creatore di una call nominativa compare con
    # il nome del registro, come nella vista pubblica (solo se verificato oggi).
    nome_del_creatore = (
        None if sei_creatore or fuori else await pcs.nome_creatore(primary, call)
    )
    membri_out = []
    for riga in mostrate:
        company = str(riga["company_profile_id"]) if riga.get("company_profile_id") else None
        az = aziende.get(company) if company else None
        membri_out.append(proietta_membro(
            riga,
            call=call,
            viewer_company_id=company_attiva,
            sei_creatore=sei_creatore,
            editable=editable,
            ident_creatore=ident,
            nome_proprio=az.nome if az is not None else None,
            nome_creatore=nome_del_creatore,
            pseudonimo_membro=_pseudonimo(cid, az),
            profilo=_profilo_membro(az, lookups)
            if company not in (None, creatore_id) and riga is not propria else None,
            posizione=per_posizione.get(str(riga.get("posizione_id"))),
        ))

    forma = call.get("forma_aggregazione_prevista")
    if fuori:
        return ConsorzioOut(
            membri=membri_out,
            validazione=ValidazioneOut(esito="grigio"),
            budget=BudgetOut(fascia=call.get("budget_fascia")),
            forma=forma if forma in voc.FORME else None,
            editable=editable,
        )
    costituzione = regole.costituzione.valore if regole and regole.costituzione else None
    documenti_out = con_stato(
        checklist(forma, costituzione,
                  richiesti=regole.documenti_richiesti if regole else (),
                  ruoli={str(r.get("ruolo")) for r in attive}),
        documenti,
    )
    if not sei_creatore:
        documenti_out = [d.model_copy(update={"note": pubblico(d.note)}) for d in documenti_out]
    esatto = _decimale(call.get("budget_progetto_eur"))
    if esatto is not None:  # numeric(14,2): PostgREST lo restituisce come numero
        esatto = esatto.quantize(_CENTESIMI)
    return ConsorzioOut(
        membri=membri_out,
        validazione=pv.proietta_validazione(calcolo.validazione, viewer_membro_id=viewer,
                                            creatore=sei_creatore),
        matrice=pv.proietta_matrice(matrice, viewer_membro_id=viewer),
        documenti=documenti_out,
        budget=BudgetOut(
            fascia=call.get("budget_fascia"),
            esatto=esatto,
            modificabile=editable and ruolo == "creatore" and call.get("stato") == "pubblicata",
        ),
        forma=forma if forma in voc.FORME else None,
        editable=editable,
        sei_creatore=sei_creatore,
        modificabile=editable and ruolo == "creatore"
        and call.get("stato") in STATI_CONSORZIO_MODIFICABILE,
        validazione_at=salvata_at,
    )


# ------------------------------------------------------------- notifiche


def _url(call_id: str, company_id: str) -> str:
    return f"/app/partenariati/call/{call_id}?tab=consorzio&azienda={company_id}"


def _bando(call: Mapping) -> str:
    return str(call.get("bando_titolo") or "del catalogo")


async def _notifica(primary, call: Mapping, *, company_id: str, titolo: str, corpo: str,
                    dedup: str) -> None:
    """In-app a titolare e membri con visibilità dell'azienda destinataria
    (deep link `?azienda=`), best-effort: mai dati di terzi oltre al titolo
    del bando."""
    await candidature.notifica_evento(
        primary,
        company_id=company_id,
        tipo=TIPO_CONSORZIO_AGGIORNATO,
        titolo=titolo,
        corpo=corpo,
        url=_url(str(call["id"]), company_id),
        dedup_key=dedup,
    )


def _in_piattaforma_non_creatore(membro: Mapping, call: Mapping) -> str | None:
    company = membro.get("company_profile_id")
    if not company or str(company) == str(call.get("company_profile_id")):
        return None
    return str(company)


# ------------------------------------------------------------- operazioni


async def get_consorzio(primary, secondary, active, user: dict, call_id: Any) -> ConsorzioOut:
    """GET /partenariati/call/{id}/consorzio: azienda creatrice (titolare e
    membri in lettura) e controparti accettate; 404 per chiunque altro. Con
    la call sospesa per moderazione (WP9) un'azienda del consorzio diversa
    dalla creatrice riceve SOLO la propria riga, per poterne uscire."""
    try:
        call, ruolo = await carica_call_autorizzata(primary, call_id, active, user,
                                                    ammessi=RUOLI_CONSORZIO)
    except NotFoundError:
        return await _propria_in_call_sospesa(primary, active, call_id)
    return await _consorzio(primary, secondary, active, call, ruolo, dopo_scrittura=False)


async def _carica_creatore(primary, active, user: dict, call_id: Any) -> dict:
    """Titolare + azienda attiva + call dell'azienda attiva (404 altrimenti)."""
    _richiedi_titolare(active)
    _richiedi_azienda(active)
    call, _ = await carica_call_autorizzata(primary, call_id, active, user,
                                            ammessi=RUOLI_SCRITTURA)
    return call


async def _dopo(primary, secondary, active, call: Mapping, ruolo: str) -> ConsorzioOut:
    """Dopo una scrittura: la call riletta, il consorzio ricalcolato e la
    validazione salvata."""
    riletta = await _righe(primary, "partner_calls", CALL_SELECT, "id", str(call["id"]))
    return await _consorzio(primary, secondary, active, riletta[0] if riletta else call, ruolo,
                            dopo_scrittura=True)


def _esito(dati: Any) -> dict:
    membro = (dati or {}).get("membro") if isinstance(dati, dict) else None
    if not isinstance(membro, dict) or not membro.get("id"):
        raise UpstreamError()
    return dati


async def aggiorna_membro(primary, secondary, active, user: dict, call_id: Any, membro_id: Any,
                          dati: MembroAggiornaIn) -> ConsorzioOut:
    """Ruolo, posizione e quota di un membro (solo il creatore). Un membro
    diverso dal creatore torna `proposto` e riceve la richiesta di conferma.
    Errori: 403, 404, 400 `posizione_non_valida`, 409
    `call_non_modificabile`, `membro_uscito`, `capofila_gia_presente`,
    `ruolo_non_ammesso`."""
    call = await _carica_creatore(primary, active, user, call_id)
    riga = await _membro(primary, call, membro_id)
    esito = _esito(await _rpc(primary, "fn_partner_membro_aggiorna", {
        **_parametri(active, user),
        "p_membro": str(riga["id"]),
        "p_posizione": str(dati.posizione_id) if dati.posizione_id else None,
        "p_ruolo": dati.ruolo,
        "p_quota": str(dati.quota_percentuale) if dati.quota_percentuale is not None else None,
    }))
    membro = esito["membro"]
    destinataria = _in_piattaforma_non_creatore(membro, call)
    if esito.get("modificato") and membro.get("company_profile_id"):
        partenariato_indice.invalida()
        if destinataria and membro.get("stato") == "proposto":
            await _notifica(
                primary, call, company_id=destinataria,
                titolo="Conferma la tua partecipazione al consorzio",
                corpo=f"Ruolo, posizione o quota della tua azienda nella call per il bando "
                      f"«{_bando(call)}» sono cambiati: controlla e conferma.",
                dedup=f"partner-consorzio:{membro['id']}:conferma:{membro.get('updated_at')}",
            )
    return await _dopo(primary, secondary, active, call, "creatore")


async def conferma(primary, secondary, active, user: dict, call_id: Any, membro_id: Any,
                   dati: MembroConfermaIn) -> ConsorzioOut:
    """Conferma della propria riga (azienda del membro, creatore compreso) o,
    per il creatore, di quella di un esterno, sui termini (ruolo, posizione,
    quota) che chi conferma ha visto: se nel frattempo sono cambiati la RPC
    risponde `membro_modificato`. Errori: 403, 404, 409 `quota_mancante`,
    `membro_uscito`, `membro_modificato`, `call_non_modificabile`."""
    _richiedi_titolare(active)
    _richiedi_azienda(active)
    call, ruolo = await carica_call_autorizzata(primary, call_id, active, user,
                                                ammessi=RUOLI_MEMBRO)
    riga = await _membro(primary, call, membro_id)
    esito = _esito(await _rpc(primary, "fn_partner_membro_conferma", {
        **_parametri(active, user),
        "p_membro": str(riga["id"]),
        "p_ruolo": dati.ruolo,
        "p_posizione": str(dati.posizione_id) if dati.posizione_id else None,
        "p_quota": str(dati.quota_percentuale) if dati.quota_percentuale is not None else None,
    }))
    if esito.get("modificato") and esito["membro"].get("company_profile_id"):
        partenariato_indice.invalida()
    return await _dopo(primary, secondary, active, call, ruolo)


def _sospesa(call: Mapping) -> bool:
    return call.get("stato") == "sospesa_moderazione" or call.get("sospesa_at") is not None


async def _riga_propria_in_call_sospesa(primary, active, call_id: Any, membro_id: Any
                                       ) -> tuple[dict, dict]:
    """WP9 (WP8 P14): durante una sospensione per moderazione la call non si
    legge, nemmeno dalla controparte (WP7), ma un'azienda del consorzio deve
    poter USCIRE. Solo per l'uscita, solo un'azienda diversa dalla creatrice
    e solo dalla PROPRIA riga; in ogni altro caso lo stesso 404 di una call
    inesistente (nessuna prova dell'esistenza). → (call, riga)."""
    identificativo = normalizza_id(call_id)
    righe = await _righe(primary, "partner_calls", CALL_SELECT, "id", identificativo)
    call = righe[0] if righe else None
    company = str(active.company_id)
    if call is None or not _sospesa(call) or str(call.get("company_profile_id")) == company:
        raise NotFoundError(MSG_CALL_NON_TROVATA)
    try:
        riga = await _membro(primary, call, membro_id)
    except NotFoundError:
        raise NotFoundError(MSG_CALL_NON_TROVATA) from None
    if str(riga.get("company_profile_id")) != company:
        raise NotFoundError(MSG_CALL_NON_TROVATA)
    return call, riga


async def _propria_in_call_sospesa(primary, active, call_id: Any) -> ConsorzioOut:
    """WP9 (WP8 P14): la GET del consorzio di una call sospesa per un'azienda
    che ne fa ancora parte e non l'ha creata: solo la propria riga (nome,
    ruolo, posizione senza titolo, quota, stato, `puo_uscire` al titolare),
    nessun altro membro, voce, requisito, documento né budget. È l'unico modo
    di trovare il proprio `membro_id` per uscire mentre la call non si legge.
    In ogni altro caso lo stesso 404 di una call inesistente."""
    if not active.company_id:
        raise NotFoundError(MSG_CALL_NON_TROVATA)
    company = str(active.company_id)
    righe = await _righe(primary, "partner_calls", CALL_SELECT, "id", normalizza_id(call_id))
    call = righe[0] if righe else None
    if call is None or not _sospesa(call) or str(call.get("company_profile_id")) == company:
        raise NotFoundError(MSG_CALL_NON_TROVATA)
    resp = (
        await primary.table("partner_call_membri").select(MEMBRO_SELECT)
        .eq("partner_call_id", str(call["id"])).eq("company_profile_id", company)
        .limit(1).execute()
    )
    riga = resp.data[0] if resp.data and isinstance(resp.data[0], dict) else None
    if riga is None or riga.get("stato") == "uscito":
        raise NotFoundError(MSG_CALL_NON_TROVATA)
    aziende = await partenariato_indice.carica_membri(primary, {company})
    az = aziende.get(company)
    editable = bool(active.editable)
    return ConsorzioOut(
        membri=[proietta_membro(
            riga, call=call, viewer_company_id=company, sei_creatore=False, editable=editable,
            ident_creatore=None, nome_proprio=az.nome if az is not None else None,
        )],
        validazione=ValidazioneOut(esito="grigio"),
        budget=BudgetOut(),
        editable=editable,
    )


async def esci(primary, secondary, active, user: dict, call_id: Any, membro_id: Any
               ) -> ConsorzioOut:
    """Il creatore toglie un membro (call pubblicata o chiusa come
    completata) o un'azienda esce da sé (in qualunque stato della call, anche
    sospesa per moderazione: WP9); la riga del creatore non si tocca. Dalla
    call sospesa si esce solo dalla PROPRIA riga, e la risposta ha solo
    quella. Errori: 403, 404, 409 `membro_non_rimovibile`,
    `call_non_modificabile`."""
    _richiedi_titolare(active)
    _richiedi_azienda(active)
    try:
        call, ruolo = await carica_call_autorizzata(primary, call_id, active, user,
                                                    ammessi=RUOLI_MEMBRO)
    except NotFoundError:
        call, riga = await _riga_propria_in_call_sospesa(primary, active, call_id, membro_id)
        ruolo = "controparte"
    else:
        riga = await _membro(primary, call, membro_id)
    esito = _esito(await _rpc(primary, "fn_partner_membro_esci", {
        **_parametri(active, user), "p_membro": str(riga["id"]),
    }))
    membro = esito["membro"]
    if esito.get("modificato") and membro.get("company_profile_id"):
        partenariato_indice.invalida()
        dedup = f"partner-consorzio:{membro['id']}:uscito:{membro.get('updated_at')}"
        destinataria = _in_piattaforma_non_creatore(membro, call)
        if esito.get("origine") == "creatore" and destinataria:
            await _notifica(
                primary, call, company_id=destinataria,
                titolo="La tua azienda non fa più parte di un consorzio",
                corpo=f"Chi ha creato la call per il bando «{_bando(call)}» ha tolto la tua "
                      "azienda dal consorzio.",
                dedup=dedup,
            )
        elif esito.get("origine") == "membro":
            await _notifica(
                primary, call, company_id=str(call["company_profile_id"]),
                titolo="Un partner ha lasciato il consorzio",
                corpo=f"Un'azienda è uscita dal consorzio della tua call per il bando "
                      f"«{_bando(call)}».",
                dedup=dedup,
            )
    return await _dopo(primary, secondary, active, call, ruolo)


async def ricalcola_validazione(primary, secondary, call_id: Any) -> bool:
    """Passo `ricalcolo_validazioni` dello scheduler (WP9, WP8 P13a):
    validazione della call ricalcolata con la vista del creatore e salvata,
    come dopo una scrittura sul consorzio. Solo negli stati in cui il
    consorzio si modifica; → False se la call non c'è o non è in quegli
    stati. Nessuna proiezione esce da qui."""
    righe = await _righe(primary, "partner_calls", CALL_SELECT, "id", normalizza_id(call_id))
    call = righe[0] if righe else None
    if call is None or call.get("stato") not in STATI_CONSORZIO_MODIFICABILE:
        return False
    # L'azienda creatrice in sola lettura (nessun utente: nessuna scrittura
    # oltre al salvataggio della validazione).
    vista = SimpleNamespace(company_id=str(call["company_profile_id"]),
                            owner_id=str(call["family_parent_id"]), editable=False)
    await _consorzio(primary, secondary, vista, call, "titolare_o_membro", dopo_scrittura=True)
    return True


async def aggiungi_o_modifica_esterno(primary, secondary, active, user: dict, call_id: Any,
                                      dati: EsternoIn, membro_id: Any = None) -> ConsorzioOut:
    """Aggiunge (senza `membro_id`) o modifica un membro esterno (Q20, solo
    il creatore). Il nome non ammette contatti né identificativi del
    creatore. Errori: 403, 404, 400 `testo_non_conforme`,
    `posizione_non_valida`, 409 `limite_membri`, `capofila_gia_presente`,
    `call_non_modificabile`."""
    call = await _carica_creatore(primary, active, user, call_id)
    if membro_id is not None:
        riga = await _membro(primary, call, membro_id)
        if riga.get("company_profile_id") is not None:
            raise NotFoundError(MSG_MEMBRO_NON_TROVATO)
        membro_id = riga["id"]
    az = await pcs.carica_azienda(primary, active.company_id, active.owner_id)
    controlla_nome_esterno(dati.denominazione, az.ident)
    _esito(await _rpc(primary, "fn_partner_membro_esterno", {
        "p_call": str(call["id"]),
        "p_attore": str(user["id"]),
        "p_owner": str(active.owner_id),
        "p_company": str(active.company_id),
        "p_payload": dati.payload(membro_id),
    }))
    return await _dopo(primary, secondary, active, call, "creatore")


async def aggiorna_budget(primary, secondary, active, user: dict, call_id: Any,
                          dati: BudgetIn) -> ConsorzioOut:
    """Fascia pubblica e budget esatto RISERVATO (solo il creatore, call
    pubblicata): stessa RPC e stessi effetti del PATCH della call. Errori:
    403, 404, 400 (budget fuori fascia), 409 `stato_call_non_valido`."""
    call = await _carica_creatore(primary, active, user, call_id)
    aggiornata = await pcs.aggiorna_budget(primary, active, user, call, dati.campi())
    return await _consorzio(primary, secondary, active, aggiornata, "creatore",
                            dopo_scrittura=True)


async def set_documento(primary, secondary, active, user: dict, call_id: Any, codice: str,
                        dati: DocumentoStatoIn) -> ConsorzioOut:
    """Stato (e note) di un documento della checklist (solo il creatore): il
    codice deve essere tra quelli della checklist della call (400
    `documento_non_valido`); le note non ammettono contatti. Errori: 403,
    404, 400, 409 `call_non_modificabile`."""
    call = await _carica_creatore(primary, active, user, call_id)
    regole = pcs.snapshot_regole(call)
    ammessi = {
        d.codice for d in checklist(
            call.get("forma_aggregazione_prevista"),
            regole.costituzione.valore if regole and regole.costituzione else None,
            richiesti=regole.documenti_richiesti if regole else (),
        )
    }
    if codice not in ammessi:
        raise AppError(*RPC_ERRORS["documento_non_valido"])
    controlla_note(dati.note)
    await _rpc(primary, "fn_partner_documento_stato", {
        "p_call": str(call["id"]),
        "p_attore": str(user["id"]),
        "p_owner": str(active.owner_id),
        "p_company": str(active.company_id),
        "p_codice": codice,
        "p_stato": dati.stato,
        "p_note": dati.note,
    })
    return await _dopo(primary, secondary, active, call, "creatore")
