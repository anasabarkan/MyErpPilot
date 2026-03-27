import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Enum, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database.session import Base

class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    executed = "executed"
    failed = "failed"

class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False)
    description = Column(String, nullable=True)
    action_type = Column(String(100), nullable=False)
    payload = Column(JSONB, nullable=False)
    status = Column(Enum(ApprovalStatus), default=ApprovalStatus.pending, nullable=False, index=True)
    
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    validated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    executed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    validated_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    
    rejection_reason = Column(String, nullable=True)

    # Relationships
    requester = relationship("User", foreign_keys=[requested_by], backref="proposed_actions")
    validator = relationship("User", foreign_keys=[validated_by], backref="validated_actions")
    executor = relationship("User", foreign_keys=[executed_by], backref="executed_actions")

    def __repr__(self) -> str:
        return f"<ApprovalRequest {self.id} (status: {self.status})>"
