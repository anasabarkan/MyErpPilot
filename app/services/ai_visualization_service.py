"""
Build safe chart specifications for chat responses.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.ai import AIVisualizationSpec


_VISUALIZATION_KEYWORDS = (
    "bar chart",
    "chart",
    "diagram",
    "diagramme",
    "graph",
    "graphe",
    "graphic",
    "graphique",
    "line chart",
    "pie chart",
    "plot",
    "visualize",
    "visualise",
)

_NUMERIC_PRIORITY = (
    "revenue",
    "total_sales",
    "sales_amount",
    "amount_total",
    "price_total",
    "quantity_sold",
    "order_count",
    "count",
    "total",
    "value",
    "amount",
)

_LABEL_PRIORITY = (
    "product_name",
    "customer_name",
    "name",
    "month",
    "date",
    "date_order",
    "payment_state",
    "state",
    "metric",
)


def build_visualization_spec(
    *,
    question: str,
    handler: str,
    data: list[dict[str, Any]] | dict[str, Any],
    summary: str | None = None,
) -> AIVisualizationSpec | None:
    if not _wants_visualization(question):
        return None

    if handler == "get_top_products" and isinstance(data, list):
        y_key = "quantity_sold" if "quantity" in question.lower() else "revenue"
        return _build_spec(
            chart_type="bar",
            title="Top selling products",
            data=data,
            x_key="product_name",
            y_keys=[y_key],
            x_label="Product",
            y_label=_format_label(y_key),
            description=summary,
        )

    if handler == "get_top_customers" and isinstance(data, list):
        return _build_spec(
            chart_type="bar",
            title="Top customers by sales",
            data=data,
            x_key="customer_name",
            y_keys=["total_sales"],
            x_label="Customer",
            y_label="Total Sales",
            description=summary,
        )

    if handler == "get_recent_orders" and isinstance(data, list):
        return _build_spec(
            chart_type="bar",
            title="Recent orders by amount",
            data=data,
            x_key="name",
            y_keys=["amount_total"],
            x_label="Order",
            y_label="Amount Total",
            description=summary,
        )

    if handler == "get_invoice_status_summary" and isinstance(data, dict):
        rows = [
            {"status": "Paid", "count": _safe_number(data.get("paid_invoices"))},
            {"status": "In payment", "count": _safe_number(data.get("in_payment_invoices"))},
            {"status": "Unpaid", "count": _safe_number(data.get("unpaid_invoices"))},
        ]
        return _build_spec(
            chart_type="pie",
            title="Invoice payment status",
            data=[row for row in rows if row["count"] > 0],
            x_key="status",
            y_keys=["count"],
            x_label="Status",
            y_label="Invoices",
            description=summary,
        )

    if handler == "get_sales_summary" and isinstance(data, dict):
        rows = [
            {"metric": "Total orders", "value": _safe_number(data.get("total_orders"))},
            {"metric": "Confirmed orders", "value": _safe_number(data.get("confirmed_orders"))},
            {"metric": "Quotations", "value": _safe_number(data.get("quotations"))},
        ]
        return _build_spec(
            chart_type="bar",
            title="Sales order summary",
            data=[row for row in rows if row["value"] > 0],
            x_key="metric",
            y_keys=["value"],
            x_label="Metric",
            y_label="Count",
            description=summary,
        )

    if isinstance(data, list):
        return _infer_list_chart(question=question, data=data, summary=summary)

    if isinstance(data, dict):
        return _infer_dict_chart(data=data, summary=summary)

    return None


def _infer_list_chart(
    *,
    question: str,
    data: list[dict[str, Any]],
    summary: str | None,
) -> AIVisualizationSpec | None:
    if not data:
        return None

    label_key = _select_label_key(data)
    y_key = _select_numeric_key(data)
    if not label_key or not y_key:
        return None

    normalized_question = question.lower()
    if "pie" in normalized_question:
        chart_type = "pie"
    elif "line" in normalized_question or _looks_temporal(label_key):
        chart_type = "line"
    elif "area" in normalized_question:
        chart_type = "area"
    else:
        chart_type = "bar"

    return _build_spec(
        chart_type=chart_type,
        title="Generated chart",
        data=data,
        x_key=label_key,
        y_keys=[y_key],
        x_label=_format_label(label_key),
        y_label=_format_label(y_key),
        description=summary,
    )


def _infer_dict_chart(
    *,
    data: dict[str, Any],
    summary: str | None,
) -> AIVisualizationSpec | None:
    rows = [
        {"metric": _format_label(key), "value": _safe_number(value)}
        for key, value in data.items()
        if isinstance(value, (int, float))
    ]
    rows = [row for row in rows if row["value"] > 0]
    if not rows:
        return None

    return _build_spec(
        chart_type="bar",
        title="Generated chart",
        data=rows,
        x_key="metric",
        y_keys=["value"],
        x_label="Metric",
        y_label="Value",
        description=summary,
    )


def _build_spec(
    *,
    chart_type: str,
    title: str,
    data: list[dict[str, Any]],
    x_key: str,
    y_keys: list[str],
    x_label: str,
    y_label: str,
    description: str | None,
) -> AIVisualizationSpec | None:
    if not data or not x_key or not y_keys:
        return None
    return AIVisualizationSpec(
        type=chart_type,
        title=title,
        data=data,
        x_key=x_key,
        y_keys=y_keys,
        x_label=x_label,
        y_label=y_label,
        description=description,
    )


def _wants_visualization(question: str) -> bool:
    normalized = question.lower()
    return any(keyword in normalized for keyword in _VISUALIZATION_KEYWORDS)


def _select_label_key(rows: list[dict[str, Any]]) -> str | None:
    keys = _all_keys(rows)
    for preferred_key in _LABEL_PRIORITY:
        if preferred_key in keys:
            return preferred_key

    for key in keys:
        if key.endswith("_id") or key == "id":
            continue
        if any(isinstance(row.get(key), str) and row.get(key) for row in rows):
            return key
    return None


def _select_numeric_key(rows: list[dict[str, Any]]) -> str | None:
    keys = _all_keys(rows)
    for preferred_key in _NUMERIC_PRIORITY:
        if preferred_key in keys and _has_numeric_value(rows, preferred_key):
            return preferred_key

    for key in keys:
        if key.endswith("_id") or key == "id":
            continue
        if _has_numeric_value(rows, key):
            return key
    return None


def _all_keys(rows: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return keys


def _has_numeric_value(rows: list[dict[str, Any]], key: str) -> bool:
    return any(_is_number(row.get(key)) for row in rows)


def _looks_temporal(key: str) -> bool:
    lowered = key.lower()
    return "date" in lowered or "month" in lowered or "year" in lowered


def _safe_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _is_number(value: Any) -> bool:
    return isinstance(value, (Decimal, int, float)) and not isinstance(value, bool)


def _format_label(key: str) -> str:
    return " ".join(part.capitalize() for part in key.split("_") if part)
