from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import NotificationSettings, ScheduleSettings
from app.scheduler import configure_jobs

router = APIRouter(prefix="/settings/notifications")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def view_notifications(request: Request, db: Session = Depends(get_db)):
    ns = db.get(NotificationSettings, 1)
    schedule = db.get(ScheduleSettings, 1)
    return templates.TemplateResponse(
        "settings_notifications.html", {"request": request, "ns": ns, "schedule": schedule}
    )


@router.post("")
def update_notifications(
    recipient_email: str = Form(""),
    daily_digest_enabled: str = Form(None),
    daily_digest_time: str = Form("08:00"),
    ending_soon_lookahead_hours: int = Form(24),
    default_deal_threshold_pct: float = Form(50.0),
    search_interval_minutes: int = Form(90),
    ending_soon_refresh_minutes: int = Form(20),
    prune_after_days: int = Form(14),
    db: Session = Depends(get_db),
):
    ns = db.get(NotificationSettings, 1)
    ns.recipient_email = recipient_email.strip() or None
    ns.daily_digest_enabled = daily_digest_enabled is not None
    ns.daily_digest_time = daily_digest_time.strip()
    ns.ending_soon_lookahead_hours = ending_soon_lookahead_hours
    ns.default_deal_threshold_pct = default_deal_threshold_pct

    schedule = db.get(ScheduleSettings, 1)
    schedule.search_interval_minutes = max(15, search_interval_minutes)
    schedule.ending_soon_refresh_minutes = max(5, ending_soon_refresh_minutes)
    schedule.prune_after_days = max(1, prune_after_days)

    db.commit()
    configure_jobs()
    return RedirectResponse("/settings/notifications", status_code=303)
