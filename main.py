from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.router import router as api_router
from core.config import settings
from database.base import Base
from database.init_db import init_db
from database.session import AsyncSessionLocal, engine
from services.notification_service import notification_service
from utils.scheduler import start_scheduler, stop_scheduler


async def run_rent_reminders() -> None:
    async with AsyncSessionLocal() as db:
        await notification_service.send_rent_renewal_reminders(db)


async def run_subscription_expiry_check() -> None:
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await init_db(db)
    start_scheduler(run_rent_reminders, run_subscription_expiry_check)
    yield
    stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    description="WhatsApp-native rental platform for Ebonyi State.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", settings.BASE_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")
