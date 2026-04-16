"""Payment endpoints for callbacks, remittance tracking, and webhook processing."""

import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.models import Payment, PaymentStatus
from database.models import User
from database.schema import PaymentRead
from database.session import get_db
from services.payment_service import payment_service
from services.whatsapp_service import whatsapp

router = APIRouter()


def _is_valid_paystack_signature(body: bytes, signature: str | None) -> bool:
    if not signature or not settings.PAYSTACK_SECRET_KEY:
        return False
    expected = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.get("/pending-remittances", response_model=list[PaymentRead])
async def get_pending_remittances(db: AsyncSession = Depends(get_db)) -> list[Payment]:
    result = await db.execute(select(Payment).where(Payment.status == PaymentStatus.success, Payment.landlord_remitted.is_(False)))
    return list(result.scalars().all())


@router.post("/{payment_id}/mark-remitted", response_model=PaymentRead)
async def mark_remitted(payment_id: int, db: AsyncSession = Depends(get_db)) -> Payment:
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment.landlord_remitted = True
    payment.remitted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(payment)
    return payment


@router.get("/callback")
async def payment_callback(reference: str) -> dict:
    return {"status": "ok", "reference": reference}


@router.post("/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    body = await request.body()
    signature = request.headers.get("x-paystack-signature")
    if not _is_valid_paystack_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid Paystack signature")

    payload = await request.json()
    event = payload.get("event")
    reference = payload.get("data", {}).get("reference")
    if event == "charge.success" and reference:
        payment = await payment_service.verify_payment(db, reference)
        if payment:
            tenant = await db.get(User, payment.payer_id)
            if tenant:
                await whatsapp.send_text(
                    tenant.phone_number,
                    f"Your payment for reference {payment.paystack_reference} has been verified successfully. Thank you for choosing G & G Homes Ltd.",
                )
    return {"status": "ok"}
