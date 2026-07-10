from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_current_user,
    get_current_user_role_names,
    require_any_permission,
    require_manager_or_admin_user,
    require_operator_or_admin_user,
    require_permission,
)
from app.core.rbac import ADMIN_ROLE, MANAGER_ROLE, OPERATOR_ROLE
from app.database.session import get_db
from app.models.approval import ApprovalStatus
from app.models.user import User
from app.schemas.approval import ApprovalCreate, ApprovalListResponse, ApprovalResponse, ApproveRequest, ExecuteRequest, RejectRequest
from app.services import approval_service, audit_service
from app.workflows.approval_workflow import ApprovalWorkflowTransitionError

router = APIRouter()
_OPERATOR_VISIBLE_STATUSES = {
    ApprovalStatus.approved,
    ApprovalStatus.executed,
    ApprovalStatus.failed,
}


def _ensure_approval_read_access(
    *,
    db: Session,
    current_user: User,
    status_filter: ApprovalStatus | None = None,
    request_status: ApprovalStatus | None = None,
) -> None:
    role_names = get_current_user_role_names(current_user)
    if ADMIN_ROLE in role_names or MANAGER_ROLE in role_names:
        return

    if OPERATOR_ROLE in role_names:
        effective_status = request_status if request_status is not None else status_filter
        if effective_status in _OPERATOR_VISIBLE_STATUSES:
            return

        audit_service.log_event(
            db=db,
            action="access_denied",
            entity_type="rbac",
            entity_id="approval_read",
            user_id=current_user.id,
            status="denied",
            message="Operators can only access approved, executed, or failed approval items.",
            metadata={
                "requested_status": effective_status.value if isinstance(effective_status, ApprovalStatus) else effective_status,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operators can only access approved, executed, or failed approval items.",
        )

    audit_service.log_event(
        db=db,
        action="access_denied",
        entity_type="rbac",
        entity_id="approval_read",
        user_id=current_user.id,
        status="denied",
        message="Only managers, operators, and admins can access approval requests.",
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only managers, operators, and admins can access approval requests.",
    )


@router.post(
    "",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new approval request",
    dependencies=[Depends(require_permission("propose_action"))],
)
def create_approval_request(
    obj_in: ApprovalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    return approval_service.create_approval_request(db, obj_in, current_user.id)


@router.get(
    "",
    response_model=ApprovalListResponse,
    summary="List approval requests",
    dependencies=[Depends(require_any_permission("approve_action", "execute_action"))],
)
def list_approval_requests(
    status_filter: ApprovalStatus = Query(None, description="Filter by status"),
    user_id: UUID = Query(None, description="Filter by requester user UUID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    _ensure_approval_read_access(db=db, current_user=current_user, status_filter=status_filter)
    items, total = approval_service.list_requests(
        db, status=status_filter, user_id=user_id, skip=skip, limit=limit
    )
    return {"items": items, "total": total}


@router.get(
    "/{request_id}",
    response_model=ApprovalResponse,
    summary="Get approval request by ID",
    dependencies=[Depends(require_any_permission("approve_action", "execute_action"))],
)
def get_approval_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    request_obj = approval_service.get_request(db, request_id)
    if not request_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found")

    _ensure_approval_read_access(
        db=db,
        current_user=current_user,
        request_status=request_obj.status,
    )
    return request_obj


@router.post(
    "/{request_id}/approve",
    response_model=ApprovalResponse,
    summary="Approve a request",
    dependencies=[Depends(require_permission("approve_action"))],
)
def approve_request(
    request_id: UUID,
    obj_in: ApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin_user),
) -> Any:
    try:
        return approval_service.approve_request(
            db,
            request_id,
            current_user.id,
            decision_reason=obj_in.decision_reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ApprovalWorkflowTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/{request_id}/reject",
    response_model=ApprovalResponse,
    summary="Reject a request",
    dependencies=[Depends(require_permission("approve_action"))],
)
def reject_request(
    request_id: UUID,
    obj_in: RejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin_user),
) -> Any:
    try:
        return approval_service.reject_request(
            db,
            request_id,
            current_user.id,
            decision_reason=obj_in.decision_reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ApprovalWorkflowTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/{request_id}/execute",
    response_model=ApprovalResponse,
    summary="Execute an approved request",
    dependencies=[Depends(require_permission("execute_action"))],
)
def execute_request(
    request_id: UUID,
    obj_in: ExecuteRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator_or_admin_user),
) -> Any:
    try:
        return approval_service.execute_request(
            db,
            request_id,
            current_user.id,
            requested_quantity=obj_in.requested_quantity if obj_in else None,
        )
    except ValueError as e:
        detail = str(e)
        status_code = (
            status.HTTP_400_BAD_REQUEST
            if detail.startswith("Requested quantity") or "product identifier" in detail
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=status_code, detail=detail)
    except ApprovalWorkflowTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
