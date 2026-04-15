"""
DataSource SQLAlchemy model.

Represents an Odoo data source connection.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database.session import Base


class DataSource(Base):
    __tablename__ = "data_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    db_type = Column(String(50), default="odoo", nullable=False)
    odoo_url = Column("host", String(255), nullable=False)
    port = Column(String(10), nullable=False, default="8069")
    database_name = Column("database", String(255), nullable=False)
    username = Column(String(255), nullable=False)
    api_key_encrypted = Column("password", String(255), nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)

    created_by = Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    models_cache = Column("schema_cache", JSONB, nullable=True)

    last_tested_at = Column(DateTime(timezone=True), nullable=True)
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

    owner = relationship("User", back_populates="data_sources")

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key_encrypted)

    @property
    def models_count(self) -> int:
        if isinstance(self.models_cache, list):
            return len(self.models_cache)
        return 0

    def __repr__(self) -> str:
        return f"<DataSource {self.name} ({self.odoo_url})>"
