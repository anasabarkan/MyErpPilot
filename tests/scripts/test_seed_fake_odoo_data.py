from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.scripts.seed_fake_odoo_data import Seeder, _build_parser, _load_fixture


class _FakeOdooClient:
    def __init__(self) -> None:
        self.next_id = 1
        self.records: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.records["stock.location"] = [{"id": 500, "name": "WH/Stock", "usage": "internal", "company_id": 1}]
        self.calls: list[tuple[str, str, list[Any]]] = []

    def search_count(self, *, user_id: int, model: str, domain: list[Any]) -> int:
        return 1

    def search_read(
        self,
        *,
        user_id: int,
        model: str,
        domain: list[Any],
        fields: list[str] | None = None,
        limit: int | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        if model == "product.product":
            template_id = _domain_value(domain, "product_tmpl_id")
            if isinstance(template_id, int):
                return [{"id": template_id + 100_000}]
            return []

        records = self.records.get(model, [])
        matched = [record for record in records if _record_matches_domain(record, domain)]
        if limit is not None:
            return matched[:limit]
        return matched

    def execute_kw(
        self,
        *,
        user_id: int,
        model: str,
        method: str,
        args: list[Any],
    ) -> int | bool:
        self.calls.append((model, method, args))
        if method == "create":
            values = dict(args[0])
            values["id"] = self.next_id
            self.next_id += 1
            self.records[model].append(values)
            return int(values["id"])
        if method == "write":
            record_ids, values = args
            for record in self.records[model]:
                if record.get("id") in record_ids:
                    record.update(values)
            return True
        return True


def _domain_value(domain: list[Any], field_name: str) -> Any:
    for clause in domain:
        if isinstance(clause, (list, tuple)) and len(clause) >= 3 and clause[0] == field_name and clause[1] == "=":
            return clause[2]
    return None


def _record_matches_domain(record: dict[str, Any], domain: list[Any]) -> bool:
    for clause in domain:
        if not isinstance(clause, (list, tuple)) or len(clause) < 3:
            continue
        field_name, operator, expected = clause[0], clause[1], clause[2]
        if operator != "=":
            continue
        if record.get(str(field_name)) != expected:
            return False
    return True


def _fixture() -> dict[str, Any]:
    return _load_fixture(str(Path("electronics_fake_data.json")))


def _positive_seeded_quantity_count(fixture: dict[str, Any]) -> int:
    products = fixture.get("products", []) if isinstance(fixture.get("products"), list) else []
    return sum(1 for item in products if float(item.get("quantite", 0) or 0) > 0)


def _template_by_default_code(client: _FakeOdooClient, default_code: str) -> dict[str, Any]:
    return next(record for record in client.records["product.template"] if record.get("default_code") == default_code)


def test_large_dry_run_plans_historical_prediction_volume():
    seeder = Seeder(
        client=_FakeOdooClient(),
        user_id=1,
        fixture=_fixture(),
        seed_key="electronics-prediction-v1",
        scale="large",
        dry_run=True,
    )

    result = seeder.run()

    assert result["model_counts"]["sale.order"]["created"] == 1440
    assert result["actions"]["sales_orders_confirmed"] == 1440
    assert result["warnings"] == []


def test_debug_logs_pipeline_stages(capsys):
    seeder = Seeder(
        client=_FakeOdooClient(),
        user_id=1,
        fixture=_fixture(),
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=True,
        debug=True,
    )

    seeder.run()

    output = capsys.readouterr().out
    assert "[seed][debug] fixture summary:" in output
    assert "stage 'sales_orders' started" in output
    assert "historical sales plan:" in output
    assert "runtime maps:" in output


def test_cli_defaults_use_odoo_application_credentials(monkeypatch):
    monkeypatch.setenv("ODOO_DB_USER", "odoo")
    monkeypatch.setenv("ODOO_DB_PASSWORD", "odoo")
    monkeypatch.delenv("ODOO_USERNAME", raising=False)
    monkeypatch.delenv("ODOO_PASSWORD", raising=False)

    args = _build_parser().parse_args(["--seed-key", "electronics-prediction-v1"])

    assert args.username == "admin"
    assert args.password == "admin"


def test_cli_accepts_odoo_application_env_credentials(monkeypatch):
    monkeypatch.setenv("ODOO_USERNAME", "demo-user")
    monkeypatch.setenv("ODOO_PASSWORD", "demo-secret")

    args = _build_parser().parse_args(["--seed-key", "electronics-prediction-v1"])

    assert args.username == "demo-user"
    assert args.password == "demo-secret"


def test_new_seeded_products_are_created_as_storable_products():
    client = _FakeOdooClient()
    fixture = _fixture()
    seeder = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    )

    seeder.run()

    template = _template_by_default_code(client, "ELEC-electronics-prediction-v1-IP15-128")
    assert template["type"] == "product"
    assert template["sale_ok"] is True
    assert template["purchase_ok"] is True


