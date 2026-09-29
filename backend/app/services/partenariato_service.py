"""Regole di partenariato per bando (WP3, docs/partenariati.md §2.3 R1-R9).

Flusso di un'estrazione (una sola per bando alla volta):
1. `avvia_analisi` (POST) → guardie (AI configurata, bando, «Analizza comunque»
   solo dopo `nessun_segnale` e una volta sola) → risultato ancora fresco o
   già in corso = 200 senza spesa → limite per utente (Gratuito: 3 al giorno
   con email verificata, altrimenti 10) → `fn_partenariato_prenota`: claim
   ATOMICO del bando + prenotazione della spesa sul budget del giorno al caso
   peggiore (fail-closed) → pipeline in background (`_spawn`) → 202;
2. `_pipeline` (non solleva MAI): `fase documenti` (link ufficiali, download
   sicuro) → heartbeat → `fase lettura` (testo dei PDF in un processo
   separato) → pre-classificatore (zero segnali = `nessun_segnale`, costo 0,
   niente modello; ma se nessun documento è stato letto per cause
   transitorie = `errore` con backoff, costo 0) → input e `content_hash`
   (uguale al precedente = `riusata`, costo 0) → heartbeat SUBITO PRIMA
   della chiamata: claim perso → ci si ferma senza chiamare il modello (e si
   chiude a costo 0 se il claim è ancora nostro) → `ai.estrai_con_strumento`
   (strumento forzato NON strict, input convalidato in modo tollerante) →
   post-elaborazione deterministica → `fn_partenariato_concludi` (un solo
   vincitore);
3. `get_stato` (GET, poll-on-read) con il failsafe `fn_partenariato_chiudi_stale`.

Spesa: costo della PIATTAFORMA (mai una riga in `ai_checks`, che vale come
quota dell'utente), registrata in `partenariati_ai_esecuzioni` (budget) e in
`api_usage_events` (`record_usage`, su ogni esito). Errori dopo l'invio: costo
= max(reale se noto, riserva), oppure ignoto (la riserva resta nel budget);
una richiesta rifiutata dal provider con un 4xx non transitorio (es. 400
`invalid_request_error`) costa 0: è respinta prima della generazione.
Log: solo id del bando e dominio dei documenti.
"""

import asyncio
import json
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import NamedTuple

from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.clients.anthropic_ai import definizione_strumento
from app.core.config import get_settings
from app.core.errors import (
    AiNotConfiguredError,
    AiTimeoutError,
    AiUpstreamError,
    AppError,
    NotFoundError,
    UpstreamError,
)
from app.schemas.partenariato import (
    FonteOut,
    PartenariatoBandoOut,
    PartenariatoEstrazione,
    RegolePartenariatoOut,
    convalida_tollerante,
)
from app.services import (
    bandi_service,
    bando_fonti_service,
    download_sicuro,
    lookup_service,
    pdf_testo,
)
from app.services.ai_check_prompts import serializza_sezioni
from app.services.ai_prezzi import costo_cents, stima_cents
from app.services.openapi_service import record_usage
from app.services.partenariato_errori import raise_from_rpc
from app.services.partenariato_preclassificatore import preclassifica
from app.services.partenariato_prompts import (
    DESCRIZIONE_STRUMENTO_ESTRAZIONE,
    PARTENARIATO_PROMPT_VERSION,
    SCHEMA_VERSION,
    STRUMENTO_ESTRAZIONE,
    SYSTEM_PARTENARIATO,
    DocumentoLetto,
    DocumentoSelezionato,
    build_partenariato_input,
    calcola_catalogo_hash,
    calcola_content_hash,
    meta_partenariato,
    seleziona_pagine,
)
from app.services.partenariato_regole import post_elabora

logger = logging.getLogger("bandofit.partenariati")

SERVIZIO = "partenariato_estrazione"
# Tetto degli id del filtro «Ammette partenariato» (i più recenti). Gli id
# finiscono nell'URL delle due query del catalogo (`id=in.(…)`): con 1000 id a
# 6 cifre la riga di richiesta supera i 9,8 KB, oltre gli 8 KB di default dei
# gateway basati su nginx. 500 id a 7 cifre stanno sotto i 6 KB (misurato con
# il builder postgrest reale, test_partenariati_api).
LIMITE_FILTRO = 500
# Caratteri inviati oltre al catalogo e al tetto dei documenti: marcatori di
# pagina, intestazioni e nota. Entrano nella riserva al caso peggiore.
MARGINE_CARATTERI = 12_000
MSG_AI_NON_CONFIGURATA = "Analisi automatica non configurata su questo ambiente"
MODALITA_FILTRO = {"ammesso": ["ammesso", "obbligatorio"], "obbligatorio": ["obbligatorio"]}

# Colonne lette per l'API: MAI claim_token né extraction (grezza).
ROW_SELECT = (
    "bando_id,bando_slug,bando_titolo,stato,fase,esito,modalita,modalita_effettiva,regole,"
    "fonti_usate,catalogo_hash,content_hash,catalogo_aggiornato_at,prompt_version,"
    "schema_version,model,input_tokens,output_tokens,cost_cents,estratta_at,verificata_at,"
    "ultima_esecuzione_at,ultima_forzata_at,claim_scade_at,esecuzione_id,errore_codice,"
    "errore_at,tentativi_falliti,prossimo_tentativo_at,updated_at"
)

_MESSAGGI_ERRORE = {
    "timeout": "L'analisi ha impiegato troppo tempo: riprova più tardi",
    "interrotta": "L'analisi si è interrotta prima della fine: riprova più tardi",
    "ai_risposta_non_valida": "L'analisi non ha prodotto un risultato valido: riprova più tardi",
    "documenti_non_raggiungibili": (
        "Non siamo riusciti a scaricare i documenti ufficiali del bando: riprova più tardi"
    ),
}
# Esiti di download che non dicono nulla sul documento (rete, portale lento o
# giù): senza documenti letti, «nessun segnale» non sarebbe una conclusione.
_MOTIVI_DOWNLOAD_TRANSITORI = frozenset({"timeout", "rete", "imprevisto", "http_408", "http_429"})
# Tempo massimo per chiudere l'estrazione quando il task viene cancellato
# (spegnimento del processo): poi ci pensa il failsafe.
CHIUSURA_SU_CANCELLAZIONE_SECONDI = 5.0
_MESSAGGIO_ERRORE = "Non siamo riusciti ad analizzare le regole di questo bando: riprova più tardi"

# Riferimenti ai task in corso: senza, il garbage collector può cancellare un
# task fire-and-forget a metà esecuzione.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Avvia la pipeline in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ------------------------------------------------------------------ utilità


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _ts(valore) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not valore:
        return None
    try:
        parsed = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(valore) -> str | None:
    parsed = _ts(valore)
    return parsed.isoformat() if parsed else None


def _https(url) -> str | None:
    return url if isinstance(url, str) and url.lower().startswith("https://") else None


