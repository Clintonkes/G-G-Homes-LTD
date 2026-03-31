"""Media upload and transformation service for property photos and videos stored in Cloudinary."""

import asyncio
import io

import cloudinary
import cloudinary.uploader

from core.config import settings

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)


class MediaService:
    async def upload(self, file_bytes: bytes, resource_type: str = "image", folder: str = "properties") -> str | None:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cloudinary.uploader.upload(
                io.BytesIO(file_bytes),
                resource_type=resource_type,
                folder=f"rentease/{folder}",
                quality="auto",
                fetch_format="auto",
            ),
        )
        return result.get("secure_url")

    def get_thumbnail_url(self, full_url: str, width: int = 400) -> str:
        return full_url.replace("/upload/", f"/upload/w_{width},c_fill,q_auto/")


media_service = MediaService()
