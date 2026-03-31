"""Integration tests covering API authentication, property, and webhook behavior."""

import pytest

from core.config import settings


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_login_valid_credentials(self, client, sample_admin):
        response = await client.post(
            "/api/v1/users/login",
            json={"email": "admin@test.com", "password": "TestPassword123!"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self, client, sample_admin):
        response = await client.post(
            "/api/v1/users/login",
            json={"email": "admin@test.com", "password": "WrongPass"},
        )
        assert response.status_code == 401


class TestPropertyEndpoints:
    @pytest.mark.asyncio
    async def test_search_properties_public(self, client, sample_property):
        response = await client.get("/api/v1/properties/")
        assert response.status_code == 200
        assert len(response.json()) >= 1

    @pytest.mark.asyncio
    async def test_nonexistent_property_404(self, client):
        response = await client.get("/api/v1/properties/99999")
        assert response.status_code == 404


class TestWebhookEndpoints:
    @pytest.mark.asyncio
    async def test_meta_webhook_verification(self, client):
        response = await client.get(
            "/api/v1/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
                "hub.challenge": "123456789",
            },
        )
        assert response.status_code == 200
        assert response.json() == 123456789

    @pytest.mark.asyncio
    async def test_webhook_post_always_200(self, client):
        response = await client.post("/api/v1/webhook/whatsapp", json={})
        assert response.status_code == 200
