from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.integrations.odoo import OdooRequestError
from app.models.data_source import DataSource
from app.services import dashboard_service


def _active_source() -> DataSource:
    return DataSource(
        id=uuid4(),
        name="Odoo Active",
        odoo_url="http://odoo:8069",
        database_name="odoo",
        username="admin",
        api_key_encrypted="encrypted-token",
        created_by=uuid4(),
        is_active=True,
        models_cache=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class FakeDashboardClient:
    def authenticate(self) -> int:
        return 5

    def list_models(self, user_id: int) -> list[dict[str, str]]:
        assert user_id == 5
        return [
            {"model": "res.partner", "name": "Contacts"},
            {"model": "product.product", "name": "Products"},
            {"model": "product.template", "name": "Product Templates"},
            {"model": "res.company", "name": "Companies"},
            {"model": "sale.order", "name": "Sales Orders"},
            {"model": "sale.order.line", "name": "Sales Order Lines"},
            {"model": "account.move", "name": "Journal Entries"},
            {"model": "purchase.order", "name": "Purchase Orders"},
            {"model": "stock.quant", "name": "Stock Quants"},
        ]

    def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
        assert user_id == 5
        domain = domain or []
        assert isinstance(domain, list)

        if model == "ir.model":
            target_model = None
            for term in domain:
                if isinstance(term, tuple) and len(term) == 3 and term[0] == "model" and term[1] == "=":
                    target_model = term[2]
            available = {item["model"] for item in self.list_models(user_id)}
            return 1 if isinstance(target_model, str) and target_model in available else 0

        if model == "res.partner":
            if ("is_company", "=", True) in domain:
                return 65
            return 120
        if model == "product.product":
            return 58
        if model == "product.template":
            return 58
        if model == "res.company":
            return 1
        if model == "purchase.order":
            return 4
        if model == "stock.quant":
            return 3
        if model == "sale.order":
            if ("state", "in", ["draft", "sent"]) in domain:
                return 5
            if ("state", "in", ["sale", "done"]) in domain:
                return 14
            return 20
        if model == "account.move":
            if ("move_type", "=", "out_invoice") in domain:
                return 9
            payment_domain = [term for term in domain if isinstance(term, tuple) and term[0] == "payment_state"]
            if payment_domain and payment_domain[0][2] == "paid":
                return 6
            if payment_domain:
                return 3
            return 12

        return 0

    def execute_kw(
        self,
        *,
        user_id: int,
        model: str,
        method: str,
        args: list[object] | None = None,
        kwargs: dict[str, object] | None = None,
    ) -> object:
        assert user_id == 5
        assert isinstance(args, list)

        if method == "fields_get":
            if model == "res.partner":
                return {"is_company": {"type": "boolean"}}
            return {}

        assert method == "read_group"
        if model == "sale.order":
            groupby = args[2] if len(args) >= 3 else []
            if groupby == []:
                return [{"amount_total_sum": 12800.75}]
            if groupby == ["date_order:month"]:
                return [
                    {"date_order:month": "2026-03-01", "amount_total_sum": 5000.0},
                    {"date_order:month": "2026-04-01", "amount_total_sum": 7800.75},
                ]
            if groupby == ["partner_id"]:
                return [
                    {"partner_id": [3, "Agrolait"], "amount_total_sum": 5200.0},
                    {"partner_id": [7, "Azure Interior"], "amount_total_sum": 3000.0},
                ]
        if model == "sale.order.line":
            return [
                {"product_id": [11, "Product A"], "product_uom_qty_sum": 150.0},
                {"product_id": [12, "Product B"], "product_uom_qty_sum": 110.0},
            ]
        if model == "stock.quant":
            return [
                {"product_id": [11, "Product A"], "quantity_sum": 2.0},
                {"product_id": [12, "Product B"], "quantity_sum": 1.0},
            ]
        return []

    def search_read(
        self,
        *,
        user_id: int,
        model: str,
        domain: list[object] | None = None,
        fields: list[str] | None = None,
        limit: int = 80,
        order: str | None = None,
    ) -> list[dict[str, object]]:
        assert user_id == 5
        assert isinstance(domain, list)
        assert isinstance(fields, list)
        if model == "sale.order":
            return [
                {
                    "id": 10,
                    "name": "SO010",
                    "partner_id": [3, "Agrolait"],
                    "amount_total": 2400.5,
                    "state": "sale",
                    "date_order": "2026-04-15 10:00:00",
                },
                {
                    "id": 11,
                    "name": "SO011",
                    "partner_id": [7, "Azure Interior"],
                    "amount_total": 5200.0,
                    "state": "done",
                    "date_order": "2026-03-20 08:10:00",
                },
            ]
        if model == "sale.order.line":
            return [
                {"product_id": [11, "Product A"], "price_total": 1500.0},
                {"product_id": [12, "Product B"], "price_total": 1100.0},
            ]
        if model == "product.product":
            product_ids = set()
            for term in domain or []:
                if (
                    isinstance(term, tuple)
                    and len(term) == 3
                    and term[0] == "id"
                    and term[1] == "in"
                    and isinstance(term[2], list)
                ):
                    product_ids = {value for value in term[2] if isinstance(value, int)}
            rows: list[dict[str, object]] = []
            if 11 in product_ids:
                rows.append(
                    {
                        "id": 11,
                        "display_name": "[FURN_1111] Acoustic Bloc Screens",
                        "name": "Acoustic Bloc Screens",
                        "product_tmpl_id": [111, "Acoustic Bloc Screens"],
                    }
                )
            if 12 in product_ids:
                rows.append(
                    {
                        "id": 12,
                        "display_name": "[FURN_2222] Office Chair",
                        "name": "Office Chair",
                        "product_tmpl_id": [112, "Office Chair"],
                    }
                )
            return rows
        if model == "product.template":
            template_ids = set()
            for term in domain or []:
                if (
                    isinstance(term, tuple)
                    and len(term) == 3
                    and term[0] == "id"
                    and term[1] == "in"
                    and isinstance(term[2], list)
                ):
                    template_ids = {value for value in term[2] if isinstance(value, int)}
            rows: list[dict[str, object]] = []
            if 111 in template_ids:
                rows.append({"id": 111, "display_name": "Acoustic Bloc Screens", "name": "Acoustic Bloc Screens"})
            if 112 in template_ids:
                rows.append({"id": 112, "display_name": "Office Chair", "name": "Office Chair"})
            return rows
        if model == "res.partner":
            return [
                {"id": 3, "name": "Agrolait"},
                {"id": 7, "name": "Azure Interior"},
            ]
        if model == "stock.quant":
            return [
                {"product_id": [11, "Acoustic Bloc Screens"], "quantity": 2.0},
                {"product_id": [12, "Office Chair"], "quantity": 12.0},
            ]
        if model == "stock.warehouse.orderpoint":
            return [
                {"product_id": [11, "Acoustic Bloc Screens"], "product_min_qty": 5.0},
                {"product_id": [12, "Office Chair"], "product_min_qty": 3.0},
            ]
        return []


def _patch_dashboard_context(monkeypatch, client: object):
    source = _active_source()
    monkeypatch.setattr(dashboard_service.data_source_repo, "get_active_data_source", lambda _db: source)
    monkeypatch.setattr(dashboard_service, "decrypt_value", lambda _value: "plain-api-key")
    monkeypatch.setattr(dashboard_service, "_build_client", lambda _source, _api_key: client)


def test_get_dashboard_summary_success(monkeypatch):
    _patch_dashboard_context(monkeypatch, FakeDashboardClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]

    assert summary["source"] == "odoo"
    assert summary["status"] == "connected"
    assert summary["database"] == "odoo"
    assert summary["metrics"]["customers"] == 120
    assert summary["metrics"]["products"] == 58
    assert summary["metrics"]["companies"] == 1
    assert summary["metrics"]["sales_orders"] == 14
    assert summary["metrics"]["invoices"] == 9
    assert summary["metrics"]["revenue_total"] == 12800.75
    assert len(summary["charts"]["sales_by_month"]) == 2
    assert len(summary["charts"]["top_products"]) == 2
    assert summary["charts"]["top_products"][0]["short_name"] == "Acoustic Bloc Screens"
    assert summary["charts"]["top_products"][0]["full_name"] == "[FURN_1111] Acoustic Bloc Screens"
    assert len(summary["recent_sales_orders"]) == 2
    assert len(summary["low_stock"]) == 1
    assert summary["low_stock"][0]["product"] == "Acoustic Bloc Screens"
    assert summary["low_stock"][0]["quantity"] == 2.0
    assert summary["low_stock"][0]["min_required"] == 5.0
    assert summary["low_stock"][0]["shortage"] == 3.0
    assert summary["charts"]["low_stock_items"][0]["shortage"] == 3.0
    assert summary["warnings"] == []


def test_get_dashboard_analytics_success(monkeypatch):
    _patch_dashboard_context(monkeypatch, FakeDashboardClient())
    analytics = dashboard_service.get_dashboard_analytics(db=object(), range_key="all")  # type: ignore[arg-type]

    assert analytics["source"] == "odoo"
    assert analytics["status"] == "connected"
    assert analytics["database"] == "odoo"
    assert analytics["range"] == "all"
    assert analytics["metrics"]["revenue"] == 7600.5
    assert analytics["metrics"]["sales_orders"] == 2
    assert analytics["metrics"]["average_order_value"] == 3800.25
    assert analytics["metrics"]["customers"] == 120
    assert analytics["metrics"]["products"] == 58
    assert analytics["charts"]["revenue_trend"]
    assert analytics["charts"]["top_customers"][0]["name"] == "Azure Interior"
    assert analytics["charts"]["top_products"][0]["name"] == "Product A"
    assert analytics["charts"]["product_categories"][0]["name"] == "Uncategorized"
    assert analytics["charts"]["stock_availability"][0]["product"] == "Office Chair"


def test_get_dashboard_analytics_partial_warning(monkeypatch):
    class PurchaseMissingClient(FakeDashboardClient):
        def list_models(self, user_id: int) -> list[dict[str, str]]:
            return [item for item in super().list_models(user_id) if item["model"] != "purchase.order"]

        def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
            if model == "purchase.order":
                raise OdooRequestError("Odoo request failed: Object purchase.order doesn't exist")
            return super().search_count(user_id=user_id, model=model, domain=domain)

    _patch_dashboard_context(monkeypatch, PurchaseMissingClient())
    analytics = dashboard_service.get_dashboard_analytics(db=object(), range_key="90d")  # type: ignore[arg-type]

    assert analytics["metrics"]["purchase_spend"] == 0
    assert any("Purchase data unavailable" in warning for warning in analytics["warnings"])


def test_get_dashboard_summary_without_active_source(monkeypatch):
    monkeypatch.setattr(dashboard_service.data_source_repo, "get_active_data_source", lambda _db: None)
    with pytest.raises(dashboard_service.DashboardSourceNotConfiguredError):
        dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]


