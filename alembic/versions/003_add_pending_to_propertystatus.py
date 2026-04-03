"""Add pending value to propertystatus enum

Revision ID: 003_add_pending_to_propertystatus
Revises: 002_add_state_to_property
Create Date: 2026-04-01

The propertystatus PostgreSQL enum was created without the 'pending' value.
This migration adds it so that new property listings submitted via WhatsApp
can be saved with status='pending' awaiting admin verification.
"""

from alembic import op

revision = "003_add_pending_status"
down_revision = "002_add_state_to_property"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires a transaction commit before new enum values are visible,
    # so we use AUTOCOMMIT mode via op.execute with the raw DDL.
    op.execute("ALTER TYPE propertystatus ADD VALUE IF NOT EXISTS 'pending'")


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values.
    # A full type recreation would be required; left as a no-op intentionally.
    pass
