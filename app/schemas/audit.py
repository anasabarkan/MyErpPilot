"""
Pydantic schemas for the Audit Logging system.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Internal Schemas ──────────────────────────────

class AuditLogCreateInternal(BaseModel):
    """Payload for internal service creation of an audit log. Not exposed to users."""
    user_id: Optional[UUID] = None
    action: str = Field(..., max_length=255)
    entity_type: str = Field(..., max_length=255)
    entity_id: Optional[str] = Field(None, max_length=255)
    status: str = Field(default="success", max_length=50)
    message: Optional[str] = Field(None, max_length=1000)
    metadata_: Optional[Dict[str, Any]] = Field(None, alias="metadata")
    ip_address: Optional[str] = Field(None, max_length=50)
    user_agent: Optional[str] = Field(None, max_length=500)


# ── Response Schemas ─────────────────────────────

class AuditLogResponse(BaseModel):
    """Public representation of an AuditLog entry."""
    id: UUID
    user_id: Optional[UUID] = None
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    status: str
    message: Optional[str] = None
    metadata_: Optional[Dict[str, Any]] = Field(None, validation_alias="metadata_")
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "populate_by_name": True
    }


class AuditLogListResponse(BaseModel):
    """Response payload for a paginated list of audit logs."""
    items: List[AuditLogResponse]
    total: int
