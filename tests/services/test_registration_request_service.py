from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.role import Role
from app.models.registration_request import RegistrationProvider, RegistrationRequestStatus
from app.services import registration_request_service


def test_create_local_registration_request_blocks_duplicate_pending(monkeypatch):
    db = MagicMock()

    monkeypatch.setattr(registration_request_service, "get_user_by_email", lambda _db, _email: None)
    monkeypatch.setattr(
        registration_request_service.registration_request_repository,
        "get_pending_request_by_email",
        lambda _db, _email: SimpleNamespace(id=uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        registration_request_service.create_local_registration_request(
            db,
            email="candidate@example.com",
            full_name="Candidate",
            password="StrongPassword123",
        )

    assert exc.value.status_code == 409
    assert "pending" in str(exc.value.detail).lower()


def test_approve_registration_request_creates_user_and_marks_request(monkeypatch):
    db = MagicMock()
    reviewer_id = uuid4()
    request_id = uuid4()

    pending_request = SimpleNamespace(
        id=request_id,
        email="candidate@example.com",
        full_name="Candidate User",
        provider=RegistrationProvider.local,
        provider_id=None,
        avatar_url=None,
        hashed_password="hashed-password",
        status=RegistrationRequestStatus.pending,
    )
    approved_request = SimpleNamespace(
        id=request_id,
        email="candidate@example.com",
        full_name="Candidate User",
        provider=RegistrationProvider.local,
        provider_id=None,
        avatar_url=None,
        hashed_password="hashed-password",
        status=RegistrationRequestStatus.approved,
        rejection_reason=None,
        reviewed_by=reviewer_id,
        reviewed_at=None,
        created_at=None,
        updated_at=None,
    )
    role = Role(name="viewer")

    monkeypatch.setattr(
        registration_request_service.registration_request_repository,
        "get_request_by_id",
        lambda _db, _request_id: pending_request,
    )
    monkeypatch.setattr(registration_request_service, "get_user_by_email", lambda _db, _email: None)
    monkeypatch.setattr(
        registration_request_service.role_repository,
        "get_role_by_name",
        lambda _db, _role_name: role,
    )
    monkeypatch.setattr(
        registration_request_service.registration_request_repository,
        "mark_approved",
        lambda _db, _request, reviewer_id: approved_request,
    )
    monkeypatch.setattr(
        registration_request_service.audit_service,
        "log_sensitive_crud_event",
        lambda *_args, **_kwargs: None,
    )

    result = registration_request_service.approve_registration_request(
        db,
        request_id=request_id,
        role_name="viewer",
        reviewer_id=reviewer_id,
    )

    assert result.status == RegistrationRequestStatus.approved
    assert db.add.call_count == 1
    created_user = db.add.call_args[0][0]
    assert created_user.email == "candidate@example.com"
    assert created_user.is_active is True
    assert created_user.is_verified is True
    assert created_user.roles == [role]
