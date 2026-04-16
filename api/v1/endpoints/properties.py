"""Property endpoints for listing searches, property retrieval, creation, and admin verification."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_current_admin_user
from database.models import Appointment, Payment, PaymentStatus, Property, PropertyStatus
from database.schema import PropertyCreate, PropertyPaymentSummary, PropertyRead
from database.session import get_db
from services.property_service import property_service
from services.whatsapp_service import whatsapp
from utils.helpers import parse_naira_amount

router = APIRouter()


@router.get("/", response_model=list[PropertyRead])
async def list_properties(
    neighbourhood: str | None = None,
    max_rent: str | None = Query(None),
    property_type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[Property]:
    parsed_max_rent = None
    if max_rent not in {None, ""}:
        try:
            parsed_max_rent = parse_naira_amount(max_rent)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please provide max_rent as a valid amount such as 500000 or 500,000.") from exc
    return await property_service.search(db, neighbourhood=neighbourhood, max_rent=parsed_max_rent, property_type=property_type)


@router.get("/{property_id}", response_model=PropertyRead)
async def get_property(property_id: int, db: AsyncSession = Depends(get_db)) -> Property:
    prop = await db.get(Property, property_id)
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return prop


@router.post("/", response_model=PropertyRead, status_code=status.HTTP_201_CREATED)
async def create_property(payload: PropertyCreate, db: AsyncSession = Depends(get_db)) -> Property:
    prop = Property(**payload.model_dump(), status=PropertyStatus.pending_verification, is_verified=False)
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop


@router.post("/{property_id}/verify", response_model=PropertyRead)
async def verify_property(property_id: int, _admin=Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)) -> Property:
    prop = await db.get(Property, property_id)
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    prop.is_verified = True
    prop.status = PropertyStatus.active
    prop.verified_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(prop)
    landlord = prop.landlord
    if landlord and landlord.phone_number:
        name = (landlord.full_name or "Partner").strip() or "Partner"
        await whatsapp.send_text(
            landlord.phone_number,
            f"Congratulations {name}. Your property '{prop.title}' has been verified, listed successfully, and is now available for prospective tenants on G & G Homes Ltd.",
        )
    return prop


@router.get("/{property_id}/payments", response_model=PropertyPaymentSummary)
async def get_property_payments(property_id: int, db: AsyncSession = Depends(get_db)) -> PropertyPaymentSummary:
    prop = await db.get(Property, property_id)
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    result = await db.execute(
        select(Payment).where(Payment.property_id == property_id).order_by(Payment.created_at.desc())
    )
    payments = list(result.scalars().all())

    active_payments = []
    pending_payments = []
    ended_payments = []
    now = datetime.now(timezone.utc)
    for payment in payments:
        if payment.status == PaymentStatus.pending:
            pending_payments.append(payment)
            continue
        tenancy_end_date = payment.tenancy_end_date
        if tenancy_end_date and tenancy_end_date.tzinfo is None:
            tenancy_end_date = tenancy_end_date.replace(tzinfo=timezone.utc)
        if tenancy_end_date and tenancy_end_date >= now:
            active_payments.append(payment)
        else:
            ended_payments.append(payment)

    return PropertyPaymentSummary(
        property_id=property_id,
        total_payments=len(payments),
        active_payments=active_payments,
        pending_payments=pending_payments,
        ended_payments=ended_payments,
    )
