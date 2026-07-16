from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import QueryVariant, SearchQuery

router = APIRouter(prefix="/settings/searches")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def list_searches(request: Request, db: Session = Depends(get_db)):
    searches = db.query(SearchQuery).order_by(SearchQuery.sort_order, SearchQuery.name).all()
    return templates.TemplateResponse("settings_searches.html", {"request": request, "searches": searches})


@router.post("")
def create_search(
    name: str = Form(...),
    query_text: str = Form(...),
    category_ids: str = Form(""),
    condition_ids: str = Form(""),
    min_price: str = Form(""),
    max_price: str = Form(""),
    deal_threshold_pct: str = Form(""),
    db: Session = Depends(get_db),
):
    search = SearchQuery(
        name=name.strip(),
        category_ids=category_ids.strip() or None,
        condition_ids=condition_ids.strip() or None,
        min_price=float(min_price) if min_price else None,
        max_price=float(max_price) if max_price else None,
        deal_threshold_pct=float(deal_threshold_pct) if deal_threshold_pct else None,
    )
    db.add(search)
    db.flush()
    db.add(QueryVariant(search_id=search.id, query_text=query_text.strip()))
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{search_id}/update")
def update_search(
    search_id: int,
    name: str = Form(...),
    category_ids: str = Form(""),
    condition_ids: str = Form(""),
    min_price: str = Form(""),
    max_price: str = Form(""),
    deal_threshold_pct: str = Form(""),
    enabled: str = Form(None),
    db: Session = Depends(get_db),
):
    search = db.get(SearchQuery, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    search.name = name.strip()
    search.category_ids = category_ids.strip() or None
    search.condition_ids = condition_ids.strip() or None
    search.min_price = float(min_price) if min_price else None
    search.max_price = float(max_price) if max_price else None
    search.deal_threshold_pct = float(deal_threshold_pct) if deal_threshold_pct else None
    search.enabled = enabled is not None
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{search_id}/delete")
def delete_search(search_id: int, db: Session = Depends(get_db)):
    search = db.get(SearchQuery, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    db.delete(search)
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{search_id}/variants")
def add_variant(search_id: int, query_text: str = Form(...), db: Session = Depends(get_db)):
    search = db.get(SearchQuery, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    db.add(QueryVariant(search_id=search_id, query_text=query_text.strip()))
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{search_id}/variants/{variant_id}/delete")
def delete_variant(search_id: int, variant_id: int, db: Session = Depends(get_db)):
    variant = db.get(QueryVariant, variant_id)
    if variant is None or variant.search_id != search_id:
        raise HTTPException(404, "Variant not found")
    db.delete(variant)
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)
