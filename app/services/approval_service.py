import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.approval import ApprovalRequest, ApprovalStatus
from app.repositories import approval_repository
from app.schemas.approval import ApprovalCreate
from app.workflows.approval_workflow import ApprovalWorkflow, ApprovalWorkflowTransitionError
from app.services import audit_service

logger = logging.getLogger(__name__)


def create_approval_request(
    db: Session, 
    obj_in: ApprovalCreate, 
    user_id: UUID
) -> ApprovalRequest:
    """
    Create a new approval request with status = pending.
    (Permission check happens at the API layer using Depends)
    """
    data = obj_in.model_dump()
    data["requested_by"] = user_id
    data["status"] = ApprovalStatus.pending
    
    request_obj = approval_repository.create_request(db, data)
    
    # Audit logic
    audit_service.log_approval_event(
        db,
        action="create",
        approval_id=str(request_obj.id),
        user_id=user_id,
        message=f"Created approval request for action: {request_obj.action_type}"
    )
    logger.info(f"Audit: User {user_id} created ApprovalRequest {request_obj.id} (Action: {request_obj.action_type})")
    
    return request_obj


def approve_request(
    db: Session, 
    request_id: UUID, 
    validator_id: UUID
) -> ApprovalRequest:
    """
    Approve an existing request. Status goes to approved.
    (Permission check happens at the API layer using Depends)
    """
    request_obj = approval_repository.get_request_by_id(db, request_id)
    if not request_obj:
        raise ValueError("Approval request not found")

    ApprovalWorkflow.validate_transition(request_obj.status, ApprovalStatus.approved)

    approval_repository.update_status(db, request_obj, ApprovalStatus.approved)
    approval_repository.assign_validator(
        db, 
        request_obj, 
        validator_id=validator_id, 
        validated_at=datetime.now(timezone.utc)
    )

    audit_service.log_approval_event(
        db,
        action="approve",
        approval_id=str(request_id),
        user_id=validator_id
    )
    logger.info(f"Audit: User {validator_id} approved ApprovalRequest {request_id}")
    return request_obj


def reject_request(
    db: Session, 
    request_id: UUID, 
    validator_id: UUID, 
    rejection_reason: str
) -> ApprovalRequest:
    """
    Reject an existing request. Status goes to rejected.
    """
    request_obj = approval_repository.get_request_by_id(db, request_id)
    if not request_obj:
        raise ValueError("Approval request not found")

    ApprovalWorkflow.validate_transition(request_obj.status, ApprovalStatus.rejected)

    approval_repository.update_status(
        db, 
        request_obj, 
        ApprovalStatus.rejected, 
        rejection_reason=rejection_reason
    )
    approval_repository.assign_validator(
        db, 
        request_obj, 
        validator_id=validator_id, 
        validated_at=datetime.now(timezone.utc)
    )

    audit_service.log_approval_event(
        db,
        action="reject",
        approval_id=str(request_id),
        user_id=validator_id,
        message=rejection_reason
    )
    logger.info(f"Audit: User {validator_id} rejected ApprovalRequest {request_id}. Reason: {rejection_reason}")
    return request_obj


def execute_request(
    db: Session, 
    request_id: UUID, 
    executor_id: UUID
) -> ApprovalRequest:
    """
    Execute an approved request. Simulate execution logic.
    """
    request_obj = approval_repository.get_request_by_id(db, request_id)
    if not request_obj:
        raise ValueError("Approval request not found")

    ApprovalWorkflow.validate_transition(request_obj.status, ApprovalStatus.executed)
    
    # In a real app, you would dispatch the execution to a worker or handler
    # based on request_obj.action_type and request_obj.payload
    try:
        # Simulate execution success
        # ... logic ...
        final_status = ApprovalStatus.executed
    except Exception as e:
        logger.error(f"Execution failed for request {request_id}: {e}")
        final_status = ApprovalStatus.failed

    approval_repository.update_status(db, request_obj, final_status)
    approval_repository.assign_executor(
        db, 
        request_obj, 
        executor_id=executor_id, 
        executed_at=datetime.now(timezone.utc)
    )

    audit_service.log_execution_event(
        db,
        action="execute_request",
        approval_id=str(request_id),
        user_id=executor_id,
        status="success" if final_status == ApprovalStatus.executed else "failed",
        # We don't have the explicit error string initialized outside the try-except scope in current code
        # unless we extract e, but we can just say failed execution
        message="Execution successful" if final_status == ApprovalStatus.executed else "Execution failed"
    )

    logger.info(f"Audit: User {executor_id} executed ApprovalRequest {request_id} with status {final_status}")
    return request_obj


def list_requests(
    db: Session, 
    status: Optional[ApprovalStatus] = None, 
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
):
    return approval_repository.list_requests(db, status=status, user_id=user_id, skip=skip, limit=limit)


def get_request(db: Session, request_id: UUID) -> Optional[ApprovalRequest]:
    return approval_repository.get_request_by_id(db, request_id)
