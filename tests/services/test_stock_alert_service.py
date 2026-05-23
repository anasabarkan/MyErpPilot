from types import SimpleNamespace
from uuid import uuid4

from app.services import stock_alert_service


def _quant(product_id=11, product="Acoustic Bloc Screens", quantity=12.0, reserved=0.0, company_id=1):
    return {
        "product_id": [product_id, product],
        "company_id": [company_id, "Main Company"],
        "quantity": quantity,
        "reserved_quantity": reserved,
    }


def _orderpoint(product_id=11, product="Acoustic Bloc Screens", min_qty=5.0, company_id=1):
    return {
        "product_id": [product_id, product],
        "company_id": [company_id, "Main Company"],
        "product_min_qty": min_qty,
    }


def _sales(product_id=11, product="Acoustic Bloc Screens", quantity=30.0):
    return {"product_id": [product_id, product], "product_uom_qty_sum": quantity}


def test_forecast_alert_created_when_stock_reaches_minimum_within_window():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=10.0)],
        orderpoints=[_orderpoint(min_qty=5.0)],
        sales_rows=[_sales(quantity=30.0)],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert len(alerts) == 1
    assert alerts[0]["product_id"] == 11
    assert alerts[0]["available_quantity"] == 10.0
    assert alerts[0]["daily_sales_velocity"] == 1.0
    assert alerts[0]["days_until_min"] == 5.0
    assert alerts[0]["risk_level"] == "medium"


def test_forecast_alert_not_created_when_depletion_is_beyond_window():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=30.0)],
        orderpoints=[_orderpoint(min_qty=5.0)],
        sales_rows=[_sales(quantity=30.0)],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert alerts == []


def test_already_low_stock_alert_created_without_sales_velocity():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=3.0)],
        orderpoints=[_orderpoint(min_qty=5.0)],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert len(alerts) == 1
    assert alerts[0]["days_until_min"] == 0.0
    assert alerts[0]["shortage"] == 2.0
    assert alerts[0]["risk_level"] == "high"


def test_products_without_reorder_rules_are_skipped():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=1.0)],
        orderpoints=[],
        sales_rows=[_sales(quantity=60.0)],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert alerts == []


def test_scan_deduplicates_existing_pending_alert(monkeypatch):
    created_payloads = []

    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_ENABLED", True)
    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_FORECAST_DAYS", 7)
    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_SALES_LOOKBACK_DAYS", 30)
    monkeypatch.setattr(stock_alert_service, "_get_admin_user", lambda _db: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(stock_alert_service, "_pending_alert_exists", lambda _db, _alert_key: True)
    monkeypatch.setattr(
        stock_alert_service,
        "_create_alert_request",
        lambda _db, payload, _requested_by: created_payloads.append(payload),
    )

    resolver = SimpleNamespace(model_exists=lambda _model: True)
    monkeypatch.setattr(
        stock_alert_service.dashboard_service,
        "_build_runtime_context",
        lambda _db: (SimpleNamespace(id="source-1"), object(), 5, resolver, set(), []),
    )
    monkeypatch.setattr(
        stock_alert_service.dashboard_service,
        "safe_search_read",
        lambda model, *_args, **_kwargs: (
            [_quant(quantity=10.0)] if model == "stock.quant" else [_orderpoint(min_qty=5.0)],
            None,
        ),
    )
    monkeypatch.setattr(
        stock_alert_service.dashboard_service,
        "safe_read_group",
        lambda *_args, **_kwargs: ([_sales(quantity=30.0)], None),
    )

    assert stock_alert_service.scan_low_stock_alerts(db=object()) == []
    assert created_payloads == []


def test_scan_handles_missing_runtime_without_crashing(monkeypatch):
    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_ENABLED", True)
    monkeypatch.setattr(stock_alert_service, "_get_admin_user", lambda _db: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(
        stock_alert_service.dashboard_service,
        "_build_runtime_context",
        lambda _db: (_ for _ in ()).throw(RuntimeError("No active Odoo source configured.")),
    )

    assert stock_alert_service.scan_low_stock_alerts(db=object()) == []
