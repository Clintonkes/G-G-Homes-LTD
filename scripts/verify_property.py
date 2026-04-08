"""Admin workflow script for reviewing, approving, and rejecting property submissions."""

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import Property, PropertyStatus
from database.session import AsyncSessionLocal
from services.whatsapp_service import whatsapp


# ---------- Query helpers ----------
def _base_property_query():
    return select(Property).options(selectinload(Property.landlord)).order_by(Property.created_at.desc())


def _property_status_label(status: PropertyStatus) -> str:
    return status.value.replace("_", " ").title()


def _format_listing_line(prop: Property) -> str:
    landlord_name = prop.landlord.full_name if prop.landlord else "Unknown"
    docs_count = len(prop.document_urls or [])
    photos_count = len(prop.photo_urls or [])
    videos_count = len(prop.video_urls or [])
    return (
        f"ID={prop.id} | Status={_property_status_label(prop.status)} | {prop.title} | "
        f"{prop.address} | Landlord={landlord_name} | Photos={photos_count} | Videos={videos_count} | Docs={docs_count}"
    )


# ---------- Review commands ----------
async def list_properties(status: PropertyStatus | None, limit: int) -> None:
    async with AsyncSessionLocal() as db:
        query = _base_property_query().limit(limit)
        if status is not None:
            query = query.where(Property.status == status)
        result = await db.execute(query)
        properties = result.scalars().all()
        if not properties:
            print("No properties found for this filter.")
            return
        for prop in properties:
            print(_format_listing_line(prop))


async def show_property(property_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(_base_property_query().where(Property.id == property_id))
        prop = result.scalar_one_or_none()
        if not prop:
            raise SystemExit(f"Property {property_id} was not found.")

        landlord_name = prop.landlord.full_name if prop.landlord else "Unknown"
        landlord_phone = prop.landlord.phone_number if prop.landlord else "N/A"
        print(f"Property ID: {prop.id}")
        print(f"Status: {_property_status_label(prop.status)}")
        print(f"Verified: {prop.is_verified}")
        print(f"Title: {prop.title}")
        print(f"Address: {prop.address}")
        print(f"Neighbourhood: {prop.neighbourhood}")
        print(f"Type: {prop.property_type.value}")
        print(f"Bedrooms: {prop.bedrooms}")
        print(f"Annual Rent: {prop.annual_rent}")
        print(f"Landlord: {landlord_name} ({landlord_phone})")
        print(f"Legal Representative Phone: {prop.legal_representative_phone or 'N/A'}")
        print(f"Photos: {len(prop.photo_urls or [])}")
        print(f"Videos: {len(prop.video_urls or [])}")
        print(f"Documents: {len(prop.document_urls or [])}")
        print(f"Created At: {prop.created_at}")
        print(f"Verified At: {prop.verified_at or 'N/A'}")


async def approve_property(property_id: int, notify: bool) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(_base_property_query().where(Property.id == property_id))
        prop = result.scalar_one_or_none()
        if not prop:
            raise SystemExit(f"Property {property_id} was not found.")

        prop.is_verified = True
        prop.status = PropertyStatus.active
        prop.verified_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(prop)

        if notify and prop.landlord and prop.landlord.phone_number:
            name = (prop.landlord.full_name or "Partner").strip() or "Partner"
            await whatsapp.send_text(
                prop.landlord.phone_number,
                f"Congratulations {name}. Your property '{prop.title}' has been verified and is now live on G & G Homes Ltd.",
            )
        print(f"Property {property_id} approved and moved to active.")


async def reject_property(property_id: int, reason: str, notify: bool) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(_base_property_query().where(Property.id == property_id))
        prop = result.scalar_one_or_none()
        if not prop:
            raise SystemExit(f"Property {property_id} was not found.")

        prop.is_verified = False
        prop.status = PropertyStatus.inactive
        await db.commit()
        await db.refresh(prop)

        if notify and prop.landlord and prop.landlord.phone_number:
            name = (prop.landlord.full_name or "Partner").strip() or "Partner"
            await whatsapp.send_text(
                prop.landlord.phone_number,
                (
                    f"Hello {name}. We reviewed your property '{prop.title}' and it could not be approved yet. "
                    f"Reason: {reason}. Please update the listing and submit again."
                ),
            )
        print(f"Property {property_id} rejected and moved to inactive.")


# ---------- CLI wiring ----------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review property submissions and approve or reject them for public listing."
    )
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List properties")
    list_parser.add_argument(
        "--status",
        choices=[status.value for status in PropertyStatus],
        default=PropertyStatus.pending_verification.value,
        help="Filter by status (default: pending_verification).",
    )
    list_parser.add_argument("--limit", type=int, default=100, help="Maximum number of records to print.")

    show_parser = subparsers.add_parser("show", help="Show full details for one property")
    show_parser.add_argument("--property-id", type=int, required=True, help="Property ID to inspect.")

    approve_parser = subparsers.add_parser("approve", help="Approve a property and make it active")
    approve_parser.add_argument("--property-id", type=int, required=True, help="Property ID to approve.")
    approve_parser.add_argument("--no-notify", action="store_true", help="Skip WhatsApp notification to landlord.")

    reject_parser = subparsers.add_parser("reject", help="Reject a property and mark it inactive")
    reject_parser.add_argument("--property-id", type=int, required=True, help="Property ID to reject.")
    reject_parser.add_argument("--reason", required=True, help="Why the listing is rejected.")
    reject_parser.add_argument("--no-notify", action="store_true", help="Skip WhatsApp notification to landlord.")

    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    if args.command in {None, "list"}:
        status = PropertyStatus(args.status)
        await list_properties(status=status, limit=args.limit)
        return
    if args.command == "show":
        await show_property(property_id=args.property_id)
        return
    if args.command == "approve":
        await approve_property(property_id=args.property_id, notify=not args.no_notify)
        return
    if args.command == "reject":
        await reject_property(property_id=args.property_id, reason=args.reason, notify=not args.no_notify)
        return

    raise SystemExit("Unknown command. Use list, show, approve, or reject.")


if __name__ == "__main__":
    asyncio.run(main())
