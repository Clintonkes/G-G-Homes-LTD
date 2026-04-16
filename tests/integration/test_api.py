"""Integration tests covering API authentication, property, and webhook behavior."""

from datetime import datetime, timedelta, timezone

import pytest

from core.config import settings
from database.models import Appointment, AppointmentStatus, Payment, PaymentStatus, PaymentType, Property, PropertyStatus, PropertyType, User, UserRole


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

    @pytest.mark.asyncio
    async def test_property_payments_summary_groups_records(self, client, db, sample_property):
        tenant = User(full_name="Buyer", phone_number="2348012222222", role=UserRole.tenant)
        db.add(tenant)
        await db.flush()

        appointment_active = Appointment(
            property_id=sample_property.id,
            tenant_id=tenant.id,
            landlord_id=sample_property.landlord_id,
            scheduled_date=datetime.now(timezone.utc),
            status=AppointmentStatus.interested,
            original_rent_amount=250000.0,
            agreed_rent_amount=220000.0,
        )
        appointment_ended = Appointment(
            property_id=sample_property.id,
            tenant_id=tenant.id,
            landlord_id=sample_property.landlord_id,
            scheduled_date=datetime.now(timezone.utc) - timedelta(days=30),
            status=AppointmentStatus.completed,
            original_rent_amount=250000.0,
            agreed_rent_amount=210000.0,
        )
        db.add_all([appointment_active, appointment_ended])
        await db.flush()

        db.add_all(
            [
                Payment(
                    payer_id=tenant.id,
                    property_id=sample_property.id,
                    appointment_id=appointment_active.id,
                    payment_type=PaymentType.rent,
                    quoted_amount=250000.0,
                    agreed_amount=220000.0,
                    gross_amount=220000.0,
                    platform_fee=8800.0,
                    net_amount=211200.0,
                    paystack_reference="ref-active",
                    checkout_url="https://checkout.example/active",
                    status=PaymentStatus.success,
                    tenancy_start_date=datetime.now(timezone.utc) - timedelta(days=20),
                    tenancy_end_date=datetime.now(timezone.utc) + timedelta(days=340),
                ),
                Payment(
                    payer_id=tenant.id,
                    property_id=sample_property.id,
                    appointment_id=appointment_ended.id,
                    payment_type=PaymentType.rent,
                    quoted_amount=250000.0,
                    agreed_amount=210000.0,
                    gross_amount=210000.0,
                    platform_fee=8400.0,
                    net_amount=201600.0,
                    paystack_reference="ref-ended",
                    checkout_url="https://checkout.example/ended",
                    status=PaymentStatus.success,
                    tenancy_start_date=datetime.now(timezone.utc) - timedelta(days=400),
                    tenancy_end_date=datetime.now(timezone.utc) - timedelta(days=35),
                ),
                Payment(
                    payer_id=tenant.id,
                    property_id=sample_property.id,
                    payment_type=PaymentType.rent,
                    quoted_amount=250000.0,
                    agreed_amount=200000.0,
                    gross_amount=200000.0,
                    platform_fee=8000.0,
                    net_amount=192000.0,
                    paystack_reference="ref-pending",
                    checkout_url="https://checkout.example/pending",
                    status=PaymentStatus.pending,
                ),
            ]
        )
        await db.commit()

        response = await client.get(f"/api/v1/properties/{sample_property.id}/payments")
        assert response.status_code == 200
        payload = response.json()
        assert payload["property_id"] == sample_property.id
        assert payload["total_payments"] == 3
        assert len(payload["active_payments"]) == 1
        assert len(payload["pending_payments"]) == 1
        assert len(payload["ended_payments"]) == 1


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
