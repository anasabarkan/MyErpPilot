from typing import List, Optional, Tuple
from uuid import UUID
from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationStatus

def create_notification(db: Session, data: dict) -> Notification:
    db_obj = Notification(**data)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def list_notifications(
    db: Session, 
    user_id: UUID, 
    status: Optional[NotificationStatus] = None, 
    skip: int = 0, 
    limit: int = 100
) -> Tuple[List[Notification], int, int]:
    query = db.query(Notification).filter(Notification.user_id == user_id)
    
    if status:
        query = query.filter(Notification.status == status)
        
    total = query.count()
    unread_count = db.query(Notification).filter(
        Notification.user_id == user_id, 
        Notification.status == NotificationStatus.unread
    ).count()
    
    items = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
    return items, total, unread_count

def mark_as_read(db: Session, notification_id: UUID) -> Optional[Notification]:
    from datetime import datetime, timezone
    
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if notification and notification.status == NotificationStatus.unread:
        notification.status = NotificationStatus.read
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification

def mark_all_as_read(db: Session, user_id: UUID) -> int:
    from datetime import datetime, timezone
    
    notifications = db.query(Notification).filter(
        Notification.user_id == user_id, 
        Notification.status == NotificationStatus.unread
    ).all()
    
    count = len(notifications)
    if count > 0:
        now = datetime.now(timezone.utc)
        for n in notifications:
            n.status = NotificationStatus.read
            n.read_at = now
        db.commit()
    return count

def mark_related_entity_as_read(
    db: Session,
    related_entity_type: str,
    related_entity_id: str,
    *,
    commit: bool = True,
) -> int:
    from datetime import datetime, timezone

    notifications = db.query(Notification).filter(
        Notification.related_entity_type == related_entity_type,
        Notification.related_entity_id == related_entity_id,
        Notification.status == NotificationStatus.unread,
    ).all()

    count = len(notifications)
    if count == 0:
        return 0

    now = datetime.now(timezone.utc)
    for notification in notifications:
        notification.status = NotificationStatus.read
        notification.read_at = now

    if commit:
        db.commit()
    return count

def get_by_id(db: Session, notification_id: UUID) -> Optional[Notification]:
    return db.query(Notification).filter(Notification.id == notification_id).first()
