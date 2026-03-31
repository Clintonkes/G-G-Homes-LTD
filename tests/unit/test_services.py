"""Unit tests covering security helpers and selected service-layer behaviors."""

from datetime import timedelta

import pytest

from core.security import create_access_token, decode_access_token, hash_password, verify_password
from services.property_service import PropertyService


class TestPasswordHashing:
    def test_hash_password_returns_string(self):
        hashed = hash_password("mypassword123")
        assert isinstance(hashed, str) and len(hashed) > 10

    def test_verify_correct_password(self):
        plain = "mypassword123"
        assert verify_password(plain, hash_password(plain)) is True

    def test_reject_wrong_password(self):
        assert verify_password("wrong", hash_password("correct")) is False

    def test_two_hashes_differ(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2


class TestJWTTokens:
    def test_create_and_decode_token(self):
        token = create_access_token(subject="42")
        assert decode_access_token(token) == "42"

    def test_invalid_token_returns_none(self):
        assert decode_access_token("not.a.token") is None

    def test_expired_token_returns_none(self):
        token = create_access_token("99", expires_delta=timedelta(hours=-1))
        assert decode_access_token(token) is None


class TestPropertyService:
    @pytest.mark.asyncio
    async def test_search_returns_only_active(self, db, sample_property):
        results = await PropertyService().search(db=db)
        assert all(p.status.value == "active" and p.is_verified for p in results)

    @pytest.mark.asyncio
    async def test_search_by_neighbourhood_case_insensitive(self, db, sample_property):
        results = await PropertyService().search(db=db, neighbourhood="GRA")
        assert any(p.id == sample_property.id for p in results)

    @pytest.mark.asyncio
    async def test_search_filters_by_max_rent(self, db, sample_property):
        included = await PropertyService().search(db=db, max_rent=300000)
        excluded = await PropertyService().search(db=db, max_rent=100000)
        assert sample_property.id in [p.id for p in included]
        assert sample_property.id not in [p.id for p in excluded]
