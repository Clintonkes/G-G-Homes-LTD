"""Tests for Paystack payment webhook verification helpers."""

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.v1.endpoints.payments import _is_valid_paystack_signature
from core.config import settings
from database.models import Transaction


def test_paystack_signature_validation_accepts_valid_signature():
    body = b'{"event":"charge.success","data":{"reference":"test_ref"}}'
    signature = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        body,
        hashlib.sha512,
    ).hexdigest()

    assert _is_valid_paystack_signature(body, signature) is True


def test_paystack_signature_validation_rejects_invalid_signature():
    body = b'{"event":"charge.success","data":{"reference":"test_ref"}}'
    assert _is_valid_paystack_signature(body, "invalid-signature") is False


@pytest.mark.asyncio
async def test_webhook_notifies_user_for_failed_payment(client, monkeypatch):
    payload = b'{"event":"charge.failed","data":{"reference":"failed_ref"}}'
    signature = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        payload,
        hashlib.sha512,
    ).hexdigest()

    fake_payment = SimpleNamespace(
        payer_id=1,
        property_id=1,
        paystack_reference="failed_ref",
        status="failed",
    )
    fake_user = SimpleNamespace(phone_number="2348012345678")
    fake_property = SimpleNamespace(title="Palm View Apartment")

    monkeypatch.setattr("api.v1.endpoints.payments.payment_service.verify_payment", AsyncMock(return_value=fake_payment))
    send_text = AsyncMock(return_value=True)
    monkeypatch.setattr("api.v1.endpoints.payments.whatsapp.send_text", send_text)

    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.session.AsyncSession.get",
        AsyncMock(side_effect=[fake_user, fake_property]),
    )

    response = await client.post(
        "/api/v1/payments/webhook",
        content=payload,
        headers={"x-paystack-signature": signature, "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert send_text.await_count == 1
    assert "could not verify your payment" in send_text.await_args.args[1].lower()


@pytest.mark.asyncio
async def test_list_transactions_returns_records(client, db):
    from database.models import Payment, PaymentStatus, PaymentType, TransactionStatus, User

    user = User(full_name="Buyer", phone_number="2348011111999")
    db.add(user)
    await db.flush()
    payment = Payment(
        payer_id=user.id,
        payment_type=PaymentType.rent,
        gross_amount=250000.0,
        platform_fee=10000.0,
        net_amount=240000.0,
        paystack_reference="tx-ref-1",
        status=PaymentStatus.pending,
    )
    db.add(payment)
    await db.flush()
    db.add(
        Transaction(
            payment_id=payment.id,
            provider="paystack",
            provider_reference="tx-ref-1",
            status=TransactionStatus.pending,
            amount=250000.0,
            currency="NGN",
            gateway_status="True",
            gateway_response="Authorization URL created",
        )
    )
    await db.commit()

    response = await client.get("/api/v1/payments/transactions")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["provider_reference"] == "tx-ref-1"
