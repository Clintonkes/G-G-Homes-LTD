"""Database models that define the platform's users, properties, appointments, payments, and subscriptions."""

import enum

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class UserRole(str, enum.Enum):
    """Defines user access roles in the platform."""

    tenant = "tenant"
    landlord = "landlord"
    both = "both"
    admin = "admin"


class PropertyType(str, enum.Enum):
    """Supported property categories for search and listing."""

    self_contain = "self_contain"
    room_and_parlour = "room_and_parlour"
    flat = "flat"
    duplex = "duplex"
    bungalow = "bungalow"
    office_space = "office_space"
    warehouse = "warehouse"


class PropertyStatus(str, enum.Enum):
    """Lifecycle states a property listing can pass through."""

    draft = "draft"
    pending = "pending"
    pending_verification = "pending_verification"
    active = "active"
    rented = "rented"
    inactive = "inactive"


class ListingType(str, enum.Enum):
    """Commercial visibility tiers for a listing."""

    standard = "standard"
    premium = "premium"
    gold = "gold"


class PaymentType(str, enum.Enum):
    """Business reasons for collecting a payment."""

    rent = "rent"
    subscription = "subscription"
    verification = "verification"
    premium_listing = "premium_listing"


class PaymentStatus(str, enum.Enum):
    """Transaction processing status for a payment record."""

    pending = "pending"
    success = "success"
    failed = "failed"
    refunded = "refunded"


class AppointmentStatus(str, enum.Enum):
    """Status values for inspection/visit appointments."""

    pending = "pending"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"
    interested = "interested"
    not_interested = "not_interested"


class SubscriptionPlan(str, enum.Enum):
    """Available subscription bundles for platform users."""

    basic = "basic"
    standard = "standard"
    annual = "annual"


class SubscriptionStatus(str, enum.Enum):
    """Current status of a user's subscription package."""

    active = "active"
    expired = "expired"
    cancelled = "cancelled"


class User(Base):
    """Stores tenant, landlord, and admin identity/account details."""

    __tablename__ = "users"

    # Primary identifier
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Personal/contact profile used for messaging and authentication
    full_name: Mapped[str] = mapped_column(String(200), default="WhatsApp User")
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    residential_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Access and onboarding/verification state
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.tenant)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    id_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    id_document_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Audit timestamp
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Entity relationships
    properties: Mapped[list["Property"]] = relationship(back_populates="landlord")
    appointments_as_tenant: Mapped[list["Appointment"]] = relationship(
        back_populates="tenant", foreign_keys="Appointment.tenant_id"
    )
    appointments_as_landlord: Mapped[list["Appointment"]] = relationship(
        back_populates="landlord", foreign_keys="Appointment.landlord_id"
    )


class Property(Base):
    """Holds property listing details, media, ownership proofs, and verification flags."""

    __tablename__ = "properties"

    # Listing and ownership identity
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    landlord_full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    landlord_phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Core listing description and location data
    title: Mapped[str] = mapped_column(String(300))
    address: Mapped[str] = mapped_column(String(500))
    neighbourhood: Mapped[str] = mapped_column(String(200), index=True)
    city: Mapped[str] = mapped_column(String(100), default="Abakaliki")
    state: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    # Property features and pricing attributes
    property_type: Mapped[PropertyType] = mapped_column(Enum(PropertyType))
    bedrooms: Mapped[int] = mapped_column(Integer, default=1)
    amenities: Mapped[list[str]] = mapped_column(JSON, default=list)
    has_water: Mapped[bool] = mapped_column(Boolean, default=False)
    has_electricity: Mapped[bool] = mapped_column(Boolean, default=False)
    annual_rent: Mapped[float] = mapped_column(Float)
    # Media and ownership evidence files
    photo_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    video_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    document_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    legal_representative_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_matches_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Moderation/verification lifecycle and listing tier
    status: Mapped[PropertyStatus] = mapped_column(Enum(PropertyStatus), default=PropertyStatus.draft)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    listing_type: Mapped[ListingType] = mapped_column(Enum(ListingType), default=ListingType.standard)
    # Audit timestamp
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Entity relationships
    landlord: Mapped["User"] = relationship(back_populates="properties")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="property")
    payments: Mapped[list["Payment"]] = relationship(back_populates="property")


class Appointment(Base):
    """Tracks tenant-to-landlord property inspection appointments."""

    __tablename__ = "appointments"

    # Primary identifier and ownership links
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Visit schedule and progress
    scheduled_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus), default=AppointmentStatus.confirmed)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_full_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tenant_phone_snapshot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tenant_address_snapshot: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Audit timestamp
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Entity relationships
    property: Mapped["Property"] = relationship(back_populates="appointments")
    tenant: Mapped["User"] = relationship(back_populates="appointments_as_tenant", foreign_keys=[tenant_id])
    landlord: Mapped["User"] = relationship(back_populates="appointments_as_landlord", foreign_keys=[landlord_id])


class Payment(Base):
    """Records financial transactions tied to rent, subscriptions, and listing services."""

    __tablename__ = "payments"

    # Primary identity and ownership links
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"), nullable=True)
    # Payment business context and amount breakdown
    payment_type: Mapped[PaymentType] = mapped_column(Enum(PaymentType))
    gross_amount: Mapped[float] = mapped_column(Float)
    platform_fee: Mapped[float] = mapped_column(Float)
    net_amount: Mapped[float] = mapped_column(Float)
    # Gateway and payout lifecycle tracking
    paystack_reference: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending)
    landlord_remitted: Mapped[bool] = mapped_column(Boolean, default=False)
    remitted_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Tenancy period details for rent-linked payments
    tenancy_start_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenancy_end_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Audit timestamp
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    property: Mapped["Property | None"] = relationship(back_populates="payments")


class Subscription(Base):
    """Stores recurring plan enrollment and expiry/payment metadata per user."""

    __tablename__ = "subscriptions"

    # Primary identity and owner link
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Plan economics and current lifecycle
    plan: Mapped[SubscriptionPlan] = mapped_column(Enum(SubscriptionPlan))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[SubscriptionStatus] = mapped_column(Enum(SubscriptionStatus), default=SubscriptionStatus.active)
    # Subscription period and payment reference
    start_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    end_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
