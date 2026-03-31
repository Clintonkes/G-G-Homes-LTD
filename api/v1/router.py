"""Top-level API router that groups and exposes all version 1 endpoint modules."""

from fastapi import APIRouter

from api.v1.endpoints import admin, appointments, payments, properties, users, webhook

router = APIRouter()
router.include_router(webhook.router, prefix="/webhook", tags=["webhook"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(properties.router, prefix="/properties", tags=["properties"])
router.include_router(appointments.router, prefix="/appointments", tags=["appointments"])
router.include_router(payments.router, prefix="/payments", tags=["payments"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
