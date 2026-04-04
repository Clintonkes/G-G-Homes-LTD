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

        index = 0
        while index < len(messages):
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
            elif msg_type in ["image", "video", "document"]:
                batch_kind = "visual" if msg_type in ["image", "video"] else "document"
                media_items = []
                while index < len(messages):
                    batch_msg = messages[index]
                    batch_phone = batch_msg.get("from", "")
                    batch_type = batch_msg.get("type", "")
                    next_kind = "visual" if batch_type in ["image", "video"] else "document" if batch_type == "document" else None
                    if batch_phone != phone or next_kind != batch_kind:
                        break
                    batch_media_id = batch_msg.get(batch_type, {}).get("id")
                    if batch_media_id:
                        media_items.append({"type": batch_type, "id": batch_media_id})
                    batch_msg_id = batch_msg.get("id", "")
                    if batch_msg_id and batch_msg_id not in message_ids:
                        message_ids.append(batch_msg_id)
                    index += 1
                media_id = media_items[0]["id"] if media_items else None
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
                await whatsapp.send_text(phone, "We ran into a small issue while processing that last message. Please send it again, or type menu and we will guide you from the beginning.")
            except Exception:
                logger.warning("Failed to send fallback WhatsApp message", exc_info=True)
    return {"status": "ok"}
