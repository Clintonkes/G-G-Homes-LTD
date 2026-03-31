"""Conversation engine for WhatsApp interactions, including state management, intent handling, and guided user flows."""

import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Appointment, AppointmentStatus, Property, PropertyStatus, PropertyType, User, UserRole
from services.intent_service import intent_service
from services.media_service import media_service
from services.property_service import property_service
from services.whatsapp_service import whatsapp
from utils.helpers import format_naira, format_phone_number

STATE_KEY_PREFIX = "state:"
DATA_KEY_PREFIX = "data:"
RESUME_KEY_PREFIX = "resume:"
RESUME_PROMPT_STATE = "RESUME_PROMPT"
SEARCH_FLOW_STATES = {"SEARCH_LOCATION", "SEARCH_BUDGET", "SEARCH_TYPE", "SEARCH_BEDROOMS", "VIEW_RESULTS", "VIEW_PROPERTY", "SCHEDULE_DATE", "SCHEDULE_CONFIRM"}
LISTING_FLOW_STATES = {"LIST_TITLE", "LIST_ADDRESS", "LIST_NEIGHBOURHOOD", "LIST_TYPE", "LIST_BEDROOMS", "LIST_RENT", "LIST_AMENITIES", "LIST_PHOTOS"}


class ChatbotEngine:
    """Coordinates conversation state and user actions for the WhatsApp assistant."""

    def __init__(self, redis_client) -> None:
        self.redis = redis_client
        digest = hashlib.sha256(b"gghomes-session-key").digest()
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

    def _is_done_message(self, input_value: str | None) -> bool:
        normalized = self._normalize_text(input_value)
        return normalized in {"done", "finished", "i am done", "that is all", "that's all", "complete"}

    def _is_greeting(self, input_value: str | None) -> bool:
        normalized = self._normalize_text(input_value)
        greetings = ["hello", "hi", "hey", "good morning", "good afternoon", "good evening", "good day"]
        return any(normalized.startswith(greeting) for greeting in greetings)

    async def _write_active_state(self, phone: str, state: str) -> None:
        await self.redis.set(self._state_key(phone), state, ex=3600)

    async def _write_active_data(self, phone: str, data: dict) -> None:
        await self.redis.set(self._data_key(phone), json.dumps(data), ex=3600)

    async def _save_resume_snapshot(self, phone: str, state: str, data: dict) -> None:
        payload = {
            "phone": phone,
            "state": state,
            "data": data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        token = self.cipher.encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")
        await self.redis.set(self._resume_key(phone), token, ex=2592000)

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
        await self.redis.delete(self._state_key(phone), self._data_key(phone), self._resume_key(phone))

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
        await self.set_data(phone, data)
        await self.set_state(phone, "VIEW_RESULTS")
        if not properties:
            await whatsapp.send_text(phone, "We could not find a verified listing that matches that search just now. If you would like, we can help you start a fresh search immediately.")
            return
        lines = [f"{index}. {prop.title} - {format_naira(prop.annual_rent)}" for index, prop in enumerate(properties, start=1)]
        await whatsapp.send_text(phone, "Here are the available properties we found for you:\n" + "\n".join(lines) + "\n\nPlease reply with the number of the property you would like to view.")

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
                        {"id": "room_and_parlour", "title": "Room and Parlour"},
                        {"id": "flat", "title": "Flat"},
                        {"id": "duplex", "title": "Duplex"},
                        {"id": "bungalow", "title": "Bungalow"},
                    ],
                }],
            )
        elif state == "SEARCH_BEDROOMS":
            await self._send_flat_bedroom_options(phone)
        elif state == "VIEW_RESULTS":
            await self._send_search_results(phone, data, db)
        elif state == "LIST_TITLE":
            await whatsapp.send_text(phone, "We are continuing your property listing. Please share the property title.")
        elif state == "LIST_ADDRESS":
            await whatsapp.send_text(phone, "Please share the property address.")
        elif state == "LIST_NEIGHBOURHOOD":
            await whatsapp.send_text(phone, "Kindly share the neighbourhood or area for this property.")
        elif state == "LIST_TYPE":
            await whatsapp.send_list(
                phone,
                "Please select the property type.",
                "Choose Type",
                [{"title": "Property Types", "rows": [{"id": item.value, "title": item.value.replace("_", " ").title()} for item in PropertyType]}],
            )
        elif state == "LIST_BEDROOMS":
            await whatsapp.send_buttons(phone, "How many bedrooms does the property have?", [{"id": f"beds_{i}", "title": f"{i} Bedroom"} for i in (1, 2, 3)])
        elif state == "LIST_RENT":
            await whatsapp.send_text(phone, "Please enter the annual rent amount in naira.")
        elif state == "LIST_AMENITIES":
            await whatsapp.send_text(phone, "Please list the amenities, separated by commas.")
        elif state == "LIST_PHOTOS":
            await whatsapp.send_text(phone, "You can now send property photos or videos. When you are done, simply say done and we will proceed.")
        elif state == "SCHEDULE_DATE":
            await whatsapp.send_text(phone, "Please share your preferred inspection date and time. Example: 15/07/2026 10:00")
        else:
            await self.send_main_menu(phone, user=await self._get_or_create_user(phone, db))

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
            "5. Support account and booking-related assistance\n\n"
            "Please choose an option below, or simply tell us what you would like help with and we will guide you from there."
        )
        await whatsapp.send_buttons(
            phone,
            welcome_message,
            [
                {"id": "search_property", "title": "Find a Home"},
                {"id": "list_property", "title": "List Property"},
                {"id": "my_account", "title": "My Account"},
            ],
        )

    async def process_message(self, phone, message_type, text, button_id, media_id, message_id, db):
        if message_id:
            await whatsapp.mark_as_read(message_id)
        user = await self._get_or_create_user(phone, db)
        phone = format_phone_number(phone)
        state = await self.get_state(phone)
        data = await self.get_data(phone)
        input_value = button_id or (text.strip() if text else None)
        normalized = self._normalize_text(input_value)
        intent_decision = await intent_service.detect_intent(input_value if not button_id else button_id, state)
        intent = intent_decision.intent

        if self._is_greeting(input_value):
            await self._offer_resume_or_restart(phone, user, state, data)
            return

        if normalized in ["menu", "home", "start"]:
            await self.clear_session(phone)
            await self.send_main_menu(phone, user)
            return

        if normalized in ["cancel", "stop", "back"]:
            await self.clear_session(phone)
            await self.send_main_menu(phone, user)
            return

        if state == RESUME_PROMPT_STATE:
            await self.handle_resume_prompt(phone, input_value, user, db)
            return

        if intent == "search_property" and state not in SEARCH_FLOW_STATES and state not in LISTING_FLOW_STATES:
            await self._start_property_search(phone)
            return

        if intent == "list_property" and state not in LISTING_FLOW_STATES:
            await self._start_property_listing(phone, user)
            return

        if intent == "my_account" and state == "MAIN_MENU":
            await whatsapp.send_text(phone, "We can help with account and booking support. Please tell us what you would like to check, or choose an option below to continue.")
            await self.send_main_menu(phone, user)
            return

        handler_map = {
            "MAIN_MENU": self.handle_main_menu,
            "SEARCH_LOCATION": self.handle_search_location,
            "SEARCH_BUDGET": self.handle_search_budget,
            "SEARCH_TYPE": self.handle_search_type,
            "SEARCH_BEDROOMS": self.handle_search_bedrooms,
            "VIEW_RESULTS": self.handle_view_results,
            "VIEW_PROPERTY": self.handle_view_property,
            "SCHEDULE_DATE": self.handle_schedule_date,
            "SCHEDULE_CONFIRM": self.handle_schedule_confirm,
            "AWAIT_PAYMENT": self.handle_await_payment,
            "LIST_TITLE": self.handle_list_title,
            "LIST_ADDRESS": self.handle_list_address,
            "LIST_NEIGHBOURHOOD": self.handle_list_neighbourhood,
            "LIST_TYPE": self.handle_list_type,
            "LIST_BEDROOMS": self.handle_list_bedrooms,
            "LIST_RENT": self.handle_list_rent,
            "LIST_AMENITIES": self.handle_list_amenities,
            "LIST_PHOTOS": self.handle_list_photos,
        }
        handler = handler_map.get(state, self.handle_main_menu)
        await handler(phone, input_value, message_type, media_id, user, db)

    async def handle_resume_prompt(self, phone: str, input_value: str | None, user: User, db: AsyncSession) -> None:
        data = await self.get_data(phone)
        if input_value == "resume_previous":
            target_state = data.get("resume_target_state", "MAIN_MENU")
            target_data = data.get("resume_target_data", {})
            await self._write_active_data(phone, json.dumps(target_data) if False else target_data)
            await self.set_data(phone, target_data)
            await self.set_state(phone, target_state)
            await whatsapp.send_text(phone, "Welcome back. We are continuing from where we stopped.")
            await self._prompt_for_state(phone, target_state, target_data, db)
            return
        if input_value == "resume_new":
            await self.clear_session(phone)
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

    async def handle_main_menu(self, phone, input_value, _message_type, _media_id, user, _db):
        intent = (await intent_service.detect_intent(input_value, "MAIN_MENU")).intent
        if intent == "search_property":
            await self._start_property_search(phone)
            return
        if intent == "list_property":
            await self._start_property_listing(phone, user)
            return
        if intent == "my_account":
            await whatsapp.send_text(phone, "We can help with account and booking support. Please tell us what you would like to check, or choose an option below to continue.")
            await self.send_main_menu(phone, user)
            return
        await whatsapp.send_text(phone, "We are here to help. Please choose one of the options below, or tell us whether you would like to find a home, list a property, or check your account.")
        await self.send_main_menu(phone, user)

    async def handle_search_location(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["neighbourhood"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "SEARCH_BUDGET")
        await self._send_search_budget_options(phone)

    async def handle_search_budget(self, phone, input_value, *_args):
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
                    {"id": "room_and_parlour", "title": "Room and Parlour"},
                    {"id": "flat", "title": "Flat"},
                    {"id": "duplex", "title": "Duplex"},
                    {"id": "bungalow", "title": "Bungalow"},
                ],
            }],
        )

    async def handle_search_type(self, phone, input_value, _message_type, _media_id, _user, db):
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

    async def handle_search_bedrooms(self, phone, input_value, _message_type, _media_id, _user, db):
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

    async def handle_view_results(self, phone, input_value, _message_type, _media_id, _user, db):
        if not input_value or not input_value.isdigit():
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

    async def handle_view_property(self, phone, input_value, *_args):
        if input_value == "schedule_visit":
            await self.set_state(phone, "SCHEDULE_DATE")
            await whatsapp.send_text(phone, "Excellent choice. Please share your preferred inspection date and time. Example: 15/07/2026 10:00")
            return
        await whatsapp.send_text(phone, "When you are ready, tap Book Inspection and we will help you schedule the visit right away.")

    async def handle_schedule_date(self, phone, input_value, _message_type, _media_id, _user, db):
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

    async def handle_schedule_confirm(self, phone, input_value, _message_type, _media_id, user, db):
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
        await self.clear_session(phone)
        await whatsapp.send_text(phone, "Your inspection has been confirmed successfully. Our team has notified the landlord, and we look forward to assisting you further.")

    async def handle_await_payment(self, phone, *_args):
        await whatsapp.send_text(phone, "Your payment is currently being verified. We will update you as soon as confirmation is received.")

    async def handle_list_title(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["title"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_ADDRESS")
        await whatsapp.send_text(phone, "Thank you. Please share the property address.")

    async def handle_list_address(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["address"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_NEIGHBOURHOOD")
        await whatsapp.send_text(phone, "Kindly share the neighbourhood or area for this property.")

    async def handle_list_neighbourhood(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["neighbourhood"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_TYPE")
        await whatsapp.send_list(
            phone,
            "Please select the property type.",
            "Choose Type",
            [{"title": "Property Types", "rows": [{"id": item.value, "title": item.value.replace("_", " ").title()} for item in PropertyType]}],
        )

    async def handle_list_type(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["property_type"] = input_value
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_BEDROOMS")
        await whatsapp.send_buttons(phone, "How many bedrooms does the property have?", [{"id": f"beds_{i}", "title": f"{i} Bedroom"} for i in (1, 2, 3)])

    async def handle_list_bedrooms(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["bedrooms"] = int((input_value or "beds_1").split("_")[-1])
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_RENT")
        await whatsapp.send_text(phone, "Please enter the annual rent amount in naira.")

    async def handle_list_rent(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["annual_rent"] = float(input_value)
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_AMENITIES")
        await whatsapp.send_text(phone, "Please list the amenities, separated by commas.")

    async def handle_list_amenities(self, phone, input_value, *_args):
        data = await self.get_data(phone)
        data["amenities"] = [item.strip() for item in input_value.split(",") if item.strip()]
        data["photo_urls"] = []
        data["video_urls"] = []
        await self.set_data(phone, data)
        await self.set_state(phone, "LIST_PHOTOS")
        await whatsapp.send_text(phone, "You can now send property photos or videos. When you are done, simply say done and we will proceed.")

    async def handle_list_photos(self, phone, input_value, message_type, media_id, _user, db):
        data = await self.get_data(phone)
        if self._is_done_message(input_value):
            prop = Property(
                landlord_id=data["landlord_id"],
                title=data["title"],
                address=data["address"],
                neighbourhood=data["neighbourhood"],
                property_type=PropertyType(data["property_type"]),
                bedrooms=data["bedrooms"],
                amenities=data.get("amenities", []),
                annual_rent=data["annual_rent"],
                photo_urls=data.get("photo_urls", []),
                video_urls=data.get("video_urls", []),
                thumbnail_url=(data.get("photo_urls") or [None])[0],
                status=PropertyStatus.pending_verification,
                is_verified=False,
            )
            db.add(prop)
            await db.commit()
            await self.clear_session(phone)
            await whatsapp.send_text(phone, "Thank you. Your property has been saved and submitted for verification. Our team will review it and keep you updated.")
            return
        if message_type not in ["image", "video"] or not media_id:
            await whatsapp.send_text(phone, "Please send an image or video, or simply say done when you are finished uploading the media.")
            return
        media_url = await whatsapp.get_media_url(media_id)
        media_bytes = await whatsapp.download_media(media_url) if media_url else None
        if not media_bytes:
            await whatsapp.send_text(phone, "We could not download that media just yet. Please send it again and we will try immediately.")
            return
        uploaded = await media_service.upload(media_bytes, resource_type="video" if message_type == "video" else "image")
        if not uploaded:
            await whatsapp.send_text(phone, "We could not upload that media at the moment. Please try again and we will continue from there.")
            return
        key = "video_urls" if message_type == "video" else "photo_urls"
        data.setdefault(key, []).append(uploaded)
        await self.set_data(phone, data)
        await whatsapp.send_text(phone, f"Your {message_type} has been received successfully. You may send more, or simply say done when you are ready for us to proceed.")
