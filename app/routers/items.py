import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingest import run_search
from app.models import Item, SearchQuery

router = APIRouter()


@router.post("/items/{item_id}/ignore")
def ignore_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    item.status = "removed"
    item.ended_at = datetime.datetime.utcnow()
    db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/searches/{search_id}/run_now")
def run_now(search_id: int, db: Session = Depends(get_db)):
    search = db.get(SearchQuery, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    run_search(db, search)
    return RedirectResponse("/settings/searches", status_code=303)
