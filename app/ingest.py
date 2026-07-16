"""Runs eBay searches, upserts Items, scores them against comps, and tracks
disappearance for pruning."""
import logging

from sqlalchemy.orm import Session

from app.ebay_client import EbayListing, ebay_client
from app.models import Item, SearchQuery
from app.scoring import score_item

logger = logging.getLogger(__name__)


def run_search(db: Session, search: SearchQuery) -> int:
    """Runs every query variant for this search across both buying-option types,
    upserts results, scores them, and marks items that disappeared. Returns the
    number of items touched."""
    seen_ebay_ids: set[str] = set()
    touched = 0

    variants = search.variants or []
    if not variants:
        return 0

    for variant in variants:
        for buying_options in (["FIXED_PRICE"], ["AUCTION"]):
            try:
                listings = ebay_client.search(
                    variant.query_text,
                    buying_options=buying_options,
                    category_ids=search.category_ids,
                    condition_ids=search.condition_ids,
                    min_price=search.min_price,
                    max_price=search.max_price,
                )
            except Exception:
                logger.exception(
                    "eBay search failed for search=%s variant=%r options=%s",
                    search.name, variant.query_text, buying_options,
                )
                continue

            for listing in listings:
                seen_ebay_ids.add(listing.ebay_item_id)
                _upsert_item(db, search, listing)
                touched += 1

    _mark_missing(db, search, seen_ebay_ids)
    db.commit()
    return touched


def _upsert_item(db: Session, search: SearchQuery, listing: EbayListing) -> None:
    item = (
        db.query(Item)
        .filter(Item.search_id == search.id, Item.ebay_item_id == listing.ebay_item_id)
        .first()
    )
    if item is None:
        item = Item(search_id=search.id, ebay_item_id=listing.ebay_item_id)
        db.add(item)

    item.title = listing.title
    item.url = listing.url
    item.image_url = listing.image_url
    item.listing_type = listing.listing_type
    item.current_price = listing.current_price
    item.currency = listing.currency
    item.bid_count = listing.bid_count
    item.watcher_count = listing.watcher_count
    item.condition = listing.condition
    item.description_snippet = listing.description_snippet
    item.seller_username = listing.seller_username
    item.listed_at = listing.listed_at
    item.end_time = listing.end_time
    item.status = "active"
    item.missing_streak = 0
    from app.models import utcnow
    item.last_seen_at = utcnow()

    score_item(db, item, search)


def _mark_missing(db: Session, search: SearchQuery, seen_ebay_ids: set[str]) -> None:
    """Items that were active but no longer show up in search results get a
    missing_streak bump. Two consecutive misses (or a passed end_time) marks them
    ended - single misses can just be transient API pagination/ranking noise."""
    import datetime
    from app.models import utcnow

    active_items = (
        db.query(Item)
        .filter(Item.search_id == search.id, Item.status == "active")
        .all()
    )
    for item in active_items:
        if item.ebay_item_id in seen_ebay_ids:
            continue
        item.missing_streak += 1
        end_passed = item.end_time is not None and item.end_time < datetime.datetime.utcnow()
        if item.missing_streak >= 2 or end_passed:
            item.status = "ended"
            item.ended_at = utcnow()
