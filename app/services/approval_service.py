import logging
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.product_inventory_state import ProductInventoryStatus
from app.repositories import approval_repository, product_inventory_state_repository
from app.schemas.approval import ApprovalCreate
from app.services import audit_service
from app.workflows.approval_workflow import ApprovalWorkflow, ApprovalWorkflowTransitionError

logger = logging.getLogger(__name__)
_AUTO_RESOLVED_REASON = "Resolved automatically after stock recovered in Odoo."


def _effective_alert_threshold(raw_min_qty: float) -> float:
    _ = raw_min_qty
    return settings.stock_alert_min_threshold


def _extract_payload_company_id(request_obj: ApprovalRequest) -> Optional[int]:
    if not isinstance(request_obj.payload, dict):
        return None

    company_id = request_obj.payload.get("company_id")
    if isinstance(company_id, bool):
        return None
    if isinstance(company_id, int):
        return company_id
    if isinstance(company_id, float) and company_id.is_integer():
        return int(company_id)
    return None


def _extract_payload_source_id(request_obj: ApprovalRequest) -> Optional[str]:
    if not isinstance(request_obj.payload, dict):
        return None

    source_id = request_obj.payload.get("source_id")
    if isinstance(source_id, str) and source_id.strip():
        return source_id.strip()

    alert_key = request_obj.payload.get("alert_key")
    if isinstance(alert_key, str) and alert_key.startswith("low_stock_forecast:"):
        parts = alert_key.split(":", 3)
        if len(parts) == 4:
            return parts[1]
    return None


def create_approval_request(
    db: Session,
    obj_in: ApprovalCreate,
    user_id: UUID,
) -> ApprovalRequest:
    data = obj_in.model_dump()
    data["requested_by"] = user_id
    data["status"] = ApprovalStatus.pending

    request_obj = approval_repository.create_request(db, data)

    audit_service.log_approval_event(
        db,
        action="create",
        approval_id=str(request_obj.id),
        user_id=user_id,
        message=f"Created approval request for action: {request_obj.action_type}",
    )
    logger.info("Audit: User %s created ApprovalRequest %s (Action: %s)", user_id, request_obj.id, request_obj.action_type)

    return request_obj


