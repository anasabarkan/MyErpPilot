from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_permission
from app.database.session import get_db
from app.models.registration_request import RegistrationRequestStatus
from app.models.user import User
from app.schemas.registration_request import (
    RegistrationRequestApprove,
    RegistrationRequestListResponse,
    RegistrationRequestReject,
    RegistrationRequestResponse,
)
from app.services import registration_request_service

router = APIRouter(prefix="/registration-requests", tags=["Registration Requests"])


@router.get(
    "",
    response_model=RegistrationRequestListResponse,
    dependencies=[Depends(require_permission("manage_users"))],
)
def list_registration_requests(
    status_filter: RegistrationRequestStatus | None = Query(
        default=None,
        description="Filter by status.",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    items, total = registration_request_service.list_registration_requests(
        db,
        status_filter=status_filter,
        skip=skip,
        limit=limit,
    )
    return {"items": items, "total": total}


@router.post(
    "/{request_id}/approve",
    response_model=RegistrationRequestResponse,
    dependencies=[Depends(require_permission("manage_users"))],
)
def approve_registration_request(
    request_id: UUID,
    payload: RegistrationRequestApprove,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return registration_request_service.approve_registration_request(
        db,
        request_id=request_id,
        role_name=payload.role_name,
        reviewer_id=current_user.id,
    )


@router.post(
    "/{request_id}/reject",
    response_model=RegistrationRequestResponse,
    dependencies=[Depends(require_permission("manage_users"))],
)
def reject_registration_request(
    request_id: UUID,
    payload: RegistrationRequestReject,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return registration_request_service.reject_registration_request(
        db,
        request_id=request_id,
        rejection_reason=payload.rejection_reason,
        reviewer_id=current_user.id,
    )
