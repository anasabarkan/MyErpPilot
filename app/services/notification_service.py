import logging
from typing import List, Optional, Tuple
from uuid import UUID
from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationSeverity, NotificationStatus
from app.repositories import notification_repository

logger = logging.getLogger(__name__)

def create_inventory_alert_notification(
    db: Session, 
    user_id: UUID, 
    product_name: str, 
    qty: float, 
    min_qty: float, 
    approval_request_id: str
) -> Notification:
    
    is_out_of_stock = qty <= 0
    severity = NotificationSeverity.critical if is_out_of_stock else NotificationSeverity.warning
    
    title = f"Out of Stock: {product_name}" if is_out_of_stock else f"Low Stock: {product_name}"
    message = f"Product '{product_name}' has fallen to {qty} units (minimum: {min_qty}). Action required."
    
    data = {
        "user_id": user_id,
        "type": "inventory_alert",
        "title": title,
        "message": message,
        "severity": severity,
        "related_entity_type": "approval_request",
        "related_entity_id": str(approval_request_id)
    }
    
    notification = notification_repository.create_notification(db, data)
    logger.info(f"Created inventory alert notification for user {user_id} regarding product '{product_name}'")
    return notification

def list_notifications(
    db: Session, 
    user_id: UUID, 
    status: Optional[NotificationStatus] = None, 
    skip: int = 0, 
    limit: int = 100
) -> Tuple[List[Notification], int, int]:
    return notification_repository.list_notifications(db, user_id, status, skip, limit)

def mark_as_read(db: Session, notification_id: UUID, user_id: UUID) -> Optional[Notification]:
    notification = notification_repository.get_by_id(db, notification_id)
    if not notification or notification.user_id != user_id:
        raise ValueError("Notification not found or access denied")
        
    return notification_repository.mark_as_read(db, notification_id)

def mark_all_as_read(db: Session, user_id: UUID) -> int:
    return notification_repository.mark_all_as_read(db, user_id)

def mark_related_alerts_as_read(db: Session, approval_request_id: str, *, commit: bool = True) -> int:
    return notification_repository.mark_related_entity_as_read(
        db,
        related_entity_type="approval_request",
        related_entity_id=str(approval_request_id),
        commit=commit,
    )
