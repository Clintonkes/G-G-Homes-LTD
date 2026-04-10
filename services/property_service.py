"""Property querying service used to search and filter verified property listings."""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Property, PropertyStatus


class PropertyService:
    async def search(
        self,
        db: AsyncSession,
        state: str | None = None,
        location: str | None = None,
        neighbourhood: str | None = None,
        max_rent: float | None = None,
        property_type: str | None = None,
        bedrooms: int | None = None,
        min_bedrooms: int | None = None,
    ) -> list[Property]:
        stmt: Select = select(Property).where(
            Property.status == PropertyStatus.active,
            Property.is_verified.is_(True),
        )
        if state:
            stmt = stmt.where(Property.state.ilike(f"%{state.strip()}%"))
        search_term = location or neighbourhood
        if search_term:
            pattern = f"%{search_term.strip()}%"
            stmt = stmt.where(
                (Property.neighbourhood.ilike(pattern)) | (Property.city.ilike(pattern))
            )
        if max_rent is not None:
            stmt = stmt.where(Property.annual_rent <= max_rent)
        if property_type:
            stmt = stmt.where(Property.property_type == property_type)
        if bedrooms is not None:
            stmt = stmt.where(Property.bedrooms == bedrooms)
        if min_bedrooms is not None:
            stmt = stmt.where(Property.bedrooms >= min_bedrooms)
        result = await db.execute(stmt.order_by(Property.created_at.desc()))
        return list(result.scalars().all())


property_service = PropertyService()
