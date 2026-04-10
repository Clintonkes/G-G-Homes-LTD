"""Add inspection contact snapshots and user residential address.

Revision ID: 006_add_inspection_contact_fields
Revises: 005_drop_bathrooms_from_properties
Create Date: 2026-04-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "006_add_inspection_contact_fields"
down_revision = "005_drop_bathrooms_from_properties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("residential_address", sa.String(length=500), nullable=True))
    op.add_column("appointments", sa.Column("tenant_full_name_snapshot", sa.String(length=200), nullable=True))
    op.add_column("appointments", sa.Column("tenant_phone_snapshot", sa.String(length=20), nullable=True))
    op.add_column("appointments", sa.Column("tenant_address_snapshot", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("appointments", "tenant_address_snapshot")
    op.drop_column("appointments", "tenant_phone_snapshot")
    op.drop_column("appointments", "tenant_full_name_snapshot")
    op.drop_column("users", "residential_address")
