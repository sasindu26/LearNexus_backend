"""
APScheduler wrapper — started inside FastAPI lifespan.
Schedules the AAE daily cron and any future background jobs.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    global _scheduler
    from app.services.aae import run_aae_check
    from app.services.parent_summary import run_parent_summaries

    _scheduler = BackgroundScheduler(timezone="Asia/Colombo")

    _scheduler.add_job(
        run_aae_check,
        trigger=CronTrigger(hour=settings.aae_cron_hour, minute=0),
        id="aae_daily",
        name="Anti-Abandonment Engine — daily nudge",
        replace_existing=True,
    )

    _scheduler.add_job(
        run_parent_summaries,
        trigger=CronTrigger(
            day_of_week=settings.parent_summary_day,
            hour=settings.parent_summary_hour,
            minute=0,
        ),
        id="parent_weekly",
        name="Parental summaries — weekly dispatch",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        f"Scheduler started — AAE daily at {settings.aae_cron_hour:02d}:00, "
        f"Parent summaries every {settings.parent_summary_day} "
        f"at {settings.parent_summary_hour:02d}:00 Asia/Colombo"
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
