import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class PurchaseOrderServiceInterface(ABC):
    @abstractmethod
    def create_purchase_order(self, product_id: int, quantity: float, vendor_id: Optional[int] = None) -> Dict[str, Any]:
        """Create a purchase order and receive it into Odoo stock."""
        pass


class OdooPurchaseOrderService(PurchaseOrderServiceInterface):
    def __init__(self, db: Session) -> None:
        self.db = db

    def _first_existing_field(self, client: Any, user_id: int, model: str, candidates: list[str]) -> Optional[str]:
        for field_name in candidates:
            try:
                count = client.search_count(
                    user_id=user_id,
                    model="ir.model.fields",
                    domain=[("model", "=", model), ("name", "=", field_name)],
                )
            except Exception:
                return None
            if count > 0:
                return field_name
        return None

    def _process_receipt_wizard(self, client: Any, user_id: int, action_result: Any) -> bool:
        if not isinstance(action_result, dict):
            return False

        res_model = action_result.get("res_model")
        res_id = action_result.get("res_id")
        if res_model not in {"stock.immediate.transfer", "stock.backorder.confirmation"}:
            return False
        if not isinstance(res_id, int):
            return False

        client.execute_kw(
            user_id=user_id,
            model=res_model,
            method="process",
            args=[[res_id]],
        )
        return True

    def _try_force_done_quantities(
        self,
        *,
        client: Any,
        user_id: int,
        model_exists: Any,
        picking_ids: list[int],
        dashboard_service: Any,
    ) -> None:
        if not picking_ids or not model_exists("stock.move.line"):
            return

        done_field = self._first_existing_field(
            client,
            user_id,
            "stock.move.line",
            ["qty_done", "quantity_done", "quantity"],
        )
        planned_field = self._first_existing_field(
            client,
            user_id,
            "stock.move.line",
            ["product_uom_qty", "reserved_uom_qty", "quantity"],
        )
        if done_field is None or planned_field is None:
            return

        fields = ["id", done_field]
        if planned_field not in fields:
            fields.append(planned_field)

        move_lines, move_line_error = dashboard_service.safe_search_read(
            "stock.move.line",
            [("picking_id", "in", picking_ids)],
            fields,
            "create_po.receipt_move_lines",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            limit=5000,
        )
        if move_line_error:
            logger.warning("Failed to fetch stock move lines for receipt completion: %s", move_line_error)
            return

        for line in move_lines:
            line_id = line.get("id")
            if not isinstance(line_id, int):
                continue

            target_qty = dashboard_service._to_float(line.get(planned_field))
            current_qty = dashboard_service._to_float(line.get(done_field))
            if target_qty <= 0 or abs(current_qty - target_qty) < 1e-6:
                continue

            client.execute_kw(
                user_id=user_id,
                model="stock.move.line",
                method="write",
                args=[[line_id], {done_field: target_qty}],
            )

    def _read_purchase_order_snapshot(
        self,
        *,
        client: Any,
        user_id: int,
        model_exists: Any,
        dashboard_service: Any,
        po_id: int,
    ) -> tuple[str, str, list[int]]:
        po_rows, po_error = dashboard_service.safe_search_read(
            "purchase.order",
            [("id", "=", po_id)],
            ["name", "state", "picking_ids"],
            "create_po.po_lookup",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            limit=1,
        )
        if po_error or not po_rows:
            raise RuntimeError(f"Unable to read created purchase order {po_id}: {po_error or 'record not found'}")

        po_row = po_rows[0]
        po_name = po_row.get("name") or f"PO#{po_id}"
        po_state = po_row.get("state") or "draft"
        raw_picking_ids = po_row.get("picking_ids") or []
        picking_ids = [picking_id for picking_id in raw_picking_ids if isinstance(picking_id, int)]
        return str(po_name), str(po_state), picking_ids

    def _receive_purchase_order(
        self,
        *,
        client: Any,
        user_id: int,
        model_exists: Any,
        dashboard_service: Any,
        po_id: int,
        po_name: str,
    ) -> dict[str, Any]:
        if not model_exists("stock.picking"):
            raise RuntimeError("Odoo stock receipt models are not available. Please install the Inventory module in Odoo.")

        _po_name, _po_state, picking_ids = self._read_purchase_order_snapshot(
            client=client,
            user_id=user_id,
            model_exists=model_exists,
            dashboard_service=dashboard_service,
            po_id=po_id,
        )
        if not picking_ids:
            receipt_rows, receipt_error = dashboard_service.safe_search_read(
                "stock.picking",
                [("origin", "=", po_name)],
                ["id"],
                "create_po.receipt_lookup_by_origin",
                client=client,
                user_id=user_id,
                model_exists_fn=model_exists,
                limit=20,
            )
            if receipt_error:
                raise RuntimeError(f"Unable to locate the incoming receipt for {po_name}: {receipt_error}")
            picking_ids = [row["id"] for row in receipt_rows if isinstance(row.get("id"), int)]

        if not picking_ids:
            raise RuntimeError(f"No incoming receipt was created for purchase order {po_name}.")

        for picking_id in picking_ids:
            try:
                client.execute_kw(
                    user_id=user_id,
                    model="stock.picking",
                    method="action_assign",
                    args=[[picking_id]],
                )
            except Exception as exc:
                logger.debug("Receipt %s could not be assigned before validation: %s", picking_id, exc)

            validate_result = client.execute_kw(
                user_id=user_id,
                model="stock.picking",
                method="button_validate",
                args=[[picking_id]],
            )
            if self._process_receipt_wizard(client, user_id, validate_result):
                continue

        receipt_rows, receipt_error = dashboard_service.safe_search_read(
            "stock.picking",
            [("id", "in", picking_ids)],
            ["id", "name", "state"],
            "create_po.receipt_status_lookup",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            limit=100,
        )
        if receipt_error:
            raise RuntimeError(f"Unable to verify receipt state for {po_name}: {receipt_error}")

        incomplete_ids = [
            row["id"]
            for row in receipt_rows
            if isinstance(row.get("id"), int) and str(row.get("state") or "") != "done"
        ]
        if incomplete_ids:
            self._try_force_done_quantities(
                client=client,
                user_id=user_id,
                model_exists=model_exists,
                picking_ids=incomplete_ids,
                dashboard_service=dashboard_service,
            )
            for picking_id in incomplete_ids:
                validate_result = client.execute_kw(
                    user_id=user_id,
                    model="stock.picking",
                    method="button_validate",
                    args=[[picking_id]],
                )
                self._process_receipt_wizard(client, user_id, validate_result)

            receipt_rows, receipt_error = dashboard_service.safe_search_read(
                "stock.picking",
                [("id", "in", picking_ids)],
                ["id", "name", "state"],
                "create_po.receipt_status_lookup_final",
                client=client,
                user_id=user_id,
                model_exists_fn=model_exists,
                limit=100,
            )
            if receipt_error:
                raise RuntimeError(f"Unable to verify final receipt state for {po_name}: {receipt_error}")

        receipt_states = {int(row["id"]): str(row.get("state") or "unknown") for row in receipt_rows if isinstance(row.get("id"), int)}
        remaining = [receipt_id for receipt_id, state in receipt_states.items() if state != "done"]
        if remaining:
            raise RuntimeError(
                f"Purchase order {po_name} was created, but receipts {remaining} are still not completed in Odoo."
            )

        receipt_names = [
            str(row.get("name") or f"Receipt#{row['id']}")
            for row in receipt_rows
            if isinstance(row.get("id"), int)
        ]
        return {
            "receipt_ids": sorted(receipt_states.keys()),
            "receipt_names": receipt_names,
            "receipt_states": receipt_states,
            "stock_updated": True,
        }

    def create_purchase_order(self, product_id: int, quantity: float, vendor_id: Optional[int] = None) -> Dict[str, Any]:
        logger.info("OdooPurchaseOrderService: Creating PO for product %s, qty %s", product_id, quantity)

        from app.services import dashboard_service

        try:
            _active_source, client, user_id, resolver, _available_models, _warnings = dashboard_service._build_runtime_context(self.db)
        except Exception as exc:
            logger.error("Failed to build Odoo context for PO creation: %s", exc)
            raise RuntimeError(f"Odoo integration is not configured or authenticated: {exc}")

        def model_exists(model_name: str) -> bool:
            return resolver.model_exists(model_name)

        if not model_exists("purchase.order") or not model_exists("purchase.order.line"):
            raise RuntimeError("Odoo Purchase Order models are not available. Please install the Purchase module in Odoo.")

        product_rows, product_err = dashboard_service.safe_search_read(
            "product.product",
            [("id", "=", product_id)],
            ["id", "display_name", "name", "standard_price", "seller_ids"],
            "create_po.product_lookup",
            client=client,
            user_id=user_id,
            model_exists_fn=model_exists,
            limit=1,
        )
        if product_err or not product_rows:
            product_name = f"Product #{product_id}"
            standard_price = 0.0
            seller_ids = []
            logger.warning(
                "Product %s not found in Odoo or error occurred: %s. Using fallbacks.",
                product_id,
                product_err,
            )
        else:
            product_row = product_rows[0]
            product_name = product_row.get("display_name") or product_row.get("name") or f"Product #{product_id}"
            standard_price = dashboard_service._to_float(product_row.get("standard_price"))
            seller_ids = product_row.get("seller_ids") or []

        if not vendor_id and seller_ids and model_exists("product.supplierinfo"):
            sellers, seller_err = dashboard_service.safe_search_read(
                "product.supplierinfo",
                [("id", "in", seller_ids)],
                ["partner_id"],
                "create_po.seller_lookup",
                client=client,
                user_id=user_id,
                model_exists_fn=model_exists,
                limit=1,
            )
            if sellers and not seller_err:
                vendor_id = dashboard_service._extract_many2one_id(sellers[0].get("partner_id"))

        if not vendor_id:
            suppliers, supplier_err = dashboard_service.safe_search_read(
                "res.partner",
                [("supplier_rank", ">", 0)],
                ["id"],
                "create_po.supplier_rank_lookup",
                client=client,
                user_id=user_id,
                model_exists_fn=model_exists,
                limit=1,
            )
            if suppliers and not supplier_err:
                vendor_id = suppliers[0]["id"]

        if not vendor_id:
            companies, company_err = dashboard_service.safe_search_read(
                "res.partner",
                [("is_company", "=", True)],
                ["id"],
                "create_po.company_lookup",
                client=client,
                user_id=user_id,
                model_exists_fn=model_exists,
                limit=1,
            )
            if companies and not company_err:
                vendor_id = companies[0]["id"]

        if not vendor_id:
            any_partner, partner_err = dashboard_service.safe_search_read(
                "res.partner",
                [],
                ["id"],
                "create_po.any_partner_lookup",
                client=client,
                user_id=user_id,
                model_exists_fn=model_exists,
                limit=1,
            )
            if any_partner and not partner_err:
                vendor_id = any_partner[0]["id"]

        if not vendor_id:
            raise ValueError("Could not find any vendor/supplier in Odoo to assign to the purchase order.")

        planned_date = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        po_line = (
            0,
            0,
            {
                "product_id": product_id,
                "product_qty": quantity,
                "price_unit": standard_price,
                "name": product_name,
                "date_planned": planned_date,
            },
        )

        po_values = {
            "partner_id": vendor_id,
            "date_order": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "order_line": [po_line],
        }

        try:
            po_id = client.execute_kw(
                user_id=user_id,
                model="purchase.order",
                method="create",
                args=[po_values],
            )
        except Exception as exc:
            logger.error("Failed to execute create on purchase.order: %s", exc)
            raise RuntimeError(f"Odoo purchase order creation failed: {exc}")

        if not isinstance(po_id, int):
            raise RuntimeError(f"Odoo purchase order creation returned unexpected result: {po_id}")

        try:
            client.execute_kw(
                user_id=user_id,
                model="purchase.order",
                method="button_confirm",
                args=[[po_id]],
            )
        except Exception as exc:
            logger.error("Failed to confirm purchase.order %s: %s", po_id, exc)
            raise RuntimeError(f"Odoo purchase order confirmation failed: {exc}")

        po_name, po_state, _picking_ids = self._read_purchase_order_snapshot(
            client=client,
            user_id=user_id,
            model_exists=model_exists,
            dashboard_service=dashboard_service,
            po_id=po_id,
        )
        receipt_result = self._receive_purchase_order(
            client=client,
            user_id=user_id,
            model_exists=model_exists,
            dashboard_service=dashboard_service,
            po_id=po_id,
            po_name=po_name,
        )

        logger.info("OdooPurchaseOrderService: Created and received PO %s (ID: %s) successfully in Odoo.", po_name, po_id)
        return {
            "id": po_id,
            "name": po_name,
            "state": po_state,
            "product_id": product_id,
            "quantity": quantity,
            "vendor_id": vendor_id,
            **receipt_result,
        }


class MockPurchaseOrderService(PurchaseOrderServiceInterface):
    def create_purchase_order(self, product_id: int, quantity: float, vendor_id: Optional[int] = None) -> Dict[str, Any]:
        logger.info("MockPurchaseOrderService: Creating mock PO for product %s, qty %s", product_id, quantity)
        return {
            "id": 999,
            "name": "P00999",
            "state": "purchase",
            "product_id": product_id,
            "quantity": quantity,
            "vendor_id": vendor_id,
            "receipt_ids": [1999],
            "receipt_names": ["WH/IN/01999"],
            "receipt_states": {1999: "done"},
            "stock_updated": True,
            "mocked": True,
        }


def get_purchase_order_service(db: Optional[Session] = None) -> PurchaseOrderServiceInterface:
    """
    Factory function to get the appropriate purchase order service.
    Returns OdooPurchaseOrderService if db is provided and Odoo source is active.
    Otherwise returns MockPurchaseOrderService.
    """
    if db:
        from app.repositories import data_source_repository as data_source_repo

        active_source = data_source_repo.get_active_data_source(db)
        if active_source:
            return OdooPurchaseOrderService(db)
    return MockPurchaseOrderService()