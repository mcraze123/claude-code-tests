import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import NotificationSettings, SearchQuery

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    searches = db.query(SearchQuery).order_by(SearchQuery.sort_order, SearchQuery.name).all()
    ns = db.get(NotificationSettings, 1)
    lookahead_hours = ns.ending_soon_lookahead_hours if ns else 24
    lookahead = datetime.datetime.utcnow() + datetime.timedelta(hours=lookahead_hours)

    buy_it_now_tables = []
    ending_soon_tables = []

    for search in searches:
        bin_items = sorted(
            [i for i in search.items if i.status == "active" and i.is_deal and i.listing_type == "FIXED_PRICE"],
            key=lambda i: (i.ratio_to_median if i.ratio_to_median is not None else 1),
        )
        if bin_items:
            buy_it_now_tables.append({"search": search, "listings": bin_items})

        ending_items = sorted(
            [
                i for i in search.items
                if i.status == "active" and i.is_deal and i.listing_type == "AUCTION"
                and i.end_time is not None and i.end_time <= lookahead
            ],
            key=lambda i: i.end_time,
        )
        if ending_items:
            ending_soon_tables.append({"search": search, "listings": ending_items})

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "buy_it_now_tables": buy_it_now_tables,
            "ending_soon_tables": ending_soon_tables,
            "now": datetime.datetime.utcnow(),
        },
    )
