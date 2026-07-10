from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import registration_requests as registration_module
from app.api.v1.endpoints.registration_requests import router as registration_router
from app.core.dependencies import get_current_user, get_db
from app.models.registration_request import RegistrationProvider, RegistrationRequestStatus


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(registration_router, prefix="/api/v1")

    def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4(), is_verified=True, roles=[SimpleNamespace(name="admin")])

    # Bypass permission dependency factory wrappers.
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if not dependant:
            continue
        for dependency in dependant.dependencies:
            call = dependency.call
            if callable(call) and getattr(call, "__name__", "") == "permission_dependency":
                app.dependency_overrides[call] = lambda: None

    return TestClient(app)


def _build_request(status: RegistrationRequestStatus):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        email="candidate@example.com",
        full_name="Candidate User",
        provider=RegistrationProvider.local,
        status=status,
        rejection_reason=None if status != RegistrationRequestStatus.rejected else "Missing details",
        reviewed_by=uuid4() if status != RegistrationRequestStatus.pending else None,
        reviewed_at=now if status != RegistrationRequestStatus.pending else None,
        created_at=now,
        updated_at=now,
    )


def test_list_registration_requests(monkeypatch, client: TestClient):
    request_obj = _build_request(RegistrationRequestStatus.pending)
    monkeypatch.setattr(
        registration_module.registration_request_service,
        "list_registration_requests",
        lambda _db, **_kwargs: ([request_obj], 1),
    )

    response = client.get("/api/v1/registration-requests")
    body = response.json()

    assert response.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["email"] == "candidate@example.com"
    assert body["items"][0]["status"] == "pending"


def test_approve_registration_request(monkeypatch, client: TestClient):
    approved_request = _build_request(RegistrationRequestStatus.approved)
    request_id = str(approved_request.id)

    monkeypatch.setattr(
        registration_module.registration_request_service,
        "approve_registration_request",
        lambda _db, **_kwargs: approved_request,
    )

    response = client.post(
        f"/api/v1/registration-requests/{request_id}/approve",
        json={"role_name": "viewer"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "approved"


def test_reject_registration_request(monkeypatch, client: TestClient):
    rejected_request = _build_request(RegistrationRequestStatus.rejected)
    request_id = str(rejected_request.id)

    monkeypatch.setattr(
        registration_module.registration_request_service,
        "reject_registration_request",
        lambda _db, **_kwargs: rejected_request,
    )

    response = client.post(
        f"/api/v1/registration-requests/{request_id}/reject",
        json={"rejection_reason": "Missing required information"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "Missing details"


