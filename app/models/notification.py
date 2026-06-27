"""
Notification SQLAlchemy model for user alerts.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.session import Base
import enum

class NotificationSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"

class NotificationStatus(str, enum.Enum):
    unread = "unread"
    read = "read"

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    type = Column(String(100), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    
    severity = Column(Enum(NotificationSeverity), default=NotificationSeverity.info, nullable=False)
    status = Column(Enum(NotificationStatus), default=NotificationStatus.unread, nullable=False, index=True)
    
    related_entity_type = Column(String(100), nullable=True)
    related_entity_id = Column(String(255), nullable=True)
    
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    read_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", backref="notifications")

    def __repr__(self) -> str:
        return f"<Notification {self.id} (type: {self.type}, status: {self.status})>"