@lru_cache(maxsize=1)
def _schema_json() -> str:
    """Lo strumento dell'estrazione come lo riceve il modello (nome,
    descrizione e schema dell'input): entra nella stima."""
    return json.dumps(
        definizione_strumento(
            PartenariatoEstrazione, STRUMENTO_ESTRAZIONE, DESCRIZIONE_STRUMENTO_ESTRAZIONE
        ),
        ensure_ascii=False,
    )


def stima_riserva_cents(bando: dict) -> int:
    """Riserva al caso peggiore di un'estrazione: prompt di sistema + scheda
    del catalogo + TUTTO il tetto dei documenti + margine + strumento (schema
    e descrizione), e tutto l'output consentito (`ai_prezzi.stima_cents`;
    modello ignoto = il più caro)."""
    settings = get_settings()
    catalogo = len(meta_partenariato(bando)) + sum(
        len(testo) + 8 for _, testo in serializza_sezioni(bando.get("contenuto"))
    )
    caratteri = (
        len(SYSTEM_PARTENARIATO)
        + catalogo
        + settings.partenariato_max_caratteri_documenti
        + MARGINE_CARATTERI
        + len(_schema_json())
    )
    return stima_cents(
        settings.partenariato_ai_model, caratteri, settings.partenariato_ai_max_tokens
    )


# ------------------------------------------------------------ letture DB


async def _chiudi_stale(primary) -> None:
    """Failsafe dei claim scaduti (processo riavviato, pipeline bloccata):
    best-effort, la lettura non deve fallire per questo."""
    try:
        await primary.rpc("fn_partenariato_chiudi_stale", {}).execute()
    except Exception:
        logger.exception("partenariati: failsafe delle estrazioni non riuscito")


