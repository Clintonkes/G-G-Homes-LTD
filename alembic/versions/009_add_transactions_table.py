"""Add transactions table for gateway-level payment tracking.

Revision ID: 009_add_transactions_table
Revises: 008_add_payment_checkout_url
Create Date: 2026-04-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "009_add_transactions_table"
down_revision = "008_add_payment_checkout_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    transactionstatus = sa.Enum("pending", "success", "failed", "abandoned", name="transactionstatus")
    transactionstatus.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_reference", sa.String(length=100), nullable=False),
        sa.Column("status", transactionstatus, nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("gateway_status", sa.String(length=100), nullable=True),
        sa.Column("gateway_response", sa.String(length=500), nullable=True),
        sa.Column("verification_message", sa.String(length=500), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_transactions_payment_id"), "transactions", ["payment_id"], unique=False)
    op.create_index(op.f("ix_transactions_provider_reference"), "transactions", ["provider_reference"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_transactions_provider_reference"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_payment_id"), table_name="transactions")
    op.drop_table("transactions")
    transactionstatus = sa.Enum("pending", "success", "failed", "abandoned", name="transactionstatus")
    transactionstatus.drop(op.get_bind(), checkfirst=True)
