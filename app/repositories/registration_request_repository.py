from uuid import UUID

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.registration_request import (
    RegistrationRequest,
    RegistrationRequestStatus,
)


def create_request(db: Session, data: dict) -> RegistrationRequest:
    request = RegistrationRequest(**data)
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def get_request_by_id(db: Session, request_id: UUID) -> RegistrationRequest | None:
    return db.query(RegistrationRequest).filter(RegistrationRequest.id == request_id).first()


def get_pending_request_by_email(db: Session, email: str) -> RegistrationRequest | None:
    return (
        db.query(RegistrationRequest)
        .filter(
            RegistrationRequest.email == email,
            RegistrationRequest.status == RegistrationRequestStatus.pending,
        )
        .order_by(desc(RegistrationRequest.created_at))
        .first()
    )


def get_latest_request_by_email(db: Session, email: str) -> RegistrationRequest | None:
    return (
        db.query(RegistrationRequest)
        .filter(RegistrationRequest.email == email)
        .order_by(desc(RegistrationRequest.created_at))
        .first()
    )


def list_requests(
    db: Session,
    *,
    status_filter: RegistrationRequestStatus | None = None,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[RegistrationRequest], int]:
    query = db.query(RegistrationRequest)
    if status_filter:
        query = query.filter(RegistrationRequest.status == status_filter)

    total = query.count()
    items = query.order_by(desc(RegistrationRequest.created_at)).offset(skip).limit(limit).all()
    return items, total


def mark_approved(
    db: Session,
    request: RegistrationRequest,
    *,
    reviewer_id: UUID,
) -> RegistrationRequest:
    request.status = RegistrationRequestStatus.approved
    request.rejection_reason = None
    request.reviewed_by = reviewer_id
    from datetime import datetime, timezone

    request.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    return request


def mark_rejected(
    db: Session,
    request: RegistrationRequest,
    *,
    reviewer_id: UUID,
    rejection_reason: str,
) -> RegistrationRequest:
    request.status = RegistrationRequestStatus.rejected
    request.rejection_reason = rejection_reason
    request.reviewed_by = reviewer_id
    from datetime import datetime, timezone

    request.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    return request

