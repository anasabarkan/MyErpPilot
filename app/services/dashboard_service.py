"""
Dashboard summary service backed by the active Odoo source.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.integrations.odoo import OdooJsonRpcClient, OdooRequestError
from app.models.data_source import DataSource
from app.repositories import data_source_repository as data_source_repo
from app.security.encryption import decrypt_value

logger = logging.getLogger(__name__)
_MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DIAGNOSTIC_MODELS = (
    "res.partner",
    "product.product",
    "product.template",
    "sale.order",
    "account.move",
)
CUSTOMERS_BASIC_WARNING = "Customers loaded with basic counting because advanced customer fields are unavailable."
PRODUCTS_MODULE_WARNING = (
    "Products data unavailable because the Products/Inventory module is not installed. "
    "Install Sales, Inventory, or Products app."
)
SALES_MODULE_WARNING = "Sales data unavailable because the Sales module is not installed. Install the Sales app."
INVOICE_MODULE_WARNING = "Invoice data unavailable because the Invoicing module is not installed."
GENERIC_DATA_WARNING = "Some dashboard data is temporarily unavailable. Please check Odoo access permissions."
NO_SALES_DATA_WARNING = "No sales data available yet. Add sales in Odoo."


class DashboardSourceNotConfiguredError(RuntimeError):
    """Raised when there is no active Odoo source configured."""


class DashboardCredentialError(RuntimeError):
    """Raised when active source credentials cannot be decrypted."""


class OdooModelResolver:
    """Resolve model availability with layered checks and memoization."""

    def __init__(
        self,
        *,
        client: OdooJsonRpcClient,
        user_id: int,
        discovered_models: set[str],
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.discovered_models = discovered_models
        self._exists_cache: dict[str, bool] = {}
        self._resolution_source: dict[str, str] = {}

    @property
    def resolution_source(self) -> dict[str, str]:
        return dict(self._resolution_source)

    def model_exists(self, model_name: str) -> bool:
        if model_name in self._exists_cache:
            return self._exists_cache[model_name]

        exists = self._resolve_model_exists(model_name)
        self._exists_cache[model_name] = exists
        return exists

    def _resolve_model_exists(self, model_name: str) -> bool:
        if model_name in self.discovered_models:
            self._resolution_source[model_name] = "discovered_models"
            return True

        try:
            count = self.client.search_count(
                user_id=self.user_id,
                model="ir.model",
                domain=[("model", "=", model_name)],
            )
            if count > 0:
                self._resolution_source[model_name] = "ir.model"
                return True
        except OdooRequestError as exc:
            logger.debug(
                "[dashboard][diagnostics] ir.model probe failed",
                extra={"model": model_name, "error": str(exc)},
            )

        try:
            self.client.search_count(user_id=self.user_id, model=model_name, domain=[])
            self._resolution_source[model_name] = "direct_probe"
            return True
        except OdooRequestError as exc:
            if _looks_like_missing_model_error(str(exc)):
                self._resolution_source[model_name] = "direct_probe_missing"
                return False

            # Do not false-negative on permissions/ACL errors.
            logger.warning(
                "[dashboard][diagnostics] direct model probe uncertain; assuming available",
                extra={"model": model_name, "error": str(exc)},
            )
            self._resolution_source[model_name] = "direct_probe_uncertain_assumed_exists"
            return True


def _normalize_odoo_url(url: str) -> str:
    return url.strip().rstrip("/")


def _build_client(data_source: DataSource, api_key: str) -> OdooJsonRpcClient:
    return OdooJsonRpcClient(
        base_url=_normalize_odoo_url(data_source.odoo_url),
        database_name=data_source.database_name.strip(),
        username=data_source.username.strip(),
        api_key=api_key,
        timeout_seconds=10.0,
    )


def _append_warning(warnings: list[str], warning: str | None) -> None:
    if warning and warning not in warnings:
        warnings.append(warning)


def _model_set_from_cache(data_source: DataSource) -> set[str]:
    models: set[str] = set()
    if not isinstance(data_source.models_cache, list):
        return models

    for item in data_source.models_cache:
        if not isinstance(item, dict):
            continue
        model_name = item.get("model")
        if isinstance(model_name, str) and model_name:
            models.add(model_name)
    return models


def _looks_like_missing_model_error(error_message: str) -> bool:
    lowered = error_message.lower()
    missing_indicators = (
        "does not exist",
        "doesn't exist",
        "object does not exist",
        "unknown model",
        "invalid model",
        "keyerror",
        "model not found",
    )
    return any(indicator in lowered for indicator in missing_indicators)


def _domain_to_log(domain: list[Any]) -> str:
    return repr(domain)


def _log_metric_rpc_call(
    *,
    metric_name: str,
    model: str,
    method: str,
    domain: list[Any],
    fields: list[str] | None,
    kwargs: dict[str, Any] | None,
) -> None:
    logger.info(
        "[dashboard][metric][%s] RPC call",
        metric_name,
        extra={
            "metric_name": metric_name,
            "model": model,
            "method": method,
            "domain": _domain_to_log(domain),
            "fields": fields or [],
            "kwargs": kwargs or {},
        },
    )


def _log_metric_failure(
    *,
    metric_name: str,
    model: str,
    method: str,
    domain: list[Any],
    fallback_strategy: str,
    error: Exception,
) -> None:
    logger.error(
        "[dashboard][metric][%s] RPC call failed; fallback=%s",
        metric_name,
        fallback_strategy,
        extra={
            "metric_name": metric_name,
            "model": model,
            "method": method,
            "domain": _domain_to_log(domain),
            "fallback_strategy": fallback_strategy,
            "error_message": str(error),
        },
        exc_info=True,
    )


def safe_search_count(
    model: str,
    domain: list[Any],
    metric_name: str,
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool] | None = None,
) -> tuple[int, str | None]:
    """Safely execute Odoo search_count with detailed logging and fallback output."""
    if model_exists_fn is not None and not model_exists_fn(model):
        message = f"Model '{model}' is unavailable."
        logger.warning(
            "[dashboard][metric][%s] model unavailable",
            metric_name,
            extra={"metric_name": metric_name, "model": model, "domain": _domain_to_log(domain)},
        )
        return 0, message

    _log_metric_rpc_call(
        metric_name=metric_name,
        model=model,
        method="search_count",
        domain=domain,
        fields=None,
        kwargs=None,
    )
    try:
        count = client.search_count(user_id=user_id, model=model, domain=domain)
        return count, None
    except OdooRequestError as exc:
        _log_metric_failure(
            metric_name=metric_name,
            model=model,
            method="search_count",
            domain=domain,
            fallback_strategy="return 0",
            error=exc,
        )
        return 0, str(exc)


def safe_search_read(
    model: str,
    domain: list[Any],
    fields: list[str],
    metric_name: str,
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool] | None = None,
    limit: int = 10,
    order: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Safely execute Odoo search_read with detailed logging and fallback output."""
    if model_exists_fn is not None and not model_exists_fn(model):
        message = f"Model '{model}' is unavailable."
        logger.warning(
            "[dashboard][metric][%s] model unavailable",
            metric_name,
            extra={"metric_name": metric_name, "model": model, "domain": _domain_to_log(domain)},
        )
        return [], message

    kwargs: dict[str, Any] = {"limit": limit}
    if order:
        kwargs["order"] = order

    _log_metric_rpc_call(
        metric_name=metric_name,
        model=model,
        method="search_read",
        domain=domain,
        fields=fields,
        kwargs=kwargs,
    )
    try:
        rows = client.search_read(
            user_id=user_id,
            model=model,
            domain=domain,
            fields=fields,
            limit=limit,
            order=order,
        )
        return rows, None
    except OdooRequestError as exc:
        _log_metric_failure(
            metric_name=metric_name,
            model=model,
            method="search_read",
            domain=domain,
            fallback_strategy="return []",
            error=exc,
        )
        return [], str(exc)


