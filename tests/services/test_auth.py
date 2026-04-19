from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models.registration_request import RegistrationRequestStatus
from app.services import auth_service
from app.services.registration_request_service import RegistrationStatusHint


def test_signup_creates_registration_request(monkeypatch):
    db = MagicMock()
    created_request = SimpleNamespace(id="req-1")

    captured = {}

    def fake_create_local_registration_request(_db, **kwargs):
        captured.update(kwargs)
        return created_request

    monkeypatch.setattr(
        auth_service.registration_request_service,
        "create_local_registration_request",
        fake_create_local_registration_request,
    )

    result = auth_service.signup(
        db,
        email="new.user@example.com",
        password="SuperSecret123",
        full_name="New User",
    )

    assert result is created_request
    assert captured == {
        "email": "new.user@example.com",
        "password": "SuperSecret123",
        "full_name": "New User",
    }


def test_authenticate_returns_pending_message(monkeypatch):
    db = MagicMock()

    monkeypatch.setattr(auth_service, "get_user_by_email", lambda _db, _email: None)
    monkeypatch.setattr(
        auth_service.registration_request_service,
        "get_registration_status_hint_for_login",
        lambda _db, email: RegistrationStatusHint(
            status=RegistrationRequestStatus.pending,
            message=f"Pending approval for {email}",
        ),
    )

    with pytest.raises(HTTPException) as exc:
        auth_service.authenticate(db, email="pending@example.com", password="irrelevant")

    assert exc.value.status_code == 403
    assert "Pending approval" in str(exc.value.detail)


def test_authenticate_returns_rejected_message(monkeypatch):
    db = MagicMock()

    monkeypatch.setattr(auth_service, "get_user_by_email", lambda _db, _email: None)
    monkeypatch.setattr(
        auth_service.registration_request_service,
        "get_registration_status_hint_for_login",
        lambda _db, **_kwargs: RegistrationStatusHint(
            status=RegistrationRequestStatus.rejected,
            message="Registration rejected.",
            rejection_reason="Missing required company details.",
        ),
    )

    with pytest.raises(HTTPException) as exc:
        auth_service.authenticate(db, email="rejected@example.com", password="irrelevant")

    assert exc.value.status_code == 403
    assert "Registration rejected" in str(exc.value.detail)
    assert "Missing required company details" in str(exc.value.detail)


def test_authenticate_invalid_credentials_when_no_registration_hint(monkeypatch):
    db = MagicMock()

    monkeypatch.setattr(auth_service, "get_user_by_email", lambda _db, _email: None)
    monkeypatch.setattr(
        auth_service.registration_request_service,
        "get_registration_status_hint_for_login",
        lambda _db, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as exc:
        auth_service.authenticate(db, email="unknown@example.com", password="wrong")

    assert exc.value.status_code == 401
    assert "Invalid email or password" in str(exc.value.detail)
