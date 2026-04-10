"""Conversation-memory and LLM routing helpers for the chatbot engine."""

import asyncio
import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.models import Appointment, AppointmentStatus, Payment, Property, PropertyStatus, Subscription, User, UserRole
from services.conversation_service import conversation_service
from services.intent_service import intent_service
from services.whatsapp_service import whatsapp
from utils.helpers import format_naira, format_phone_number

logger = logging.getLogger(__name__)
STATE_KEY_PREFIX = "state:"
DATA_KEY_PREFIX = "data:"
RESUME_KEY_PREFIX = "resume:"
RECENT_CONTEXT_KEY_PREFIX = "recent_context:"
CONVERSATION_HISTORY_KEY_PREFIX = "conversation_history:"
RESUME_PROMPT_STATE = "RESUME_PROMPT"
CUSTOMER_SERVICE_STATE = "CUSTOMER_SERVICE"
ACCOUNT_MENU_STATE = "ACCOUNT_MENU"
ACCOUNT_EDIT_NAME_STATE = "ACCOUNT_EDIT_NAME"
ACCOUNT_EDIT_EMAIL_STATE = "ACCOUNT_EDIT_EMAIL"
SEARCH_HIGHER_BUDGET_OFFER_STATE = "SEARCH_HIGHER_BUDGET_OFFER"
SEARCH_NEIGHBOURHOOD_STATE = "SEARCH_NEIGHBOURHOOD"
LIST_WATER_STATE = "LIST_WATER"
SCHEDULE_VISITOR_NAME_STATE = "SCHEDULE_VISITOR_NAME"
SCHEDULE_VISITOR_ADDRESS_STATE = "SCHEDULE_VISITOR_ADDRESS"
SEARCH_FLOW_STATES = {
    "SEARCH_LOCATION",
    SEARCH_NEIGHBOURHOOD_STATE,
    "SEARCH_BUDGET",
    "SEARCH_TYPE",
    "SEARCH_BEDROOMS",
    SEARCH_HIGHER_BUDGET_OFFER_STATE,
    "VIEW_RESULTS",
    "VIEW_PROPERTY",
    "SCHEDULE_DATE",
    SCHEDULE_VISITOR_NAME_STATE,
    SCHEDULE_VISITOR_ADDRESS_STATE,
    "SCHEDULE_CONFIRM",
}
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
    LIST_WATER_STATE,
    "LIST_PHOTOS",
    "LIST_DOCUMENTS",
    "LIST_LEGAL_REP",
    "LIST_USER_NAME",
    "LIST_USER_PHONE",
}
ACCOUNT_FLOW_STATES = {"ACCOUNT_MENU", "ACCOUNT_EDIT_NAME", "ACCOUNT_EDIT_EMAIL"}


