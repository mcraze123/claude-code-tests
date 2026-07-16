import datetime
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db import SessionLocal
from app.email_service import send_daily_digest
from app.ingest import run_search
from app.models import Item, NotificationSettings, ScheduleSettings, SearchQuery
from app.pruning import prune_ended_items

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=settings.app_timezone)

SEARCH_JOB_ID = "run_all_searches"
ENDING_SOON_JOB_ID = "refresh_ending_soon"
DIGEST_JOB_ID = "send_daily_digest"
PRUNE_JOB_ID = "prune_ended_items"


def job_run_all_searches():
    db = SessionLocal()
    try:
        searches = db.query(SearchQuery).filter(SearchQuery.enabled.is_(True)).all()
        for search in searches:
            try:
                count = run_search(db, search)
                logger.info("Search '%s': %d listings processed", search.name, count)
            except Exception:
                logger.exception("Search '%s' failed", search.name)
    finally:
        db.close()


def job_refresh_ending_soon():
    """Re-fetches item detail for active auctions ending within the configured
    lookahead window, to keep bid/watcher counts fresh between full search runs."""
    from app.ebay_client import ebay_client
    from app.scoring import score_item

    db = SessionLocal()
    try:
        ns = db.get(NotificationSettings, 1)
        lookahead_hours = ns.ending_soon_lookahead_hours if ns else 24
        cutoff = datetime.datetime.utcnow() + datetime.timedelta(hours=lookahead_hours)

        items = (
            db.query(Item)
            .filter(
                Item.status == "active",
                Item.listing_type == "AUCTION",
                Item.end_time.isnot(None),
                Item.end_time <= cutoff,
            )
            .all()
        )
        for item in items:
            try:
                listing = ebay_client.get_item_detail(item.ebay_item_id)
            except Exception:
                logger.exception("Failed to refresh ending-soon item %s", item.ebay_item_id)
                continue
            if listing is None:
                item.status = "ended"
                item.ended_at = datetime.datetime.utcnow()
                continue
            item.current_price = listing.current_price
            item.bid_count = listing.bid_count
            item.watcher_count = listing.watcher_count
            item.last_seen_at = datetime.datetime.utcnow()
            score_item(db, item, item.search)
        db.commit()
    finally:
        db.close()


def job_send_daily_digest():
    db = SessionLocal()
    try:
        send_daily_digest(db)
    finally:
        db.close()


def job_prune_ended_items():
    db = SessionLocal()
    try:
        prune_ended_items(db)
    finally:
        db.close()


def _digest_trigger_hour_minute(time_str: str) -> tuple[int, int]:
    try:
        hh, mm = time_str.split(":")
        return int(hh), int(mm)
    except Exception:
        return 8, 0


def configure_jobs():
    """(Re)reads ScheduleSettings/NotificationSettings from the DB and (re)installs
    jobs with the current cadence. Called on startup and whenever settings change."""
    db = SessionLocal()
    try:
        schedule = db.get(ScheduleSettings, 1)
        ns = db.get(NotificationSettings, 1)
    finally:
        db.close()

    search_interval = schedule.search_interval_minutes if schedule else 90
    ending_soon_interval = schedule.ending_soon_refresh_minutes if schedule else 20
    digest_time = ns.daily_digest_time if ns else "08:00"
    digest_enabled = ns.daily_digest_enabled if ns else True
    hour, minute = _digest_trigger_hour_minute(digest_time)

    for job_id in (SEARCH_JOB_ID, ENDING_SOON_JOB_ID, DIGEST_JOB_ID, PRUNE_JOB_ID):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    scheduler.add_job(
        job_run_all_searches, IntervalTrigger(minutes=search_interval),
        id=SEARCH_JOB_ID, next_run_time=datetime.datetime.now(), max_instances=1,
    )
    scheduler.add_job(
        job_refresh_ending_soon, IntervalTrigger(minutes=ending_soon_interval),
        id=ENDING_SOON_JOB_ID, max_instances=1,
    )
    if digest_enabled:
        from apscheduler.triggers.cron import CronTrigger
        scheduler.add_job(
            job_send_daily_digest, CronTrigger(hour=hour, minute=minute),
            id=DIGEST_JOB_ID, max_instances=1,
        )
    scheduler.add_job(
        job_prune_ended_items, IntervalTrigger(hours=6),
        id=PRUNE_JOB_ID, max_instances=1,
    )


def start_scheduler():
    configure_jobs()
    scheduler.start()
