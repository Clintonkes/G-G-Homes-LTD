"""Notification service for rent reminders and outbound tenant or landlord updates."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment, PaymentStatus, PaymentType, Property, User
from services.whatsapp_service import whatsapp
from utils.helpers import format_naira


class NotificationService:
    async def send_rent_renewal_reminders(self, db: AsyncSession) -> int:
        sent_count = 0
        now = datetime.now(timezone.utc)
        for days in [90, 60, 30]:
            target_start = now + timedelta(days=days)
            target_end = target_start + timedelta(days=1)
            result = await db.execute(
                select(Payment).where(
                    Payment.payment_type == PaymentType.rent,
                    Payment.status == PaymentStatus.success,
                    Payment.tenancy_end_date >= target_start,
                    Payment.tenancy_end_date < target_end,
                )
            )
            for payment in result.scalars().all():
                if await self._send_renewal_reminder(db, payment, days):
                    sent_count += 1
        return sent_count

    async def _send_renewal_reminder(self, db: AsyncSession, payment: Payment, days_remaining: int) -> bool:
        tenant = await db.get(User, payment.payer_id)
        prop = await db.get(Property, payment.property_id) if payment.property_id else None
        if not tenant or not prop:
            return False

        sent = await whatsapp.send_buttons(
            tenant.phone_number,
            (
                f"Your tenancy for {prop.title} expires in {days_remaining} days.\n"
                f"Rent due: {format_naira(payment.gross_amount)}"
            ),
            [
                {"id": f"renew_{payment.id}", "title": "Renew Now"},
                {"id": f"remind_{payment.id}", "title": "Remind Later"},
            ],
        )
        if days_remaining == 30:
            landlord = await db.get(User, prop.landlord_id)
            if landlord:
                await whatsapp.send_text(landlord.phone_number, f"Tenant renewal for {prop.title} is due in 30 days.")
        return sent


notification_service = NotificationService()
