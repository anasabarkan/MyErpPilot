"""
Validation queue.

Stores pending actions that require human approval before
the orchestrator can execute them on the ERP system.
"""

import json
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.database.session import Base, SessionLocal
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ValidationItem(Base):
    """An action waiting for human approval."""

    __tablename__ = "validation_queue"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(String(100), nullable=False)
    action_type = Column(String(100))
    action_payload = Column(Text)           # JSON string of full action
    status = Column(String(50), default="PENDING")  # PENDING | APPROVED | REJECTED
    reviewer = Column(String(100))
    review_notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    reviewed_at = Column(DateTime)


def enqueue_action(workflow_id: str, action: dict) -> int:
    """
    Insert a new action into the validation queue.

    Returns the ID of the created validation item.
    """
    db = SessionLocal()
    try:
        item = ValidationItem(
            workflow_id=workflow_id,
            action_type=action.get("action_type", "unknown"),
            action_payload=json.dumps(action),
            status="PENDING",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        logger.info("Action enqueued — id=%d, type=%s", item.id, item.action_type)
        return item.id
    finally:
        db.close()


def get_pending() -> list[dict]:
    """Return all PENDING validation items."""
    db = SessionLocal()
    try:
        items = db.query(ValidationItem).filter_by(status="PENDING").all()
        return [
            {
                "id": item.id,
                "workflow_id": item.workflow_id,
                "action_type": item.action_type,
                "action_payload": json.loads(item.action_payload) if item.action_payload else {},
                "status": item.status,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ]
    finally:
        db.close()


def get_item(item_id: int) -> ValidationItem | None:
    """Retrieve a single validation item by ID."""
    db = SessionLocal()
    try:
        return db.query(ValidationItem).filter_by(id=item_id).first()
    finally:
        db.close()
