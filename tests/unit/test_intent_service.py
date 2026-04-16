"""Unit tests for the intent classification orchestration and confidence gating."""

from unittest.mock import AsyncMock

import pytest

from services.intent_service import IntentDecision, IntentService


class TestIntentService:
    @pytest.mark.asyncio
    async def test_listing_city_state_defaults_to_continue(self):
        service = IntentService()
        decision = service._rule_based_intent("Abakaliki", "LIST_CITY")
        assert decision.intent == "continue"
        assert decision.source == "fallback"

    @pytest.mark.asyncio
    async def test_detect_intent_uses_llm_when_high_confidence(self, monkeypatch):
        service = IntentService()
        monkeypatch.setattr(
            service,
            "_llm_intent",
            AsyncMock(return_value=IntentDecision(intent="search_property", confidence=0.93, source="llm")),
        )

        decision = await service.detect_intent("I need a flat in GRA", "MAIN_MENU")
        assert decision.intent == "search_property"
        assert decision.source == "llm"

    @pytest.mark.asyncio
    async def test_detect_intent_falls_back_when_llm_confidence_is_low(self, monkeypatch):
        service = IntentService()
        monkeypatch.setattr("services.intent_service.settings.LLM_INTENT_ALWAYS_USE", False)
        monkeypatch.setattr(
            service,
            "_llm_intent",
            AsyncMock(return_value=IntentDecision(intent="list_property", confidence=0.2, source="llm")),
        )

        decision = await service.detect_intent("show me houses", "MAIN_MENU")
        assert decision.intent == "unknown"
        assert decision.source == "fallback"

    @pytest.mark.asyncio
    async def test_detect_intent_uses_llm_even_with_low_confidence_when_always_use(self, monkeypatch):
        service = IntentService()
        monkeypatch.setattr("services.intent_service.settings.LLM_INTENT_ALWAYS_USE", True)
        monkeypatch.setattr(
            service,
            "_llm_intent",
            AsyncMock(return_value=IntentDecision(intent="list_property", confidence=0.1, source="llm")),
        )

        decision = await service.detect_intent("show me houses", "MAIN_MENU")
        assert decision.intent == "list_property"
        assert decision.source == "llm"

    @pytest.mark.asyncio
    async def test_rule_based_intent_detects_payment_status_check_language(self):
        service = IntentService()
        decision = service._rule_based_intent("I have paid already, please check", "MAIN_MENU")
        assert decision.intent == "status_check"
        assert decision.source == "rule"

    @pytest.mark.asyncio
    async def test_rule_based_intent_detects_reopen_checkout_language(self):
        service = IntentService()
        decision = service._rule_based_intent("Please send checkout again", "MAIN_MENU")
        assert decision.intent == "make_payment"
        assert decision.source == "rule"
