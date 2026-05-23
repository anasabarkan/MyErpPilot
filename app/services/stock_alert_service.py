"""Automatic low-stock forecast alerts backed by Odoo inventory data."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.user import User
from app.services import dashboard_service

logger = logging.getLogger(__name__)

LOW_STOCK_ALERT_ACTION_TYPE = "low_stock_forecast_alert"


def _round(value: float) -> float:
    return round(value, 2)


def _alert_key(source_id: object, product_id: int, company_id: int | None) -> str:
    company_part = "global" if company_id is None else str(company_id)
    return f"low_stock_forecast:{source_id}:{company_part}:{product_id}"


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


def _sales_velocity_by_product(
    sales_rows: list[dict[str, Any]],
    lookback_days: int,
) -> dict[int, float]:
    safe_days = max(lookback_days, 1)
    velocity_by_product: dict[int, float] = {}

    for row in sales_rows:
        product_id = dashboard_service._extract_many2one_id(row.get("product_id"))
        if product_id is None:
            continue

        quantity_sold = dashboard_service._to_float(
            row.get("product_uom_qty_sum", row.get("product_uom_qty"))
        )
        velocity_by_product[product_id] = velocity_by_product.get(product_id, 0.0) + quantity_sold / safe_days

    return velocity_by_product


def build_low_stock_alert_payloads(
    *,
    source_id: object,
    stock_quants: list[dict[str, Any]],
    orderpoints: list[dict[str, Any]],
    sales_rows: list[dict[str, Any]],
    forecast_days: int,
    sales_lookback_days: int,
) -> list[dict[str, Any]]:
    """Return alert payloads for products that will reach minimum stock soon."""
    quantity_by_product, quantity_by_product_company, product_name_by_id = _sum_quantities_by_product(stock_quants)
    min_qty_by_product_company = _max_orderpoints_by_product(orderpoints, product_name_by_id)
    velocity_by_product = _sales_velocity_by_product(sales_rows, sales_lookback_days)

    alerts: list[dict[str, Any]] = []
    for (product_id, company_id), min_required in min_qty_by_product_company.items():
        available_quantity = quantity_by_product_company.get(
            (product_id, company_id),
            quantity_by_product.get(product_id, 0.0),
        )
        daily_sales_velocity = velocity_by_product.get(product_id, 0.0)
        shortage = max(min_required - available_quantity, 0.0)
        already_low = available_quantity <= min_required

        if already_low:
            days_until_min = 0.0
        elif daily_sales_velocity > 0:
            days_until_min = (available_quantity - min_required) / daily_sales_velocity
        else:
            continue

        if not already_low and days_until_min > forecast_days:
            continue

        alerts.append(
            {
                "product_id": product_id,
                "product": product_name_by_id.get(product_id, "Unknown Product"),
                "available_quantity": _round(available_quantity),
                "min_required": _round(min_required),
                "daily_sales_velocity": _round(daily_sales_velocity),
                "days_until_min": _round(max(days_until_min, 0.0)),
                "shortage": _round(shortage),
                "forecast_days": forecast_days,
                "sales_lookback_days": sales_lookback_days,
                "alert_key": _alert_key(source_id, product_id, company_id),
                "risk_level": "high" if already_low else "medium",
            }
        )

    alerts.sort(key=lambda item: (float(item["days_until_min"]), -float(item["shortage"]), str(item["product"])))
    return alerts


def _get_admin_user(db: Session) -> User | None:
    return db.query(User).filter(User.email == settings.ADMIN_SEED_EMAIL).first()


def _pending_alert_exists(db: Session, alert_key: str) -> bool:
    query = db.query(ApprovalRequest).filter(
        ApprovalRequest.action_type == LOW_STOCK_ALERT_ACTION_TYPE,
        ApprovalRequest.status == ApprovalStatus.pending,
    )

    try:
        return query.filter(ApprovalRequest.payload["alert_key"].as_string() == alert_key).first() is not None
    except Exception:  # pragma: no cover - fallback for non-PostgreSQL test engines
        return any(item.payload.get("alert_key") == alert_key for item in query.all())


def _create_alert_request(db: Session, payload: dict[str, Any], requested_by: object) -> ApprovalRequest:
    days_until_min = float(payload["days_until_min"])
    product = str(payload["product"])
    if days_until_min <= 0:
        description = (
            f"{product} is already at or below its reorder minimum. "
            f"Available: {payload['available_quantity']}, minimum: {payload['min_required']}."
        )
    else:
        description = (
            f"{product} is forecast to reach its reorder minimum in {days_until_min:.1f} days. "
            f"Available: {payload['available_quantity']}, minimum: {payload['min_required']}."
        )

    request_obj = ApprovalRequest(
        title=f"Low stock forecast: {product}",
        description=description,
        action_type=LOW_STOCK_ALERT_ACTION_TYPE,
        payload=payload,
        status=ApprovalStatus.pending,
        requested_by=requested_by,
    )
    db.add(request_obj)
    db.commit()
    db.refresh(request_obj)
    return request_obj


def scan_low_stock_alerts(db: Session) -> list[ApprovalRequest]:
    """Scan Odoo inventory and create pending validation alerts for low-stock forecasts."""
    if not settings.STOCK_ALERT_ENABLED:
        logger.info("[stock-alert] skipped because STOCK_ALERT_ENABLED=false")
        return []

    admin_user = _get_admin_user(db)
    if not admin_user:
        logger.warning("[stock-alert] skipped because configured admin user was not found")
        return []

    try:
        active_source, client, user_id, resolver, _available_models, warnings = dashboard_service._build_runtime_context(db)
    except Exception as exc:  # noqa: BLE001 - startup monitor must never crash the API
        logger.warning("[stock-alert] skipped because Odoo runtime is unavailable", extra={"error": str(exc)})
        return []

    def model_exists(model_name: str) -> bool:
        return resolver.model_exists(model_name)

    required_models = ("stock.quant", "stock.warehouse.orderpoint", "sale.order.line")
    if not all(model_exists(model_name) for model_name in required_models):
        logger.warning("[stock-alert] skipped because required Odoo models are unavailable")
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
    orderpoints, orderpoint_error = dashboard_service.safe_search_read(
        "stock.warehouse.orderpoint",
        [],
        ["product_id", "company_id", "product_min_qty"],
        "stock_alert.orderpoints",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
        limit=5000,
        order="product_id asc",
    )

    since = datetime.now(timezone.utc) - timedelta(days=max(settings.STOCK_ALERT_SALES_LOOKBACK_DAYS, 1))
    sales_rows, sales_error = dashboard_service.safe_read_group(
        "sale.order.line",
        [
            ("order_id.state", "in", ["sale", "done"]),
            ("order_id.date_order", ">=", since.strftime("%Y-%m-%d %H:%M:%S")),
        ],
        ["product_id", "product_uom_qty:sum"],
        ["product_id"],
        "stock_alert.sales_velocity",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
        limit=5000,
    )

    if quant_error or orderpoint_error:
        logger.warning(
            "[stock-alert] skipped because inventory records could not be fetched",
            extra={"quant_error": quant_error, "orderpoint_error": orderpoint_error, "warnings": warnings},
        )
        return []
    if sales_error:
        logger.warning("[stock-alert] sales velocity unavailable; only already-low items can alert", extra={"error": sales_error})

    payloads = build_low_stock_alert_payloads(
        source_id=active_source.id,
        stock_quants=stock_quants,
        orderpoints=orderpoints,
        sales_rows=sales_rows,
        forecast_days=max(settings.STOCK_ALERT_FORECAST_DAYS, 1),
        sales_lookback_days=max(settings.STOCK_ALERT_SALES_LOOKBACK_DAYS, 1),
    )

    created: list[ApprovalRequest] = []
    for payload in payloads:
        if _pending_alert_exists(db, str(payload["alert_key"])):
            continue
        created.append(_create_alert_request(db, payload, admin_user.id))

    logger.info("[stock-alert] scan completed", extra={"alerts_created": len(created), "alerts_detected": len(payloads)})
    return created


async def run_stock_alert_loop(session_factory: Callable[[], Session]) -> None:
    """Run stock scans forever on the configured interval."""
    while True:
        await asyncio.sleep(max(settings.STOCK_ALERT_SCAN_INTERVAL_SECONDS, 60))

        db = session_factory()
        try:
            scan_low_stock_alerts(db)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[stock-alert] background scan failed", extra={"error": str(exc)})
        finally:
            db.close()
