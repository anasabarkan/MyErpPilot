from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.product_inventory_state import ProductInventoryStatus
from app.repositories import product_inventory_state_repository as repository


class FakeQuery:
    def __init__(self, items):
        self._items = list(items)

    def filter_by(self, **kwargs):
        return FakeQuery(
            [
                item
                for item in self._items
                if all(getattr(item, key) == value for key, value in kwargs.items())
            ]
        )

    def order_by(self, *_args, **_kwargs):
        return self

    def offset(self, value):
        return FakeQuery(self._items[value:])

    def limit(self, value):
        return FakeQuery(self._items[:value])

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)

    def count(self):
        return len(self._items)


class FakeSession:
    def __init__(self):
        self.rows = []

    def query(self, _model):
        return FakeQuery(self.rows)

    def add(self, obj):
        if obj not in self.rows:
            self.rows.append(obj)

    def delete(self, obj):
        self.rows.remove(obj)


def test_upsert_updates_existing_scope_in_place_for_null_company():
    db = FakeSession()
    source_id = uuid4()
    first_sync = datetime.now(timezone.utc)
    second_sync = first_sync + timedelta(minutes=15)

    row, created = repository.upsert_state(
        db,
        source_id=source_id,
        odoo_product_id=11,
        company_id=None,
        product_name="Acoustic Bloc Screens",
        quantity=3.0,
        reserved_quantity=1.0,
        available_quantity=2.0,
        state=ProductInventoryStatus.low_stock,
        synced_at=first_sync,
    )
    assert created is True
    assert len(db.rows) == 1

    updated_row, changed = repository.upsert_state(
        db,
        source_id=source_id,
        odoo_product_id=11,
        company_id=None,
        product_name="Acoustic Bloc Screens",
        quantity=8.0,
        reserved_quantity=0.0,
        available_quantity=8.0,
        state=ProductInventoryStatus.in_stock,
        synced_at=second_sync,
    )

    assert changed is True
    assert updated_row is row
    assert len(db.rows) == 1
    assert updated_row.quantity == 8.0
    assert updated_row.available_quantity == 8.0
    assert updated_row.state == ProductInventoryStatus.in_stock
    assert updated_row.last_synced_at == second_sync
    assert updated_row.last_state_changed_at == second_sync


def test_delete_missing_states_removes_only_absent_scope_keys():
    db = FakeSession()
    source_id = uuid4()
    repository.upsert_state(
        db,
        source_id=source_id,
        odoo_product_id=11,
        company_id=1,
        product_name="Desk Lamp",
        quantity=1.0,
        reserved_quantity=0.0,
        available_quantity=1.0,
        state=ProductInventoryStatus.low_stock,
    )
    repository.upsert_state(
        db,
        source_id=source_id,
        odoo_product_id=12,
        company_id=None,
        product_name="Laptop",
        quantity=10.0,
        reserved_quantity=0.0,
        available_quantity=10.0,
        state=ProductInventoryStatus.in_stock,
    )

    deleted = repository.delete_missing_states(
        db,
        source_id=source_id,
        present_scope_keys={repository.scope_key(odoo_product_id=12, company_id=None)},
    )

    assert deleted == 1
    assert len(db.rows) == 1
    assert db.rows[0].odoo_product_id == 12


def test_list_states_filters_by_state_and_applies_pagination():
    db = FakeSession()
    source_id = uuid4()
    for product_id, state in ((11, ProductInventoryStatus.low_stock), (12, ProductInventoryStatus.in_stock), (13, ProductInventoryStatus.low_stock)):
        repository.upsert_state(
            db,
            source_id=source_id,
            odoo_product_id=product_id,
            company_id=None,
            product_name=f"Product {product_id}",
            quantity=1.0,
            reserved_quantity=0.0,
            available_quantity=1.0 if state == ProductInventoryStatus.low_stock else 8.0,
            state=state,
        )

    items, total = repository.list_states(
        db,
        source_id=source_id,
        state=ProductInventoryStatus.low_stock,
        skip=1,
        limit=1,
    )

    assert total == 2
    assert len(items) == 1
    assert items[0].state == ProductInventoryStatus.low_stock
