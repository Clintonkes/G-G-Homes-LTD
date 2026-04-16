"""Tests for Paystack payment webhook verification helpers."""

import hashlib
import hmac

from api.v1.endpoints.payments import _is_valid_paystack_signature
from core.config import settings


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
