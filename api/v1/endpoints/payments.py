"""Payment endpoints for callbacks, remittance tracking, and webhook processing."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment, PaymentStatus
from database.schema import PaymentRead
from database.session import get_db
from services.payment_service import payment_service
from services.whatsapp_service import whatsapp

router = APIRouter()


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
    body = await request.json()
    event = body.get("event")
    reference = body.get("data", {}).get("reference")
    if event == "charge.success" and reference:
        payment = await payment_service.verify_payment(db, reference)
        if payment:
            await whatsapp.send_text("2348000000000", f"Payment {payment.paystack_reference} has been verified successfully.")
    return {"status": "ok"}
