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

    @pytest.mark.asyncio
    async def test_webhook_batches_related_media_from_same_payload(self, client, monkeypatch):
        calls = []

        async def fake_get_redis():
            return object()

        async def fake_process_message(self, **kwargs):
            calls.append(kwargs)

        monkeypatch.setattr("api.v1.endpoints.webhook.get_redis", fake_get_redis)
        monkeypatch.setattr("api.v1.endpoints.webhook.ChatbotEngine.process_message", fake_process_message)

        response = await client.post(
            "/api/v1/webhook/whatsapp",
            json={
                "entry": [{
                    "changes": [{
                        "value": {
                            "messages": [
                                {
                                    "from": "2348012345678",
                                    "id": "wamid.image-1",
                                    "type": "image",
                                    "image": {"id": "media-1"},
                                },
                                {
                                    "from": "2348012345678",
                                    "id": "wamid.text-1",
                                    "type": "text",
                                    "text": {"body": "caption filler"},
                                },
                                {
                                    "from": "2348012345678",
                                    "id": "wamid.image-2",
                                    "type": "image",
                                    "context": {"id": "wamid.image-1"},
                                    "image": {"id": "media-2"},
                                },
                            ],
                        },
                    }],
                }],
            },
        )

        assert response.status_code == 200
        assert len(calls) == 2
        assert calls[0]["message_type"] == "image"
        assert calls[0]["message_ids"] == ["wamid.image-1", "wamid.image-2"]
        assert calls[0]["media_items"] == [
            {"type": "image", "id": "media-1"},
            {"type": "image", "id": "media-2"},
        ]
        assert calls[1]["message_type"] == "text"
