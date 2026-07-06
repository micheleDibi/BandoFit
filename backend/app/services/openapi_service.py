"""Import dei dati aziendali certificati (openapi.it IT-full) e dossier.

Regole di spesa: ogni import costa credito reale, quindi il flusso è protetto
tre volte — validazione locale gratuita della P.IVA, cooldown sull'ultimo
recupero, lock anti-concorrenza per famiglia. Ogni chiamata a pagamento viene
annotata in api_usage_events QUALUNQUE sia l'esito.
"""

import logging
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
    ImportResult,
    PersonOut,
    SuggestionsOut,
)
from app.services import company_service, family_service, lookup_service
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
LOCK_TTL_SECONDS = 120

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
) -> None:
    """Annota una chiamata a pagamento. Non solleva MAI: un registro non
    scrivibile non deve mandare in errore un import riuscito."""
    try:
        await primary.table("api_usage_events").insert(
            {
                "user_id": str(user_id),
                "family_parent_id": str(family_parent_id),
                "provider": "openapi",
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


async def _fetch_company_data(primary, company_profile_id: str) -> dict | None:
    resp = (
        await primary.table("company_data")
        .select("raw,derived,piva_fetched,sandbox,fetch_count,fetched_at")
        .eq("company_profile_id", str(company_profile_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _acquire_lock(primary, parent_id: str) -> bool:
    resp = await primary.rpc(
        "fn_acquire_import_lock",
        {"p_parent_id": str(parent_id), "p_ttl_seconds": LOCK_TTL_SECONDS},
    ).execute()
    return bool(resp.data)


async def _release_lock(primary, parent_id: str) -> None:
    try:
        await primary.rpc(
            "fn_release_import_lock", {"p_parent_id": str(parent_id)}
        ).execute()
    except Exception:
        logger.exception("release del lock di import fallita (scadrà da solo)")


async def import_company(
    primary, secondary, openapi: OpenapiClient, user: dict, partita_iva: str | None
) -> ImportResult:
    """Recupera IT-full, persiste raw+derivati+persone e compila i campi
    aziendali VUOTI (mai sovrascrivere i valori dell'utente)."""
    if not openapi.enabled:
        raise OpenapiNotConfiguredError()

    # Stessa regola di scrittura dei dati aziendali: un figlio ATTIVO eredita.
    membership = await family_service.get_membership(primary, user["id"])
    if membership and membership["status"] == "active":
        raise ForbiddenError("I dati aziendali li gestisce il titolare dell'azienda")

    parent_id = str(user["id"])
    company_row = await _fetch_company_row(primary, parent_id)

    piva = partita_iva or (company_row or {}).get("partita_iva")
    if not piva:
        raise BadRequestError("Inserisci la partita IVA da importare")
    if not validate_partita_iva(piva):
        raise BadRequestError("La partita IVA non è valida: controlla le 11 cifre")

    settings = get_settings()
    cost = 0 if openapi.sandbox else COST_IT_FULL_CENTS

    # Cooldown: l'import costa, un doppio click non deve pagare due volte.
    existing_data = (
        await _fetch_company_data(primary, company_row["id"]) if company_row else None
    )
    if existing_data:
        last = _parse_ts(existing_data.get("fetched_at"))
        cooldown = timedelta(minutes=settings.company_import_cooldown_minutes)
        if last and datetime.now(timezone.utc) - last < cooldown:
            remaining = cooldown - (datetime.now(timezone.utc) - last)
            minutes = max(1, int(remaining.total_seconds() // 60) + 1)
            raise AppError(
                409,
                "import_cooldown",
                f"Dati aziendali già aggiornati di recente: riprova tra circa {minutes} minuti",
            )

    if not await _acquire_lock(primary, parent_id):
        raise AppError(
            409,
            "import_in_progress",
            "Un'importazione è già in corso per questa azienda: attendi qualche istante",
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
        # accetta P.IVA o CF: confrontiamo con entrambi).
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

        lookups = await lookup_service.get_lookups(secondary)

        # Il profilo aziendale deve esistere per agganciare i dati certificati.
        if company_row is None:
            ragione = (payload.get("companyDetails") or {}).get("companyName") or "—"
            insert = (
                await primary.table("company_profiles")
                .insert({"parent_id": parent_id, "ragione_sociale": ragione, "partita_iva": piva})
                .execute()
            )
            company_row = await _fetch_company_row(primary, parent_id)
            if company_row is None:  # pragma: no cover — solo per robustezza
                raise OpenapiUpstreamError()

        updates, applied, conflicts, suggestions = build_autofill(
            payload, company_row, lookups
        )
        if updates:
            await primary.table("company_profiles").update(updates).eq(
                "parent_id", parent_id
            ).execute()

        derived = build_derived(payload, lookups)
        fetched_at = datetime.now(timezone.utc).isoformat()
        await primary.table("company_data").upsert(
            {
                "company_profile_id": company_row["id"],
                "provider": "openapi.it",
                "endpoint": "IT-full",
                "piva_fetched": piva,
                "sandbox": openapi.sandbox,
                "raw": payload,
                "derived": derived,
                "denominazione": (payload.get("companyDetails") or {}).get("companyName"),
                "stato_impresa": stato_impresa(payload),
                "fetch_count": (existing_data or {}).get("fetch_count", 0) + 1,
                "fetched_at": fetched_at,
            },
            on_conflict="company_profile_id",
        ).execute()

        people = extract_people(payload)
        await primary.table("company_people").delete().eq(
            "company_profile_id", company_row["id"]
        ).execute()
        if people:
            await primary.table("company_people").insert(
                [{"company_profile_id": company_row["id"], **person} for person in people]
            ).execute()

        await primary.table("audit_log").insert(
            {
                "actor_id": parent_id,
                "action": "company.imported",
                "target_user_id": parent_id,
                "family_parent_id": parent_id,
                "payload": {
                    "piva": piva,
                    "campi_compilati": applied,
                    "sandbox": openapi.sandbox,
                },
            }
        ).execute()
    finally:
        await _release_lock(primary, parent_id)

    company = await company_service.get_company(primary, user)
    return ImportResult(
        company=company,
        dossier=build_dossier(payload),
        people=[PersonOut(**{k: v for k, v in p.items() if k != "raw"}) for p in people],
        autofill=AutofillOut(applied=applied, conflicts=conflicts),
        suggestions=SuggestionsOut(**suggestions),
        fetched_at=fetched_at,
        sandbox=openapi.sandbox,
    )


async def get_dossier(primary, user: dict) -> DossierResponse:
    """Dossier certificato: proprio per il titolare, della famiglia (sola
    lettura) per un figlio attivo — stessa regola dei dati aziendali."""
    membership = await family_service.get_membership(primary, user["id"])
    if membership and membership["status"] == "active":
        owner_id, editable = str(membership["parent_id"]), False
    else:
        owner_id, editable = str(user["id"]), True

    company_row = await _fetch_company_row(primary, owner_id)
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
