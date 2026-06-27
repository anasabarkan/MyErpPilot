from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.session import Base


class ProductInventoryStatus(str, enum.Enum):
    in_stock = "in_stock"
    low_stock = "low_stock"
    out_of_stock = "out_of_stock"


class ProductInventoryState(Base):
    __tablename__ = "product_inventory_states"
    __table_args__ = (
        Index("ix_product_inventory_states_source_id", "source_id"),
        Index("ix_product_inventory_states_odoo_product_id", "odoo_product_id"),
        Index("ix_product_inventory_states_company_id", "company_id"),
        Index("ix_product_inventory_states_state", "state"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False)
    odoo_product_id = Column(Integer, nullable=False)
    company_id = Column(Integer, nullable=True)
    product_name = Column(String(255), nullable=False)
    quantity = Column(Float, nullable=False, default=0.0)
    reserved_quantity = Column(Float, nullable=False, default=0.0)
    available_quantity = Column(Float, nullable=False, default=0.0)
    state = Column(Enum(ProductInventoryStatus), nullable=False)
    last_synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_state_changed_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    data_source = relationship("DataSource")

    def __repr__(self) -> str:
        return (
            f"<ProductInventoryState source={self.source_id} product={self.odoo_product_id} "
            f"company={self.company_id} state={self.state}>"
        )
