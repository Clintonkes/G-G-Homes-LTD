from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Property, PropertyStatus


class PropertyService:
    async def search(
        self,
        db: AsyncSession,
        neighbourhood: str | None = None,
        max_rent: float | None = None,
        property_type: str | None = None,
    ) -> list[Property]:
        stmt: Select = select(Property).where(
            Property.status == PropertyStatus.active,
            Property.is_verified.is_(True),
        )
        if neighbourhood:
            stmt = stmt.where(Property.neighbourhood.ilike(neighbourhood))
        if max_rent is not None:
            stmt = stmt.where(Property.annual_rent <= max_rent)
        if property_type:
            stmt = stmt.where(Property.property_type == property_type)
        result = await db.execute(stmt.order_by(Property.created_at.desc()))
        return list(result.scalars().all())


property_service = PropertyService()
