"""Matches discovered items to a search's comp entries and scores them as deals."""
from sqlalchemy.orm import Session

from app.models import CompEntry, Item, SearchQuery


def match_comp(item_title: str, comps: list[CompEntry]) -> CompEntry | None:
    """Picks the comp entry whose match_keywords all appear in the item title
    (case-insensitive). If multiple comps match, the one with the most keywords wins,
    since it's the more specific match."""
    title_lower = item_title.lower()
    best: CompEntry | None = None
    best_score = 0
    for comp in comps:
        keywords = [k.strip().lower() for k in comp.match_keywords.split(",") if k.strip()]
        if keywords and all(k in title_lower for k in keywords):
            if len(keywords) > best_score:
                best = comp
                best_score = len(keywords)
    return best


def score_item(db: Session, item: Item, search: SearchQuery) -> None:
    """Sets item.comp_id, comp_avg_price/median, ratios, and is_deal in place. Does not commit."""
    comp = match_comp(item.title, search.comps)
    item.comp_id = comp.id if comp else None

    if not comp or comp.avg_price is None or comp.median_price is None or item.current_price is None:
        item.comp_avg_price = None
        item.comp_median_price = None
        item.ratio_to_avg = None
        item.ratio_to_median = None
        item.is_deal = False
        return

    threshold_pct = search.deal_threshold_pct
    if threshold_pct is None:
        from app.models import NotificationSettings
        ns = db.get(NotificationSettings, 1)
        threshold_pct = ns.default_deal_threshold_pct if ns else 50.0
    threshold = threshold_pct / 100.0

    item.comp_avg_price = comp.avg_price
    item.comp_median_price = comp.median_price
    item.ratio_to_avg = round(item.current_price / comp.avg_price, 4) if comp.avg_price else None
    item.ratio_to_median = round(item.current_price / comp.median_price, 4) if comp.median_price else None

    item.is_deal = bool(
        (item.ratio_to_avg is not None and item.ratio_to_avg <= threshold)
        or (item.ratio_to_median is not None and item.ratio_to_median <= threshold)
    )
