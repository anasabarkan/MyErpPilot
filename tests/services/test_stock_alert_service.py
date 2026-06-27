import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.approval import ApprovalStatus
from app.models.product_inventory_state import ProductInventoryState, ProductInventoryStatus
from app.services import stock_alert_service


class FakeQuery:
    def __init__(self, session, model, items):
        self.session = session
        self.model = model
        self.items = list(items)

    def filter_by(self, **kwargs):
        return FakeQuery(
            self.session,
            self.model,
            [item for item in self.items if all(getattr(item, key) == value for key, value in kwargs.items())],
        )

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def offset(self, value):
        return FakeQuery(self.session, self.model, self.items[value:])

    def limit(self, value):
        return FakeQuery(self.session, self.model, self.items[:value])

    def first(self):
        return self.items[0] if self.items else None

    def all(self):
        return list(self.items)

    def count(self):
        return len(self.items)

    def get(self, _id):
        for item in self.items:
            if getattr(item, "id", None) == _id:
                return item
        return None


class FakeDB:
    def __init__(self):
        self.inventory_states = []
        self.approvals = []
        self.users = []
        self.commits = 0

    def query(self, model):
        if model is ProductInventoryState:
            return FakeQuery(self, model, self.inventory_states)
        model_name = getattr(model, "__name__", "")
        if model_name == "ApprovalRequest":
            return FakeQuery(self, model, self.approvals)
        if model_name == "User":
            return FakeQuery(self, model, self.users)
        return FakeQuery(self, model, [])

    def add(self, obj):
        if isinstance(obj, ProductInventoryState):
            if obj not in self.inventory_states:
                self.inventory_states.append(obj)
        else:
            if obj not in self.approvals:
                self.approvals.append(obj)

    def delete(self, obj):
        if obj in self.inventory_states:
            self.inventory_states.remove(obj)
        if obj in self.approvals:
            self.approvals.remove(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        return None

    def close(self):
        return None


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


def _tracked_product(product_id=11, product="Acoustic Bloc Screens"):
    return {"id": product_id, "display_name": product}


def test_low_stock_alert_created_when_quantity_below_threshold():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=4.0)],
        orderpoints=[_orderpoint(min_qty=5.0)],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert len(alerts) == 1
    assert alerts[0]["product_id"] == 11
    assert alerts[0]["company_id"] == 1
    assert alerts[0]["available_quantity"] == 4.0
    assert alerts[0]["daily_sales_velocity"] == 0.0
    assert alerts[0]["days_until_min"] == 0.0
    assert alerts[0]["risk_level"] == "high"


def test_low_stock_alert_not_created_when_quantity_is_above_threshold():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=10.0)],
        orderpoints=[_orderpoint(min_qty=5.0)],
        sales_rows=[],
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


def test_products_without_reorder_rules_use_default_threshold():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=1.0)],
        orderpoints=[],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert len(alerts) == 1
    assert alerts[0]["min_required"] == 5.0
    assert alerts[0]["shortage"] == 4.0


def test_products_without_reorder_rules_use_env_threshold(monkeypatch):
    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_MIN_THRESHOLD", 3)

    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=1.0)],
        orderpoints=[],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert len(alerts) == 1
    assert alerts[0]["min_required"] == 3.0
    assert alerts[0]["shortage"] == 2.0
def test_orderpoint_below_threshold_still_uses_threshold_floor():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=4.0)],
        orderpoints=[_orderpoint(min_qty=3.0)],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert len(alerts) == 1
    assert alerts[0]["min_required"] == 5.0
    assert alerts[0]["available_quantity"] == 4.0


def test_orderpoint_backfills_zero_quantity_product_without_quant():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[],
        orderpoints=[_orderpoint(product_id=21, product="Desk Lamp", min_qty=1.0)],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert alerts == [
        {
            "product_id": 21,
            "company_id": 1,
            "product": "Desk Lamp",
            "available_quantity": 0.0,
            "min_required": 5.0,
            "daily_sales_velocity": 0.0,
            "days_until_min": 0.0,
            "shortage": 5.0,
            "forecast_days": 7,
            "sales_lookback_days": 30,
            "alert_key": "low_stock_forecast:source-1:1:21",
            "risk_level": "high",
        }
    ]


def test_equal_threshold_does_not_create_immediate_alert():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[_quant(quantity=5.0)],
        orderpoints=[],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
    )

    assert alerts == []


