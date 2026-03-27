"""
Repository for audit logs.
"""

from datetime import datetime
from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.schemas.audit import AuditLogCreateInternal


def create_log(db: Session, log_data: AuditLogCreateInternal) -> AuditLog:
    """Create a new audit log entry."""
    db_log = AuditLog(**log_data.model_dump(by_alias=True))
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


def get_log_by_id(db: Session, log_id: UUID) -> Optional[AuditLog]:
    """Retrieve an audit log by its ID."""
    return db.scalars(select(AuditLog).where(AuditLog.id == log_id)).first()


def list_logs(
    db: Session,
    *,
    user_id: Optional[UUID] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100
) -> Tuple[Sequence[AuditLog], int]:
    """List audit logs with optional filtering and pagination."""
    stmt = select(AuditLog)
    count_stmt = select(func.count()).select_from(AuditLog)

    filters = []
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id:
        filters.append(AuditLog.entity_id == entity_id)
    if action:
        filters.append(AuditLog.action == action)
    if status:
        filters.append(AuditLog.status == status)
    if start_date:
        filters.append(AuditLog.created_at >= start_date)
    if end_date:
        filters.append(AuditLog.created_at <= end_date)

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    # Order by descending created_at
    stmt = stmt.order_by(desc(AuditLog.created_at)).offset(skip).limit(limit)

    total = db.scalar(count_stmt) or 0
    items = db.scalars(stmt).all()

    return items, total


def list_logs_by_user(db: Session, user_id: UUID, skip: int = 0, limit: int = 100) -> Tuple[Sequence[AuditLog], int]:
    """List all audit logs related to a specific user (either performed by them, or on them as an entity)."""
    # Using an OR condition
    from sqlalchemy import or_
    stmt = select(AuditLog).where(
        or_(
            AuditLog.user_id == user_id,
            AuditLog.entity_id == str(user_id)
        )
    ).order_by(desc(AuditLog.created_at)).offset(skip).limit(limit)
    
    count_stmt = select(func.count()).select_from(AuditLog).where(
        or_(
            AuditLog.user_id == user_id,
            AuditLog.entity_id == str(user_id)
        )
    )
    
    total = db.scalar(count_stmt) or 0
    items = db.scalars(stmt).all()
    
    return items, total


def list_logs_by_entity(db: Session, entity_type: str, entity_id: str, skip: int = 0, limit: int = 100) -> Tuple[Sequence[AuditLog], int]:
    """List all audit logs for a specific entity."""
    return list_logs(db, entity_type=entity_type, entity_id=entity_id, skip=skip, limit=limit)
