"""Utility script for listing and verifying pending property submissions."""

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import Property, PropertyStatus
from database.session import AsyncSessionLocal
from services.whatsapp_service import whatsapp


async def list_pending() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Property).options(selectinload(Property.landlord)).where(Property.status == PropertyStatus.pending_verification)
        )
        properties = result.scalars().all()
        if not properties:
            print("No pending properties found.")
            return
        for prop in properties:
            landlord_name = prop.landlord.full_name if prop.landlord else "Unknown"
            print(f"ID={prop.id} | {prop.title} | {prop.address} | Landlord={landlord_name} | Docs={len(prop.document_urls or [])}")


async def verify_property(property_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Property).options(selectinload(Property.landlord)).where(Property.id == property_id))
        prop = result.scalar_one_or_none()
        if not prop:
            raise SystemExit(f"Property {property_id} was not found.")
        prop.is_verified = True
        prop.status = PropertyStatus.active
        prop.verified_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(prop)
        if prop.landlord and prop.landlord.phone_number:
            name = (prop.landlord.full_name or "Partner").strip() or "Partner"
            await whatsapp.send_text(
                prop.landlord.phone_number,
                f"Congratulations {name}. Your property '{prop.title}' has been verified, listed successfully, and is now available for prospective tenants on G & G Homes Ltd.",
            )
        print(f"Property {property_id} verified successfully.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="List or verify pending property submissions.")
    parser.add_argument("--property-id", type=int, help="Property ID to verify")
    parser.add_argument("--list", action="store_true", help="List all pending properties")
    args = parser.parse_args()

    if args.list or not args.property_id:
        await list_pending()
    if args.property_id:
        await verify_property(args.property_id)


if __name__ == "__main__":
    asyncio.run(main())
