from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.approval import ApprovalStatus
from app.models.product_inventory_state import ProductInventoryStatus
from app.services import approval_service
from app.schemas.approval import ApproveRequest
from app.services.purchase_order_service import MockPurchaseOrderService


@pytest.fixture
def mock_db():
    class MockDB:
        def __init__(self):
            self.commits = 0
            self.refreshes = 0
            self.rollbacks = 0
            self.added = []

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            self.commits += 1

        def refresh(self, obj):
            self.refreshes += 1

        def rollback(self):
            self.rollbacks += 1

        def query(self, model):
            class MockQuery:
                def filter(self, *args, **kwargs):
                    return self

                def first(self):
                    return None

                def get(self, _id):
                    return None

                def all(self):
                    return []

            return MockQuery()

    return MockDB()


def test_approve_low_stock_only_marks_request_approved(monkeypatch, mock_db):
    class MockRequest:
        id = uuid4()
        status = ApprovalStatus.pending
        action_type = "low_stock_forecast_alert"
        product_id = 123
        product_name = "Test Product"
        requested_quantity = None
        decision_reason = None

    mock_req = MockRequest()

    import app.repositories.approval_repository as repo
    monkeypatch.setattr(repo, "get_request_by_id", lambda db, rid: mock_req)
    monkeypatch.setattr(repo, "update_status", lambda db, req, status, **kwargs: setattr(req, "status", status))
    monkeypatch.setattr(repo, "assign_validator", lambda db, req, validator_id, validated_at: setattr(req, "validated_by", validator_id))

    import app.services.audit_service as audit
    monkeypatch.setattr(audit, "log_approval_event", lambda *args, **kwargs: None)

    validator_id = uuid4()
    result = approval_service.approve_request(
        mock_db,
        mock_req.id,
        validator_id,
        decision_reason="Approved based on forecast",
    )

    assert result.status == ApprovalStatus.approved
    assert result.validated_by == validator_id
    assert result.decision_reason == "Approved based on forecast"
    assert result.requested_quantity is None


def test_execute_low_stock_requires_operator_quantity(monkeypatch, mock_db):
    class MockRequest:
        id = uuid4()
        status = ApprovalStatus.approved
        action_type = "low_stock_forecast_alert"
        product_id = 123
        requested_quantity = None

    mock_req = MockRequest()

    import app.repositories.approval_repository as repo
    monkeypatch.setattr(repo, "get_request_by_id", lambda db, rid: mock_req)

    with pytest.raises(ValueError, match="Requested quantity must be provided and greater than 0"):
        approval_service.execute_request(mock_db, mock_req.id, uuid4())


def test_approve_request_schema_does_not_expose_requested_quantity():
    payload = ApproveRequest.model_validate({"decision_reason": "ok", "requested_quantity": 50})

    assert payload.decision_reason == "ok"
    assert not hasattr(payload, "requested_quantity")


def test_execute_low_stock_creates_po_with_operator_quantity(monkeypatch, mock_db):
    class MockRequest:
        id = uuid4()
        status = ApprovalStatus.approved
        action_type = "low_stock_forecast_alert"
        product_id = 123
        product_name = "Test Product"
        requested_quantity = None
        executed_by = None

    mock_req = MockRequest()

    def update_status(_db, req, status, **kwargs):
        req.status = status
        if "requested_quantity" in kwargs:
            req.requested_quantity = kwargs["requested_quantity"]
        return req

    def assign_executor(_db, req, executor_id, executed_at):
        req.executed_by = executor_id
        req.executed_at = executed_at
        return req

    import app.repositories.approval_repository as repo
    monkeypatch.setattr(repo, "get_request_by_id", lambda db, rid: mock_req)
    monkeypatch.setattr(repo, "update_status", update_status)
    monkeypatch.setattr(repo, "assign_executor", assign_executor)
    monkeypatch.setattr("app.services.purchase_order_service.get_purchase_order_service", lambda db: MockPurchaseOrderService())
    monkeypatch.setattr("app.services.notification_service.mark_related_alerts_as_read", lambda *args, **kwargs: 0)

    import app.services.audit_service as audit
    monkeypatch.setattr(audit, "log_execution_event", lambda *args, **kwargs: None)

    executor_id = uuid4()
    result = approval_service.execute_request(
        mock_db,
        mock_req.id,
        executor_id,
        requested_quantity=50.0,
    )

    assert result.status == ApprovalStatus.executed
    assert result.requested_quantity == 50.0
    assert result.executed_by == executor_id


def test_reject_low_stock(monkeypatch, mock_db):
    class MockRequest:
        id = uuid4()
        status = ApprovalStatus.pending
        action_type = "low_stock_forecast_alert"

    mock_req = MockRequest()

    import app.repositories.approval_repository as repo
    monkeypatch.setattr(repo, "get_request_by_id", lambda db, rid: mock_req)
    monkeypatch.setattr(repo, "update_status", lambda db, req, status, **kwargs: setattr(req, "status", status))
    monkeypatch.setattr(repo, "assign_validator", lambda db, req, validator_id, validated_at: None)

    import app.services.audit_service as audit
    monkeypatch.setattr(audit, "log_approval_event", lambda *args, **kwargs: None)

    result = approval_service.reject_request(
        mock_db,
        mock_req.id,
        uuid4(),
        decision_reason="Too expensive",
    )

    assert result.status == ApprovalStatus.rejected