def test_seed_creates_stock_quants_from_product_quantite():
    client = _FakeOdooClient()
    fixture = _fixture()
    seeder = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    )

    result = seeder.run()

    assert result["model_counts"]["stock.quant"]["created"] == _positive_seeded_quantity_count(fixture)
    assert seeder.product_by_sku["LG-C4-65"]["quantite"] == 0.0

    charger_product = seeder.product_by_sku["USBC-65W"]
    charger_quant = next(record for record in client.records["stock.quant"] if record["product_id"] == charger_product["product_id"])
    assert charger_quant["quantity"] == 40


def test_existing_seeded_product_is_updated_from_consumable_to_storable():
    client = _FakeOdooClient()
    legacy_fixture = deepcopy(_fixture())
    legacy_fixture["products"][0]["type"] = "consu"
    repair_fixture = deepcopy(_fixture())
    seed_key = "electronics-prediction-v1"

    Seeder(
        client=client,
        user_id=1,
        fixture=legacy_fixture,
        seed_key=seed_key,
        scale="small",
        dry_run=False,
    ).run()
    repair_result = Seeder(
        client=client,
        user_id=1,
        fixture=repair_fixture,
        seed_key=seed_key,
        scale="small",
        dry_run=False,
    ).run()

    template = _template_by_default_code(client, "ELEC-electronics-prediction-v1-IP15-128")
    assert template["type"] == "product"
    assert repair_result["model_counts"]["product.template"]["created"] == 0
    assert repair_result["model_counts"]["product.template"]["skipped"] == len(repair_fixture["products"])
    assert len([record for record in client.records["product.template"] if record.get("default_code") == "ELEC-electronics-prediction-v1-IP15-128"]) == 1


def test_existing_storable_seed_product_is_left_stable_without_duplicates():
    client = _FakeOdooClient()
    fixture = _fixture()
    first_result = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    ).run()
    second_result = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    ).run()

    assert first_result["model_counts"]["product.template"]["created"] == len(fixture["products"])
    assert second_result["model_counts"]["product.template"]["created"] == 0
    assert second_result["model_counts"]["product.template"]["skipped"] == len(fixture["products"])
    assert len([record for record in client.records["product.template"] if record.get("default_code") == "ELEC-electronics-prediction-v1-IP15-128"]) == 1
    assert _template_by_default_code(client, "ELEC-electronics-prediction-v1-IP15-128")["type"] == "product"


def test_missing_product_type_defaults_to_storable_product():
    client = _FakeOdooClient()
    fixture = deepcopy(_fixture())
    fixture["products"][0].pop("type", None)
    seeder = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1-missing-type",
        scale="small",
        dry_run=False,
    )

    seeder.run()

    template = _template_by_default_code(client, "ELEC-electronics-prediction-v1-missing-type-IP15-128")
    assert template["type"] == "product"


def test_historical_seed_creates_confirmed_orders_across_24_months():
    client = _FakeOdooClient()
    seeder = Seeder(
        client=client,
        user_id=1,
        fixture=_fixture(),
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    )

    result = seeder.run()
    sale_orders = client.records["sale.order"]
    sale_months = {str(order["date_order"])[:7] for order in sale_orders}
    confirmed_calls = [
        call for call in client.calls if call[0] == "sale.order" and call[1] == "action_confirm"
    ]
    write_calls = [call for call in client.calls if call[0] == "sale.order" and call[1] == "write"]

    assert result["model_counts"]["sale.order"]["created"] == 120
    assert result["actions"]["sales_orders_confirmed"] == 120
    assert len(sale_months) == 24
    assert all(str(order["client_order_ref"]).startswith("SO-DEMO-electronics-prediction-v1-H") for order in sale_orders)
    assert len(confirmed_calls) == 120
    assert len(write_calls) == 120


def test_historical_seed_is_idempotent_by_client_order_ref():
    client = _FakeOdooClient()
    fixture = _fixture()
    first = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    )
    second = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    )

    first.run()
    result = second.run()

    assert result["model_counts"]["sale.order"]["created"] == 0
    assert result["model_counts"]["sale.order"]["skipped"] == 120


def test_stock_quant_seed_is_idempotent_by_product_and_location():
    client = _FakeOdooClient()
    fixture = _fixture()
    first = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    )
    second = Seeder(
        client=client,
        user_id=1,
        fixture=fixture,
        seed_key="electronics-prediction-v1",
        scale="small",
        dry_run=False,
    )

    first.run()
    result = second.run()

    assert len(client.records["stock.quant"]) == _positive_seeded_quantity_count(fixture)
    assert result["model_counts"]["stock.quant"]["created"] == 0
    assert result["model_counts"]["stock.quant"]["updated"] == 0
    assert result["model_counts"]["stock.quant"]["skipped"] == len(fixture["products"])
