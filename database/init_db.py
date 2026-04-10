"""Database bootstrap logic that prepares initial application data such as the admin account."""

import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.security import hash_password
from database.models import User, UserRole

logger = logging.getLogger(__name__)


def _run_alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    alembic_cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    script = ScriptDirectory.from_config(alembic_cfg)
    heads = ", ".join(script.get_heads())
    logger.warning("Alembic script heads: %s", heads)
    print(f"Alembic script heads: {heads}")

    sync_url = make_url(settings.DATABASE_URL)
    if sync_url.drivername.endswith("+asyncpg"):
        sync_url = sync_url.set(drivername=sync_url.drivername.replace("+asyncpg", ""))
    engine = create_engine(sync_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        before_rev = context.get_current_revision()
        logger.warning("Alembic current revision before upgrade: %s", before_rev)
        print(f"Alembic current revision before upgrade: {before_rev}")
    command.upgrade(alembic_cfg, "head")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        after_rev = context.get_current_revision()
        logger.warning("Alembic current revision after upgrade: %s", after_rev)
        print(f"Alembic current revision after upgrade: {after_rev}")


async def init_db(db: AsyncSession) -> None:
    deploy_ref = (
        os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or os.getenv("RAILWAY_DEPLOYMENT_ID")
        or os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("COMMIT_SHA")
        or "unknown"
    )
    logger.warning("Boot deploy reference: %s", deploy_ref)
    print(f"Boot deploy reference: {deploy_ref}")

    if settings.AUTO_MIGRATE_ON_STARTUP:
        lock_key = 914201
        got_lock = False
        try:
            result = await db.execute(text("select pg_try_advisory_lock(:key)"), {"key": lock_key})
            got_lock = bool(result.scalar())
            if got_lock:
                await asyncio.to_thread(_run_alembic_upgrade)
            else:
                logger.info("Automatic migration skipped; another process holds advisory lock.")
        except Exception:
            logger.exception("Automatic migration failed on startup.")
            raise
        finally:
            if got_lock:
                await db.execute(text("select pg_advisory_unlock(:key)"), {"key": lock_key})

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
