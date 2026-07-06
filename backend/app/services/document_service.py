"""Documenti ufficiali dell'azienda (visure camerali) via openapi.it.

Flusso: POST della richiesta (A PAGAMENTO se accettata; il tipo d'impresa
giusto si scopre per tentativi, i rifiuti error-213 sono GRATUITI) →
"In erogazione" → polling gratuito → download ZIP → estrazione del PDF e del
TESTO (pypdf: oggetto sociale e poteri inclusi, input per l'AI-check) →
upload nel bucket Storage `company-documents`.

La richiesta è protetta come le altre chiamate a pagamento: lock per azienda,
indice unico parziale sul pending, registro consumi su ogni tentativo.
"""

import base64
import io
import logging
import zipfile
from datetime import datetime, timedelta, timezone

from app.clients.openapi import (
    VISURA_VARIANTS,
    OpenapiClient,
    OpenapiInvalidIdError,
    OpenapiTimeoutError,
    OpenapiWrongTypeError,
)
from app.core.errors import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    OpenapiNotConfiguredError,
    OpenapiUpstreamError,
)
from app.schemas.openapi_data import DocumentOut, DocumentsResponse
from app.services import family_service
from app.services.openapi_service import _acquire_lock, _release_lock, record_usage

logger = logging.getLogger("bandofit.documents")

BUCKET = "company-documents"
DOC_LOCK_TTL_SECONDS = 60

# Prezzi indicativi per variante (centesimi, IVA esclusa).
VISURA_COST_CENTS = {
    "ordinaria-societa-capitale": 490,
    "ordinaria-societa-persone": 490,
    "ordinaria-impresa-individuale": 290,
}

DOCUMENT_SELECT = (
    "id,company_profile_id,kind,endpoint,request_id,status,error_detail,"
    "file_path,file_name,file_size,pages,extracted_text,cost_cents,sandbox,"
    "created_at,ready_at"
)

_PENDING_STATES = {"in erogazione"}
# Parole che nei messaggi di stato del provider indicano un esito negativo
# definitivo: la richiesta va marcata error, non lasciata pending per sempre.
_FAILED_KEYWORDS = ("annullat", "errore", "rifiutat", "non evadibile", "respint")
# Failsafe: una richiesta senza esito entro questo tempo viene chiusa come
# error, così l'indice unico sul pending non blocca nuove richieste all'infinito.
_STALE_AFTER = timedelta(hours=24)


def _to_out(row: dict) -> DocumentOut:
    return DocumentOut(
        id=row["id"],
        kind=row["kind"],
        endpoint=row["endpoint"],
        status=row["status"],
        error_detail=row.get("error_detail"),
        file_name=row.get("file_name"),
        file_size=row.get("file_size"),
        pages=row.get("pages"),
        has_text=bool(row.get("extracted_text")),
        cost_cents=row.get("cost_cents") or 0,
        sandbox=bool(row.get("sandbox")),
        created_at=str(row.get("created_at")),
        ready_at=str(row["ready_at"]) if row.get("ready_at") else None,
    )


async def _owner_and_editable(primary, user: dict) -> tuple[str, bool]:
    """Stessa regola di visibilità dei dati aziendali."""
    membership = await family_service.get_membership(primary, user["id"])
    if membership and membership["status"] == "active":
        return str(membership["parent_id"]), False
    return str(user["id"]), True


