"""Utility script for seeding local development data into the application database."""

import asyncio

from database.base import Base
from database.init_db import init_db
from database.models import Property, PropertyStatus, PropertyType, User, UserRole
from database.session import AsyncSessionLocal, engine


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await init_db(db)
        landlord = User(full_name="Sample Landlord", phone_number="2348011111111", role=UserRole.landlord)
        db.add(landlord)
        await db.flush()
        for index in range(1, 4):
            db.add(
                Property(
                    landlord_id=landlord.id,
                    title=f"Sample Flat {index}",
                    address=f"{index} GRA Avenue",
                    neighbourhood="GRA",
                    property_type=PropertyType.flat,
                    bedrooms=3,
                    annual_rent=250000.0,
                    amenities=["PHCN", "Borehole", "Parking"],
                    photo_urls=["https://example.com/photo.jpg"],
                    status=PropertyStatus.active,
                    is_verified=True,
                )
            )
        await db.commit()
    print("SEEDING COMPLETE! Admin: admin@rentease.ng")


if __name__ == "__main__":
    asyncio.run(seed())
