"""add state to property

Revision ID: 002_add_state_to_property
Revises: 001_initial_tables
Create Date: 2026-04-01
"""

from alembic import op
import sqlalchemy as sa

revision = "002_add_state_to_property"
down_revision = "001_initial_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("state", sa.String(100), server_default="Ebonyi"))


def downgrade() -> None:
    op.drop_column("properties", "state")
