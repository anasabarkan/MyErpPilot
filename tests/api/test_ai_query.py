from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import ai as ai_module
from app.api.v1.endpoints.ai import router as ai_router
from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.schemas.ai import AIQueryResponse
from app.services.ai_orchestrator_service import (
    AIIntentNotImplementedError,
    AIUnsupportedQuestionError,
)
from app.services.odoo_query_service import OdooQueryExecutionError, OdooQuerySourceNotConfiguredError


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(ai_router, prefix="/api/v1/ai")

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


def test_ai_query_success(monkeypatch, client: TestClient):
    class _FakeOrchestrator:
        def __init__(self, _db):
            pass

        def query(self, *, question: str, user_id):
            return AIQueryResponse(
                question=question,
                intent="analytics_query",
                source="odoo",
                handler="get_top_customers",
                data=[{"customer_name": "Agrolait", "sales_amount": 2000.0}],
                summary="Here are your top 5 customers based on sales amount.",
                suggested_actions=[],
            )

    monkeypatch.setattr(ai_module, "AIOrchestratorService", _FakeOrchestrator)

    response = client.post("/api/v1/ai/query", json={"question": "Who are my top 5 customers?"})
    body = response.json()

    assert response.status_code == 200
    assert body["intent"] == "analytics_query"
    assert body["handler"] == "get_top_customers"
    assert body["data"][0]["customer_name"] == "Agrolait"


def test_ai_query_no_active_source(monkeypatch, client: TestClient):
    class _FakeOrchestrator:
        def __init__(self, _db):
            pass

        def query(self, *, question: str, user_id):
            raise OdooQuerySourceNotConfiguredError("No active Odoo source configured.")

    monkeypatch.setattr(ai_module, "AIOrchestratorService", _FakeOrchestrator)

    response = client.post("/api/v1/ai/query", json={"question": "top customers"})
    body = response.json()

    assert response.status_code == 404
    assert "No active Odoo source configured" in body["detail"]


def test_ai_query_unsupported_question(monkeypatch, client: TestClient):
    class _FakeOrchestrator:
        def __init__(self, _db):
            pass

        def query(self, *, question: str, user_id):
            raise AIUnsupportedQuestionError("unsupported question")

    monkeypatch.setattr(ai_module, "AIOrchestratorService", _FakeOrchestrator)

    response = client.post("/api/v1/ai/query", json={"question": "Explain employee sentiment"})
    body = response.json()

    assert response.status_code == 422
    assert body["detail"] == "unsupported question"


def test_ai_query_not_implemented_intent(monkeypatch, client: TestClient):
    class _FakeOrchestrator:
        def __init__(self, _db):
            pass

        def query(self, *, question: str, user_id):
            raise AIIntentNotImplementedError("Intent 'dashboard_query' is not implemented.")

    monkeypatch.setattr(ai_module, "AIOrchestratorService", _FakeOrchestrator)

    response = client.post("/api/v1/ai/query", json={"question": "dashboard"})
    body = response.json()

    assert response.status_code == 422
    assert "not implemented" in body["detail"]


def test_ai_query_odoo_failure(monkeypatch, client: TestClient):
    class _FakeOrchestrator:
        def __init__(self, _db):
            pass

        def query(self, *, question: str, user_id):
            raise OdooQueryExecutionError("Odoo failed")

    monkeypatch.setattr(ai_module, "AIOrchestratorService", _FakeOrchestrator)

    response = client.post("/api/v1/ai/query", json={"question": "sales summary"})
    body = response.json()

    assert response.status_code == 502
    assert "Odoo query failed" in body["detail"]

