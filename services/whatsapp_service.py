"""Wrapper around the WhatsApp Cloud API for sending messages, media, and interaction payloads."""

import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class WhatsAppService:
    def __init__(self) -> None:
        self.headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }

    async def _post(self, payload: dict) -> bool:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(settings.whatsapp_api_url, headers=self.headers, json=payload)
        if response.is_success:
            return True
        logger.warning("WhatsApp API error: %s", response.text)
        return False

    async def send_text(self, to: str, message: str) -> bool:
        return await self._post({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}})

    async def send_buttons(self, to: str, body_text: str, buttons: list[dict], header_text: str | None = None, footer_text: str | None = None) -> bool:
        action_buttons = [{"type": "reply", "reply": {"id": btn["id"], "title": btn["title"][:20]}} for btn in buttons[:3]]
        interactive: dict = {"type": "button", "body": {"text": body_text}, "action": {"buttons": action_buttons}}
        if header_text:
            interactive["header"] = {"type": "text", "text": header_text}
        if footer_text:
            interactive["footer"] = {"text": footer_text}
        return await self._post({"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": interactive})

    async def send_list(self, to: str, body_text: str, button_label: str, sections: list[dict]) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text},
                "action": {"button": button_label[:20], "sections": sections},
            },
        }
        return await self._post(payload)

    async def send_image(self, to: str, image_url: str, caption: str | None = None) -> bool:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"link": image_url}}
        if caption:
            payload["image"]["caption"] = caption
        return await self._post(payload)

    async def send_video(self, to: str, video_url: str, caption: str | None = None) -> bool:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "video", "video": {"link": video_url}}
        if caption:
            payload["video"]["caption"] = caption
        return await self._post(payload)

    async def mark_as_read(self, message_id: str) -> bool:
        return await self._post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})

    async def get_media_url(self, media_id: str) -> str | None:
        url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{media_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"})
        if response.is_success:
            return response.json().get("url")
        return None

    async def download_media(self, media_url: str) -> bytes | None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(media_url, headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"})
        if response.is_success:
            return response.content
        return None


whatsapp = WhatsAppService()
