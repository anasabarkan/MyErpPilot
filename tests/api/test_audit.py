from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import audit as audit_module
from app.api.v1.endpoints.audit import router as audit_router
from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.audit_log import AuditLog
from app.models.user import User


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(audit_router, prefix="/api/v1/audit")

    class _FakeSession:
        def close(self):
            return None

    def override_get_db():
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4(), is_verified=True, roles=[SimpleNamespace(name="admin")])

    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if not dependant:
            continue
        for dependency in dependant.dependencies:
            call = dependency.call
            if callable(call) and getattr(call, "__name__", "") == "permission_dependency":
                app.dependency_overrides[call] = lambda: None

    return TestClient(app)


def _make_log(*, user_display_name: str):
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        user_display_name=user_display_name,
        action="login",
        entity_type="auth",
        entity_id="req_001",
        status="success",
        message="ok",
        metadata_=None,
        ip_address=None,
        user_agent=None,
        created_at=datetime.now(timezone.utc),
    )


def test_list_audit_logs_includes_user_display_name(client: TestClient, monkeypatch):
    def fake_list_logs(_db, **_kwargs):
        return [_make_log(user_display_name="Alice Doe")], 1

    monkeypatch.setattr(audit_module.audit_repository, "list_logs", fake_list_logs)

    response = client.get("/api/v1/audit/logs")
    body = response.json()

    assert response.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["user_display_name"] == "Alice Doe"


def test_list_audit_logs_falls_back_to_system_display_name(client: TestClient, monkeypatch):
    def fake_list_logs(_db, **_kwargs):
        return [_make_log(user_display_name="System")], 1

    monkeypatch.setattr(audit_module.audit_repository, "list_logs", fake_list_logs)

    response = client.get("/api/v1/audit/logs")
    body = response.json()

    assert response.status_code == 200
    assert body["items"][0]["user_display_name"] == "System"


def test_list_audit_logs_keeps_filters_and_returns_new_field(client: TestClient, monkeypatch):
    captured = {}

    def fake_list_logs(_db, **kwargs):
        captured.update(kwargs)
        return [_make_log(user_display_name="Alice Doe")], 1

    monkeypatch.setattr(audit_module.audit_repository, "list_logs", fake_list_logs)

    response = client.get("/api/v1/audit/logs", params={"action": "login", "entity_id": "req_001"})
    body = response.json()

    assert response.status_code == 200
    assert captured["action"] == "login"
    assert captured["entity_id"] == "req_001"
    assert body["items"][0]["user_display_name"] == "Alice Doe"


def test_audit_log_user_display_name_property_fallback_order():
    user = User(email="alice@example.com", full_name="Alice Doe")
    log = AuditLog(action="login", entity_type="auth", status="success")
    log.user = user

    assert log.user_display_name == "Alice Doe"

    user.full_name = ""
    assert log.user_display_name == "alice@example.com"

    log.user = None
    assert log.user_display_name == "System"