def approve_request(
    db: Session,
    request_id: UUID,
    validator_id: UUID,
    requested_quantity: Optional[float] = None,
    decision_reason: Optional[str] = None,
) -> ApprovalRequest:
    request_obj = approval_repository.get_request_by_id(db, request_id)
    if not request_obj:
        raise ValueError("Approval request not found")

    ApprovalWorkflow.validate_transition(request_obj.status, ApprovalStatus.approved)

    if request_obj.action_type == "low_stock_forecast_alert":
        if requested_quantity is None or requested_quantity <= 0:
            raise ValueError("Requested quantity must be provided and greater than 0")
        if request_obj.product_id is None:
            raise ValueError("Low stock approval request is missing its product identifier")

    if requested_quantity is not None:
        request_obj.requested_quantity = requested_quantity
    if decision_reason is not None:
        request_obj.decision_reason = decision_reason

    approval_repository.update_status(
        db,
        request_obj,
        ApprovalStatus.approved,
        decision_reason=decision_reason,
        requested_quantity=requested_quantity,
    )
    approval_repository.assign_validator(
        db,
        request_obj,
        validator_id=validator_id,
        validated_at=datetime.now(timezone.utc),
    )

    if request_obj.action_type != "low_stock_forecast_alert":
        from app.services import notification_service

        notification_service.mark_related_alerts_as_read(db, str(request_id))

    audit_service.log_approval_event(
        db,
        action="approve",
        approval_id=str(request_id),
        user_id=validator_id,
        message=decision_reason,
    )
    logger.info("Audit: User %s approved ApprovalRequest %s", validator_id, request_id)

    if request_obj.action_type == "low_stock_forecast_alert":
        from app.services import notification_service
        from app.services.purchase_order_service import get_purchase_order_service

        try:
            po_service = get_purchase_order_service(db)
            result = po_service.create_purchase_order(
                product_id=request_obj.product_id,
                quantity=requested_quantity,
            )
            approval_repository.update_status(db, request_obj, ApprovalStatus.executed)
            approval_repository.assign_executor(
                db,
                request_obj,
                executor_id=validator_id,
                executed_at=datetime.now(timezone.utc),
            )
            notification_service.mark_related_alerts_as_read(db, str(request_id))
            stock_message = "and updated stock in Odoo" if result.get("stock_updated") else "but stock is still pending in Odoo"
            audit_service.log_execution_event(
                db,
                action="execute_request",
                approval_id=str(request_id),
                user_id=validator_id,
                status="success",
                message=f"Created PO {result.get('name')} for {requested_quantity} units {stock_message}",
                metadata=result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to create purchase order: %s", exc)
            approval_repository.update_status(db, request_obj, ApprovalStatus.failed)
            audit_service.log_execution_event(
                db,
                action="execute_request",
                approval_id=str(request_id),
                user_id=validator_id,
                status="failed",
                message=f"Failed to create purchase order: {exc}",
            )

    return request_obj


def reject_request(
    db: Session,
    request_id: UUID,
    validator_id: UUID,
    decision_reason: Optional[str] = None,
) -> ApprovalRequest:
    request_obj = approval_repository.get_request_by_id(db, request_id)
    if not request_obj:
        raise ValueError("Approval request not found")

    ApprovalWorkflow.validate_transition(request_obj.status, ApprovalStatus.rejected)

    if decision_reason is not None:
        request_obj.decision_reason = decision_reason
        request_obj.rejection_reason = decision_reason

    approval_repository.update_status(
        db,
        request_obj,
        ApprovalStatus.rejected,
        decision_reason=decision_reason,
        rejection_reason=decision_reason,
    )
    approval_repository.assign_validator(
        db,
        request_obj,
        validator_id=validator_id,
        validated_at=datetime.now(timezone.utc),
    )

    from app.services import notification_service

    notification_service.mark_related_alerts_as_read(db, str(request_id))

    audit_service.log_approval_event(
        db,
        action="reject",
        approval_id=str(request_id),
        user_id=validator_id,
        message=decision_reason,
    )
    logger.info("Audit: User %s rejected ApprovalRequest %s. Reason: %s", validator_id, request_id, decision_reason)
    return request_obj


def execute_request(
    db: Session,
    request_id: UUID,
    executor_id: UUID,
) -> ApprovalRequest:
    request_obj = approval_repository.get_request_by_id(db, request_id)
    if not request_obj:
        raise ValueError("Approval request not found")

    ApprovalWorkflow.validate_transition(request_obj.status, ApprovalStatus.executed)

    try:
        final_status = ApprovalStatus.executed
    except Exception as exc:  # pragma: no cover
        logger.error("Execution failed for request %s: %s", request_id, exc)
        final_status = ApprovalStatus.failed

    approval_repository.update_status(db, request_obj, final_status)
    approval_repository.assign_executor(
        db,
        request_obj,
        executor_id=executor_id,
        executed_at=datetime.now(timezone.utc),
    )

    audit_service.log_execution_event(
        db,
        action="execute_request",
        approval_id=str(request_id),
        user_id=executor_id,
        status="success" if final_status == ApprovalStatus.executed else "failed",
        message="Execution successful" if final_status == ApprovalStatus.executed else "Execution failed",
    )

    logger.info("Audit: User %s executed ApprovalRequest %s with status %s", executor_id, request_id, final_status)
    return request_obj


def refresh_pending_alerts_from_odoo(db: Session, requests: List[ApprovalRequest]) -> None:
    pending_alerts = [
        request_obj
        for request_obj in requests
        if request_obj.status == ApprovalStatus.pending
        and request_obj.action_type == "low_stock_forecast_alert"
        and request_obj.product_id is not None
    ]
    if not pending_alerts:
        return

    source_ids = {source_id for source_id in (_extract_payload_source_id(request_obj) for request_obj in pending_alerts) if source_id}
    product_ids = {request_obj.product_id for request_obj in pending_alerts if request_obj.product_id is not None}
    if not source_ids or not product_ids:
        return

    state_rows = product_inventory_state_repository.list_by_sources_and_products(
        db,
        source_ids=source_ids,
        product_ids=product_ids,
    )
    if not state_rows:
        return

    state_by_scope: dict[tuple[str, int, int | None], Any] = {}
    for row in state_rows:
        state_by_scope[(str(row.source_id), row.odoo_product_id, row.company_id)] = row

    updated = False
    auto_resolved_count = 0
    now = datetime.now(timezone.utc)
    for request_obj in pending_alerts:
        source_id = _extract_payload_source_id(request_obj)
        if source_id is None or request_obj.product_id is None:
            continue
        company_id = _extract_payload_company_id(request_obj)
        state_row = state_by_scope.get((source_id, request_obj.product_id, company_id))
        if state_row is None:
            continue

        live_qty = float(state_row.available_quantity)
        live_min = _effective_alert_threshold(request_obj.min_qty or 0.0)
        shortage = max(live_min - live_qty, 0.0)

        next_payload: Optional[dict[str, Any]] = None
        if isinstance(request_obj.payload, dict):
            next_payload = {
                **request_obj.payload,
                "quantity": float(state_row.quantity),
                "reserved_quantity": float(state_row.reserved_quantity),
                "available_quantity": live_qty,
                "min_required": live_min,
                "shortage": shortage,
                "state": state_row.state.value if hasattr(state_row.state, "value") else str(state_row.state),
            }

        if request_obj.current_qty != live_qty:
            request_obj.current_qty = live_qty
            updated = True
        if request_obj.min_qty != live_min:
            request_obj.min_qty = live_min
            updated = True
        if next_payload is not None and next_payload != request_obj.payload:
            request_obj.payload = next_payload
            updated = True

        if state_row.state == ProductInventoryStatus.in_stock:
            request_obj.status = ApprovalStatus.executed
            request_obj.executed_at = request_obj.executed_at or now
            if not request_obj.decision_reason:
                request_obj.decision_reason = _AUTO_RESOLVED_REASON
            if next_payload is not None:
                request_obj.payload = {
                    **next_payload,
                    "resolved_automatically": True,
                    "resolved_at": now.isoformat(),
                }
            updated = True
            auto_resolved_count += 1

    if not updated:
        return

    if auto_resolved_count:
        from app.services import notification_service

        for request_obj in pending_alerts:
            if request_obj.status == ApprovalStatus.executed and getattr(request_obj, "id", None) is not None:
                notification_service.mark_related_alerts_as_read(
                    db,
                    str(request_obj.id),
                    commit=False,
                )

    try:
        db.commit()
        if auto_resolved_count:
            logger.info("Automatically resolved %s recovered low-stock alert(s) after inventory state refresh.", auto_resolved_count)
        else:
            logger.info("Successfully updated pending alerts quantities from local inventory state.")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("Failed to commit updated alert quantities: %s", exc)


def list_requests(
    db: Session,
    status: Optional[ApprovalStatus] = None,
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100,
):
    items, total = approval_repository.list_requests(db, status=status, user_id=user_id, skip=skip, limit=limit)
    if items:
        refresh_pending_alerts_from_odoo(db, items)
        if status == ApprovalStatus.pending and any(item.status != ApprovalStatus.pending for item in items):
            return approval_repository.list_requests(db, status=status, user_id=user_id, skip=skip, limit=limit)
    return items, total


def get_request(db: Session, request_id: UUID) -> Optional[ApprovalRequest]:
    request_obj = approval_repository.get_request_by_id(db, request_id)
    if request_obj:
        refresh_pending_alerts_from_odoo(db, [request_obj])
    return request_obj

