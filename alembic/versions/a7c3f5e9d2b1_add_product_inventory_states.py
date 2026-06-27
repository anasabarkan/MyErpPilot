"""add_product_inventory_states

Revision ID: a7c3f5e9d2b1
Revises: f1e2d3c4b5a6
Create Date: 2026-06-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a7c3f5e9d2b1'
down_revision: Union[str, None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


product_inventory_status = sa.Enum(
    'in_stock',
    'low_stock',
    'out_of_stock',
    name='productinventorystatus',
)


def upgrade() -> None:
    product_inventory_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'product_inventory_states',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('odoo_product_id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=True),
        sa.Column('product_name', sa.String(length=255), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('reserved_quantity', sa.Float(), nullable=False),
        sa.Column('available_quantity', sa.Float(), nullable=False),
        sa.Column('state', product_inventory_status, nullable=False),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_state_changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_product_inventory_states_source_id', 'product_inventory_states', ['source_id'], unique=False)
    op.create_index('ix_product_inventory_states_odoo_product_id', 'product_inventory_states', ['odoo_product_id'], unique=False)
    op.create_index('ix_product_inventory_states_company_id', 'product_inventory_states', ['company_id'], unique=False)
    op.create_index('ix_product_inventory_states_state', 'product_inventory_states', ['state'], unique=False)
    op.execute(
        "CREATE UNIQUE INDEX uq_product_inventory_states_scope "
        "ON product_inventory_states (source_id, odoo_product_id, COALESCE(company_id, -1))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_product_inventory_states_scope")
    op.drop_index('ix_product_inventory_states_state', table_name='product_inventory_states')
    op.drop_index('ix_product_inventory_states_company_id', table_name='product_inventory_states')
    op.drop_index('ix_product_inventory_states_odoo_product_id', table_name='product_inventory_states')
    op.drop_index('ix_product_inventory_states_source_id', table_name='product_inventory_states')
    op.drop_table('product_inventory_states')
    product_inventory_status.drop(op.get_bind(), checkfirst=True)
