"""Payment orchestration service for initializing and verifying Paystack transactions."""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.models import Appointment, AppointmentStatus, Payment, PaymentStatus, PaymentType, Property, PropertyStatus, Transaction, TransactionStatus, User

PAYSTACK_BASE_URL = "https://api.paystack.co"
logger = logging.getLogger(__name__)


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
        response_json = response.json()
        data = response_json["data"]
        logger.info(
            "Paystack initialize response received; reference=%s status=%s message=%s",
            data.get("reference"),
            response_json.get("status"),
            response_json.get("message"),
        )

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
        await db.flush()
        transaction = Transaction(
            payment_id=payment.id,
            provider="paystack",
            provider_reference=data["reference"],
            status=TransactionStatus.pending,
            amount=gross_amount,
            currency="NGN",
            gateway_status=str(response_json.get("status")),
            gateway_response=response_json.get("message"),
            verification_message="Transaction initialized successfully.",
            raw_payload=response_json,
        )
        db.add(transaction)
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
        response_json = response.json()
        paystack_data = response_json["data"]
        logger.info(
            "Paystack verify response received; reference=%s status=%s gateway_response=%s",
            reference,
            paystack_data.get("status"),
            paystack_data.get("gateway_response"),
        )

        result = await db.execute(select(Payment).where(Payment.paystack_reference == reference))
        payment = result.scalar_one_or_none()
        if not payment:
            return None

        transaction_result = await db.execute(
            select(Transaction).where(Transaction.provider_reference == reference).order_by(Transaction.created_at.desc())
        )
        transaction = transaction_result.scalars().first()

        if paystack_data["status"] == "success":
            payment.status = PaymentStatus.success
            prop = await db.get(Property, payment.property_id)
            if prop:
                prop.status = PropertyStatus.rented
            if payment.appointment_id:
                appointment = await db.get(Appointment, payment.appointment_id)
                if appointment:
                    appointment.status = AppointmentStatus.completed
            transaction_status = TransactionStatus.success
        else:
            payment.status = PaymentStatus.failed
            transaction_status = TransactionStatus.failed

        if transaction:
            transaction.status = transaction_status
            transaction.gateway_status = str(paystack_data.get("status"))
            transaction.gateway_response = paystack_data.get("gateway_response")
            transaction.verification_message = response_json.get("message")
            transaction.raw_payload = response_json
            transaction.verified_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(payment)
        return payment


payment_service = PaymentService()
