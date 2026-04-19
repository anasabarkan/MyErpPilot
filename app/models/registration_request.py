import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.session import Base


class RegistrationProvider(str, enum.Enum):
    local = "local"
    google = "google"


class RegistrationRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class RegistrationRequest(Base):
    __tablename__ = "registration_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    provider = Column(
        Enum(RegistrationProvider, name="registration_provider"),
        nullable=False,
    )

    # Local registrations only.
    hashed_password = Column(String(255), nullable=True)

    # Google registrations only.
    provider_id = Column(String(255), nullable=True)
    avatar_url = Column(String(500), nullable=True)

    status = Column(
        Enum(RegistrationRequestStatus, name="registration_request_status"),
        default=RegistrationRequestStatus.pending,
        nullable=False,
        index=True,
    )
    rejection_reason = Column(String, nullable=True)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    reviewer = relationship("User", foreign_keys=[reviewed_by], backref="reviewed_registration_requests")

    def __repr__(self) -> str:
        return f"<RegistrationRequest {self.email} ({self.provider}, {self.status})>"

