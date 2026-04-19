from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth as auth_module
from app.api.v1.endpoints.auth import router as auth_router
from app.core.dependencies import get_db
from app.models.registration_request import RegistrationProvider, RegistrationRequestStatus
from app.services.auth_service import GoogleCallbackResult


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1")

    def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db

    return TestClient(app)


def test_signup_returns_registration_request_payload(monkeypatch, client: TestClient):
    created_at = datetime.now(timezone.utc)
    request_obj = SimpleNamespace(
        id=uuid4(),
        email="new.user@example.com",
        full_name="New User",
        provider=RegistrationProvider.local,
        status=RegistrationRequestStatus.pending,
        rejection_reason=None,
        reviewed_by=None,
        reviewed_at=None,
        created_at=created_at,
        updated_at=created_at,
    )

    monkeypatch.setattr(auth_module.auth_service, "signup", lambda _db, **_kwargs: request_obj)

    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "new.user@example.com",
            "full_name": "New User",
            "password": "StrongPassword123",
        },
    )
    body = response.json()

    assert response.status_code == 201
    assert body["request"]["email"] == "new.user@example.com"
    assert body["request"]["status"] == "pending"
    assert "Registration request submitted" in body["message"]


def test_google_callback_redirects_with_pending_status(monkeypatch, client: TestClient):
    async def fake_google_callback(_db, *, code: str):
        assert code == "valid-code"
        return GoogleCallbackResult(
            outcome="registration_pending",
            message="Your registration request is pending admin approval.",
        )

    monkeypatch.setattr(auth_module.auth_service, "google_callback", fake_google_callback)

    response = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "valid-code"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    redirect_location = response.headers["location"]
    query = parse_qs(urlparse(redirect_location).query)

    assert query["status"] == ["pending"]
    assert "pending admin approval" in query["message"][0]

