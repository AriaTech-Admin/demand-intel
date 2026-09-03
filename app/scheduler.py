"""Scheduled refresh. Avoids unnecessary external API calls by running the
full pipeline on a fixed interval (config.REFRESH_INTERVAL_MINUTES)."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import config
from .pipeline.refresh import run_refresh

log = logging.getLogger(__name__)
_scheduler = None


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_refresh, "interval",
                       minutes=config.REFRESH_INTERVAL_MINUTES,
                       id="refresh_pipeline", replace_existing=True)
    _scheduler.start()
    log.info("Refresh scheduler started (every %s min)", config.REFRESH_INTERVAL_MINUTES)


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
        log.info("Refresh scheduler stopped")
