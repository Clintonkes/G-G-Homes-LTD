"""Conversation engine for WhatsApp interactions, including state management, intent handling, and guided user flows."""

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.models import Appointment, AppointmentStatus, Payment, Property, PropertyStatus, PropertyType, Subscription, User, UserRole
from services.conversation_service import conversation_service
from services.intent_service import intent_service
from services.media_service import media_service
from services.property_service import property_service
from services.whatsapp_service import whatsapp
from utils.helpers import format_naira, format_phone_number, parse_naira_amount

STATE_KEY_PREFIX = "state:"
DATA_KEY_PREFIX = "data:"
RESUME_KEY_PREFIX = "resume:"
MEDIA_BATCH_KEY_PREFIX = "media_batch:"
RECENT_CONTEXT_KEY_PREFIX = "recent_context:"
RESUME_PROMPT_STATE = "RESUME_PROMPT"
LIST_BEDROOMS_CUSTOM_STATE = "LIST_BEDROOMS_CUSTOM"
CUSTOMER_SERVICE_STATE = "CUSTOMER_SERVICE"
ACCOUNT_MENU_STATE = "ACCOUNT_MENU"
ACCOUNT_EDIT_NAME_STATE = "ACCOUNT_EDIT_NAME"
ACCOUNT_EDIT_EMAIL_STATE = "ACCOUNT_EDIT_EMAIL"
SEARCH_HIGHER_BUDGET_OFFER_STATE = "SEARCH_HIGHER_BUDGET_OFFER"
SEARCH_FLOW_STATES = {"SEARCH_LOCATION", "SEARCH_BUDGET", "SEARCH_TYPE", "SEARCH_BEDROOMS", SEARCH_HIGHER_BUDGET_OFFER_STATE, "VIEW_RESULTS", "VIEW_PROPERTY", "SCHEDULE_DATE", "SCHEDULE_CONFIRM"}
LISTING_FLOW_STATES = {"LIST_TITLE", "LIST_ADDRESS", "LIST_NEIGHBOURHOOD", "LIST_CITY", "LIST_STATE", "LIST_TYPE", "LIST_BEDROOMS", LIST_BEDROOMS_CUSTOM_STATE, "LIST_RENT", "LIST_AMENITIES", "LIST_PHOTOS", "LIST_DOCUMENTS", "LIST_LEGAL_REP", "LIST_USER_NAME", "LIST_USER_PHONE"}
ACCOUNT_FLOW_STATES = {ACCOUNT_MENU_STATE, ACCOUNT_EDIT_NAME_STATE, ACCOUNT_EDIT_EMAIL_STATE}


