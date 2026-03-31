"""Webhook endpoints that receive and validate incoming WhatsApp platform events."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.session import get_db, get_redis
from services.chatbot_engine import ChatbotEngine

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
    try:
        body = await request.json()
        entry = body["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages", [])
        if not messages:
            return {"status": "ok"}

        msg = messages[0]
        msg_id = msg.get("id", "")
        phone = msg.get("from", "")
        msg_type = msg.get("type", "")
        text = None
        button_id = None
        media_id = None

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            itype = interactive.get("type", "")
            if itype == "button_reply":
                button_id = interactive.get("button_reply", {}).get("id")
            elif itype == "list_reply":
                button_id = interactive.get("list_reply", {}).get("id")
        elif msg_type in ["image", "video"]:
            media_id = msg.get(msg_type, {}).get("id")

        redis = await get_redis()
        engine = ChatbotEngine(redis_client=redis)
        await engine.process_message(phone=phone, message_type=msg_type, text=text, button_id=button_id, media_id=media_id, message_id=msg_id, db=db)
    except Exception as exc:
        logger.error("Webhook error: %s", exc, exc_info=True)
    return {"status": "ok"}
