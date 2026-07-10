from typing import List, Optional
from uuid import UUID

from sqlalchemy import desc, nullslast
from sqlalchemy.orm import Session

from app.models.approval import ApprovalRequest, ApprovalStatus


def create_request(db: Session, request_data: dict) -> ApprovalRequest:
    db_obj = ApprovalRequest(**request_data)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def get_request_by_id(db: Session, request_id: UUID) -> Optional[ApprovalRequest]:
    return db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()


def list_requests(
    db: Session, 
    status: Optional[ApprovalStatus] = None, 
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
) -> tuple[List[ApprovalRequest], int]:
    query = db.query(ApprovalRequest)

    if status:
        query = query.filter(ApprovalRequest.status == status)
    if user_id:
        query = query.filter(ApprovalRequest.requested_by == user_id)

    total = query.count()
    if status == ApprovalStatus.executed:
        query = query.order_by(
            nullslast(desc(ApprovalRequest.executed_at)),
            ApprovalRequest.created_at.desc(),
        )
    else:
        query = query.order_by(ApprovalRequest.created_at.desc())
    items = query.offset(skip).limit(limit).all()

    return items, total


def find_pending_for_product(db: Session, product_id: int, action_type: str) -> Optional[ApprovalRequest]:
    return db.query(ApprovalRequest).filter(
        ApprovalRequest.product_id == product_id,
        ApprovalRequest.action_type == action_type,
        ApprovalRequest.status == ApprovalStatus.pending
    ).first()

def update_status(
    db: Session, 
    request_obj: ApprovalRequest, 
    new_status: ApprovalStatus, 
    rejection_reason: Optional[str] = None,
    decision_reason: Optional[str] = None,
    requested_quantity: Optional[float] = None
) -> ApprovalRequest:
    request_obj.status = new_status
    if rejection_reason is not None:
        request_obj.rejection_reason = rejection_reason
    if decision_reason is not None:
        request_obj.decision_reason = decision_reason
    if requested_quantity is not None:
        request_obj.requested_quantity = requested_quantity
        
    db.commit()
    db.refresh(request_obj)
    return request_obj


def assign_validator(db: Session, request_obj: ApprovalRequest, validator_id: UUID, validated_at) -> ApprovalRequest:
    request_obj.validated_by = validator_id
    request_obj.validated_at = validated_at
    db.commit()
    db.refresh(request_obj)
    return request_obj


def assign_executor(db: Session, request_obj: ApprovalRequest, executor_id: UUID, executed_at) -> ApprovalRequest:
    request_obj.executed_by = executor_id
    request_obj.executed_at = executed_at
    db.commit()
    db.refresh(request_obj)
    return request_obj
