"""Bilancio ufficiale on-demand (WP2): il bilancio ottico openapi (Visure
Camerali, 4,50 € a richiesta accettata) con PDF e XBRL, pagato dall'addon
consumabile «bilancio-ufficiale».

Flusso:
- `richiedi`: pre-check GRATUITI (forma giuridica da IT-full; `/impresa` a
  0,001 € solo per le forme non note come società di capitali, dentro il
  tetto giornaliero fail-closed delle chiamate openapi), poi
  `fn_bilancio_richiesta_crea` consuma 1 unità SEMPRE (nessun bypass
  gratuito) e crea la riga `in_invio`, poi la POST al provider, inline e MAI
  ritentata. Ogni esito chiude o fa avanzare la riga con un update
  condizionato; i rifiuti certi si rimborsano (`RIMBORSO_AUTOMATICO`, Q5).
- `avanza`: fa avanzare UNA richiesta aperta (stato del provider, allegati,
  XBRL → fonte `xbrl` dei bilanci, documenti, chiusura, notifica). Un solo
  poller alla volta per richiesta (claim a DB su `ultimo_poll_at`); tutte le
  scritture sono idempotenti, quindi un secondo completamento non fa danni.
- Chi chiama `avanza`: il follower in-process lanciato dopo la POST (ogni
  60 s per 20 minuti, poi ogni 5 minuti fino a 40) e il poll-on-read delle
  letture, che fa SOLO claim + `_spawn` (mai il completamento dentro la GET).
- Failsafe: `in_invio` da oltre 5 minuti → `esito_ignoto`; `esito_ignoto` si
  riconcilia con l'id annotato nel registro consumi o con la lista del
  provider (rimborso come non inviata SOLO se, dopo 30 minuti, la lista è
  completa e nessuna richiesta della stessa P.IVA può essere questa; senza
  prova per 24 ore = scaduta, senza rimborso); `in_lavorazione` da oltre 24
  ore → `scaduta`, senza rimborso (72 ore se l'ultimo tentativo è fallito per
  un guasto e non per una risposta del provider). Ogni chiusura senza
  rimborso finisce nel log a livello ERROR, per l'accredito manuale.

Errori del CONTENUTO (ZIP, XBRL) non si ritentano: la richiesta si chiude
`completata` con `xbrl_esito` e il PDF conservato. Si ritentano solo i
guasti infrastrutturali (rete, DB). La P.IVA non finisce mai in chiaro in
log, registro consumi, audit o notifiche.
"""

import asyncio
import base64
import binascii
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError
from postgrest.types import ReturnMethod

from app.clients.openapi import (
    OpenapiBilancioNonDisponibileError,
    OpenapiCreditoProviderError,
    OpenapiFormaNonAmmessaError,
    OpenapiIdentificativoNonValidoError,
    OpenapiListaParzialeError,
    OpenapiNonInviataError,
    OpenapiRispostaTroppoGrandeError,
)
from app.core.errors import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    OpenapiNotConfiguredError,
    OpenapiUpstreamError,
    PaymentRequiredError,
    UpstreamError,
)
from app.core.privacy import mask_piva
from app.schemas.bilancio_ufficiale import (
    AddonBreve,
    BilanciUfficialiOut,
    BilancioRichiestaOut,
)
from app.services import bilanci_service, notification_service, openapi_service
from app.services.bilanci_mapping import RigaFonte
from app.services.openapi_mapping import forma_giuridica_codice, validate_partita_iva
from app.services.xbrl_bilancio import (
    MAX_ZIP_BYTES,
    ErroreContenutoBilancio,
    esito_senza_xbrl,
    estrai_allegati,
    parse_xbrl,
    righe_da_istanza,
)

logger = logging.getLogger("bandofit.bilancio_ufficiale")

BILANCIO_UFFICIALE_ADDON_SLUG = "bilancio-ufficiale"
COST_BILANCIO_OTTICO_CENTS = 450
# Tetti delle ultime 24 ore, applicati dalla RPC sotto lock (fail-closed).
MAX_RICHIESTE_GIORNO_PIATTAFORMA = 30
MAX_RICHIESTE_GIORNO_OWNER = 5

# Un solo poller per richiesta: il claim dura POLL_MIN_SECONDS.
POLL_MIN_SECONDS = 60
# Follower in-process dopo la POST: ogni 60 s per 20 minuti, poi ogni 300 s
# fino a 40 minuti. Dopo, la richiesta avanza solo alla lettura.
FOLLOWER_INTERVALLO_BREVE_SECONDS = 60
FOLLOWER_FASE_BREVE_SECONDS = 20 * 60
FOLLOWER_INTERVALLO_LUNGO_SECONDS = 300
FOLLOWER_DURATA_SECONDS = 40 * 60

# Failsafe (vedi docstring del modulo).
STALE_INVIO = timedelta(minutes=5)
RICONCILIAZIONE_DOPO = timedelta(minutes=30)
STALE_LAVORAZIONE = timedelta(hours=24)
# `in_lavorazione` quando l'ultimo tentativo è fallito per un guasto (rete,
# DB) e non per una risposta del provider: un errore transitorio non chiude
# una richiesta forse già pagata e pronta. Oltre questa soglia si chiude
# comunque, per non bloccare l'azienda (una sola richiesta aperta).
STALE_LAVORAZIONE_GUASTO = timedelta(hours=72)
STALE_ESITO_IGNOTO = timedelta(hours=24)
# Riconciliazione: la richiesta del provider deve essere nata in questa
# finestra attorno alla riga (la POST parte subito dopo la RPC, con un tetto
# di tempo ben sotto i 5 minuti; 1 minuto di tolleranza sugli orologi).
_RICONCILIAZIONE_PRIMA = timedelta(minutes=1)
_RICONCILIAZIONE_DOPO_CREAZIONE = STALE_INVIO + timedelta(minutes=1)

# Stati del provider (confronto case-insensitive). «Dati disponbili» è il
# refuso dell'enum OAS; le risposte reali usano «Dati disponibili».
STATI_PROVIDER_PRONTI = frozenset({"dati disponibili", "dati disponbili", "visura evasa"})
STATO_PROVIDER_ANNULLATA = "annullata"

STATI_APERTI = ("in_invio", "in_lavorazione", "esito_ignoto")

# Q5: rimborso automatico SOLO quando la richiesta certamente non ha prodotto
# un addebito (rifiuto sincrono, mai partita, credito esaurito del provider,
# annullata dal provider). Mai per esito ignoto non dimostrato, scadenze ed
# errori di lettura del contenuto.
RIMBORSO_AUTOMATICO = frozenset({
    "bilancio_non_disponibile",
    "forma_non_ammessa",
    "identificativo_non_valido",
    "non_inviata",
    "credito_provider",
    "annullata",
})