def test_customer_rank_never_used(monkeypatch):
    class CustomerRankForbiddenClient(FakeDashboardClient):
        def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
            for term in domain or []:
                if isinstance(term, tuple) and term and term[0] == "customer_rank":
                    raise AssertionError("customer_rank must not be used")
            return super().search_count(user_id=user_id, model=model, domain=domain)

        def search_read(
            self,
            *,
            user_id: int,
            model: str,
            domain: list[object] | None = None,
            fields: list[str] | None = None,
            limit: int = 80,
            order: str | None = None,
        ) -> list[dict[str, object]]:
            if fields and "customer_rank" in fields:
                raise AssertionError("customer_rank field must not be requested")
            return super().search_read(
                user_id=user_id,
                model=model,
                domain=domain,
                fields=fields,
                limit=limit,
                order=order,
            )

    _patch_dashboard_context(monkeypatch, CustomerRankForbiddenClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]
    assert summary["metrics"]["customers"] == 120


def test_product_product_missing_falls_back_to_template(monkeypatch):
    class ProductProductMissingClient(FakeDashboardClient):
        def list_models(self, user_id: int) -> list[dict[str, str]]:
            return [item for item in super().list_models(user_id) if item["model"] != "product.product"]

        def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
            if model == "product.product":
                raise OdooRequestError("Odoo request failed: Object product.product doesn't exist")
            if model == "product.template":
                return 42
            return super().search_count(user_id=user_id, model=model, domain=domain)

    _patch_dashboard_context(monkeypatch, ProductProductMissingClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]
    assert summary["metrics"]["products"] == 42


