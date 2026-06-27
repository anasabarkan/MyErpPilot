"""Automatic low-stock alerts backed by Odoo inventory data."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.product_inventory_state import ProductInventoryState, ProductInventoryStatus
from app.models.user import User
from app.repositories import product_inventory_state_repository
from app.services import dashboard_service

logger = logging.getLogger(__name__)

LOW_STOCK_ALERT_ACTION_TYPE = "low_stock_forecast_alert"
_AUTO_RESOLVED_REASON = "Resolved automatically after stock recovered in Odoo."


def _round(value: float) -> float:
    return round(value, 2)


def _effective_min_required(raw_min_qty: float) -> float:
    _ = raw_min_qty
    return settings.stock_alert_min_threshold


def _alert_key(source_id: object, product_id: int, company_id: int | None) -> str:
    company_part = "global" if company_id is None else str(company_id)
    return f"low_stock_forecast:{source_id}:{company_part}:{product_id}"


def _payload_source_id(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    source_id = payload.get("source_id")
    if isinstance(source_id, str) and source_id.strip():
        return source_id.strip()
    alert_key = payload.get("alert_key")
    if isinstance(alert_key, str) and alert_key.startswith("low_stock_forecast:"):
        parts = alert_key.split(":", 3)
        if len(parts) == 4:
            return parts[1]
    return None


def _stock_state_from_available_quantity(available_quantity: float) -> ProductInventoryStatus:
    if available_quantity <= 0:
        return ProductInventoryStatus.out_of_stock
    if available_quantity < _effective_min_required(0.0):
        return ProductInventoryStatus.low_stock
    return ProductInventoryStatus.in_stock


def _sum_quantities_by_product(
    stock_quants: list[dict[str, Any]],
) -> tuple[dict[int, float], dict[tuple[int, int | None], float], dict[int, str]]:
    quantity_by_product: dict[int, float] = {}
    quantity_by_product_company: dict[tuple[int, int | None], float] = {}
    product_name_by_id: dict[int, str] = {}

    for quant in stock_quants:
        product_id = dashboard_service._extract_many2one_id(quant.get("product_id"))
        if product_id is None:
            continue

        product_name_by_id[product_id] = dashboard_service._extract_many2one_name(
            quant.get("product_id"),
            default="Unknown Product",
        )
        quantity = dashboard_service._to_float(quant.get("quantity"))
        reserved = dashboard_service._to_float(quant.get("reserved_quantity"))
        available = quantity - reserved
        company_id = dashboard_service._extract_many2one_id(quant.get("company_id"))

        quantity_by_product[product_id] = quantity_by_product.get(product_id, 0.0) + available
        key = (product_id, company_id)
        quantity_by_product_company[key] = quantity_by_product_company.get(key, 0.0) + available

    return quantity_by_product, quantity_by_product_company, product_name_by_id


def _max_orderpoints_by_product(
    orderpoints: list[dict[str, Any]],
    product_name_by_id: dict[int, str],
) -> dict[tuple[int, int | None], float]:
    min_qty_by_product_company: dict[tuple[int, int | None], float] = {}

    for orderpoint in orderpoints:
        product_id = dashboard_service._extract_many2one_id(orderpoint.get("product_id"))
        if product_id is None:
            continue

        product_name_by_id.setdefault(
            product_id,
            dashboard_service._extract_many2one_name(orderpoint.get("product_id"), default="Unknown Product"),
        )
        company_id = dashboard_service._extract_many2one_id(orderpoint.get("company_id"))
        min_qty = dashboard_service._to_float(orderpoint.get("product_min_qty"))
        key = (product_id, company_id)
        min_qty_by_product_company[key] = max(min_qty_by_product_company.get(key, 0.0), min_qty)

    return min_qty_by_product_company


def _tracked_products_by_id(tracked_products: list[dict[str, Any]]) -> dict[int, str]:
    tracked_product_name_by_id: dict[int, str] = {}

    for product in tracked_products:
        product_id = product.get("id")
        if not isinstance(product_id, int):
            continue

        display_name = product.get("display_name")
        fallback_name = product.get("name")
        if isinstance(display_name, str) and display_name.strip():
            tracked_product_name_by_id[product_id] = display_name.strip()
        elif isinstance(fallback_name, str) and fallback_name.strip():
            tracked_product_name_by_id[product_id] = fallback_name.strip()
        else:
            tracked_product_name_by_id[product_id] = f"Product #{product_id}"

    return tracked_product_name_by_id


def build_low_stock_alert_payloads(
    *,
    source_id: object,
    stock_quants: list[dict[str, Any]],
    orderpoints: list[dict[str, Any]],
    sales_rows: list[dict[str, Any]],
    forecast_days: int,
    sales_lookback_days: int,
    tracked_products: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return alert payloads for products whose available quantity is below the global threshold."""
    quantity_by_product, quantity_by_product_company, product_name_by_id = _sum_quantities_by_product(stock_quants)
    min_qty_by_product_company = _max_orderpoints_by_product(orderpoints, product_name_by_id)
    tracked_product_name_by_id = _tracked_products_by_id(tracked_products or [])
    for product_id, product_name in tracked_product_name_by_id.items():
        product_name_by_id.setdefault(product_id, product_name)
    _ = sales_rows

    all_product_company_keys = set(quantity_by_product_company.keys()) | set(min_qty_by_product_company.keys())
    covered_product_ids = {product_id for product_id, _company_id in all_product_company_keys}
    for product_id in tracked_product_name_by_id:
        if product_id not in covered_product_ids:
            all_product_company_keys.add((product_id, None))
    products_with_orderpoints = {product_id for product_id, _company_id in min_qty_by_product_company.keys()}

    alerts: list[dict[str, Any]] = []
    for product_id, company_id in all_product_company_keys:
        if product_id in products_with_orderpoints and (product_id, company_id) not in min_qty_by_product_company:
            continue
        min_required = _effective_min_required(
            min_qty_by_product_company.get((product_id, company_id), 0.0)
        )
        available_quantity = quantity_by_product_company.get(
            (product_id, company_id),
            quantity_by_product.get(product_id, 0.0),
        )
        if available_quantity >= min_required:
            continue

        shortage = max(min_required - available_quantity, 0.0)
        alerts.append(
            {
                "product_id": product_id,
                "company_id": company_id,
                "product": product_name_by_id.get(product_id, "Unknown Product"),
                "available_quantity": _round(available_quantity),
                "min_required": _round(min_required),
                "daily_sales_velocity": 0.0,
                "days_until_min": 0.0,
                "shortage": _round(shortage),
                "forecast_days": forecast_days,
                "sales_lookback_days": sales_lookback_days,
                "alert_key": _alert_key(source_id, product_id, company_id),
                "risk_level": "high",
            }
        )

    alerts.sort(key=lambda item: (-float(item["shortage"]), str(item["product"])))
    return alerts