def safe_read_group(
    model: str,
    domain: list[Any],
    fields: list[str],
    groupby: list[str],
    metric_name: str,
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool] | None = None,
    limit: int | None = None,
    orderby: str | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Safely execute Odoo read_group with detailed logging and fallback output."""
    if model_exists_fn is not None and not model_exists_fn(model):
        message = f"Model '{model}' is unavailable."
        logger.warning(
            "[dashboard][metric][%s] model unavailable",
            metric_name,
            extra={"metric_name": metric_name, "model": model, "domain": _domain_to_log(domain)},
        )
        return [], message

    kwargs: dict[str, Any] = {"lazy": False}
    if limit is not None:
        kwargs["limit"] = limit
    if orderby:
        kwargs["orderby"] = orderby
    if context:
        kwargs["context"] = context

    _log_metric_rpc_call(
        metric_name=metric_name,
        model=model,
        method="read_group",
        domain=domain,
        fields=fields,
        kwargs={"groupby": groupby, **kwargs},
    )

    try:
        raw = client.execute_kw(
            user_id=user_id,
            model=model,
            method="read_group",
            args=[domain, fields, groupby],
            kwargs=kwargs,
        )
    except OdooRequestError as exc:
        _log_metric_failure(
            metric_name=metric_name,
            model=model,
            method="read_group",
            domain=domain,
            fallback_strategy="return []",
            error=exc,
        )
        return [], str(exc)

    if not isinstance(raw, list):
        logger.error(
            "[dashboard][metric][%s] read_group returned unexpected payload type",
            metric_name,
            extra={
                "metric_name": metric_name,
                "model": model,
                "domain": _domain_to_log(domain),
                "payload_type": type(raw).__name__,
            },
        )
        return [], "Unexpected read_group payload."

    safe_rows = [item for item in raw if isinstance(item, dict)]
    return safe_rows, None

def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _extract_many2one_name(value: Any, default: str = "Unknown") -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 2 and isinstance(value[1], str):
        return value[1]
    return default


def _extract_many2one_id(value: Any) -> int | None:
    if isinstance(value, (list, tuple)) and len(value) >= 1 and isinstance(value[0], int):
        return value[0]
    return None


def _clean_product_short_name(full_name: str) -> str:
    if not full_name:
        return "Unknown Product"
    # Keep internal reference in full_name, shorten axis label by removing bracketed code prefix.
    short_name = re.sub(r"^\[[^\]]+\]\s*", "", full_name).strip()
    return short_name or full_name


def _extract_ids_from_in_domain(domain: list[Any], field_name: str) -> set[int]:
    for term in domain:
        if (
            isinstance(term, tuple)
            and len(term) == 3
            and term[0] == field_name
            and term[1] == "in"
            and isinstance(term[2], list)
        ):
            return {value for value in term[2] if isinstance(value, int)}
    return set()


def _resolve_top_product_names(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
    product_ids: set[int],
    display_hint_by_id: dict[int, str],
) -> dict[int, tuple[str, str]]:
    """Resolve product names with priority: product display_name > template name > product name."""
    if not product_ids:
        return {}

    template_name_by_id: dict[int, str] = {}
    product_rows_by_id: dict[int, dict[str, Any]] = {}

    if model_exists_fn("product.product"):
        product_rows, product_error = safe_search_read(
            "product.product",
            [("id", "in", sorted(product_ids))],
            ["id", "display_name", "name", "product_tmpl_id"],
            "top_products.product_lookup",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists_fn,
            limit=max(len(product_ids), 1),
        )
        if product_error:
            logger.warning(
                "[dashboard][metric][top_products] product lookup failed",
                extra={"error": product_error},
            )
        else:
            for row in product_rows:
                product_id = row.get("id")
                if isinstance(product_id, int):
                    product_rows_by_id[product_id] = row

    template_ids: set[int] = set()
    for row in product_rows_by_id.values():
        product_tmpl_id = _extract_many2one_id(row.get("product_tmpl_id"))
        if product_tmpl_id is not None:
            template_ids.add(product_tmpl_id)

    if template_ids and model_exists_fn("product.template"):
        template_rows, template_error = safe_search_read(
            "product.template",
            [("id", "in", sorted(template_ids))],
            ["id", "display_name", "name"],
            "top_products.template_lookup",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists_fn,
            limit=max(len(template_ids), 1),
        )
        if template_error:
            logger.warning(
                "[dashboard][metric][top_products] template lookup failed",
                extra={"error": template_error},
            )
        else:
            for row in template_rows:
                template_id = row.get("id")
                if not isinstance(template_id, int):
                    continue
                display_name = row.get("display_name") if isinstance(row.get("display_name"), str) else None
                template_name = row.get("name") if isinstance(row.get("name"), str) else None
                resolved_name = display_name or template_name
                if resolved_name:
                    template_name_by_id[template_id] = resolved_name

    resolved_names: dict[int, tuple[str, str]] = {}
    for product_id in product_ids:
        product_row = product_rows_by_id.get(product_id, {})
        product_display = product_row.get("display_name") if isinstance(product_row.get("display_name"), str) else None
        product_name = product_row.get("name") if isinstance(product_row.get("name"), str) else None
        template_id = _extract_many2one_id(product_row.get("product_tmpl_id"))
        template_name = template_name_by_id.get(template_id) if template_id is not None else None
        hint_name = display_hint_by_id.get(product_id)

        full_name = product_display or template_name or product_name or hint_name or "Unknown Product"
        short_name = _clean_product_short_name(full_name)
        resolved_names[product_id] = (short_name, full_name)

    return resolved_names


def _extract_sum_value(row: dict[str, Any], field: str) -> float:
    direct = row.get(f"{field}_sum")
    if isinstance(direct, (int, float)):
        return float(direct)

    fallback = row.get(field)
    if isinstance(fallback, (int, float)):
        return float(fallback)

    for key, value in row.items():
        if key.startswith(f"{field}_") and key.endswith("_sum") and isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _month_slots(month_count: int) -> list[tuple[int, int]]:
    now = datetime.now(timezone.utc)
    slots: list[tuple[int, int]] = []
    for offset in reversed(range(month_count)):
        year = now.year
        month = now.month - offset
        while month <= 0:
            month += 12
            year -= 1
        slots.append((year, month))
    return slots


def _month_start(month_count: int) -> datetime:
    year, month = _month_slots(month_count)[0]
    return datetime(year=year, month=month, day=1, tzinfo=timezone.utc)


def _parse_odoo_date(value: str) -> datetime | None:
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m", "%B %Y")
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_group_month(row: dict[str, Any]) -> tuple[int, int] | None:
    range_payload = row.get("__range")
    if isinstance(range_payload, dict):
        month_range = range_payload.get("date_order:month")
        if isinstance(month_range, dict):
            start = month_range.get("from")
            if isinstance(start, str):
                parsed = _parse_odoo_date(start)
                if parsed:
                    return parsed.year, parsed.month

    grouped = row.get("date_order:month") or row.get("date_order")
    if isinstance(grouped, str):
        parsed = _parse_odoo_date(grouped)
        if parsed:
            return parsed.year, parsed.month
    return None


def _fetch_available_models(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    active_source: DataSource,
    warnings: list[str],
) -> set[str]:
    try:
        models = {item["model"] for item in client.list_models(user_id)}
        logger.info(
            "[dashboard][diagnostics] discovered models from Odoo",
            extra={"source_id": str(active_source.id), "models_count": len(models)},
        )
        return models
    except OdooRequestError as exc:
        available_models = _model_set_from_cache(active_source)
        if available_models:
            _append_warning(
                warnings,
                "Unable to refresh model list from Odoo; using cached model availability.",
            )
            logger.warning(
                "[dashboard][diagnostics] model list fetch failed, using cache",
                extra={"source_id": str(active_source.id), "error": str(exc)},
            )
            return available_models

        _append_warning(warnings, "Unable to refresh model list from Odoo; using best-effort metrics.")
        logger.warning(
            "[dashboard][diagnostics] model list fetch failed, no cache",
            extra={"source_id": str(active_source.id), "error": str(exc)},
        )
        return set()


def _field_exists(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model: str,
    field_name: str,
    metric_name: str,
    model_exists_fn: Callable[[str], bool],
) -> bool:
    if not model_exists_fn(model):
        return False

    _log_metric_rpc_call(
        metric_name=metric_name,
        model=model,
        method="fields_get",
        domain=[],
        fields=[field_name],
        kwargs={"attributes": ["type"]},
    )
    try:
        payload = client.execute_kw(
            user_id=user_id,
            model=model,
            method="fields_get",
            args=[[field_name]],
            kwargs={"attributes": ["type"]},
        )
    except OdooRequestError as exc:
        logger.debug(
            "[dashboard][metric][%s] fields_get probe failed",
            metric_name,
            extra={"model": model, "field": field_name, "error": str(exc)},
            exc_info=True,
        )
        return False

    return isinstance(payload, dict) and field_name in payload


def _compute_customers_metric(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
) -> tuple[int, list[str]]:
    metric_warnings: list[str] = []

    primary_total, primary_error = safe_search_count(
        "res.partner",
        [],
        "customers.total",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if not primary_error:
        return primary_total, metric_warnings

    logger.warning(
        "[dashboard][metric][customers] base count failed, attempting fallback",
        extra={"model": "res.partner", "error": primary_error},
    )
    if _field_exists(
        client=client,
        user_id=user_id,
        model="res.partner",
        field_name="is_company",
        metric_name="customers.is_company_field_probe",
        model_exists_fn=model_exists_fn,
    ):
        fallback_total, fallback_error = safe_search_count(
            "res.partner",
            [("is_company", "=", True)],
            "customers.is_company_fallback",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists_fn,
        )
        if not fallback_error:
            metric_warnings.append(CUSTOMERS_BASIC_WARNING)
            return fallback_total, metric_warnings

        logger.warning(
            "[dashboard][metric][customers] is_company fallback failed",
            extra={"model": "res.partner", "error": fallback_error},
        )

    metric_warnings.append(GENERIC_DATA_WARNING)
    return 0, metric_warnings


def _compute_products_metric(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
) -> tuple[int, list[str]]:
    metric_warnings: list[str] = []
    product_variant_available = model_exists_fn("product.product")
    product_template_available = model_exists_fn("product.template")

    if not product_variant_available and not product_template_available:
        metric_warnings.append(PRODUCTS_MODULE_WARNING)
        logger.info(
            "[dashboard][metric][products] skipped because product models are unavailable",
            extra={"product_product": False, "product_template": False},
        )
        return 0, metric_warnings

    if product_variant_available:
        products_product, product_error = safe_search_count(
            "product.product",
            [],
            "products.product_product",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists_fn,
        )
        if not product_error:
            return products_product, metric_warnings
        logger.warning(
            "[dashboard][metric][products] product.product count failed",
            extra={"model": "product.product", "error": product_error},
        )

    if product_template_available:
        products_template, template_error = safe_search_count(
            "product.template",
            [],
            "products.product_template",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists_fn,
        )
        if not template_error:
            return products_template, metric_warnings
        logger.warning(
            "[dashboard][metric][products] product.template count failed",
            extra={"model": "product.template", "error": template_error},
        )

    metric_warnings.append("Products data is temporarily unavailable.")
    return 0, metric_warnings


def _compute_quotations_metric(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
) -> tuple[int, list[str]]:
    metric_warnings: list[str] = []
    model = "sale.order"

    if not model_exists_fn(model):
        metric_warnings.append(SALES_MODULE_WARNING)
        logger.info(
            "[dashboard][metric][quotations] skipped because sale.order is unavailable",
            extra={"model": model},
        )
        return 0, metric_warnings

    filtered_domain = [("state", "in", ["draft", "sent"])]
    filtered, filtered_error = safe_search_count(
        model,
        filtered_domain,
        "quotations.filtered",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if not filtered_error:
        return filtered, metric_warnings

    fallback, fallback_error = safe_search_count(
        model,
        [],
        "quotations.total_fallback",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if not fallback_error:
        metric_warnings.append("Quotations loaded with basic counting because filtered states are unavailable.")
        return fallback, metric_warnings

    logger.warning(
        "[dashboard][metric][quotations] both filtered and fallback queries failed",
        extra={"model": model, "filtered_error": filtered_error, "fallback_error": fallback_error},
    )
    metric_warnings.append("Quotations data is temporarily unavailable.")
    return 0, metric_warnings


def _compute_sales_orders_metric(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
) -> tuple[int, list[str]]:
    metric_warnings: list[str] = []
    model = "sale.order"

    if not model_exists_fn(model):
        metric_warnings.append(SALES_MODULE_WARNING)
        logger.info(
            "[dashboard][metric][sales_orders] skipped because sale.order is unavailable",
            extra={"model": model},
        )
        return 0, metric_warnings

    filtered_domain = [("state", "in", ["sale", "done"])]
    filtered, filtered_error = safe_search_count(
        model,
        filtered_domain,
        "sales_orders.filtered",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if not filtered_error:
        return filtered, metric_warnings

    fallback, fallback_error = safe_search_count(
        model,
        [],
        "sales_orders.total_fallback",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if not fallback_error:
        metric_warnings.append("Sales orders loaded with basic counting because filtered states are unavailable.")
        return fallback, metric_warnings

    logger.warning(
        "[dashboard][metric][sales_orders] both filtered and fallback queries failed",
        extra={"model": model, "filtered_error": filtered_error, "fallback_error": fallback_error},
    )
    metric_warnings.append("Sales orders data is temporarily unavailable.")
    return 0, metric_warnings

def _compute_invoices_metric(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
) -> tuple[int, list[str]]:
    metric_warnings: list[str] = []
    model = "account.move"

    if not model_exists_fn(model):
        metric_warnings.append(INVOICE_MODULE_WARNING)
        logger.info(
            "[dashboard][metric][invoices] skipped because account.move is unavailable",
            extra={"model": model},
        )
        return 0, metric_warnings

    filtered_domain = [("move_type", "=", "out_invoice")]
    filtered, filtered_error = safe_search_count(
        model,
        filtered_domain,
        "invoices.filtered",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if not filtered_error:
        return filtered, metric_warnings

    fallback, fallback_error = safe_search_count(
        model,
        [],
        "invoices.total_fallback",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if not fallback_error:
        metric_warnings.append("Invoices loaded with basic counting because invoice type filters are unavailable.")
        return fallback, metric_warnings

    logger.warning(
        "[dashboard][metric][invoices] both filtered and fallback queries failed",
        extra={"model": model, "filtered_error": filtered_error, "fallback_error": fallback_error},
    )
    metric_warnings.append("Invoice data is temporarily unavailable.")
    return 0, metric_warnings


def _compute_revenue_total_metric(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
) -> tuple[float, list[str]]:
    metric_warnings: list[str] = []
    if not model_exists_fn("sale.order"):
        metric_warnings.append(SALES_MODULE_WARNING)
        return 0.0, metric_warnings

    grouped, grouped_error = safe_read_group(
        "sale.order",
        [("state", "in", ["sale", "done"])],
        ["amount_total:sum"],
        [],
        "revenue_total.grouped",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
        limit=1,
    )
    if grouped and not grouped_error:
        return round(_extract_sum_value(grouped[0], "amount_total"), 2), metric_warnings
    if grouped_error:
        logger.warning(
            "[dashboard][metric][revenue_total] grouped query failed",
            extra={"model": "sale.order", "error": grouped_error},
        )
        metric_warnings.append("Revenue is temporarily unavailable because sales totals could not be aggregated.")
    return 0.0, metric_warnings


def _compute_simple_count_metric(
    *,
    metric_name: str,
    model: str,
    domain: list[Any],
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
) -> tuple[int, list[str]]:
    value, error = safe_search_count(
        model,
        domain,
        metric_name,
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    if error:
        logger.warning(
            "[dashboard][metric][%s] simple count failed",
            metric_name,
            extra={"model": model, "error": error},
        )
        return 0, [f"{metric_name.replace('_', ' ').title()} is currently unavailable."]
    return value, []


def get_sales_by_month(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
    warnings: list[str],
    month_count: int = 6,
) -> list[dict[str, str | float]]:
    if month_count <= 0:
        return []
    if not model_exists_fn("sale.order"):
        _append_warning(warnings, SALES_MODULE_WARNING)
        return []

    rows, error = safe_search_read(
        "sale.order",
        [("state", "in", ["sale", "done"])],
        ["amount_total", "date_order", "partner_id"],
        "sales_by_month",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
        limit=5000,
        order="date_order asc",
    )
    if error:
        _append_warning(warnings, "Sales trends are temporarily unavailable.")
        return []

    month_revenue: dict[str, float] = {}
    for row in rows:
        order_date = row.get("date_order")
        if not isinstance(order_date, str):
            continue
        parsed_date = _parse_odoo_date(order_date)
        if not parsed_date:
            continue
        month_key = f"{parsed_date.year:04d}-{parsed_date.month:02d}"
        month_revenue[month_key] = month_revenue.get(month_key, 0.0) + _to_float(row.get("amount_total"))

    if not month_revenue:
        _append_warning(warnings, NO_SALES_DATA_WARNING)
        return []

    sorted_months = sorted(month_revenue.items(), key=lambda item: item[0])
    if month_count > 0:
        sorted_months = sorted_months[-month_count:]

    return [{"month": month, "revenue": round(revenue, 2)} for month, revenue in sorted_months]


def get_top_customers(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
    warnings: list[str],
    limit: int = 5,
) -> list[dict[str, str | float]]:
    if not model_exists_fn("sale.order"):
        _append_warning(warnings, SALES_MODULE_WARNING)
        return []

    rows, error = safe_search_read(
        "sale.order",
        [("state", "in", ["sale", "done"])],
        ["amount_total", "date_order", "partner_id"],
        "top_customers",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
        limit=5000,
        order="date_order desc",
    )
    if error:
        _append_warning(warnings, "Top customers chart is temporarily unavailable.")
        return []

    customer_totals: dict[str, float] = {}
    for row in rows:
        partner = row.get("partner_id")
        name = _extract_many2one_name(partner, default="Unknown Customer")
        customer_totals[name] = customer_totals.get(name, 0.0) + _to_float(row.get("amount_total"))

    if not customer_totals:
        _append_warning(warnings, NO_SALES_DATA_WARNING)
        return []

    ranked_customers = sorted(customer_totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    items: list[dict[str, str | float]] = []
    for customer_name, revenue in ranked_customers:
        if revenue <= 0:
            continue
        items.append({"name": customer_name, "revenue": round(revenue, 2)})
    return items


def get_top_products(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
    warnings: list[str],
    limit: int = 5,
) -> list[dict[str, str | float]]:
    if not model_exists_fn("sale.order.line"):
        _append_warning(warnings, SALES_MODULE_WARNING)
        return []

    rows, error = safe_search_read(
        "sale.order.line",
        [("order_id.state", "in", ["sale", "done"])],
        ["product_id", "price_total"],
        "top_products",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
        limit=5000,
        order="price_total desc",
    )
    if error:
        _append_warning(warnings, "Top products chart is temporarily unavailable.")
        return []

    product_totals_by_id: dict[int, float] = {}
    product_display_hints: dict[int, str] = {}
    for row in rows:
        product_ref = row.get("product_id")
        product_id = _extract_many2one_id(product_ref)
        product_name = _extract_many2one_name(product_ref, default="Unknown Product")
        if product_id is None:
            continue
        product_display_hints[product_id] = product_name
        product_totals_by_id[product_id] = product_totals_by_id.get(product_id, 0.0) + _to_float(row.get("price_total"))

    if not product_totals_by_id:
        _append_warning(warnings, NO_SALES_DATA_WARNING)
        return []

    product_name_map = _resolve_top_product_names(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
        product_ids=set(product_totals_by_id.keys()),
        display_hint_by_id=product_display_hints,
    )

    ranked_products = sorted(product_totals_by_id.items(), key=lambda item: item[1], reverse=True)[:limit]
    items: list[dict[str, str | float]] = []
    for product_id, revenue in ranked_products:
        if revenue <= 0:
            continue
        short_name, full_name = product_name_map.get(
            product_id,
            (product_display_hints.get(product_id, "Unknown Product"), product_display_hints.get(product_id, "Unknown Product")),
        )
        items.append(
            {
                "name": short_name,
                "short_name": short_name,
                "full_name": full_name,
                "revenue": round(revenue, 2),
            }
        )
    return items


def get_invoice_status(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
    warnings: list[str],
) -> list[dict[str, int | str]]:
    if not model_exists_fn("account.move"):
        _append_warning(warnings, INVOICE_MODULE_WARNING)
        return []

    # Keep this user-facing chart intuitive:
    # "Paid" and "Unpaid" are based on posted customer invoices/refunds only.
    posted_domain = [("move_type", "in", ["out_invoice", "out_refund"]), ("state", "=", "posted")]
    posted_total, posted_total_error = safe_search_count(
        "account.move",
        posted_domain,
        "invoice_status.posted_total",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )
    paid, paid_error = safe_search_count(
        "account.move",
        [*posted_domain, ("payment_state", "=", "paid")],
        "invoice_status.paid",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
    )

    unpaid = 0
    unpaid_error: str | None = None
    if not posted_total_error and not paid_error:
        unpaid = max(posted_total - paid, 0)
    else:
        unpaid, unpaid_error = safe_search_count(
            "account.move",
            [*posted_domain, ("payment_state", "!=", "paid")],
            "invoice_status.unpaid",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists_fn,
        )

    if posted_total_error or paid_error or unpaid_error:
        _append_warning(warnings, "Invoice status breakdown is temporarily unavailable.")

    return [{"label": "Paid", "value": paid}, {"label": "Unpaid", "value": unpaid}]


def get_low_stock_items(
    *,
    client: OdooJsonRpcClient,
    user_id: int,
    model_exists_fn: Callable[[str], bool],
    warnings: list[str],
    limit: int = 8,
) -> list[dict[str, float | str]]:
    if not model_exists_fn("stock.quant") or not model_exists_fn("stock.warehouse.orderpoint"):
        _append_warning(warnings, PRODUCTS_MODULE_WARNING)
        return []

    stock_quants, quant_error = safe_search_read(
        "stock.quant",
        [("location_id.usage", "=", "internal"), ("quantity", "!=", 0)],
        ["product_id", "company_id", "quantity"],
        "low_stock.stock_quant",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
        limit=5000,
        order="product_id asc",
    )
    if quant_error:
        logger.warning(
            "[dashboard][metric][low_stock] stock.quant fetch failed",
            extra={"error": quant_error},
        )
        _append_warning(warnings, "Low stock list is temporarily unavailable.")
        return []

    orderpoints, orderpoint_error = safe_search_read(
        "stock.warehouse.orderpoint",
        [],
        ["product_id", "company_id", "product_min_qty"],
        "low_stock.orderpoints",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists_fn,
        limit=5000,
        order="product_id asc",
    )
    if orderpoint_error:
        logger.warning(
            "[dashboard][metric][low_stock] stock.warehouse.orderpoint fetch failed",
            extra={"error": orderpoint_error},
        )
        _append_warning(warnings, "Low stock list is temporarily unavailable.")
        return []

    logger.info(
        "[dashboard][metric][low_stock] source records fetched",
        extra={"stock_quant_records": len(stock_quants), "orderpoint_records": len(orderpoints)},
    )

    quantity_by_product: dict[int, float] = {}
    quantity_by_product_company: dict[tuple[int, int | None], float] = {}
    product_name_by_id: dict[int, str] = {}
    for quant in stock_quants:
        product_ref = quant.get("product_id")
        if not isinstance(product_ref, (list, tuple)) or len(product_ref) < 2:
            continue
        if not isinstance(product_ref[0], int):
            continue
        product_id = product_ref[0]
        product_name = product_ref[1] if isinstance(product_ref[1], str) else "Unknown Product"
        product_name_by_id[product_id] = product_name
        quantity_value = _to_float(quant.get("quantity"))
        quantity_by_product[product_id] = quantity_by_product.get(product_id, 0.0) + quantity_value

        company_id = _extract_many2one_id(quant.get("company_id"))
        company_key = (product_id, company_id)
        quantity_by_product_company[company_key] = quantity_by_product_company.get(company_key, 0.0) + quantity_value

    min_qty_by_product_company: dict[tuple[int, int | None], float] = {}
    for orderpoint in orderpoints:
        product_ref = orderpoint.get("product_id")
        if not isinstance(product_ref, (list, tuple)) or len(product_ref) < 2:
            continue
        if not isinstance(product_ref[0], int):
            continue
        product_id = product_ref[0]
        product_name = product_ref[1] if isinstance(product_ref[1], str) else "Unknown Product"
        product_name_by_id.setdefault(product_id, product_name)
        min_qty = _to_float(orderpoint.get("product_min_qty"))
        company_id = _extract_many2one_id(orderpoint.get("company_id"))
        company_key = (product_id, company_id)
        if company_key in min_qty_by_product_company:
            min_qty_by_product_company[company_key] = max(min_qty_by_product_company[company_key], min_qty)
        else:
            min_qty_by_product_company[company_key] = min_qty

    low_stock_by_product: dict[int, dict[str, float | str]] = {}
    for (product_id, company_id), min_qty in min_qty_by_product_company.items():
        quantity = quantity_by_product_company.get((product_id, company_id), quantity_by_product.get(product_id, 0.0))
        if quantity < min_qty:
            shortage = max(min_qty - quantity, 0.0)
            existing = low_stock_by_product.get(product_id)
            if existing is None or _to_float(existing.get("shortage")) < shortage:
                low_stock_by_product[product_id] = {
                    "product": product_name_by_id.get(product_id, "Unknown Product"),
                    "quantity": round(quantity, 2),
                    "min_required": round(min_qty, 2),
                    "shortage": round(shortage, 2),
                }

    low_stock_items = list(low_stock_by_product.values())
    low_stock_items.sort(key=lambda item: (-float(item["shortage"]), float(item["quantity"])))
    logger.info(
        "[dashboard][metric][low_stock] low stock computed",
        extra={"matched_low_stock_items": len(low_stock_items)},
    )
    return low_stock_items[:limit]


def _format_low_stock_chart_items(items: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
    chart_rows: list[dict[str, float | str]] = []
    for row in items:
        product = row.get("product")
        quantity = row.get("quantity")
        min_required = row.get("min_required")
        shortage = row.get("shortage")
        if not isinstance(product, str):
            continue
        chart_rows.append(
            {
                "name": product,
                "qty": _to_float(quantity),
                "min_required": _to_float(min_required),
                "shortage": _to_float(shortage),
            }
        )
    return chart_rows


def _format_recent_sales_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    for row in rows:
        formatted.append(
            {
                "id": row.get("id") if isinstance(row.get("id"), int) else None,
                "name": row.get("name") if isinstance(row.get("name"), str) else "N/A",
                "customer": _extract_many2one_name(row.get("partner_id")),
                "amount_total": _to_float(row.get("amount_total")),
                "state": row.get("state") if isinstance(row.get("state"), str) else "unknown",
                "date_order": row.get("date_order") if isinstance(row.get("date_order"), str) else None,
            }
        )
    return formatted


def _format_top_customers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    for row in rows:
        formatted.append(
            {
                "id": row.get("id") if isinstance(row.get("id"), int) else None,
                "name": row.get("name") if isinstance(row.get("name"), str) else "Unknown",
            }
        )
    return formatted

def _build_runtime_context(
    db: Session,
) -> tuple[DataSource, OdooJsonRpcClient, int, OdooModelResolver, set[str], list[str]]:
    active_source = data_source_repo.get_active_data_source(db)
    if not active_source:
        raise DashboardSourceNotConfiguredError(
            "No active Odoo source configured. Activate a source in Settings first."
        )

    try:
        api_key = decrypt_value(active_source.api_key_encrypted)
    except ValueError as exc:
        raise DashboardCredentialError(
            "Active Odoo credentials cannot be decrypted. Re-save the active source API key."
        ) from exc

    client = _build_client(active_source, api_key)
    user_id = client.authenticate()

    warnings: list[str] = []
    available_models = _fetch_available_models(
        client=client,
        user_id=user_id,
        active_source=active_source,
        warnings=warnings,
    )
    resolver = OdooModelResolver(
        client=client,
        user_id=user_id,
        discovered_models=available_models,
    )
    return active_source, client, user_id, resolver, available_models, warnings


def get_dashboard_summary(db: Session) -> dict[str, Any]:
    active_source, client, user_id, resolver, available_models, warnings = _build_runtime_context(db)

    logger.info(
        "[dashboard][summary] fetch started",
        extra={
            "source_id": str(active_source.id),
            "source_name": active_source.name,
            "database_name": active_source.database_name,
            "odoo_url": active_source.odoo_url,
            "available_models_count": len(available_models),
        },
    )

    def model_exists(model_name: str) -> bool:
        return resolver.model_exists(model_name)

    metrics: dict[str, Any] = {}

    customers, customers_warnings = _compute_customers_metric(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["customers"] = customers
    for warning in customers_warnings:
        _append_warning(warnings, warning)

    products, products_warnings = _compute_products_metric(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["products"] = products
    for warning in products_warnings:
        _append_warning(warnings, warning)

    quotations, quotations_warnings = _compute_quotations_metric(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["quotations"] = quotations
    for warning in quotations_warnings:
        _append_warning(warnings, warning)

    sales_orders, sales_orders_warnings = _compute_sales_orders_metric(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["sales_orders"] = sales_orders
    for warning in sales_orders_warnings:
        _append_warning(warnings, warning)

    invoices, invoices_warnings = _compute_invoices_metric(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["invoices"] = invoices
    for warning in invoices_warnings:
        _append_warning(warnings, warning)

    revenue_total, revenue_warnings = _compute_revenue_total_metric(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["revenue_total"] = revenue_total
    for warning in revenue_warnings:
        _append_warning(warnings, warning)

    companies, companies_warnings = _compute_simple_count_metric(
        metric_name="companies",
        model="res.company",
        domain=[],
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["companies"] = companies
    for warning in companies_warnings:
        _append_warning(warnings, warning)

    purchase_orders, purchase_orders_warnings = _compute_simple_count_metric(
        metric_name="purchase_orders",
        model="purchase.order",
        domain=[],
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    metrics["purchase_orders"] = purchase_orders
    for warning in purchase_orders_warnings:
        _append_warning(warnings, warning)

    low_stock_items = get_low_stock_items(
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
        warnings=warnings,
    )

    charts = {
        "sales_by_month": get_sales_by_month(
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            warnings=warnings,
        ),
        "top_customers": get_top_customers(
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            warnings=warnings,
        ),
        "top_products": get_top_products(
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            warnings=warnings,
        ),
        "invoice_status": get_invoice_status(
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            warnings=warnings,
        ),
        "low_stock_items": _format_low_stock_chart_items(low_stock_items),
    }

    recent_sales_orders_raw: list[dict[str, Any]] = []
    if model_exists("sale.order"):
        recent_sales_orders_raw, recent_sales_error = safe_search_read(
            "sale.order",
            [],
            ["id", "name", "partner_id", "amount_total", "state", "date_order"],
            "recent_sales_orders",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            limit=8,
            order="date_order desc",
        )
        if recent_sales_error:
            _append_warning(warnings, "Recent sales orders are temporarily unavailable.")
    else:
        _append_warning(warnings, SALES_MODULE_WARNING)

    top_customers_raw, top_customers_error = safe_search_read(
        "res.partner",
        [],
        ["id", "name"],
        "top_customers_list",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
        limit=5,
        order="name asc",
    )
    if top_customers_error:
        _append_warning(warnings, "Top customers list is temporarily unavailable.")

    summary = {
        "status": "connected",
        "source": "odoo",
        "database": active_source.database_name,
        "metrics": metrics,
        "charts": charts,
        "low_stock": low_stock_items,
        "recent_sales_orders": _format_recent_sales_orders(recent_sales_orders_raw),
        "top_customers": _format_top_customers(top_customers_raw),
        "warnings": warnings,
    }

    logger.info(
        "[dashboard][summary] fetch completed",
        extra={
            "source_id": str(active_source.id),
            "warnings_count": len(warnings),
            "metrics": metrics,
            "charts_sizes": {key: len(value) for key, value in charts.items()},
        },
    )
    return summary


def _build_metric_test_payload(
    *,
    ok: bool,
    value: int | float,
    strategy: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "value": value,
        "strategy": strategy,
        "error": error,
    }


def get_dashboard_diagnostics(db: Session) -> dict[str, Any]:
    active_source, client, user_id, resolver, _available_models, warnings = _build_runtime_context(db)

    logger.info(
        "[dashboard][diagnostics] started",
        extra={"source_id": str(active_source.id), "source_name": active_source.name},
    )

    def model_exists(model_name: str) -> bool:
        return resolver.model_exists(model_name)

    model_map = {model_name: model_exists(model_name) for model_name in _DIAGNOSTIC_MODELS}

    customers_value, customers_error = safe_search_count(
        "res.partner",
        [],
        "diagnostics.customers",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    products_product_value, products_product_error = safe_search_count(
        "product.product",
        [],
        "diagnostics.products_product",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    products_template_value, products_template_error = safe_search_count(
        "product.template",
        [],
        "diagnostics.products_template",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    quotations_value, quotations_error = safe_search_count(
        "sale.order",
        [("state", "in", ["draft", "sent"])],
        "diagnostics.quotations",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    sales_orders_value, sales_orders_error = safe_search_count(
        "sale.order",
        [("state", "in", ["sale", "done"])],
        "diagnostics.sales_orders",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )
    invoices_value, invoices_error = safe_search_count(
        "account.move",
        [("move_type", "=", "out_invoice")],
        "diagnostics.invoices",
        client=client,
        user_id=user_id,
        model_exists_fn=model_exists,
    )

    diagnostics_payload = {
        "active_source": {
            "name": active_source.name,
            "url": active_source.odoo_url,
            "database": active_source.database_name,
            "username": active_source.username,
        },
        "models": model_map,
        "metric_tests": {
            "customers": _build_metric_test_payload(
                ok=customers_error is None,
                value=customers_value,
                strategy="search_count([])",
                error=customers_error,
            ),
            "products_product": _build_metric_test_payload(
                ok=products_product_error is None,
                value=products_product_value,
                strategy="search_count([])",
                error=products_product_error,
            ),
            "products_template": _build_metric_test_payload(
                ok=products_template_error is None,
                value=products_template_value,
                strategy="search_count([])",
                error=products_template_error,
            ),
            "quotations": _build_metric_test_payload(
                ok=quotations_error is None,
                value=quotations_value,
                strategy="[('state','in',['draft','sent'])]",
                error=quotations_error,
            ),
            "sales_orders": _build_metric_test_payload(
                ok=sales_orders_error is None,
                value=sales_orders_value,
                strategy="[('state','in',['sale','done'])]",
                error=sales_orders_error,
            ),
            "invoices": _build_metric_test_payload(
                ok=invoices_error is None,
                value=invoices_value,
                strategy="[('move_type','=','out_invoice')]",
                error=invoices_error,
            ),
        },
        "warnings": warnings,
    }

    logger.info(
        "[dashboard][diagnostics] completed",
        extra={
            "source_id": str(active_source.id),
            "models": model_map,
            "metric_test_errors": {
                key: payload["error"]
                for key, payload in diagnostics_payload["metric_tests"].items()
                if payload["error"]
            },
        },
    )
    return diagnostics_payload
