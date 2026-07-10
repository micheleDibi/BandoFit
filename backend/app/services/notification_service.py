"""Notifiche in-app (DB primario, service_role).

È il canale AFFIDABILE degli eventi applicativi: le email sono best-effort
(email_service non solleva mai), la notifica in-app persiste. L'inserimento è
idempotente per (user_id, dedup_key): un retry non crea doppioni e un fan-out
parziale ri-eseguito inserisce solo i destinatari mancanti — il constraint
UNIQUE pieno fa da arbiter dell'upsert ignore-duplicates.
"""

import logging
from datetime import datetime, timezone
from math import ceil

from app.core.errors import BadRequestError
from app.schemas.notification import MarkReadIn, NotificationOut, NotificationsPage

logger = logging.getLogger("bandofit.notifications")

NOTIFICATION_SELECT = "id,tipo,titolo,corpo,url,read_at,created_at"


async def notify(
    primary,
    user_ids: list[str],
    *,
    tipo: str,
    titolo: str,
    corpo: str | None = None,
    url: str | None = None,
    dedup_key: str,
) -> None:
    """Recapita la stessa notifica a più destinatari. MAI solleva: un guasto
    sulle notifiche non deve far fallire la transizione che le origina
    (stesso patto dell'audit_log best-effort)."""
    if not user_ids:
        return
    rows = [
        {
            "user_id": str(user_id),
            "tipo": tipo,
            "titolo": titolo,
            "corpo": corpo,
            "url": url,
            "dedup_key": dedup_key,
        }
        for user_id in user_ids
    ]
    try:
        await primary.table("notifications").upsert(
            rows, on_conflict="user_id,dedup_key", ignore_duplicates=True
        ).execute()
    except Exception:
        logger.warning(
            "notifiche non recapitate (tipo=%s, destinatari=%d)",
            tipo,
            len(user_ids),
            exc_info=True,
        )


async def list_notifications(
    primary, user_id: str, page: int, page_size: int
) -> NotificationsPage:
    offset = (page - 1) * page_size
    resp = (
        await primary.table("notifications")
        .select(NOTIFICATION_SELECT, count="exact")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    unread = (
        await primary.table("notifications")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .is_("read_at", "null")
        .limit(1)
        .execute()
    )
    total = resp.count or 0
    return NotificationsPage(
        items=[NotificationOut(**row) for row in resp.data],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, ceil(total / page_size)) if total else 0,
        non_lette=unread.count or 0,
    )


async def mark_read(primary, user_id: str, data: MarkReadIn) -> None:
    if not data.all and not data.ids:
        raise BadRequestError("Indica le notifiche da segnare come lette")
    query = (
        primary.table("notifications")
        .update({"read_at": datetime.now(timezone.utc).isoformat()})
        .eq("user_id", user_id)
        .is_("read_at", "null")
    )
    if not data.all and data.ids:
        query = query.in_("id", data.ids)
    await query.execute()
