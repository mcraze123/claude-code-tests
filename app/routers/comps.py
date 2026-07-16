import logging

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.comps import compute_stats, get_provider
from app.db import get_db
from app.models import CompEntry, CompSample, SearchQuery

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings/searches/{search_id}/comps")


def recompute_comp_stats(comp: CompEntry) -> None:
    """Confirmed sold samples are trusted over fetched-asking-price samples: if any
    manual_sold samples exist, only those feed the average/median; otherwise falls
    back to the fetched asking-price samples as a rough estimate."""
    sold_prices = [s.price for s in comp.samples if s.source == "manual_sold"]
    prices = sold_prices if sold_prices else [s.price for s in comp.samples]
    stats = compute_stats(prices)
    comp.avg_price = stats.avg_price
    comp.median_price = stats.median_price
    comp.sample_count = stats.sample_count


@router.post("")
def create_comp(
    search_id: int,
    model_name: str = Form(...),
    match_keywords: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    search = db.get(SearchQuery, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    comp = CompEntry(
        search_id=search_id,
        model_name=model_name.strip(),
        match_keywords=match_keywords.strip(),
        notes=notes.strip() or None,
    )
    db.add(comp)
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{comp_id}/update")
def update_comp(
    search_id: int,
    comp_id: int,
    model_name: str = Form(...),
    match_keywords: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    comp = db.get(CompEntry, comp_id)
    if comp is None or comp.search_id != search_id:
        raise HTTPException(404, "Comp not found")
    comp.model_name = model_name.strip()
    comp.match_keywords = match_keywords.strip()
    comp.notes = notes.strip() or None
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{comp_id}/delete")
def delete_comp(search_id: int, comp_id: int, db: Session = Depends(get_db)):
    comp = db.get(CompEntry, comp_id)
    if comp is None or comp.search_id != search_id:
        raise HTTPException(404, "Comp not found")
    db.delete(comp)
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{comp_id}/samples")
def add_sample(
    search_id: int,
    comp_id: int,
    price: float = Form(...),
    source: str = Form("manual_sold"),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    comp = db.get(CompEntry, comp_id)
    if comp is None or comp.search_id != search_id:
        raise HTTPException(404, "Comp not found")
    db.add(CompSample(comp_id=comp_id, price=price, source=source, note=note.strip() or None))
    db.flush()
    recompute_comp_stats(comp)
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{comp_id}/samples/{sample_id}/delete")
def delete_sample(search_id: int, comp_id: int, sample_id: int, db: Session = Depends(get_db)):
    comp = db.get(CompEntry, comp_id)
    if comp is None or comp.search_id != search_id:
        raise HTTPException(404, "Comp not found")
    sample = db.get(CompSample, sample_id)
    if sample is None or sample.comp_id != comp_id:
        raise HTTPException(404, "Sample not found")
    db.delete(sample)
    db.flush()
    recompute_comp_stats(comp)
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)


@router.post("/{comp_id}/fetch_asking")
def fetch_asking_prices(search_id: int, comp_id: int, db: Session = Depends(get_db)):
    """Pre-fill helper: pulls current active 'working condition' asking prices from
    eBay as a rough starting point, clearly tagged as fetched_asking (not sold)."""
    comp = db.get(CompEntry, comp_id)
    if comp is None or comp.search_id != search_id:
        raise HTTPException(404, "Comp not found")

    keywords = [k.strip() for k in comp.match_keywords.split(",") if k.strip()]
    provider = get_provider()
    try:
        prices = provider.fetch_reference_prices(keywords)
    except NotImplementedError as e:
        logger.warning("Comp provider fetch failed: %s", e)
        return RedirectResponse("/settings/searches", status_code=303)

    # Replace prior fetched samples with the fresh batch; keep manual_sold samples intact.
    db.query(CompSample).filter(CompSample.comp_id == comp_id, CompSample.source == "fetched_asking").delete()
    for price in prices:
        db.add(CompSample(comp_id=comp_id, price=price, source="fetched_asking", note="auto-fetched asking price"))
    db.flush()
    recompute_comp_stats(comp)
    db.commit()
    return RedirectResponse("/settings/searches", status_code=303)
