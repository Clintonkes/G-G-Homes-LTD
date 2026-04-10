"""Database bootstrap logic that prepares initial application data such as the admin account."""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.security import hash_password
from database.models import User, UserRole

logger = logging.getLogger(__name__)


def _run_alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    command.upgrade(alembic_cfg, "head")


async def init_db(db: AsyncSession) -> None:
    if settings.AUTO_MIGRATE_ON_STARTUP:
        try:
            await asyncio.to_thread(_run_alembic_upgrade)
        except Exception:
            logger.exception("Automatic migration failed on startup.")
            raise

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
