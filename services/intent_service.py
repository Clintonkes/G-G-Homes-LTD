"""Intent classification service that combines deterministic rules with an optional LLM-backed classifier."""

import json
from dataclasses import dataclass

import httpx

from core.config import settings

ALLOWED_INTENTS = {"search_property", "list_property", "my_account", "continue", "unknown"}
SEARCH_FLOW_STATES = {"SEARCH_LOCATION", "SEARCH_BUDGET", "SEARCH_TYPE", "SEARCH_BEDROOMS", "VIEW_RESULTS", "VIEW_PROPERTY", "SCHEDULE_DATE", "SCHEDULE_CONFIRM"}
LISTING_FLOW_STATES = {"LIST_TITLE", "LIST_ADDRESS", "LIST_NEIGHBOURHOOD", "LIST_TYPE", "LIST_BEDROOMS", "LIST_RENT", "LIST_AMENITIES", "LIST_PHOTOS"}


@dataclass
class IntentDecision:
    intent: str = "unknown"
    confidence: float = 0.0
    source: str = "fallback"


class IntentService:
    """Resolves free-form user messages into stable intents for the state machine."""

    def _normalize_text(self, value: str | None) -> str:
        return (value or "").strip().lower()

    def _contains_any(self, normalized: str, terms: list[str]) -> bool:
        return any(term in normalized for term in terms)

    def _rule_based_intent(self, message: str | None, current_state: str) -> IntentDecision:
        normalized = self._normalize_text(message)
        if not normalized:
            return IntentDecision(intent="unknown", confidence=0.0, source="fallback")

        listing_terms = [
            "list my property",
            "enlist my property",
            "register my property",
            "submit my property",
            "post my property",
            "advertise my property",
            "rent out my property",
            "property for rent",
            "i want to list",
            "i want to enlist",
            "i want to post",
            "list property",
            "enlist property",
        ]
        search_terms = [
            "find a house",
            "find house",
            "find a home",
            "search for",
            "show me houses",
            "looking for",
            "need accommodation",
            "need a place",
            "search house",
            "search property",
        ]
        account_terms = ["account", "profile", "my details", "my booking", "my appointment"]

        is_listing = self._contains_any(normalized, listing_terms)
        is_search = self._contains_any(normalized, search_terms)
        is_account = self._contains_any(normalized, account_terms)

        if current_state in LISTING_FLOW_STATES:
            if is_search:
                return IntentDecision(intent="search_property", confidence=0.9, source="fallback")
            if is_account:
                return IntentDecision(intent="my_account", confidence=0.82, source="fallback")
            return IntentDecision(intent="continue", confidence=0.95, source="fallback")

        if current_state in SEARCH_FLOW_STATES:
            if is_listing:
                return IntentDecision(intent="list_property", confidence=0.92, source="fallback")
            if is_account:
                return IntentDecision(intent="my_account", confidence=0.82, source="fallback")
            return IntentDecision(intent="continue", confidence=0.9, source="fallback")

        if is_listing:
            return IntentDecision(intent="list_property", confidence=0.92, source="fallback")
        if is_search or any(term in normalized for term in ["house", "home", "apartment", "flat"]):
            return IntentDecision(intent="search_property", confidence=0.88, source="fallback")
        if is_account:
            return IntentDecision(intent="my_account", confidence=0.8, source="fallback")

        if current_state != "MAIN_MENU":
            return IntentDecision(intent="continue", confidence=0.65, source="fallback")
        return IntentDecision(intent="unknown", confidence=0.2, source="fallback")

    async def _llm_intent(self, message: str, current_state: str) -> IntentDecision | None:
        if not settings.LLM_INTENT_ENABLED or not settings.LLM_INTENT_API_KEY:
            return None

        system_prompt = (
            "You classify WhatsApp real-estate assistant messages into a small set of intents. "
            "Return strict JSON with keys: intent, confidence. "
            "Allowed intents: search_property, list_property, my_account, continue, unknown. "
            "Respect the current workflow. If the user is already in a listing workflow and their message answers the current step, return continue. "
            "If the user is already in a property-search workflow and their message answers the current step, return continue. "
            "Only switch to list_property or search_property when the user clearly wants to change direction."
        )
        user_prompt = (
            f"Current state: {current_state}\n"
            f"User message: {message}\n"
            "Respond with JSON only."
        )
        payload = {
            "model": settings.LLM_INTENT_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {settings.LLM_INTENT_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=settings.LLM_INTENT_TIMEOUT_SECONDS) as client:
                response = await client.post(settings.LLM_INTENT_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            intent = parsed.get("intent", "unknown")
            confidence = float(parsed.get("confidence", 0.0))
            if intent not in ALLOWED_INTENTS:
                return None
            return IntentDecision(intent=intent, confidence=confidence, source="llm")
        except Exception:
            return None

    async def detect_intent(self, message: str | None, current_state: str) -> IntentDecision:
        fallback = self._rule_based_intent(message, current_state)
        normalized = self._normalize_text(message)
        if not normalized:
            return fallback
        llm_decision = await self._llm_intent(normalized, current_state)
        if llm_decision and llm_decision.intent in ALLOWED_INTENTS:
            return llm_decision
        return fallback


intent_service = IntentService()
