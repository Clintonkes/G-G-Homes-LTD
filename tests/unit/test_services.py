"""Unit tests covering security helpers and selected service-layer behaviors."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from core.security import create_access_token, decode_access_token, hash_password, verify_password
from services.chatbot_engine import ChatbotEngine
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


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


class TestChatbotMediaBatching:
    @pytest.mark.asyncio
    async def test_handle_list_photos_counts_batched_media_before_reply(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        await engine.set_state(phone, "LIST_PHOTOS")
        await engine.set_data(phone, {"photo_urls": [], "video_urls": []})

        intent_decision = type("IntentDecision", (), {"intent": "unknown"})()
        monkeypatch.setattr("services.chatbot_engine.intent_service.detect_intent", AsyncMock(return_value=intent_decision))
        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        monkeypatch.setattr("services.chatbot_engine.whatsapp.get_media_url", AsyncMock(side_effect=["url-1", "url-2", "url-3"]))
        monkeypatch.setattr("services.chatbot_engine.whatsapp.download_media", AsyncMock(side_effect=[b"a", b"b", b"c"]))
        monkeypatch.setattr("services.chatbot_engine.asyncio.sleep", AsyncMock(return_value=None))
        send_text = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)
        monkeypatch.setattr(
            "services.chatbot_engine.media_service.upload",
            AsyncMock(side_effect=["https://cdn.example/1.jpg", "https://cdn.example/2.jpg", "https://cdn.example/3.mp4"]),
        )

        await engine.process_message(
            phone=phone,
            message_type="image",
            text=None,
            button_id=None,
            media_id="media-1",
            media_items=[
                {"type": "image", "id": "media-1"},
                {"type": "image", "id": "media-2"},
                {"type": "video", "id": "media-3"},
            ],
            message_id="msg-1",
            message_ids=["msg-1", "msg-2", "msg-3"],
            db=db,
        )

        saved_data = await engine.get_data(phone)
        assert len(saved_data["photo_urls"]) == 2
        assert len(saved_data["video_urls"]) == 1
        assert send_text.await_count == 1
        assert "Please send a property image or video" not in send_text.await_args.args[1]
        assert "That brings us to 3 media files" in send_text.await_args.args[1]

    @pytest.mark.asyncio
    async def test_media_batch_token_only_latest_request_should_reply(self, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"
        monkeypatch.setattr("services.chatbot_engine.asyncio.sleep", AsyncMock(return_value=None))

        first = await engine._register_media_batch(phone, "LIST_PHOTOS")
        second = await engine._register_media_batch(phone, "LIST_PHOTOS")

        assert await engine._await_media_quiet_period(phone, "LIST_PHOTOS", first) is False
        assert await engine._await_media_quiet_period(phone, "LIST_PHOTOS", second) is True

    @pytest.mark.asyncio
    async def test_unexpected_document_is_rejected_before_upload(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        await engine.set_state(phone, "LIST_TITLE")
        await engine.set_data(phone, {})

        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        send_text = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)
        get_media_url = AsyncMock(return_value="url")
        monkeypatch.setattr("services.chatbot_engine.whatsapp.get_media_url", get_media_url)
        upload = AsyncMock(return_value="saved")
        monkeypatch.setattr("services.chatbot_engine.media_service.upload", upload)

        await engine.process_message(
            phone=phone,
            message_type="document",
            text=None,
            button_id=None,
            media_id="doc-1",
            message_id="msg-1",
            db=db,
        )

        assert send_text.await_count == 1
        assert "not collecting files at this stage" in send_text.await_args.args[1]
        get_media_url.assert_not_awaited()
        upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recent_listing_gratitude_gets_contextual_reply_not_welcome(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        await engine._remember_listing_outcome(phone, "pending_verification")

        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        send_text = AsyncMock(return_value=True)
        send_buttons = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_buttons", send_buttons)

        await engine.process_message(
            phone=phone,
            message_type="text",
            text="thank you",
            button_id=None,
            media_id=None,
            message_id="msg-1",
            db=db,
        )

        assert send_text.await_count == 1
        assert "awaiting verification" in send_text.await_args.args[1]
        send_buttons.assert_not_awaited()