def test_tracked_products_without_quant_or_orderpoint_default_to_zero():
    alerts = stock_alert_service.build_low_stock_alert_payloads(
        source_id="source-1",
        stock_quants=[],
        orderpoints=[],
        sales_rows=[],
        forecast_days=7,
        sales_lookback_days=30,
        tracked_products=[_tracked_product(product_id=77, product="Apple iPhone 15 128GB")],
    )

    assert alerts == [
        {
            "product_id": 77,
            "company_id": None,
            "product": "Apple iPhone 15 128GB",
            "available_quantity": 0.0,
            "min_required": 5.0,
            "daily_sales_velocity": 0.0,
            "days_until_min": 0.0,
            "shortage": 5.0,
            "forecast_days": 7,
            "sales_lookback_days": 30,
            "alert_key": "low_stock_forecast:source-1:global:77",
            "risk_level": "high",
        }
    ]


def test_build_product_inventory_state_payloads_tracks_company_available_quantity_and_state():
    payloads = stock_alert_service.build_product_inventory_state_payloads(
        stock_quants=[
            _quant(product_id=11, quantity=5.0, reserved=1.0, company_id=1),
            _quant(product_id=11, quantity=2.0, reserved=0.0, company_id=1),
            _quant(product_id=11, quantity=0.0, reserved=0.0, company_id=2),
        ],
        tracked_products=[_tracked_product(product_id=11)],
    )

    assert payloads == [
        {
            "odoo_product_id": 11,
            "company_id": 1,
            "product_name": "Acoustic Bloc Screens",
            "quantity": 7.0,
            "reserved_quantity": 1.0,
            "available_quantity": 6.0,
            "state": ProductInventoryStatus.in_stock,
        },
        {
            "odoo_product_id": 11,
            "company_id": 2,
            "product_name": "Acoustic Bloc Screens",
            "quantity": 0.0,
            "reserved_quantity": 0.0,
            "available_quantity": 0.0,
            "state": ProductInventoryStatus.out_of_stock,
        },
    ]


def test_build_product_inventory_state_payloads_uses_env_threshold(monkeypatch):
    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_MIN_THRESHOLD", 7)

    payloads = stock_alert_service.build_product_inventory_state_payloads(
        stock_quants=[_quant(product_id=11, quantity=6.0, reserved=0.0, company_id=1)],
        tracked_products=[_tracked_product(product_id=11)],
    )

    assert payloads == [
        {
            "odoo_product_id": 11,
            "company_id": 1,
            "product_name": "Acoustic Bloc Screens",
            "quantity": 6.0,
            "reserved_quantity": 0.0,
            "available_quantity": 6.0,
            "state": ProductInventoryStatus.low_stock,
        }
    ]
def test_scan_syncs_inventory_rows_and_creates_alert_from_state(monkeypatch):
    created_payloads = []
    db = FakeDB()

    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_ENABLED", True)
    monkeypatch.setattr(stock_alert_service, "_get_admin_user", lambda _db: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(
        stock_alert_service,
        "_create_alert_request",
        lambda _db, payload, _requested_by: created_payloads.append(payload) or SimpleNamespace(payload=payload),
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
            [_tracked_product(product_id=77, product="Apple iPhone 15 128GB")]
            if model == "product.product"
            else [],
            None,
        ),
    )

    created = stock_alert_service.scan_low_stock_alerts(db=db)

    assert len(created) == 1
    assert len(db.inventory_states) == 1
    assert db.inventory_states[0].state == ProductInventoryStatus.out_of_stock
    assert created_payloads[0]["state"] == "out_of_stock"
    assert created_payloads[0]["available_quantity"] == 0.0


