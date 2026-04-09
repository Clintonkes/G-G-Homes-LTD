"""Intent classification service that combines deterministic rules with an optional LLM-backed classifier."""

import json
from dataclasses import dataclass

import httpx

from core.config import settings

ALLOWED_INTENTS = {
    "search_property",
    "list_property",
    "my_account",
    "customer_service",
    "switch_service",
    "continue",
    "restart",
    "decline",
    "greeting",
    "gratitude",
    "status_check",
    "clarification",
    "goodbye",
    "unknown",
}
SEARCH_FLOW_STATES = {"SEARCH_LOCATION", "SEARCH_BUDGET", "SEARCH_TYPE", "SEARCH_BEDROOMS", "VIEW_RESULTS", "VIEW_PROPERTY", "SCHEDULE_DATE", "SCHEDULE_CONFIRM"}
LISTING_FLOW_STATES = {
    "LIST_TITLE",
    "LIST_ADDRESS",
    "LIST_NEIGHBOURHOOD",
    "LIST_CITY",
    "LIST_STATE",
    "LIST_TYPE",
    "LIST_BEDROOMS",
    "LIST_BEDROOMS_CUSTOM",
    "LIST_RENT",
    "LIST_AMENITIES",
    "LIST_PHOTOS",
    "LIST_DOCUMENTS",
    "LIST_LEGAL_REP",
    "LIST_USER_NAME",
    "LIST_USER_PHONE",
}


@dataclass
class IntentDecision:
    intent: str = "unknown"
    confidence: float = 0.0
    source: str = "fallback"


class IntentService:
    """Resolves free-form user messages into stable intents for the state machine."""

    def _normalize_text(self, value: str | None) -> str:
        return (value or "").strip().lower()

    def _state_step_hint(self, current_state: str) -> str:
        hints = {
            "LIST_TITLE": "expects property title text",
            "LIST_ADDRESS": "expects property address text",
            "LIST_NEIGHBOURHOOD": "expects neighbourhood and nearby landmark",
            "LIST_CITY": "expects city text",
            "LIST_STATE": "expects state text",
            "LIST_TYPE": "expects a property type selection",
            "LIST_BEDROOMS": "expects number of bedrooms",
            "LIST_BEDROOMS_CUSTOM": "expects exact bedroom count as number",
            "LIST_RENT": "expects annual rent amount",
            "LIST_AMENITIES": "expects comma-separated amenities",
            "LIST_PHOTOS": "expects images or videos",
            "LIST_DOCUMENTS": "expects ownership document files",
            "LIST_LEGAL_REP": "expects legal representative phone number",
            "LIST_USER_NAME": "expects landlord full name",
            "LIST_USER_PHONE": "expects landlord phone number",
            "SEARCH_LOCATION": "expects desired location text",
            "SEARCH_BUDGET": "expects budget choice or budget amount",
            "SEARCH_TYPE": "expects preferred property type",
            "SEARCH_BEDROOMS": "expects preferred bedroom count",
            "VIEW_RESULTS": "expects selected property number",
            "SCHEDULE_DATE": "expects preferred inspection date and time",
            "SCHEDULE_CONFIRM": "expects confirmation to complete booking",
        }
        return hints.get(current_state, "expects continuation of current flow")

    def _contains_any(self, normalized: str, terms: list[str]) -> bool:
        return any(term in normalized for term in terms)


    def _extract_response_text(self, payload: dict) -> str | None:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        output = payload.get("output") or []
        if isinstance(output, list):
            for item in output:
                contents = item.get("content") if isinstance(item, dict) else None
                if not isinstance(contents, list):
                    continue
                for content in contents:
                    if not isinstance(content, dict):
                        continue
                    if content.get("type") in {"output_text", "text"}:
                        text = content.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
        return None


    def _rule_based_intent(self, message: str | None, current_state: str) -> IntentDecision:
        normalized = self._normalize_text(message)
        if not normalized:
            return IntentDecision(intent="unknown", confidence=0.0, source="fallback")
        if current_state != "MAIN_MENU":
            return IntentDecision(intent="continue", confidence=0.55, source="fallback")
        return IntentDecision(intent="unknown", confidence=0.2, source="fallback")


    async def _llm_intent(self, message: str, current_state: str) -> IntentDecision | None:
        if not settings.LLM_INTENT_ENABLED or not settings.LLM_INTENT_API_KEY:
            return None

        step_hint = self._state_step_hint(current_state)

        system_prompt = (
            "You classify WhatsApp real-estate assistant messages into a small set of intents. "
            "Return strict JSON with keys: intent, confidence. "
            "Allowed intents: search_property, list_property, my_account, customer_service, switch_service, continue, restart, decline, greeting, gratitude, status_check, clarification, goodbye, unknown. "
            "Use the current workflow state and the user's meaning, not exact wording. "
            "Return continue when the user is answering the current step, confirming, proceeding, or indicating they are finished with the current step. "
            "Return restart when the user wants to reset the whole conversation. "
            "Return switch_service when the user wants to switch to a different service but has not specified which one. "
            "Return search_property when the user wants to begin, restart, or change a property search. "
            "Return list_property when the user wants to list or submit a property. "
            "Return my_account when the user wants account or profile help. "
            "Return customer_service when the user wants support or a human. "
            "Return decline when the user is rejecting the current suggestion or wants an alternative. "
            "Return greeting, gratitude, status_check, clarification, or goodbye when those are the user's primary intent. "
            "Do not depend on specific phrases; infer the intent semantically."
        )
        
        user_prompt = (
            f"Current state: {current_state}\n"
            f"Current step hint: {step_hint}\n"
            f"User message: {message}\n"
            "Respond with JSON only."
        )

        use_responses_api = settings.LLM_INTENT_API_URL.rstrip("/").endswith("/responses")
        if use_responses_api:
            payload = {
                "model": settings.LLM_INTENT_MODEL,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
                "temperature": settings.LLM_INTENT_TEMPERATURE,
                "max_output_tokens": settings.LLM_INTENT_MAX_TOKENS,
                "text": {"format": {"type": "json_object"}},
            }
        else:
            payload = {
                "model": settings.LLM_INTENT_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": settings.LLM_INTENT_TEMPERATURE,
                "max_tokens": settings.LLM_INTENT_MAX_TOKENS,
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
            response_json = response.json()
            if use_responses_api:
                content = self._extract_response_text(response_json)
            else:
                content = response_json["choices"][0]["message"]["content"]
            if not content:
                return None
            parsed = json.loads(content)
            intent = parsed.get("intent", "unknown")
            confidence = float(parsed.get("confidence", 0.0))
            if intent not in ALLOWED_INTENTS:
                return None
            confidence = max(0.0, min(confidence, 1.0))
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
            if settings.LLM_INTENT_ALWAYS_USE:
                return llm_decision
            if llm_decision.intent == fallback.intent:
                return llm_decision
            if llm_decision.confidence >= settings.LLM_INTENT_MIN_CONFIDENCE:
                return llm_decision
        return fallback


intent_service = IntentService()
