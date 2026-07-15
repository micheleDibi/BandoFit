"""Orchestrazione dell'AI-check (compatibilità azienda ↔ bando).

Flusso: POST → guardie (feature attiva, titolare, dati aziendali, bando,
cooldown) → lock breve SOLO attorno a verifica-quota + insert della riga
pending (chiude la corsa sulla quota; la garanzia anti doppio-run è l'indice
unico parziale) → pipeline in background nel processo (asyncio.create_task):
estrazione (con CACHE per bando) → matching → scoring deterministico →
update condizionato su pending → registro consumi.

Protezioni di spesa come le altre chiamate a pagamento: mai retry su chiamate
potenzialmente addebitate, registro consumi su ogni esito, failsafe che chiude
come error le analisi pending da oltre 10 minuti (riavvii del processo).
"""

import asyncio
import logging
import math
import uuid
from datetime import date, datetime, timedelta, timezone

from postgrest.exceptions import APIError

from app.clients.anthropic_ai import AiCheckClient
from app.core.config import get_settings
from app.core.errors import (
    AiNotConfiguredError,
    AiQuotaExceededError,
    AiTimeoutError,
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.schemas.ai_check import (
    AiCheckOut,
    AiChecksResponse,
    AiQuotaOut,
    ExtractionResult,
)
from app.services import bandi_service, link_policy
from app.services.ai_check_prompts import (
    PROMPT_VERSION,
    SYSTEM_EXTRACT,
    SYSTEM_MATCH,
    build_bando_input,
    build_company_pack,
    build_matching_input,
    compute_content_hash,
)
from app.services.ai_check_scoring import facet_prechecks, score_report
from app.services.family_service import owner_and_editable
from app.services.openapi_mapping import build_dossier
from app.services.openapi_service import _acquire_lock, _release_lock, record_usage

logger = logging.getLogger("bandofit.ai_check")

# Il lock serve solo a serializzare verifica-quota → insert (poche letture
# PostgREST): NON è tenuto durante le chiamate LLM. Il TTL è comodo rispetto
# alla durata attesa (decine di ms): un TTL troppo stretto rischierebbe di
# scadere durante uno stallo del DB e di far rubare/cancellare il lock a
# un'operazione concorrente (import e AI-check condividono la stessa chiave).
AI_LOCK_TTL_SECONDS = 30
# Failsafe: un'analisi senza esito entro questo tempo viene chiusa come error
# (il task in-process può morire con un riavvio del container).
_STALE_AFTER = timedelta(minutes=10)

# Prezzi del modello in centesimi di dollaro per milione di token
# (claude-sonnet-5: $3 input / $15 output). Il costo è calcolato dagli
# usage reali e salvato su riga e registro consumi.
PRICE_INPUT_CENTS_PER_MTOK = 300
PRICE_OUTPUT_CENTS_PER_MTOK = 1500
# Stima prudente registrata quando il timeout lascia l'addebito ignoto.
TIMEOUT_COST_CENTS = 15

CHECK_SELECT = (
    "id,company_profile_id,user_id,family_parent_id,bando_id,bando_slug,"
    "bando_titolo,status,error_detail,esito,punteggio,tipo_punteggio,report,"
    "model,prompt_version,extraction_cached,input_tokens,output_tokens,"
    "cost_cents,created_at,ready_at"
)

# Riferimenti ai task in corso: senza, il garbage collector può cancellare
# un task fire-and-forget a metà esecuzione.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Avvia la pipeline in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def cost_cents(input_tokens: int, output_tokens: int) -> int:
    return math.ceil(
        (input_tokens * PRICE_INPUT_CENTS_PER_MTOK + output_tokens * PRICE_OUTPUT_CENTS_PER_MTOK)
        / 1_000_000
    )


def _to_out(row: dict, include_report: bool = False) -> AiCheckOut:
    # Scrub in lettura per i report STORICI: quelli generati prima del
    # filtro dei domini esclusi possono citare il concorrente nel testo
    # (i nuovi nascono puliti: il testo del bando è filtrato a monte).
    report = row.get("report") if include_report else None
    if report is not None:
        report = link_policy.scrub_text_mentions(report)
    return AiCheckOut(
        id=str(row["id"]),
        bando_id=row["bando_id"],
        bando_slug=row["bando_slug"],
        bando_titolo=row["bando_titolo"],
        status=row["status"],
        error_detail=row.get("error_detail"),
        esito=row.get("esito"),
        punteggio=row.get("punteggio"),
        tipo_punteggio=row.get("tipo_punteggio"),
        model=row.get("model"),
        extraction_cached=bool(row.get("extraction_cached")),
        created_at=str(row.get("created_at")),
        ready_at=str(row["ready_at"]) if row.get("ready_at") else None,
        report=report,
    )


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------- quota

async def _count(query) -> int:
    resp = await query.limit(1).execute()
    return resp.count or 0


async def get_quota(primary, owner_id: str) -> AiQuotaOut:
    """Quota del periodo di abbonamento attivo, contro il totale del piano.

    Il conteggio usa le RIGHE di ai_checks (`pending` + `ready`) nella
    finestra dell'abbonamento — non il registro consumi: la riga cambia stato
    con un solo update atomico (niente finestra di sotto-conteggio tra fine
    analisi e scrittura dell'evento) e la sua persistenza è transazionale col
    risultato (il ledger è best-effort e resta solo registro di spesa).
    Le analisi fallite (`error`) non consumano quota. Ogni generazione, anche
    la rigenerazione sullo stesso bando, consuma 1. Nota: la finestra segue
    l'abbonamento attivo (un cambio piano la fa ripartire — accettato in
    fase 1, senza pagamenti)."""
    sub_resp = (
        await primary.table("user_subscriptions")
        .select("data_inizio,data_scadenza,subscription_plans(ai_check)")
        .eq("user_id", str(owner_id))
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not sub_resp.data:
        return AiQuotaOut(totale=0, usati=0, rimanenti=0)
    sub = sub_resp.data[0]
    plan = sub.get("subscription_plans") or {}
    totale = int(plan.get("ai_check") or 0)
    inizio = str(sub.get("data_inizio") or "")
    scadenza = str(sub.get("data_scadenza") or "")

    used_query = (
        primary.table("ai_checks")
        .select("id", count="exact")
        .eq("family_parent_id", str(owner_id))
        .in_("status", ["pending", "ready"])
    )
    if inizio:
        used_query = used_query.gte("created_at", inizio)
    if scadenza:
        try:
            end_exclusive = (date.fromisoformat(scadenza) + timedelta(days=1)).isoformat()
            used_query = used_query.lt("created_at", end_exclusive)
        except ValueError:
            pass
    usati = await _count(used_query)

    return AiQuotaOut(
        totale=totale,
        usati=usati,
        rimanenti=max(0, totale - usati),
        periodo_inizio=inizio or None,
        periodo_fine=scadenza or None,
    )


# ----------------------------------------------------------------- richiesta

async def _company_context(primary, company_id: str | None) -> tuple[dict, dict | None, list[dict]]:
    """Profilo aziendale + dati certificati + persone dell'azienda attiva."""
    _no_company = BadRequestError(
        "Compila prima i dati aziendali (o usa «Importa da P.IVA» dalla pagina Azienda)"
    )
    if not company_id:
        raise _no_company
    company_resp = (
        await primary.table("company_profiles")
        .select("id,ragione_sociale,forma_giuridica,partita_iva,codice_fiscale,"
                "ateco_id,ateco_codice,ateco_descrizione,settore_id,settore_nome,"
                "regione_id,regione_nome,beneficiari,anno_fondazione,indirizzo,comune,"
                "provincia,cap,classe_dimensionale,numero_dipendenti,fascia_fatturato,"
                "pec,telefono,sito_web")
        .eq("id", str(company_id))
        .limit(1)
        .execute()
    )
    company = company_resp.data[0] if company_resp.data else None
    if company is None:
        raise _no_company

    data_resp = (
        await primary.table("company_data")
        .select("raw,derived")
        .eq("company_profile_id", company["id"])
        .limit(1)
        .execute()
    )
    company_data = data_resp.data[0] if data_resp.data else None

    people_resp = (
        await primary.table("company_people")
        .select("kind,nome,cognome,denominazione,ruoli,is_legale_rappresentante")
        .eq("company_profile_id", company["id"])
        .order("kind")
        .execute()
    )

    return company, company_data, people_resp.data or []


async def request_check(
    primary, secondary, ai: AiCheckClient, user: dict, active, bando_slug: str
) -> AiCheckOut:
    """Avvia un AI-check (consuma 1 quota del piano; costo API ~0,10 $)."""
    if not ai.enabled:
        raise AiNotConfiguredError()

    if not active.editable:
        raise ForbiddenError("L'AI-check lo avvia il titolare dell'azienda")
    owner_id = active.owner_id

    # Failsafe anche qui: un'analisi zombie (riavvio del container) non deve
    # bloccare il bando né gonfiare la quota fino alla prossima GET.
    await _close_stale(primary, owner_id)

    company, company_data, people = await _company_context(primary, active.company_id)
    if not any(company.get(f) for f in ("ateco_id", "settore_id", "regione_id")) and not company_data:
        raise BadRequestError(
            "Servono più dati aziendali per un'analisi utile: compila ATECO, settore o "
            "regione, oppure usa «Importa da P.IVA»"
        )

    bando = await bandi_service.fetch_bando_for_ai(secondary, bando_slug)

    settings = get_settings()
    cooldown = timedelta(minutes=settings.ai_check_cooldown_minutes)
    # Contano anche le pending: una POST che scavalca il flip pending→ready
    # dell'analisi precedente non deve bypassare il cooldown.
    latest_resp = (
        await primary.table("ai_checks")
        .select("created_at,status")
        .eq("company_profile_id", company["id"])
        .eq("bando_id", bando["id"])
        .in_("status", ["pending", "ready"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if latest_resp.data:
        latest_row = latest_resp.data[0]
        if latest_row.get("status") == "pending":
            raise AppError(
                409,
                "ai_check_in_progress",
                "C'è già un'analisi in corso per questo bando: attendi che venga completata",
            )
        last = _parse_ts(latest_row.get("created_at"))
        if last and datetime.now(timezone.utc) - last < cooldown:
            remaining = cooldown - (datetime.now(timezone.utc) - last)
            minutes = max(1, int(remaining.total_seconds() // 60) + 1)
            raise AppError(
                429,
                "ai_check_cooldown",
                f"Hai appena analizzato questo bando: riprova tra circa {minutes} minuti",
            )

    # Input costruiti PRIMA del lock e del task: la pipeline in background
    # riceve tutto in memoria e il lock resta brevissimo.
    bando_text, sections = build_bando_input(bando, bando.get("contenuto"))
    content_hash = compute_content_hash(bando, bando_text)
    raw = (company_data or {}).get("raw") or {}
    company_pack = build_company_pack(
        profile=user,
        company=company,
        dossier=build_dossier(raw) if raw else None,
        derived=(company_data or {}).get("derived"),
        people=people,
    )
    prechecks = facet_prechecks(bando, company, (company_data or {}).get("derived"))

    if not await _acquire_lock(primary, owner_id, AI_LOCK_TTL_SECONDS):
        raise AppError(
            409,
            "ai_check_in_progress",
            "Un'altra operazione è in corso per questa azienda: riprova tra qualche istante",
        )
    try:
        quota = await get_quota(primary, owner_id)
        if quota.rimanenti <= 0:
            if quota.totale <= 0:
                raise AiQuotaExceededError(
                    "Il tuo piano non include AI-check: passa a un piano superiore"
                )
            raise AiQuotaExceededError(
                "Hai esaurito gli AI-check del tuo piano per questo periodo"
            )
        try:
            insert = await primary.table("ai_checks").insert(
                {
                    "company_profile_id": company["id"],
                    "user_id": str(user["id"]),
                    "family_parent_id": str(owner_id),
                    "bando_id": bando["id"],
                    "bando_slug": bando["slug"],
                    "bando_titolo": bando.get("titolo_breve") or bando.get("titolo") or bando["slug"],
                    "status": "pending",
                    "model": ai.model,
                    "prompt_version": PROMPT_VERSION,
                }
            ).execute()
        except APIError as exc:
            if exc.code == "23505":
                # L'indice unico parziale respinge una seconda analisi in
                # corso per la stessa coppia azienda × bando.
                raise AppError(
                    409,
                    "ai_check_in_progress",
                    "C'è già un'analisi in corso per questo bando: attendi che venga completata",
                ) from exc
            raise  # altri guasti DB: semantica 502 del gestore generico
        row = insert.data[0]
    finally:
        await _release_lock(primary, owner_id)

    _spawn(
        _run_pipeline(
            primary,
            ai,
            check_id=str(row["id"]),
            user_id=str(user["id"]),
            owner_id=str(owner_id),
            bando=bando,
            bando_text=bando_text,
            sections=sections,
            content_hash=content_hash,
            company_pack=company_pack,
            prechecks=prechecks,
        )
    )

    # Best-effort e DOPO lo spawn: un guasto sull'audit non deve né far
    # fallire la richiesta né lasciare una riga pending orfana senza pipeline.
    try:
        await primary.table("audit_log").insert(
            {
                "actor_id": str(user["id"]),
                "action": "company.ai_check_requested",
                "target_user_id": str(owner_id),
                "family_parent_id": str(owner_id),
                "payload": {"bando_slug": bando["slug"], "bando_id": bando["id"]},
            }
        ).execute()
    except Exception:
        logger.exception("audit dell'ai-check non scrivibile")

    return _to_out(row)


# ----------------------------------------------------------------- pipeline

async def _get_extraction(
    primary, ai: AiCheckClient, bando: dict, bando_text: str, content_hash: str
) -> tuple[ExtractionResult, bool, int, int]:
    """Estrazione con cache per bando: (risultato, cache_hit, in_tok, out_tok).

    Scelta consapevole: nessun lock cross-azienda sulla cache — due prime
    analisi CONCORRENTI sullo stesso bando pagano entrambe lo stadio A
    (spesa doppia limitata, ~centesimi; l'upsert è last-write-wins con lo
    stesso contenuto). Serializzarle costerebbe più complessità del danno."""
    cache_resp = (
        await primary.table("bando_requirements")
        .select("extraction,content_hash,prompt_version")
        .eq("bando_id", bando["id"])
        .limit(1)
        .execute()
    )
    cached = cache_resp.data[0] if cache_resp.data else None
    if (
        cached
        and cached.get("content_hash") == content_hash
        and cached.get("prompt_version") == PROMPT_VERSION
    ):
        try:
            return ExtractionResult.model_validate(cached["extraction"]), True, 0, 0
        except Exception:
            logger.warning("cache estrazione non valida per bando %s: rigenero", bando["id"])

    extraction, usage = await ai.extract(SYSTEM_EXTRACT, bando_text)
    # Best-effort: l'estrazione (già pagata) è in memoria — perdere la
    # scrittura della cache non deve far fallire l'analisi né azzerare il
    # conteggio dei token già spesi nel registro.
    try:
        await primary.table("bando_requirements").upsert(
            {
                "bando_id": bando["id"],
                "bando_slug": bando["slug"],
                "content_hash": content_hash,
                "prompt_version": PROMPT_VERSION,
                "model": ai.model,
                "extraction": extraction.model_dump(),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
            on_conflict="bando_id",
        ).execute()
    except Exception:
        logger.exception("cache estrazione non scrivibile per bando %s", bando["id"])
    return extraction, False, usage.input_tokens, usage.output_tokens


async def _mark_error(primary, check_id: str, detail: str) -> None:
    """Best-effort: viene chiamata nei rami d'errore della pipeline — se
    fallisse anche lei (stesso incidente di rete), la scrittura del registro
    consumi che segue non deve saltare (e il failsafe chiuderà la riga)."""
    try:
        await primary.table("ai_checks").update(
            {"status": "error", "error_detail": detail}
        ).eq("id", check_id).eq("status", "pending").execute()
    except Exception:
        logger.exception("ai-check %s: impossibile marcare l'errore", check_id)


async def _run_pipeline(
    primary,
    ai: AiCheckClient,
    *,
    check_id: str,
    user_id: str,
    owner_id: str,
    bando: dict,
    bando_text: str,
    sections: dict[str, str],
    content_hash: str,
    company_pack: str,
    prechecks: dict,
) -> None:
    """Estrazione → matching → scoring → persistenza. Non solleva MAI fuori:
    ogni esito viene scritto sulla riga e nel registro consumi."""
    input_tokens = output_tokens = 0
    meta_base = {"bando_slug": bando["slug"], "check_id": check_id, "model": ai.model}
    try:
        extraction, cache_hit, in_a, out_a = await _get_extraction(
            primary, ai, bando, bando_text, content_hash
        )
        input_tokens += in_a
        output_tokens += out_a

        matching_input = build_matching_input(
            extraction.model_dump(), prechecks, company_pack
        )
        matching, usage_b = await ai.match(SYSTEM_MATCH, matching_input)
        input_tokens += usage_b.input_tokens
        output_tokens += usage_b.output_tokens

        report = score_report(
            extraction,
            matching,
            prechecks,
            sections,
            meta={
                "model": ai.model,
                "prompt_version": PROMPT_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "bando_hash": content_hash,
                "extraction_cached": cache_hit,
            },
        )
        cost = cost_cents(input_tokens, output_tokens)

        update = (
            await primary.table("ai_checks")
            .update(
                {
                    "status": "ready",
                    "esito": report["esito_ammissibilita"],
                    "punteggio": report["punteggio_totale"],
                    "tipo_punteggio": report["tipo_punteggio"],
                    "report": report,
                    "extraction_cached": cache_hit,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_cents": cost,
                    "ready_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", check_id)
            .eq("status", "pending")  # solo un vincitore (failsafe compreso)
            .execute()
        )
        outcome = "success" if update.data else "error"
        if outcome == "error":
            # Il failsafe ha chiuso la riga prima di noi: il report è perso ma
            # la quota non va consumata (conta solo success).
            logger.error("ai-check %s completato su riga non più pending", check_id)
        await record_usage(
            primary,
            user_id=user_id,
            family_parent_id=owner_id,
            service="ai_check",
            outcome=outcome,
            cost_cents=cost,
            meta={
                **meta_base,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "extraction_cached": cache_hit,
            },
            provider="anthropic",
        )
    except AiTimeoutError:
        await _mark_error(primary, check_id, "L'analisi ha impiegato troppo tempo: riprova")
        await record_usage(
            primary,
            user_id=user_id,
            family_parent_id=owner_id,
            service="ai_check",
            outcome="timeout_unknown",
            cost_cents=max(TIMEOUT_COST_CENTS, cost_cents(input_tokens, output_tokens)),
            meta=meta_base,
            provider="anthropic",
        )
    except Exception:
        logger.exception("ai-check %s fallito", check_id)
        await _mark_error(primary, check_id, "Analisi non riuscita: riprova più tardi")
        await record_usage(
            primary,
            user_id=user_id,
            family_parent_id=owner_id,
            service="ai_check",
            outcome="error",
            # Se il guasto è arrivato dopo una chiamata riuscita, il costo è
            # comunque stato speso: va registrato.
            cost_cents=cost_cents(input_tokens, output_tokens),
            meta=meta_base,
            provider="anthropic",
        )


# ------------------------------------------------------------------- lettura

async def _close_stale(primary, owner_id: str) -> None:
    """Failsafe al poll-on-read: analisi pending da oltre 10 minuti chiuse
    come error (il task in-process può essere morto con un riavvio)."""
    threshold = (datetime.now(timezone.utc) - _STALE_AFTER).isoformat()
    try:
        await primary.table("ai_checks").update(
            {"status": "error", "error_detail": "Analisi interrotta: riprova"}
        ).eq("family_parent_id", str(owner_id)).eq("status", "pending").lt(
            "created_at", threshold
        ).execute()
    except Exception:
        logger.exception("chiusura analisi stale fallita")


async def list_checks(
    primary,
    active,
    bando_slug: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AiChecksResponse:
    """Storico AI-check dell'azienda attiva. Con `bando_slug` include i report
    completi (storico per bando, il primo è il più recente)."""
    # Parametro vuoto = nessun filtro: va normalizzato PRIMA, così filtro e
    # inclusione dei report restano coerenti (la lista globale è sintetica).
    bando_slug = (bando_slug or "").strip() or None
    owner_id, editable = active.owner_id, active.editable
    await _close_stale(primary, owner_id)

    query = (
        primary.table("ai_checks")
        .select(CHECK_SELECT, count="exact")
        .eq("family_parent_id", str(owner_id))
    )
    # La quota resta condivisa dall'azienda (family_parent_id); lo STORICO è
    # però dell'azienda attiva (per l'Advisor multi-azienda).
    if active.company_id is not None:
        query = query.eq("company_profile_id", active.company_id)
    if bando_slug:
        query = query.eq("bando_slug", bando_slug)
    offset = (page - 1) * page_size
    resp = (
        await query.order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    quota = await get_quota(primary, owner_id)
    include_report = bando_slug is not None
    return AiChecksResponse(
        editable=editable,
        quota=quota,
        items=[_to_out(row, include_report=include_report) for row in (resp.data or [])],
        total=resp.count or 0,
    )


async def get_check(primary, active, check_id: str) -> AiCheckOut:
    """Singolo report dell'azienda attiva, completo (anche per i figli attivi)."""
    try:
        # Forma CANONICA: Python accetta anche urn:uuid:/graffe che Postgres
        # rifiuterebbe con 22P02 (→ 502) — la query usa l'UUID normalizzato.
        normalized = str(uuid.UUID(str(check_id)))
    except ValueError:
        # Un id malformato è, per il chiamante, un report inesistente.
        raise NotFoundError("Report non trovato") from None
    owner_id = active.owner_id
    await _close_stale(primary, owner_id)
    query = (
        primary.table("ai_checks")
        .select(CHECK_SELECT)
        .eq("id", normalized)
        .eq("family_parent_id", str(owner_id))  # mai report di altre aziende
    )
    if active.company_id is not None:
        query = query.eq("company_profile_id", active.company_id)
    resp = await query.limit(1).execute()
    if not resp.data:
        raise NotFoundError("Report non trovato")
    return _to_out(resp.data[0], include_report=True)


async def quota_for(primary, user: dict) -> AiQuotaOut:
    owner_id, _ = await owner_and_editable(primary, user)
    # Anche qui il failsafe: una pending zombie non deve gonfiare la quota.
    await _close_stale(primary, owner_id)
    return await get_quota(primary, owner_id)
