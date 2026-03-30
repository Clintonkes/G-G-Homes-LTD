import asyncio
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.base import Base
from database.models import Property, PropertyStatus, PropertyType, User, UserRole
from database.session import get_db
from main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()


@pytest.fixture()
async def sample_admin(db: AsyncSession) -> User:
    from core.security import hash_password

    admin = User(
        full_name="Admin User",
        email="admin@test.com",
        phone_number="2348099999999",
        hashed_password=hash_password("TestPassword123!"),
        role=UserRole.admin,
        is_admin=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return admin


@pytest.fixture()
async def sample_property(db: AsyncSession) -> Property:
    landlord = User(full_name="Landlord", phone_number="2348088888888", role=UserRole.landlord)
    db.add(landlord)
    await db.flush()
    prop = Property(
        landlord_id=landlord.id,
        title="3 Bedroom Flat",
        address="12 Okpara Avenue",
        neighbourhood="GRA",
        property_type=PropertyType.flat,
        bedrooms=3,
        annual_rent=250000,
        amenities=["PHCN"],
        status=PropertyStatus.active,
        is_verified=True,
        photo_urls=["https://example.com/photo.jpg"],
    )
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop
