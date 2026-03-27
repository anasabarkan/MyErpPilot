"""
Approval service.

Handles the human-in-the-loop approval / rejection workflow.
When an action is approved, it triggers ERP execution and audit logging.
"""

import json
from datetime import datetime

from app.database.audit_logs import log_action
from app.database.session import SessionLocal
from app.tools.erp_action_tool import execute_erp_action
from app.utils.logger import get_logger
from app.validation.validation_queue import ValidationItem

logger = get_logger(__name__)


def approve_action(
    item_id: int,
    reviewer: str = "human_reviewer",
    notes: str = "",
) -> dict:
    """
    Approve a pending validation item, execute the ERP action,
    and record the result in the audit log.

    Returns:
        {"success": bool, "action_result": dict, "error": str | None}
    """
    db = SessionLocal()
    try:
        item = db.query(ValidationItem).filter_by(id=item_id).first()
        if not item:
            return {"success": False, "action_result": {}, "error": "Item not found"}

        if item.status != "PENDING":
            return {
                "success": False,
                "action_result": {},
                "error": f"Item is already {item.status}",
            }

        # ── Update validation status ───────────────────────
        item.status = "APPROVED"
        item.reviewer = reviewer
        item.review_notes = notes
        item.reviewed_at = datetime.utcnow()
        db.commit()

        # ── Execute the ERP action ─────────────────────────
        action = json.loads(item.action_payload) if item.action_payload else {}
        action_result = execute_erp_action(action)

        # ── Audit log ─────────────────────────────────────
        log_action(
            user_id=reviewer,
            query=f"Approved action {item_id}",
            suggested_action=action,
            validation_status="APPROVED",
            action_result=action_result,
        )

        logger.info("Action %d approved by %s", item_id, reviewer)
        return {"success": True, "action_result": action_result, "error": None}

    except Exception as exc:
        logger.error("Approval failed: %s", exc)
        return {"success": False, "action_result": {}, "error": str(exc)}
    finally:
        db.close()


def reject_action(
    item_id: int,
    reviewer: str = "human_reviewer",
    notes: str = "",
) -> dict:
    """
    Reject a pending validation item and record in audit log.

    Returns:
        {"success": bool, "error": str | None}
    """
    db = SessionLocal()
    try:
        item = db.query(ValidationItem).filter_by(id=item_id).first()
        if not item:
            return {"success": False, "error": "Item not found"}

        if item.status != "PENDING":
            return {"success": False, "error": f"Item is already {item.status}"}

        # ── Update validation status ───────────────────────
        item.status = "REJECTED"
        item.reviewer = reviewer
        item.review_notes = notes
        item.reviewed_at = datetime.utcnow()
        db.commit()

        # ── Audit log ─────────────────────────────────────
        action = json.loads(item.action_payload) if item.action_payload else {}
        log_action(
            user_id=reviewer,
            query=f"Rejected action {item_id}",
            suggested_action=action,
            validation_status="REJECTED",
        )

        logger.info("Action %d rejected by %s", item_id, reviewer)
        return {"success": True, "error": None}

    except Exception as exc:
        logger.error("Rejection failed: %s", exc)
        return {"success": False, "error": str(exc)}
    finally:
        db.close()
