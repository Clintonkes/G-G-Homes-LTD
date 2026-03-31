"""Database bootstrap logic that prepares initial application data such as the admin account."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.security import hash_password
from database.models import User, UserRole


async def init_db(db: AsyncSession) -> None:
    result = await db.execute(select(User).where(User.email == settings.ADMIN_EMAIL))
    admin = result.scalar_one_or_none()
    if admin:
        return

    admin = User(
        full_name="RentEase Admin",
        email=settings.ADMIN_EMAIL,
        phone_number="+2348000000000",
        hashed_password=hash_password(settings.ADMIN_PASSWORD),
        role=UserRole.admin,
        is_admin=True,
        id_verified=True,
        onboarding_complete=True,
    )
    db.add(admin)
    await db.commit()
