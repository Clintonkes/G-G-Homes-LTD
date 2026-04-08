"""Unit tests covering security helpers and selected service-layer behaviors."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from core.security import create_access_token, decode_access_token, hash_password, verify_password
from database.models import User, UserRole
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

    async def rpush(self, key, value):
        values = self.store.get(key, [])
        if not isinstance(values, list):
            values = [values]
        values.append(value)
        self.store[key] = values

    async def lrange(self, key, start, end):
        values = self.store.get(key, [])
        if not isinstance(values, list):
            return []
        if end == -1:
            return values[start:]
        return values[start : end + 1]


class TestChatbotMediaBatching:
    @pytest.mark.asyncio
    async def test_resume_prompt_accepts_natural_continue_phrase(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        await engine.set_state(phone, "RESUME_PROMPT")
        await engine.set_data(
            phone,
            {
                "resume_target_state": "LIST_ADDRESS",
                "resume_target_data": {"title": "2 Bedroom Flat"},
            },
        )

        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        send_text = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)

        await engine.process_message(
            phone=phone,
            message_type="text",
            text="lets continue our previous conversation",
            button_id=None,
            media_id=None,
            message_id="msg-1",
            db=db,
        )

        assert await engine.get_state(phone) == "LIST_ADDRESS"
        assert send_text.await_count == 2
        assert "continuing from where we stopped" in send_text.await_args_list[0].args[1].lower()

    @pytest.mark.asyncio
    async def test_list_photos_prompt_only_for_text_or_document(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        await engine.set_state(phone, "LIST_PHOTOS")
        await engine.set_data(phone, {"photo_urls": ["https://cdn.example/1.jpg"], "video_urls": []})

        intent_decision = type("IntentDecision", (), {"intent": "unknown"})()
        monkeypatch.setattr("services.chatbot_engine.intent_service.detect_intent", AsyncMock(return_value=intent_decision))
        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        send_text = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)

        await engine.process_message(
            phone=phone,
            message_type="sticker",
            text=None,
            button_id=None,
            media_id=None,
            message_id="msg-1",
            db=db,
        )
        send_text.assert_not_awaited()

        await engine.process_message(
            phone=phone,
            message_type="text",
            text="hello there",
            button_id=None,
            media_id=None,
            message_id="msg-2",
            db=db,
        )
        assert send_text.await_count == 1
        assert "please send a property image or video" in send_text.await_args.args[1].lower()

        await engine.process_message(
            phone=phone,
            message_type="document",
            text=None,
            button_id=None,
            media_id="doc-1",
            message_id="msg-3",
            db=db,
        )
        assert send_text.await_count == 2
        assert "still collecting property photos and videos" in send_text.await_args.args[1].lower()

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
    async def test_three_photos_then_done_advances_once_to_documents(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        await engine.set_state(phone, "LIST_PHOTOS")
        await engine.set_data(phone, {"photo_urls": [], "video_urls": []})

        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        monkeypatch.setattr("services.chatbot_engine.whatsapp.get_media_url", AsyncMock(side_effect=["url-1", "url-2", "url-3"]))
        monkeypatch.setattr("services.chatbot_engine.whatsapp.download_media", AsyncMock(side_effect=[b"a", b"b", b"c"]))
        monkeypatch.setattr("services.chatbot_engine.asyncio.sleep", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "services.chatbot_engine.media_service.upload",
            AsyncMock(side_effect=["https://cdn.example/1.jpg", "https://cdn.example/2.jpg", "https://cdn.example/3.jpg"]),
        )
        send_text = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)

        await engine.process_message(
            phone=phone,
            message_type="image",
            text=None,
            button_id=None,
            media_id="media-1",
            media_items=[
                {"type": "image", "id": "media-1"},
                {"type": "image", "id": "media-2"},
                {"type": "image", "id": "media-3"},
            ],
            message_id="msg-1",
            message_ids=["msg-1", "msg-2", "msg-3"],
            db=db,
        )

        assert send_text.await_count == 1
        assert "That brings us to 3 media files" in send_text.await_args.args[1]
        assert "Please send a property image or video" not in send_text.await_args.args[1]

        saved_data = await engine.get_data(phone)
        assert len(saved_data["photo_urls"]) == 3
        assert len(saved_data["video_urls"]) == 0
        assert await engine.get_state(phone) == "LIST_PHOTOS"

        send_text.reset_mock()

        await engine.process_message(
            phone=phone,
            message_type="text",
            text="done",
            button_id=None,
            media_id=None,
            message_id="msg-4",
            db=db,
        )

        assert send_text.await_count == 1
        assert "upload the ownership documents" in send_text.await_args.args[1].lower()
        assert await engine.get_state(phone) == "LIST_DOCUMENTS"

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

    @pytest.mark.asyncio
    async def test_start_afresh_clears_recent_context_and_returns_main_menu(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        await engine._remember_listing_outcome(phone, "pending_verification")

        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        send_text = AsyncMock(return_value=True)
        send_list = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_list", send_list)

        await engine.process_message(
            phone=phone,
            message_type="text",
            text="let's start afresh",
            button_id=None,
            media_id=None,
            message_id="msg-1",
            db=db,
        )

        assert await engine.get_state(phone) == "MAIN_MENU"
        assert await engine._get_recent_context(phone) == {}
        send_text.assert_not_awaited()
        assert send_list.await_count == 1

    @pytest.mark.asyncio
    async def test_idle_nice_job_gets_polite_close_not_welcome(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        send_text = AsyncMock(return_value=True)
        send_buttons = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_buttons", send_buttons)

        await engine.process_message(
            phone=phone,
            message_type="text",
            text="nice job",
            button_id=None,
            media_id=None,
            message_id="msg-1",
            db=db,
        )

        assert send_text.await_count == 1
        assert "whenever you need us" in send_text.await_args.args[1].lower()
        send_buttons.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_customer_service_request_enters_support_flow(self, db, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        phone = "2348012345678"

        monkeypatch.setattr("services.chatbot_engine.whatsapp.mark_as_read", AsyncMock(return_value=True))
        send_text = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_text", send_text)

        await engine.process_message(
            phone=phone,
            message_type="text",
            text="I need customer service",
            button_id=None,
            media_id=None,
            message_id="msg-1",
            db=db,
        )

        assert await engine.get_state(phone) == "CUSTOMER_SERVICE"
        assert send_text.await_count == 1
        assert "customer service is ready to help" in send_text.await_args.args[1].lower()

    @pytest.mark.asyncio
    async def test_main_menu_uses_list_with_customer_service(self, monkeypatch):
        engine = ChatbotEngine(redis_client=FakeRedis())
        user = User(full_name="Ada Lovelace", phone_number="2348012345678", role=UserRole.tenant)
        send_list = AsyncMock(return_value=True)
        monkeypatch.setattr("services.chatbot_engine.whatsapp.send_list", send_list)

        await engine.send_main_menu("2348012345678", user)

        assert send_list.await_count == 1
        args = send_list.await_args.args
        assert "6. Customer service" in args[1]
        rows = args[3][0]["rows"]
        assert any(row["id"] == "customer_service" and row["title"] == "Customer Service" for row in rows)
