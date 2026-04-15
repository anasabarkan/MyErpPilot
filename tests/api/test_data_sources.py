from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import data_sources as data_sources_module
from app.api.v1.endpoints.data_sources import router as data_sources_router
from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.data_source import DataSource


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(data_sources_router, prefix="/api/v1/data-sources")

    def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())

    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if not dependant:
            continue
        for dependency in dependant.dependencies:
            call = dependency.call
            if callable(call) and getattr(call, "__name__", "") == "permission_dependency":
                app.dependency_overrides[call] = lambda: None

    return TestClient(app)


def test_payload_connection_test_returns_models(monkeypatch, client: TestClient):
    monkeypatch.setattr(
        data_sources_module.service,
        "test_connection_payload",
        lambda _payload: {
            "success": True,
            "message": "Connection successful.",
            "models": [{"model": "res.partner", "name": "Contact"}],
        },
    )

    response = client.post(
        "/api/v1/data-sources/test-connection",
        json={
            "odoo_url": "http://localhost:8069",
            "database_name": "odoo",
            "username": "admin",
            "api_key": "secret",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["models"][0]["model"] == "res.partner"
    assert "api_key" not in body


def test_active_source_endpoint_shape(monkeypatch, client: TestClient):
    source = DataSource(
        id=uuid4(),
        name="Odoo Prod",
        odoo_url="http://localhost:8069",
        database_name="odoo",
        username="admin",
        api_key_encrypted="encrypted-token",
        created_by=uuid4(),
        is_active=True,
        models_cache=[{"model": "res.partner", "name": "Contact"}],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(data_sources_module.service, "get_active_data_source", lambda _db: source)

    response = client.get("/api/v1/data-sources/active")
    body = response.json()

    assert response.status_code == 200
    assert body["active_data_source"]["name"] == "Odoo Prod"
    assert body["active_data_source"]["api_key_configured"] is True
    assert "api_key" not in body["active_data_source"]