def build_product_inventory_state_payloads(
    *,
    stock_quants: list[dict[str, Any]],
    tracked_products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tracked_product_name_by_id = _tracked_products_by_id(tracked_products)
    aggregates: dict[tuple[int, int | None], dict[str, float | str | int | None]] = {}
    product_has_quant: set[int] = set()

    for quant in stock_quants:
        product_id = dashboard_service._extract_many2one_id(quant.get("product_id"))
        if product_id is None:
            continue
        product_has_quant.add(product_id)
        product_name = dashboard_service._extract_many2one_name(
            quant.get("product_id"),
            default=tracked_product_name_by_id.get(product_id, "Unknown Product"),
        )
        company_id = dashboard_service._extract_many2one_id(quant.get("company_id"))
        key = (product_id, company_id)
        current = aggregates.setdefault(
            key,
            {
                "odoo_product_id": product_id,
                "company_id": company_id,
                "product_name": product_name,
                "quantity": 0.0,
                "reserved_quantity": 0.0,
            },
        )
        current["product_name"] = product_name
        current["quantity"] = dashboard_service._to_float(current.get("quantity")) + dashboard_service._to_float(quant.get("quantity"))
        current["reserved_quantity"] = dashboard_service._to_float(current.get("reserved_quantity")) + dashboard_service._to_float(quant.get("reserved_quantity"))

    for product_id, product_name in tracked_product_name_by_id.items():
        if product_id in product_has_quant:
            continue
        aggregates[(product_id, None)] = {
            "odoo_product_id": product_id,
            "company_id": None,
            "product_name": product_name,
            "quantity": 0.0,
            "reserved_quantity": 0.0,
        }

    payloads: list[dict[str, Any]] = []
    for payload in aggregates.values():
        quantity = _round(dashboard_service._to_float(payload.get("quantity")))
        reserved_quantity = _round(dashboard_service._to_float(payload.get("reserved_quantity")))
        available_quantity = _round(quantity - reserved_quantity)
        payloads.append(
            {
                "odoo_product_id": int(payload["odoo_product_id"]),
                "company_id": payload.get("company_id") if isinstance(payload.get("company_id"), int) else None,
                "product_name": str(payload.get("product_name") or "Unknown Product"),
                "quantity": quantity,
                "reserved_quantity": reserved_quantity,
                "available_quantity": available_quantity,
                "state": _stock_state_from_available_quantity(available_quantity),
            }
        )

    payloads.sort(key=lambda item: (str(item["product_name"]), item["company_id"] is not None, item["company_id"] or -1))
    return payloads


def _get_admin_user(db: Session) -> User | None:
    return db.query(User).filter(User.email == settings.ADMIN_SEED_EMAIL).first()


def _pending_alert_exists(db: Session, alert_key: str) -> bool:
    query = db.query(ApprovalRequest).filter(
        ApprovalRequest.action_type == LOW_STOCK_ALERT_ACTION_TYPE,
        ApprovalRequest.status == ApprovalStatus.pending,
    )

    try:
        return query.filter(ApprovalRequest.payload["alert_key"].as_string() == alert_key).first() is not None
    except Exception:
        return any(item.payload.get("alert_key") == alert_key for item in query.all())


def _create_alert_request(db: Session, payload: dict[str, Any], requested_by: object) -> ApprovalRequest:
    product = str(payload["product"])
    description = (
        f"{product} is below the low-stock threshold. "
        f"Available: {payload['available_quantity']}, threshold: {payload['min_required']}."
    )

    request_obj = ApprovalRequest(
        title=f"Low stock alert: {product}",
        description=description,
        action_type=LOW_STOCK_ALERT_ACTION_TYPE,
        payload=payload,
        status=ApprovalStatus.pending,
        requested_by=requested_by,
        product_id=payload["product_id"],
        product_name=payload["product"],
        current_qty=payload["available_quantity"],
        min_qty=payload["min_required"],
    )
    db.add(request_obj)
    db.commit()
    db.refresh(request_obj)

    try:
        from app.services import notification_service, email_notification_service, audit_service

        notification_service.create_inventory_alert_notification(
            db=db,
            user_id=requested_by,
            product_name=payload["product"],
            qty=payload["available_quantity"],
            min_qty=payload["min_required"],
            approval_request_id=request_obj.id,
        )

        user = db.query(User).get(requested_by)

        audit_service.log_action(
            db=db,
            user_id=requested_by,
            action="notification_sent",
            entity_type="approval_request",
            entity_id=str(request_obj.id),
            status="success",
            details={
                "type": "low_stock_alert",
                "product": payload["product"],
            },
        )

        if user and user.email:
            platform_link = f"{settings.FRONTEND_BASE_URL}/validations"
            email_notification_service.send_low_stock_email(
                to_email=user.email,
                product_name=payload["product"],
                qty_available=payload["available_quantity"],
                min_qty=payload["min_required"],
                platform_link=platform_link,
            )

    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to create notifications for low stock alert: {e}")

    return request_obj


def _build_alert_payload_from_state(source_id: object, row: ProductInventoryState) -> dict[str, Any]:
    min_required = _round(_effective_min_required(0.0))
    available_quantity = _round(row.available_quantity)
    shortage = _round(max(min_required - available_quantity, 0.0))
    return {
        "source_id": str(source_id),
        "product_id": row.odoo_product_id,
        "company_id": row.company_id,
        "product": row.product_name,
        "quantity": _round(row.quantity),
        "reserved_quantity": _round(row.reserved_quantity),
        "available_quantity": available_quantity,
        "min_required": min_required,
        "shortage": shortage,
        "forecast_days": max(settings.STOCK_ALERT_FORECAST_DAYS, 1),
        "sales_lookback_days": max(settings.STOCK_ALERT_SALES_LOOKBACK_DAYS, 1),
        "alert_key": _alert_key(source_id, row.odoo_product_id, row.company_id),
        "risk_level": "critical" if row.state == ProductInventoryStatus.out_of_stock else "high",
        "state": row.state.value,
    }


def _matching_pending_alerts_for_source(db: Session, source_id: object) -> list[ApprovalRequest]:
    source_token = str(source_id)
    rows = db.query(ApprovalRequest).filter(
        ApprovalRequest.action_type == LOW_STOCK_ALERT_ACTION_TYPE,
        ApprovalRequest.status == ApprovalStatus.pending,
    ).all()
    return [row for row in rows if _payload_source_id(getattr(row, "payload", None)) == source_token]


def _update_pending_alert_request(request_obj: ApprovalRequest, payload: dict[str, Any]) -> bool:
    description = (
        f"{payload['product']} is below the low-stock threshold. "
        f"Available: {payload['available_quantity']}, threshold: {payload['min_required']}."
    )
    changed = False
    if request_obj.title != f"Low stock alert: {payload['product']}":
        request_obj.title = f"Low stock alert: {payload['product']}"
        changed = True
    if request_obj.description != description:
        request_obj.description = description
        changed = True
    if request_obj.product_name != payload["product"]:
        request_obj.product_name = payload["product"]
        changed = True
    if request_obj.current_qty != payload["available_quantity"]:
        request_obj.current_qty = payload["available_quantity"]
        changed = True
    if request_obj.min_qty != payload["min_required"]:
        request_obj.min_qty = payload["min_required"]
        changed = True
    if request_obj.payload != payload:
        request_obj.payload = payload
        changed = True
    return changed


def _resolve_pending_alert_request(
    request_obj: ApprovalRequest,
    *,
    payload: dict[str, Any] | None,
    resolved_at: datetime,
) -> bool:
    changed = False
    if request_obj.status != ApprovalStatus.executed:
        request_obj.status = ApprovalStatus.executed
        changed = True
    if request_obj.executed_at is None:
        request_obj.executed_at = resolved_at
        changed = True
    if not request_obj.decision_reason:
        request_obj.decision_reason = _AUTO_RESOLVED_REASON
        changed = True
    if payload is not None:
        resolved_payload = {
            **payload,
            "resolved_automatically": True,
            "resolved_at": resolved_at.isoformat(),
        }
        if request_obj.current_qty != payload["available_quantity"]:
            request_obj.current_qty = payload["available_quantity"]
            changed = True
        if request_obj.min_qty != payload["min_required"]:
            request_obj.min_qty = payload["min_required"]
            changed = True
        if request_obj.payload != resolved_payload:
            request_obj.payload = resolved_payload
            changed = True
    return changed


def _sync_inventory_state_rows(
    db: Session,
    *,
    source_id: object,
    payloads: list[dict[str, Any]],
) -> list[ProductInventoryState]:
    present_scope_keys = {
        product_inventory_state_repository.scope_key(
            odoo_product_id=int(payload["odoo_product_id"]),
            company_id=payload["company_id"] if isinstance(payload.get("company_id"), int) else None,
        )
        for payload in payloads
    }
    synced_at = datetime.now(timezone.utc)
    for payload in payloads:
        product_inventory_state_repository.upsert_state(
            db,
            source_id=source_id,
            odoo_product_id=int(payload["odoo_product_id"]),
            company_id=payload["company_id"] if isinstance(payload.get("company_id"), int) else None,
            product_name=str(payload["product_name"]),
            quantity=dashboard_service._to_float(payload.get("quantity")),
            reserved_quantity=dashboard_service._to_float(payload.get("reserved_quantity")),
            available_quantity=dashboard_service._to_float(payload.get("available_quantity")),
            state=payload["state"] if isinstance(payload.get("state"), ProductInventoryStatus) else ProductInventoryStatus(payload["state"]),
            synced_at=synced_at,
        )
    product_inventory_state_repository.delete_missing_states(
        db,
        source_id=source_id,
        present_scope_keys=present_scope_keys,
    )
    db.commit()
    return product_inventory_state_repository.list_for_source(db, source_id=source_id)


def _reconcile_pending_alerts_from_inventory_states(
    db: Session,
    *,
    source_id: object,
    inventory_states: list[ProductInventoryState],
    requested_by: object,
) -> list[ApprovalRequest]:
    from app.services import notification_service

    pending_alerts = _matching_pending_alerts_for_source(db, source_id)
    pending_by_key = {
        str(alert.payload.get("alert_key")): alert
        for alert in pending_alerts
        if isinstance(alert.payload, dict) and isinstance(alert.payload.get("alert_key"), str)
    }

    alert_payloads_by_key = {
        _build_alert_payload_from_state(source_id, row)["alert_key"]: _build_alert_payload_from_state(source_id, row)
        for row in inventory_states
    }
    active_low_stock_keys = {
        key
        for key, payload in alert_payloads_by_key.items()
        if payload["state"] in {ProductInventoryStatus.low_stock.value, ProductInventoryStatus.out_of_stock.value}
    }

    created: list[ApprovalRequest] = []
    updated = False
    resolved_ids: list[str] = []

    for key in active_low_stock_keys:
        payload = alert_payloads_by_key[key]
        existing = pending_by_key.get(key)
        if existing is None:
            created.append(_create_alert_request(db, payload, requested_by))
            continue
        if _update_pending_alert_request(existing, payload):
            updated = True

    now = datetime.now(timezone.utc)
    for key, request_obj in pending_by_key.items():
        if key in active_low_stock_keys:
            continue
        payload = alert_payloads_by_key.get(key)
        if _resolve_pending_alert_request(request_obj, payload=payload, resolved_at=now):
            updated = True
        if getattr(request_obj, "id", None) is not None:
            resolved_ids.append(str(request_obj.id))

    if updated:
        db.commit()
    if resolved_ids:
        for approval_request_id in resolved_ids:
            notification_service.mark_related_alerts_as_read(db, approval_request_id, commit=False)
        db.commit()

    return created


def scan_low_stock_alerts(db: Session) -> list[ApprovalRequest]:
    """Sync local inventory states from Odoo and create or resolve low-stock validation alerts."""
    if not settings.STOCK_ALERT_ENABLED:
        logger.info("[stock-alert] skipped because STOCK_ALERT_ENABLED=false")
        return []

    try:
        active_source, client, user_id, resolver, _available_models, warnings = dashboard_service._build_runtime_context(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[stock-alert] skipped because Odoo runtime is unavailable", extra={"error": str(exc)})
        return []

    def model_exists(model_name: str) -> bool:
        return resolver.model_exists(model_name)

    if not model_exists("stock.quant") or not model_exists("product.product"):
        logger.warning("[stock-alert] skipped because required Odoo stock models are unavailable")
        return []

    stock_quants, quant_error = dashboard_service.safe_search_read(
        "stock.quant",
        [("location_id.usage", "=", "internal")],
        ["product_id", "company_id", "quantity", "reserved_quantity"],
        "stock_alert.stock_quant",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
        limit=5000,
        order="product_id asc",
    )
    if quant_error:
        logger.warning(
            "[stock-alert] skipped because inventory quantities could not be fetched",
            extra={"quant_error": quant_error, "warnings": warnings},
        )
        return []

    tracked_products, tracked_products_error = dashboard_service.safe_search_read(
        "product.product",
        [("active", "=", True)],
        ["id", "display_name"],
        "stock_alert.products",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
        limit=5000,
        order="id asc",
    )
    if tracked_products_error:
        logger.warning(
            "[stock-alert] skipped because active products could not be fetched",
            extra={"tracked_products_error": tracked_products_error},
        )
        return []

    state_payloads = build_product_inventory_state_payloads(
        stock_quants=stock_quants,
        tracked_products=tracked_products,
    )
    inventory_states = _sync_inventory_state_rows(
        db,
        source_id=active_source.id,
        payloads=state_payloads,
    )

    admin_user = _get_admin_user(db)
    if not admin_user:
        logger.warning("[stock-alert] inventory states synced but alert creation skipped because configured admin user was not found")
        return []

    created = _reconcile_pending_alerts_from_inventory_states(
        db,
        source_id=active_source.id,
        inventory_states=inventory_states,
        requested_by=admin_user.id,
    )

    logger.info(
        "[stock-alert] sync completed",
        extra={
            "inventory_states": len(inventory_states),
            "alerts_created": len(created),
        },
    )
    return created


async def run_stock_alert_loop(session_factory: Callable[[], Session]) -> None:
    """Run stock scans forever on the configured interval."""
    while True:
        await asyncio.sleep(settings.stock_alert_scan_interval_seconds)

        db = session_factory()
        try:
            scan_low_stock_alerts(db)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[stock-alert] background scan failed", extra={"error": str(exc)})
        finally:
            db.close()

