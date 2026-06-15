from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from app.scripts.seed_fake_odoo_data import Seeder, _build_parser, _load_fixture


class _FakeOdooClient:
    def __init__(self) -> None:
        self.next_id = 1
        self.records: dict[str, list[dict[str, Any]]] = defaultdict(list)
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
        for field_name in ("name", "default_code", "email", "client_order_ref", "partner_ref", "ref"):
            expected = _domain_value(domain, field_name)
            if expected is not None:
                matched = [record for record in records if record.get(field_name) == expected]
                return matched[: limit or 1]
        return []

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


def _fixture() -> dict[str, Any]:
    return _load_fixture(str(Path("electronics_fake_data.json")))


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