def test_refresh_pending_alerts_uses_company_scope(monkeypatch, mock_db):
    mock_req = SimpleNamespace(
        status=ApprovalStatus.pending,
        action_type="low_stock_forecast_alert",
        product_id=11,
        current_qty=0.0,
        min_qty=5.0,
        id=uuid4(),
        payload={"source_id": "source-1", "company_id": 1, "available_quantity": 0.0, "min_required": 5.0, "state": "low_stock"},
        decision_reason=None,
        executed_at=None,
    )

    monkeypatch.setattr(
        "app.repositories.product_inventory_state_repository.list_by_sources_and_products",
        lambda db, source_ids, product_ids: [
            SimpleNamespace(
                source_id="source-1",
                odoo_product_id=11,
                company_id=1,
                quantity=2.0,
                reserved_quantity=0.0,
                available_quantity=2.0,
                state=ProductInventoryStatus.low_stock,
            ),
            SimpleNamespace(
                source_id="source-1",
                odoo_product_id=11,
                company_id=2,
                quantity=50.0,
                reserved_quantity=0.0,
                available_quantity=50.0,
                state=ProductInventoryStatus.in_stock,
            ),
        ],
    )

    approval_service.refresh_pending_alerts_from_odoo(mock_db, [mock_req])

    assert mock_req.current_qty == 2.0
    assert mock_req.min_qty == 5.0
    assert mock_req.status == ApprovalStatus.pending
    assert mock_req.payload["shortage"] == 3.0
    assert mock_req.payload["state"] == "low_stock"


def test_refresh_pending_alerts_keeps_threshold_floor(monkeypatch, mock_db):
    mock_req = SimpleNamespace(
        id=uuid4(),
        status=ApprovalStatus.pending,
        action_type="low_stock_forecast_alert",
        product_id=11,
        current_qty=2.0,
        min_qty=5.0,
        payload={"source_id": "source-1", "company_id": 1, "available_quantity": 2.0, "min_required": 5.0, "state": "low_stock"},
        decision_reason=None,
        executed_at=None,
    )

    monkeypatch.setattr(
        "app.repositories.product_inventory_state_repository.list_by_sources_and_products",
        lambda db, source_ids, product_ids: [
            SimpleNamespace(
                source_id="source-1",
                odoo_product_id=11,
                company_id=1,
                quantity=4.0,
                reserved_quantity=0.0,
                available_quantity=4.0,
                state=ProductInventoryStatus.low_stock,
            )
        ],
    )

    approval_service.refresh_pending_alerts_from_odoo(mock_db, [mock_req])

    assert mock_req.current_qty == 4.0
    assert mock_req.min_qty == 5.0
    assert mock_req.status == ApprovalStatus.pending
    assert mock_req.payload["shortage"] == 1.0
    assert mock_req.payload["state"] == "low_stock"


def test_refresh_pending_alerts_auto_resolves_recovered_stock(monkeypatch, mock_db):
    mock_req = SimpleNamespace(
        status=ApprovalStatus.pending,
        action_type="low_stock_forecast_alert",
        product_id=11,
        current_qty=2.0,
        min_qty=5.0,
        id=uuid4(),
        payload={"source_id": "source-1", "company_id": 1, "available_quantity": 2.0, "min_required": 5.0, "state": "low_stock"},
        decision_reason=None,
        executed_at=None,
    )

    monkeypatch.setattr(
        "app.repositories.product_inventory_state_repository.list_by_sources_and_products",
        lambda db, source_ids, product_ids: [
            SimpleNamespace(
                source_id="source-1",
                odoo_product_id=11,
                company_id=1,
                quantity=9.0,
                reserved_quantity=0.0,
                available_quantity=9.0,
                state=ProductInventoryStatus.in_stock,
            )
        ],
    )
    monkeypatch.setattr("app.services.notification_service.mark_related_alerts_as_read", lambda *args, **kwargs: 0)

    approval_service.refresh_pending_alerts_from_odoo(mock_db, [mock_req])

    assert mock_req.current_qty == 9.0
    assert mock_req.status == ApprovalStatus.executed
    assert mock_req.decision_reason == "Resolved automatically after stock recovered in Odoo."
    assert mock_req.payload["resolved_automatically"] is True
    assert mock_req.payload["state"] == "in_stock"
    assert mock_db.commits == 1


def test_refresh_pending_alerts_uses_env_threshold(monkeypatch, mock_db):
    mock_req = SimpleNamespace(
        id=uuid4(),
        status=ApprovalStatus.pending,
        action_type="low_stock_forecast_alert",
        product_id=11,
        current_qty=2.0,
        min_qty=5.0,
        payload={"source_id": "source-1", "company_id": 1, "available_quantity": 2.0, "min_required": 5.0, "state": "low_stock"},
        decision_reason=None,
        executed_at=None,
    )

    monkeypatch.setattr(approval_service.settings, "STOCK_ALERT_MIN_THRESHOLD", 10)
    monkeypatch.setattr(
        "app.repositories.product_inventory_state_repository.list_by_sources_and_products",
        lambda db, source_ids, product_ids: [
            SimpleNamespace(
                source_id="source-1",
                odoo_product_id=11,
                company_id=1,
                quantity=10.0,
                reserved_quantity=0.0,
                available_quantity=10.0,
                state=ProductInventoryStatus.in_stock,
            )
        ],
    )
    monkeypatch.setattr("app.services.notification_service.mark_related_alerts_as_read", lambda *args, **kwargs: 0)

    approval_service.refresh_pending_alerts_from_odoo(mock_db, [mock_req])

    assert mock_req.current_qty == 10.0
    assert mock_req.min_qty == 10.0
    assert mock_req.status == ApprovalStatus.executed
    assert mock_req.payload["min_required"] == 10.0
    assert mock_req.payload["state"] == "in_stock"

