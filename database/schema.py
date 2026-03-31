"""Pydantic schemas used to validate API requests and structure API responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from database.models import (
    AppointmentStatus,
    ListingType,
    PaymentStatus,
    PaymentType,
    PropertyStatus,
    PropertyType,
    SubscriptionPlan,
    SubscriptionStatus,
    UserRole,
)


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    full_name: str
    phone_number: str
    email: EmailStr | None = None
    password: str | None = None
    role: UserRole = UserRole.tenant


class UserRead(ORMBase):
    id: int
    full_name: str
    phone_number: str
    email: EmailStr | None = None
    role: UserRole
    is_active: bool
    is_admin: bool
    onboarding_complete: bool
    id_verified: bool
    created_at: datetime | None = None


class PropertyCreate(BaseModel):
    landlord_id: int
    title: str
    address: str
    neighbourhood: str
    city: str = "Abakaliki"
    property_type: PropertyType
    bedrooms: int = 1
    bathrooms: int = 1
    amenities: list[str] = Field(default_factory=list)
    has_water: bool = False
    has_electricity: bool = False
    annual_rent: float
    photo_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    thumbnail_url: str | None = None
    listing_type: ListingType = ListingType.standard


class PropertyRead(ORMBase):
    id: int
    landlord_id: int
    title: str
    address: str
    neighbourhood: str
    city: str
    property_type: PropertyType
    bedrooms: int
    bathrooms: int
    amenities: list[str]
    has_water: bool
    has_electricity: bool
    annual_rent: float
    photo_urls: list[str]
    video_urls: list[str]
    thumbnail_url: str | None = None
    status: PropertyStatus
    is_verified: bool
    listing_type: ListingType


class AppointmentCreate(BaseModel):
    property_id: int
    tenant_id: int
    landlord_id: int
    scheduled_date: datetime
    notes: str | None = None


class AppointmentUpdate(BaseModel):
    status: AppointmentStatus | None = None
    notes: str | None = None


class AppointmentRead(ORMBase):
    id: int
    property_id: int
    tenant_id: int
    landlord_id: int
    scheduled_date: datetime
    status: AppointmentStatus
    notes: str | None = None


class PaymentRead(ORMBase):
    id: int
    payer_id: int
    property_id: int | None = None
    payment_type: PaymentType
    gross_amount: float
    platform_fee: float
    net_amount: float
    paystack_reference: str
    status: PaymentStatus
    landlord_remitted: bool


class SubscriptionRead(ORMBase):
    id: int
    user_id: int
    plan: SubscriptionPlan
    amount: float
    status: SubscriptionStatus


class AdminDashboardStats(BaseModel):
    total_users: int = 0
    total_properties: int = 0
    active_listings: int = 0
    total_transactions: int = 0
    total_revenue_naira: float = 0
    pending_verifications: int = 0
    upcoming_appointments: int = 0
    pending_remittances: int = 0


class WhatsAppWebhookEnvelope(BaseModel):
    entry: list[dict[str, Any]] = Field(default_factory=list)
