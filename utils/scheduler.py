from collections.abc import Awaitable, Callable

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler(timezone=pytz.timezone("Africa/Lagos"))


def start_scheduler(
    run_rent_reminders: Callable[[], Awaitable[None]],
    run_subscription_expiry_check: Callable[[], Awaitable[None]],
) -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        run_rent_reminders,
        trigger=CronTrigger(hour=8, minute=0),
        id="rent_reminders",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_subscription_expiry_check,
        trigger=CronTrigger(hour=9, minute=0),
        id="subscription_expiry",
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
