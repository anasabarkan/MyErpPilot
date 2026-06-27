from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.product_inventory_state import ProductInventoryState, ProductInventoryStatus


def scope_key(*, odoo_product_id: int, company_id: int | None) -> tuple[int, int | None]:
    return odoo_product_id, company_id


def get_by_scope(
    db: Session,
    *,
    source_id: UUID,
    odoo_product_id: int,
    company_id: int | None,
) -> ProductInventoryState | None:
    return (
        db.query(ProductInventoryState)
        .filter_by(source_id=source_id, odoo_product_id=odoo_product_id, company_id=company_id)
        .first()
    )


def upsert_state(
    db: Session,
    *,
    source_id: UUID,
    odoo_product_id: int,
    company_id: int | None,
    product_name: str,
    quantity: float,
    reserved_quantity: float,
    available_quantity: float,
    state: ProductInventoryStatus,
    synced_at: datetime | None = None,
) -> tuple[ProductInventoryState, bool]:
    now = synced_at or datetime.now(timezone.utc)
    existing = get_by_scope(
        db,
        source_id=source_id,
        odoo_product_id=odoo_product_id,
        company_id=company_id,
    )
    if existing is None:
        row = ProductInventoryState(
            source_id=source_id,
            odoo_product_id=odoo_product_id,
            company_id=company_id,
            product_name=product_name,
            quantity=quantity,
            reserved_quantity=reserved_quantity,
            available_quantity=available_quantity,
            state=state,
            last_synced_at=now,
            last_state_changed_at=now,
        )
        db.add(row)
        return row, True

    changed = False
    state_changed = existing.state != state
    if existing.product_name != product_name:
        existing.product_name = product_name
        changed = True
    if existing.quantity != quantity:
        existing.quantity = quantity
        changed = True
    if existing.reserved_quantity != reserved_quantity:
        existing.reserved_quantity = reserved_quantity
        changed = True
    if existing.available_quantity != available_quantity:
        existing.available_quantity = available_quantity
        changed = True
    if state_changed:
        existing.state = state
        existing.last_state_changed_at = now
        changed = True

    existing.last_synced_at = now
    return existing, changed or state_changed


def delete_missing_states(
    db: Session,
    *,
    source_id: UUID,
    present_scope_keys: set[tuple[int, int | None]],
) -> int:
    rows = db.query(ProductInventoryState).filter_by(source_id=source_id).all()
    deleted = 0
    for row in rows:
        key = scope_key(odoo_product_id=row.odoo_product_id, company_id=row.company_id)
        if key in present_scope_keys:
            continue
        db.delete(row)
        deleted += 1
    return deleted


def list_states(
    db: Session,
    *,
    source_id: UUID,
    state: ProductInventoryStatus | str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[ProductInventoryState], int]:
    query = db.query(ProductInventoryState).filter_by(source_id=source_id)
    if state is not None:
        normalized = ProductInventoryStatus(state) if not isinstance(state, ProductInventoryStatus) else state
        query = query.filter_by(state=normalized)

    total = query.count()
    items = query.order_by(ProductInventoryState.product_name.asc(), ProductInventoryState.company_id.asc()).offset(skip).limit(limit).all()
    return items, total


def list_for_source(db: Session, *, source_id: UUID) -> list[ProductInventoryState]:
    return db.query(ProductInventoryState).filter_by(source_id=source_id).all()


def list_by_sources_and_products(
    db: Session,
    *,
    source_ids: Iterable[UUID],
    product_ids: Iterable[int],
) -> list[ProductInventoryState]:
    source_id_set = set(source_ids)
    product_id_set = set(product_ids)
    if not source_id_set or not product_id_set:
        return []
    rows = db.query(ProductInventoryState).all()
    return [
        row
        for row in rows
        if row.source_id in source_id_set and row.odoo_product_id in product_id_set
    ]
