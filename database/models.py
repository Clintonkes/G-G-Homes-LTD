"""Database models that define the platform's users, properties, appointments, payments, and subscriptions."""

import enum

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class UserRole(str, enum.Enum):
    tenant = "tenant"
    landlord = "landlord"
    both = "both"
    admin = "admin"


class PropertyType(str, enum.Enum):
    self_contain = "self_contain"
    room_and_parlour = "room_and_parlour"
    flat = "flat"
    duplex = "duplex"
    bungalow = "bungalow"


class PropertyStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    pending_verification = "pending_verification"
    active = "active"
    rented = "rented"
    inactive = "inactive"


class ListingType(str, enum.Enum):
    standard = "standard"
    premium = "premium"
    gold = "gold"


class PaymentType(str, enum.Enum):
    rent = "rent"
    subscription = "subscription"
    verification = "verification"
    premium_listing = "premium_listing"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    refunded = "refunded"


class AppointmentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"
    interested = "interested"
    not_interested = "not_interested"


class SubscriptionPlan(str, enum.Enum):
    basic = "basic"
    standard = "standard"
    annual = "annual"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    cancelled = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), default="WhatsApp User")
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.tenant)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    id_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    id_document_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    properties: Mapped[list["Property"]] = relationship(back_populates="landlord")
    appointments_as_tenant: Mapped[list["Appointment"]] = relationship(
        back_populates="tenant", foreign_keys="Appointment.tenant_id"
    )
    appointments_as_landlord: Mapped[list["Appointment"]] = relationship(
        back_populates="landlord", foreign_keys="Appointment.landlord_id"
    )


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    landlord_full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    landlord_phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    address: Mapped[str] = mapped_column(String(500))
    neighbourhood: Mapped[str] = mapped_column(String(200), index=True)
    city: Mapped[str] = mapped_column(String(100), default="Abakaliki")
    state: Mapped[str] = mapped_column(String(100), default="Ebonyi")
    property_type: Mapped[PropertyType] = mapped_column(Enum(PropertyType))
    bedrooms: Mapped[int] = mapped_column(Integer, default=1)
    bathrooms: Mapped[int] = mapped_column(Integer, default=1)
    amenities: Mapped[list[str]] = mapped_column(JSON, default=list)
    has_water: Mapped[bool] = mapped_column(Boolean, default=False)
    has_electricity: Mapped[bool] = mapped_column(Boolean, default=False)
    annual_rent: Mapped[float] = mapped_column(Float)
    photo_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    video_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    document_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    legal_representative_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_matches_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[PropertyStatus] = mapped_column(Enum(PropertyStatus), default=PropertyStatus.draft)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    listing_type: Mapped[ListingType] = mapped_column(Enum(ListingType), default=ListingType.standard)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    landlord: Mapped["User"] = relationship(back_populates="properties")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="property")
    payments: Mapped[list["Payment"]] = relationship(back_populates="property")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scheduled_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus), default=AppointmentStatus.confirmed)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    property: Mapped["Property"] = relationship(back_populates="appointments")
    tenant: Mapped["User"] = relationship(back_populates="appointments_as_tenant", foreign_keys=[tenant_id])
    landlord: Mapped["User"] = relationship(back_populates="appointments_as_landlord", foreign_keys=[landlord_id])


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"), nullable=True)
    payment_type: Mapped[PaymentType] = mapped_column(Enum(PaymentType))
    gross_amount: Mapped[float] = mapped_column(Float)
    platform_fee: Mapped[float] = mapped_column(Float)
    net_amount: Mapped[float] = mapped_column(Float)
    paystack_reference: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending)
    landlord_remitted: Mapped[bool] = mapped_column(Boolean, default=False)
    remitted_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenancy_start_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenancy_end_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    property: Mapped["Property | None"] = relationship(back_populates="payments")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[SubscriptionPlan] = mapped_column(Enum(SubscriptionPlan))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[SubscriptionStatus] = mapped_column(Enum(SubscriptionStatus), default=SubscriptionStatus.active)
    start_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    end_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
