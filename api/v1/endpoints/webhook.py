"""Webhook endpoints that receive and validate incoming WhatsApp platform events."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.session import get_db, get_redis
from services.chatbot_engine import ChatbotEngine
from services.whatsapp_service import whatsapp

router = APIRouter()
logger = logging.getLogger(__name__)
MEDIA_MESSAGE_TYPES = {"image", "video", "document"}
MEDIA_CONTAINER_KEYS = ("media_group_id", "container_id", "parent_id", "id", "message_id")


def _media_kind(message_type: str) -> str | None:
    if message_type in {"image", "video"}:
        return "visual"
    if message_type == "document":
        return "document"
    return None


def _message_relationship_refs(message: dict) -> set[str]:
    refs: set[str] = set()
    msg_id = message.get("id")
    if msg_id:
        refs.add(str(msg_id))

    context = message.get("context") or {}
    for key in MEDIA_CONTAINER_KEYS:
        value = context.get(key)
        if value:
            refs.add(str(value))

    message_type = message.get("type", "")
    media_payload = message.get(message_type) or {}
    if isinstance(media_payload, dict):
        for key in MEDIA_CONTAINER_KEYS:
            value = media_payload.get(key)
            if value:
                refs.add(str(value))

    return refs


def _build_media_batch(messages: list[dict], start_index: int, consumed_indexes: set[int]) -> tuple[list[dict], list[str], int]:
    start_message = messages[start_index]
    phone = start_message.get("from", "")
    batch_kind = _media_kind(start_message.get("type", ""))
    if batch_kind is None:
        return [], [], start_index + 1

    media_items: list[dict] = []
    message_ids: list[str] = []
    related_refs = _message_relationship_refs(start_message)
    last_grouped_index = start_index

    for index in range(start_index, len(messages)):
        if index in consumed_indexes:
            continue
        candidate = messages[index]
        candidate_phone = candidate.get("from", "")
        candidate_type = candidate.get("type", "")
        candidate_kind = _media_kind(candidate_type)
        if candidate_phone != phone or candidate_kind != batch_kind:
            continue

        candidate_refs = _message_relationship_refs(candidate)
        same_container = bool(related_refs & candidate_refs)
        contiguous_match = index == last_grouped_index + 1
        if index != start_index and not same_container and not contiguous_match:
            continue

        candidate_media_id = (candidate.get(candidate_type) or {}).get("id")
        if candidate_media_id:
            media_items.append({"type": candidate_type, "id": candidate_media_id})
        candidate_message_id = candidate.get("id", "")
        if candidate_message_id and candidate_message_id not in message_ids:
            message_ids.append(candidate_message_id)
        related_refs.update(candidate_refs)
        consumed_indexes.add(index)
        last_grouped_index = index

    return media_items, message_ids, start_index + 1


@router.get("/whatsapp")
async def verify_whatsapp_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return int(hub_challenge or 0)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def receive_whatsapp_message(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    phone = ""
    try:
        body = await request.json()
        entry = body["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages", [])
        if not messages:
            return {"status": "ok"}

        redis = await get_redis()
        engine = ChatbotEngine(redis_client=redis)

        consumed_indexes: set[int] = set()
        index = 0
        while index < len(messages):
            if index in consumed_indexes:
                index += 1
                continue

            msg = messages[index]
            msg_id = msg.get("id", "")
            phone = msg.get("from", "")
            msg_type = msg.get("type", "")
            text = None
            button_id = None
            media_id = None
            media_items = None
            message_ids: list[str] = [msg_id] if msg_id else []

            if msg_type == "text":
                text = msg.get("text", {}).get("body", "")

            elif msg_type == "interactive":
                interactive = msg.get("interactive", {})
                itype = interactive.get("type", "")
                if itype == "button_reply":
                    button_id = interactive.get("button_reply", {}).get("id")
                elif itype == "list_reply":
                    button_id = interactive.get("list_reply", {}).get("id")

            elif msg_type in MEDIA_MESSAGE_TYPES:
                media_items, message_ids, next_index = _build_media_batch(messages, index, consumed_indexes)
                media_id = media_items[0]["id"] if media_items else None
                index = next_index

                await engine.process_message(
                    phone=phone,
                    message_type=msg_type,
                    text=text,
                    button_id=button_id,
                    media_id=media_id,
                    media_items=media_items,
                    message_id=msg_id,
                    message_ids=message_ids,
                    db=db,
                )
                continue

            await engine.process_message(
                phone=phone,
                message_type=msg_type,
                text=text,
                button_id=button_id,
                media_id=media_id,
                media_items=media_items,
                message_id=msg_id,
                message_ids=message_ids,
                db=db,
            )
            index += 1

    except Exception as exc:
        logger.error("Webhook error: %s", exc, exc_info=True)
        if phone:
            try:
                await whatsapp.send_text(
                    phone,
                    "We ran into a small issue processing that message. "
                    "Please send it again, or type menu to start over."
                )
            except Exception:
                logger.warning("Failed to send fallback message", exc_info=True)

    return {"status": "ok"}