def test_scan_updates_existing_pending_alert_without_creating_duplicate(monkeypatch):
    created_payloads = []
    db = FakeDB()
    db.approvals.append(
        SimpleNamespace(
            id=uuid4(),
            title="Low stock alert: Acoustic Bloc Screens",
            description="old",
            action_type="low_stock_forecast_alert",
            payload={
                "source_id": "source-1",
                "alert_key": "low_stock_forecast:source-1:1:11",
                "state": "low_stock",
                "available_quantity": 4.0,
                "min_required": 5.0,
                "product": "Acoustic Bloc Screens",
            },
            status=ApprovalStatus.pending,
            product_id=11,
            product_name="Acoustic Bloc Screens",
            current_qty=4.0,
            min_qty=5.0,
            decision_reason=None,
            executed_at=None,
        )
    )

    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_ENABLED", True)
    monkeypatch.setattr(stock_alert_service, "_get_admin_user", lambda _db: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(
        stock_alert_service,
        "_create_alert_request",
        lambda _db, payload, _requested_by: created_payloads.append(payload) or SimpleNamespace(payload=payload),
    )

    resolver = SimpleNamespace(model_exists=lambda _model: True)
    monkeypatch.setattr(
        stock_alert_service.dashboard_service,
        "_build_runtime_context",
        lambda _db: (SimpleNamespace(id="source-1"), object(), 5, resolver, set(), []),
    )

    def fake_search_read(model, *_args, **_kwargs):
        if model == "stock.quant":
            return [_quant(product_id=11, quantity=3.0, reserved=0.0, company_id=1)], None
        if model == "product.product":
            return [_tracked_product(product_id=11)], None
        return [], None

    monkeypatch.setattr(stock_alert_service.dashboard_service, "safe_search_read", fake_search_read)

    created = stock_alert_service.scan_low_stock_alerts(db=db)

    assert created == []
    assert created_payloads == []
    assert len(db.approvals) == 1
    assert db.approvals[0].current_qty == 3.0
    assert db.approvals[0].payload["state"] == "low_stock"


def test_scan_auto_resolves_alert_when_stock_recovers(monkeypatch):
    created_payloads = []
    db = FakeDB()

    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_ENABLED", True)
    monkeypatch.setattr(stock_alert_service, "_get_admin_user", lambda _db: SimpleNamespace(id=uuid4()))

    def fake_create_alert_request(_db, payload, _requested_by):
        created_payloads.append(payload)
        approval = SimpleNamespace(
            id=uuid4(),
            title=f"Low stock alert: {payload['product']}",
            description="generated",
            action_type="low_stock_forecast_alert",
            payload=payload,
            status=ApprovalStatus.pending,
            product_id=payload["product_id"],
            product_name=payload["product"],
            current_qty=payload["available_quantity"],
            min_qty=payload["min_required"],
            decision_reason=None,
            executed_at=None,
        )
        db.approvals.append(approval)
        return approval

    monkeypatch.setattr(stock_alert_service, "_create_alert_request", fake_create_alert_request)

    resolver = SimpleNamespace(model_exists=lambda _model: True)
    monkeypatch.setattr(
        stock_alert_service.dashboard_service,
        "_build_runtime_context",
        lambda _db: (SimpleNamespace(id="source-1"), object(), 5, resolver, set(), []),
    )

    reads = [
        {
            "stock.quant": [_quant(product_id=11, quantity=2.0, reserved=0.0, company_id=1)],
            "product.product": [_tracked_product(product_id=11)],
        },
        {
            "stock.quant": [_quant(product_id=11, quantity=9.0, reserved=0.0, company_id=1)],
            "product.product": [_tracked_product(product_id=11)],
        },
    ]

    def fake_search_read(model, *_args, **_kwargs):
        current = reads[0 if len(created_payloads) == 0 else 1]
        return current.get(model, []), None

    monkeypatch.setattr(stock_alert_service.dashboard_service, "safe_search_read", fake_search_read)
    monkeypatch.setattr(
        stock_alert_service.notification_service if hasattr(stock_alert_service, "notification_service") else stock_alert_service,
        "mark_related_alerts_as_read",
        lambda *args, **kwargs: 0,
        raising=False,
    )

    stock_alert_service.scan_low_stock_alerts(db=db)
    stock_alert_service.scan_low_stock_alerts(db=db)

    assert len(db.approvals) == 1
    assert db.approvals[0].status == ApprovalStatus.executed
    assert db.approvals[0].decision_reason == "Resolved automatically after stock recovered in Odoo."
    assert db.approvals[0].payload["state"] == "in_stock"
    assert db.approvals[0].payload["resolved_automatically"] is True


def test_scan_handles_missing_runtime_without_crashing(monkeypatch):
    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_ENABLED", True)
    monkeypatch.setattr(
        stock_alert_service.dashboard_service,
        "_build_runtime_context",
        lambda _db: (_ for _ in ()).throw(RuntimeError("No active Odoo source configured.")),
    )

    assert stock_alert_service.scan_low_stock_alerts(db=FakeDB()) == []


def test_stock_alert_worker_scans_using_env_minutes(monkeypatch):
    sleep_calls = []
    scan_calls = []
    closed = []

    monkeypatch.setattr(stock_alert_service.settings, "STOCK_ALERT_SCAN_INTERVAL_MINUTES", 12)

    class DummySession:
        def close(self):
            closed.append(True)

    def session_factory():
        return DummySession()

    def fake_scan(db):
        scan_calls.append(db)
        return []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(stock_alert_service, "scan_low_stock_alerts", fake_scan)
    monkeypatch.setattr(stock_alert_service.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stock_alert_service.run_stock_alert_loop(session_factory))

    assert sleep_calls == [720, 720]
    assert len(scan_calls) == 1
    assert len(closed) == 1