# Tetto della risposta degli allegati, letta in streaming: il base64 dello
# ZIP più grande accettato dal parser, più l'envelope JSON.
MAX_ALLEGATI_BYTES = 4 * -(-MAX_ZIP_BYTES // 3) + 65_536

MAX_RICHIESTE_LISTA = 20
_MAX_AVVISI = 50
_MAX_AVVISO_CARATTERI = 200
_MAX_STATO_PROVIDER = 100

RICHIESTA_SELECT = (
    "id,company_profile_id,family_parent_id,richiesto_da,partita_iva,anno_richiesto,"
    "anno_bilancio,stato,stato_provider,provider_request_id,errore_codice,xbrl_esito,"
    "avvisi,sandbox,ultimo_poll_at,inviata_at,completata_at,rimborsata_at,created_at"
)
_ADDON_SELECT = "id,slug,nome,prezzo,tipo_prezzo,etichetta_prezzo,tipo_fruizione,is_active"

# Detail di fn_bilancio_richiesta_crea → (status, code, messaggio). Gli altri
# (input costruito dal backend) sono errori interni: log + 502.
_RPC_ERRORS: dict[str, tuple[int, str, str]] = {
    "addon_not_available": (
        404, "not_found", "Il bilancio ufficiale non è al momento disponibile",
    ),
    "addon_credit_esaurito": (
        409, "payment_required",
        "Non hai bilanci ufficiali disponibili: puoi acquistarli dal checkout",
    ),
    "bilancio_in_corso": (
        409, "bilancio_in_corso",
        "C'è già una richiesta di bilancio ufficiale in corso per questa azienda: attendi "
        "l'esito",
    ),
    "bilanci_limite_owner": (
        429, "limite_bilanci_giornaliero",
        "Hai raggiunto il numero di bilanci ufficiali richiedibili oggi: riprova domani",
    ),
    "bilanci_limite_piattaforma": (
        503, "bilanci_sospesi",
        "Le richieste di bilancio ufficiale sono sospese per oggi: riprova domani",
    ),
    "company_not_found": (404, "not_found", "Azienda non trovata"),
    "owner_not_found": (404, "not_found", "Titolare non trovato"),
}

# Messaggi per l'utente (italiano piano; il FE li mostra così come sono).
_MESSAGGI_ERRORE = {
    "bilancio_non_disponibile": "Il Registro Imprese non ha questo bilancio.",
    "forma_non_ammessa":
        "Per la forma giuridica di questa azienda il bilancio ufficiale non è disponibile.",
    "identificativo_non_valido": "Il Registro Imprese non riconosce la partita IVA dell'azienda.",
    "credito_provider": "Il servizio non è disponibile in questo momento: riprova più tardi.",
    "non_inviata": "La richiesta non è partita: riprova più tardi.",
    "errore_provider": "Il servizio che fornisce i bilanci ha avuto un problema.",
    "scaduta":
        "Il bilancio non è arrivato entro 24 ore. Scrivi all'assistenza: verifichiamo e, se "
        "serve, ti restituiamo l'unità.",
    "esito_ignoto_scaduto":
        "Non abbiamo ricevuto risposta dal Registro Imprese. Scrivi all'assistenza: "
        "verifichiamo e, se serve, ti restituiamo l'unità.",
}
# Richiesta completata senza PDF conservato (troppo grande, illeggibile o
# assente nell'archivio): lo si dice sempre, anche se i numeri ci sono.
_MESSAGGIO_SENZA_PDF = "Il PDF non è stato conservato: se ti serve, scrivi all'assistenza."
_MESSAGGI_LETTURA = {
    "assente": "Il documento non contiene i numeri in un formato leggibile.",
    "firmato_non_leggibile": "I numeri sono in un file firmato che non riusciamo a leggere.",
    "non_valido": "Non siamo riusciti a leggere i numeri dal documento.",
    "consolidato":
        "È il bilancio consolidato di un gruppo: i suoi numeri non entrano nei bilanci "
        "dell'azienda.",
    "cf_non_corrispondente":
        "Il documento riporta un codice fiscale diverso da quello dell'azienda, quindi non "
        "abbiamo usato i suoi numeri.",
    "troppo_grande": "Il documento ricevuto è troppo grande per essere elaborato per intero.",
}

# Riferimenti ai task in corso: senza, il garbage collector può cancellare
# un task fire-and-forget a metà esecuzione (pattern ai_check_service).
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Avvia un lavoro in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_FUSO_ITALIA = ZoneInfo("Europe/Rome")


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _orologio() -> float:
    """Orologio del follower (sostituibile nei test)."""
    return time.monotonic()


async def _attendi(secondi: float) -> None:
    """Attesa del follower (sostituibile nei test)."""
    await asyncio.sleep(secondi)


def _eta(riga: dict, colonna: str = "created_at") -> timedelta:
    istante = openapi_service._parse_ts(riga.get(colonna)) or openapi_service._parse_ts(
        riga.get("created_at")
    )
    return _adesso() - istante if istante else timedelta(0)


def _uuid_o_none(valore) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (ValueError, TypeError, AttributeError):
        return None


# ------------------------------------------------------------------ letture DB

async def _leggi_riga(primary, richiesta_id: str, company_id: str | None = None) -> dict | None:
    query = (
        primary.table("company_bilancio_richieste")
        .select(RICHIESTA_SELECT)
        .eq("id", str(richiesta_id))
    )
    if company_id is not None:
        query = query.eq("company_profile_id", str(company_id))  # mai righe di altre aziende
    resp = await query.limit(1).execute()
    return resp.data[0] if resp.data else None


async def _richieste(primary, company_id: str) -> list[dict]:
    resp = (
        await primary.table("company_bilancio_richieste")
        .select(RICHIESTA_SELECT)
        .eq("company_profile_id", str(company_id))
        .order("created_at", desc=True)
        .limit(MAX_RICHIESTE_LISTA)
        .execute()
    )
    return resp.data or []


async def _richieste_con_pdf(primary, company_id: str) -> set[str]:
    """Id delle richieste dell'azienda con un PDF conservato. Mai il
    contenuto: la lista non trasporta byte."""
    resp = (
        await primary.table("company_bilancio_documenti")
        .select("richiesta_id")
        .eq("company_profile_id", str(company_id))
        .eq("tipo", "pdf")
        .execute()
    )
    return {str(r["richiesta_id"]) for r in (resp.data or [])}


async def _anni_acquisiti(primary, company_id: str) -> list[int]:
    """Esercizi già registrati da un bilancio ufficiale (fonte xbrl, ruolo
    corrente): richiederli di nuovo pagherebbe lo stesso documento."""
    resp = (
        await primary.table("company_financials_fonti")
        .select("anno")
        .eq("company_profile_id", str(company_id))
        .eq("fonte", "xbrl")
        .eq("ruolo", "corrente")
        .execute()
    )
    return sorted({int(r["anno"]) for r in (resp.data or [])})


async def _anni_posseduti(primary, company_id: str) -> tuple[set[int], int | None]:
    """Esercizi di cui l'azienda ha già il bilancio ufficiale: registrati da
    un XBRL (`_anni_acquisiti`) o consegnati da una richiesta `completata`
    (anno letto dal documento, altrimenti quello richiesto), anche senza
    numeri leggibili: lo stesso documento tornerebbe identico, a pagamento.

    Il secondo valore serve solo alla guardia di «ultimo disponibile»: per
    una richiesta completata ad anno ignoto (né richiesto né letto),
    l'esercizio più recente che può aver consegnato, cioè l'anno della
    richiesta − 1 (None se non ce ne sono)."""
    anni = set(await _anni_acquisiti(primary, company_id))
    resp = (
        await primary.table("company_bilancio_richieste")
        .select("anno_richiesto,anno_bilancio,created_at")
        .eq("company_profile_id", str(company_id))
        .eq("stato", "completata")
        .execute()
    )
    ignoto: int | None = None
    for riga in resp.data or []:
        anno = riga.get("anno_bilancio") or riga.get("anno_richiesto")
        if anno is not None:
            anni.add(int(anno))
            continue
        creata = openapi_service._parse_ts(riga.get("created_at"))
        if creata is not None:
            stima = creata.astimezone(_FUSO_ITALIA).year - 1
            ignoto = stima if ignoto is None else max(ignoto, stima)
    return anni, ignoto


async def _addon_attivo(primary) -> dict | None:
    resp = (
        await primary.table("addons")
        .select(_ADDON_SELECT)
        .eq("slug", BILANCIO_UFFICIALE_ADDON_SLUG)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _quantita(primary, owner_id: str, addon_id) -> int:
    """Unità dell'addon nell'inventario del titolare (chi paga)."""
    resp = (
        await primary.table("user_addon_inventory")
        .select("quantita")
        .eq("user_id", str(owner_id))
        .eq("addon_id", addon_id)
        .limit(1)
        .execute()
    )
    return int(resp.data[0].get("quantita") or 0) if resp.data else 0


def _addon_breve(addon: dict | None) -> AddonBreve | None:
    if addon is None:
        return None
    return AddonBreve(
        slug=addon["slug"],
        nome=addon["nome"],
        tipo_prezzo=addon.get("tipo_prezzo") or "importo",
        etichetta_prezzo=addon.get("etichetta_prezzo"),
        prezzo=addon.get("prezzo") or 0,
    )


# ---------------------------------------------------------------- risposta

def _messaggio(riga: dict, pdf_disponibile: bool) -> str | None:
    stato = riga.get("stato")
    if stato in ("in_invio", "in_lavorazione"):
        return "Di solito arriva entro 15 minuti: ti avvisiamo quando è pronto."
    if stato == "esito_ignoto":
        return (
            "Non sappiamo ancora se la richiesta è arrivata al Registro Imprese: lo "
            "verifichiamo noi, non serve rifarla."
        )
    if stato == "completata":
        esito = riga.get("xbrl_esito")
        if not esito or esito == "ok":
            testo = "I suoi numeri sono ora nei bilanci dell'azienda."
        else:
            testo = _MESSAGGI_LETTURA.get(esito) or ""
            if testo and pdf_disponibile:
                testo += " Trovi il bilancio nel PDF."
        if not pdf_disponibile:
            testo = f"{testo} {_MESSAGGIO_SENZA_PDF}".strip()
        return testo or None
    if stato == "annullata":
        return "Il Registro Imprese ha annullato la richiesta."
    return _MESSAGGI_ERRORE.get(riga.get("errore_codice") or "")


def _richiesta_out(riga: dict, pdf_disponibile: bool) -> BilancioRichiestaOut:
    avvisi = riga.get("avvisi")
    return BilancioRichiestaOut(
        id=str(riga["id"]),
        stato=riga["stato"],
        anno_richiesto=riga.get("anno_richiesto"),
        anno_bilancio=riga.get("anno_bilancio"),
        errore_codice=riga.get("errore_codice"),
        messaggio=_messaggio(riga, pdf_disponibile),
        xbrl_esito=riga.get("xbrl_esito"),
        avvisi_count=len(avvisi) if isinstance(avvisi, list) else 0,
        rimborsata=riga.get("rimborsata_at") is not None,
        pdf_disponibile=pdf_disponibile,
        created_at=str(riga.get("created_at") or ""),
        completata_at=str(riga["completata_at"]) if riga.get("completata_at") else None,
    )


# --------------------------------------------------------------- scritture DB

async def _registra(
    primary, *, user_id: str, owner_id: str, service: str, outcome: str, cost_cents: int,
    meta: dict,
) -> None:
    await openapi_service.record_usage(
        primary, user_id=user_id, family_parent_id=owner_id, service=service,
        outcome=outcome, cost_cents=cost_cents, meta=meta,
    )


async def _registra_get(primary, riga: dict, outcome: str, operazione: str) -> None:
    """Ogni GET al provider (stato, lista, allegati) nel registro consumi, a
    costo 0 (0,001 € dentro la franchigia giornaliera)."""
    await _registra(
        primary, user_id=riga["richiesto_da"], owner_id=riga["family_parent_id"],
        service="bilancio-ottico-stato", outcome=outcome, cost_cents=0,
        meta={"richiesta_id": str(riga["id"]), "operazione": operazione},
    )


async def _claim(primary, richiesta_id: str) -> bool:
    """Un solo poller per richiesta aperta: True se questo processo ha preso
    il turno (ultimo_poll_at più vecchio di POLL_MIN_SECONDS)."""
    try:
        resp = await primary.rpc(
            "fn_bilancio_richiesta_claim_poll",
            {"p_richiesta_id": str(richiesta_id), "p_min_secondi": POLL_MIN_SECONDS},
        ).execute()
    except Exception:
        logger.exception("bilancio ufficiale: claim del poll non riuscito")
        return False
    return resp.data is True


async def _transizione(primary, richiesta_id: str, da: str, campi: dict) -> bool:
    """Transizione NON terminale con update condizionato sullo stato di
    partenza: un solo vincitore. True se la riga è cambiata."""
    resp = (
        await primary.table("company_bilancio_richieste")
        .update(campi)
        .eq("id", str(richiesta_id))
        .eq("stato", da)
        .execute()
    )
    return bool(resp.data)


async def _chiudi(
    primary, richiesta_id: str, stato: str, campi: dict, *, rimborsa: bool
) -> dict:
    """Passaggio a uno stato terminale (fn_bilancio_richiesta_chiudi):
    condizionato dagli stati aperti, rimborso al più una volta. Ritorna
    `{aggiornata, rimborsata, quantita_residua}`. Errori → log + 502."""
    try:
        resp = await primary.rpc(
            "fn_bilancio_richiesta_chiudi",
            {
                "p_richiesta_id": str(richiesta_id),
                "p_stato": stato,
                "p_campi": campi,
                "p_rimborsa": rimborsa,
            },
        ).execute()
    except APIError as exc:
        logger.error(
            "bilancio ufficiale: chiusura %s non riuscita (code=%s, detail=%s)",
            stato, exc.code, (exc.details or "").strip(),
        )
        raise UpstreamError() from exc
    return resp.data if isinstance(resp.data, dict) else {}


async def _chiudi_con_codice(
    primary, riga: dict, stato: str, codice: str | None, *, stato_provider: str | None = None,
    notifica: bool = False,
) -> dict:
    """Chiusura senza bilancio (non_disponibile | annullata | errore) con il
    rimborso deciso da RIMBORSO_AUTOMATICO. Con `notifica`, avvisa chi ha
    fatto la richiesta (esiti asincroni: in una POST l'utente è presente)."""
    campi: dict = {}
    if codice:
        campi["errore_codice"] = codice
    if stato_provider:
        campi["stato_provider"] = stato_provider[:_MAX_STATO_PROVIDER]
    rimborsa = (codice or stato) in RIMBORSO_AUTOMATICO
    esito = await _chiudi(primary, riga["id"], stato, campi, rimborsa=rimborsa)
    if esito.get("aggiornata") and not rimborsa:
        # Q5: senza rimborso automatico (scaduta, esito ignoto scaduto) il
        # provider può aver addebitato: decide l'admin, con un accredito
        # manuale. Livello ERROR perché il monitoraggio lo intercetti.
        logger.error(
            "bilancio ufficiale: richiesta %s chiusa senza rimborso (%s): da valutare per un "
            "accredito manuale",
            riga["id"], codice or stato,
        )
    if esito.get("aggiornata") and notifica:
        await _notifica(primary, riga, pronto=False, rimborsata=bool(esito.get("rimborsata")))
    return esito


async def _notifica(
    primary, riga: dict, *, pronto: bool, rimborsata: bool = False, utilizzabile: bool = True
) -> None:
    """Notifica in-app a chi ha fatto la richiesta. Nessun dato di terzi (né
    P.IVA né ragione sociale): il link porta l'azienda, così un Advisor
    arriva su quella giusta. `utilizzabile` False = completata senza PDF né
    numeri: niente «è pronto»."""
    company_id = str(riga["company_profile_id"])
    if pronto and utilizzabile:
        tipo = "bilancio_ufficiale.pronto"
        titolo = "Il bilancio ufficiale è pronto"
        corpo = "Lo trovi nella sezione Bilanci della pagina Azienda."
    elif pronto:
        tipo = "bilancio_ufficiale.non_disponibile"
        titolo = "Bilancio ufficiale non utilizzabile"
        corpo = (
            "Il documento è arrivato, ma non siamo riusciti a conservarlo né a leggerne i "
            "numeri: scrivi all'assistenza. Trovi i dettagli nella sezione Bilanci della pagina "
            "Azienda."
        )
    else:
        tipo = "bilancio_ufficiale.non_disponibile"
        titolo = "Bilancio ufficiale non disponibile"
        corpo = "Non siamo riusciti a ottenere il bilancio richiesto."
        if rimborsata:
            corpo += " L'unità ti è stata restituita."
        corpo += " Trovi i dettagli nella sezione Bilanci della pagina Azienda."
    await notification_service.notify(
        primary,
        [str(riga["richiesto_da"])],
        tipo=tipo,
        titolo=titolo,
        corpo=corpo,
        url=f"/app/azienda?azienda={company_id}#bilanci",
        dedup_key=f"bilancio-ufficiale:{riga['id']}",
        company_profile_id=company_id,
    )


async def _audit(primary, *, actor_id: str, owner_id: str, action: str, payload: dict) -> None:
    try:
        await primary.table("audit_log").insert(
            {
                "actor_id": str(actor_id),
                "action": action,
                "target_user_id": str(owner_id),
                "family_parent_id": str(owner_id),
                "payload": payload,
            }
        ).execute()
    except Exception:
        logger.exception("audit del bilancio ufficiale non scrivibile (%s)", action)


# ------------------------------------------------------------------ richiesta

def _offre_bilancio(impresa: dict) -> bool:
    chiamate = impresa.get("chiamate_disponibili")
    if not isinstance(chiamate, list):
        return False
    return any(
        str(c).strip().rstrip("/").split("/")[-1].lower() == "bilancio-ottico" for c in chiamate
    )


async def _verifica_impresa(primary, openapi, *, user_id: str, owner_id: str, piva: str) -> None:
    """Pre-check `/impresa` per le forme non note come società di capitali:
    il bilancio ottico dev'essere tra le chiamate disponibili. Un errore del
    provider blocca SENZA spendere (502)."""
    meta = {"piva": mask_piva(piva)}
    try:
        imprese = await openapi.impresa(piva)
    except Exception as exc:
        logger.warning(
            "bilancio ufficiale: pre-check /impresa non riuscito (%s)", type(exc).__name__
        )
        await _registra(
            primary, user_id=user_id, owner_id=owner_id, service="visure-impresa",
            outcome="error", cost_cents=0, meta=meta,
        )
        raise OpenapiUpstreamError(
            "Non siamo riusciti a verificare se il bilancio ufficiale è disponibile per questa "
            "azienda: riprova più tardi. Non ti è stato addebitato nulla"
        ) from exc
    await _registra(
        primary, user_id=user_id, owner_id=owner_id, service="visure-impresa",
        outcome="success", cost_cents=0, meta=meta,
    )
    if not any(_offre_bilancio(impresa) for impresa in imprese):
        raise AppError(
            409,
            "bilancio_non_richiedibile",
            "Il Registro Imprese non fornisce il bilancio ufficiale per questa azienda",
        )


async def _prenota_verifica(primary, owner_id: str) -> None:
    """Una chiamata `/impresa` nel tetto giornaliero dell'owner. Tetto
    esaurito → 429 con un testo sul bilancio (non sull'import); RPC in
    errore → 502. In entrambi i casi nessuna chiamata al provider."""
    try:
        await openapi_service.prenota_operazione_openapi(primary, owner_id)
    except AppError as exc:
        if exc.code != "limite_giornaliero_openapi":
            raise
        raise AppError(
            429,
            "limite_giornaliero_openapi",
            "Hai raggiunto il numero di verifiche sul Registro Imprese di oggi: riprova domani",
        ) from exc


def _raise_from_rpc(exc: APIError):
    detail = (exc.details or "").strip()
    mappato = _RPC_ERRORS.get(detail)
    if mappato:
        raise AppError(*mappato) from exc
    logger.error(
        "bilancio ufficiale: RPC di creazione non mappata (code=%s, detail=%s)", exc.code, detail
    )
    raise UpstreamError() from exc


async def _dati_richiesta(primary, active) -> tuple[dict, dict, str]:
    """Azienda, dati importati e P.IVA per una richiesta; solleva se manca
    qualcosa (nessuna spesa)."""
    company_id = str(active.company_id)
    company_row = await openapi_service._fetch_company_row_by_id(primary, company_id)
    if company_row is None:
        raise NotFoundError("Azienda non trovata")
    dati = await openapi_service._fetch_company_data(primary, company_id)
    piva = (dati or {}).get("piva_fetched")
    if not dati or not piva:
        raise NotFoundError(
            "Per richiedere il bilancio ufficiale importa prima i dati aziendali da partita IVA "
            "dalla pagina Azienda"
        )
    if not validate_partita_iva(piva):
        raise BadRequestError("La partita IVA importata non è valida: ripeti l'importazione")
    piva_profilo = company_row.get("partita_iva")
    if piva_profilo and piva_profilo != piva:
        raise BadRequestError(
            "La partita IVA dei dati aziendali è diversa da quella importata dal Registro "
            "Imprese: correggila nei dati aziendali, poi richiedi il bilancio"
        )
    return company_row, dati, piva


def _verifica_ultimo_disponibile(posseduti: set[int], anno_ignoto: int | None) -> None:
    """«Ultimo disponibile» (anno None): l'esercizio che il provider
    restituirebbe non si conosce prima di pagare. Regola prudente: se
    l'azienda ha già un bilancio ufficiale da anno corrente − 2 in poi, può
    essere proprio quello (a inizio anno l'ultimo depositato è spesso di due
    esercizi fa), quindi si chiede un anno esplicito. 409, nessuna spesa."""
    soglia = _adesso().astimezone(_FUSO_ITALIA).year - 2
    noto = max(posseduti, default=None)
    if noto is not None and noto >= soglia:
        raise AppError(
            409,
            "bilancio_gia_presente",
            f"Hai già il bilancio ufficiale {noto} di questa azienda: con «ultimo disponibile» "
            "potresti riceverlo di nuovo. Scegli l'esercizio che ti serve",
        )
    if anno_ignoto is not None and anno_ignoto >= soglia:
        raise AppError(
            409,
            "bilancio_gia_presente",
            "Hai già un bilancio ufficiale recente di questa azienda: con «ultimo disponibile» "
            "potresti riceverlo di nuovo. Scegli l'esercizio che ti serve",
        )


async def richiedi(primary, openapi, active, user: dict, anno: int | None) -> BilancioRichiestaOut:
    """Richiede il bilancio ufficiale (bilancio ottico, A PAGAMENTO) per
    l'azienda attiva, consumando SEMPRE 1 unità dell'addon del titolare.

    Prima della spesa, in ordine: servizio configurato, titolare, azienda e
    P.IVA importata, niente società di persone, anno non già posseduto (con
    «ultimo disponibile»: nessun bilancio ufficiale da anno corrente − 2), addon
    attivo, unità disponibili (cortesia: l'arbitro è la RPC), nessuna
    richiesta aperta, `/impresa` per le forme non SC (prenotata nel tetto
    giornaliero openapi dell'owner). Poi la RPC (tetti, consumo atomico) e
    la POST, mai ritentata dopo la partenza."""
    if not openapi.enabled:
        raise OpenapiNotConfiguredError()
    if not active.editable:
        raise ForbiddenError("Il bilancio ufficiale lo richiede il titolare dell'azienda")
    if not active.company_id:
        raise NotFoundError("Nessuna azienda: compila prima i dati aziendali")
    company_id = str(active.company_id)
    owner_id = str(active.owner_id)
    user_id = str(user["id"])

    _company_row, dati, piva = await _dati_richiesta(primary, active)
    forma = forma_giuridica_codice((dati or {}).get("raw") or {})
    if forma == "SP":
        raise AppError(
            409,
            "bilancio_non_richiedibile",
            "Le società di persone non depositano il bilancio: il bilancio ufficiale non è "
            "disponibile",
        )
    posseduti, anno_ignoto = await _anni_posseduti(primary, company_id)
    if anno is not None and anno in posseduti:
        raise AppError(
            409,
            "bilancio_gia_presente",
            f"Hai già il bilancio ufficiale {anno} di questa azienda: lo trovi nella sezione "
            "Bilanci",
        )
    if anno is None:
        _verifica_ultimo_disponibile(posseduti, anno_ignoto)
    addon = await _addon_attivo(primary)
    if addon is None:
        raise NotFoundError("Il bilancio ufficiale non è al momento disponibile")
    if await _quantita(primary, owner_id, addon["id"]) <= 0:
        raise PaymentRequiredError(
            "Non hai bilanci ufficiali disponibili: puoi acquistarli dal checkout"
        )
    if any(r.get("stato") in STATI_APERTI for r in await _richieste(primary, company_id)):
        raise AppError(*_RPC_ERRORS["bilancio_in_corso"])
    if forma != "SC":
        # `/impresa` è a pagamento e un rifiuto (409) non consuma unità né
        # crea righe, quindi i tetti della RPC non lo fermano: prima si
        # prenota nel tetto giornaliero FAIL-CLOSED delle chiamate openapi
        # dell'owner (lo stesso dell'import).
        await _prenota_verifica(primary, owner_id)
        await _verifica_impresa(primary, openapi, user_id=user_id, owner_id=owner_id, piva=piva)

    try:
        resp = await primary.rpc(
            "fn_bilancio_richiesta_crea",
            {"p_payload": {
                "company_profile_id": company_id,
                "family_parent_id": owner_id,
                "richiesto_da": user_id,
                "partita_iva": piva,
                "anno_richiesto": anno,
                "addon_id": addon["id"],
                "sandbox": openapi.sandbox,
                "max_piattaforma": MAX_RICHIESTE_GIORNO_PIATTAFORMA,
                "max_owner": MAX_RICHIESTE_GIORNO_OWNER,
            }},
        ).execute()
    except APIError as exc:
        _raise_from_rpc(exc)
    riga = (resp.data or {}).get("richiesta") if isinstance(resp.data, dict) else None
    if not riga or not riga.get("id"):
        # L'unità è consumata e la riga (se c'è) è in_invio: i failsafe la
        # portano a esito_ignoto e poi, senza traccia al provider, al rimborso.
        logger.error("bilancio ufficiale: RPC di creazione senza richiesta nella risposta")
        raise UpstreamError()
    rid = str(riga["id"])

    await _invia(primary, openapi, riga, user_id=user_id, owner_id=owner_id, piva=piva, anno=anno)

    finale = await _leggi_riga(primary, rid, company_id) or riga
    await _audit(
        primary, actor_id=user_id, owner_id=owner_id,
        action="company.bilancio_ufficiale_richiesto",
        payload={
            "company_profile_id": company_id,
            "richiesta_id": rid,
            "anno_richiesto": anno,
            "stato": finale.get("stato"),
        },
    )
    return _richiesta_out(finale, pdf_disponibile=False)


async def _invia(
    primary, openapi, riga: dict, *, user_id: str, owner_id: str, piva: str, anno: int | None
) -> None:
    """POST al provider e prima transizione della riga `in_invio`."""
    rid = str(riga["id"])
    costo = 0 if openapi.sandbox else COST_BILANCIO_OTTICO_CENTS
    meta = {"piva": mask_piva(piva), "richiesta_id": rid}

    async def registra(outcome: str, cost_cents: int, extra: dict | None = None) -> None:
        await _registra(
            primary, user_id=user_id, owner_id=owner_id, service="bilancio-ottico",
            outcome=outcome, cost_cents=cost_cents, meta={**meta, **(extra or {})},
        )

    async def rifiutata(stato: str, codice: str) -> None:
        await registra("error", 0, {"esito": codice})
        await _chiudi_con_codice(primary, riga, stato, codice)

    try:
        accettata = await openapi.bilancio_ottico_richiedi(piva, anno)
    except OpenapiBilancioNonDisponibileError:
        await rifiutata("non_disponibile", "bilancio_non_disponibile")
        return
    except OpenapiFormaNonAmmessaError:
        await rifiutata("non_disponibile", "forma_non_ammessa")
        return
    except OpenapiIdentificativoNonValidoError:
        await rifiutata("non_disponibile", "identificativo_non_valido")
        return
    except OpenapiCreditoProviderError:
        await rifiutata("errore", "credito_provider")
        return
    except OpenapiNonInviataError:
        await rifiutata("errore", "non_inviata")
        return
    except Exception as exc:
        # Timeout, 5xx, risposta malformata, rifiuto non classificato: la
        # richiesta può essere partita e addebitata. Esito ignoto (stima
        # prudente a costo pieno), MAI un secondo invio: lo risolve la
        # riconciliazione con la lista del provider.
        logger.warning("bilancio ufficiale: POST a esito ignoto (%s)", type(exc).__name__)
        await registra("timeout_unknown", costo, {"errore": type(exc).__name__})
        try:
            await _transizione(primary, rid, "in_invio", {"stato": "esito_ignoto"})
        except Exception:
            logger.exception("bilancio ufficiale: passaggio a esito_ignoto non scritto")
        # Il follower porta avanti la riconciliazione anche se nessuno
        # riapre la pagina.
        _spawn(_segui(primary, openapi, rid))
        return

    provider_id = str(accettata["id"])
    # Prima il registro (con l'id del provider: la richiesta è PAGATA e
    # resta rintracciabile anche se l'update sotto fallisse), poi la riga.
    await registra("success", costo, {"provider_request_id": provider_id})
    campi = {
        "stato": "in_lavorazione",
        "provider_request_id": provider_id,
        "inviata_at": _adesso().isoformat(),
        "costo_provider_cents": costo,
        "stato_provider": str(accettata.get("stato_richiesta") or "")[:_MAX_STATO_PROVIDER]
        or None,
    }
    for tentativo in (1, 2):
        try:
            if not await _transizione(primary, rid, "in_invio", campi):
                logger.error(
                    "bilancio ufficiale: richiesta %s accettata (provider %s) ma non più in_invio",
                    rid, provider_id,
                )
            break
        except Exception:
            logger.exception(
                "bilancio ufficiale: richiesta %s accettata (provider %s) non annotata "
                "(tentativo %s): la recupera la riconciliazione",
                rid, provider_id, tentativo,
            )
    _spawn(_segui(primary, openapi, rid))


# ------------------------------------------------------------------ avanzamento

def _stesso_ambiente(riga: dict, openapi) -> bool:
    """La richiesta è nata nell'ambiente openapi in uso (sandbox o
    produzione). Quelle dell'altro ambiente (dopo un cambio di
    `OPENAPI_ENV`) non si interrogano né si chiudono: il provider non le
    conosce e le chiuderebbe, o rimborserebbe, a torto."""
    return bool(riga.get("sandbox")) == bool(openapi.sandbox)


def _avanzabile(riga: dict, openapi) -> bool:
    """Una richiesta si fa avanzare (e si chiude) solo se è aperta, se
    openapi è configurato e se è nata nell'ambiente in uso. Senza provider
    le chiusure a tempo chiuderebbero come scadute richieste forse pronte e
    già pagate: restano aperte finché openapi non torna configurato (come
    nel failsafe, che in quel caso non parte)."""
    return (
        riga.get("stato") in STATI_APERTI
        and bool(openapi.enabled)
        and _stesso_ambiente(riga, openapi)
    )


async def avanza(primary, openapi, riga: dict, *, gia_reclamata: bool = False) -> None:
    """Fa avanzare UNA richiesta aperta. Idempotente e senza eccezioni (gira
    in background): chi non vince il claim non fa nulla; `gia_reclamata`
    se il claim l'ha già preso il chiamante (poll-on-read). Dopo il claim la
    riga si RILEGGE e si decide solo su quella: il chiamante può averne una
    copia vecchia (lotto del failsafe, follower, lettura) e un altro poller
    può averla già riconciliata o chiusa. Con openapi non configurato, o per
    le richieste dell'altro ambiente openapi, non si fa nulla (`_avanzabile`)."""
    if not _avanzabile(riga, openapi):
        return
    try:
        if not gia_reclamata and not await _claim(primary, riga["id"]):
            return
        attuale = await _leggi_riga(primary, riga["id"])
        if attuale is None or attuale.get("stato") not in STATI_APERTI:
            return
        riga = attuale
        stato = riga["stato"]
        if stato == "in_invio":
            if _eta(riga) <= STALE_INVIO:
                return  # la POST può essere ancora in volo
            if not await _transizione(primary, riga["id"], "in_invio", {"stato": "esito_ignoto"}):
                return
            riga = {**riga, "stato": "esito_ignoto"}
            stato = "esito_ignoto"
        if stato == "esito_ignoto":
            riga = await _riconcilia(primary, openapi, riga)
            if riga is None:
                return
        await _avanza_lavorazione(primary, openapi, riga)
    except Exception:
        logger.exception("bilancio ufficiale: avanzamento della richiesta %s non riuscito",
                         riga.get("id"))


def _ts_provider(valore) -> datetime | None:
    """`timestamp_creation` del provider: epoch in secondi (visto sul campo)
    oppure ISO."""
    if isinstance(valore, bool) or valore is None:
        return None
    if isinstance(valore, (int, float)):
        try:
            return datetime.fromtimestamp(float(valore), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    testo = str(valore).strip()
    if testo.isdigit():
        return _ts_provider(int(testo))
    return openapi_service._parse_ts(testo)


def _solo_cifre(valore) -> str:
    testo = str(valore or "").strip().upper()
    return testo[2:] if testo.startswith("IT") else testo


def _anno_voce(voce: dict) -> str | None:
    anno = voce.get("anno_chiusura")
    return str(anno).strip() if anno not in (None, "") else None


def _compatibili(voci: list[dict], riga: dict) -> list[tuple[datetime, str]]:
    """Richieste del provider compatibili con la riga, per riconciliarla:
    stessa P.IVA, nate nella finestra della POST, anno compatibile. Con
    «ultimo disponibile» (anno None) il provider può scrivere l'anno risolto:
    va bene qualunque anno. Prima quelle con l'anno identico, poi per
    istante. (istante, id)."""
    creata = openapi_service._parse_ts(riga.get("created_at"))
    if creata is None:
        return []
    anno = riga.get("anno_richiesto")
    anno_atteso = str(anno) if anno is not None else None
    trovate = []
    for voce in voci:
        pid = voce.get("id")
        if not pid:
            continue
        if voce.get("tipo") not in (None, "bilancio-ottico"):
            continue
        if _solo_cifre(voce.get("cf_piva_id")) != riga.get("partita_iva"):
            continue
        anno_voce = _anno_voce(voce)
        if anno_atteso is not None and anno_voce not in (None, anno_atteso):
            continue
        nata = _ts_provider(voce.get("timestamp_creation"))
        if nata is None:
            continue
        if creata - _RICONCILIAZIONE_PRIMA <= nata <= creata + _RICONCILIAZIONE_DOPO_CREAZIONE:
            trovate.append((anno_voce != anno_atteso, nata, str(pid)))
    return [(nata, pid) for _diverso, nata, pid in sorted(trovate)]


def _tracce(voci: list[dict], riga: dict) -> list[str | None]:
    """Richieste del provider che POTREBBERO essere la POST della riga: stessa
    P.IVA, nate da `created_at − 1 min` in poi o con un istante illeggibile,
    di qualunque anno e tipo (id None se il provider non lo dà). Finché ce n'è
    una non attribuita ad altre righe, l'assenza della POST NON è provata:
    niente rimborso."""
    creata = openapi_service._parse_ts(riga.get("created_at"))
    trovate: list[str | None] = []
    for voce in voci:
        if _solo_cifre(voce.get("cf_piva_id")) != riga.get("partita_iva"):
            continue
        nata = _ts_provider(voce.get("timestamp_creation"))
        if creata is None or nata is None or nata >= creata - _RICONCILIAZIONE_PRIMA:
            pid = voce.get("id")
            trovate.append(str(pid) if pid else None)
    return trovate


async def _id_gia_usati(primary, ids: list[str], escludi: str) -> set[str]:
    """Id del provider già attribuiti ad ALTRE righe. La riga `escludi` (quella
    che si sta riconciliando) non conta: se nel frattempo ha preso lei l'id,
    contarlo «già usato» la farebbe sembrare mai partita."""
    if not ids:
        return set()
    resp = (
        await primary.table("company_bilancio_richieste")
        .select("provider_request_id")
        .in_("provider_request_id", ids)
        .neq("id", str(escludi))
        .execute()
    )
    return {str(r["provider_request_id"]) for r in (resp.data or [])}


async def _ancora_senza_invio(primary, riga: dict) -> bool:
    """Rilettura subito prima di chiudere una riga `esito_ignoto`: se la
    lista del provider è stata lenta, il claim può essere scaduto e un altro
    poller può averla riconciliata nel frattempo. Si chiude solo se è ancora
    `esito_ignoto` e senza id del provider."""
    attuale = await _leggi_riga(primary, riga["id"])
    if (
        attuale is not None
        and attuale.get("stato") == "esito_ignoto"
        and not attuale.get("provider_request_id")
    ):
        return True
    logger.info("bilancio ufficiale: richiesta %s cambiata durante la riconciliazione: nessuna "
                "chiusura", riga["id"])
    return False


async def _eventi_post(primary, riga: dict) -> list[dict] | None:
    """Eventi `bilancio-ottico` (la POST) della riga nel registro consumi.
    None se il registro non è leggibile (nulla si deduce)."""
    try:
        resp = (
            await primary.table("api_usage_events")
            .select("outcome,request_meta,created_at")
            .eq("family_parent_id", str(riga["family_parent_id"]))
            .eq("service", "bilancio-ottico")
            .eq("request_meta->>richiesta_id", str(riga["id"]))
            .limit(10)
            .execute()
        )
    except Exception:
        logger.exception("bilancio ufficiale: registro consumi della richiesta %s non letto",
                         riga["id"])
        return None
    return resp.data or []


async def _in_lavorazione(
    primary, riga: dict, provider_id: str, inviata: datetime | None
) -> dict | None:
    """`esito_ignoto → in_lavorazione` con l'id del provider. La riga
    aggiornata, o None se un altro poller (o un'altra riga) ci è arrivato
    prima."""
    campi = {
        "stato": "in_lavorazione",
        "provider_request_id": provider_id,
        "inviata_at": (inviata or _adesso()).isoformat(),
        "costo_provider_cents": 0 if riga.get("sandbox") else COST_BILANCIO_OTTICO_CENTS,
    }
    try:
        cambiata = await _transizione(primary, riga["id"], "esito_ignoto", campi)
    except APIError as exc:
        # 23505 su cbr_provider_id_uniq: un'altra riga l'ha appena preso.
        logger.warning("bilancio ufficiale: riconciliazione in conflitto (code=%s)", exc.code)
        return None
    if not cambiata:
        return None
    logger.info("bilancio ufficiale: richiesta %s riconciliata con il provider", riga["id"])
    return {**riga, **campi}


async def _riconcilia(primary, openapi, riga: dict) -> dict | None:
    """`esito_ignoto`: cerca la POST. Prima l'id del provider già annotato
    nel registro consumi (POST accettata, riga non aggiornata), poi la lista
    del provider. Trovata → `in_lavorazione` (ritorna la riga aggiornata).
    Rimborso (non inviata) SOLO con la prova: dopo 30 minuti la lista è
    completa e non contiene nessuna richiesta della stessa P.IVA che possa
    essere questa. Senza prova per 24 ore → scaduta, senza rimborso. None =
    nulla da fare ora."""
    eta = _eta(riga)
    eventi = await _eventi_post(primary, riga)
    registrata = next(
        (
            e for e in (eventi or [])
            if e.get("outcome") == "success"
            and isinstance(e.get("request_meta"), dict)
            and e["request_meta"].get("provider_request_id")
        ),
        None,
    )
    if registrata is not None:
        return await _in_lavorazione(
            primary, riga, str(registrata["request_meta"]["provider_request_id"]),
            openapi_service._parse_ts(registrata.get("created_at")),
        )

    voci = None
    completa = False
    if openapi.enabled:
        try:
            voci = await openapi.bilancio_ottico_lista()
            completa = True
            await _registra_get(primary, riga, "success", "lista")
        except OpenapiListaParzialeError as exc:
            voci = exc.voci  # utili a riconciliare, mai a provare un'assenza
            await _registra_get(primary, riga, "success", "lista")
        except Exception as exc:
            logger.warning("bilancio ufficiale: lista del provider non letta (%s)",
                           type(exc).__name__)
            await _registra_get(primary, riga, "error", "lista")

    if voci is not None:
        candidati = _compatibili(voci, riga)
        tracce = _tracce(voci, riga)
        usati = await _id_gia_usati(
            primary, sorted({pid for _nata, pid in candidati} | {p for p in tracce if p}),
            escludi=riga["id"],
        )
        liberi = [(nata, pid) for nata, pid in candidati if pid not in usati]
        if liberi:
            nata, pid = liberi[0]
            aggiornata = await _in_lavorazione(primary, riga, pid, nata)
            if aggiornata is not None and eventi is not None and not any(
                e.get("outcome") in ("success", "timeout_unknown") for e in eventi
            ):
                # La POST è partita ma il processo è morto prima di annotarla
                # (riavvio durante l'invio): la spesa va nel registro.
                await _registra(
                    primary, user_id=riga["richiesto_da"], owner_id=riga["family_parent_id"],
                    service="bilancio-ottico", outcome="success",
                    cost_cents=aggiornata["costo_provider_cents"],
                    meta={"richiesta_id": str(riga["id"]), "provider_request_id": pid,
                          "origine": "riconciliazione"},
                )
            return aggiornata
        dubbie = [p for p in tracce if p is None or p not in usati]
        if completa and not dubbie and eta > RICONCILIAZIONE_DOPO:
            # Dimostrato: nessuna richiesta al provider può essere questa.
            if await _ancora_senza_invio(primary, riga):
                await _chiudi_con_codice(primary, riga, "errore", "non_inviata", notifica=True)
            return None
        if dubbie and eta > RICONCILIAZIONE_DOPO:
            logger.warning(
                "bilancio ufficiale: richiesta %s senza riconciliazione certa (%s voci della "
                "stessa P.IVA non attribuite): nessun rimborso automatico",
                riga["id"], len(dubbie),
            )

    if eta > STALE_ESITO_IGNOTO and await _ancora_senza_invio(primary, riga):
        await _chiudi_con_codice(primary, riga, "errore", "esito_ignoto_scaduto", notifica=True)
    return None


async def _avanza_lavorazione(primary, openapi, riga: dict) -> None:
    """`in_lavorazione`: stato del provider; annullata → rimborso; pronta →
    completamento; ancora in corso da oltre 24 ore → scaduta, senza rimborso.
    Se l'ultimo tentativo è fallito per un guasto (stato non letto, allegati
    non scaricati, DB) la soglia è STALE_LAVORAZIONE_GUASTO: un errore
    transitorio non chiude una richiesta forse pronta e già pagata."""
    eta = _eta(riga, "inviata_at")
    stato_provider = None
    guasto = False
    if openapi.enabled and riga.get("provider_request_id"):
        try:
            letto = await openapi.bilancio_ottico_stato(riga["provider_request_id"])
            await _registra_get(primary, riga, "success", "stato")
            stato_provider = str(letto.get("stato_richiesta") or "").strip()
        except Exception as exc:
            logger.warning("bilancio ufficiale: stato del provider non letto (%s)",
                           type(exc).__name__)
            await _registra_get(primary, riga, "error", "stato")
            guasto = True
    if stato_provider is not None:
        normalizzato = stato_provider.lower()
        if normalizzato == STATO_PROVIDER_ANNULLATA:
            await _chiudi_con_codice(
                primary, riga, "annullata", None, stato_provider=stato_provider, notifica=True
            )
            return
        if normalizzato in STATI_PROVIDER_PRONTI:
            try:
                if await _completa(primary, openapi, riga, stato_provider):
                    return
            except _AllegatiNonScaricati:
                guasto = True
            except Exception:
                # Guasto infrastrutturale (DB, RPC): si riprova al prossimo poll.
                logger.exception("bilancio ufficiale: completamento della richiesta %s non "
                                 "riuscito", riga.get("id"))
                guasto = True
    if eta > (STALE_LAVORAZIONE_GUASTO if guasto else STALE_LAVORAZIONE):
        await _chiudi_con_codice(
            primary, riga, "errore", "scaduta", stato_provider=stato_provider, notifica=True
        )


# ---------------------------------------------------------------- completamento

class _AllegatiNonScaricati(Exception):
    """Download degli allegati fallito (rete, 5xx, risposta malformata): un
    guasto da ritentare, non una risposta del provider."""


@dataclass
class _Contenuto:
    """Esito della lettura dello ZIP: documenti da conservare, righe della
    fonte xbrl, `xbrl_esito`, anno consegnato e avvisi (codici stabili)."""

    esito: str
    pdf: bytes | None = None
    xbrl: bytes | None = None
    righe: list[RigaFonte] = field(default_factory=list)
    anno: int | None = None
    avvisi: list[str] = field(default_factory=list)


def _elabora_allegato(file_b64: str, identificativi: set[str]) -> _Contenuto:
    """Base64 → ZIP → PDF e XBRL → righe. CPU-bound: gira in un thread (il
    parse XBRL ha una deadline interna). Ogni errore di CONTENUTO diventa un
    `xbrl_esito`: non si ritenta, e il PDF buono resta."""
    try:
        # Gli spazi bianchi (a capo in stile MIME ogni 76 caratteri) non sono
        # un errore del contenuto; ogni altro carattere fuori alfabeto sì.
        zip_bytes = base64.b64decode("".join(file_b64.split()), validate=True)
    except (binascii.Error, ValueError, TypeError, AttributeError):
        return _Contenuto("non_valido", avvisi=["allegato_base64_non_valido"])
    try:
        allegati = estrai_allegati(zip_bytes)
    except ErroreContenutoBilancio as exc:
        return _Contenuto(exc.esito, avvisi=[exc.codice])
    except Exception:
        logger.exception("bilancio ufficiale: estrazione degli allegati fallita")
        return _Contenuto("non_valido", avvisi=["allegati_errore_interno"])
    avvisi = list(allegati.avvisi)
    if allegati.xbrl is None:
        return _Contenuto(esito_senza_xbrl(allegati), pdf=allegati.pdf, avvisi=avvisi)
    try:
        istanza = parse_xbrl(allegati.xbrl)
        righe, esito = righe_da_istanza(istanza, identificativi)
    except ErroreContenutoBilancio as exc:
        return _Contenuto(
            exc.esito, pdf=allegati.pdf, xbrl=allegati.xbrl, avvisi=[*avvisi, exc.codice]
        )
    except Exception:
        # Un nostro difetto del parser su questo file: si ripeterebbe a ogni
        # poll. L'XBRL resta conservato e si potrà rileggere gratis.
        logger.exception("bilancio ufficiale: lettura dell'XBRL fallita")
        return _Contenuto(
            "non_valido", pdf=allegati.pdf, xbrl=allegati.xbrl,
            avvisi=[*avvisi, "xbrl_errore_interno"],
        )
    anno = istanza.esercizi[0].anno if istanza.esercizi else None
    return _Contenuto(
        esito, pdf=allegati.pdf, xbrl=allegati.xbrl, righe=righe, anno=anno,
        avvisi=[*avvisi, *istanza.avvisi],
    )


def _avvisi_puliti(avvisi: list[str]) -> list[str]:
    visti = dict.fromkeys(str(a)[:_MAX_AVVISO_CARATTERI] for a in avvisi if a)
    return list(visti)[:_MAX_AVVISI]


def _nome_file(anno: int | None, tipo: str) -> str:
    """Nome generato dal server, mai quello dell'archivio del provider."""
    return f"bilancio-{anno}.{tipo}" if anno else f"bilancio.{tipo}"


async def _identificativi(primary, riga: dict) -> set[str]:
    """P.IVA della richiesta e CF certificato dall'import (se è la stessa
    P.IVA): l'istanza XBRL deve riportarne almeno uno."""
    attesi = {str(riga["partita_iva"])}
    dati = await openapi_service._fetch_company_data(primary, riga["company_profile_id"])
    if dati and dati.get("piva_fetched") == riga["partita_iva"]:
        cf = ((dati.get("raw") or {}).get("companyDetails") or {}).get("taxCode")
        if cf:
            attesi.add(str(cf))
    return attesi


async def _salva_documenti(primary, riga: dict, contenuto: _Contenuto) -> None:
    """PDF (se entro il tetto) e XBRL (sempre, se leggibile dallo ZIP).
    Upsert che ignora i duplicati: un secondo completamento non esplode."""
    anno = contenuto.anno or riga.get("anno_richiesto")
    documenti = []
    for tipo, dati in (("pdf", contenuto.pdf), ("xbrl", contenuto.xbrl)):
        if not dati:
            continue
        documenti.append({
            "richiesta_id": str(riga["id"]),
            "company_profile_id": str(riga["company_profile_id"]),
            "tipo": tipo,
            "nome_file": _nome_file(anno, tipo),
            "dimensione": len(dati),
            "sha256": hashlib.sha256(dati).hexdigest(),
            "contenuto": "\\x" + dati.hex(),  # bytea in formato hex (PostgREST)
        })
    if not documenti:
        return
    await primary.table("company_bilancio_documenti").upsert(
        documenti,
        on_conflict="richiesta_id,tipo",
        ignore_duplicates=True,
        returning=ReturnMethod.minimal,  # niente eco di megabyte in risposta
    ).execute()


async def _completa(primary, openapi, riga: dict, stato_provider: str) -> bool:
    """Allegati → contenuto → fonte xbrl → documenti → chiusura `completata`
    → notifica. True se la richiesta è chiusa (anche da un altro poller);
    False se il provider dice che gli allegati non sono ancora scaricabili.
    Un guasto (download fallito: `_AllegatiNonScaricati`; DB) solleva e si
    ritenta al prossimo poll."""
    try:
        allegato = await openapi.bilancio_ottico_allegati(
            riga["provider_request_id"], max_bytes=MAX_ALLEGATI_BYTES
        )
    except OpenapiRispostaTroppoGrandeError:
        await _registra_get(primary, riga, "error", "allegati")
        contenuto = _Contenuto("troppo_grande", avvisi=["allegati_troppo_grandi"])
    except Exception as exc:
        logger.warning("bilancio ufficiale: allegati non scaricati (%s)", type(exc).__name__)
        await _registra_get(primary, riga, "error", "allegati")
        raise _AllegatiNonScaricati() from exc
    else:
        await _registra_get(primary, riga, "success", "allegati")
        if allegato is None:
            return False  # pronti tra poco: 422/273
        identificativi = await _identificativi(primary, riga)
        contenuto = await asyncio.to_thread(_elabora_allegato, allegato["file"], identificativi)

    company_id = str(riga["company_profile_id"])
    # Prima i documenti: un PDF pagato resta scaricabile anche se la
    # registrazione della fonte fallisse fino al failsafe. Entrambe le
    # scritture sono idempotenti.
    await _salva_documenti(primary, riga, contenuto)
    if contenuto.esito == "ok" and contenuto.righe:
        await bilanci_service.registra_fonte(
            primary, company_id, "xbrl", contenuto.righe, f"bilancio:{riga['id']}"
        )

    campi: dict = {
        "xbrl_esito": contenuto.esito,
        "avvisi": _avvisi_puliti(contenuto.avvisi),
        "stato_provider": stato_provider[:_MAX_STATO_PROVIDER],
    }
    if contenuto.anno is not None and 1990 <= contenuto.anno <= 2100:
        campi["anno_bilancio"] = contenuto.anno
    esito = await _chiudi(primary, riga["id"], "completata", campi, rimborsa=False)
    if esito.get("aggiornata"):
        # Senza PDF né numeri non c'è nulla di pronto da usare.
        utilizzabile = bool(contenuto.pdf) or (contenuto.esito == "ok" and bool(contenuto.righe))
        await _notifica(primary, riga, pronto=True, utilizzabile=utilizzabile)
        # import locale: evita cicli
        from app.services.compatibility import invalidate_company_facets

        invalidate_company_facets(company_id)
    return True


async def _segui(primary, openapi, richiesta_id: str) -> None:
    """Follower in-process dopo una POST accettata: ogni 60 s per i primi 20
    minuti, poi ogni 5 minuti fino a 40. Si ferma appena la richiesta è
    chiusa. Un riavvio lo perde: resta il poll-on-read."""
    inizio = _orologio()
    try:
        while True:
            trascorso = _orologio() - inizio
            if trascorso >= FOLLOWER_DURATA_SECONDS:
                return
            intervallo = (
                FOLLOWER_INTERVALLO_BREVE_SECONDS
                if trascorso < FOLLOWER_FASE_BREVE_SECONDS
                else FOLLOWER_INTERVALLO_LUNGO_SECONDS
            )
            await _attendi(intervallo)
            try:
                riga = await _leggi_riga(primary, richiesta_id)
            except Exception:
                # Un guasto transitorio del DB non ferma il follower: si
                # riprova al giro successivo, fino alla sua durata massima.
                logger.exception("bilancio ufficiale: follower della richiesta %s, lettura non "
                                 "riuscita", richiesta_id)
                continue
            if riga is None or riga.get("stato") not in STATI_APERTI:
                return
            await avanza(primary, openapi, riga)
    except Exception:
        logger.exception("bilancio ufficiale: follower della richiesta %s interrotto",
                         richiesta_id)


# ------------------------------------------------------------------- letture

async def _poll_on_read(primary, openapi, riga: dict) -> None:
    """Alla lettura: SOLO claim + `_spawn(avanza)`. Mai il completamento
    dentro la GET; best-effort. Niente claim per ciò che `avanza` non
    toccherebbe (openapi non configurato, altro ambiente openapi)."""
    if not _avanzabile(riga, openapi):
        return
    if await _claim(primary, riga["id"]):
        _spawn(avanza(primary, openapi, riga, gia_reclamata=True))


async def _richiedibile(
    primary, openapi, active, addon: dict | None, righe: list[dict]
) -> tuple[bool, str | None]:
    """Se il titolare può fare una richiesta ORA (le unità non contano: con
    0 unità il FE propone l'acquisto). Nessuna chiamata al provider."""
    if not active.editable:
        return False, "Il bilancio ufficiale lo richiede il titolare dell'azienda."
    if not openapi.enabled:
        return False, "Il bilancio ufficiale non è disponibile su questo ambiente."
    if addon is None:
        return False, "Il bilancio ufficiale non è al momento disponibile."
    if any(r.get("stato") in STATI_APERTI for r in righe):
        return False, "C'è già una richiesta in corso: ti avvisiamo quando è pronta."
    try:
        _company_row, dati, _piva = await _dati_richiesta(primary, active)
    except AppError as exc:
        return False, exc.message
    if forma_giuridica_codice((dati or {}).get("raw") or {}) == "SP":
        return False, "Le società di persone non depositano il bilancio."
    return True, None


async def lista(primary, openapi, active) -> BilanciUfficialiOut:
    """Bilanci ufficiali dell'azienda attiva: addon, unità del titolare, anni
    già posseduti (`anni_acquisiti`: la stessa regola della guardia di
    `richiedi`, anche per le richieste oltre le ultime 20) e le ultime
    richieste (le aperte avanzano in background)."""
    addon = await _addon_attivo(primary)
    quantita = await _quantita(primary, active.owner_id, addon["id"]) if addon else 0
    if not active.company_id:
        return BilanciUfficialiOut(
            editable=active.editable,
            richiedibile=False,
            motivo_non_richiedibile="Crea prima la tua azienda dalla pagina Azienda.",
            addon=_addon_breve(addon),
            quantita=quantita,
        )
    company_id = str(active.company_id)
    righe = await _richieste(primary, company_id)
    for riga in righe:
        await _poll_on_read(primary, openapi, riga)
    con_pdf = await _richieste_con_pdf(primary, company_id)
    richiedibile, motivo = await _richiedibile(primary, openapi, active, addon, righe)
    return BilanciUfficialiOut(
        editable=active.editable,
        richiedibile=richiedibile,
        motivo_non_richiedibile=motivo,
        addon=_addon_breve(addon),
        quantita=quantita,
        anni_acquisiti=sorted((await _anni_posseduti(primary, company_id))[0]),
        richieste=[_richiesta_out(r, str(r["id"]) in con_pdf) for r in righe],
    )


async def _riga_dell_azienda(primary, active, richiesta_id) -> dict:
    rid = _uuid_o_none(richiesta_id)
    if not active.company_id or rid is None:
        raise NotFoundError("Richiesta non trovata")
    riga = await _leggi_riga(primary, rid, str(active.company_id))
    if riga is None:
        raise NotFoundError("Richiesta non trovata")
    return riga


async def dettaglio(primary, openapi, active, richiesta_id) -> BilancioRichiestaOut:
    """Una richiesta dell'azienda attiva (404 se è di un'altra)."""
    riga = await _riga_dell_azienda(primary, active, richiesta_id)
    await _poll_on_read(primary, openapi, riga)
    con_pdf = await _richieste_con_pdf(primary, str(active.company_id))
    return _richiesta_out(riga, str(riga["id"]) in con_pdf)


def _bytea(valore) -> bytes | None:
    """bytea da PostgREST: stringa esadecimale `\\x…`."""
    if isinstance(valore, (bytes, bytearray)):
        return bytes(valore)
    if isinstance(valore, str) and valore.startswith("\\x"):
        try:
            return bytes.fromhex(valore[2:])
        except ValueError:
            return None
    return None


async def scarica_pdf(
    primary, active, richiesta_id, *, user: dict | None = None
) -> tuple[bytes, str]:
    """PDF del bilancio ufficiale: autorizzazione LIVE sull'azienda attiva
    (404 fuori azienda), 409 se il PDF non è conservato. Nome generato."""
    riga = await _riga_dell_azienda(primary, active, richiesta_id)
    resp = (
        await primary.table("company_bilancio_documenti")
        .select("contenuto,dimensione,sha256")
        .eq("richiesta_id", str(riga["id"]))
        .eq("company_profile_id", str(active.company_id))
        .eq("tipo", "pdf")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise AppError(
            409, "documento_non_disponibile", "Il PDF di questo bilancio non è disponibile"
        )
    documento = resp.data[0]
    contenuto = _bytea(documento.get("contenuto"))
    if (
        contenuto is None
        or len(contenuto) != documento.get("dimensione")
        or hashlib.sha256(contenuto).hexdigest() != documento.get("sha256")
    ):
        logger.error("bilancio ufficiale: PDF della richiesta %s non integro", riga["id"])
        raise UpstreamError("Il PDF è momentaneamente non disponibile")
    actor = str((user or {}).get("id") or active.owner_id)
    await _audit(
        primary, actor_id=actor, owner_id=active.owner_id,
        action="company.bilancio_ufficiale_scaricato",
        payload={"company_profile_id": str(active.company_id), "richiesta_id": str(riga["id"])},
    )
    return contenuto, _nome_file(riga.get("anno_bilancio") or riga.get("anno_richiesto"), "pdf")