class ChatbotEngine:
    """Coordinates conversation state and user actions for the WhatsApp assistant."""

    def __init__(self, redis_client) -> None:
        self.redis = redis_client
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(digest))

    def _state_key(self, phone: str) -> str:
        return f"{STATE_KEY_PREFIX}{phone}"

    def _data_key(self, phone: str) -> str:
        return f"{DATA_KEY_PREFIX}{phone}"

    def _resume_key(self, phone: str) -> str:
        return f"{RESUME_KEY_PREFIX}{phone}"

    def _normalize_text(self, value: str | None) -> str:
        return (value or "").strip().lower()

    def _display_name(self, user: User) -> str | None:
        name = (user.full_name or "").strip()
        if not name or name.lower() in {"guest", "whatsapp user", "valued client"}:
            return None
        return name

    def _is_placeholder_name(self, name: str | None) -> bool:
        if not name:
            return True
        normalized = name.strip().lower()
        return normalized in {"whatsapp user", "valued client", "guest", "partner", "unknown"}

    def _resolve_service_selection(self, input_value: str | None) -> str | None:
        normalized = self._normalize_text(input_value)
        if not normalized:
            return None
        mapping = {
            "search_property": "search_property",
            "list_property": "list_property",
            "my_account": "my_account",
            "customer_service": "customer_service",
            "account_profile": "account_profile",
            "account_edit_profile": "account_edit_profile",
            "account_edit_name": "account_edit_name",
            "account_edit_email": "account_edit_email",
            "account_listings": "account_listings",
            "account_appointments": "account_appointments",
            "account_payments": "account_payments",
            "account_subscriptions": "account_subscriptions",
            "account_back_home": "account_back_home",
        }
        return mapping.get(normalized)

    def _pluralize(self, count: int, singular: str, plural: str | None = None) -> str:
        return singular if count == 1 else (plural or f"{singular}s")

    def _property_search_result_line(self, index: int, prop: Property) -> str:
        city = (prop.city or "").strip() or "N/A"
        state = (prop.state or "").strip() or "N/A"
        return (
            f"{index}. {prop.title}\n"
            f"Location: {city}, {state}\n"
            f"Address: {prop.address}\n"
            f"Neighbourhood: {prop.neighbourhood}\n"
            f"Price: {format_naira(prop.annual_rent)}"
        )


    async def _send_result_selection_prompt(self, phone: str, properties: list[Property]) -> None:
        lines = [self._property_search_result_line(index, prop) for index, prop in enumerate(properties, start=1)]
        await whatsapp.send_text(
            phone,
            "Here are the available properties we found for you:\n\n"
            + "\n\n".join(lines)
            + "\n\nPlease reply with the number of the property you would like to view.",
        )

    def _media_batch_key(self, phone: str, state: str) -> str:
        return f"{MEDIA_BATCH_KEY_PREFIX}{state}:{phone}"

    def _recent_context_key(self, phone: str) -> str:
        return f"{RECENT_CONTEXT_KEY_PREFIX}{phone}"

    def _describe_listing_status(self, status: str | None) -> str:
        if status == PropertyStatus.pending_verification.value:
            return "awaiting verification"
        if status == "suspended":
            return "currently suspended pending review"
        if not status:
            return "under review"
        return status.replace("_", " ")


    def _state_instruction_text(self, state: str, data: dict) -> str:
        prompts = {
            "SEARCH_LOCATION": "Please tell us the neighbourhood, area, or location you want to search.",
            "SEARCH_BUDGET": "Please choose the budget range you would like us to work with.",
            "SEARCH_TYPE": "Please select the property type you prefer.",
            "SEARCH_BEDROOMS": "Please choose the bedroom option that matches what you want.",
            SEARCH_HIGHER_BUDGET_OFFER_STATE: "We found options above your budget. Please tell us if you want to view them or adjust your budget.",
            "VIEW_RESULTS": "Please reply with the number of the property you would like to view.",
            "VIEW_PROPERTY": "Tap Book Inspection whenever you are ready.",
            "SCHEDULE_DATE": "Please share your preferred inspection date and time. Example: 15/07/2026 10:00.",
            "SCHEDULE_CONFIRM": "Please tap Confirm when you are ready to finalize the inspection booking.",
            "LIST_TITLE": "Please share the property title.",
            "LIST_ADDRESS": "Please share the property address.",
            "LIST_NEIGHBOURHOOD": "Please share the neighbourhood and a nearby landmark for this property.",
            "LIST_CITY": "Please share the city where the property is located.",
            "LIST_STATE": "Please share the state where the property is located.",
            "LIST_TYPE": "Please select the property type.",
            "LIST_BEDROOMS": "Please choose the bedroom count for this property.",
            LIST_BEDROOMS_CUSTOM_STATE: "Please enter the exact number of bedrooms for this property.",
            "LIST_RENT": "Please enter the annual rent amount in naira.",
            "LIST_AMENITIES": "Please list the amenities, separated by commas.",
            "LIST_PHOTOS": "Please send at least 3 clear property photos or videos before we continue.",
            "LIST_DOCUMENTS": "Please upload the ownership documents for this property and reply with done when you finish.",
            "LIST_LEGAL_REP": "Please share the phone number of a legal representative for this listing.",
            "LIST_USER_NAME": "Please share your full name so we can complete the property record.",
            "LIST_USER_PHONE": "Please share your phone number so we can reach you if needed.",
        }
        return prompts.get(state, "Please continue with the current step and we will guide you.")


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

    async def _register_media_batch(self, phone: str, state: str) -> str:
        token = datetime.now(timezone.utc).isoformat()
        await self.redis.set(self._media_batch_key(phone, state), token, ex=30)
        return token

    async def _await_media_quiet_period(self, phone: str, state: str, token: str) -> bool:
        await asyncio.sleep(1.5)
        latest = await self.redis.get(self._media_batch_key(phone, state))
        return latest == token

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
            await whatsapp.send_text(phone, f"You are welcome. {summary_text} We will keep you updated here as soon as there is progress.")
            return True
        if intent == "greeting":
            await whatsapp.send_text(phone, f"Hello again. {summary_text} We will keep you updated here. If you need anything else, say menu.")
            return True
        if intent == "status_check":
            await whatsapp.send_text(phone, f"{summary_text} We will send the next update here as soon as there is progress.")
            return True
        if intent == "goodbye":
            await whatsapp.send_text(phone, f"Thank you for chatting with G & G Homes. {summary_text} We are here whenever you need us.")
            return True
        return False

    async def _handle_idle_courtesy_message(self, phone: str, input_value: str | None, active_state: str | None, current_state: str | None) -> bool:
        if active_state:
            return False
        intent = (await intent_service.detect_intent(input_value, current_state or "MAIN_MENU")).intent
        if intent == "gratitude":
            await whatsapp.send_text(phone, "You are welcome. We are here whenever you need us. Just say menu if you would like to continue.")
            return True
        if intent == "goodbye":
            await whatsapp.send_text(phone, "Thank you for chatting with G & G Homes. We are here whenever you need us. Just say menu any time.")
            return True
        return False

    async def _open_customer_service(self, phone: str, state: str, data: dict, db: AsyncSession) -> None:
        support_data = {
            "support_previous_state": state,
            "support_previous_data": data,
        }
        await self.set_data(phone, support_data)
        await self.set_state(phone, CUSTOMER_SERVICE_STATE)
        recent_context = await self._get_recent_context(phone)
        if recent_context.get("kind") == "listing_completion":
            status_text = self._describe_listing_status(recent_context.get("status"))
            await whatsapp.send_text(
                phone,
                f"Customer service is ready to help. Your most recent property listing is currently {status_text}. Tell us what you need help with, or say continue to resume your previous flow.",
            )
            return
        await whatsapp.send_text(
            phone,
            "Customer service is ready to help. Please tell us the issue you want us to help with, such as listing update, booking help, account issue, or finding a property. You can also say continue to resume your previous flow.",
        )

    async def _open_account_service(self, phone: str, user: User, db: AsyncSession) -> None:
        await self.set_state(phone, ACCOUNT_MENU_STATE)
        landlord_listings_result = await db.execute(select(Property).where(Property.landlord_id == user.id))
        landlord_listings = landlord_listings_result.scalars().all()
        active_count = sum(1 for prop in landlord_listings if prop.status == PropertyStatus.active)
        pending_count = sum(1 for prop in landlord_listings if prop.status == PropertyStatus.pending_verification)
        appointments_result = await db.execute(
            select(Appointment).where(or_(Appointment.tenant_id == user.id, Appointment.landlord_id == user.id))
        )
        appointments_count = len(appointments_result.scalars().all())

        display_name = self._display_name(user) or "there"
        await whatsapp.send_text(
            phone,
            (
                f"Account dashboard for {display_name}.\n"
                f"Listings: {len(landlord_listings)} total ({active_count} active, {pending_count} pending verification)\n"
                f"Appointments: {appointments_count}\n"
                "Choose what you want to view below."
            ),
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


    async def _handle_unexpected_media(self, phone: str, state: str, data: dict, media_types: set[str]) -> None:
        if state == "LIST_PHOTOS" and "document" in media_types:
            total_media = len(data.get("photo_urls", [])) + len(data.get("video_urls", []))
            message = "We are still collecting property photos and videos right now. Please finish sending at least 3 clear photos or videos before we move to ownership documents."
            if total_media > 0:
                message += f" We currently have {total_media}."
            await whatsapp.send_text(phone, message)
            return
        if state == "LIST_DOCUMENTS" and any(media_type in {"image", "video"} for media_type in media_types):
            await whatsapp.send_text(phone, "We are now on the ownership document step. Please send PDF, Word, or similar legal document files only. Property photos and videos cannot be accepted at this stage.")
            return
        descriptor = "document files" if media_types == {"document"} else "photos or videos" if media_types <= {"image", "video"} else "files"
        instruction = self._state_instruction_text(state, data)
        await whatsapp.send_text(phone, f"I noticed you sent {descriptor}, but we are not collecting files at this stage. {instruction}")


    async def _write_active_state(self, phone: str, state: str) -> None:
        await self.redis.set(self._state_key(phone), state, ex=settings.REDIS_STATE_TTL_SECONDS)


    async def _write_active_data(self, phone: str, data: dict) -> None:
        await self.redis.set(self._data_key(phone), json.dumps(data), ex=settings.REDIS_STATE_TTL_SECONDS)


    async def _save_resume_snapshot(self, phone: str, state: str, data: dict) -> None:
        payload = {
            "phone": phone,
            "state": state,
            "data": data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        token = self.cipher.encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")
        await self.redis.set(self._resume_key(phone), token, ex=settings.REDIS_RESUME_TTL_SECONDS)


    async def _load_resume_snapshot(self, phone: str) -> tuple[str | None, dict]:
        token = await self.redis.get(self._resume_key(phone))
        if not token:
            return None, {}
        try:
            payload = json.loads(self.cipher.decrypt(token.encode("utf-8")).decode("utf-8"))
        except (InvalidToken, ValueError, TypeError):
            await self.redis.delete(self._resume_key(phone))
            return None, {}
        if payload.get("phone") != phone:
            await self.redis.delete(self._resume_key(phone))
            return None, {}
        state = payload.get("state")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        if state:
            await self._write_active_state(phone, state)
            await self._write_active_data(phone, data)
        return state, data

    async def _get_session(self, phone: str) -> tuple[str, dict]:
        state = await self.redis.get(self._state_key(phone))
        payload = await self.redis.get(self._data_key(phone))
        if state:
            data = json.loads(payload) if payload else {}
            if data:
                await self._write_active_data(phone, data)
            await self._write_active_state(phone, state)
            return state, data
        restored_state, restored_data = await self._load_resume_snapshot(phone)
        return restored_state or "MAIN_MENU", restored_data

    async def get_state(self, phone: str) -> str:
        state, _ = await self._get_session(phone)
        return state

    async def set_state(self, phone: str, state: str) -> None:
        data = await self.get_data(phone)
        await self._write_active_state(phone, state)
        await self._save_resume_snapshot(phone, state, data)

    async def get_data(self, phone: str) -> dict:
        _, data = await self._get_session(phone)
        return data

    async def set_data(self, phone: str, data: dict) -> None:
        state = await self.get_state(phone)
        await self._write_active_data(phone, data)
        await self._save_resume_snapshot(phone, state, data)

    async def clear_session(self, phone: str) -> None:
        await self.redis.delete(
            self._state_key(phone),
            self._data_key(phone),
            self._resume_key(phone),
            self._media_batch_key(phone, "LIST_PHOTOS"),
            self._media_batch_key(phone, "LIST_DOCUMENTS"),
            f"media_accum:photo_urls:{phone}",
            f"media_accum:video_urls:{phone}",
            f"media_accum:document_urls:{phone}",
        )

    async def reset_conversation(self, phone: str, clear_recent_context: bool = False) -> None:
        await self.clear_session(phone)
        if clear_recent_context:
            await self.redis.delete(self._recent_context_key(phone))


    async def _get_or_create_user(self, phone: str, db: AsyncSession) -> User:
        phone = format_phone_number(phone)
        result = await db.execute(select(User).where(User.phone_number == phone))
        user = result.scalar_one_or_none()
        if user:
            return user
        user = User(full_name="Valued Client", phone_number=phone, role=UserRole.tenant)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    async def _start_property_search(self, phone: str) -> None:
        await self.clear_session(phone)
        await self.set_state(phone, "SEARCH_LOCATION")
        await whatsapp.send_text(phone, "Certainly. Which neighbourhood, area, or location would you like us to search for?")

    async def _start_property_listing(self, phone: str, user: User) -> None:
        await self.clear_session(phone)
        await self.set_state(phone, "LIST_TITLE")
        await self.set_data(phone, {"landlord_id": user.id})
        await whatsapp.send_text(phone, "Absolutely. Let us get your property listed. Please share the property title you would like us to use.")

    async def _send_search_budget_options(self, phone: str) -> None:
        await whatsapp.send_list(
            phone,
            "Thank you. Please choose the budget range you would like us to work with.",
            "Choose Budget",
            [{
                "title": "Budget Range",
                "rows": [
                    {"id": "budget_100000", "title": "Up to 100k"},
                    {"id": "budget_250000", "title": "Up to 250k"},
                    {"id": "budget_500000", "title": "Up to 500k"},
                    {"id": "budget_flexible", "title": "More than 500k"},
                ],
            }],
        )

    async def _send_flat_bedroom_options(self, phone: str) -> None:
        await whatsapp.send_list(
            phone,
            "Please choose the flat size you would like us to search for.",
            "Choose Size",
            [{
                "title": "Bedroom Options",
                "rows": [
                    {"id": "search_beds_1", "title": "1 Bedroom"},
                    {"id": "search_beds_2", "title": "2 Bedroom"},
                    {"id": "search_beds_3", "title": "3 Bedroom"},
                    {"id": "search_beds_4_plus", "title": "4+ Bedroom"},
                ],
            }],
        )

    async def _send_listing_bedroom_options(self, phone: str) -> None:
        await whatsapp.send_list(
            phone,
            "How many bedrooms does the property have?",
            "Choose Bedrooms",
            [{
                "title": "Bedroom Options",
                "rows": [
                    {"id": "list_beds_1", "title": "1 Bedroom"},
                    {"id": "list_beds_2", "title": "2 Bedroom"},
                    {"id": "list_beds_3", "title": "3 Bedroom"},
                    {"id": "list_beds_4_plus", "title": "4+ Bedroom"},
                ],
            }],
        )

    async def _send_search_results(self, phone: str, data: dict, db: AsyncSession) -> None:
        properties = await property_service.search(
            db,
            neighbourhood=data.get("neighbourhood"),
            max_rent=data.get("max_rent"),
            property_type=data.get("property_type"),
            bedrooms=data.get("bedrooms"),
            min_bedrooms=data.get("min_bedrooms"),
        )
        data["result_ids"] = [prop.id for prop in properties]
        data.pop("over_budget_result_ids", None)
        data.pop("over_budget_max_rent", None)
        await self.set_data(phone, data)
        if not properties:
            max_rent = data.get("max_rent")
            if max_rent is not None:
                broader_matches = await property_service.search(
                    db,
                    neighbourhood=data.get("neighbourhood"),
                    max_rent=None,
                    property_type=data.get("property_type"),
                    bedrooms=data.get("bedrooms"),
                    min_bedrooms=data.get("min_bedrooms"),
                )
                over_budget = [prop for prop in broader_matches if float(prop.annual_rent) > float(max_rent)]
                if over_budget:
                    data["over_budget_result_ids"] = [prop.id for prop in over_budget]
                    data["over_budget_max_rent"] = max_rent
                    await self.set_data(phone, data)
                    await self.set_state(phone, SEARCH_HIGHER_BUDGET_OFFER_STATE)
                    await whatsapp.send_text(
                        phone,
                        (
                            f"We could not find a verified {data.get('property_type', 'property').replace('_', ' ')} in "
                            f"{data.get('neighbourhood', 'that location')} within {format_naira(float(max_rent))}. "
                            f"However, we found {len(over_budget)} matching {self._pluralize(len(over_budget), 'property')} above your budget. "
                            "Would you like to see them? Reply yes to view, or no to search with another budget."
                        ),
                    )
                    return
            await self.set_state(phone, "VIEW_RESULTS")
            await whatsapp.send_text(phone, "We could not find a verified listing that matches that search just now. If you would like, we can help you start a fresh search immediately.")
            return
        await self.set_state(phone, "VIEW_RESULTS")
        await self._send_result_selection_prompt(phone, properties)

    async def _offer_resume_or_restart(self, phone: str, user: User, state: str, data: dict) -> None:
        if state == "MAIN_MENU":
            await self.send_main_menu(phone, user)
            return
        await self.set_data(phone, {
            "resume_target_state": state,
            "resume_target_data": data,
        })
        await self.set_state(phone, RESUME_PROMPT_STATE)
        await whatsapp.send_buttons(
            phone,
            "Good to hear from you again. We still have your previous conversation saved. Would you like us to continue where we stopped or start a fresh conversation?",
            [
                {"id": "resume_previous", "title": "Continue"},
                {"id": "resume_new", "title": "Start New"},
            ],
        )

    async def _prompt_for_state(self, phone: str, state: str, data: dict, db: AsyncSession) -> None:
        if state == "SEARCH_LOCATION":
            await whatsapp.send_text(phone, "We are continuing your property search. Which neighbourhood, area, or location would you like us to search for?")
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
            await whatsapp.send_text(phone, "We found matching properties above your budget. Reply yes to view them, or no to search with another budget.")
        elif state == "VIEW_RESULTS":
            await self._send_search_results(phone, data, db)
        elif state == "LIST_TITLE":
            await whatsapp.send_text(phone, "We are continuing your property listing. Please share the property title.")
        elif state == "LIST_ADDRESS":
            await whatsapp.send_text(phone, "Please share the property address.")
        elif state == "LIST_NEIGHBOURHOOD":
            await whatsapp.send_text(phone, "Kindly share the neighbourhood and a nearby landmark for this property.")
        elif state == "LIST_CITY":
            await whatsapp.send_text(phone, "Please share the city where the property is located.")
        elif state == "LIST_STATE":
            await whatsapp.send_text(phone, "Please share the state where the property is located.")
        elif state == "LIST_TYPE":
            await whatsapp.send_list(
                phone,
                "Please select the property type.",
                "Choose Type",
                [{"title": "Property Types", "rows": [{"id": item.value, "title": item.value.replace("_", " ").title()} for item in PropertyType if item != PropertyType.room_and_parlour]}],
            )
        elif state == "LIST_BEDROOMS":
            await self._send_listing_bedroom_options(phone)
        elif state == LIST_BEDROOMS_CUSTOM_STATE:
            await whatsapp.send_text(phone, "Please enter the exact number of bedrooms for this property, for example 4, 5, or 6.")
        elif state == "LIST_RENT":
            await whatsapp.send_text(phone, "Please enter the annual rent amount in naira. You can write it as 500000 or 500,000.")
        elif state == "LIST_AMENITIES":
            await whatsapp.send_text(phone, "Please list the amenities, separated by commas.")
        elif state == "LIST_PHOTOS":
            await whatsapp.send_text(phone, "You can now send property photos or videos. Please send at least 3 clear photos or videos of the property. When you are done, simply say done and we will proceed.")
        elif state == "LIST_DOCUMENTS":
            await whatsapp.send_text(phone, "Please upload the ownership documents for this property. Your data is secure and will not be shared with any third party. When you have uploaded the document files, reply with done.")
        elif state == "LIST_LEGAL_REP":
            await whatsapp.send_text(phone, "Please share the phone number of a legal representative we should keep on this listing.")
        elif state == "LIST_USER_NAME":
            await whatsapp.send_text(phone, "Please share your full name so we can complete the property record.")
        elif state == "LIST_USER_PHONE":
            await whatsapp.send_text(phone, "Please share your phone number so we can reach you if needed.")
        elif state == "SCHEDULE_DATE":
            await whatsapp.send_text(phone, "Please share your preferred inspection date and time. Example: 15/07/2026 10:00")
        elif state == ACCOUNT_MENU_STATE:
            await self._open_account_service(phone, await self._get_or_create_user(phone, db), db)
        elif state == ACCOUNT_EDIT_NAME_STATE:
            await whatsapp.send_text(phone, "Please send your full name in this format: Firstname Lastname.")
        elif state == ACCOUNT_EDIT_EMAIL_STATE:
            await whatsapp.send_text(phone, "Please send your email address, for example name@example.com.")
        else:
            await self.send_main_menu(phone, user=await self._get_or_create_user(phone, db))

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
        llm_reply = await conversation_service.generate_reply(
            message=input_value or "",
            current_state=state,
            state_instruction=self._state_instruction_text(state, data),
            recent_context=recent_context,
            data_context=self._conversation_data_context(state, data, recent_context),
        )
        if not llm_reply:
            return False

        if llm_reply.action == "restart":
            await self.reset_conversation(phone, clear_recent_context=True)
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

        if llm_reply.reply:
            await whatsapp.send_text(phone, llm_reply.reply)
            return True
        return False

    async def send_main_menu(self, phone: str, user: User) -> None:
        await self.set_state(phone, "MAIN_MENU")
        name = self._display_name(user)
        greeting_line = f"Welcome to G & G Homes Ltd, {name}." if name else "Welcome to G & G Homes Ltd."
        welcome_message = (
            f"{greeting_line} We are delighted to receive you and it is our pleasure to assist you with prompt, professional support in real time.\n\n"
            "Here is what we can help you with today:\n"
            "1. Search available houses and rental properties\n"
            "2. Share property details, photos, videos, and inspection options\n"
            "3. Help schedule inspection visits\n"
            "4. Guide landlords through listing a property\n"
            "5. Support account and booking-related assistance\n"
            "6. Customer service\n\n"
            "Please choose an option below, or simply tell us what you would like help with and we will guide you from there."
        )
        await whatsapp.send_list(
            phone,
            welcome_message,
            "Choose Service",
            [{
                "title": "Services",
                "rows": [
                    {"id": "search_property", "title": "Find a Property"},
                    {"id": "list_property", "title": "List Property"},
                    {"id": "my_account", "title": "My Account"},
                    {"id": "customer_service", "title": "Customer Service"},
                ],
            }],
        )

    async def process_message(
        self,
        phone: str,
        message_type: str,
        text,
        button_id,
        media_id,
        message_id: str,
        db,
        media_items: list | None = None,
        message_ids: list | None = None,
    ) -> None:
        read_ids = [mid for mid in (message_ids or [message_id]) if mid]
        for mid in read_ids:
            await whatsapp.mark_as_read(mid)
        user = await self._get_or_create_user(phone, db)
        phone = format_phone_number(phone)
        active_state = await self.redis.get(self._state_key(phone))
        state = await self.get_state(phone)
        data = await self.get_data(phone)
        input_value = button_id or (text.strip() if text else None)
        normalized = self._normalize_text(input_value)
        direct_selection = self._resolve_service_selection(input_value)
        recent_context = await self._get_recent_context(phone)

        # Global safety controls that should interrupt any flow immediately.
        if normalized in ["menu", "home", "start"]:
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, user)
            return

        if normalized in ["cancel", "stop", "back"]:
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, user)
            return

        # Guardrails for media sent in the wrong stage.
        media_items = media_items or ([{"type": message_type, "id": media_id}] if message_type in ["image", "video", "document"] and media_id else None)
        incoming_media_types = {item.get("type") for item in (media_items or []) if item.get("type")}
        if incoming_media_types:
            if state not in {"LIST_PHOTOS", "LIST_DOCUMENTS"}:
                await self._handle_unexpected_media(phone, state, data, incoming_media_types)
                return
            if state == "LIST_PHOTOS" and "document" in incoming_media_types:
                await self._handle_unexpected_media(phone, state, data, incoming_media_types)
                return
            if state == "LIST_DOCUMENTS" and any(media_type in {"image", "video"} for media_type in incoming_media_types):
                await self._handle_unexpected_media(phone, state, data, incoming_media_types)
                return

        intent = direct_selection
        if not intent:
            intent_decision = await intent_service.detect_intent(input_value if not button_id else button_id, state)
            intent = intent_decision.intent

        if intent == "restart":
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, user)
            return

        if intent == "search_property":
            await self._start_property_search(phone)
            return

        if intent == "list_property":
            await self._start_property_listing(phone, user)
            return

        if intent == "my_account":
            await self._open_account_service(phone, user, db)
            return

        if intent == "customer_service":
            await self._open_customer_service(phone, state, data, db)
            return

        # Let LLM/direct intent routing decide first; use courtesy replies only when intent is social or ambiguous.
        if intent in {"unknown", "continue", "greeting", "gratitude", "status_check", "goodbye", "clarification"}:
            if await self._handle_recent_context_message(phone, input_value, recent_context, active_state, state):
                return
            if await self._handle_idle_courtesy_message(phone, input_value, active_state, state):
                return

        if intent == "greeting" and not active_state and state != "MAIN_MENU":
            await self._offer_resume_or_restart(phone, user, state, data)
            return

        if state == RESUME_PROMPT_STATE:
            await self.handle_resume_prompt(phone, input_value, user, db, intent=intent)
            return

        # State-specific handlers keep each workflow step isolated and testable.
        handler_map = {
            "MAIN_MENU": self.handle_main_menu,
            "SEARCH_LOCATION": self.handle_search_location,
            "SEARCH_BUDGET": self.handle_search_budget,
            "SEARCH_TYPE": self.handle_search_type,
            "SEARCH_BEDROOMS": self.handle_search_bedrooms,
            SEARCH_HIGHER_BUDGET_OFFER_STATE: self.handle_search_higher_budget_offer,
            "VIEW_RESULTS": self.handle_view_results,
            "VIEW_PROPERTY": self.handle_view_property,
            "SCHEDULE_DATE": self.handle_schedule_date,
            "SCHEDULE_CONFIRM": self.handle_schedule_confirm,
            "AWAIT_PAYMENT": self.handle_await_payment,
            CUSTOMER_SERVICE_STATE: self.handle_customer_service,
            ACCOUNT_MENU_STATE: self.handle_account_menu,
            ACCOUNT_EDIT_NAME_STATE: self.handle_account_edit_name,
            ACCOUNT_EDIT_EMAIL_STATE: self.handle_account_edit_email,
            "LIST_TITLE": self.handle_list_title,
            "LIST_ADDRESS": self.handle_list_address,
            "LIST_NEIGHBOURHOOD": self.handle_list_neighbourhood,
            "LIST_CITY": self.handle_list_city,
            "LIST_STATE": self.handle_list_state,
            "LIST_TYPE": self.handle_list_type,
            "LIST_BEDROOMS": self.handle_list_bedrooms,
            LIST_BEDROOMS_CUSTOM_STATE: self.handle_list_bedrooms_custom,
            "LIST_RENT": self.handle_list_rent,
            "LIST_AMENITIES": self.handle_list_amenities,
            "LIST_PHOTOS": self.handle_list_photos,
            "LIST_DOCUMENTS": self.handle_list_documents,
            "LIST_LEGAL_REP": self.handle_list_legal_rep,
            "LIST_USER_NAME": self.handle_list_user_name,
            "LIST_USER_PHONE": self.handle_list_user_phone,
        }

        handler = handler_map.get(state, self.handle_main_menu)

        if state == "LIST_PHOTOS":
            await handler(phone, input_value, message_type, media_id, user, db, media_items, intent=intent)
            return
        if state == "LIST_DOCUMENTS":
            await handler(phone, input_value, message_type, media_id, user, db, media_items, intent=intent)
            return
        await handler(phone, input_value, message_type, media_id, user, db, intent=intent)

    async def handle_resume_prompt(self, phone: str, input_value: str | None, user: User, db: AsyncSession, intent: str | None = None) -> None:
        data = await self.get_data(phone)

        is_resume = input_value == "resume_previous" or intent == "continue"
        is_new = input_value == "resume_new" or intent == "restart"

        if is_new:
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, user)
            return
        if intent == "search_property":
            await self._start_property_search(phone)
            return
        if intent == "list_property":
            await self._start_property_listing(phone, user)
            return
        if intent == "my_account":
            await self._open_account_service(phone, user, db)
            return
        if intent == "customer_service":
            await self._open_customer_service(phone, "MAIN_MENU", data, db)
            return

        if is_resume:
            target_state = data.get("resume_target_state", "MAIN_MENU")
            target_data = data.get("resume_target_data", {})
            await self._write_active_data(phone, target_data)
            await self._write_active_state(phone, target_state)
            await self._save_resume_snapshot(phone, target_state, target_data)
            await whatsapp.send_text(phone, "Welcome back. We are continuing from where we stopped.")
            await self._prompt_for_state(phone, target_state, target_data, db)
            return
        if is_new:
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, user)
            return
        await whatsapp.send_buttons(
            phone,
            "Please choose whether you would like us to continue the previous conversation or start a new one.",
            [
                {"id": "resume_previous", "title": "Continue"},
                {"id": "resume_new", "title": "Start New"},
            ],
        )


    async def handle_main_menu(self, phone, input_value, _message_type, _media_id, user, _db, **kwargs):
        direct_selection = self._resolve_service_selection(input_value)
        intent = direct_selection or (await intent_service.detect_intent(input_value, "MAIN_MENU")).intent
        if intent == "search_property":
            await self._start_property_search(phone)
            return
        if intent == "list_property":
            await self._start_property_listing(phone, user)
            return
        if intent == "my_account":
            await self._open_account_service(phone, user, kwargs.get("db") or _db)
            return
        if intent == "customer_service":
            await whatsapp.send_text(phone, "Customer service is here to help. Tell us whether you need listing support, booking help, account help, or help finding a property.")
            return
        if await self._send_llm_conversational_reply(phone, input_value, "MAIN_MENU", await self.get_data(phone), user, kwargs.get("db") or _db, await self._get_recent_context(phone)):
            return
        await self.send_main_menu(phone, user)

    async def handle_customer_service(self, phone, input_value, _message_type, _media_id, user, db, **kwargs):
        data = await self.get_data(phone)
        previous_state = data.get("support_previous_state", "MAIN_MENU")
        previous_data = data.get("support_previous_data", {})

        intent = kwargs.get("intent") or (await intent_service.detect_intent(input_value, CUSTOMER_SERVICE_STATE)).intent

        if intent == "continue":
            await self._write_active_data(phone, previous_data)
            await self._write_active_state(phone, previous_state)
            await self._save_resume_snapshot(phone, previous_state, previous_data)
            await whatsapp.send_text(phone, "Certainly. We are resuming your previous conversation now.")
            await self._prompt_for_state(phone, previous_state, previous_data, db)
            return

        if intent == "search_property":
            await self._start_property_search(phone)
            return
        if intent == "list_property":
            await self._start_property_listing(phone, user)
            return
        if intent == "my_account":
            await self._open_account_service(phone, user, db)
            return
        if intent == "status_check":
            recent_context = await self._get_recent_context(phone)
            if await self._handle_recent_context_message(phone, input_value, recent_context, active_state=None, current_state=CUSTOMER_SERVICE_STATE):
                return
        if intent in {"gratitude", "goodbye"}:
            await whatsapp.send_text(phone, "You are welcome. If you need anything else, just say menu and we will continue from there.")
            return
        if await self._send_llm_conversational_reply(phone, input_value, CUSTOMER_SERVICE_STATE, data, user, db, await self._get_recent_context(phone)):
            return
        await whatsapp.send_text(phone, "Customer service can help with listing updates, booking questions, account support, and finding a property. Please tell us which one you need, or say continue to resume your previous conversation.")

    async def handle_account_menu(self, phone, input_value, _message_type, _media_id, user, db, **kwargs):
        selection = self._resolve_service_selection(input_value)
        if selection == "account_back_home":
            await self.send_main_menu(phone, user)
            return
        if selection == "account_profile":
            await whatsapp.send_text(
                phone,
                (
                    "Profile details:\n"
                    f"Name: {(user.full_name or 'Not set').strip()}\n"
                    f"Phone: {user.phone_number}\n"
                    f"Email: {user.email or 'Not set'}\n"
                    f"Role: {user.role.value.replace('_', ' ').title()}\n"
                    f"ID Verified: {'Yes' if user.id_verified else 'No'}\n"
                    f"Onboarding Complete: {'Yes' if user.onboarding_complete else 'No'}"
                ),
            )
            await self._open_account_service(phone, user, db)
            return
        if selection == "account_edit_profile":
            await whatsapp.send_list(
                phone,
                "What would you like to update in your profile?",
                "Edit Field",
                [{
                    "title": "Profile Update",
                    "rows": [
                        {"id": "account_edit_name", "title": "Edit Full Name"},
                        {"id": "account_edit_email", "title": "Edit Email"},
                        {"id": "account_back_home", "title": "Back To Main Menu"},
                    ],
                }],
            )
            return
        if selection == "account_edit_name":
            await self.set_state(phone, ACCOUNT_EDIT_NAME_STATE)
            await whatsapp.send_text(phone, "Please send your full name in this format: Firstname Lastname.")
            return
        if selection == "account_edit_email":
            await self.set_state(phone, ACCOUNT_EDIT_EMAIL_STATE)
            await whatsapp.send_text(phone, "Please send your email address, for example name@example.com.")
            return
        if selection == "account_listings":
            result = await db.execute(
                select(Property).where(Property.landlord_id == user.id).order_by(Property.created_at.desc())
            )
            listings = result.scalars().all()
            if not listings:
                await whatsapp.send_text(phone, "You do not have any submitted listings yet.")
            else:
                preview = listings[:8]
                lines = [
                    f"{idx}. {prop.title} | {self._describe_listing_status(prop.status.value if hasattr(prop.status, 'value') else str(prop.status))}"
                    for idx, prop in enumerate(preview, start=1)
                ]
                await whatsapp.send_text(
                    phone,
                    "Your listings:\n" + "\n".join(lines),
                )
            await self._open_account_service(phone, user, db)
            return
        if selection == "account_payments":
            result = await db.execute(
                select(Payment).where(Payment.payer_id == user.id).order_by(Payment.created_at.desc())
            )
            payments = result.scalars().all()
            if not payments:
                await whatsapp.send_text(phone, "No payment history found on your account yet.")
            else:
                preview = payments[:8]
                lines = [
                    (
                        f"{idx}. {pay.payment_type.value.replace('_', ' ').title()} | "
                        f"{format_naira(pay.gross_amount)} | {pay.status.value.replace('_', ' ').title()}"
                    )
                    for idx, pay in enumerate(preview, start=1)
                ]
                await whatsapp.send_text(phone, "Payment history:\n" + "\n".join(lines))
            await self._open_account_service(phone, user, db)
            return
        if selection == "account_subscriptions":
            result = await db.execute(
                select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.start_date.desc())
            )
            subscriptions = result.scalars().all()
            if not subscriptions:
                await whatsapp.send_text(phone, "No active subscription record found on your account yet.")
            else:
                latest = subscriptions[0]
                await whatsapp.send_text(
                    phone,
                    (
                        "Subscription status:\n"
                        f"Plan: {latest.plan.value.title()}\n"
                        f"Status: {latest.status.value.title()}\n"
                        f"Amount: {format_naira(latest.amount)}\n"
                        f"Start: {latest.start_date:%d/%m/%Y}\n"
                        f"End: {latest.end_date:%d/%m/%Y}" if latest.end_date else
                        (
                            "Subscription status:\n"
                            f"Plan: {latest.plan.value.title()}\n"
                            f"Status: {latest.status.value.title()}\n"
                            f"Amount: {format_naira(latest.amount)}\n"
                            f"Start: {latest.start_date:%d/%m/%Y}\n"
                            "End: Not set"
                        )
                    ),
                )
            await self._open_account_service(phone, user, db)
            return
        if selection == "account_appointments":
            result = await db.execute(
                select(Appointment)
                .where(or_(Appointment.tenant_id == user.id, Appointment.landlord_id == user.id))
                .order_by(Appointment.scheduled_date.desc())
            )
            appointments = result.scalars().all()
            if not appointments:
                await whatsapp.send_text(phone, "You do not have any appointments yet.")
            else:
                preview = appointments[:8]
                lines = [
                    f"{idx}. {appt.scheduled_date:%d/%m/%Y %H:%M} | {appt.status.value.replace('_', ' ')}"
                    for idx, appt in enumerate(preview, start=1)
                ]
                await whatsapp.send_text(phone, "Your appointments:\n" + "\n".join(lines))
            await self._open_account_service(phone, user, db)
            return

        intent = (await intent_service.detect_intent(input_value, ACCOUNT_MENU_STATE)).intent
        if intent == "search_property":
            await self._start_property_search(phone)
            return
        if intent == "list_property":
            await self._start_property_listing(phone, user)
            return
        if intent == "customer_service":
            await self._open_customer_service(phone, ACCOUNT_MENU_STATE, await self.get_data(phone), db)
            return
        if await self._send_llm_conversational_reply(phone, input_value, ACCOUNT_MENU_STATE, await self.get_data(phone), user, db, await self._get_recent_context(phone)):
            return
        await whatsapp.send_text(phone, "Please choose one of the account options so we can show the exact section you need.")
        await self._open_account_service(phone, user, db)

    async def handle_account_edit_name(self, phone, input_value, _message_type, _media_id, user, db, **kwargs):
        full_name = (input_value or "").strip()
        if len(full_name.split()) < 2:
            await whatsapp.send_text(phone, "Please send your full name, for example Firstname Lastname.")
            return
        user.full_name = full_name
        await db.commit()
        await db.refresh(user)
        await whatsapp.send_text(phone, f"Profile updated successfully. Your name is now {full_name}.")
        await self._open_account_service(phone, user, db)

    async def handle_account_edit_email(self, phone, input_value, _message_type, _media_id, user, db, **kwargs):
        email = (input_value or "").strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            await whatsapp.send_text(phone, "Please send a valid email address, for example name@example.com.")
            return
        user.email = email
        await db.commit()
        await db.refresh(user)
        await whatsapp.send_text(phone, f"Email updated successfully to {email}.")
        await self._open_account_service(phone, user, db)


    async def handle_search_location(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        data["neighbourhood"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "SEARCH_BUDGET")
        await self._send_search_budget_options(phone)


    async def handle_search_budget(self, phone, input_value, *_args,**kwargs):
        data = await self.get_data(phone)
        if input_value == "budget_flexible":
            data["max_rent"] = None
        else:
            data["max_rent"] = float((input_value or "budget_500000").split("_")[-1])
        await self.set_data(phone, data)
        await self.set_state(phone, "SEARCH_TYPE")
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


    async def handle_search_type(self, phone, input_value, _message_type, _media_id, _user, db,**kwargs):
        data = await self.get_data(phone)
        data["property_type"] = input_value
        data.pop("bedrooms", None)
        data.pop("min_bedrooms", None)
        await self.set_data(phone, data)
        if input_value == "flat":
            await self.set_state(phone, "SEARCH_BEDROOMS")
            await self._send_flat_bedroom_options(phone)
            return
        await self._send_search_results(phone, data, db)


    async def handle_search_bedrooms(self, phone, input_value, _message_type, _media_id, _user, db,**kwargs):
        data = await self.get_data(phone)
        data.pop("bedrooms", None)
        data.pop("min_bedrooms", None)
        if input_value == "search_beds_4_plus":
            data["min_bedrooms"] = 4
        elif input_value and input_value.startswith("search_beds_"):
            data["bedrooms"] = int(input_value.split("_")[-1])
        else:
            await whatsapp.send_text(phone, "Please choose the bedroom option that matches what you want, and we will continue the search for you.")
            return
        await self.set_data(phone, data)
        await self._send_search_results(phone, data, db)


    async def handle_search_higher_budget_offer(self, phone, input_value, _message_type, _media_id, _user, db, **kwargs):
        data = await self.get_data(phone)
        over_budget_ids = data.get("over_budget_result_ids", [])
        intent = kwargs.get("intent") or (await intent_service.detect_intent(input_value, SEARCH_HIGHER_BUDGET_OFFER_STATE)).intent
        if not over_budget_ids:
            await self.set_state(phone, "SEARCH_BUDGET")
            await self._send_search_budget_options(phone)
            return

        if intent == "continue":
            result = await db.execute(select(Property).where(Property.id.in_(over_budget_ids)))
            found = result.scalars().all()
            order_map = {pid: idx for idx, pid in enumerate(over_budget_ids)}
            properties = sorted(found, key=lambda prop: order_map.get(prop.id, 10**9))
            data["result_ids"] = [prop.id for prop in properties]
            await self.set_data(phone, data)
            await self.set_state(phone, "VIEW_RESULTS")
            await self._send_result_selection_prompt(phone, properties)
            return

        if intent == "decline":
            await self.set_state(phone, "SEARCH_BUDGET")
            await whatsapp.send_text(phone, "No problem. Let us try another budget range.")
            await self._send_search_budget_options(phone)
            return

        if await self._send_llm_conversational_reply(phone, input_value, SEARCH_HIGHER_BUDGET_OFFER_STATE, data, _user, db, await self._get_recent_context(phone)):
            return
        await whatsapp.send_text(
            phone,
            "Please reply yes to view the available options above your budget, or no to search with another budget.",
        )


    async def handle_view_results(self, phone, input_value, _message_type, _media_id, _user, db, **kwargs):
        intent = kwargs.get("intent") or (await intent_service.detect_intent(input_value, "VIEW_RESULTS")).intent
        if intent == "restart":
            await self.reset_conversation(phone, clear_recent_context=True)
            await self.send_main_menu(phone, await self._get_or_create_user(phone, db))
            return
        if intent == "search_property":
            await self._start_property_search(phone)
            return
        if not input_value or not input_value.isdigit():
            if await self._send_llm_conversational_reply(phone, input_value, "VIEW_RESULTS", await self.get_data(phone), _user, db, await self._get_recent_context(phone)):
                return
            await whatsapp.send_text(phone, "Please reply with the number of the property you would like us to open for you, or tell us if you would prefer to start a fresh search or list a property.")
            return
        data = await self.get_data(phone)
        result_ids = data.get("result_ids", [])
        index = int(input_value) - 1
        if index < 0 or index >= len(result_ids):
            await whatsapp.send_text(phone, "That property number does not match the current list. Please send one of the listed numbers and we will open it for you.")
            return
        property_id = result_ids[index]
        prop = await db.get(Property, property_id)
        data["selected_property_id"] = property_id
        await self.set_data(phone, data)
        await self.set_state(phone, "VIEW_PROPERTY")
        if prop.photo_urls:
            await whatsapp.send_image(phone, prop.photo_urls[0], prop.title)
        if prop.video_urls:
            await whatsapp.send_video(phone, prop.video_urls[0], "Property video")
        await whatsapp.send_buttons(
            phone,
            f"{prop.title}\n{prop.address}\nRent: {format_naira(prop.annual_rent)}\nAmenities: {', '.join(prop.amenities) or 'Not specified'}",
            [{"id": "schedule_visit", "title": "Book Inspection"}],
        )


    async def handle_view_property(self, phone, input_value, *_args, **kwargs):
        if input_value == "schedule_visit":
            await self.set_state(phone, "SCHEDULE_DATE")
            await whatsapp.send_text(phone, "Excellent choice. Please share your preferred inspection date and time. Example: 15/07/2026 10:00")
            return
        await whatsapp.send_text(phone, "When you are ready, tap Book Inspection and we will help you schedule the visit right away.")


    async def handle_schedule_date(self, phone, input_value, _message_type, _media_id, _user, db, **kwargs):
        try:
            scheduled_date = datetime.strptime(input_value, "%d/%m/%Y %H:%M")
        except Exception:
            await whatsapp.send_text(phone, "Please use this format for the visit date and time: DD/MM/YYYY HH:MM")
            return
        data = await self.get_data(phone)
        data["scheduled_date"] = scheduled_date.isoformat()
        prop = await db.get(Property, data["selected_property_id"])
        await self.set_data(phone, data)
        await self.set_state(phone, "SCHEDULE_CONFIRM")
        await whatsapp.send_buttons(phone, f"Kindly confirm your inspection for {prop.title} on {scheduled_date:%d/%m/%Y %H:%M}.", [{"id": "confirm_booking", "title": "Confirm"}])


    async def handle_schedule_confirm(self, phone, input_value, _message_type, _media_id, user, db, **kwargs):
        if input_value != "confirm_booking":
            await whatsapp.send_text(phone, "Please tap Confirm when you are ready, and we will finalize the inspection booking for you.")
            return
        data = await self.get_data(phone)
        prop = await db.get(Property, data["selected_property_id"])
        appointment = Appointment(
            property_id=prop.id,
            tenant_id=user.id,
            landlord_id=prop.landlord_id,
            scheduled_date=datetime.fromisoformat(data["scheduled_date"]),
            status=AppointmentStatus.confirmed,
        )
        db.add(appointment)
        await db.commit()
        await self._remember_booking_outcome(phone, data.get("scheduled_date"))
        await self.clear_session(phone)
        await whatsapp.send_text(phone, "Your inspection has been confirmed successfully. Our team has notified the landlord, and we look forward to assisting you further.")


    async def handle_await_payment(self, phone, *_args, **kwargs):
        await whatsapp.send_text(phone, "Your payment is currently being verified. We will update you as soon as confirmation is received.")


    async def handle_list_title(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        data["title"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_ADDRESS")
        await whatsapp.send_text(phone, "Thank you. Please share the property address.")


    async def handle_list_address(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        data["address"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_NEIGHBOURHOOD")
        await whatsapp.send_text(phone, "Kindly share the neighbourhood and a nearby landmark for this property.")


    async def handle_list_neighbourhood(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        data["neighbourhood"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_CITY")
        await whatsapp.send_text(phone, "Please share the city where the property is located.")


    async def handle_list_city(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        data["city"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_STATE")
        await whatsapp.send_text(phone, "Please share the state where the property is located.")


    async def handle_list_state(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        data["state"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_TYPE")
        await whatsapp.send_list(
            phone,
            "Please select the property type.",
            "Choose Type",
            [{"title": "Property Types", "rows": [{"id": item.value, "title": item.value.replace("_", " ").title()} for item in PropertyType if item != PropertyType.room_and_parlour]}],
        )


    async def handle_list_type(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        data["property_type"] = input_value
        await self.set_data(phone, data)
        if input_value in (PropertyType.office_space.value, PropertyType.warehouse.value):
            data["bedrooms"] = 0
            await self.set_data(phone, data)
            await self.set_state(phone, "LIST_RENT")
            await whatsapp.send_text(phone, "Please enter the annual rent amount in naira. You can write it as 500000 or 500,000.")
            return
        await self.set_state(phone, "LIST_BEDROOMS")
        await self._send_listing_bedroom_options(phone)


    async def handle_list_bedrooms(self, phone, input_value, *_args, **kwargs):
        data = await self.get_data(phone)
        if input_value == "list_beds_4_plus":
            await self.set_state(phone, LIST_BEDROOMS_CUSTOM_STATE)
            await whatsapp.send_text(phone, "Please enter the exact number of bedrooms for this property, for example 4, 5, or 6.")
            return
        if not input_value or not input_value.startswith("list_beds_"):
            await self._send_listing_bedroom_options(phone)
            return
        data["bedrooms"] = int(input_value.split("_")[-1])
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_RENT")
        await whatsapp.send_text(phone, "Please enter the annual rent amount in naira. You can write it as 500000 or 500,000.")


    async def handle_list_bedrooms_custom(self, phone, input_value, *_args, **kwargs):
        try:
            bedrooms = int((input_value or "").strip())
            if bedrooms < 4:
                raise ValueError
        except (TypeError, ValueError):
            await whatsapp.send_text(phone, "Please enter a valid bedroom number such as 4, 5, or 6.")
            return
        data = await self.get_data(phone)
        data["bedrooms"] = bedrooms
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_RENT")
        await whatsapp.send_text(phone, "Please enter the annual rent amount in naira. You can write it as 500000 or 500,000.")


    async def handle_list_rent(self, phone, input_value, *_args, **kwargs):
        try:
            annual_rent = parse_naira_amount(input_value or "")
        except ValueError:
            await whatsapp.send_text(phone, "Please enter the annual rent in a valid format such as 500000 or 500,000.")
            return
        data = await self.get_data(phone)
        data["annual_rent"] = annual_rent
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_AMENITIES")
        await whatsapp.send_text(phone, "Please list the amenities, separated by commas.")


    async def handle_list_amenities(self, phone, input_value, *_args, **kwargs):
        intent = kwargs.get("intent") or (await intent_service.detect_intent(input_value, "LIST_AMENITIES")).intent
        if intent == "clarification":
            await whatsapp.send_text(phone, "Amenities are the useful features that come with the property, such as water supply, prepaid meter, POP finishing, fenced compound, parking space, security, or wardrobes. Please list the available amenities, separated by commas.")
            return
        data = await self.get_data(phone)
        data["amenities"] = [item.strip() for item in input_value.split(",") if item.strip()]
        data["photo_urls"] = []
        data["video_urls"] = []
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_PHOTOS")
        await whatsapp.send_text(phone, "You can now send property photos or videos. Please send at least 3 clear photos or videos of the property. When you are done, simply say done and we will proceed.")


    async def handle_list_photos(
        self,
        phone: str,
        input_value,
        message_type: str,
        media_id,
        _user,
        _db,
        media_items: list | None = None,
        **_kwargs,
    ) -> None:
        data = await self.get_data(phone)
        photo_urls = data.get("photo_urls", [])
        video_urls = data.get("video_urls", [])
        total_media = len(photo_urls) + len(video_urls)
        intent = _kwargs.get("intent") or (await intent_service.detect_intent(input_value, "LIST_PHOTOS")).intent

        # Progress to the next step only when user explicitly confirms completion.
        if intent == "continue":
            if total_media < 3:
                await whatsapp.send_text(phone, f"We need at least 3 clear photos or videos before we can continue. So far we have received {total_media}. Please send {3 - total_media} more.")
                return
            await self.set_state(phone, "LIST_DOCUMENTS")
            await whatsapp.send_text(phone, "Perfect! Your photos look great. Now please upload the ownership documents for this property. Your data is secure and will not be shared with any third party. When you have uploaded the document files, reply with done.")
            return

        if message_type == "document":
            await whatsapp.send_text(phone, "We are still collecting property photos and videos at this stage. Please send at least 3 clear photos or videos before we move to ownership documents.")
            return

        if message_type == "text":
            msg = "Please send a property image or video."
            if total_media > 0:
                msg += f" We have {total_media} media file(s). Send at least 3 to continue, or say 'done' if you have sent enough."
            else:
                msg += " We need at least 3 to get started."
            await whatsapp.send_text(phone, msg)
            return

        if not input_value and message_type not in ["image", "video"]:
            return

        if message_type not in ["image", "video"]:
            return

        if not media_id:
            await whatsapp.send_text(phone, "We could not process that file. Please resend the property image or video.")
            return

        incoming_media = media_items or [{"type": message_type, "id": media_id}]
        existing_urls = set(photo_urls + video_urls)
        accepted_count = 0
        duplicate_count = 0
        failed_count = 0

        for item in incoming_media:
            current_type = item.get("type")
            current_id = item.get("id")
            if current_type not in ["image", "video"] or not current_id:
                failed_count += 1
                continue

            media_url = await whatsapp.get_media_url(current_id)
            if not media_url:
                failed_count += 1
                continue

            media_bytes = await whatsapp.download_media(media_url)
            if not media_bytes:
                failed_count += 1
                continue

            try:
                uploaded = await media_service.upload(media_bytes, resource_type="video" if current_type == "video" else "image")
            except Exception:
                failed_count += 1
                continue

            if not uploaded:
                failed_count += 1
                continue

            if uploaded in existing_urls:
                duplicate_count += 1
                continue

            existing_urls.add(uploaded)
            accum_key = f"media_accum:{'video_urls' if current_type == 'video' else 'photo_urls'}:{phone}"
            await self.redis.rpush(accum_key, uploaded)
            accepted_count += 1

        batch_token = await self._register_media_batch(phone, "LIST_PHOTOS")
        if not await self._await_media_quiet_period(phone, "LIST_PHOTOS", batch_token):
            return

        data = await self.get_data(phone)
        for key in ["photo_urls", "video_urls"]:
            accum_key = f"media_accum:{key}:{phone}"
            accumulated = await self.redis.lrange(accum_key, 0, -1) or []
            if accumulated:
                existing = set(data.get(key, []))
                data[key] = data.get(key, []) + [u for u in accumulated if u not in existing]
                await self.redis.delete(accum_key)
        await self.set_data(phone, data)

        photo_urls = data.get("photo_urls", [])
        video_urls = data.get("video_urls", [])
        total_media = len(photo_urls) + len(video_urls)

        if accepted_count == 0 and duplicate_count and not failed_count:
            await whatsapp.send_text(phone, "It looks like those file(s) were already received. Please send different photos or videos, or say 'done' to continue.")
            return
        if accepted_count == 0 and failed_count:
            await whatsapp.send_text(phone, "I could not save those media files. Please send them again. Once we have at least 3, you can say 'done' to continue.")
            return

        if total_media < 3:
            response = f"Got it! We received {accepted_count} new media {self._pluralize(accepted_count, 'file')}. That is {total_media} so far. Send {3 - total_media} more, then say 'done' to continue."
        elif total_media == 3:
            response = f"That brings us to {total_media} media files. You can send more or say 'done' to continue."
        else:
            response = f"Received! We now have {total_media} media files. Say 'done' whenever you are ready to continue."

        if duplicate_count:
            response += f" {duplicate_count} duplicate {self._pluralize(duplicate_count, 'file')} {'was' if duplicate_count == 1 else 'were'} skipped."
        if failed_count:
            resend_target = 'it' if failed_count == 1 else 'them'
            response += f" {failed_count} {self._pluralize(failed_count, 'file')} could not be processed, so please resend {resend_target}."
        await whatsapp.send_text(phone, response)


    async def handle_list_legal_rep(self, phone, input_value, *_args, **kwargs):
        legal_phone = format_phone_number(input_value or "")
        if len(legal_phone) < 13:
            await whatsapp.send_text(phone, "Please send a valid phone number with 11 digits, for example 08012345678.")
            return
        data = await self.get_data(phone)
        data["legal_representative_phone"] = legal_phone
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_USER_NAME")
        await whatsapp.send_text(phone, "Please share your full name so we can complete the property record.")


    async def handle_list_user_name(self, phone, input_value, *_args, **kwargs):
        full_name = (input_value or "").strip()
        if len(full_name.split()) < 2:
            await whatsapp.send_text(phone, "Please share your full name, for example Firstname Lastname.")
            return
        data = await self.get_data(phone)
        data["landlord_full_name"] = full_name
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_USER_PHONE")
        await whatsapp.send_text(phone, "Please share your phone number so we can reach you if needed.")


    async def handle_list_user_phone(self, phone, input_value, _message_type, _media_id, _user, db, **kwargs):
        user_phone = format_phone_number(input_value or "")
        if len(user_phone) < 13:
            await whatsapp.send_text(phone, "Please send a valid phone number with 11 digits, for example 08012345678.")
            return
        data = await self.get_data(phone)
        legal_phone = data.get("legal_representative_phone")
        if legal_phone and user_phone == legal_phone:
            await whatsapp.send_text(phone, "The property owner's phone number cannot be the same as the legal representative's phone number. Please provide a different number.")
            return
        data["landlord_phone_number"] = user_phone
        await self.set_data(phone, data)
        prop = Property(
            landlord_id=data["landlord_id"],
            landlord_full_name=data.get("landlord_full_name"),
            landlord_phone_number=data.get("landlord_phone_number"),
            title=data["title"],
            address=data["address"],
            neighbourhood=data["neighbourhood"],
            property_type=PropertyType(data["property_type"]),
            bedrooms=data["bedrooms"],
            amenities=data.get("amenities", []),
            annual_rent=data["annual_rent"],
            photo_urls=data.get("photo_urls", []),
            video_urls=data.get("video_urls", []),
            document_urls=data.get("document_urls", []),
            legal_representative_phone=data.get("legal_representative_phone"),
            address_matches_documents=True,
            thumbnail_url=(data.get("photo_urls") or [None])[0],
            status=PropertyStatus.pending_verification,
            is_verified=False,
        )
        db.add(prop)
        await db.commit()
        await self.clear_session(phone)
        await self._remember_listing_outcome(phone, PropertyStatus.pending_verification.value)
        await whatsapp.send_text(phone, "Thank you. Your property details, photos, and ownership documents have been submitted successfully. We will verify the details within 24 hours and send you an update here.")


    async def handle_list_documents(self, phone, input_value, message_type, media_id, _user, db, media_items=None, **_kwargs):
        data = await self.get_data(phone)
        doc_urls = data.get("document_urls", [])
        intent = _kwargs.get("intent") or (await intent_service.detect_intent(input_value, "LIST_DOCUMENTS")).intent

        if message_type == "text" and doc_urls:
            await self.set_state(phone, "LIST_LEGAL_REP")
            await whatsapp.send_text(phone, "Thank you. Please share the phone number of a legal representative we should keep on this listing.")
            return

        if intent == "continue":
            if not doc_urls:
                await whatsapp.send_text(phone, "Please upload at least one ownership document before replying with done.")
                return
            await self.set_state(phone, "LIST_LEGAL_REP")
            await whatsapp.send_text(phone, "Thank you. Please share the phone number of a legal representative we should keep on this listing.")
            return

        if not input_value and message_type != "document":
            return

        if message_type in ["image", "video"]:
            await whatsapp.send_text(phone, "This section is for ownership documents only — PDF, Word, or similar legal files. Photos and videos cannot be accepted here. Please send the document files that prove ownership of this property.")
            return

        if message_type != "document" or not media_id:
            await whatsapp.send_text(phone, "Please upload the ownership document files for this property, then reply with done when you have finished.")
            return

        # Upload all incoming documents, then use Redis RPUSH to accumulate atomically.
        # Each WhatsApp document arrives as a separate webhook call; RPUSH is atomic so
        # concurrent handlers don't overwrite each other. The quiet-period survivor merges.
        incoming_documents = media_items or [{"type": message_type, "id": media_id}]
        existing_urls = set(doc_urls)
        accepted_count = 0
        duplicate_count = 0
        failed_count = 0

        for item in incoming_documents:
            current_type = item.get("type")
            current_id = item.get("id")
            if current_type != "document" or not current_id:
                failed_count += 1
                continue
            media_url = await whatsapp.get_media_url(current_id)
            if not media_url:
                failed_count += 1
                continue
            media_bytes = await whatsapp.download_media(media_url)
            if not media_bytes:
                failed_count += 1
                continue
            uploaded = await media_service.upload(media_bytes, resource_type="raw", folder="property_documents")
            if not uploaded:
                failed_count += 1
                continue
            if uploaded in existing_urls:
                duplicate_count += 1
                continue
            existing_urls.add(uploaded)
            await self.redis.rpush(f"media_accum:document_urls:{phone}", uploaded)
            accepted_count += 1

        batch_token = await self._register_media_batch(phone, "LIST_DOCUMENTS")
        if not await self._await_media_quiet_period(phone, "LIST_DOCUMENTS", batch_token):
            return

        # Only the last-arriving handler reaches here; merge all accumulated URLs into data
        data = await self.get_data(phone)
        accum_key = f"media_accum:document_urls:{phone}"
        accumulated = await self.redis.lrange(accum_key, 0, -1) or []
        if accumulated:
            existing = set(data.get("document_urls", []))
            data["document_urls"] = data.get("document_urls", []) + [u for u in accumulated if u not in existing]
            await self.redis.delete(accum_key)
        await self.set_data(phone, data)

        if accepted_count == 0 and duplicate_count and not failed_count:
            await whatsapp.send_text(phone, "Those documents were already received. Please send different document files, or say 'done' to continue.")
            return
        if accepted_count == 0 and failed_count:
            await whatsapp.send_text(phone, "We could not save those documents. Please send them again.")
            return

        response = "Your document has been received. You may send more, or say 'done' to continue."
        if accepted_count > 1:
            response = f"We received {accepted_count} new documents. You may send more, or say 'done' to continue."
        if duplicate_count:
            response += f" {duplicate_count} duplicate {self._pluralize(duplicate_count, 'file')} {'was' if duplicate_count == 1 else 'were'} skipped."
        if failed_count:
            resend_target = 'it' if failed_count == 1 else 'them'
            response += f" {failed_count} {self._pluralize(failed_count, 'file')} could not be processed, so please resend {resend_target}."
        await whatsapp.send_text(phone, response)
