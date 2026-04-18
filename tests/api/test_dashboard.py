from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import dashboard as dashboard_module
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.integrations.odoo import OdooConnectionError
from app.services.dashboard_service import DashboardSourceNotConfiguredError


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(dashboard_router, prefix="/api/v1/dashboard")

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


def test_dashboard_summary_success(monkeypatch, client: TestClient):
    monkeypatch.setattr(
        dashboard_module.dashboard_service,
        "get_dashboard_summary",
        lambda _db: {
            "source": "odoo",
            "status": "connected",
            "database": "odoo",
            "metrics": {
                "customers": 120,
                "products": 58,
                "quotations": 5,
                "sales_orders": 14,
                "invoices": 9,
                "revenue_total": 12500.5,
                "companies": 1,
                "purchase_orders": 4,
            },
            "low_stock": [{"product": "Acoustic Bloc Screens", "quantity": 2, "min_required": 5, "shortage": 3}],
            "charts": {
                "sales_by_month": [{"month": "2026-03", "revenue": 5000}],
                "top_customers": [{"name": "Agrolait", "revenue": 5200}],
                "top_products": [
                    {
                        "name": "Acoustic Bloc Screens",
                        "short_name": "Acoustic Bloc Screens",
                        "full_name": "[FURN_1111] Acoustic Bloc Screens",
                        "revenue": 150,
                    }
                ],
                "invoice_status": [{"label": "Paid", "value": 6}],
                "low_stock_items": [{"name": "Acoustic Bloc Screens", "qty": 2, "min_required": 5, "shortage": 3}],
            },
            "recent_sales_orders": [],
            "top_customers": [],
            "warnings": [],
        },
    )

    response = client.get("/api/v1/dashboard/summary")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "connected"
    assert body["metrics"]["customers"] == 120


def test_dashboard_summary_missing_active_source(monkeypatch, client: TestClient):
    monkeypatch.setattr(
        dashboard_module.dashboard_service,
        "get_dashboard_summary",
        lambda _db: (_ for _ in ()).throw(
            DashboardSourceNotConfiguredError("No active Odoo source configured.")
        ),
    )

    response = client.get("/api/v1/dashboard/summary")
    body = response.json()

    assert response.status_code == 404
    assert "No active Odoo source configured" in body["detail"]


def test_dashboard_summary_connection_error(monkeypatch, client: TestClient):
    monkeypatch.setattr(
        dashboard_module.dashboard_service,
        "get_dashboard_summary",
        lambda _db: (_ for _ in ()).throw(OdooConnectionError("unable to connect")),
    )

    response = client.get("/api/v1/dashboard/summary")

    assert response.status_code == 502


def test_dashboard_diagnostics_success(monkeypatch, client: TestClient):
    monkeypatch.setattr(
        dashboard_module.dashboard_service,
        "get_dashboard_diagnostics",
        lambda _db: {
            "active_source": {
                "name": "Odoo Active",
                "url": "http://odoo:8069",
                "database": "odoo",
                "username": "admin",
            },
            "models": {
                "res.partner": True,
                "product.product": True,
                "product.template": True,
                "sale.order": True,
                "account.move": True,
            },
            "metric_tests": {
                "customers": {"ok": True, "value": 120, "strategy": "search_count([])", "error": None},
                "products_product": {"ok": True, "value": 58, "strategy": "search_count([])", "error": None},
                "products_template": {"ok": True, "value": 58, "strategy": "search_count([])", "error": None},
                "quotations": {
                    "ok": True,
                    "value": 5,
                    "strategy": "[('state','in',['draft','sent'])]",
                    "error": None,
                },
                "sales_orders": {
                    "ok": True,
                    "value": 14,
                    "strategy": "[('state','in',['sale','done'])]",
                    "error": None,
                },
                "invoices": {
                    "ok": True,
                    "value": 9,
                    "strategy": "[('move_type','=','out_invoice')]",
                    "error": None,
                },
            },
            "warnings": [],
        },
    )

    response = client.get("/api/v1/dashboard/diagnostics")
    body = response.json()

    assert response.status_code == 200
    assert body["active_source"]["name"] == "Odoo Active"
    assert body["metric_tests"]["customers"]["ok"] is True