class ChatbotConversationMixin:
    def _conversation_history_key(self, phone: str) -> str:
        return f"{CONVERSATION_HISTORY_KEY_PREFIX}{phone}"

    def _recent_context_key(self, phone: str) -> str:
        return f"{RECENT_CONTEXT_KEY_PREFIX}{phone}"

    async def _set_recent_context(self, phone: str, payload: dict) -> None:
        await self.redis.set(self._recent_context_key(phone), json.dumps(payload), ex=settings.REDIS_RESUME_TTL_SECONDS)

    async def _get_recent_context(self, phone: str) -> dict:
        payload = await self.redis.get(self._recent_context_key(phone))
        if not payload:
            return {}
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            await self.redis.delete(self._recent_context_key(phone))
            return {}
        return data if isinstance(data, dict) else {}

    async def _remember_listing_outcome(self, phone: str, status: str) -> None:
        await self._set_recent_context(
            phone,
            {
                "kind": "listing_completion",
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _remember_booking_outcome(self, phone: str, scheduled_date: str | None = None) -> None:
        await self._set_recent_context(
            phone,
            {
                "kind": "booking_completion",
                "scheduled_date": scheduled_date,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _get_conversation_history(self, phone: str) -> list[dict]:
        payload = await self.redis.get(self._conversation_history_key(phone))
        if not payload:
            return []
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            await self.redis.delete(self._conversation_history_key(phone))
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def _append_conversation_history(self, phone: str, role: str, state: str, content: str | None) -> None:
        text = (content or "").strip()
        if not text:
            return
        history = await self._get_conversation_history(phone)
        history.append(
            {
                "role": role,
                "state": state,
                "content": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        await self.redis.set(
            self._conversation_history_key(phone),
            json.dumps(history[-12:], ensure_ascii=True, default=str),
            ex=settings.REDIS_RESUME_TTL_SECONDS,
        )

    async def _send_text_and_track(self, phone: str, state: str, content: str) -> None:
        await whatsapp.send_text(phone, content)
        await self._append_conversation_history(phone, "assistant", state, content)

    async def _emit_llm_reply(self, phone: str, state: str, reply: str | None) -> None:
        text = (reply or "").strip()
        if not text:
            return
        await self._send_text_and_track(phone, state, text)

    def _is_structured_input_state(self, state: str) -> bool:
        return (
            state in LISTING_FLOW_STATES
            or state in ACCOUNT_FLOW_STATES
            or state in {"SEARCH_LOCATION", "SEARCH_NEIGHBOURHOOD", "SEARCH_BUDGET", "SEARCH_TYPE", "SEARCH_BEDROOMS", SEARCH_HIGHER_BUDGET_OFFER_STATE, "SCHEDULE_DATE", SCHEDULE_VISITOR_NAME_STATE, SCHEDULE_VISITOR_ADDRESS_STATE, "SCHEDULE_CONFIRM", RESUME_PROMPT_STATE}
        )

    def _state_instruction_text(self, state: str, data: dict) -> str:
        prompts = {
            "SEARCH_LOCATION": "Please tell us the state where you want to search for a property.",
            SEARCH_NEIGHBOURHOOD_STATE: "Please tell us the neighbourhood, area, or city you want us to search within.",
            "SEARCH_BUDGET": "Please choose the budget range you would like us to work with.",
            "SEARCH_TYPE": "Please select the property type you prefer.",
            "SEARCH_BEDROOMS": "Please choose the bedroom option that matches what you want.",
            SEARCH_HIGHER_BUDGET_OFFER_STATE: "We found options above your budget. Please tell us if you want to view them or adjust your budget.",
            "VIEW_RESULTS": "Please reply with the number of the property you would like to view.",
            "VIEW_PROPERTY": "Tap Book Inspection whenever you are ready.",
            "SCHEDULE_DATE": "Please share your preferred inspection date and time. Example: 15/07/2026 10:00.",
            SCHEDULE_VISITOR_NAME_STATE: "Please share your full name for the inspection record.",
            SCHEDULE_VISITOR_ADDRESS_STATE: "Please share the address for the inspection record.",
            "SCHEDULE_CONFIRM": "Please tap Confirm when you are ready to finalize the inspection booking.",
            "LIST_TITLE": "Please share the property title.",
            "LIST_ADDRESS": "Please share the property address.",
            "LIST_NEIGHBOURHOOD": "Please share the neighbourhood and a nearby landmark for this property.",
            "LIST_CITY": "Please share the city where the property is located.",
            "LIST_STATE": "Please share the state where the property is located.",
            "LIST_TYPE": "Please select the property type.",
            "LIST_BEDROOMS": "Please choose the bedroom count for this property.",
            "LIST_RENT": "Please enter the annual rent amount in naira.",
            "LIST_AMENITIES": "Please list the amenities, separated by commas.",
            LIST_WATER_STATE: "Please tell us whether the property has water. Reply yes or no.",
            "LIST_PHOTOS": "Please send at least 3 clear property photos or videos before we continue.",
            "LIST_DOCUMENTS": "Please upload the ownership documents for this property and reply with done when you finish.",
            "LIST_LEGAL_REP": "Please share the phone number of a legal representative for this listing.",
            "LIST_USER_NAME": "Please share your full name so we can complete the property record.",
            "LIST_USER_PHONE": "Please share your phone number so we can reach you if needed.",
        }
        return prompts.get(state, "Please continue with the current step and we will guide you.")

    def _conversation_data_context(self, state: str, data: dict, recent_context: dict | None = None) -> dict:
        context = {
            "state": state,
            "state_instruction": self._state_instruction_text(state, data),
        }
        if data:
            context["data"] = {
                "keys": sorted(data.keys()),
                "result_count": len(data.get("result_ids", []) or []),
                "over_budget_count": len(data.get("over_budget_result_ids", []) or []),
                "has_selected_property": bool(data.get("selected_property_id")),
                "has_resume_snapshot": bool(data.get("resume_target_state")),
            }
        if recent_context:
            context["recent_context"] = recent_context
        return context

    async def _send_llm_conversational_reply(
        self,
        phone: str,
        input_value: str | None,
        state: str,
        data: dict,
        user: User,
        db: AsyncSession,
        recent_context: dict | None = None,
    ) -> bool:
        conversation_history = await self._get_conversation_history(phone)
        llm_reply = await conversation_service.generate_reply(
            message=input_value or "",
            current_state=state,
            state_instruction=self._state_instruction_text(state, data),
            conversation_history=conversation_history,
            recent_context=recent_context,
            data_context=self._conversation_data_context(state, data, recent_context),
        )
        if not llm_reply:
            logger.debug("LLM conversation reply unavailable; state=%s phone=%s", state, phone)
            return False

        logger.debug(
            "LLM conversation reply received; state=%s phone=%s action=%s confidence=%.2f reply_present=%s",
            state,
            phone,
            llm_reply.action,
            llm_reply.confidence,
            bool(llm_reply.reply),
        )

        if llm_reply.action == "restart":
            if llm_reply.reply:
                await self._emit_llm_reply(phone, state, llm_reply.reply)
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, user)
            return True
        if llm_reply.action == "switch_service":
            if llm_reply.reply:
                await self._emit_llm_reply(phone, state, llm_reply.reply)
            await self.send_main_menu(phone, user)
            return True
        if llm_reply.action == "search_property":
            if self._is_structured_input_state(state):
                logger.debug("LLM search_property action suppressed in structured state; state=%s phone=%s", state, phone)
                return False
            if llm_reply.reply:
                await self._emit_llm_reply(phone, state, llm_reply.reply)
            await self._start_property_search(phone)
            return True
        if llm_reply.action == "list_property":
            if self._is_structured_input_state(state):
                logger.debug("LLM list_property action suppressed in structured state; state=%s phone=%s", state, phone)
                return False
            if llm_reply.reply:
                await self._emit_llm_reply(phone, state, llm_reply.reply)
            await self._start_property_listing(phone, user)
            return True
        if llm_reply.action == "my_account":
            if self._is_structured_input_state(state):
                logger.debug("LLM my_account action suppressed in structured state; state=%s phone=%s", state, phone)
                return False
            if llm_reply.reply:
                await self._emit_llm_reply(phone, state, llm_reply.reply)
            await self._open_account_service(phone, user, db)
            return True
        if llm_reply.action == "customer_service":
            if self._is_structured_input_state(state):
                logger.debug("LLM customer_service action suppressed in structured state; state=%s phone=%s", state, phone)
                return False
            if llm_reply.reply:
                await self._emit_llm_reply(phone, state, llm_reply.reply)
            await self._open_customer_service(phone, state, data, db)
            return True

        if llm_reply.reply and not self._is_structured_input_state(state):
            await self._emit_llm_reply(phone, state, llm_reply.reply)
            return True
        if llm_reply.reply:
            logger.debug("LLM reply suppressed in structured state; state=%s phone=%s", state, phone)
        logger.debug("LLM reply had no text or routing action; state=%s phone=%s", state, phone)
        return False

    async def _handle_llm_interrupt(
        self,
        phone: str,
        input_value: str | None,
        state: str,
        data: dict,
        user: User,
        db: AsyncSession,
        recent_context: dict | None = None,
    ) -> bool:
        if not input_value:
            return False
        conversation_history = await self._get_conversation_history(phone)
        llm_reply = await conversation_service.generate_reply(
            message=input_value,
            current_state=state,
            state_instruction=self._state_instruction_text(state, data),
            conversation_history=conversation_history,
            recent_context=recent_context,
            data_context=self._conversation_data_context(state, data, recent_context),
        )
        if not llm_reply or llm_reply.action not in {"restart", "switch_service", "search_property", "list_property", "my_account", "customer_service"}:
            return False

        logger.debug("LLM interrupt routed; state=%s phone=%s action=%s confidence=%.2f", state, phone, llm_reply.action, llm_reply.confidence)

        if llm_reply.reply:
            await self._emit_llm_reply(phone, state, llm_reply.reply)

        if llm_reply.action == "restart":
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, user)
            return True
        if llm_reply.action == "switch_service":
            await self.send_main_menu(phone, user)
            return True
        if llm_reply.action == "search_property":
            await self._start_property_search(phone)
            return True
        if llm_reply.action == "list_property":
            await self._start_property_listing(phone, user)
            return True
        if llm_reply.action == "my_account":
            await self._open_account_service(phone, user, db)
            return True
        if llm_reply.action == "customer_service":
            await self._open_customer_service(phone, state, data, db)
            return True
        return False

    async def _handle_recent_context_message(self, phone: str, input_value: str | None, recent_context: dict, active_state: str | None, current_state: str | None) -> bool:
        if active_state or not recent_context:
            return False
        context_kind = recent_context.get("kind")
        if context_kind not in {"listing_completion", "booking_completion"}:
            return False
        if context_kind == "listing_completion":
            status_text = self._describe_listing_status(recent_context.get("status"))
            summary_text = f"Your recent property listing is {status_text}."
        else:
            scheduled_date = recent_context.get("scheduled_date")
            summary_text = "Your recent inspection booking is still confirmed."
            if scheduled_date:
                try:
                    parsed_date = datetime.fromisoformat(scheduled_date)
                    summary_text = f"Your recent inspection booking is confirmed for {parsed_date:%d/%m/%Y %H:%M}."
                except ValueError:
                    pass
        intent = (await intent_service.detect_intent(input_value, current_state or "MAIN_MENU")).intent
        if intent == "gratitude":
            await self._send_text_and_track(phone, current_state or "MAIN_MENU", f"You are welcome. {summary_text} We will keep you updated here as soon as there is progress.")
            return True
        if intent == "status_check":
            await self._send_text_and_track(phone, current_state or "MAIN_MENU", f"{summary_text} We will send the next update here as soon as there is progress.")
            return True
        if intent == "goodbye":
            await self._send_text_and_track(phone, current_state or "MAIN_MENU", f"Thank you for chatting with G & G Homes. {summary_text} We are here whenever you need us.")
            return True
        return False

    async def _handle_idle_courtesy_message(self, phone: str, input_value: str | None, active_state: str | None, current_state: str | None) -> bool:
        if active_state:
            return False
        intent = (await intent_service.detect_intent(input_value, current_state or "MAIN_MENU")).intent
        if intent == "gratitude":
            await self._send_text_and_track(phone, current_state or "MAIN_MENU", "You are welcome. We are here whenever you need us. Just say menu if you would like to continue.")
            return True
        if intent == "goodbye":
            await self._send_text_and_track(phone, current_state or "MAIN_MENU", "Thank you for chatting with G & G Homes. We are here whenever you need us. Just say menu any time.")
            return True
        return False

    async def _open_customer_service(self, phone: str, state: str, data: dict, db: AsyncSession) -> None:
        support_data = {"support_previous_state": state, "support_previous_data": data}
        await self.set_data(phone, support_data)
        await self.set_state(phone, CUSTOMER_SERVICE_STATE)
        recent_context = await self._get_recent_context(phone)
        if recent_context.get("kind") == "listing_completion":
            status_text = self._describe_listing_status(recent_context.get("status"))
            await self._send_text_and_track(phone, CUSTOMER_SERVICE_STATE, f"Customer service is ready to help. Your most recent property listing is currently {status_text}. Tell us what you need help with, or say continue to resume your previous flow.")
            return
        await self._send_text_and_track(phone, CUSTOMER_SERVICE_STATE, "Customer service is ready to help. Please tell us the issue you want us to help with, such as listing update, booking help, account issue, or finding a property. You can also say continue to resume your previous flow.")

    async def _open_account_service(self, phone: str, user: User, db: AsyncSession) -> None:
        await self.set_state(phone, ACCOUNT_MENU_STATE)
        landlord_listings_result = await db.execute(select(Property).where(Property.landlord_id == user.id))
        landlord_listings = landlord_listings_result.scalars().all()
        active_count = sum(1 for prop in landlord_listings if prop.status == PropertyStatus.active)
        pending_count = sum(1 for prop in landlord_listings if prop.status == PropertyStatus.pending_verification)
        appointments_result = await db.execute(select(Appointment).where(or_(Appointment.tenant_id == user.id, Appointment.landlord_id == user.id)))
        appointments_count = len(appointments_result.scalars().all())
        display_name = self._display_name(user) or "there"
        await self._send_text_and_track(
            phone,
            ACCOUNT_MENU_STATE,
            f"Account dashboard for {display_name}.\nListings: {len(landlord_listings)} total ({active_count} active, {pending_count} pending verification)\nAppointments: {appointments_count}\nChoose what you want to view below.",
        )
        await whatsapp.send_list(
            phone,
            "Select an account section.",
            "Open Section",
            [{
                "title": "My Account",
                "rows": [
                    {"id": "account_profile", "title": "Profile Details"},
                    {"id": "account_edit_profile", "title": "Edit Profile"},
                    {"id": "account_listings", "title": "My Listings"},
                    {"id": "account_appointments", "title": "My Appointments"},
                    {"id": "account_payments", "title": "Payment History"},
                    {"id": "account_subscriptions", "title": "Subscription Status"},
                    {"id": "account_back_home", "title": "Back To Main Menu"},
                ],
            }],
        )

    async def _offer_resume_or_restart(self, phone: str, user: User, state: str, data: dict) -> None:
        if state == "MAIN_MENU":
            await self.send_main_menu(phone, user)
            return
        await self.set_data(phone, {"resume_target_state": state, "resume_target_data": data})
        await self.set_state(phone, RESUME_PROMPT_STATE)
        prompt = "Good to hear from you again. We still have your previous conversation saved. Would you like us to continue where we stopped or start a fresh conversation?"
        await whatsapp.send_buttons(
            phone,
            prompt,
            [
                {"id": "resume_previous", "title": "Continue"},
                {"id": "resume_new", "title": "Start New"},
            ],
        )
        await self._append_conversation_history(phone, "assistant", RESUME_PROMPT_STATE, prompt)

    async def _prompt_for_state(self, phone: str, state: str, data: dict, db: AsyncSession) -> None:
        if state == "SEARCH_LOCATION":
            await self._send_text_and_track(phone, state, "We are continuing your property search. Which state would you like us to search in?")
        elif state == SEARCH_NEIGHBOURHOOD_STATE:
            await self._send_text_and_track(phone, state, "Please share the neighbourhood, area, or city you want us to search within.")
        elif state == "SEARCH_BUDGET":
            await self._send_search_budget_options(phone)
        elif state == "SEARCH_TYPE":
            await whatsapp.send_list(
                phone,
                "Great. Please select the property type you prefer.",
                "Choose Type",
                [{
                    "title": "Types",
                    "rows": [
                        {"id": "self_contain", "title": "Self Contain"},
                        {"id": "flat", "title": "Flat"},
                        {"id": "duplex", "title": "Duplex"},
                        {"id": "bungalow", "title": "Bungalow"},
                        {"id": "office_space", "title": "Office Space"},
                        {"id": "warehouse", "title": "Warehouse"},
                    ],
                }],
            )
        elif state == "SEARCH_BEDROOMS":
            await self._send_flat_bedroom_options(phone)
        elif state == SEARCH_HIGHER_BUDGET_OFFER_STATE:
            await self._send_text_and_track(phone, state, "We found matching properties above your budget. Reply yes to view them, or no to search with another budget.")
        elif state == "VIEW_RESULTS":
            await self._send_search_results(phone, data, db)
        elif state == "LIST_TITLE":
            await self._send_text_and_track(phone, state, "We are continuing your property listing. Please share the property title.")
        elif state == "LIST_ADDRESS":
            await self._send_text_and_track(phone, state, "Please share the property address.")
        elif state == "LIST_NEIGHBOURHOOD":
            await self._send_text_and_track(phone, state, "Kindly share the neighbourhood and a nearby landmark for this property.")
        elif state == "LIST_CITY":
            await self._send_text_and_track(phone, state, "Please share the city where the property is located.")
        elif state == "LIST_STATE":
            await self._send_text_and_track(phone, state, "Please share the state where the property is located.")
        elif state == "LIST_TYPE":
            await whatsapp.send_list(
                phone,
                "Please select the property type.",
                "Choose Type",
                [{"title": "Property Types", "rows": [{"id": item.value, "title": item.value.replace("_", " ").title()} for item in PropertyType if item != PropertyType.room_and_parlour]}],
            )
        elif state == "LIST_BEDROOMS":
            await self._send_listing_bedroom_options(phone)
        elif state == "LIST_BEDROOMS_CUSTOM":
            await self._send_text_and_track(phone, state, "Please enter the exact number of bedrooms for this property, for example 4, 5, or 6.")
        elif state == "LIST_RENT":
            await self._send_text_and_track(phone, state, "Please enter the annual rent amount in naira. You can write it as 500000 or 500,000.")
        elif state == "LIST_AMENITIES":
            await self._send_text_and_track(phone, state, "Please list the amenities, separated by commas.")
        elif state == LIST_WATER_STATE:
            await self._send_text_and_track(phone, state, "Does the property have water? Please reply yes or no.")
        elif state == "LIST_PHOTOS":
            await self._send_text_and_track(phone, state, "You can now send property photos or videos. Please send at least 3 clear photos or videos of the property. When you are done, simply say done and we will proceed.")
        elif state == "LIST_DOCUMENTS":
            await self._send_text_and_track(phone, state, "Please upload the ownership documents for this property. Your data is secure and will not be shared with any third party. When you have uploaded the document files, reply with done.")
        elif state == "LIST_LEGAL_REP":
            await self._send_text_and_track(phone, state, "Please share the phone number of a legal representative we should keep on this listing.")
        elif state == "LIST_USER_NAME":
            await self._send_text_and_track(phone, state, "Please share your full name so we can complete the property record.")
        elif state == "LIST_USER_PHONE":
            await self._send_text_and_track(phone, state, "Please share your phone number so we can reach you if needed.")
        elif state == "SCHEDULE_DATE":
            await self._send_text_and_track(phone, state, "Please share your preferred inspection date and time. Example: 15/07/2026 10:00")
        elif state == SCHEDULE_VISITOR_NAME_STATE:
            await self._send_text_and_track(phone, state, "Please share your full name for the inspection record.")
        elif state == SCHEDULE_VISITOR_ADDRESS_STATE:
            await self._send_text_and_track(phone, state, "Please share the address for the inspection record.")
        elif state == ACCOUNT_MENU_STATE:
            await self._open_account_service(phone, await self._get_or_create_user(phone, db), db)
        elif state == ACCOUNT_EDIT_NAME_STATE:
            await self._send_text_and_track(phone, state, "Please send your full name in this format: Firstname Lastname.")
        elif state == ACCOUNT_EDIT_EMAIL_STATE:
            await self._send_text_and_track(phone, state, "Please send your email address, for example name@example.com.")
        else:
            await self.send_main_menu(phone, user=await self._get_or_create_user(phone, db))
