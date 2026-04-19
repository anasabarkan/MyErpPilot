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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
DEFAULT_ODOO_USER = "odoo"
DEFAULT_ODOO_PASSWORD = "odoo"


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
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.fixture = fixture
        self.seed_key = seed_key
        self.seed_slug = _slugify(seed_key)
        self.scale = scale
        self.scale_config = SCALE_CONFIGS[scale]
        self.dry_run = dry_run
        seed_material = f"{seed_key}:{scale}".encode("utf-8")
        seed_number = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        self.rng = random.Random(seed_number)

        self.counters: dict[str, dict[str, int]] = {
            "product.category": {"created": 0, "skipped": 0},
            "product.template": {"created": 0, "skipped": 0},
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
            category_name = str(item.get("category", "")).strip()
            category_id = self.category_by_name.get(category_name)
            if category_id is None:
                self._warn(f"Category '{category_name}' missing for SKU '{sku}'. Product skipped.")
                continue

            default_code = f"ELEC-{self.seed_key}-{sku}"
            found = self._search_one(
                model="product.template",
                domain=[("default_code", "=", default_code)],
                fields=["id", "default_code", "name"],
            )
            if found and isinstance(found.get("id"), int):
                template_id = int(found["id"])
                self.counters["product.template"]["skipped"] += 1
            else:
                if self.dry_run:
                    template_id = self._next_synthetic_id()
                else:
                    name = str(item.get("name", sku)).strip()
                    brand = str(item.get("brand", "")).strip()
                    full_name = f"{brand} {name}".strip()
                    values = {
                        "name": full_name,
                        "default_code": default_code,
                        "categ_id": category_id,
                        "list_price": float(item.get("list_price", 0.0) or 0.0),
                        "standard_price": float(item.get("cost_price", 0.0) or 0.0),
                        "type": str(item.get("type", "consu")),
                        "sale_ok": True,
                        "purchase_ok": True,
                    }
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
            }

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
        self._ensure_categories()
        self._ensure_products()
        self._ensure_partners()
        self._ensure_sales_orders()
        self._ensure_purchase_orders()
        self._ensure_invoices()

        mode_label = "dry-run" if self.dry_run else "apply"
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

    parser.add_argument("--url", default=os.getenv("ODOO_URL", DEFAULT_ODOO_URL), help="Odoo base URL.")
    parser.add_argument(
        "--database",
        default=os.getenv("ODOO_DB_NAME", DEFAULT_ODOO_DB),
        help="Odoo database name.",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("ODOO_DB_USER", DEFAULT_ODOO_USER),
        help="Odoo username.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("ODOO_DB_PASSWORD", DEFAULT_ODOO_PASSWORD),
        help="Odoo password or API key.",
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

    client = OdooJsonRpcClient(
        base_url=str(args.url).strip().rstrip("/"),
        database_name=str(args.database).strip(),
        username=str(args.username).strip(),
        api_key=str(args.password),
    )

    try:
        user_id = client.authenticate()
    except (OdooConnectionError, OdooAuthenticationError, OdooRequestError) as exc:
        print(f"[seed] Odoo connection/auth error: {exc}")
        return 3

    seeder = Seeder(
        client=client,
        user_id=user_id,
        fixture=fixture,
        seed_key=str(args.seed_key).strip(),
        scale=str(args.scale).strip(),
        dry_run=bool(args.dry_run),
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
