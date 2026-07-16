import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


def utcnow():
    return datetime.datetime.utcnow()


class SearchQuery(Base):
    """A single named search shown as its own table on the dashboard."""
    __tablename__ = "search_queries"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    category_ids = Column(String, nullable=True)  # comma-separated eBay category IDs, optional
    condition_ids = Column(String, nullable=True)  # comma-separated eBay conditionIds filter, optional
    min_price = Column(Float, nullable=True)
    max_price = Column(Float, nullable=True)
    deal_threshold_pct = Column(Float, nullable=True)  # override global default (e.g. 50.0); null = use global
    enabled = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    variants = relationship("QueryVariant", back_populates="search", cascade="all, delete-orphan", order_by="QueryVariant.id")
    comps = relationship("CompEntry", back_populates="search", cascade="all, delete-orphan")
    items = relationship("Item", back_populates="search", cascade="all, delete-orphan")


class QueryVariant(Base):
    """One eBay Browse API `q` string belonging to a search. Multiple variants emulate
    OR-grouping that the Browse API's q param doesn't support natively - each variant is
    run as a separate API call and results are merged/deduped into the same search's table."""
    __tablename__ = "query_variants"

    id = Column(Integer, primary_key=True)
    search_id = Column(Integer, ForeignKey("search_queries.id"), nullable=False)
    query_text = Column(String, nullable=False)  # eBay Browse API q syntax: quotes, -exclude

    search = relationship("SearchQuery", back_populates="variants")


class CompEntry(Base):
    """A reference/comp price entry for a specific model within a search, e.g.
    'Alienware x17 R2' inside the 'Alienware Laptops' search. Items are matched to a
    CompEntry via match_keywords; avg/median are computed from CompSample rows."""
    __tablename__ = "comp_entries"

    id = Column(Integer, primary_key=True)
    search_id = Column(Integer, ForeignKey("search_queries.id"), nullable=False)
    model_name = Column(String, nullable=False)
    match_keywords = Column(String, nullable=False)  # comma-separated; item title must contain all to match
    notes = Column(Text, nullable=True)
    avg_price = Column(Float, nullable=True)
    median_price = Column(Float, nullable=True)
    sample_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    search = relationship("SearchQuery", back_populates="comps")
    samples = relationship("CompSample", back_populates="comp", cascade="all, delete-orphan")
    items = relationship("Item", back_populates="comp")


class CompSample(Base):
    """A single reference price sample feeding a CompEntry's avg/median. `source`
    distinguishes user-confirmed sold prices from auto-fetched current asking prices
    (asking-price samples are only used as a fallback when no sold samples exist)."""
    __tablename__ = "comp_samples"

    id = Column(Integer, primary_key=True)
    comp_id = Column(Integer, ForeignKey("comp_entries.id"), nullable=False)
    price = Column(Float, nullable=False)
    source = Column(String, default="manual_sold", nullable=False)  # manual_sold | fetched_asking
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    comp = relationship("CompEntry", back_populates="samples")


class Item(Base):
    """A live eBay listing discovered by a search run."""
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    search_id = Column(Integer, ForeignKey("search_queries.id"), nullable=False)
    comp_id = Column(Integer, ForeignKey("comp_entries.id"), nullable=True)

    ebay_item_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    image_url = Column(String, nullable=True)

    listing_type = Column(String, nullable=False)  # FIXED_PRICE | AUCTION
    current_price = Column(Float, nullable=True)
    currency = Column(String, default="USD")
    bid_count = Column(Integer, nullable=True)
    watcher_count = Column(Integer, nullable=True)

    condition = Column(String, nullable=True)
    description_snippet = Column(Text, nullable=True)
    seller_username = Column(String, nullable=True)

    listed_at = Column(DateTime, nullable=True)  # eBay itemCreationDate, when available
    end_time = Column(DateTime, nullable=True)  # auction/listing end time, when available

    comp_avg_price = Column(Float, nullable=True)  # snapshot at time of last scoring
    comp_median_price = Column(Float, nullable=True)
    ratio_to_avg = Column(Float, nullable=True)
    ratio_to_median = Column(Float, nullable=True)
    is_deal = Column(Boolean, default=False, nullable=False)

    status = Column(String, default="active", nullable=False)  # active | ended | removed
    first_seen_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)
    ended_at = Column(DateTime, nullable=True)
    missing_streak = Column(Integer, default=0, nullable=False)  # consecutive search runs where item no longer appeared

    search = relationship("SearchQuery", back_populates="items")
    comp = relationship("CompEntry", back_populates="items")

    __table_args__ = (UniqueConstraint("search_id", "ebay_item_id", name="uq_search_item"),)


class NotificationSettings(Base):
    """Singleton row (id=1) holding the editable notification schedule/config."""
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, default=1)
    recipient_email = Column(String, nullable=True)
    daily_digest_enabled = Column(Boolean, default=True, nullable=False)
    daily_digest_time = Column(String, default="08:00", nullable=False)  # HH:MM, app_timezone
    ending_soon_lookahead_hours = Column(Integer, default=24, nullable=False)
    default_deal_threshold_pct = Column(Float, default=50.0, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ScheduleSettings(Base):
    """Singleton row (id=1) holding the editable search-run cadence."""
    __tablename__ = "schedule_settings"

    id = Column(Integer, primary_key=True, default=1)
    search_interval_minutes = Column(Integer, default=90, nullable=False)
    ending_soon_refresh_minutes = Column(Integer, default=20, nullable=False)
    prune_after_days = Column(Integer, default=14, nullable=False)  # hard-delete ended items after this long
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
