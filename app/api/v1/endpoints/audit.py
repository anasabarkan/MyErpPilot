from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_manager_or_admin_user, require_permission
from app.database.session import get_db
from app.models.user import User
from app.repositories import audit_repository
from app.schemas.audit import AuditLogListResponse, AuditLogResponse

router = APIRouter()


@router.get(
    "/logs",
    response_model=AuditLogListResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("view_audit_logs"))],
)
def get_audit_logs(
    user_id: Optional[UUID] = Query(None, description="Filter by user ID"),
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    action: Optional[str] = Query(None, description="Filter by action string"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    status: Optional[str] = Query(None, description="Filter by status (e.g. success, failed, denied)"),
    start_date: Optional[datetime] = Query(None, description="Start date for logs (UTC)"),
    end_date: Optional[datetime] = Query(None, description="End date for logs (UTC)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin_user),
):
    """
    List audit logs with optional filtering.
    Requires a manager/admin role and 'view_audit_logs' permission.
    """
    items, total = audit_repository.list_logs(
        db,
        user_id=user_id,
        entity_id=entity_id,
        action=action,
        entity_type=entity_type,
        status=status,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )
    return AuditLogListResponse(items=list(items), total=total)


@router.get(
    "/logs/{log_id}",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("view_audit_logs"))],
)
def get_audit_log(
    log_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin_user),
):
    """
    Retrieve a specific audit log by its ID.
    Requires a manager/admin role and 'view_audit_logs' permission.
    """
    log = audit_repository.get_log_by_id(db, log_id)
    if not log:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not found")
    return log
