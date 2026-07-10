from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.approval import ApprovalStatus

class ApprovalCreate(BaseModel):
    title: str = Field(..., max_length=255, description="Short title of the proposed action")
    description: Optional[str] = Field(None, description="Detailed explanation of the action")
    action_type: str = Field(..., max_length=100, description="Identifier for the type of action to execute")
    payload: Dict[str, Any] = Field(..., description="JSON payload containing data needed for execution")

class ApprovalResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    action_type: str
    payload: Dict[str, Any]
    status: ApprovalStatus
    requested_by: UUID
    validated_by: Optional[UUID] = None
    executed_by: Optional[UUID] = None
    created_at: datetime
    validated_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    
    # Human-in-the-Loop Low Stock Workflow
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    current_qty: Optional[float] = None
    min_qty: Optional[float] = None
    requested_quantity: Optional[float] = None
    decision_reason: Optional[str] = None

    class Config:
        from_attributes = True

class ApproveRequest(BaseModel):
    decision_reason: Optional[str] = Field(None, description="Optional reason for approval")

class ExecuteRequest(BaseModel):
    requested_quantity: Optional[float] = Field(None, gt=0, description="Quantity to order for low-stock execution")

class RejectRequest(BaseModel):
    decision_reason: Optional[str] = Field(None, description="Optional reason for rejection")

class ApprovalUpdateStatus(BaseModel):
    # Depending on endpoint, this might not be needed if endpoints are specifically
    # /approve, /reject, /execute. But provided for completeness.
    status: ApprovalStatus
    rejection_reason: Optional[str] = None

class ApprovalListResponse(BaseModel):
    items: List[ApprovalResponse]
    total: int
