"""Import dei dati aziendali certificati (openapi.it IT-full) e dossier.

Regole di spesa: ogni import costa credito reale, quindi il flusso è protetto
tre volte — validazione locale gratuita della P.IVA, cooldown sull'ultimo
recupero, lock anti-concorrenza per famiglia. Ogni chiamata a pagamento viene
annotata in api_usage_events QUALUNQUE sia l'esito.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from app.clients.openapi import OpenapiClient, OpenapiInvalidIdError
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    OpenapiNotConfiguredError,
    OpenapiTimeoutError,
    OpenapiUpstreamError,
)
from app.schemas.openapi_data import (
    AutofillOut,
    DossierResponse,
    ImportPreview,
    ImportPreviewAzienda,
    ImportResult,
    PersonOut,
    SuggestionsOut,
)
from app.services import company_service, family_service, lookup_service
from app.services.codice_fiscale import is_valid_cf, normalize_cf
from app.services.openapi_mapping import (
    build_autofill,
    build_derived,
    build_dossier,
    extract_people,
    stato_impresa,
    validate_partita_iva,
)

logger = logging.getLogger("bandofit.openapi")

COST_IT_FULL_CENTS = 30
COST_VERIFICA_CF_CENTS = 5
# Più lungo della deadline complessiva del client (240s, vedi clients/openapi.py):
# il lock NON deve scadere mentre l'import è ancora in corso, o un secondo
# import concorrente pagherebbe una seconda chiamata.
LOCK_TTL_SECONDS = 300
# La conferma non aspetta nessuna rete esterna: qualche scrittura PostgREST.
CONFIRM_LOCK_TTL_SECONDS = 30
VERIFY_LOCK_TTL_SECONDS = 30
# Cooldown (in-memory) tra verifiche CF A PAGAMENTO dello stesso utente:
# il lock copre la concorrenza, questo evita il drenaggio di credito con
# tentativi in serie.
VERIFY_COOLDOWN_SECONDS = 30.0
_last_paid_verify: dict[str, float] = {}

PEOPLE_SELECT = (
    "kind,nome,cognome,denominazione,codice_fiscale,data_nascita,luogo_nascita,"
    "genere,ruoli,is_legale_rappresentante,quota_percentuale,data_inizio_carica"
)


async def record_usage(
    primary,
    *,
    user_id: str,
    family_parent_id: str,
    service: str,
    outcome: str,
    cost_cents: int,
    meta: dict | None = None,
    provider: str = "openapi",
) -> None:
    """Annota una chiamata a pagamento. Non solleva MAI: un registro non
    scrivibile non deve mandare in errore un import riuscito."""
    try:
        await primary.table("api_usage_events").insert(
            {
                "user_id": str(user_id),
                "family_parent_id": str(family_parent_id),
                "provider": provider,
                "service": service,
                "outcome": outcome,
                "cost_cents": cost_cents,
                "request_meta": meta or {},
            }
        ).execute()
    except Exception:
        logger.exception("registro consumi non scrivibile (service=%s)", service)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def _fetch_company_row(primary, parent_id: str) -> dict | None:
    resp = (
        await primary.table("company_profiles")
        .select("id," + company_service.COMPANY_SELECT)
        .eq("parent_id", str(parent_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _fetch_company_row_by_id(primary, company_id: str) -> dict | None:
    resp = (
        await primary.table("company_profiles")
        .select("id," + company_service.COMPANY_SELECT)
        .eq("id", str(company_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _fetch_company_data(primary, company_profile_id: str) -> dict | None:
    resp = (
        await primary.table("company_data")
        .select("raw,derived,piva_fetched,sandbox,fetch_count,fetched_at")
        .eq("company_profile_id", str(company_profile_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _fetch_draft(primary, parent_id: str) -> dict | None:
    """Anteprima già pagata e non ancora scaduta. Un draft scaduto è come non
    esistesse: il payload va richiesto (e ripagato)."""
    resp = (
        await primary.table("company_import_drafts")
        .select("partita_iva,raw,sandbox,fetched_at,expires_at")
        .eq("parent_id", str(parent_id))
        .gt("expires_at", datetime.now(timezone.utc).isoformat())
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _store_draft(
    primary, parent_id: str, piva: str, payload: dict, *, sandbox: bool
) -> tuple[str, str]:
    """Mette in staging il payload appena pagato. Una riga per titolare:
    una nuova anteprima sostituisce la precedente. Ritorna (fetched_at, expires_at)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.company_import_draft_ttl_minutes)
    await primary.table("company_import_drafts").upsert(
        {
            "parent_id": str(parent_id),
            "partita_iva": piva,
            "raw": payload,
            "sandbox": sandbox,
            "fetched_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        on_conflict="parent_id",
    ).execute()
    return now.isoformat(), expires_at.isoformat()


async def _delete_draft(primary, parent_id: str) -> None:
    await primary.table("company_import_drafts").delete().eq(
        "parent_id", str(parent_id)
    ).execute()


async def _lock_remaining_minutes(primary, parent_id: str) -> int:
    """Minuti che restano al lock. Dopo un timeout del provider il lock viene
    lasciato scadere di proposito (fino a 5 minuti): dire «attendi qualche
    istante» sarebbe una presa in giro."""
    try:
        resp = (
            await primary.table("company_import_locks")
            .select("expires_at")
            .eq("parent_id", str(parent_id))
            .limit(1)
            .execute()
        )
        expires = _parse_ts(resp.data[0]["expires_at"]) if resp.data else None
    except Exception:  # pragma: no cover — il messaggio è accessorio
        expires = None
    if not expires:
        return 1
    remaining = expires - datetime.now(timezone.utc)
    return max(1, int(remaining.total_seconds() // 60) + 1)


async def _acquire_lock(primary, parent_id: str, ttl_seconds: int = LOCK_TTL_SECONDS) -> bool:
    resp = await primary.rpc(
        "fn_acquire_import_lock",
        {"p_parent_id": str(parent_id), "p_ttl_seconds": ttl_seconds},
    ).execute()
    return bool(resp.data)


async def _release_lock(primary, parent_id: str) -> None:
    try:
        await primary.rpc(
            "fn_release_import_lock", {"p_parent_id": str(parent_id)}
        ).execute()
    except Exception:
        logger.exception("release del lock di import fallita (scadrà da solo)")


def _resolve_piva(partita_iva: str | None, company_row: dict | None) -> str:
    piva = partita_iva or (company_row or {}).get("partita_iva")
    if not piva:
        raise BadRequestError("Inserisci la partita IVA da importare")
    if not validate_partita_iva(piva):
        raise BadRequestError("La partita IVA non è valida: controlla le 11 cifre")
    return piva


def _build_preview(
    payload: dict,
    company_row: dict | None,
    lookups,
    *,
    piva: str,
    fetched_at: str,
    expires_at: str,
    sandbox: bool,
    reused: bool,
) -> ImportPreview:
    """Anteprima di sola lettura. `build_autofill` è puro: lo chiamiamo qui e
    SCARTIAMO `updates` — così l'anteprima mostra esattamente ciò che la
    conferma scriverà, senza una seconda implementazione che possa divergere."""
    dossier = build_dossier(payload)
    anagrafica = dossier.get("anagrafica") or {}
    attivita = dossier.get("attivita") or {}
    sede = dossier.get("sede") or {}
    ateco = attivita.get("ateco") or {}

    _updates, applied, conflicts, suggestions = build_autofill(payload, company_row, lookups)

    people = extract_people(payload)
    rappresentante = next(
        (
            " ".join(filter(None, [p.get("nome"), p.get("cognome")])) or p.get("denominazione")
            for p in people
            if p.get("is_legale_rappresentante")
        ),
        None,
    )

    indirizzo = ", ".join(
        filter(None, [sede.get("indirizzo"), sede.get("cap"), sede.get("comune")])
    )
    if sede.get("provincia"):
        indirizzo = f"{indirizzo} ({sede['provincia']})" if indirizzo else sede["provincia"]

    codice = ateco.get("codice")
    descrizione = ateco.get("descrizione")

    return ImportPreview(
        azienda=ImportPreviewAzienda(
            partita_iva=piva,
            ragione_sociale=anagrafica.get("denominazione"),
            codice_fiscale=anagrafica.get("codice_fiscale"),
            forma_giuridica=anagrafica.get("forma_giuridica_dettaglio")
            or anagrafica.get("forma_giuridica"),
            stato_impresa=stato_impresa(payload),
            sede=indirizzo or None,
            regione=sede.get("regione"),
            ateco=f"{codice} — {descrizione}" if codice and descrizione else codice,
            legale_rappresentante=rappresentante,
            numero_persone=len(people),
        ),
        autofill=AutofillOut(applied=applied, conflicts=conflicts),
        suggestions=SuggestionsOut(**suggestions),
        fetched_at=fetched_at,
        draft_expires_at=expires_at,
        reused=reused,
        sandbox=sandbox,
    )


async def preview_import(
    primary, secondary, openapi: OpenapiClient, active, partita_iva: str | None
) -> ImportPreview:
    """Fase 1: recupera IT-full (A PAGAMENTO) e mostra cosa si sta per importare.

    NON scrive nulla sui dati aziendali. Il payload pagato finisce in staging
    (`company_import_drafts`) e la conferma lo consuma senza ripagarlo. Opera
    sull'azienda ATTIVA (multi-azienda); il lock/draft restano per owner."""
    if not openapi.enabled:
        raise OpenapiNotConfiguredError()

    if not active.editable:
        raise ForbiddenError("I dati aziendali li gestisce il titolare dell'azienda")
    parent_id = active.owner_id
    company_row = (
        await _fetch_company_row_by_id(primary, active.company_id)
        if active.company_id
        else None
    )
    piva = _resolve_piva(partita_iva, company_row)

    settings = get_settings()
    lookups = await lookup_service.get_lookups(secondary)

    # Anteprima già pagata per la STESSA azienda: si riusa, gratis e senza
    # cooldown. È ciò che rende indolore un «annulla» seguito da un ripensamento.
    draft = await _fetch_draft(primary, parent_id)
    if draft and draft["partita_iva"] == piva:
        return _build_preview(
            draft["raw"], company_row, lookups,
            piva=piva,
            fetched_at=draft["fetched_at"],
            expires_at=draft["expires_at"],
            sandbox=draft["sandbox"],
            reused=True,
        )

    # Cooldown: l'import costa, un doppio click non deve pagare due volte.
    # Vale sull'ultimo fetch PAGATO, che può essere un import confermato
    # (company_data) o un'anteprima di un'altra P.IVA rimasta in staging —
    # altrimenti si drenerebbe credito cambiando P.IVA a ogni tentativo.
    existing_data = (
        await _fetch_company_data(primary, company_row["id"]) if company_row else None
    )
    timestamps = [
        _parse_ts((existing_data or {}).get("fetched_at")),
        _parse_ts((draft or {}).get("fetched_at")),
    ]
    last = max((t for t in timestamps if t), default=None)
    cooldown = timedelta(minutes=settings.company_import_cooldown_minutes)
    if last and datetime.now(timezone.utc) - last < cooldown:
        remaining = cooldown - (datetime.now(timezone.utc) - last)
        minutes = max(1, int(remaining.total_seconds() // 60) + 1)
        raise AppError(
            409,
            "import_cooldown",
            f"Dati aziendali già aggiornati di recente: riprova tra circa {minutes} minuti",
        )

    cost = 0 if openapi.sandbox else COST_IT_FULL_CENTS

    if not await _acquire_lock(primary, parent_id):
        minutes = await _lock_remaining_minutes(primary, parent_id)
        raise AppError(
            409,
            "import_in_progress",
            "Un recupero dati è ancora in corso, o si è appena interrotto. Per evitare "
            f"un doppio addebito riprova tra circa {minutes} minuti",
        )

    try:
        payload = await openapi.it_full(piva)
    except OpenapiInvalidIdError:
        await record_usage(
            primary, user_id=parent_id, family_parent_id=parent_id,
            service="IT-full", outcome="error", cost_cents=0, meta={"piva": piva},
        )
        await _release_lock(primary, parent_id)
        raise NotFoundError("Partita IVA non trovata nel Registro Imprese") from None
    except OpenapiTimeoutError:
        # Esito (e addebito) ignoto: NESSUN retry automatico, il lock scade da
        # solo così un retry immediato dell'utente non paga due volte al buio.
        await record_usage(
            primary, user_id=parent_id, family_parent_id=parent_id,
            service="IT-full", outcome="timeout_unknown", cost_cents=cost,
            meta={"piva": piva},
        )
        raise
    except AppError:
        await record_usage(
            primary, user_id=parent_id, family_parent_id=parent_id,
            service="IT-full", outcome="error", cost_cents=0, meta={"piva": piva},
        )
        await _release_lock(primary, parent_id)
        raise

    try:
        # Guardia: la risposta deve riguardare l'azienda richiesta (IT-full
        # accetta P.IVA o CF: confrontiamo con entrambi). Un payload che non
        # combacia non entra MAI in staging.
        returned_ids = {
            str((payload.get("companyDetails") or {}).get("vatCode") or ""),
            str((payload.get("companyDetails") or {}).get("taxCode") or ""),
        }
        if piva not in returned_ids:
            await record_usage(
                primary, user_id=parent_id, family_parent_id=parent_id,
                service="IT-full", outcome="success", cost_cents=cost,
                meta={"piva": piva, "mismatch": True},
            )
            logger.error("openapi: risposta per id diversi da %s: %s", piva, returned_ids)
            raise OpenapiUpstreamError(
                "La risposta del provider non corrisponde alla partita IVA richiesta"
            )

        await record_usage(
            primary, user_id=parent_id, family_parent_id=parent_id,
            service="IT-full", outcome="success", cost_cents=cost, meta={"piva": piva},
        )
        fetched_at, expires_at = await _store_draft(
            primary, parent_id, piva, payload, sandbox=openapi.sandbox
        )
    finally:
        await _release_lock(primary, parent_id)

    return _build_preview(
        payload, company_row, lookups,
        piva=piva,
        fetched_at=fetched_at,
        expires_at=expires_at,
        sandbox=openapi.sandbox,
        reused=False,
    )


async def confirm_import(
    primary, secondary, active, partita_iva: str | None
) -> ImportResult:
    """Fase 2: scrive i dati dell'anteprima sull'azienda ATTIVA. NON chiama il
    provider, quindi non costa nulla e non passa dal cooldown (che protegge il
    fetch, non la scrittura). Il draft viene consumato: una seconda conferma
    non trova nulla."""
    if not active.editable:
        raise ForbiddenError("I dati aziendali li gestisce il titolare dell'azienda")
    parent_id = active.owner_id

    draft = await _fetch_draft(primary, parent_id)
    if draft is None:
        raise AppError(
            409,
            "draft_not_found",
            "L'anteprima è scaduta: riavvia l'importazione dei dati",
        )
    if partita_iva and partita_iva != draft["partita_iva"]:
        raise AppError(
            409,
            "draft_mismatch",
            "L'anteprima si riferisce a un'altra partita IVA: riavvia l'importazione",
        )

    # Serializza due conferme concorrenti. TTL breve: qui non si aspetta nessuna
    # rete esterna, solo qualche scrittura PostgREST.
    if not await _acquire_lock(primary, parent_id, CONFIRM_LOCK_TTL_SECONDS):
        raise AppError(
            409,
            "import_in_progress",
            "Un'importazione è già in corso per questa azienda: attendi qualche istante",
        )

    try:
        result = await _persist_import(
            primary, secondary, active,
            piva=draft["partita_iva"],
            payload=draft["raw"],
            sandbox=draft["sandbox"],
        )
        await _delete_draft(primary, parent_id)
    finally:
        await _release_lock(primary, parent_id)

    return result


async def _persist_import(
    primary, secondary, active, *, piva: str, payload: dict, sandbox: bool
) -> ImportResult:
    """Persiste raw + derivati + persone e compila i campi aziendali VUOTI
    (mai sovrascrivere i valori dell'utente) sull'azienda ATTIVA. Nessuna
    chiamata esterna."""
    parent_id = active.owner_id
    company_row = (
        await _fetch_company_row_by_id(primary, active.company_id)
        if active.company_id
        else None
    )
    existing_data = (
        await _fetch_company_data(primary, company_row["id"]) if company_row else None
    )
    lookups = await lookup_service.get_lookups(secondary)

    # Il profilo aziendale deve esistere per agganciare i dati certificati.
    # Bootstrap (owner senza azienda): si crea qui la prima azienda. Con più
    # aziende si scrive sempre per `id`, mai per `parent_id` (non più univoco).
    if company_row is None:
        ragione = (payload.get("companyDetails") or {}).get("companyName") or "—"
        ins = await primary.table("company_profiles").insert(
            {"parent_id": parent_id, "ragione_sociale": ragione, "partita_iva": piva}
        ).execute()
        company_id = ins.data[0]["id"] if ins.data else None
        company_row = await _fetch_company_row_by_id(primary, company_id) if company_id else None
        if company_row is None:  # pragma: no cover — solo per robustezza
            raise OpenapiUpstreamError()
    company_id = company_row["id"]

    updates, applied, conflicts, suggestions = build_autofill(payload, company_row, lookups)
    if updates:
        await primary.table("company_profiles").update(updates).eq(
            "id", company_id
        ).execute()

    derived = build_derived(payload, lookups)
    fetched_at = datetime.now(timezone.utc).isoformat()
    await primary.table("company_data").upsert(
        {
            "company_profile_id": company_id,
            "provider": "openapi.it",
            "endpoint": "IT-full",
            "piva_fetched": piva,
            "sandbox": sandbox,
            "raw": payload,
            "derived": derived,
            "denominazione": (payload.get("companyDetails") or {}).get("companyName"),
            "stato_impresa": stato_impresa(payload),
            "fetch_count": (existing_data or {}).get("fetch_count", 0) + 1,
            "fetched_at": fetched_at,
        },
        on_conflict="company_profile_id",
    ).execute()

    # L'import è l'azione che ABILITA il punteggio di compatibilità (ateco +
    # regione + regioni_ids): la cache (per id azienda) va scaduta subito, o il
    # badge non comparirebbe fino al TTL.
    from app.services.compatibility import invalidate_company_facets  # import locale: evita cicli

    invalidate_company_facets(company_id)

    people = extract_people(payload)
    await primary.table("company_people").delete().eq(
        "company_profile_id", company_id
    ).execute()
    if people:
        await primary.table("company_people").insert(
            [{"company_profile_id": company_id, **person} for person in people]
        ).execute()

    await primary.table("audit_log").insert(
        {
            "actor_id": parent_id,
            "action": "company.imported",
            "target_user_id": parent_id,
            "family_parent_id": parent_id,
            "payload": {
                "company_profile_id": company_id,
                "piva": piva,
                "campi_compilati": applied,
                "sandbox": sandbox,
            },
        }
    ).execute()

    company = await company_service.company_response_for_id(primary, company_id)
    return ImportResult(
        company=company,
        dossier=build_dossier(payload),
        people=[PersonOut(**{k: v for k, v in p.items() if k != "raw"}) for p in people],
        autofill=AutofillOut(applied=applied, conflicts=conflicts),
        suggestions=SuggestionsOut(**suggestions),
        fetched_at=fetched_at,
        sandbox=sandbox,
    )


def _mask_cf(cf: str) -> str:
    """CF mascherato per i log/registri: mai il dato personale in chiaro."""
    return cf[:6] + "*" * 7 + cf[13:] if len(cf) == 16 else "***"


async def verify_cf(primary, openapi: OpenapiClient, user: dict, codice_fiscale: str) -> dict:
    """Verifica il CF personale all'Anagrafe Tributaria (A PAGAMENTO, ~0,05 €).

    La validazione strutturale locale è gratuita e blocca gli input malformati
    prima di spendere. Idempotente: lo stesso CF già verificato non ripaga."""
    cf = normalize_cf(codice_fiscale)
    if not is_valid_cf(cf):
        raise AppError(400, "cf_invalid", "Il codice fiscale non è formalmente valido")

    profile_resp = (
        await primary.table("profiles")
        .select("codice_fiscale,cf_verified_at")
        .eq("id", str(user["id"]))
        .limit(1)
        .execute()
    )
    current = profile_resp.data[0] if profile_resp.data else {}
    if current.get("codice_fiscale") == cf and current.get("cf_verified_at"):
        return {"codice_fiscale": cf, "cf_verified_at": current["cf_verified_at"]}

    if not openapi.enabled:
        raise OpenapiNotConfiguredError()

    user_id = str(user["id"])

    # Cooldown tra tentativi A PAGAMENTO in serie (il lock sotto copre la
    # concorrenza): protegge il credito da spam di verifiche.
    last_paid = _last_paid_verify.get(user_id)
    if last_paid is not None and time.monotonic() - last_paid < VERIFY_COOLDOWN_SECONDS:
        raise AppError(
            429,
            "verify_cooldown",
            "Hai appena richiesto una verifica: attendi qualche secondo e riprova",
        )

    membership = await family_service.get_membership(primary, user["id"])
    family_parent_id = (
        str(membership["parent_id"])
        if membership and membership["status"] == "active"
        else user_id
    )
    cost = 0 if openapi.sandbox else COST_VERIFICA_CF_CENTS
    meta = {"cf": _mask_cf(cf)}

    # Stesso principio dell'import: la chiamata a pagamento avviene tra
    # statement PostgREST, serve un lock esplicito contro il doppio addebito
    # (doppio click, due tab).
    if not await _acquire_lock(primary, user_id, VERIFY_LOCK_TTL_SECONDS):
        raise AppError(
            409,
            "verify_in_progress",
            "Una verifica è già in corso: attendi qualche istante",
        )

    try:
        valid = await openapi.verifica_cf(cf)
        _last_paid_verify[user_id] = time.monotonic()
    except OpenapiInvalidIdError:
        await record_usage(
            primary, user_id=user_id, family_parent_id=family_parent_id,
            service="IT-verifica_cf", outcome="error", cost_cents=0, meta=meta,
        )
        await _release_lock(primary, user_id)
        raise AppError(400, "cf_invalid", "Il codice fiscale non è formalmente valido") from None
    except OpenapiTimeoutError:
        # Esito (e addebito) ignoto: cooldown attivo e lock lasciato scadere.
        _last_paid_verify[user_id] = time.monotonic()
        await record_usage(
            primary, user_id=user_id, family_parent_id=family_parent_id,
            service="IT-verifica_cf", outcome="timeout_unknown", cost_cents=cost, meta=meta,
        )
        raise
    except AppError:
        await record_usage(
            primary, user_id=user_id, family_parent_id=family_parent_id,
            service="IT-verifica_cf", outcome="error", cost_cents=0, meta=meta,
        )
        await _release_lock(primary, user_id)
        raise

    try:
        await record_usage(
            primary, user_id=user_id, family_parent_id=family_parent_id,
            service="IT-verifica_cf", outcome="success", cost_cents=cost,
            meta={**meta, "valid": valid},
        )

        if valid:
            verified_at = datetime.now(timezone.utc).isoformat()
            # CF + marca temporale nello STESSO update: il trigger DB rispetta
            # il valore esplicito di cf_verified_at.
            await primary.table("profiles").update(
                {"codice_fiscale": cf, "cf_verified_at": verified_at}
            ).eq("id", user_id).execute()
            return {"codice_fiscale": cf, "cf_verified_at": verified_at}

        # CF formalmente corretto ma non registrato: lo salviamo SOLO se
        # l'utente non ha già un CF verificato — una verifica fallita non deve
        # mai cancellare un dato buono (il trigger DB azzererebbe la marca).
        if current.get("codice_fiscale") != cf and not current.get("cf_verified_at"):
            await primary.table("profiles").update({"codice_fiscale": cf}).eq(
                "id", user_id
            ).execute()
        raise AppError(
            400,
            "cf_not_valid",
            "Il codice fiscale non risulta registrato all'Anagrafe Tributaria",
        )
    finally:
        await _release_lock(primary, user_id)


async def _dossier_from_company_row(
    primary, company_row: dict | None, editable: bool
) -> DossierResponse:
    if company_row is None:
        return DossierResponse(editable=editable, imported=False)

    data = await _fetch_company_data(primary, company_row["id"])
    if data is None:
        return DossierResponse(editable=editable, imported=False)

    people_resp = (
        await primary.table("company_people")
        .select(PEOPLE_SELECT)
        .eq("company_profile_id", company_row["id"])
        .order("kind")
        .execute()
    )
    return DossierResponse(
        editable=editable,
        imported=True,
        fetched_at=data.get("fetched_at"),
        sandbox=data.get("sandbox"),
        dossier=build_dossier(data.get("raw") or {}),
        people=[PersonOut(**p) for p in (people_resp.data or [])],
        derived=data.get("derived") or {},
    )


async def get_dossier(primary, active) -> DossierResponse:
    """Dossier certificato dell'azienda attiva: proprio per il titolare, della
    famiglia (sola lettura) per un figlio attivo — `editable` dal resolver."""
    company_row = (
        await _fetch_company_row_by_id(primary, active.company_id)
        if active.company_id
        else None
    )
    return await _dossier_from_company_row(primary, company_row, active.editable)


async def get_dossier_for_owner(
    primary, owner_id: str, *, editable: bool = False
) -> DossierResponse:
    """Dossier del titolare indicato, SENZA regole di visibilità: il chiamante
    ha già autorizzato l'accesso (assegnazione, con audit, nel flusso
    consulenze)."""
    return await _dossier_from_company_row(
        primary, await _fetch_company_row(primary, owner_id), editable
    )
