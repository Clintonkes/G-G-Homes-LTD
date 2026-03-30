from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.models import Payment, PaymentStatus, PaymentType, Property, PropertyStatus, User

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaymentService:
    async def initialize_rent_payment(self, db: AsyncSession, tenant: User, property_id: int) -> dict:
        prop = await db.get(Property, property_id)
        if not prop:
            raise ValueError("Property not found")

        gross_amount = prop.annual_rent
        platform_fee = round(gross_amount * settings.TRANSACTION_FEE_PERCENT / 100, 2)
        net_amount = gross_amount - platform_fee
        amount_kobo = int(gross_amount * 100)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{PAYSTACK_BASE_URL}/transaction/initialize",
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
                json={
                    "email": tenant.email or f"{tenant.phone_number}@rentease.ng",
                    "amount": amount_kobo,
                    "currency": "NGN",
                    "metadata": {"tenant_id": tenant.id, "property_id": property_id},
                    "callback_url": f"{settings.BASE_URL}/api/v1/payments/callback",
                },
            )
        response.raise_for_status()
        data = response.json()["data"]

        payment = Payment(
            payer_id=tenant.id,
            property_id=property_id,
            payment_type=PaymentType.rent,
            gross_amount=gross_amount,
            platform_fee=platform_fee,
            net_amount=net_amount,
            paystack_reference=data["reference"],
            status=PaymentStatus.pending,
            tenancy_start_date=datetime.now(timezone.utc),
            tenancy_end_date=datetime.now(timezone.utc) + timedelta(days=365),
        )
        db.add(payment)
        await db.commit()
        await db.refresh(payment)
        return {
            "payment_url": data["authorization_url"],
            "reference": data["reference"],
            "gross_amount": gross_amount,
        }

    async def verify_payment(self, db: AsyncSession, reference: str) -> Payment | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            )
        response.raise_for_status()
        paystack_data = response.json()["data"]

        result = await db.execute(select(Payment).where(Payment.paystack_reference == reference))
        payment = result.scalar_one_or_none()
        if not payment:
            return None

        if paystack_data["status"] == "success":
            payment.status = PaymentStatus.success
            prop = await db.get(Property, payment.property_id)
            if prop:
                prop.status = PropertyStatus.rented
        else:
            payment.status = PaymentStatus.failed

        await db.commit()
        await db.refresh(payment)
        return payment


payment_service = PaymentService()
