"""Webhook Revolut (pubblico, senza auth utente: la prova è la firma HMAC).

Meccanica verificata in sandbox (Fase 0):
- payload THIN: solo ``{"event", "order_id", ...}`` — lo stato vero si rilegge
  SEMPRE dal provider (``GET /api/orders/{id}``), mai dedotto dall'evento;
- firma: ``Revolut-Signature: v1=HMAC_SHA256(secret, "v1.{ts}.{raw_body}")``,
  con possibili firme multiple comma-separated (rotazione del secret) e
  ``Revolut-Request-Timestamp`` in MILLISECONDI; tolleranza anti-replay 5 min;
- consegna at-least-once senza ordine garantito: il dedup è differenziato per
  cardinalità (vedi migration 0026) e le RPC a valle sono idempotenti, quindi
  ri-processare è sempre sicuro.
Si risponde subito 204 e si elabora in background: Revolut ritenta 3×/10min
solo sugli errori HTTP, non deve dipendere dalla nostra latenza di processing.
"""

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.api.deps import get_revolut
from app.core.config import get_settings

logger = logging.getLogger("bandofit.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_TOLLERANZA_MS = 5 * 60 * 1000
_EVENTI_ORDER_LEVEL = {"ORDER_COMPLETED", "ORDER_FAILED", "ORDER_CANCELLED"}
_EVENTI_GESTITI = _EVENTI_ORDER_LEVEL | {"ORDER_PAYMENT_DECLINED", "ORDER_PAYMENT_FAILED"}


def _firma_valida(secret: str, timestamp: str, raw: bytes, header: str) -> bool:
    attesa = "v1=" + hmac.new(
        secret.encode(), b"v1." + timestamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    # Più firme durante la rotazione del secret: basta che una corrisponda.
    return any(hmac.compare_digest(attesa, s.strip()) for s in header.split(","))


async def _processa(primary, revolut, event_id: str, order_id: str) -> None:
    from app.services import payment_service  # import locale: evita cicli

    try:
        esito = await payment_service.elabora_ordine(primary, revolut, order_id)
        stato = str(esito.get("esito") or "ok")
    except Exception:
        logger.exception("webhook: elaborazione fallita per ordine %s", order_id)
        stato = "errore"
    try:
        await (
            primary.table("webhook_events")
            .update({"processed_at": "now()", "esito": stato})
            .eq("id", event_id)
            .execute()
        )
    except Exception:
        logger.exception("webhook: aggiornamento esito fallito (%s)", event_id)


@router.post("/revolut", status_code=204)
async def revolut_webhook(request: Request, background: BackgroundTasks) -> Response:
    settings = get_settings()
    secret = settings.revolut_webhook_secret
    if not secret:
        # Senza secret la firma non è verificabile: rifiutare è l'unica
        # risposta onesta (Revolut ritenterà; il deploy va corretto).
        return Response(status_code=503)

    raw = await request.body()
    timestamp = request.headers.get("Revolut-Request-Timestamp", "")
    firma = request.headers.get("Revolut-Signature", "")
    if not timestamp.isdigit() or not firma:
        return Response(status_code=401)
    if abs(time.time() * 1000 - int(timestamp)) > _TOLLERANZA_MS:
        return Response(status_code=401)
    if not _firma_valida(secret, timestamp, raw, firma):
        logger.warning("webhook revolut: firma non valida")
        return Response(status_code=401)

    try:
        payload = json.loads(raw)
    except ValueError:
        return Response(status_code=400)
    evento = str(payload.get("event") or "")
    order_id = str(payload.get("order_id") or "")
    if evento not in _EVENTI_GESTITI or not order_id:
        # Evento non pertinente (o futuro): ack e basta.
        return Response(status_code=204)

    primary = request.app.state.primary
    revolut = get_revolut(request)
    riga = {"provider": "revolut", "event": evento, "resource_id": order_id,
            "payload": payload}
    try:
        resp = await primary.table("webhook_events").insert(riga).execute()
    except Exception as exc:
        if "webhook_events_dedup_order" in str(exc) or "23505" in str(exc):
            # Retry di un evento order-level già visto: ack senza rielaborare.
            return Response(status_code=204)
        logger.exception("webhook revolut: registrazione evento fallita")
        return Response(status_code=500)  # Revolut ritenterà

    background.add_task(_processa, primary, revolut, str(resp.data[0]["id"]), order_id)
    return Response(status_code=204)
