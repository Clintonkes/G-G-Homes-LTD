"""LLM-backed conversational reply generation for natural WhatsApp interactions."""

import logging
import json
from dataclasses import dataclass

import httpx

from core.config import settings

logger = logging.getLogger(__name__)
ALLOWED_ACTIONS = {
    "none",
    "continue",
    "restart",
    "search_property",
    "list_property",
    "my_account",
    "customer_service",
    "switch_service",
}


@dataclass
class ConversationReply:
    reply: str = ""
    action: str = "none"
    confidence: float = 0.0
    source: str = "fallback"


class ConversationService:
    """Produces natural language responses and optional routing hints from the LLM."""

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

    def _serialize_context(self, context: dict | None) -> str:
        if not context:
            return "{}"
        try:
            return json.dumps(context, ensure_ascii=True, default=str)
        except Exception:
            return "{}"

    async def generate_reply(
        self,
        *,
        message: str,
        current_state: str,
        state_instruction: str,
        conversation_history: list[dict] | None = None,
        recent_context: dict | None = None,
        data_context: dict | None = None,
    ) -> ConversationReply | None:
        if not settings.LLM_CHAT_ENABLED or not settings.LLM_CHAT_API_KEY:
            logger.debug(
                "LLM chat disabled or missing API key; state=%s",
                current_state,
            )
            return None

        system_prompt = (
            "You are the WhatsApp assistant for G & G Homes. "
            "Write a concise, warm, natural reply that sounds like a helpful human assistant. "
            "Use the current workflow state and context to understand what the user means. "
            "Return strict JSON with keys: reply, action, confidence. "
            f"Allowed actions: {', '.join(sorted(ALLOWED_ACTIONS))}. "
            "Use action none when you are only replying conversationally. "
            "Use action continue when the user is answering the current step. "
            "Use action restart when the user wants to start over. "
            "Use action switch_service when the user wants to change to another service without naming it clearly. "
            "Use action search_property, list_property, my_account, or customer_service when the user clearly wants that new path. "
            "Do not mention internal states, rules, or JSON. "
            "Do not rely on exact phrases; infer the user's intent semantically."
        )
        
        user_prompt = (
            f"Current state: {current_state}\n"
            f"State guidance: {state_instruction}\n"
            f"User message: {message}\n"
            f"Conversation history: {self._serialize_context(conversation_history)}\n"
            f"Recent context: {self._serialize_context(recent_context)}\n"
            f"Data context: {self._serialize_context(data_context)}\n"
            "Respond with JSON only."
        )

        use_responses_api = settings.LLM_CHAT_API_URL.rstrip("/").endswith("/responses")
        if use_responses_api:
            payload = {
                "model": settings.LLM_CHAT_MODEL,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
                "temperature": settings.LLM_CHAT_TEMPERATURE,
                "max_output_tokens": settings.LLM_CHAT_MAX_TOKENS,
                "text": {"format": {"type": "json_object"}},
            }
        else:
            payload = {
                "model": settings.LLM_CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": settings.LLM_CHAT_TEMPERATURE,
                "max_tokens": settings.LLM_CHAT_MAX_TOKENS,
                "response_format": {"type": "json_object"},
            }

        headers = {
            "Authorization": f"Bearer {settings.LLM_CHAT_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            logger.debug(
                "LLM chat request starting; state=%s action_candidates=%s",
                current_state,
                sorted(ALLOWED_ACTIONS),
            )
            async with httpx.AsyncClient(timeout=settings.LLM_CHAT_TIMEOUT_SECONDS) as client:
                response = await client.post(settings.LLM_CHAT_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            response_json = response.json()
            if use_responses_api:
                content = self._extract_response_text(response_json)
            else:
                content = response_json["choices"][0]["message"]["content"]
            if not content:
                logger.debug("LLM chat returned empty content; state=%s", current_state)
                return None
            parsed = json.loads(content)
            reply = str(parsed.get("reply", "")).strip()
            action = str(parsed.get("action", "none")).strip()
            if action not in ALLOWED_ACTIONS:
                action = "none"
            confidence = float(parsed.get("confidence", 0.0))
            confidence = max(0.0, min(confidence, 1.0))
            logger.debug(
                "LLM chat parsed; state=%s action=%s confidence=%.2f reply_present=%s",
                current_state,
                action,
                confidence,
                bool(reply),
            )
            return ConversationReply(reply=reply, action=action, confidence=confidence, source="llm")
        except Exception:
            logger.exception("LLM chat request failed; state=%s", current_state)
            return None


conversation_service = ConversationService()
