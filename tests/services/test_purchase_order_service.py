import pytest

from app.services.purchase_order_service import MockPurchaseOrderService, OdooPurchaseOrderService, get_purchase_order_service


def test_purchase_order_service_factory():
    service = get_purchase_order_service()
    assert isinstance(service, MockPurchaseOrderService)


def test_mock_purchase_order_creation():
    service = MockPurchaseOrderService()
    result = service.create_purchase_order(product_id=101, quantity=50, vendor_id=5)

    assert result["state"] == "purchase"
    assert result["product_id"] == 101
    assert result["quantity"] == 50
    assert result["vendor_id"] == 5
    assert result["stock_updated"] is True
    assert result["mocked"] is True


def test_odoo_purchase_order_service_with_mocked_odoo(monkeypatch):
    class MockClient:
        def __init__(self):
            self.calls = []

        def authenticate(self):
            return 1

        def execute_kw(self, *args, **kwargs):
            self.calls.append((kwargs.get("model"), kwargs.get("method")))
            if kwargs.get("model") == "purchase.order" and kwargs.get("method") == "create":
                return 12345
            return True

    class MockResolver:
        def model_exists(self, _name):
            return True

    mock_active_source = type("MockActiveSource", (), {"id": 99})
    mock_client = MockClient()
    mock_resolver = MockResolver()

    import app.services.dashboard_service as ds

    monkeypatch.setattr(ds, "_build_runtime_context", lambda db: (mock_active_source, mock_client, 1, mock_resolver, set(), []))

    def mock_safe_search_read(model, domain, fields, metric_name, *args, **kwargs):
        if model == "product.product":
            return [{"id": 101, "display_name": "Test Product", "standard_price": 15.0, "seller_ids": [10]}], None
        if model == "product.supplierinfo":
            return [{"id": 10, "partner_id": [5, "Vendor 5"]}], None
        if model == "purchase.order":
            return [{"id": 12345, "name": "PO001", "state": "purchase", "picking_ids": [700]}], None
        if model == "stock.picking":
            return [{"id": 700, "name": "WH/IN/00700", "state": "done"}], None
        return [], None

    monkeypatch.setattr(ds, "safe_search_read", mock_safe_search_read)

    service = OdooPurchaseOrderService(db=type("MockDB", (), {}))
    result = service.create_purchase_order(product_id=101, quantity=50.0)

    assert result["id"] == 12345
    assert result["name"] == "PO001"
    assert result["state"] == "purchase"
    assert result["product_id"] == 101
    assert result["quantity"] == 50.0
    assert result["vendor_id"] == 5
    assert result["receipt_ids"] == [700]
    assert result["stock_updated"] is True
    assert ("purchase.order", "button_confirm") in mock_client.calls
    assert ("stock.picking", "button_validate") in mock_client.calls