import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import SessionLocal, init_db
from app.models import NotificationSettings, ScheduleSettings
from app.routers import comps, dashboard, items, notifications, searches
from app.scheduler import start_scheduler
from app.seed.default_searches import seed_default_searches

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="eBay Deal Finder")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard.router)
app.include_router(searches.router)
app.include_router(comps.router)
app.include_router(notifications.router)
app.include_router(items.router)


def _ensure_singleton_rows():
    db = SessionLocal()
    try:
        if db.get(NotificationSettings, 1) is None:
            db.add(NotificationSettings(id=1))
        if db.get(ScheduleSettings, 1) is None:
            db.add(ScheduleSettings(id=1))
        db.commit()
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    init_db()
    _ensure_singleton_rows()
    db = SessionLocal()
    try:
        seed_default_searches(db)
    finally:
        db.close()
    start_scheduler()