async def _company_row(primary, owner_id: str) -> dict | None:
    resp = (
        await primary.table("company_profiles")
        .select("id,partita_iva,codice_fiscale")
        .eq("parent_id", owner_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _variant_order(legal_form_text: str) -> tuple[str, ...]:
    """Ordina le varianti di visura in base alla forma giuridica nota (da
    IT-full), così il primo tentativo è quasi sempre quello giusto. I
    tentativi sbagliati sono comunque gratuiti (error 213)."""
    text = (legal_form_text or "").lower()
    capitale, persone, individuale = VISURA_VARIANTS
    if any(n in text for n in ("individual", "sole", "impresa individuale")):
        return (individuale, capitale, persone)
    if any(n in text for n in ("partnership", "persone", "s.n.c", "s.a.s", "snc", "sas")):
        return (persone, capitale, individuale)
    if any(n in text for n in ("association", "other forms", "ente", "foundation", "fondazione", "comitato")):
        # Enti iscritti solo al REA: serviti dal canale impresa-individuale
        # (verificato sul campo).
        return (individuale, capitale, persone)
    return VISURA_VARIANTS


async def _legal_form_hint(primary, company_profile_id: str) -> str:
    resp = (
        await primary.table("company_data")
        .select("raw")
        .eq("company_profile_id", str(company_profile_id))
        .limit(1)
        .execute()
    )
    if not resp.data:
        return ""
    legal = (resp.data[0].get("raw") or {}).get("legalForm") or {}
    parts = [
        ((legal.get("detailedLegalForm") or {}).get("description") or ""),
        ((legal.get("legalForm") or {}).get("description") or ""),
    ]
    return " ".join(parts)


# ------------------------------------------------------------------ richiesta

async def request_document(primary, openapi: OpenapiClient, user: dict) -> DocumentOut:
    """Richiede la visura camerale ufficiale (A PAGAMENTO, ~2,90–4,90 €)."""
    if not openapi.enabled:
        raise OpenapiNotConfiguredError()

    owner_id, editable = await _owner_and_editable(primary, user)
    if not editable:
        raise ForbiddenError("I documenti aziendali li richiede il titolare dell'azienda")

    company = await _company_row(primary, owner_id)
    piva = (company or {}).get("partita_iva")
    if not company or not piva:
        raise BadRequestError(
            "Compila prima i dati aziendali con la partita IVA (o usa «Importa da P.IVA»)"
        )

    pending = (
        await primary.table("company_documents")
        .select("id")
        .eq("company_profile_id", company["id"])
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    if pending.data:
        raise AppError(
            409,
            "document_in_progress",
            "C'è già una visura in lavorazione: attendi che venga completata",
        )

    # La lettura della forma giuridica avviene PRIMA del lock: se fallisse
    # dopo, il lock resterebbe trattenuto per l'intero TTL senza motivo.
    hint = await _legal_form_hint(primary, company["id"])

    if not await _acquire_lock(primary, owner_id, DOC_LOCK_TTL_SECONDS):
        raise AppError(
            409,
            "document_in_progress",
            "Un'altra operazione è in corso per questa azienda: riprova tra qualche istante",
        )

    accepted: dict | None = None
    variant_used: str | None = None
    try:
        for variant in _variant_order(hint):
            try:
                accepted = await openapi.visura_request(variant, piva)
                variant_used = variant
                break
            except OpenapiWrongTypeError:
                continue  # rifiuto gratuito: variante successiva
    except OpenapiInvalidIdError:
        await record_usage(
            primary, user_id=owner_id, family_parent_id=owner_id,
            service="visura", outcome="error", cost_cents=0, meta={"piva": piva},
        )
        await _release_lock(primary, owner_id)
        raise NotFoundError("Partita IVA non trovata nel Registro Imprese") from None
    except OpenapiTimeoutError:
        # Esito (e addebito) ignoto: lock lasciato scadere.
        await record_usage(
            primary, user_id=owner_id, family_parent_id=owner_id,
            service="visura", outcome="timeout_unknown",
            cost_cents=0 if openapi.sandbox else max(VISURA_COST_CENTS.values()),
            meta={"piva": piva},
        )
        raise
    except AppError:
        await record_usage(
            primary, user_id=owner_id, family_parent_id=owner_id,
            service="visura", outcome="error", cost_cents=0, meta={"piva": piva},
        )
        await _release_lock(primary, owner_id)
        raise

    # Fuori dal try: il rifiuto di tutte le varianti non deve essere
    # ri-processato dal ramo AppError (doppia riga nel registro consumi).
    if accepted is None or variant_used is None:
        await record_usage(
            primary, user_id=owner_id, family_parent_id=owner_id,
            service="visura", outcome="error", cost_cents=0, meta={"piva": piva},
        )
        await _release_lock(primary, owner_id)
        raise BadRequestError(
            "Il Registro Imprese non fornisce la visura per questo tipo di impresa"
        )

    cost = 0 if openapi.sandbox else VISURA_COST_CENTS.get(variant_used, 490)
    request_id = str(accepted.get("id"))
    try:
        # Il request_id va nel registro consumi: se l'insert sotto fallisse,
        # resterebbe comunque un riferimento per recuperare il documento pagato.
        await record_usage(
            primary, user_id=owner_id, family_parent_id=owner_id,
            service="visura", outcome="success", cost_cents=cost,
            meta={"piva": piva, "variant": variant_used, "request_id": request_id},
        )
        document_row = {
            "company_profile_id": company["id"],
            "kind": "visura",
            "endpoint": variant_used,
            "request_id": request_id,
            "status": "pending",
            "cost_cents": cost,
            "sandbox": openapi.sandbox,
            "requested_by": str(user["id"]),
        }
        try:
            insert = await primary.table("company_documents").insert(document_row).execute()
        except Exception:
            # La visura è GIÀ pagata: perdere questa riga significherebbe
            # perdere il documento. Un ritento, poi si propaga (il request_id
            # è recuperabile dal registro consumi e da questo log).
            logger.exception(
                "visura pagata ma insert fallito: ritento (request_id=%s, variant=%s)",
                request_id, variant_used,
            )
            insert = await primary.table("company_documents").insert(document_row).execute()
        row = insert.data[0] if insert.data else None
        if row is None:  # pragma: no cover — solo per robustezza
            logger.error(
                "visura pagata senza riga documento (request_id=%s)", request_id
            )
            raise OpenapiUpstreamError()

        await primary.table("audit_log").insert(
            {
                "actor_id": str(user["id"]),
                "action": "company.document_requested",
                "target_user_id": owner_id,
                "family_parent_id": owner_id,
                "payload": {"kind": "visura", "variant": variant_used, "piva": piva},
            }
        ).execute()

        # La visura spesso è pronta in pochi secondi: un tentativo di
        # completamento inline evita all'utente il refresh.
        row = await _try_complete(primary, openapi, row)
        return _to_out(row)
    finally:
        await _release_lock(primary, owner_id)


# ---------------------------------------------------------------- evasione

def _extract_pdf(zip_bytes: bytes) -> tuple[bytes, str]:
    """Estrae il primo PDF dallo ZIP; se lo ZIP non è leggibile, restituisce
    i byte così come sono (mai perdere il documento pagato)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        for name in zf.namelist():
            if name.lower().endswith(".pdf"):
                return zf.read(name), name.rsplit("/", 1)[-1]
    except zipfile.BadZipFile:
        logger.warning("visura: allegato non ZIP, salvo i byte grezzi")
    return zip_bytes, "visura.pdf"


def _extract_text(pdf_bytes: bytes) -> tuple[str | None, int | None]:
    """Testo del PDF via pypdf. Difensivo: un PDF non estraibile non deve
    far fallire l'evasione (il documento resta scaricabile)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return (text.strip() or None), len(reader.pages)
    except Exception:
        logger.exception("visura: estrazione testo fallita, salvo solo il PDF")
        return None, None


async def _upload_pdf(primary, path: str, pdf_bytes: bytes) -> None:
    storage = primary.storage
    try:
        await storage.create_bucket(BUCKET, options={"public": False})
    except Exception:
        pass  # esiste già (o verrà segnalato dall'upload)
    await storage.from_(BUCKET).upload(
        path, pdf_bytes, {"content-type": "application/pdf", "upsert": "true"}
    )


async def _mark_error(primary, row: dict, detail: str) -> dict:
    await primary.table("company_documents").update(
        {"status": "error", "error_detail": detail}
    ).eq("id", row["id"]).eq("status", "pending").execute()
    return {**row, "status": "error", "error_detail": detail}


def _is_stale(row: dict) -> bool:
    try:
        created = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > _STALE_AFTER


async def _try_complete(primary, openapi: OpenapiClient, row: dict) -> dict:
    """Se la richiesta è stata evasa dal provider, scarica l'allegato, estrae
    PDF+testo e archivia. Idempotente: l'update è condizionato su pending.
    Gli esiti negativi del provider (o le richieste senza esito da oltre 24h)
    vengono chiusi come error, così non bloccano nuove richieste per sempre."""
    if row.get("status") != "pending" or not row.get("request_id"):
        return row
    try:
        status = await openapi.visura_status(row["endpoint"], row["request_id"])
    except AppError:
        # Transitorio: si riproverà alla prossima lettura — ma non all'infinito.
        if _is_stale(row):
            return await _mark_error(
                primary, row, "Richiesta scaduta senza esito dal Registro Imprese"
            )
        return row

    stato = str(status.get("stato_richiesta") or "").strip().lower()
    allegati = status.get("allegati") or []
    if any(keyword in stato for keyword in _FAILED_KEYWORDS):
        return await _mark_error(
            primary, row, f"Richiesta non evasa dal Registro Imprese ({stato})"
        )
    if stato in _PENDING_STATES or not allegati:
        if _is_stale(row):
            return await _mark_error(
                primary, row, "Richiesta scaduta senza esito dal Registro Imprese"
            )
        return row

    try:
        payload = await openapi.visura_allegati(row["endpoint"], row["request_id"])
        zip_bytes = base64.b64decode(payload["file"])
        pdf_bytes, file_name = _extract_pdf(zip_bytes)
        text, pages = _extract_text(pdf_bytes)
        path = f"{row['company_profile_id']}/{row['id']}.pdf"
        await _upload_pdf(primary, path, pdf_bytes)
    except Exception:
        logger.exception("visura: evasione fallita (doc=%s)", row.get("id"))
        return row  # si riproverà: il documento pagato non va marcato error per un guasto nostro

    await primary.table("company_documents").update(
        {
            "status": "ready",
            "file_path": path,
            "file_name": file_name,
            "file_size": len(pdf_bytes),
            "pages": pages,
            "extracted_text": text,
            "ready_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", row["id"]).eq("status", "pending").execute()  # solo un vincitore

    refreshed = (
        await primary.table("company_documents")
        .select(DOCUMENT_SELECT)
        .eq("id", row["id"])
        .limit(1)
        .execute()
    )
    return refreshed.data[0] if refreshed.data else row


# ------------------------------------------------------------------- lettura

async def list_documents(primary, openapi: OpenapiClient, user: dict) -> DocumentsResponse:
    """Elenco documenti (visibilità come i dati aziendali). Le richieste
    pending vengono completate qui se il provider le ha evase (poll gratuito)."""
    owner_id, editable = await _owner_and_editable(primary, user)
    company = await _company_row(primary, owner_id)
    if company is None:
        return DocumentsResponse(editable=editable, documents=[])

    resp = (
        await primary.table("company_documents")
        .select(DOCUMENT_SELECT)
        .eq("company_profile_id", company["id"])
        .order("created_at", desc=True)
        .execute()
    )
    rows = resp.data or []
    out: list[DocumentOut] = []
    for row in rows:
        if row.get("status") == "pending" and openapi.enabled:
            row = await _try_complete(primary, openapi, row)
        out.append(_to_out(row))
    return DocumentsResponse(editable=editable, documents=out)


async def download_document(primary, user: dict, document_id: str) -> tuple[bytes, str]:
    """Scarica il PDF di un documento pronto (anche per i figli attivi)."""
    owner_id, _ = await _owner_and_editable(primary, user)
    company = await _company_row(primary, owner_id)
    if company is None:
        raise NotFoundError("Documento non trovato")

    resp = (
        await primary.table("company_documents")
        .select("id,company_profile_id,status,file_path,file_name")
        .eq("id", str(document_id))
        .eq("company_profile_id", company["id"])  # mai documenti di altre aziende
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Documento non trovato")
    row = resp.data[0]
    if row["status"] != "ready" or not row.get("file_path"):
        raise AppError(409, "document_not_ready", "Il documento non è ancora pronto")

    try:
        pdf_bytes = await primary.storage.from_(BUCKET).download(row["file_path"])
    except Exception as exc:
        logger.exception("visura: download dal bucket fallito (doc=%s)", document_id)
        raise OpenapiUpstreamError("Documento momentaneamente non disponibile") from exc
    return pdf_bytes, row.get("file_name") or "visura.pdf"