async def _leggi_riga(primary, bando_id: int) -> dict | None:
    resp = (
        await primary.table("bando_partenariato")
        .select(ROW_SELECT)
        .eq("bando_id", bando_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _stato_pubblico(secondary, bando_id: int):
    """Stato effettivo e ultimo cambiamento da `bando_pubblico`; None se la
    lettura fallisce (segnale facoltativo: vale la riverifica periodica)."""
    try:
        stati = await bando_fonti_service.leggi_stato_bandi(secondary, [bando_id])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "partenariati: bando_pubblico non leggibile (bando %s, %s)",
            bando_id,
            getattr(exc, "code", None) or type(exc).__name__,
        )
        return None
    return stati.get(bando_id)


async def _avviata_at(primary, esecuzione_id) -> str | None:
    if not esecuzione_id:
        return None
    try:
        resp = (
            await primary.table("partenariati_ai_esecuzioni")
            .select("avviata_at")
            .eq("id", str(esecuzione_id))
            .limit(1)
            .execute()
        )
    except Exception:
        logger.exception("partenariati: avvio dell'esecuzione non leggibile")
        return None
    return _iso(resp.data[0].get("avviata_at")) if resp.data else None


async def _bando_base(secondary, slug: str) -> dict:
    """Il minimo del bando per la GET (niente contenuto: la GET è in polling)."""
    resp = (
        await secondary.table("bando")
        .select("id,slug,titolo,titolo_breve,stato_bando")
        .eq("slug", slug)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Bando non trovato")
    return resp.data[0]


async def _slug_da_id(secondary, bando_id: int) -> str:
    resp = (
        await secondary.table("bando")
        .select("id,slug")
        .eq("id", bando_id)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if not resp.data or not resp.data[0].get("slug"):
        raise NotFoundError("Bando non trovato")
    return resp.data[0]["slug"]


# ------------------------------------------------------ stato e freschezza


def _in_cooldown(row: dict, adesso: datetime) -> bool:
    """Stessa regola della RPC: dopo un errore vale il backoff, altrimenti
    `ultima_esecuzione_at` + cooldown del bando."""
    if row.get("errore_codice"):
        prossimo = _ts(row.get("prossimo_tentativo_at"))
        return prossimo is not None and prossimo > adesso
    ultima = _ts(row.get("ultima_esecuzione_at"))
    ore = get_settings().partenariato_cooldown_bando_ore
    return ultima is not None and ultima + timedelta(hours=ore) > adesso


def _aggiornabile(row: dict, stato_pubblico, adesso: datetime) -> bool:
    """Risultato vecchio e fuori dal cooldown: prompt o schema cambiati,
    riverifica periodica scaduta o catalogo cambiato dopo la generazione."""
    if row.get("stato") == "in_corso" or not row.get("esito"):
        return False
    if _in_cooldown(row, adesso):
        return False
    if (
        row.get("prompt_version") != PARTENARIATO_PROMPT_VERSION
        or row.get("schema_version") != SCHEMA_VERSION
    ):
        return True
    verificata = _ts(row.get("verificata_at"))
    giorni = get_settings().partenariato_riverifica_giorni
    if verificata is None or adesso - verificata >= timedelta(days=giorni):
        return True
    cambiato = _ts(getattr(stato_pubblico, "ultimo_cambiamento_at", None))
    base = _ts(row.get("catalogo_aggiornato_at"))
    return bool(cambiato and base and cambiato > base)


def _forza_disponibile(row: dict | None) -> bool:
    """«Analizza comunque»: solo se l'ultimo esito è `nessun_segnale` e dopo
    non c'è già stata un'esecuzione forzata."""
    if not row or row.get("esito") != "nessun_segnale" or row.get("stato") == "in_corso":
        return False
    forzata = _ts(row.get("ultima_forzata_at"))
    estratta = _ts(row.get("estratta_at"))
    return forzata is None or (estratta is not None and forzata < estratta)


def _regole_out(row: dict) -> RegolePartenariatoOut | None:
    regole = row.get("regole")
    if not isinstance(regole, dict):
        return None
    try:
        return RegolePartenariatoOut.model_validate(regole)
    except ValidationError:
        logger.warning("partenariati: regole non leggibili (bando %s)", row.get("bando_id"))
        return None


def _fonti_out(fonti_usate) -> list[FonteOut]:
    fonti: list[FonteOut] = []
    for voce in fonti_usate if isinstance(fonti_usate, list) else []:
        if not isinstance(voce, dict):
            continue
        incluse = voce.get("pagine_incluse")
        try:
            fonti.append(
                FonteOut(
                    n=voce.get("n"),
                    etichetta=voce.get("etichetta") or "Documento ufficiale",
                    dominio=voce.get("dominio"),
                    url=_https(voce.get("url")),
                    stato=voce.get("stato"),
                    pagine_totali=int(voce.get("pagine_totali") or 0),
                    pagine_incluse=[n for n in incluse if isinstance(n, int)]
                    if isinstance(incluse, list)
                    else [],
                    troncato=bool(voce.get("troncato")),
                )
            )
        except (ValidationError, TypeError, ValueError):
            continue
    return fonti


def _to_out(
    bando: dict,
    row: dict | None,
    stato_pubblico,
    *,
    adesso: datetime,
    ai_attiva: bool | None = None,
    avviata_at: str | None = None,
) -> PartenariatoBandoOut:
    base = {
        "bando_id": int(bando["id"]),
        "bando_slug": bando.get("slug") or (row or {}).get("bando_slug") or "",
        "stato_bando": getattr(stato_pubblico, "stato_effettivo", None) or bando.get("stato_bando"),
    }
    if row is None:
        attiva = ai_attiva is not False
        return PartenariatoBandoOut(
            **base,
            stato="non_estratta",
            puo_avviare=attiva,
            motivo_non_avviabile=None if attiva else "ai_non_configurata",
        )
    esito = row.get("esito")
    in_corso = row.get("stato") == "in_corso"
    if in_corso and not esito:
        stato = "in_corso"
    elif esito == "estratta":
        stato = "pronta"
    elif esito == "nessun_segnale":
        stato = "nessun_segnale"
    else:
        stato = "errore"
    prossimo = _ts(row.get("prossimo_tentativo_at"))
    aggiornabile = not in_corso and _aggiornabile(row, stato_pubblico, adesso)

    motivo: str | None = None
    if ai_attiva is False:
        motivo = "ai_non_configurata"
    elif in_corso:
        motivo = "in_corso"
    elif stato == "errore":
        motivo = "cooldown" if _in_cooldown(row, adesso) else None
    elif not aggiornabile and not (stato == "nessun_segnale" and _forza_disponibile(row)):
        motivo = "cooldown" if _in_cooldown(row, adesso) else "aggiornata"

    return PartenariatoBandoOut(
        **base,
        stato=stato,
        fase=row.get("fase") if in_corso else None,
        aggiornamento_in_corso=in_corso and bool(esito),
        aggiornabile=aggiornabile,
        regole=_regole_out(row) if esito == "estratta" else None,
        fonti=_fonti_out(row.get("fonti_usate")),
        estratta_at=_iso(row.get("estratta_at")),
        verificata_at=_iso(row.get("verificata_at")),
        avviata_at=avviata_at if in_corso else None,
        # Anche con le regole precedenti ancora servite: l'ultimo tentativo è fallito.
        errore=(
            _MESSAGGI_ERRORE.get(row.get("errore_codice") or "", _MESSAGGIO_ERRORE)
            if row.get("errore_codice") and not in_corso
            else None
        ),
        riprova_dopo=prossimo.isoformat() if prossimo and prossimo > adesso else None,
        puo_avviare=motivo is None,
        motivo_non_avviabile=motivo,
    )


async def calls_aperte(primary, bando_id: int) -> int:
    """Call aperte sul bando (WP5): pubblicate, visibili a tutti e non
    scadute (Europe/Rome; lo scheduler le chiude di notte, qui non si aspetta).
    Le call `solo_invitati` non si contano: non devono rivelarsi. Best-effort:
    un guasto vale 0, la GET in polling non fallisce per questo."""
    oggi = bandi_service.today_italy().isoformat()
    try:
        resp = (
            await primary.table("partner_calls")
            .select("scadenza_call")
            .eq("bando_id", int(bando_id))
            .eq("stato", "pubblicata")
            .eq("visibilita", "pubblica")
            .limit(1000)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "partenariati: call aperte non leggibili (bando %s, %s)",
            bando_id,
            getattr(exc, "code", None) or type(exc).__name__,
        )
        return 0
    return sum(
        1
        for riga in resp.data or []
        if isinstance(riga, dict) and str(riga.get("scadenza_call") or "")[:10] >= oggi
    )


async def get_stato(primary, secondary, slug: str, *, ai=None) -> PartenariatoBandoOut:
    """Stato delle regole di partenariato del bando (GET in polling). Applica
    il failsafe dei claim scaduti prima di leggere."""
    bando = await _bando_base(secondary, slug)
    await _chiudi_stale(primary)
    row = await _leggi_riga(primary, int(bando["id"]))
    stato_pubblico = await _stato_pubblico(secondary, int(bando["id"]))
    avviata_at = None
    if row and row.get("stato") == "in_corso":
        avviata_at = await _avviata_at(primary, row.get("esecuzione_id")) or _iso(
            row.get("updated_at")
        )
    out = _to_out(
        bando,
        row,
        stato_pubblico,
        adesso=_adesso(),
        ai_attiva=None if ai is None else bool(ai.enabled),
        avviata_at=avviata_at,
    )
    out.calls_aperte = await calls_aperte(primary, int(bando["id"]))
    return out


# ------------------------------------------------------------ avvio


async def _slug_piano(primary, owner_id: str) -> str | None:
    resp = (
        await primary.table("user_subscriptions")
        .select("subscription_plans(slug)")
        .eq("user_id", str(owner_id))
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    piano = resp.data[0].get("subscription_plans") or {}
    return piano.get("slug") if isinstance(piano, dict) else None


async def _email_verificata(primary, user_id: str) -> bool:
    """Fail-closed: se la verifica non si può fare, l'analisi non parte."""
    try:
        resp = await primary.rpc(
            "fn_email_verificate", {"p_user_ids": [str(user_id)]}
        ).execute()
    except Exception as exc:
        logger.error("partenariati: verifica dell'email non riuscita (%s)", type(exc).__name__)
        raise UpstreamError() from exc
    return str(user_id) in {str(v) for v in (resp.data or [])}


async def limite_utente(primary, user: dict, owner_id: str) -> int:
    """Analisi al giorno avviabili dall'utente: il piano è quello del
    titolare (un membro lo eredita). Gratuito o senza abbonamento → 3, e solo
    con l'email verificata; gli altri piani → 10."""
    settings = get_settings()
    slug = await _slug_piano(primary, owner_id)
    if slug and slug != "gratuito":
        return settings.partenariato_limite_utente_giorno
    if not await _email_verificata(primary, user["id"]):
        raise AppError(
            403,
            "email_non_verificata",
            "Conferma il tuo indirizzo email per avviare l'analisi delle regole di partenariato",
        )
    return settings.partenariato_limite_utente_gratuito_giorno


@dataclass
class _Richiesta:
    origine: str  # utente | call | batch | admin | valutazione
    budget_cents: int
    utente: dict | None = None
    owner_id: str | None = None
    company_id: str | None = None
    # «Analizza comunque» dell'utente (validato: dopo nessun_segnale, una volta)
    forza: bool = False
    # admin: salta il cooldown del bando
    ignora_cooldown: bool = False
    # admin: nessun controllo di freschezza, nessuna scorciatoia della cache
    sempre: bool = False


@dataclass
class _Avvio:
    out: PartenariatoBandoOut
    pipeline: Coroutine | None = None
    esito: str = "in_corso"  # prenotata | in_corso | fresca


async def _avvia(primary, secondary, ai, bando: dict, richiesta: _Richiesta) -> _Avvio:
    """Decide se serve un'estrazione e, se sì, prenota claim e spesa. La
    pipeline torna come coroutine: il chiamante la lancia in background (API)
    o la attende (batch, valutazione)."""
    settings = get_settings()
    bando_id = int(bando["id"])
    adesso = _adesso()
    await _chiudi_stale(primary)
    row = await _leggi_riga(primary, bando_id)
    stato_pubblico = await _stato_pubblico(secondary, bando_id)

    def corrente(riga: dict | None, avviata_at: str | None = None) -> PartenariatoBandoOut:
        return _to_out(
            bando, riga, stato_pubblico, adesso=_adesso(), ai_attiva=True, avviata_at=avviata_at
        )

    if row and row.get("stato") == "in_corso":
        avviata = await _avviata_at(primary, row.get("esecuzione_id"))
        return _Avvio(corrente(row, avviata))
    ignora_cooldown = richiesta.ignora_cooldown
    if richiesta.forza:
        if not _forza_disponibile(row):
            raise AppError(
                409,
                "forza_non_ammessa",
                "L'analisi forzata si può chiedere una sola volta, dopo un'analisi che non ha "
                "trovato riferimenti al partenariato",
            )
        ignora_cooldown = True
    elif not richiesta.sempre and row and row.get("esito") and not _aggiornabile(
        row, stato_pubblico, adesso
    ):
        return _Avvio(corrente(row), esito="fresca")

    limite = None
    if richiesta.origine in ("utente", "call") and richiesta.utente:
        limite = await limite_utente(primary, richiesta.utente, richiesta.owner_id)

    riserva = stima_riserva_cents(bando)
    richiedente = str(richiesta.utente["id"]) if richiesta.utente else None
    try:
        resp = await primary.rpc(
            "fn_partenariato_prenota",
            {
                "p_bando_id": bando_id,
                "p_bando_slug": bando.get("slug"),
                "p_bando_titolo": (
                    bando.get("titolo_breve") or bando.get("titolo") or bando.get("slug")
                ),
                "p_origine": richiesta.origine,
                "p_richiedente": richiedente,
                "p_company": str(richiesta.company_id) if richiesta.company_id else None,
                "p_owner": str(richiesta.owner_id) if richiesta.owner_id else None,
                "p_budget_cents": int(richiesta.budget_cents),
                "p_costo_riservato_cents": riserva,
                "p_limite_utente": limite,
                "p_cooldown_minuti": settings.partenariato_cooldown_bando_ore * 60,
                "p_ttl_secondi": settings.partenariato_claim_ttl_seconds,
                "p_ignora_cooldown": ignora_cooldown,
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    dati = resp.data if isinstance(resp.data, dict) else {}
    if dati.get("esito") != "prenotata" or not dati.get("claim_token"):
        # Un'altra richiesta ha vinto il claim nel frattempo.
        attuale = await _leggi_riga(primary, bando_id)
        avviata = await _avviata_at(primary, (attuale or {}).get("esecuzione_id"))
        return _Avvio(corrente(attuale, avviata))

    pipeline = _pipeline(
        primary,
        secondary,
        ai,
        bando=bando,
        claim_token=str(dati["claim_token"]),
        esecuzione_id=str(dati.get("esecuzione_id") or ""),
        origine=richiesta.origine,
        forza=richiesta.forza or richiesta.sempre,
        user_id=richiedente,
        owner_id=str(richiesta.owner_id) if richiesta.owner_id else richiedente,
        riserva_cents=riserva,
        precedente=row,
    )
    # La pipeline va lanciata comunque: una rilettura fallita non deve
    # lasciare il claim appeso fino al failsafe.
    try:
        attuale = await _leggi_riga(primary, bando_id)
    except Exception:
        logger.exception("partenariati: rilettura dopo la prenotazione non riuscita")
        attuale = None
    if not attuale or attuale.get("stato") != "in_corso":
        attuale = {**(row or {}), "bando_id": bando_id, "stato": "in_corso", "fase": "documenti"}
    return _Avvio(corrente(attuale, _adesso().isoformat()), pipeline, esito="prenotata")


async def avvia_analisi(
    primary,
    secondary,
    ai,
    user: dict,
    active,
    slug: str,
    *,
    forza: bool = False,
    origine: str = "utente",
) -> tuple[PartenariatoBandoOut, bool]:
    """Avvia (se serve) l'estrazione delle regole del bando.

    Ritorna (stato, avviata): avviata = una nuova estrazione è partita ora
    (202); altrimenti il risultato è fresco o un'estrazione è già in corso
    (200). Errori: 404 bando, 409 `forza_non_ammessa`, 403
    `email_non_verificata`, 429 `partenariato_cooldown` /
    `ai_limite_giornaliero` / `ai_sospesa_oggi`, 503 `ai_not_configured`."""
    if not ai.enabled:
        raise AiNotConfiguredError(MSG_AI_NON_CONFIGURATA)
    bando = await bandi_service.fetch_bando_for_ai(secondary, slug)
    avvio = await _avvia(
        primary,
        secondary,
        ai,
        bando,
        _Richiesta(
            origine=origine,
            budget_cents=get_settings().partenariato_budget_cents_giorno,
            utente=user,
            owner_id=active.owner_id,
            company_id=active.company_id,
            forza=forza,
        ),
    )
    if avvio.pipeline is not None:
        _spawn(avvio.pipeline)
    return avvio.out, avvio.pipeline is not None


async def forza_admin(
    primary, secondary, ai, user: dict, bando_id: int, *, ignora_cooldown: bool = False
) -> tuple[PartenariatoBandoOut, bool]:
    """Estrazione chiesta dall'admin: nessun limite per utente, nessuna
    scorciatoia della cache (paga sempre il modello), cooldown saltato solo
    con `ignora_cooldown`; stesso budget giornaliero delle estrazioni."""
    if not ai.enabled:
        raise AiNotConfiguredError(MSG_AI_NON_CONFIGURATA)
    slug = await _slug_da_id(secondary, bando_id)
    bando = await bandi_service.fetch_bando_for_ai(secondary, slug)
    avvio = await _avvia(
        primary,
        secondary,
        ai,
        bando,
        _Richiesta(
            origine="admin",
            budget_cents=get_settings().partenariato_budget_cents_giorno,
            utente=user,
            ignora_cooldown=ignora_cooldown,
            sempre=True,
        ),
    )
    if avvio.pipeline is not None:
        _spawn(avvio.pipeline)
    return avvio.out, avvio.pipeline is not None


async def esegui_per_bando(
    primary, secondary, ai, bando: dict, *, origine: str, budget_cents: int
) -> str:
    """Estrazione ATTESA (batch notturno, valutazione): nessun utente,
    nessun limite per utente, budget del gruppo dell'origine. Ritorna
    `fresca`, `in_corso` o l'esito della pipeline. I rifiuti della RPC
    (budget esaurito, cooldown) salgono come `AppError`: il chiamante decide
    se fermarsi."""
    avvio = await _avvia(
        primary,
        secondary,
        ai,
        bando,
        _Richiesta(origine=origine, budget_cents=budget_cents),
    )
    if avvio.pipeline is None:
        return avvio.esito
    return await avvio.pipeline


async def bando_ids_per_modalita(primary, modalita: str) -> list[int]:
    """Id dei bandi con esito `estratta` e modalità EFFETTIVA (citazione
    verificata) compatibile: `ammesso` comprende `obbligatorio`. Un solo
    array dalla RPC (niente max-rows), al massimo LIMITE_FILTRO, i più recenti."""
    valori = MODALITA_FILTRO.get(modalita)
    if not valori:
        return []
    resp = await primary.rpc(
        "fn_partenariato_bando_ids", {"p_modalita": valori, "p_limite": LIMITE_FILTRO}
    ).execute()
    ids = [int(v) for v in (resp.data or []) if isinstance(v, int) and not isinstance(v, bool)]
    if len(ids) >= LIMITE_FILTRO:
        logger.warning(
            "partenariati: filtro «%s» al tetto di %s bandi (i più recenti)", modalita, LIMITE_FILTRO
        )
    return ids


# ------------------------------------------------------------ pipeline


async def _rinnova(primary, bando_id: int, claim_token: str, fase: str) -> bool:
    """Heartbeat: False = claim perso (ripreso da altri o chiuso dal failsafe)."""
    resp = await primary.rpc(
        "fn_partenariato_rinnova",
        {
            "p_bando_id": bando_id,
            "p_claim_token": claim_token,
            "p_fase": fase,
            "p_ttl_secondi": get_settings().partenariato_claim_ttl_seconds,
        },
    ).execute()
    return resp.data is True


async def _concludi(primary, bando_id: int, claim_token: str, esito: str, dati: dict) -> bool:
    resp = await primary.rpc(
        "fn_partenariato_concludi",
        {
            "p_bando_id": bando_id,
            "p_claim_token": claim_token,
            "p_esito": esito,
            "p_dati": dati,
        },
    ).execute()
    return resp.data is True


async def _concludi_sicuro(primary, bando_id: int, claim_token: str, esito: str, dati: dict):
    """Chiusura nei rami d'errore: se fallisce anche lei, il failsafe chiuderà
    la riga allo scadere del claim (la riserva resta nel budget)."""
    try:
        return await _concludi(primary, bando_id, claim_token, esito, dati)
    except Exception:
        logger.exception("partenariati: chiusura dell'estrazione non riuscita (bando %s)", bando_id)
        return False


async def _registra(
    primary,
    *,
    user_id: str | None,
    owner_id: str | None,
    outcome: str,
    cost_cents: int,
    meta: dict,
) -> None:
    """Registro consumi (`api_usage_events`) su ogni esito. Senza utente
    (batch) la riga si scrive con utente e owner NULL: `record_usage` li
    renderebbe stringhe non valide. Non solleva mai."""
    if user_id and owner_id:
        await record_usage(
            primary,
            user_id=user_id,
            family_parent_id=owner_id,
            service=SERVIZIO,
            outcome=outcome,
            cost_cents=int(cost_cents),
            meta=meta,
            provider="anthropic",
        )
        return
    try:
        await primary.table("api_usage_events").insert(
            {
                "user_id": user_id,
                "family_parent_id": owner_id,
                "provider": "anthropic",
                "service": SERVIZIO,
                "outcome": outcome,
                "cost_cents": int(cost_cents),
                "request_meta": meta,
            }
        ).execute()
    except Exception:
        logger.exception("registro consumi non scrivibile (service=%s)", SERVIZIO)


async def _nessun_testo():
    return None


async def _lookups(secondary):
    try:
        return await lookup_service.get_lookups(secondary)
    except Exception:
        logger.warning("partenariati: lookup del catalogo non disponibili", exc_info=True)
        return None


def documenti_letti(candidati, scaricati, testi) -> tuple[list[DocumentoLetto], list[dict]]:
    """Documenti per il prompt e voci di `fonti_usate` (audit: sha256 e byte,
    mai il contenuto)."""
    documenti: list[DocumentoLetto] = []
    fonti: list[dict] = []
    for n, (candidato, scaricato, testo) in enumerate(zip(candidati, scaricati, testi), start=1):
        # «ok» del download = «scaricato» nella macchina a stati del documento.
        stato_download = "scaricato" if scaricato.stato == "ok" else scaricato.stato
        fonte = {
            "n": n,
            "etichetta": candidato.etichetta,
            "dominio": candidato.dominio,
            # Bloccato dal download (redirect verso un dominio escluso, IP non
            # pubblico…): il link non si mostra, il browser ci arriverebbe.
            "url": None if scaricato.stato == "bloccato_policy" else _https(candidato.url),
            "tipo": candidato.tipo,
            "origine": candidato.origine,
            "stato": stato_download,
            "sha256": scaricato.sha256,
            "byte": scaricato.byte,
            "pagine_totali": 0,
            "pagine_incluse": [],
            "troncato": False,
        }
        if scaricato.stato != "ok" or testo is None:
            fonti.append(fonte)
            documenti.append(
                DocumentoLetto(n=n, etichetta=candidato.etichetta, dominio=candidato.dominio,
                               stato=stato_download)
            )
            continue
        leggibile = testo.stato in ("letto", "letto_parziale")
        fonte.update(stato=testo.stato, pagine_totali=testo.pagine_totali)
        fonti.append(fonte)
        documenti.append(
            DocumentoLetto(
                n=n,
                etichetta=candidato.etichetta,
                dominio=candidato.dominio,
                stato=testo.stato,
                pagine_totali=testo.pagine_totali,
                # Una scansione (non_leggibile) non entra: poche righe sparse
                # sarebbero solo rumore per il modello.
                pagine=list(testo.pagine) if leggibile else [],
                parziale=testo.stato == "letto_parziale",
            )
        )
    return documenti, fonti


def _fallimento_transitorio(scaricato, testo) -> bool:
    """Il documento non è stato letto per una causa che può sparire da sola
    (rete, portale lento o in errore, lettura oltre il tempo): non è un
    documento «senza segnali»."""
    if scaricato.stato == "errore_download":
        motivo = scaricato.motivo or ""
        return motivo in _MOTIVI_DOWNLOAD_TRANSITORI or motivo.startswith("http_5")
    return testo is not None and testo.stato == "timeout"


def _segna_incluse(fonti: list[dict], selezionati: list[DocumentoSelezionato]) -> list[dict]:
    per_n = {doc.n: doc for doc in selezionati}
    aggiornate = []
    for fonte in fonti:
        doc = per_n.get(fonte["n"])
        voce = dict(fonte)
        if doc is not None and voce["stato"] in ("letto", "letto_parziale"):
            voce["pagine_incluse"] = doc.numeri_pagina
            voce["troncato"] = doc.troncato
            if not doc.pagine:
                voce["stato"] = "escluso_tetto"
        aggiornate.append(voce)
    return aggiornate


def _versioni_uguali(precedente: dict) -> bool:
    return (
        precedente.get("prompt_version") == PARTENARIATO_PROMPT_VERSION
        and precedente.get("schema_version") == SCHEMA_VERSION
    )


def _catalogo_invariato(precedente: dict | None, catalogo_hash: str, forza: bool) -> bool:
    """Stesso catalogo e stessi documenti candidati di un'estrazione ancora
    dentro la riverifica periodica: non serve riacquisire i documenti."""
    if forza or not precedente or precedente.get("esito") != "estratta":
        return False
    if precedente.get("catalogo_hash") != catalogo_hash or not _versioni_uguali(precedente):
        return False
    verificata = _ts(precedente.get("verificata_at"))
    giorni = get_settings().partenariato_riverifica_giorni
    return verificata is not None and _adesso() - verificata < timedelta(days=giorni)


def _riusabile(precedente: dict | None, content_hash: str, forza: bool) -> bool:
    return bool(
        not forza
        and precedente
        and precedente.get("esito") == "estratta"
        and precedente.get("content_hash") == content_hash
        and _versioni_uguali(precedente)
    )


# ------------------------------------------- passi della pipeline senza DB
# Nessun claim, cache o scrittura: li compone `_pipeline` e, identici, la
# valutazione locale (`partenariato_valutazione_locale`), che deve restare
# fedele alla produzione.


class InputModello(NamedTuple):
    """Ciò che si invia al modello e ciò che serve a verificarne l'output."""

    testo: str
    sezioni: dict[str, str]  # indice → testo ESATTAMENTE come inviato
    selezionati: list[DocumentoSelezionato]
    fonti: list[dict]  # voci di `fonti_usate` con le pagine incluse


def candidati_documenti(bando: dict, links, *, settings) -> list:
    """Documenti da acquisire: da `bando_link`; dagli allegati del catalogo
    SOLO se la lettura dei link è fallita (None)."""
    return bando_fonti_service.seleziona_candidati(
        links, bando.get("allegati") if links is None else None,
        settings.partenariato_max_documenti,
    )


async def scarica_documenti(candidati: list, *, settings) -> list:
    """Download sicuro dei candidati, in parallelo (fase documenti)."""
    return await asyncio.gather(
        *(
            download_sicuro.scarica_pdf(
                c.url,
                max_bytes=settings.partenariato_pdf_max_bytes,
                timeout_s=settings.partenariato_download_timeout_seconds,
            )
            for c in candidati
        )
    )


async def leggi_documenti(scaricati: list, *, settings) -> list:
    """Testo dei PDF scaricati, ciascuno in un processo separato con tempo
    massimo (fase lettura); None per i documenti non scaricati."""
    return await asyncio.gather(
        *(
            pdf_testo.estrai_testo(
                s.contenuto,
                max_pagine=settings.partenariato_pdf_max_pagine,
                timeout_s=settings.partenariato_pdf_timeout_seconds,
            )
            if s.stato == "ok" and s.contenuto
            else _nessun_testo()
            for s in scaricati
        )
    )


def documenti_non_raggiungibili(links, documenti, scaricati, testi) -> bool:
    """Niente letto e una causa che può sparire da sola: link ufficiali non
    leggibili dal catalogo (None = errore di lettura) o documenti non
    scaricati/letti per rete, portale lento, tempo scaduto."""
    return not any(doc.pagine for doc in documenti) and (
        links is None or any(_fallimento_transitorio(s, t) for s, t in zip(scaricati, testi))
    )


def sezioni_preclassificatore(bando: dict, documenti: list[DocumentoLetto]) -> dict[str, str]:
    """Scheda del catalogo e pagine lette, come le vede il pre-classificatore."""
    sezioni = {"META": meta_partenariato(bando), **dict(serializza_sezioni(bando.get("contenuto")))}
    for doc in documenti:
        for numero, testo in doc.pagine:
            sezioni[f"D{doc.n}-p{numero}"] = testo
    return sezioni


def esito_senza_modello(livello: str, documenti_mancati: bool, *, forza: bool) -> str | None:
    """Esito deciso SENZA chiamare il modello, None se il modello serve. Zero
    segnali (e nessun «Analizza comunque») = `nessun_segnale`; ma se nessun
    documento è stato letto per cause transitorie = `errore`
    (`documenti_non_raggiungibili`): «nessun segnale» varrebbe 14 giorni
    senza aver letto nulla."""
    if livello != "nessuno" or forza:
        return None
    return "errore" if documenti_mancati else "nessun_segnale"


def prepara_input(
    bando: dict, documenti: list[DocumentoLetto], fonti: list[dict], per_sezione, *, settings
) -> InputModello:
    """Pagine selezionate entro il tetto dei caratteri, fonti con le pagine
    incluse e input del modello."""
    selezionati = seleziona_pagine(
        documenti,
        max_caratteri=settings.partenariato_max_caratteri_documenti,
        per_sezione=per_sezione,
    )
    fonti = _segna_incluse(fonti, selezionati)
    testo, sezioni, _ = build_partenariato_input(bando, bando.get("contenuto"), selezionati)
    return InputModello(testo, sezioni, selezionati, fonti)


def stima_input_cents(testo: str, *, settings) -> int:
    """Costo massimo della chiamata sull'input davvero inviato: prompt di
    sistema + messaggio + strumento (schema e descrizione), e tutti i
    `max_tokens`."""
    return stima_cents(
        settings.partenariato_ai_model,
        len(SYSTEM_PARTENARIATO) + len(testo) + len(_schema_json()),
        settings.partenariato_ai_max_tokens,
    )


async def genera_estrazione(ai, testo: str, *, settings):
    """La chiamata al modello: (PartenariatoEstrazione, AiUsage). Strumento
    forzato NON strict (lo schema come grammatica dell'output strutturato è
    rifiutato: «The compiled grammar is too large»), input convalidato in
    modo tollerante (`convalida_tollerante`)."""
    return await ai.estrai_con_strumento(
        SYSTEM_PARTENARIATO,
        testo,
        PartenariatoEstrazione,
        nome_strumento=STRUMENTO_ESTRAZIONE,
        descrizione=DESCRIZIONE_STRUMENTO_ESTRAZIONE,
        convalida=convalida_tollerante,
        model=settings.partenariato_ai_model,
        max_tokens=settings.partenariato_ai_max_tokens,
        timeout=settings.partenariato_ai_timeout_seconds,
    )


# Stati 4xx del provider che si risolvono ripetendo (timeout della richiesta,
# conflitto, rate limit): ogni altro 4xx è un rifiuto della richiesta stessa,
# prima della generazione (nessun token).
STATI_4XX_TRANSITORI = frozenset({408, 409, 429})


def stato_http(exc: BaseException) -> int | None:
    """Stato HTTP della risposta d'errore del provider dietro un
    `AiUpstreamError` (il client lo solleva `from` l'eccezione dell'SDK);
    None se una risposta HTTP non c'è stata (rete, timeout)."""
    stato = getattr(exc.__cause__, "status_code", None)
    return stato if isinstance(stato, int) and not isinstance(stato, bool) else None


def non_transitorio(stato: int | None) -> bool:
    """Un 4xx che ripetere non risolve (tutti tranne 408, 409 e 429)."""
    return stato is not None and 400 <= stato < 500 and stato not in STATI_4XX_TRANSITORI


async def regole_da_estrazione(secondary, estrazione, sezioni: dict, fonti: list) -> dict:
    """Post-elaborazione deterministica (verifica delle citazioni sul testo
    inviato, coerenza, regioni dalle lookup del catalogo)."""
    return post_elabora(estrazione, sezioni, fonti, await _lookups(secondary))


@dataclass
class _Contesto:
    """Ciò che serve ai rami della pipeline per chiudere e registrare."""

    bando_id: int
    claim_token: str
    user_id: str | None
    owner_id: str | None
    meta: dict = field(default_factory=dict)


async def _chiudi_senza_modello(primary, ctx: _Contesto, esito: str, dati: dict) -> str:
    """nessun_segnale / riusata / errore prima del modello: costo 0
    ESPLICITO (assente = costo ignoto, la riserva resterebbe nel budget)."""
    await _concludi(
        primary,
        ctx.bando_id,
        ctx.claim_token,
        esito,
        {**dati, "cost_cents": 0, "input_tokens": 0, "output_tokens": 0},
    )
    await _registra(
        primary, user_id=ctx.user_id, owner_id=ctx.owner_id,
        outcome="error" if esito == "errore" else "success", cost_cents=0,
        meta={**ctx.meta, "esito": esito, **(
            {"errore": dati.get("errore_codice")} if esito == "errore" else {}
        )},
    )
    return esito


async def _pipeline(
    primary,
    secondary,
    ai,
    *,
    bando: dict,
    claim_token: str,
    esecuzione_id: str,
    origine: str,
    forza: bool,
    user_id: str | None,
    owner_id: str | None,
    riserva_cents: int,
    precedente: dict | None,
) -> str:
    """Documenti → lettura → pre-classificazione → input → modello →
    post-elaborazione → chiusura. Non solleva MAI: ogni esito chiude la riga e
    finisce nel registro consumi. Ritorna l'esito (estratta, riusata,
    nessun_segnale, errore, timeout, claim_perso)."""
    settings = get_settings()
    modello = settings.partenariato_ai_model
    bando_id = int(bando["id"])
    contenuto = bando.get("contenuto")
    ctx = _Contesto(
        bando_id=bando_id,
        claim_token=claim_token,
        user_id=user_id,
        owner_id=owner_id,
        meta={"bando_id": bando_id, "bando_slug": bando.get("slug"), "origine": origine,
              "esecuzione_id": esecuzione_id, "model": modello},
    )
    usage = None  # usage della risposta del modello, se è arrivata
    inviata = False  # la richiesta al modello può essere partita
    stima = 0  # costo massimo stimato sull'input davvero inviato
    try:
        # ---- fase documenti (impostata dalla prenotazione)
        links = await bando_fonti_service.leggi_link_documenti(secondary, bando_id)
        candidati = candidati_documenti(bando, links, settings=settings)
        catalogo_hash = calcola_catalogo_hash(bando, contenuto, [c.url for c in candidati])
        stato_pubblico = await _stato_pubblico(secondary, bando_id)
        meta_dati = {
            "catalogo_hash": catalogo_hash,
            "catalogo_aggiornato_at": _iso(
                getattr(stato_pubblico, "ultimo_cambiamento_at", None)
            ),
            "prompt_version": PARTENARIATO_PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
        }
        if _catalogo_invariato(precedente, catalogo_hash, forza):
            return await _chiudi_senza_modello(primary, ctx, "riusata", meta_dati)

        scaricati = await scarica_documenti(candidati, settings=settings)
        if not await _rinnova(primary, bando_id, claim_token, "lettura"):
            return await _claim_perso(primary, ctx)

        # ---- fase lettura
        testi = await leggi_documenti(scaricati, settings=settings)
        documenti, fonti = documenti_letti(candidati, scaricati, testi)
        documenti_mancati = documenti_non_raggiungibili(links, documenti, scaricati, testi)
        # I byte dei PDF non servono più (fonti ha sha256 e dimensione): non
        # restano in memoria durante l'attesa e la chiamata al modello.
        del scaricati
        # Regex su centinaia di migliaia di caratteri: fuori dall'event loop.
        pre = await asyncio.to_thread(preclassifica, sezioni_preclassificatore(bando, documenti))
        senza_modello = esito_senza_modello(pre.livello, documenti_mancati, forza=forza)
        if senza_modello == "errore":
            # Nessun documento letto per cause transitorie: errore con
            # backoff, costo 0 (il modello non è stato chiamato).
            return await _chiudi_senza_modello(
                primary, ctx, "errore",
                {"errore_codice": "documenti_non_raggiungibili", "model": None},
            )
        if senza_modello == "nessun_segnale":
            return await _chiudi_senza_modello(
                primary,
                ctx,
                "nessun_segnale",
                {**meta_dati, "preclassificazione": pre.a_dict(), "fonti_usate": fonti,
                 "content_hash": None, "model": None},
            )

        testo, sezioni, selezionati, fonti = prepara_input(
            bando, documenti, fonti, pre.per_sezione, settings=settings
        )
        content_hash = calcola_content_hash(
            testo,
            selezionati,
            {
                "max_documenti": settings.partenariato_max_documenti,
                "max_pagine": settings.partenariato_pdf_max_pagine,
                "max_caratteri_documenti": settings.partenariato_max_caratteri_documenti,
            },
        )
        comuni = {
            **meta_dati,
            "preclassificazione": pre.a_dict(),
            "fonti_usate": fonti,
            "content_hash": content_hash,
        }
        if _riusabile(precedente, content_hash, forza):
            return await _chiudi_senza_modello(primary, ctx, "riusata", comuni)

        # ---- fase analisi: heartbeat SUBITO prima della chiamata pagata
        if not await _rinnova(primary, bando_id, claim_token, "analisi"):
            return await _claim_perso(primary, ctx)
        stima = stima_input_cents(testo, settings=settings)
        if stima > riserva_cents:
            logger.warning(
                "partenariati: stima %s oltre la riserva %s (bando %s)", stima, riserva_cents,
                bando_id,
            )
        inviata = True
        estrazione, usage = await genera_estrazione(ai, testo, settings=settings)
        costo = costo_cents(modello, usage.input_tokens, usage.output_tokens)
        regole = await regole_da_estrazione(secondary, estrazione, sezioni, fonti)
        vinto = await _concludi(
            primary,
            bando_id,
            claim_token,
            "estratta",
            {
                **comuni,
                "modalita": estrazione.modalita,
                "modalita_effettiva": regole["modalita_effettiva"],
                "extraction": estrazione.model_dump(mode="json"),
                "regole": regole,
                "model": modello,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_cents": costo,
            },
        )
        if not vinto:
            # Il claim è scaduto durante la chiamata: il risultato pagato va
            # perso (la riserva resta nel budget, l'esecuzione è interrotta).
            logger.error("partenariati: claim perso dopo la generazione (bando %s)", bando_id)
        await _registra(
            primary, user_id=user_id, owner_id=owner_id,
            outcome="success" if vinto else "error", cost_cents=costo,
            meta={**ctx.meta, "esito": "estratta" if vinto else "claim_perso",
                  "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        )
        return "estratta" if vinto else "claim_perso"
    except AiTimeoutError:
        # Esito e addebito ignoti: si registra il caso peggiore (la riserva,
        # o la stima sull'input davvero inviato se più alta).
        peggiore = max(riserva_cents, stima)
        await _concludi_sicuro(
            primary, bando_id, claim_token, "timeout",
            {"cost_cents": peggiore, "model": modello, "errore_codice": "timeout"},
        )
        await _registra(
            primary, user_id=user_id, owner_id=owner_id, outcome="timeout_unknown",
            cost_cents=peggiore, meta={**ctx.meta, "esito": "timeout"},
        )
        return "timeout"
    except asyncio.CancelledError:
        # Task cancellato (spegnimento del processo, deploy): si chiude qui
        # l'estrazione e si registra il consumo, poi la cancellazione prosegue.
        # Senza, la riga resterebbe al failsafe e il registro consumi per
        # provider non vedrebbe una chiamata forse già addebitata.
        await _chiudi_su_cancellazione(
            primary, ctx, modello=modello, usage=usage, inviata=inviata,
            peggiore=max(riserva_cents, stima),
        )
        raise
    except AiUpstreamError as exc:
        uso = exc.usage
        if uso is not None:
            # La risposta è arrivata (troncata o non valida): pagata.
            costo = max(costo_cents(modello, uso.input_tokens, uso.output_tokens), riserva_cents)
            codice = "ai_risposta_non_valida"
        elif non_transitorio(stato_http(exc)):
            # Richiesta rifiutata dal provider (4xx non transitorio, es. 400
            # invalid_request_error): respinta prima della generazione,
            # nessun token. Costo 0 ESPLICITO: la riserva esce dal budget.
            costo = 0
            codice = "ai_richiesta_rifiutata"
        else:
            costo = None  # errore di rete o del provider: costo ignoto
            codice = "ai_non_disponibile"
        await _concludi_sicuro(
            primary, bando_id, claim_token, "errore",
            {
                "cost_cents": costo,
                "input_tokens": uso.input_tokens if uso else 0,
                "output_tokens": uso.output_tokens if uso else 0,
                "model": modello,
                "errore_codice": codice,
            },
        )
        await _registra(
            primary, user_id=user_id, owner_id=owner_id, outcome="error",
            cost_cents=riserva_cents if costo is None else costo,
            meta={**ctx.meta, "esito": "errore", "errore": codice,
                  "costo_ignoto": costo is None},
        )
        return "errore"
    except Exception as exc:
        logger.exception("partenariati: estrazione fallita (bando %s)", bando_id)
        if usage is not None:
            # Chiamata riuscita e pagata, guasto dopo: come ogni errore dopo
            # l'invio, costo = max(reale, riserva).
            costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens),
                        riserva_cents)
        elif inviata and not isinstance(exc, AiNotConfiguredError):
            costo = None
        else:
            costo = 0  # il modello non è stato chiamato
        await _concludi_sicuro(
            primary, bando_id, claim_token, "errore",
            {
                "cost_cents": costo,
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "model": modello if inviata else None,
                "errore_codice": "errore_interno",
            },
        )
        await _registra(
            primary, user_id=user_id, owner_id=owner_id, outcome="error",
            cost_cents=riserva_cents if costo is None else costo,
            meta={**ctx.meta, "esito": "errore", "costo_ignoto": costo is None},
        )
        return "errore"


async def _claim_perso(primary, ctx: _Contesto) -> str:
    """Heartbeat fallito: il claim è scaduto, oppure un altro ha ripreso il
    bando o il failsafe l'ha chiuso. Il modello NON si chiama. Se il claim è
    ancora nostro (scaduto ma non ripreso) si chiude qui come errore
    `interrotta` a costo 0: la riserva esce subito dal budget. Altrimenti
    l'ha già chiuso chi l'ha ripreso: prima della fase `analisi` la 0034
    chiude l'esecuzione a costo 0 (il modello non può essere stato chiamato)."""
    logger.warning("partenariati: claim perso prima dell'analisi (bando %s)", ctx.bando_id)
    await _concludi_sicuro(
        primary, ctx.bando_id, ctx.claim_token, "errore",
        {"cost_cents": 0, "input_tokens": 0, "output_tokens": 0, "model": None,
         "errore_codice": "interrotta"},
    )
    await _registra(
        primary, user_id=ctx.user_id, owner_id=ctx.owner_id, outcome="error", cost_cents=0,
        meta={**ctx.meta, "esito": "claim_perso"},
    )
    return "claim_perso"


async def _chiudi_su_cancellazione(
    primary, ctx: _Contesto, *, modello: str, usage, inviata: bool, peggiore: int
) -> None:
    """Chiusura best-effort di un'estrazione il cui task è stato cancellato,
    con un tempo massimo: allo spegnimento non si trattiene il processo."""
    if usage is not None:
        # Risposta arrivata e pagata: costo = max(reale, riserva), come gli
        # altri errori dopo l'invio.
        costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens), peggiore)
        esito, outcome = "errore", "error"
        dati = {"cost_cents": costo, "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens, "model": modello}
    elif inviata:
        # Chiamata forse partita e forse addebitata: come un timeout.
        costo, esito, outcome = peggiore, "timeout", "timeout_unknown"
        dati = {"cost_cents": costo, "model": modello}
    else:
        costo, esito, outcome = 0, "errore", "error"
        dati = {"cost_cents": 0, "input_tokens": 0, "output_tokens": 0, "model": None}
    try:
        async with asyncio.timeout(CHIUSURA_SU_CANCELLAZIONE_SECONDI):
            await _concludi_sicuro(
                primary, ctx.bando_id, ctx.claim_token, esito,
                {**dati, "errore_codice": "interrotta"},
            )
            await _registra(
                primary, user_id=ctx.user_id, owner_id=ctx.owner_id, outcome=outcome,
                cost_cents=costo, meta={**ctx.meta, "esito": "interrotta"},
            )
    except Exception:  # noqa: BLE001 — il failsafe chiuderà la riga
        logger.warning("partenariati: chiusura dopo la cancellazione non riuscita (bando %s)",
                       ctx.bando_id)

