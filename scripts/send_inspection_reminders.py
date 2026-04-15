"""One-off runner to send same-day inspection reminders."""

import asyncio

from database.session import get_db
from services.notification_service import notification_service


async def main() -> None:
    async for db in get_db():
        count = await notification_service.send_inspection_day_reminders(db)
        print(f"Sent {count} inspection reminders.")
        return


if __name__ == "__main__":
    asyncio.run(main())