def test_product_template_missing_uses_product_product(monkeypatch):
    class ProductTemplateMissingClient(FakeDashboardClient):
        def list_models(self, user_id: int) -> list[dict[str, str]]:
            return [item for item in super().list_models(user_id) if item["model"] != "product.template"]

        def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
            if model == "product.template":
                raise OdooRequestError("Odoo request failed: Object product.template doesn't exist")
            return super().search_count(user_id=user_id, model=model, domain=domain)

    _patch_dashboard_context(monkeypatch, ProductTemplateMissingClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]
    assert summary["metrics"]["products"] == 58


def test_products_module_missing_returns_zero_and_warning(monkeypatch):
    class ProductsMissingClient(FakeDashboardClient):
        def list_models(self, user_id: int) -> list[dict[str, str]]:
            return [item for item in super().list_models(user_id) if item["model"] not in {"product.product", "product.template"}]

        def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
            if model in {"product.product", "product.template"}:
                raise OdooRequestError(f"Odoo request failed: Object {model} doesn't exist")
            return super().search_count(user_id=user_id, model=model, domain=domain)

    _patch_dashboard_context(monkeypatch, ProductsMissingClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]
    assert summary["metrics"]["products"] == 0
    assert any("Products data unavailable because the Products/Inventory module is not installed." in w for w in summary["warnings"])


