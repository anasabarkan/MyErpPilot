from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import approvals as approvals_module
from app.api.v1.endpoints import data_sources as data_sources_module
from app.api.v1.endpoints.approvals import router as approvals_router
from app.api.v1.endpoints.data_sources import router as data_sources_router
from app.api.v1.endpoints.registration_requests import router as registration_router
from app.core.dependencies import get_current_user
from app.database.session import get_db


def build_client(*, router, prefix: str, role_name: str) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid4(),
        is_verified=True,
        roles=[SimpleNamespace(name=role_name)],
    )

    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if not dependant:
            continue
        for dependency in dependant.dependencies:
            call = dependency.call
            if callable(call) and getattr(call, "__name__", "") == "permission_dependency":
                app.dependency_overrides[call] = lambda: None

    return TestClient(app)


def test_viewer_cannot_access_data_sources_list():
    client = build_client(router=data_sources_router, prefix="/api/v1/data-sources", role_name="viewer")

    response = client.get("/api/v1/data-sources/")

    assert response.status_code == 403
    assert "role privileges" in response.json()["detail"]


def test_manager_cannot_access_signup_approvals():
    client = build_client(router=registration_router, prefix="/api/v1", role_name="manager")

    response = client.get("/api/v1/registration-requests")

    assert response.status_code == 403
    assert "role privileges" in response.json()["detail"]


def test_manager_cannot_execute_approved_actions():
    client = build_client(router=approvals_router, prefix="/api/v1/approvals", role_name="manager")

    response = client.post(f"/api/v1/approvals/{uuid4()}/execute")

    assert response.status_code == 403
    assert "role privileges" in response.json()["detail"]


def test_operator_can_read_only_execution_visible_statuses(monkeypatch):
    client = build_client(router=approvals_router, prefix="/api/v1/approvals", role_name="operator")
    monkeypatch.setattr(approvals_module.approval_service, "list_requests", lambda *_args, **_kwargs: ([], 0))

    approved_response = client.get("/api/v1/approvals", params={"status_filter": "approved"})
    pending_response = client.get("/api/v1/approvals", params={"status_filter": "pending"})

    assert approved_response.status_code == 200
    assert pending_response.status_code == 403
    assert "approved, executed, or failed" in pending_response.json()["detail"]
