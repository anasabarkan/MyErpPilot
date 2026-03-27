from typing import List, Optional
from uuid import UUID

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
    items = query.order_by(ApprovalRequest.created_at.desc()).offset(skip).limit(limit).all()

    return items, total


def update_status(
    db: Session, 
    request_obj: ApprovalRequest, 
    new_status: ApprovalStatus, 
    rejection_reason: Optional[str] = None
) -> ApprovalRequest:
    request_obj.status = new_status
    if rejection_reason is not None:
        request_obj.rejection_reason = rejection_reason
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