def test_sale_order_missing_returns_partial_summary(monkeypatch):
    class SalesMissingClient(FakeDashboardClient):
        def list_models(self, user_id: int) -> list[dict[str, str]]:
            return [item for item in super().list_models(user_id) if item["model"] not in {"sale.order", "sale.order.line"}]

        def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
            if model in {"sale.order", "sale.order.line"}:
                raise OdooRequestError(f"Odoo request failed: Object {model} doesn't exist")
            return super().search_count(user_id=user_id, model=model, domain=domain)

    _patch_dashboard_context(monkeypatch, SalesMissingClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]

    assert summary["status"] == "connected"
    assert summary["metrics"]["quotations"] == 0
    assert summary["metrics"]["sales_orders"] == 0
    assert summary["metrics"]["revenue_total"] == 0.0
    assert summary["charts"]["sales_by_month"] == []
    assert any("Sales data unavailable because the Sales module is not installed." in w for w in summary["warnings"])


def test_dashboard_diagnostics_contains_detailed_metric_tests(monkeypatch):
    class DiagnosticsFailureClient(FakeDashboardClient):
        def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
            if model == "product.product":
                raise OdooRequestError("Odoo request failed: AccessError: product.product denied.")
            return super().search_count(user_id=user_id, model=model, domain=domain)

    _patch_dashboard_context(monkeypatch, DiagnosticsFailureClient())
    diagnostics = dashboard_service.get_dashboard_diagnostics(db=object())  # type: ignore[arg-type]

    assert diagnostics["active_source"]["name"] == "Odoo Active"
    assert diagnostics["models"]["res.partner"] is True
    assert diagnostics["metric_tests"]["customers"]["ok"] is True
    assert diagnostics["metric_tests"]["products_product"]["ok"] is False
    assert diagnostics["metric_tests"]["products_template"]["ok"] is True


def test_low_stock_uses_orderpoint_and_defaults_missing_quant_to_zero(monkeypatch):
    class MissingQuantClient(FakeDashboardClient):
        def search_read(
            self,
            *,
            user_id: int,
            model: str,
            domain: list[object] | None = None,
            fields: list[str] | None = None,
            limit: int = 80,
            order: str | None = None,
        ) -> list[dict[str, object]]:
            if model == "stock.quant":
                return []
            if model == "stock.warehouse.orderpoint":
                return [{"product_id": [21, "Desk Lamp"], "product_min_qty": 5.0}]
            return super().search_read(
                user_id=user_id,
                model=model,
                domain=domain,
                fields=fields,
                limit=limit,
                order=order,
            )

    _patch_dashboard_context(monkeypatch, MissingQuantClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]
    assert summary["low_stock"] == [{"product": "Desk Lamp", "quantity": 0.0, "min_required": 5.0, "shortage": 5.0}]


def test_low_stock_skips_products_without_reorder_rules(monkeypatch):
    class NoOrderPointClient(FakeDashboardClient):
        def search_read(
            self,
            *,
            user_id: int,
            model: str,
            domain: list[object] | None = None,
            fields: list[str] | None = None,
            limit: int = 80,
            order: str | None = None,
        ) -> list[dict[str, object]]:
            if model == "stock.quant":
                return [{"product_id": [99, "No Rule Item"], "quantity": 1.0}]
            if model == "stock.warehouse.orderpoint":
                return []
            return super().search_read(
                user_id=user_id,
                model=model,
                domain=domain,
                fields=fields,
                limit=limit,
                order=order,
            )

    _patch_dashboard_context(monkeypatch, NoOrderPointClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]
    assert summary["low_stock"] == []


def test_low_stock_uses_company_scoped_quantities_to_avoid_false_zero(monkeypatch):
    class CompanyScopedQuantClient(FakeDashboardClient):
        def search_read(
            self,
            *,
            user_id: int,
            model: str,
            domain: list[object] | None = None,
            fields: list[str] | None = None,
            limit: int = 80,
            order: str | None = None,
        ) -> list[dict[str, object]]:
            if model == "stock.quant":
                assert ("location_id.usage", "=", "internal") in (domain or [])
                assert ("quantity", "!=", 0) in (domain or [])
                return [
                    {"product_id": [11, "Acoustic Bloc Screens"], "company_id": [1, "Main Company"], "quantity": 2.0},
                    {"product_id": [11, "Acoustic Bloc Screens"], "company_id": [2, "Other Company"], "quantity": -2.0},
                ]
            if model == "stock.warehouse.orderpoint":
                return [
                    {"product_id": [11, "Acoustic Bloc Screens"], "company_id": [1, "Main Company"], "product_min_qty": 5.0}
                ]
            return super().search_read(
                user_id=user_id,
                model=model,
                domain=domain,
                fields=fields,
                limit=limit,
                order=order,
            )

    _patch_dashboard_context(monkeypatch, CompanyScopedQuantClient())
    summary = dashboard_service.get_dashboard_summary(db=object())  # type: ignore[arg-type]
    assert summary["low_stock"] == [
        {"product": "Acoustic Bloc Screens", "quantity": 2.0, "min_required": 5.0, "shortage": 3.0}
    ]
