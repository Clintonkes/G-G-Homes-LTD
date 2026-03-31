"""Administrative endpoints for platform health checks, dashboard reporting, and manual operations."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_current_admin_user
from database.models import Appointment, Payment, PaymentStatus, Property, PropertyStatus, User
from database.schema import AdminDashboardStats
from database.session import AsyncSessionLocal, get_db
from services.notification_service import notification_service

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    return {"status": "healthy"}


@router.get("/dashboard", response_model=AdminDashboardStats)
async def get_dashboard_stats(_current_admin=Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)) -> AdminDashboardStats:
    now = datetime.now(timezone.utc)
    next_week = now + timedelta(days=7)
    return AdminDashboardStats(
        total_users=await db.scalar(select(func.count(User.id))) or 0,
        total_properties=await db.scalar(select(func.count(Property.id))) or 0,
        active_listings=await db.scalar(select(func.count(Property.id)).where(Property.status == PropertyStatus.active)) or 0,
        total_transactions=await db.scalar(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.success)) or 0,
        total_revenue_naira=await db.scalar(select(func.coalesce(func.sum(Payment.platform_fee), 0)).where(Payment.status == PaymentStatus.success)) or 0,
        pending_verifications=await db.scalar(select(func.count(Property.id)).where(Property.status == PropertyStatus.pending_verification)) or 0,
        upcoming_appointments=await db.scalar(select(func.count(Appointment.id)).where(Appointment.scheduled_date >= now, Appointment.scheduled_date <= next_week)) or 0,
        pending_remittances=await db.scalar(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.success, Payment.landlord_remitted.is_(False))) or 0,
    )


@router.post("/trigger-reminders")
async def trigger_reminders(_current_admin=Depends(get_current_admin_user)) -> dict:
    async with AsyncSessionLocal() as db:
        sent = await notification_service.send_rent_renewal_reminders(db)
    return {"sent": sent}
