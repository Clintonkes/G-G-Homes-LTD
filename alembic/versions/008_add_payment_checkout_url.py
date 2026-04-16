"""Add checkout URL to payment records.

Revision ID: 008_add_payment_checkout_url
Revises: 007_payment_links
Create Date: 2026-04-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "008_add_payment_checkout_url"
down_revision = "007_payment_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("checkout_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "checkout_url")
