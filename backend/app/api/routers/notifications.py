from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, PrimaryClient
from app.schemas.notification import MarkReadIn, NotificationsPage
from app.services import notification_service

router = APIRouter(prefix="/me/notifications", tags=["notifications"])


@router.get("", response_model=NotificationsPage)
async def list_notifications(
    user: CurrentUser,
    primary: PrimaryClient,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
) -> NotificationsPage:
    return await notification_service.list_notifications(primary, user["id"], page, page_size)


@router.post("/read", status_code=204)
async def mark_read(data: MarkReadIn, user: CurrentUser, primary: PrimaryClient) -> None:
    await notification_service.mark_read(primary, user["id"], data)
