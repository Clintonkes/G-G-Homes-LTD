from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Appointment, AppointmentStatus, User
from database.schema import AppointmentCreate, AppointmentRead, AppointmentUpdate
from database.session import get_db
from services.payment_service import payment_service
from services.whatsapp_service import whatsapp

router = APIRouter()


@router.post("/", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
async def create_appointment(payload: AppointmentCreate, db: AsyncSession = Depends(get_db)) -> Appointment:
    appointment = Appointment(**payload.model_dump(), status=AppointmentStatus.confirmed)
    db.add(appointment)
    await db.commit()
    await db.refresh(appointment)
    return appointment


@router.get("/upcoming/today", response_model=list[AppointmentRead])
async def get_todays_appointments(db: AsyncSession = Depends(get_db)) -> list[Appointment]:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    result = await db.execute(select(Appointment).where(Appointment.scheduled_date >= start, Appointment.scheduled_date < end))
    return list(result.scalars().all())


@router.patch("/{appointment_id}", response_model=AppointmentRead)
async def update_appointment(appointment_id: int, payload: AppointmentUpdate, db: AsyncSession = Depends(get_db)) -> Appointment:
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if payload.status is not None:
        appointment.status = payload.status
    if payload.notes is not None:
        appointment.notes = payload.notes
    await db.commit()
    await db.refresh(appointment)

    if appointment.status == AppointmentStatus.interested:
        tenant = await db.get(User, appointment.tenant_id)
        if tenant:
            payment = await payment_service.initialize_rent_payment(db, tenant, appointment.property_id)
            await whatsapp.send_text(tenant.phone_number, f"Proceed with your payment here: {payment['payment_url']}")
    return appointment
