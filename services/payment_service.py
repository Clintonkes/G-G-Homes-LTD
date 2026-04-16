"""Payment orchestration service for initializing and verifying Paystack transactions."""

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.models import Appointment, AppointmentStatus, Payment, PaymentStatus, PaymentType, Property, PropertyStatus, User

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaymentService:
    async def initialize_rent_payment(
        self,
        db: AsyncSession,
        tenant: User,
        property_id: int,
        appointment_id: int | None = None,
        agreed_amount: float | None = None,
    ) -> dict:
        prop = await db.get(Property, property_id)
        if not prop:
            raise ValueError("Property not found")

        if appointment_id is not None:
            existing_result = await db.execute(
                select(Payment).where(
                    Payment.appointment_id == appointment_id,
                    Payment.status == PaymentStatus.pending,
                ).order_by(Payment.created_at.desc())
            )
            existing_payment = existing_result.scalar_one_or_none()
            if existing_payment and existing_payment.checkout_url:
                return {
                    "payment_url": existing_payment.checkout_url,
                    "reference": existing_payment.paystack_reference,
                    "gross_amount": float(existing_payment.gross_amount),
                }

        quoted_amount = float(prop.annual_rent)
        gross_amount = float(agreed_amount if agreed_amount is not None else quoted_amount)
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
                    "metadata": {
                        "tenant_id": tenant.id,
                        "property_id": property_id,
                        "appointment_id": appointment_id,
                        "quoted_amount": quoted_amount,
                        "agreed_amount": gross_amount,
                    },
                    "callback_url": f"{settings.BASE_URL}/api/v1/payments/callback",
                },
            )
        response.raise_for_status()
        data = response.json()["data"]

        payment = Payment(
            payer_id=tenant.id,
            property_id=property_id,
            appointment_id=appointment_id,
            payment_type=PaymentType.rent,
            quoted_amount=quoted_amount,
            agreed_amount=gross_amount,
            gross_amount=gross_amount,
            platform_fee=platform_fee,
            net_amount=net_amount,
            paystack_reference=data["reference"],
            checkout_url=data.get("authorization_url"),
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
            if payment.appointment_id:
                appointment = await db.get(Appointment, payment.appointment_id)
                if appointment:
                    appointment.status = AppointmentStatus.completed
        else:
            payment.status = PaymentStatus.failed

        await db.commit()
        await db.refresh(payment)
        return payment


payment_service = PaymentService()
