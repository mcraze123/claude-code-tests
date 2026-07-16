import datetime
import logging

from sqlalchemy.orm import Session

from app.models import Item, ScheduleSettings

logger = logging.getLogger(__name__)


def prune_ended_items(db: Session) -> int:
    """Hard-deletes items that have been ended/removed for longer than the
    configured retention window, keeping the DB from growing unbounded."""
    schedule = db.get(ScheduleSettings, 1)
    retention_days = schedule.prune_after_days if schedule else 14

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=retention_days)
    deleted = (
        db.query(Item)
        .filter(Item.status == "ended", Item.ended_at.isnot(None), Item.ended_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    if deleted:
        logger.info("Pruned %d ended items older than %d days", deleted, retention_days)
    return deleted
