"""
Seed local Odoo with deterministic fake electronics data.

Usage:
    python -m app.scripts.seed_fake_odoo_data --seed-key electronics-v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from app.integrations.odoo import (
    OdooAuthenticationError,
    OdooConnectionError,
    OdooJsonRpcClient,
    OdooRequestError,
)


@dataclass(frozen=True)
class ScaleConfig:
    customer_count: int
    supplier_count: int
    sales_order_count: int
    purchase_order_count: int
    invoice_count: int
    sales_confirm_ratio: float
    purchase_confirm_ratio: float


SCALE_CONFIGS: dict[str, ScaleConfig] = {
    "small": ScaleConfig(
        customer_count=6,
        supplier_count=3,
        sales_order_count=12,
        purchase_order_count=8,
        invoice_count=10,
        sales_confirm_ratio=0.50,
        purchase_confirm_ratio=0.40,
    ),
    "medium": ScaleConfig(
        customer_count=12,
        supplier_count=6,
        sales_order_count=28,
        purchase_order_count=18,
        invoice_count=24,
        sales_confirm_ratio=0.65,
        purchase_confirm_ratio=0.55,
    ),
    "large": ScaleConfig(
        customer_count=24,
        supplier_count=12,
        sales_order_count=64,
        purchase_order_count=40,
        invoice_count=56,
        sales_confirm_ratio=0.75,
        purchase_confirm_ratio=0.70,
    ),
}


DEFAULT_ODOO_URL = "http://localhost:8069"
DEFAULT_ODOO_DB = "odoo"
DEFAULT_ODOO_USER = "admin"
DEFAULT_ODOO_PASSWORD = "admin"


def _env_first(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _mask_secret(value: str) -> str:
    if not value:
        return "<empty>"
    return f"<set, length {len(value)}>"


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "seed"


def _weighted_choice(patterns: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    total = 0
    for item in patterns:
        weight = int(item.get("weight", 1))
        if weight > 0:
            total += weight
    if total <= 0:
        return rng.choice(patterns)

    roll = rng.uniform(0, total)
    cursor = 0.0
    for item in patterns:
        weight = int(item.get("weight", 1))
        if weight <= 0:
            continue
        cursor += weight
        if roll <= cursor:
            return item
    return patterns[-1]


def _recent_datetime(rng: random.Random, *, days_back: int = 120) -> str:
    now = datetime.now(timezone.utc)
    delta_days = rng.randint(0, max(days_back, 1))
    delta_seconds = rng.randint(0, 86_399)
    dt = now - timedelta(days=delta_days, seconds=delta_seconds)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _recent_date(rng: random.Random, *, days_back: int = 120) -> str:
    now = datetime.now(timezone.utc).date()
    delta_days = rng.randint(0, max(days_back, 1))
    dt = now - timedelta(days=delta_days)
    return dt.strftime("%Y-%m-%d")


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    month_index = year * 12 + (month - 1) + offset
    return month_index // 12, month_index % 12 + 1


def _days_in_month(year: int, month: int) -> int:
    next_year, next_month = _add_months(year, month, 1)
    first_next_month = datetime(next_year, next_month, 1, tzinfo=timezone.utc)
    last_this_month = first_next_month - timedelta(days=1)
    return last_this_month.day


class DryRunOdooClient:
    """Minimal offline Odoo client used to make --dry-run instant and credential-free."""

    def search_count(self, *, user_id: int, model: str, domain: list[Any]) -> int:
        return 1

    def search_read(
        self,
        *,
        user_id: int,
        model: str,
        domain: list[Any],
        fields: list[str] | None = None,
        limit: int = 80,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def execute_kw(
        self,
        *,
        user_id: int,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> object:
        return True


class Seeder:
    def __init__(
        self,
        *,
        client: OdooJsonRpcClient,
        user_id: int,
        fixture: dict[str, Any],
        seed_key: str,
        scale: str,
        dry_run: bool,
        debug: bool = False,
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.fixture = fixture
        self.seed_key = seed_key
        self.seed_slug = _slugify(seed_key)
        self.scale = scale
        self.scale_config = SCALE_CONFIGS[scale]
        self.dry_run = dry_run
        self.debug = debug
        seed_material = f"{seed_key}:{scale}".encode("utf-8")
        seed_number = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        self.rng = random.Random(seed_number)

        self.counters: dict[str, dict[str, int]] = {
            "product.category": {"created": 0, "skipped": 0},
            "product.template": {"created": 0, "skipped": 0},
            "stock.quant": {"created": 0, "updated": 0, "skipped": 0},
            "res.partner.customer": {"created": 0, "skipped": 0},
            "res.partner.supplier": {"created": 0, "skipped": 0},
            "sale.order": {"created": 0, "skipped": 0},
            "purchase.order": {"created": 0, "skipped": 0},
            "account.move.out_invoice": {"created": 0, "skipped": 0},
        }
        self.action_counts = {
            "sales_orders_confirmed": 0,
            "purchase_orders_confirmed": 0,
            "invoices_posted": 0,
        }
        self.warnings: list[str] = []
        self.model_exists_cache: dict[str, bool] = {}
        self.synthetic_id = -1

        # Runtime maps for generating transactions.
        self.category_by_name: dict[str, int] = {}
        self.product_by_sku: dict[str, dict[str, Any]] = {}
        self.customers: list[dict[str, Any]] = []
        self.suppliers: list[dict[str, Any]] = []
        self._internal_stock_location_cache: dict[str, int | None] | None = None

    def _debug(self, message: str) -> None:
        if self.debug:
            print(f"[seed][debug] {message}")

    def _progress(self, message: str) -> None:
        print(f"[seed] {message}")

    @contextmanager
    def _stage(self, name: str):
        started_at = perf_counter()
        self._debug(f"stage '{name}' started")
        try:
            yield
        finally:
            elapsed_ms = int((perf_counter() - started_at) * 1000)
            self._debug(f"stage '{name}' finished in {elapsed_ms}ms")

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _next_synthetic_id(self) -> int:
        value = self.synthetic_id
        self.synthetic_id -= 1
        return value

    def _model_exists(self, model: str) -> bool:
        cached = self.model_exists_cache.get(model)
        if cached is not None:
            self._debug(f"model_exists cache hit: {model}={cached}")
            return cached

        exists = False
        try:
            count = self.client.search_count(
                user_id=self.user_id,
                model="ir.model",
                domain=[("model", "=", model)],
            )
            exists = count > 0
        except OdooRequestError:
            # Fall back to direct probe.
            try:
                self.client.search_count(user_id=self.user_id, model=model, domain=[])
                exists = True
            except OdooRequestError:
                exists = False

        self.model_exists_cache[model] = exists
        self._debug(f"model_exists probe: {model}={exists}")
        return exists

    def _search_one(
        self,
        *,
        model: str,
        domain: list[Any],
        fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        rows = self.client.search_read(
            user_id=self.user_id,
            model=model,
            domain=domain,
            fields=fields or ["id"],
            limit=1,
        )
        if rows:
            return rows[0]
        return None

    def _create(self, *, model: str, values: dict[str, Any]) -> int:
        result = self.client.execute_kw(
            user_id=self.user_id,
            model=model,
            method="create",
            args=[values],
        )
        if not isinstance(result, int):
            raise OdooRequestError(f"Unexpected create response for model '{model}'.")
        return result

    def _write(self, *, model: str, record_id: int, values: dict[str, Any]) -> None:
        result = self.client.execute_kw(
            user_id=self.user_id,
            model=model,
            method="write",
            args=[[record_id], values],
        )
        if result is not True:
            raise OdooRequestError(f"Unexpected write response for model '{model}'.")

    def _coerce_quantity(self, raw_value: Any, *, sku: str) -> float:
        if raw_value in (None, ""):
            return 0.0
        try:
            quantity = float(raw_value)
        except (TypeError, ValueError):
            self._warn(f"Invalid quantite for SKU '{sku}'. Using 0.")
            return 0.0
        if quantity < 0:
            self._warn(f"Negative quantite for SKU '{sku}' was clamped to 0.")
            return 0.0
        return round(quantity, 2)

    def _many2one_id(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], int):
            return value[0]
        return None

    def _internal_stock_location(self) -> dict[str, int | None] | None:
        if self._internal_stock_location_cache is not None:
            return self._internal_stock_location_cache
        if not self._model_exists("stock.location"):
            self._warn("Missing model 'stock.location'. Inventory quantity seeding skipped.")
            self._internal_stock_location_cache = {}
            return None

        location_row = self._search_one(
            model="stock.location",
            domain=[("usage", "=", "internal")],
            fields=["id", "company_id", "name", "usage"],
        )
        if not location_row or not isinstance(location_row.get("id"), int):
            self._warn("No internal stock location found. Inventory quantity seeding skipped.")
            self._internal_stock_location_cache = {}
            return None

        self._internal_stock_location_cache = {
            "id": int(location_row["id"]),
            "company_id": self._many2one_id(location_row.get("company_id")),
        }
        self._debug(
            "resolved internal stock location: "
            f"id={self._internal_stock_location_cache['id']}, "
            f"company_id={self._internal_stock_location_cache['company_id']}"
        )
        return self._internal_stock_location_cache

    def _stock_quant_values(
        self,
        *,
        product_id: int,
        location_id: int,
        quantity: float,
        company_id: int | None,
        inventory_adjustment: bool,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "product_id": product_id,
            "location_id": location_id,
        }
        if company_id is not None:
            values["company_id"] = company_id
        if inventory_adjustment:
            values["inventory_quantity_auto_apply"] = quantity
        else:
            values["quantity"] = quantity
        return values

    def _upsert_stock_quant(
        self,
        *,
        product_id: int,
        sku: str,
        quantity: float,
        location_id: int,
        company_id: int | None,
    ) -> None:
        existing = self._search_one(
            model="stock.quant",
            domain=[("product_id", "=", product_id), ("location_id", "=", location_id)],
            fields=["id", "quantity"],
        )
        if existing and isinstance(existing.get("id"), int):
            quant_id = int(existing["id"])
            current_quantity = self._coerce_quantity(existing.get("quantity"), sku=sku)
            if abs(current_quantity - quantity) < 1e-9:
                self.counters["stock.quant"]["skipped"] += 1
                return
            for inventory_adjustment in (False, True):
                try:
                    self._write(
                        model="stock.quant",
                        record_id=quant_id,
                        values=self._stock_quant_values(
                            product_id=product_id,
                            location_id=location_id,
                            quantity=quantity,
                            company_id=company_id,
                            inventory_adjustment=inventory_adjustment,
                        ),
                    )
                    self.counters["stock.quant"]["updated"] += 1
                    return
                except OdooRequestError:
                    continue
            self._warn(f"Failed to update stock.quant for SKU '{sku}'.")
            self.counters["stock.quant"]["skipped"] += 1
            return

        if quantity <= 0:
            self.counters["stock.quant"]["skipped"] += 1
            return

        for inventory_adjustment in (False, True):
            try:
                self._create(
                    model="stock.quant",
                    values=self._stock_quant_values(
                        product_id=product_id,
                        location_id=location_id,
                        quantity=quantity,
                        company_id=company_id,
                        inventory_adjustment=inventory_adjustment,
                    ),
                )
                self.counters["stock.quant"]["created"] += 1
                return
            except OdooRequestError:
                continue
        self._warn(f"Failed to create stock.quant for SKU '{sku}'.")
        self.counters["stock.quant"]["skipped"] += 1

    def _ensure_categories(self) -> None:
        if not self._model_exists("product.category"):
            self._warn("Missing model 'product.category'. Categories/products seeding skipped.")
            return

        for raw_name in self.fixture.get("categories", []):
            name = str(raw_name).strip()
            if not name:
                continue

            found = self._search_one(
                model="product.category",
                domain=[("name", "=", name)],
                fields=["id", "name"],
            )
            if found and isinstance(found.get("id"), int):
                self.category_by_name[name] = int(found["id"])
                self.counters["product.category"]["skipped"] += 1
                continue

            if self.dry_run:
                category_id = self._next_synthetic_id()
            else:
                category_id = self._create(model="product.category", values={"name": name})

            self.category_by_name[name] = category_id
            self.counters["product.category"]["created"] += 1

    def _ensure_products(self) -> None:
        if not self._model_exists("product.template"):
            self._warn("Missing model 'product.template'. Product seeding skipped.")
            return
        if not self._model_exists("product.product"):
            self._warn("Missing model 'product.product'. Product variant lookup unavailable.")
            return

        products = self.fixture.get("products", [])
        for item in products:
            sku = str(item.get("sku", "")).strip()
            if not sku:
                continue
            quantity = self._coerce_quantity(item.get("quantite"), sku=sku)
            category_name = str(item.get("category", "")).strip()
            category_id = self.category_by_name.get(category_name)
            if category_id is None:
                self._warn(f"Category '{category_name}' missing for SKU '{sku}'. Product skipped.")
                continue

            name = str(item.get("name", sku)).strip()
            brand = str(item.get("brand", "")).strip()
            full_name = f"{brand} {name}".strip()
            product_type = str(item.get("type", "product")).strip() or "product"
            default_code = f"ELEC-{self.seed_key}-{sku}"
            values = {
                "name": full_name,
                "default_code": default_code,
                "categ_id": category_id,
                "list_price": float(item.get("list_price", 0.0) or 0.0),
                "standard_price": float(item.get("cost_price", 0.0) or 0.0),
                "type": product_type,
                "sale_ok": True,
                "purchase_ok": True,
            }
            found = self._search_one(
                model="product.template",
                domain=[("default_code", "=", default_code)],
                fields=["id", "default_code", "name", "type", "sale_ok", "purchase_ok"],
            )
            if found and isinstance(found.get("id"), int):
                template_id = int(found["id"])
                if not self.dry_run:
                    self._write(model="product.template", record_id=template_id, values=values)
                self.counters["product.template"]["skipped"] += 1
            else:
                if self.dry_run:
                    template_id = self._next_synthetic_id()
                else:
                    template_id = self._create(model="product.template", values=values)
                self.counters["product.template"]["created"] += 1

            if self.dry_run:
                product_id = self._next_synthetic_id()
            else:
                product_row = self._search_one(
                    model="product.product",
                    domain=[("product_tmpl_id", "=", template_id)],
                    fields=["id"],
                )
                if not product_row or not isinstance(product_row.get("id"), int):
                    self._warn(f"Unable to find product variant for template default_code={default_code}.")
                    continue
                product_id = int(product_row["id"])

            self.product_by_sku[sku] = {
                "product_id": product_id,
                "template_id": template_id,
                "default_code": default_code,
                "name": str(item.get("name", sku)),
                "list_price": float(item.get("list_price", 0.0) or 0.0),
                "cost_price": float(item.get("cost_price", 0.0) or 0.0),
                "quantite": quantity,
            }

    def _ensure_stock_levels(self) -> None:
        if not self.product_by_sku:
            return
        if not self._model_exists("stock.quant"):
            self._warn("Missing model 'stock.quant'. Inventory quantity seeding skipped (install Inventory module).")
            return

        location = self._internal_stock_location()
        if not location:
            return

        location_id = location.get("id")
        if not isinstance(location_id, int):
            self._warn("Resolved stock location is invalid. Inventory quantity seeding skipped.")
            return

        company_id = location.get("company_id")
        for sku, product in self.product_by_sku.items():
            quantity = self._coerce_quantity(product.get("quantite"), sku=sku)
            if self.dry_run:
                if quantity > 0:
                    self.counters["stock.quant"]["created"] += 1
                else:
                    self.counters["stock.quant"]["skipped"] += 1
                continue
            self._upsert_stock_quant(
                product_id=int(product["product_id"]),
                sku=sku,
                quantity=quantity,
                location_id=location_id,
                company_id=company_id if isinstance(company_id, int) else None,
            )

    def _partner_email(self, partner_type: str, index: int) -> str:
        return f"demoseed.{partner_type}.{self.seed_slug}.{index:03d}@myerppilot.demo"

    def _ensure_partners(self) -> None:
        if not self._model_exists("res.partner"):
            self._warn("Missing model 'res.partner'. Customer/supplier seeding skipped.")
            return

        customer_names = [str(v).strip() for v in self.fixture.get("customers", []) if str(v).strip()]
        supplier_names = [str(v).strip() for v in self.fixture.get("suppliers", []) if str(v).strip()]
        if not customer_names:
            self._warn("Fixture has no customers.")
        if not supplier_names:
            self._warn("Fixture has no suppliers.")

        for idx in range(1, self.scale_config.customer_count + 1):
            source_name = customer_names[(idx - 1) % len(customer_names)] if customer_names else f"Customer {idx}"
            cycle = (idx - 1) // max(len(customer_names), 1)
            name = source_name if cycle == 0 else f"{source_name} #{cycle + 1}"
            email = self._partner_email("customer", idx)
            found = self._search_one(
                model="res.partner",
                domain=[("email", "=", email)],
                fields=["id", "name", "email"],
            )
            if found and isinstance(found.get("id"), int):
                partner_id = int(found["id"])
                self.counters["res.partner.customer"]["skipped"] += 1
            else:
                values = {
                    "name": name,
                    "email": email,
                    "company_type": "company",
                    "is_company": True,
                    "customer_rank": 1,
                }
                if self.dry_run:
                    partner_id = self._next_synthetic_id()
                else:
                    partner_id = self._create(model="res.partner", values=values)
                self.counters["res.partner.customer"]["created"] += 1

            self.customers.append({"id": partner_id, "name": name, "email": email})

        for idx in range(1, self.scale_config.supplier_count + 1):
            source_name = supplier_names[(idx - 1) % len(supplier_names)] if supplier_names else f"Supplier {idx}"
            cycle = (idx - 1) // max(len(supplier_names), 1)
            name = source_name if cycle == 0 else f"{source_name} #{cycle + 1}"
            email = self._partner_email("supplier", idx)
            found = self._search_one(
                model="res.partner",
                domain=[("email", "=", email)],
                fields=["id", "name", "email"],
            )
            if found and isinstance(found.get("id"), int):
                partner_id = int(found["id"])
                self.counters["res.partner.supplier"]["skipped"] += 1
            else:
                values = {
                    "name": name,
                    "email": email,
                    "company_type": "company",
                    "is_company": True,
                    "supplier_rank": 1,
                }
                if self.dry_run:
                    partner_id = self._next_synthetic_id()
                else:
                    partner_id = self._create(model="res.partner", values=values)
                self.counters["res.partner.supplier"]["created"] += 1

            self.suppliers.append({"id": partner_id, "name": name, "email": email})

    def _build_sale_lines(self, pattern: dict[str, Any]) -> list[tuple[int, int, dict[str, Any]]]:
        qty_min = int(pattern.get("quantity_min", 1))
        qty_max = int(pattern.get("quantity_max", max(qty_min, 1)))
        if qty_max < qty_min:
            qty_max = qty_min

        lines: list[tuple[int, int, dict[str, Any]]] = []
        for sku in pattern.get("bundle", []):
            product = self.product_by_sku.get(str(sku))
            if not product:
                continue
            qty = self.rng.randint(max(qty_min, 1), max(qty_max, 1))
            line = (
                0,
                0,
                {
                    "product_id": int(product["product_id"]),
                    "product_uom_qty": qty,
                    "price_unit": float(product["list_price"]),
                    "name": str(product["name"]),
                },
            )
            lines.append(line)
        return lines

    def _prediction_history_config(self) -> dict[str, Any] | None:
        config = self.fixture.get("prediction_history")
        return config if isinstance(config, dict) else None

    def _historical_sales_order_count(self, config: dict[str, Any]) -> int:
        explicit_key = f"{self.scale}_sales_order_count"
        explicit_value = config.get(explicit_key)
        if isinstance(explicit_value, int) and explicit_value > 0:
            return explicit_value
        return self.scale_config.sales_order_count

    def _historical_months(self, config: dict[str, Any]) -> list[tuple[int, int]]:
        history_months = int(config.get("history_months", 24) or 24)
        history_months = max(1, min(history_months, 60))
        now = datetime.now(timezone.utc)
        start_year, start_month = _add_months(now.year, now.month, -(history_months - 1))
        return [_add_months(start_year, start_month, index) for index in range(history_months)]

    def _month_order_counts(self, *, total_orders: int, months: list[tuple[int, int]], config: dict[str, Any]) -> list[int]:
        seasonality = config.get("seasonality") if isinstance(config.get("seasonality"), dict) else {}
        weights = [max(float(seasonality.get(str(month), 1.0) or 1.0), 0.1) for _, month in months]
        weight_total = sum(weights)
        raw_counts = [total_orders * weight / weight_total for weight in weights]
        counts = [int(value) for value in raw_counts]
        remainder = total_orders - sum(counts)
        ranked_months = sorted(
            range(len(raw_counts)),
            key=lambda index: (raw_counts[index] - counts[index], weights[index]),
            reverse=True,
        )
        for index in ranked_months[:remainder]:
            counts[index] += 1
        return counts

    def _historical_order_datetime(self, *, year: int, month: int, order_index: int, month_order_count: int) -> str:
        day_count = _days_in_month(year, month)
        spacing = day_count / max(month_order_count, 1)
        day = min(day_count, max(1, int(order_index * spacing) + 1))
        hour = 9 + (order_index % 9)
        minute = (order_index * 7) % 60
        return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"

    def _profile_quantity(self, profile: dict[str, Any], *, month_offset: int, calendar_month: int) -> int:
        base_quantity = max(float(profile.get("base_quantity", 1) or 1), 0.1)
        monthly_growth = float(profile.get("monthly_growth", 0.0) or 0.0)
        noise = max(float(profile.get("noise", 0.0) or 0.0), 0.0)
        peak_months = {int(value) for value in profile.get("peak_months", []) if isinstance(value, int)}
        spike_months = {int(value) for value in profile.get("spike_months", []) if isinstance(value, int)}

        trend_factor = max(0.2, (1.0 + monthly_growth) ** month_offset)
        peak_factor = 1.35 if calendar_month in peak_months else 1.0
        spike_factor = 1.85 if calendar_month in spike_months else 1.0
        noise_factor = self.rng.uniform(max(0.5, 1.0 - noise), 1.0 + noise)
        quantity = base_quantity * trend_factor * peak_factor * spike_factor * noise_factor
        return max(1, int(round(quantity)))

    def _build_historical_sale_lines(
        self,
        *,
        profiles: list[dict[str, Any]],
        config: dict[str, Any],
        month_offset: int,
        calendar_month: int,
    ) -> list[tuple[int, int, dict[str, Any]]]:
        lines_per_order_min = max(int(config.get("lines_per_order_min", 2) or 2), 1)
        lines_per_order_max = max(int(config.get("lines_per_order_max", 5) or 5), lines_per_order_min)
        selected_count = self.rng.randint(lines_per_order_min, lines_per_order_max)

        lines: list[tuple[int, int, dict[str, Any]]] = []
        used_skus: set[str] = set()
        attempts = 0
        while len(lines) < selected_count and attempts < selected_count * 6:
            attempts += 1
            profile = _weighted_choice(profiles, self.rng)
            sku = str(profile.get("sku", "")).strip()
            if not sku or sku in used_skus:
                continue
            product = self.product_by_sku.get(sku)
            if not product:
                continue
            used_skus.add(sku)
            quantity = self._profile_quantity(
                profile,
                month_offset=month_offset,
                calendar_month=calendar_month,
            )
            lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": int(product["product_id"]),
                        "product_uom_qty": quantity,
                        "price_unit": float(product["list_price"]),
                        "name": str(product["name"]),
                    },
                )
            )
        return lines

    def _build_purchase_lines(self, pattern: dict[str, Any]) -> list[tuple[int, int, dict[str, Any]]]:
        qty_min = int(pattern.get("quantity_min", 1))
        qty_max = int(pattern.get("quantity_max", max(qty_min, 1)))
        if qty_max < qty_min:
            qty_max = qty_min
        planned_date = _recent_datetime(self.rng)

        lines: list[tuple[int, int, dict[str, Any]]] = []
        for sku in pattern.get("bundle", []):
            product = self.product_by_sku.get(str(sku))
            if not product:
                continue
            qty = self.rng.randint(max(qty_min, 1), max(qty_max, 1))
            line = (
                0,
                0,
                {
                    "product_id": int(product["product_id"]),
                    "product_qty": qty,
                    "price_unit": float(product["cost_price"]),
                    "name": str(product["name"]),
                    "date_planned": planned_date,
                },
            )
            lines.append(line)
        return lines

    def _build_invoice_lines(self, pattern: dict[str, Any]) -> list[tuple[int, int, dict[str, Any]]]:
        qty_min = int(pattern.get("quantity_min", 1))
        qty_max = int(pattern.get("quantity_max", max(qty_min, 1)))
        if qty_max < qty_min:
            qty_max = qty_min

        lines: list[tuple[int, int, dict[str, Any]]] = []
        for sku in pattern.get("bundle", []):
            product = self.product_by_sku.get(str(sku))
            if not product:
                continue
            qty = self.rng.randint(max(qty_min, 1), max(qty_max, 1))
            line = (
                0,
                0,
                {
                    "product_id": int(product["product_id"]),
                    "quantity": qty,
                    "price_unit": float(product["list_price"]),
                    "name": str(product["name"]),
                },
            )
            lines.append(line)
        return lines

    def _ensure_sales_orders(self) -> None:
        if not self._model_exists("sale.order"):
            self._warn("Missing model 'sale.order'. Sales order seeding skipped (install Sales module).")
            return
        if not self.customers:
            self._warn("No customer partners available; sales order seeding skipped.")
            return
        if not self.product_by_sku:
            self._warn("No products available; sales order seeding skipped.")
            return

        patterns = [p for p in self.fixture.get("sales_patterns", []) if isinstance(p, dict)]
        if not patterns:
            self._warn("Fixture has no sales_patterns; sales order seeding skipped.")
            return

        prediction_history = self._prediction_history_config()
        if prediction_history:
            self._debug("prediction_history fixture detected; using historical sales generation")
            self._ensure_historical_sales_orders(prediction_history)
            return

        confirm_target = int(self.scale_config.sales_order_count * self.scale_config.sales_confirm_ratio)
        for idx in range(1, self.scale_config.sales_order_count + 1):
            ref = f"SO-DEMO-{self.seed_key}-{idx:04d}"
            existing = self._search_one(
                model="sale.order",
                domain=[("client_order_ref", "=", ref)],
                fields=["id", "state"],
            )
            if existing and isinstance(existing.get("id"), int):
                self.counters["sale.order"]["skipped"] += 1
                continue

            pattern = _weighted_choice(patterns, self.rng)
            order_lines = self._build_sale_lines(pattern)
            if not order_lines:
                self._warn(f"Sales pattern '{pattern.get('name', 'unknown')}' has no resolvable products.")
                self.counters["sale.order"]["skipped"] += 1
                continue
            customer = self.rng.choice(self.customers)
            values = {
                "partner_id": int(customer["id"]),
                "client_order_ref": ref,
                "date_order": _recent_datetime(self.rng),
                "origin": f"FAKE-ELEC-{self.seed_key}",
                "note": f"Seed pattern: {pattern.get('name', 'N/A')}",
                "order_line": order_lines,
            }

            if self.dry_run:
                order_id = self._next_synthetic_id()
            else:
                order_id = self._create(model="sale.order", values=values)
            self.counters["sale.order"]["created"] += 1

            should_confirm = idx <= confirm_target
            if should_confirm:
                if self.dry_run:
                    self.action_counts["sales_orders_confirmed"] += 1
                else:
                    try:
                        self.client.execute_kw(
                            user_id=self.user_id,
                            model="sale.order",
                            method="action_confirm",
                            args=[[order_id]],
                        )
                        self.action_counts["sales_orders_confirmed"] += 1
                    except OdooRequestError as exc:
                        self._warn(f"Failed to confirm sale order '{ref}': {exc}")

    def _ensure_historical_sales_orders(self, config: dict[str, Any]) -> None:
        profiles = [
            profile
            for profile in config.get("profiles", [])
            if isinstance(profile, dict) and str(profile.get("sku", "")).strip() in self.product_by_sku
        ]
        if not profiles:
            self._warn("Fixture prediction_history has no resolvable profiles; historical sales seeding skipped.")
            return

        total_orders = self._historical_sales_order_count(config)
        months = self._historical_months(config)
        month_counts = self._month_order_counts(total_orders=total_orders, months=months, config=config)
        self._debug(
            "historical sales plan: "
            f"orders={total_orders}, months={len(months)}, "
            f"first={months[0][0]:04d}-{months[0][1]:02d}, "
            f"last={months[-1][0]:04d}-{months[-1][1]:02d}, "
            f"profiles={len(profiles)}, min_month_orders={min(month_counts)}, max_month_orders={max(month_counts)}"
        )

        global_index = 1
        for month_offset, ((year, month), month_order_count) in enumerate(zip(months, month_counts, strict=False)):
            self._debug(f"historical month {year:04d}-{month:02d}: planned_orders={month_order_count}")
            for month_order_index in range(month_order_count):
                ref = f"SO-DEMO-{self.seed_key}-H{year:04d}{month:02d}-{month_order_index + 1:04d}"
                existing = self._search_one(
                    model="sale.order",
                    domain=[("client_order_ref", "=", ref)],
                    fields=["id", "state"],
                )
                if existing and isinstance(existing.get("id"), int):
                    self.counters["sale.order"]["skipped"] += 1
                    continue

                order_lines = self._build_historical_sale_lines(
                    profiles=profiles,
                    config=config,
                    month_offset=month_offset,
                    calendar_month=month,
                )
                if not order_lines:
                    self._warn(f"Historical sale order '{ref}' has no resolvable products.")
                    self.counters["sale.order"]["skipped"] += 1
                    continue

                customer = self.rng.choice(self.customers)
                order_date = self._historical_order_datetime(
                    year=year,
                    month=month,
                    order_index=month_order_index,
                    month_order_count=month_order_count,
                )
                values = {
                    "partner_id": int(customer["id"]),
                    "client_order_ref": ref,
                    "date_order": order_date,
                    "origin": f"FAKE-ELEC-HISTORY-{self.seed_key}",
                    "note": f"Prediction history seed month={year:04d}-{month:02d}; sequence={global_index}",
                    "order_line": order_lines,
                }

                if self.dry_run:
                    order_id = self._next_synthetic_id()
                else:
                    order_id = self._create(model="sale.order", values=values)
                self.counters["sale.order"]["created"] += 1

                if self.dry_run:
                    self.action_counts["sales_orders_confirmed"] += 1
                else:
                    try:
                        self.client.execute_kw(
                            user_id=self.user_id,
                            model="sale.order",
                            method="action_confirm",
                            args=[[order_id]],
                        )
                        self.client.execute_kw(
                            user_id=self.user_id,
                            model="sale.order",
                            method="write",
                            args=[[order_id], {"date_order": order_date}],
                        )
                        self.action_counts["sales_orders_confirmed"] += 1
                    except OdooRequestError as exc:
                        self._warn(f"Failed to confirm historical sale order '{ref}': {exc}")
                if global_index == 1 or global_index % 100 == 0 or global_index == total_orders:
                    self._progress(
                        f"historical sales progress: {global_index}/{total_orders} orders processed"
                    )
                global_index += 1

    def _ensure_purchase_orders(self) -> None:
        if not self._model_exists("purchase.order"):
            self._warn("Missing model 'purchase.order'. Purchase order seeding skipped (install Purchase module).")
            return
        if not self.suppliers:
            self._warn("No supplier partners available; purchase order seeding skipped.")
            return
        if not self.product_by_sku:
            self._warn("No products available; purchase order seeding skipped.")
            return

        patterns = [p for p in self.fixture.get("purchase_patterns", []) if isinstance(p, dict)]
        if not patterns:
            self._warn("Fixture has no purchase_patterns; purchase order seeding skipped.")
            return

        confirm_target = int(self.scale_config.purchase_order_count * self.scale_config.purchase_confirm_ratio)
        for idx in range(1, self.scale_config.purchase_order_count + 1):
            ref = f"PO-DEMO-{self.seed_key}-{idx:04d}"
            existing = self._search_one(
                model="purchase.order",
                domain=[("partner_ref", "=", ref)],
                fields=["id", "state"],
            )
            if existing and isinstance(existing.get("id"), int):
                self.counters["purchase.order"]["skipped"] += 1
                continue

            pattern = _weighted_choice(patterns, self.rng)
            order_lines = self._build_purchase_lines(pattern)
            if not order_lines:
                self._warn(f"Purchase pattern '{pattern.get('name', 'unknown')}' has no resolvable products.")
                self.counters["purchase.order"]["skipped"] += 1
                continue
            supplier = self.rng.choice(self.suppliers)
            values = {
                "partner_id": int(supplier["id"]),
                "partner_ref": ref,
                "date_order": _recent_datetime(self.rng),
                "origin": f"FAKE-ELEC-{self.seed_key}",
                "notes": f"Seed pattern: {pattern.get('name', 'N/A')}",
                "order_line": order_lines,
            }

            if self.dry_run:
                order_id = self._next_synthetic_id()
            else:
                order_id = self._create(model="purchase.order", values=values)
            self.counters["purchase.order"]["created"] += 1

            should_confirm = idx <= confirm_target
            if should_confirm:
                if self.dry_run:
                    self.action_counts["purchase_orders_confirmed"] += 1
                else:
                    try:
                        self.client.execute_kw(
                            user_id=self.user_id,
                            model="purchase.order",
                            method="button_confirm",
                            args=[[order_id]],
                        )
                        self.action_counts["purchase_orders_confirmed"] += 1
                    except OdooRequestError as exc:
                        self._warn(f"Failed to confirm purchase order '{ref}': {exc}")

    def _ensure_invoices(self) -> None:
        if not self._model_exists("account.move"):
            self._warn("Missing model 'account.move'. Invoice seeding skipped (install Invoicing module).")
            return
        if not self.customers:
            self._warn("No customer partners available; invoice seeding skipped.")
            return
        if not self.product_by_sku:
            self._warn("No products available; invoice seeding skipped.")
            return

        patterns = [p for p in self.fixture.get("sales_patterns", []) if isinstance(p, dict)]
        if not patterns:
            self._warn("Fixture has no sales_patterns; invoice seeding skipped.")
            return

        for idx in range(1, self.scale_config.invoice_count + 1):
            ref = f"INV-DEMO-{self.seed_key}-{idx:04d}"
            existing = self._search_one(
                model="account.move",
                domain=[("move_type", "=", "out_invoice"), ("ref", "=", ref)],
                fields=["id", "state"],
            )
            if existing and isinstance(existing.get("id"), int):
                self.counters["account.move.out_invoice"]["skipped"] += 1
                continue

            pattern = _weighted_choice(patterns, self.rng)
            invoice_lines = self._build_invoice_lines(pattern)
            if not invoice_lines:
                self._warn(f"Invoice pattern '{pattern.get('name', 'unknown')}' has no resolvable products.")
                self.counters["account.move.out_invoice"]["skipped"] += 1
                continue
            customer = self.rng.choice(self.customers)
            values = {
                "move_type": "out_invoice",
                "partner_id": int(customer["id"]),
                "invoice_date": _recent_date(self.rng),
                "ref": ref,
                "invoice_origin": f"FAKE-ELEC-{self.seed_key}",
                "invoice_line_ids": invoice_lines,
            }

            if self.dry_run:
                move_id = self._next_synthetic_id()
            else:
                move_id = self._create(model="account.move", values=values)
            self.counters["account.move.out_invoice"]["created"] += 1

            if self.dry_run:
                self.action_counts["invoices_posted"] += 1
            else:
                try:
                    self.client.execute_kw(
                        user_id=self.user_id,
                        model="account.move",
                        method="action_post",
                        args=[[move_id]],
                    )
                    self.action_counts["invoices_posted"] += 1
                except OdooRequestError as exc:
                    self._warn(f"Failed to post invoice '{ref}': {exc}")

    def run(self) -> dict[str, Any]:
        self._debug(
            "fixture summary: "
            f"categories={len(self.fixture.get('categories', []))}, "
            f"products={len(self.fixture.get('products', []))}, "
            f"customers={len(self.fixture.get('customers', []))}, "
            f"suppliers={len(self.fixture.get('suppliers', []))}, "
            f"prediction_history={bool(self._prediction_history_config())}"
        )
        with self._stage("categories"):
            self._ensure_categories()
        with self._stage("products"):
            self._ensure_products()
        with self._stage("stock_levels"):
            self._ensure_stock_levels()
        with self._stage("partners"):
            self._ensure_partners()
        with self._stage("sales_orders"):
            self._ensure_sales_orders()
        with self._stage("purchase_orders"):
            self._ensure_purchase_orders()
        with self._stage("invoices"):
            self._ensure_invoices()

        mode_label = "dry-run" if self.dry_run else "apply"
        self._debug(
            "runtime maps: "
            f"categories={len(self.category_by_name)}, products={len(self.product_by_sku)}, "
            f"customers={len(self.customers)}, suppliers={len(self.suppliers)}"
        )
        return {
            "mode": mode_label,
            "seed_key": self.seed_key,
            "scale": self.scale,
            "model_counts": self.counters,
            "actions": self.action_counts,
            "warnings": self.warnings,
        }


def _load_fixture(path_value: str) -> dict[str, Any]:
    candidate = Path(path_value)
    if not candidate.is_absolute():
        scripts_dir = Path(__file__).resolve().parent
        candidate = scripts_dir / "data" / path_value

    if not candidate.exists():
        raise FileNotFoundError(f"Fixture file not found: {candidate}")

    with candidate.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Fixture root must be a JSON object.")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed deterministic fake electronics data into Odoo.")
    parser.add_argument("--seed-key", required=True, help="Stable key used for idempotency and deterministic data.")
    parser.add_argument(
        "--fixture",
        default="electronics_fake_data.json",
        help="Fixture JSON file name under app/scripts/data or absolute path.",
    )
    parser.add_argument(
        "--scale",
        choices=sorted(SCALE_CONFIGS.keys()),
        default="medium",
        help="Data volume scale.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan operations without writing to Odoo.")
    parser.add_argument("--debug", action="store_true", help="Print safe pipeline diagnostics while seeding.")

    parser.add_argument("--url", default=os.getenv("ODOO_URL", DEFAULT_ODOO_URL), help="Odoo base URL.")
    parser.add_argument(
        "--database",
        default=os.getenv("ODOO_DB_NAME", DEFAULT_ODOO_DB),
        help="Odoo database name.",
    )
    parser.add_argument(
        "--username",
        default=_env_first("ODOO_USERNAME", "ODOO_USER", default=DEFAULT_ODOO_USER),
        help="Odoo application username. Uses ODOO_USERNAME/ODOO_USER or defaults to admin.",
    )
    parser.add_argument(
        "--password",
        default=_env_first("ODOO_PASSWORD", "ODOO_API_KEY", default=DEFAULT_ODOO_PASSWORD),
        help="Odoo application password or API key. Uses ODOO_PASSWORD/ODOO_API_KEY or defaults to admin.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="Odoo JSON-RPC timeout for real seeding. Dry-run does not connect.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        fixture = _load_fixture(args.fixture)
    except Exception as exc:  # noqa: BLE001 - CLI should surface user-friendly failures.
        print(f"[seed] Fixture error: {exc}")
        return 2

    cli_username = any(arg == "--username" or arg.startswith("--username=") for arg in sys.argv[1:])
    cli_password = any(arg == "--password" or arg.startswith("--password=") for arg in sys.argv[1:])
    if args.debug:
        prediction_history = fixture.get("prediction_history") if isinstance(fixture.get("prediction_history"), dict) else {}
        print("[seed][debug] CLI configuration:")
        print(f"[seed][debug]   url={str(args.url).strip().rstrip('/')}")
        print(f"[seed][debug]   database={str(args.database).strip()}")
        print(f"[seed][debug]   username={str(args.username).strip()} ({'cli' if cli_username else 'env/default'})")
        print(f"[seed][debug]   password={_mask_secret(str(args.password))} ({'cli' if cli_password else 'env/default'})")
        print(f"[seed][debug]   fixture={args.fixture}")
        print(f"[seed][debug]   scale={args.scale}")
        print(f"[seed][debug]   dry_run={bool(args.dry_run)}")
        print(
            "[seed][debug]   prediction_history="
            f"months={prediction_history.get('history_months', 'n/a')}, "
            f"large_orders={prediction_history.get('large_sales_order_count', 'n/a')}, "
            f"profiles={len(prediction_history.get('profiles', [])) if isinstance(prediction_history.get('profiles'), list) else 0}"
        )

    if args.dry_run:
        client = DryRunOdooClient()
        user_id = 0
        print("[seed] Dry-run mode: skipping Odoo connection/authentication.")
    else:
        client = OdooJsonRpcClient(
            base_url=str(args.url).strip().rstrip("/"),
            database_name=str(args.database).strip(),
            username=str(args.username).strip(),
            api_key=str(args.password),
            timeout_seconds=float(args.timeout_seconds),
        )

        try:
            if args.debug:
                print("[seed][debug] Authenticating against Odoo JSON-RPC...")
            user_id = client.authenticate()
        except (OdooConnectionError, OdooAuthenticationError, OdooRequestError) as exc:
            print(f"[seed] Odoo connection/auth error: {exc}")
            print("[seed] Hint: use Odoo application credentials, not PostgreSQL credentials.")
            print("[seed] Example: python -m app.scripts.seed_fake_odoo_data --seed-key electronics-prediction-v1 --scale large --username admin --password <your-odoo-password> --debug")
            return 3
        if args.debug:
            print(f"[seed][debug] Authentication succeeded as user_id={user_id}")

    seeder = Seeder(
        client=client,
        user_id=user_id,
        fixture=fixture,
        seed_key=str(args.seed_key).strip(),
        scale=str(args.scale).strip(),
        dry_run=bool(args.dry_run),
        debug=bool(args.debug),
    )

    try:
        result = seeder.run()
    except OdooRequestError as exc:
        print(f"[seed] Odoo request failed: {exc}")
        return 4
    except Exception as exc:  # noqa: BLE001 - CLI guardrail.
        print(f"[seed] Unexpected error: {exc}")
        return 5

    mode = result["mode"]
    print(f"[seed] Completed ({mode}) | seed_key={result['seed_key']} | scale={result['scale']}")
    print("[seed] Model summary:")
    for model_name, counters in result["model_counts"].items():
        created = counters.get("created", 0)
        skipped = counters.get("skipped", 0)
        if args.dry_run:
            print(f"  - {model_name}: would_create={created}, existing_or_skip={skipped}")
        else:
            print(f"  - {model_name}: created={created}, skipped={skipped}")

    print("[seed] Action summary:")
    for action_name, value in result["actions"].items():
        if args.dry_run:
            print(f"  - {action_name}: would_run={value}")
        else:
            print(f"  - {action_name}: {value}")

    warnings = result.get("warnings", [])
    if warnings:
        print("[seed] Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("[seed] Warnings: none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
