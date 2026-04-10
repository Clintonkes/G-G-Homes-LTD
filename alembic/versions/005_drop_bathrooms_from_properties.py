"""Drop bathrooms from properties.

Revision ID: 005_drop_bathrooms_from_properties
Revises: 004_add_office_warehouse
Create Date: 2026-04-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "005_drop_bathrooms_from_properties"
down_revision = "004_add_office_warehouse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("properties", "bathrooms")


def downgrade() -> None:
    op.add_column("properties", sa.Column("bathrooms", sa.Integer(), nullable=False, server_default="1"))
