"""
User SQLAlchemy model.

Supports both local (email/password) and OAuth (Google) accounts.
Includes a ``role`` column to prepare for future RBAC extensions.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.session import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=True)  # NULL for OAuth-only accounts

    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    # Auth provider: "local" | "google"
    provider = Column(String(50), default="local", nullable=False)
    provider_id = Column(String(255), nullable=True)  # e.g. Google sub ID

    avatar_url = Column(String(500), nullable=True)

    # RBAC relationships
    roles = relationship("Role", secondary="user_roles", backref="users")

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

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.provider})>"
