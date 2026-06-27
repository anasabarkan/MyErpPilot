from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.notification import NotificationStatus
from app.models.user import User
from app.schemas.notification import NotificationListResponse, NotificationResponse
from app.services import notification_service

router = APIRouter()

@router.get("/", response_model=NotificationListResponse)
def list_my_notifications(
    status: Optional[NotificationStatus] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Retrieve notifications for the current user."""
    items, total, unread_count = notification_service.list_notifications(
        db=db, user_id=current_user.id, status=status, skip=skip, limit=limit
    )
    return {
        "items": items,
        "total": total,
        "unread_count": unread_count,
    }

@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_as_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Mark a specific notification as read."""
    notification = notification_service.mark_as_read(db, notification_id, current_user.id)
    return notification

@router.post("/read-all", response_model=dict)
def mark_all_notifications_as_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Mark all unread notifications as read for the current user."""
    updated_count = notification_service.mark_all_as_read(db, current_user.id)
    return {"updated_count": updated_count}
