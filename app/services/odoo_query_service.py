"""
Odoo query service used by AI orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.odoo import OdooJsonRpcClient, OdooRequestError
from app.models.data_source import DataSource
from app.repositories import data_source_repository as data_source_repo
from app.security.encryption import decrypt_value


class OdooQuerySourceNotConfiguredError(RuntimeError):
    """Raised when there is no active Odoo source."""


class OdooQueryCredentialError(RuntimeError):
    """Raised when active Odoo credentials cannot be decrypted."""


class OdooQueryExecutionError(RuntimeError):
    """Raised when an Odoo query could not be executed."""


@dataclass
class OdooRuntimeContext:
    data_source: DataSource
    client: OdooJsonRpcClient
    user_id: int
    available_models: set[str]


class OdooQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_runtime_context(self) -> OdooRuntimeContext:
        active_source = data_source_repo.get_active_data_source(self.db)
        if not active_source:
            raise OdooQuerySourceNotConfiguredError(
                "No active Odoo source configured. Activate a source in Settings first."
            )

        try:
            api_key = decrypt_value(active_source.api_key_encrypted)
        except ValueError as exc:
            raise OdooQueryCredentialError(
                "Active Odoo credentials cannot be decrypted. Re-save the active source API key."
            ) from exc

        client = OdooJsonRpcClient(
            base_url=active_source.odoo_url.strip().rstrip("/"),
            database_name=active_source.database_name.strip(),
            username=active_source.username.strip(),
            api_key=api_key,
            timeout_seconds=10.0,
        )
        user_id = client.authenticate()
        available_models = self._load_available_models(
            active_source=active_source,
            client=client,
            user_id=user_id,
        )

        return OdooRuntimeContext(
            data_source=active_source,
            client=client,
            user_id=user_id,
            available_models=available_models,
        )

    def get_top_customers(
        self,
        runtime: OdooRuntimeContext,
        *,
        limit: int = 5,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_model(runtime, "sale.order")
        safe_limit = self._coerce_limit(limit, default=5, maximum=100)
        domain = [("state", "in", ["sale", "done"])]
        domain.extend(self._date_domain("date_order", date_from=date_from, date_to=date_to))
        rows = self._read_group(
            runtime,
            model="sale.order",
            domain=domain,
            fields=["partner_id", "amount_total:sum", "id:count"],
            groupby=["partner_id"],
        )

        if not rows:
            return []

        customers: list[dict[str, Any]] = []
        for row in rows:
            partner_ref = row.get("partner_id")
            customer_id = self._many2one_id(partner_ref)
            if customer_id is None:
                continue

            total_sales = round(self._safe_float(self._extract_sum_value(row, "amount_total")), 2)
            raw_order_count = row.get("__count")
            if not isinstance(raw_order_count, (int, float)):
                raw_order_count = row.get("id_count")
            order_count = self._safe_int(raw_order_count)

            customers.append(
                {
                    "customer_id": customer_id,
                    "customer_name": self._many2one_name(partner_ref, default="Unknown Customer"),
                    "total_sales": total_sales,
                    "order_count": order_count,
                }
            )

        customers.sort(
            key=lambda item: (
                -float(item["total_sales"]),
                -int(item["order_count"]),
                str(item["customer_name"]).lower(),
            )
        )
        return customers[:safe_limit]

    def get_top_products(
        self,
        runtime: OdooRuntimeContext,
        *,
        limit: int = 5,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_model(runtime, "sale.order.line")
        safe_limit = self._coerce_limit(limit, default=5, maximum=100)
        domain = [("order_id.state", "in", ["sale", "done"])]
        domain.extend(self._date_domain("order_id.date_order", date_from=date_from, date_to=date_to))
        rows = self._read_group(
            runtime,
            model="sale.order.line",
            domain=domain,
            fields=["product_id", "price_total:sum", "product_uom_qty:sum", "id:count"],
            groupby=["product_id"],
        )
        if not rows:
            return []

        products: list[dict[str, Any]] = []
        for row in rows:
            product_ref = row.get("product_id")
            product_id = self._many2one_id(product_ref)
            if product_id is None:
                continue

            products.append(
                {
                    "product_id": product_id,
                    "product_name": self._many2one_name(product_ref, default="Unknown Product"),
                    "quantity_sold": round(self._safe_float(self._extract_sum_value(row, "product_uom_qty")), 2),
                    "revenue": round(self._safe_float(self._extract_sum_value(row, "price_total")), 2),
                }
            )

        products.sort(
            key=lambda item: (
                -float(item["revenue"]),
                -float(item["quantity_sold"]),
                str(item["product_name"]).lower(),
            )
        )
        return products[:safe_limit]

    def get_product_sales_history(
        self,
        runtime: OdooRuntimeContext,
        *,
        months: int = 36,
        max_lines: int = 10000,
    ) -> list[dict[str, Any]]:
        self._ensure_model(runtime, "sale.order")
        self._ensure_model(runtime, "sale.order.line")

        safe_months = self._coerce_limit(months, default=36, maximum=60)
        safe_max_lines = self._coerce_limit(max_lines, default=10000, maximum=50000)
        start_year, start_month = self._month_offset(datetime.now(timezone.utc).year, datetime.now(timezone.utc).month, -(safe_months - 1))
        date_from = f"{start_year}-{start_month:02d}-01 00:00:00"

        try:
            line_rows = runtime.client.search_read(
                user_id=runtime.user_id,
                model="sale.order.line",
                domain=[
                    ("order_id.state", "in", ["sale", "done"]),
                    ("order_id.date_order", ">=", date_from),
                ],
                fields=["product_id", "order_id", "price_total", "product_uom_qty"],
                limit=safe_max_lines,
                order="id asc",
            )
        except OdooRequestError as exc:
            raise OdooQueryExecutionError(str(exc)) from exc

        order_ids = sorted(
            {
                order_id
                for row in line_rows
                if (order_id := self._many2one_id(row.get("order_id"))) is not None
            }
        )
        if not order_ids:
            return []

        try:
            order_rows = runtime.client.search_read(
                user_id=runtime.user_id,
                model="sale.order",
                domain=[("id", "in", order_ids)],
                fields=["id", "date_order"],
                limit=max(len(order_ids), 1),
                order="date_order asc",
            )
        except OdooRequestError as exc:
            raise OdooQueryExecutionError(str(exc)) from exc

        order_months: dict[int, str] = {}
        for row in order_rows:
            raw_id = row.get("id")
            parsed_date = self._parse_odoo_datetime(row.get("date_order"))
            if isinstance(raw_id, int) and parsed_date:
                order_months[raw_id] = f"{parsed_date.year:04d}-{parsed_date.month:02d}"

        grouped: dict[tuple[int, str], dict[str, Any]] = {}
        for row in line_rows:
            product_ref = row.get("product_id")
            product_id = self._many2one_id(product_ref)
            order_id = self._many2one_id(row.get("order_id"))
            month = order_months.get(order_id) if order_id is not None else None
            if product_id is None or not month:
                continue

            key = (product_id, month)
            product_name = self._many2one_name(product_ref, default="Unknown Product")
            entry = grouped.setdefault(
                key,
                {
                    "product_id": product_id,
                    "product_name": product_name,
                    "month": month,
                    "revenue": 0.0,
                    "quantity_sold": 0.0,
                },
            )
            entry["revenue"] = round(float(entry["revenue"]) + self._safe_float(row.get("price_total")), 2)
            entry["quantity_sold"] = round(float(entry["quantity_sold"]) + self._safe_float(row.get("product_uom_qty")), 2)

        return sorted(grouped.values(), key=lambda item: (str(item["month"]), str(item["product_name"]).lower()))

    def get_sales_summary(
        self,
        runtime: OdooRuntimeContext,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_model(runtime, "sale.order")
        date_domain = self._date_domain("date_order", date_from=date_from, date_to=date_to)

        total_orders = runtime.client.search_count(
            user_id=runtime.user_id,
            model="sale.order",
            domain=date_domain,
        )
        quotations = runtime.client.search_count(
            user_id=runtime.user_id,
            model="sale.order",
            domain=[("state", "in", ["draft", "sent"]), *date_domain],
        )
        confirmed_orders = runtime.client.search_count(
            user_id=runtime.user_id,
            model="sale.order",
            domain=[("state", "in", ["sale", "done"]), *date_domain],
        )
        grouped = self._read_group(
            runtime,
            model="sale.order",
            domain=[("state", "in", ["sale", "done"]), *date_domain],
            fields=["amount_total:sum"],
            groupby=[],
            limit=1,
        )
        revenue_total = self._extract_sum_value(grouped[0], "amount_total") if grouped else 0.0

        return {
            "total_orders": self._safe_int(total_orders),
            "confirmed_orders": self._safe_int(confirmed_orders),
            "quotations": self._safe_int(quotations),
            "revenue_total": round(self._safe_float(revenue_total), 2),
        }

    def get_invoice_status_summary(self, runtime: OdooRuntimeContext) -> dict[str, Any]:
        self._ensure_model(runtime, "account.move")

        base_domain = [("move_type", "=", "out_invoice"), ("state", "=", "posted")]
        total_posted = runtime.client.search_count(
            user_id=runtime.user_id,
            model="account.move",
            domain=base_domain,
        )
        paid = runtime.client.search_count(
            user_id=runtime.user_id,
            model="account.move",
            domain=[*base_domain, ("payment_state", "=", "paid")],
        )
        in_payment = runtime.client.search_count(
            user_id=runtime.user_id,
            model="account.move",
            domain=[*base_domain, ("payment_state", "=", "in_payment")],
        )
        unpaid = max(total_posted - paid - in_payment, 0)
        grouped = self._read_group(
            runtime,
            model="account.move",
            domain=base_domain,
            fields=["payment_state", "amount_total:sum"],
            groupby=["payment_state"],
        )

        by_payment_state: list[dict[str, Any]] = []
        for row in grouped:
            state_value = row.get("payment_state")
            state_label = state_value if isinstance(state_value, str) else "unknown"
            by_payment_state.append(
                {
                    "payment_state": state_label,
                    "amount_total": round(self._extract_sum_value(row, "amount_total"), 2),
                }
            )

        return {
            "total_posted_invoices": total_posted,
            "paid_invoices": paid,
            "in_payment_invoices": in_payment,
            "unpaid_invoices": unpaid,
            "by_payment_state": by_payment_state,
        }

    def get_recent_orders(self, runtime: OdooRuntimeContext, *, limit: int = 10) -> list[dict[str, Any]]:
        self._ensure_model(runtime, "sale.order")
        rows = runtime.client.search_read(
            user_id=runtime.user_id,
            model="sale.order",
            domain=[],
            fields=["id", "name", "partner_id", "amount_total", "state", "date_order"],
            limit=limit,
            order="date_order desc",
        )
        return [
            {
                "id": row.get("id") if isinstance(row.get("id"), int) else None,
                "name": row.get("name") if isinstance(row.get("name"), str) else "N/A",
                "customer_name": self._many2one_name(row.get("partner_id"), default="Unknown Customer"),
                "amount_total": float(row.get("amount_total") or 0.0),
                "state": row.get("state") if isinstance(row.get("state"), str) else "unknown",
                "date_order": row.get("date_order") if isinstance(row.get("date_order"), str) else None,
            }
            for row in rows
        ]

    def _read_group(
        self,
        runtime: OdooRuntimeContext,
        *,
        model: str,
        domain: list[object],
        fields: list[str],
        groupby: list[str],
        limit: int | None = None,
        orderby: str | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"lazy": False}
        if limit is not None:
            kwargs["limit"] = limit
        if orderby:
            kwargs["orderby"] = orderby

        try:
            payload = runtime.client.execute_kw(
                user_id=runtime.user_id,
                model=model,
                method="read_group",
                args=[domain, fields, groupby],
                kwargs=kwargs,
            )
        except OdooRequestError as exc:
            raise OdooQueryExecutionError(str(exc)) from exc

        if not isinstance(payload, list):
            raise OdooQueryExecutionError(
                f"Unexpected read_group payload for model '{model}'."
            )

        return [row for row in payload if isinstance(row, dict)]

    @staticmethod
    def _date_domain(
        field_name: str,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[tuple[str, str, str]]:
        domain: list[tuple[str, str, str]] = []
        if date_from:
            domain.append((field_name, ">=", date_from))
        if date_to:
            domain.append((field_name, "<=", date_to))
        return domain

    @staticmethod
    def _month_offset(year: int, month: int, offset: int) -> tuple[int, int]:
        month_index = year * 12 + (month - 1) + offset
        return month_index // 12, month_index % 12 + 1

    @staticmethod
    def _parse_odoo_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def _load_available_models(
        self,
        *,
        active_source: DataSource,
        client: OdooJsonRpcClient,
        user_id: int,
    ) -> set[str]:
        cached_models = self._model_set_from_cache(active_source)
        if cached_models:
            return cached_models

        models = client.list_models(user_id)
        model_set = {
            item["model"]
            for item in models
            if isinstance(item, dict) and isinstance(item.get("model"), str)
        }
        data_source_repo.update_data_source(
            self.db,
            active_source,
            {
                "models_cache": models,
                "last_tested_at": datetime.now(timezone.utc),
            },
        )
        return model_set

    @staticmethod
    def _model_set_from_cache(data_source: DataSource) -> set[str]:
        if not isinstance(data_source.models_cache, list):
            return set()

        model_names: set[str] = set()
        for item in data_source.models_cache:
            if not isinstance(item, dict):
                continue
            model = item.get("model")
            if isinstance(model, str) and model:
                model_names.add(model)
        return model_names

    def _ensure_model(self, runtime: OdooRuntimeContext, model_name: str) -> None:
        if self._model_exists(runtime, model_name):
            return
        raise OdooQueryExecutionError(f"Required Odoo model '{model_name}' is unavailable.")

    def _model_exists(self, runtime: OdooRuntimeContext, model_name: str) -> bool:
        if model_name in runtime.available_models:
            return True

        try:
            count = runtime.client.search_count(
                user_id=runtime.user_id,
                model="ir.model",
                domain=[("model", "=", model_name)],
            )
            if count > 0:
                runtime.available_models.add(model_name)
                return True
        except OdooRequestError:
            pass

        try:
            runtime.client.search_count(
                user_id=runtime.user_id,
                model=model_name,
                domain=[],
            )
            runtime.available_models.add(model_name)
            return True
        except OdooRequestError:
            return False

    @staticmethod
    def _many2one_name(value: object, *, default: str = "Unknown") -> str:
        if isinstance(value, (list, tuple)) and len(value) > 1 and isinstance(value[1], str):
            return value[1]
        return default

    @staticmethod
    def _many2one_id(value: object) -> int | None:
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], int):
            return value[0]
        return None

    @staticmethod
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

    @staticmethod
    def _safe_float(value: object, *, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return default
            try:
                return float(cleaned)
            except ValueError:
                return default
        return default

    @staticmethod
    def _safe_int(value: object, *, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return default
            try:
                return int(float(cleaned))
            except ValueError:
                return default
        return default

    def _coerce_limit(self, value: object, *, default: int, maximum: int) -> int:
        numeric = self._safe_int(value, default=default)
        if numeric <= 0:
            return default
        return min(numeric, maximum)
