from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.registration_request import (
    RegistrationProvider,
    RegistrationRequest,
    RegistrationRequestStatus,
)
from app.models.user import User
from app.repositories import registration_request_repository, role_repository
from app.services import audit_service
from app.services.user_service import get_user_by_email


PENDING_APPROVAL_MESSAGE = "Your registration request is pending admin approval."


@dataclass
class RegistrationStatusHint:
    status: RegistrationRequestStatus
    message: str
    rejection_reason: str | None = None


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def create_local_registration_request(
    db: Session,
    *,
    email: str,
    full_name: str,
    password: str,
) -> RegistrationRequest:
    normalized_email = _normalize_email(email)

    if get_user_by_email(db, normalized_email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    pending_request = registration_request_repository.get_pending_request_by_email(db, normalized_email)
    if pending_request:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=PENDING_APPROVAL_MESSAGE,
        )

    request = registration_request_repository.create_request(
        db,
        {
            "email": normalized_email,
            "full_name": full_name.strip(),
            "provider": RegistrationProvider.local,
            "hashed_password": hash_password(password),
            "status": RegistrationRequestStatus.pending,
        },
    )
    audit_service.log_auth_event(
        db,
        action="signup_request",
        status="success",
        message=f"Local registration request created ({normalized_email}).",
    )
    return request


def create_google_registration_request(
    db: Session,
    *,
    email: str,
    full_name: str,
    provider_id: str,
    avatar_url: str | None,
) -> RegistrationRequest:
    normalized_email = _normalize_email(email)

    if get_user_by_email(db, normalized_email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    pending_request = registration_request_repository.get_pending_request_by_email(db, normalized_email)
    if pending_request:
        # Keep pending request fresh with latest Google profile information.
        pending_request.full_name = full_name.strip()
        pending_request.provider_id = provider_id
        pending_request.avatar_url = avatar_url
        db.commit()
        db.refresh(pending_request)
        return pending_request

    request = registration_request_repository.create_request(
        db,
        {
            "email": normalized_email,
            "full_name": full_name.strip(),
            "provider": RegistrationProvider.google,
            "provider_id": provider_id,
            "avatar_url": avatar_url,
            "status": RegistrationRequestStatus.pending,
        },
    )
    audit_service.log_auth_event(
        db,
        action="google_signup_request",
        status="success",
        message=f"Google registration request created ({normalized_email}).",
    )
    return request


def get_registration_status_hint_for_login(
    db: Session,
    *,
    email: str,
) -> RegistrationStatusHint | None:
    normalized_email = _normalize_email(email)
    latest_request = registration_request_repository.get_latest_request_by_email(db, normalized_email)
    if not latest_request:
        return None

    if latest_request.status == RegistrationRequestStatus.pending:
        return RegistrationStatusHint(
            status=RegistrationRequestStatus.pending,
            message=PENDING_APPROVAL_MESSAGE,
        )

    if latest_request.status == RegistrationRequestStatus.rejected:
        reason = latest_request.rejection_reason or "No reason provided by administrator."
        return RegistrationStatusHint(
            status=RegistrationRequestStatus.rejected,
            message=(
                "Your registration request was rejected. "
                "You can submit a new registration request."
            ),
            rejection_reason=reason,
        )

    return None


def list_registration_requests(
    db: Session,
    *,
    status_filter: RegistrationRequestStatus | None,
    skip: int,
    limit: int,
) -> tuple[list[RegistrationRequest], int]:
    return registration_request_repository.list_requests(
        db,
        status_filter=status_filter,
        skip=skip,
        limit=limit,
    )


def get_registration_request(
    db: Session,
    *,
    request_id: UUID,
) -> RegistrationRequest | None:
    return registration_request_repository.get_request_by_id(db, request_id)


def approve_registration_request(
    db: Session,
    *,
    request_id: UUID,
    role_name: str,
    reviewer_id: UUID,
) -> RegistrationRequest:
    request = registration_request_repository.get_request_by_id(db, request_id)
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registration request not found.",
        )
    if request.status != RegistrationRequestStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending registration requests can be approved.",
        )

    if get_user_by_email(db, request.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    role = role_repository.get_role_by_name(db, role_name)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_name}' not found.",
        )
    if role.name == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The admin role cannot be assigned from this endpoint.",
        )

    user = User(
        email=request.email,
        full_name=request.full_name,
        hashed_password=request.hashed_password,
        provider=request.provider.value,
        provider_id=request.provider_id,
        avatar_url=request.avatar_url,
        is_active=True,
        is_verified=True,
        roles=[role],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    request = registration_request_repository.mark_approved(
        db,
        request,
        reviewer_id=reviewer_id,
    )
    audit_service.log_sensitive_crud_event(
        db,
        action="approve_registration_request",
        entity_type="registration_request",
        entity_id=str(request.id),
        user_id=reviewer_id,
        metadata={"created_user_id": str(user.id), "role_name": role_name},
    )
    return request


def reject_registration_request(
    db: Session,
    *,
    request_id: UUID,
    rejection_reason: str,
    reviewer_id: UUID,
) -> RegistrationRequest:
    request = registration_request_repository.get_request_by_id(db, request_id)
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registration request not found.",
        )
    if request.status != RegistrationRequestStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending registration requests can be rejected.",
        )

    request = registration_request_repository.mark_rejected(
        db,
        request,
        reviewer_id=reviewer_id,
        rejection_reason=rejection_reason.strip(),
    )
    audit_service.log_sensitive_crud_event(
        db,
        action="reject_registration_request",
        entity_type="registration_request",
        entity_id=str(request.id),
        user_id=reviewer_id,
        metadata={"rejection_reason": rejection_reason.strip()},
    )
    return request

