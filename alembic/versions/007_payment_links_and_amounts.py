"""Add appointment payment links and negotiated rent amounts.

Revision ID: 007_payment_links
Revises: 006_add_inspection_contact_fields
Create Date: 2026-04-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "007_payment_links"
down_revision = "006_add_inspection_contact_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("original_rent_amount", sa.Float(), nullable=True))
    op.add_column("appointments", sa.Column("agreed_rent_amount", sa.Float(), nullable=True))
    op.add_column("payments", sa.Column("appointment_id", sa.Integer(), nullable=True))
    op.add_column("payments", sa.Column("quoted_amount", sa.Float(), nullable=True))
    op.add_column("payments", sa.Column("agreed_amount", sa.Float(), nullable=True))
    op.create_index(op.f("ix_payments_appointment_id"), "payments", ["appointment_id"], unique=False)
    op.create_foreign_key("fk_payments_appointment_id_appointments", "payments", "appointments", ["appointment_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_payments_appointment_id_appointments", "payments", type_="foreignkey")
    op.drop_index(op.f("ix_payments_appointment_id"), table_name="payments")
    op.drop_column("payments", "agreed_amount")
    op.drop_column("payments", "quoted_amount")
    op.drop_column("payments", "appointment_id")
    op.drop_column("appointments", "agreed_rent_amount")
    op.drop_column("appointments", "original_rent_amount")
