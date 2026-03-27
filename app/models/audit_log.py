"""
AuditLog SQLAlchemy model to track all important actions performed in the application.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.session import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    
    action = Column(String(255), nullable=False, index=True)
    entity_type = Column(String(255), nullable=False, index=True)
    entity_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="success")  # success / failed / denied
    
    message = Column(String(1000), nullable=True)
    metadata_ = Column("metadata", JSON, nullable=True)
    
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    user = relationship("User")

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} on {self.entity_type} ({self.status})>"
