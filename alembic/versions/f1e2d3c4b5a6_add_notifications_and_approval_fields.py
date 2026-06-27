"""add_notifications_and_approval_fields

Revision ID: f1e2d3c4b5a6
Revises: b2cee402625c
Create Date: 2026-06-19 16:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, None] = 'b2cee402625c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the notifications table
    op.create_table('notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('type', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('severity', postgresql.ENUM('info', 'warning', 'critical', name='notificationseverity', create_type=False), nullable=False),
        sa.Column('status', postgresql.ENUM('unread', 'read', name='notificationstatus', create_type=False), nullable=False),
        sa.Column('related_entity_type', sa.String(length=100), nullable=True),
        sa.Column('related_entity_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)
    op.create_index(op.f('ix_notifications_status'), 'notifications', ['status'], unique=False)
    op.create_index(op.f('ix_notifications_type'), 'notifications', ['type'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)

    # 2. Add new columns to approval_requests
    op.add_column('approval_requests', sa.Column('product_id', sa.Integer(), nullable=True))
    op.add_column('approval_requests', sa.Column('product_name', sa.String(length=255), nullable=True))
    op.add_column('approval_requests', sa.Column('current_qty', sa.Float(), nullable=True))
    op.add_column('approval_requests', sa.Column('min_qty', sa.Float(), nullable=True))
    op.add_column('approval_requests', sa.Column('requested_quantity', sa.Float(), nullable=True))
    op.add_column('approval_requests', sa.Column('decision_reason', sa.String(), nullable=True))
    op.create_index(op.f('ix_approval_requests_product_id'), 'approval_requests', ['product_id'], unique=False)

    # 3. Migrate existing data from payload JSON to new columns
    op.execute("""
        UPDATE approval_requests 
        SET product_id = (payload->>'product_id')::integer,
            product_name = payload->>'product',
            current_qty = (payload->>'available_quantity')::numeric,
            min_qty = (payload->>'min_required')::numeric
        WHERE action_type = 'low_stock_forecast_alert'
    """)


def downgrade() -> None:
    # Remove added columns
    op.drop_index(op.f('ix_approval_requests_product_id'), table_name='approval_requests')
    op.drop_column('approval_requests', 'decision_reason')
    op.drop_column('approval_requests', 'requested_quantity')
    op.drop_column('approval_requests', 'min_qty')
    op.drop_column('approval_requests', 'current_qty')
    op.drop_column('approval_requests', 'product_name')
    op.drop_column('approval_requests', 'product_id')

    # Drop notifications table
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_type'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_status'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_created_at'), table_name='notifications')
    op.drop_table('notifications')
    
    # Drop ENUMs created by Alembic
    op.execute("DROP TYPE IF EXISTS notificationseverity")
    op.execute("DROP TYPE IF EXISTS notificationstatus")
