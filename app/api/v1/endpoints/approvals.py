from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_any_permission, require_permission
from app.database.session import get_db
from app.models.approval import ApprovalStatus
from app.models.user import User
from app.schemas.approval import ApprovalCreate, ApprovalListResponse, ApprovalResponse, ApproveRequest, RejectRequest
from app.services import approval_service
from app.workflows.approval_workflow import ApprovalWorkflowTransitionError

router = APIRouter()


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
    dependencies=[Depends(require_any_permission("approve_action", "view_audit_logs", "execute_action"))],
)
def list_approval_requests(
    status_filter: ApprovalStatus = Query(None, description="Filter by status"),
    user_id: UUID = Query(None, description="Filter by requester user UUID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    items, total = approval_service.list_requests(
        db, status=status_filter, user_id=user_id, skip=skip, limit=limit
    )
    return {"items": items, "total": total}


@router.get(
    "/{request_id}",
    response_model=ApprovalResponse,
    summary="Get approval request by ID",
    dependencies=[Depends(require_any_permission("approve_action", "view_audit_logs", "execute_action"))],
)
def get_approval_request(
    request_id: UUID,
    db: Session = Depends(get_db),
) -> Any:
    request_obj = approval_service.get_request(db, request_id)
    if not request_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found")
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
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        return approval_service.approve_request(
            db,
            request_id,
            current_user.id,
            requested_quantity=obj_in.requested_quantity,
            decision_reason=obj_in.decision_reason
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
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        return approval_service.reject_request(
            db,
            request_id,
            current_user.id,
            decision_reason=obj_in.decision_reason
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        return approval_service.execute_request(db, request_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ApprovalWorkflowTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))