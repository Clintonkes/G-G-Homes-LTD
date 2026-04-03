"""Add office_space and warehouse to propertytype enum

Revision ID: 004_add_office_warehouse
Revises: 003_add_pending_status
Create Date: 2026-04-02
"""

from alembic import op

revision = "004_add_office_warehouse"
down_revision = "003_add_pending_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE propertytype ADD VALUE IF NOT EXISTS 'office_space'")
    op.execute("ALTER TYPE propertytype ADD VALUE IF NOT EXISTS 'warehouse'")


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values.
    pass
