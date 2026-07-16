import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CompEntry, Item, NotificationSettings, SearchQuery
from app.scoring import match_comp, score_item


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(NotificationSettings(id=1, default_deal_threshold_pct=50.0))
    session.commit()
    yield session
    session.close()


def make_search_with_comp(db, avg=200.0, median=190.0, threshold_pct=None):
    search = SearchQuery(name="Test Search", deal_threshold_pct=threshold_pct)
    db.add(search)
    db.flush()
    comp = CompEntry(
        search_id=search.id,
        model_name="Widget X1",
        match_keywords="widget, x1",
        avg_price=avg,
        median_price=median,
        sample_count=3,
    )
    db.add(comp)
    db.flush()
    return search, comp


def test_match_comp_requires_all_keywords():
    comp = CompEntry(id=1, search_id=1, model_name="Widget X1", match_keywords="widget, x1")
    assert match_comp("Broken Widget X1 for parts", [comp]) is comp
    assert match_comp("Broken Widget X2 for parts", [comp]) is None


def test_match_comp_prefers_more_specific():
    generic = CompEntry(id=1, search_id=1, model_name="Widget", match_keywords="widget")
    specific = CompEntry(id=2, search_id=1, model_name="Widget X1", match_keywords="widget, x1")
    result = match_comp("Widget X1 broken for parts", [generic, specific])
    assert result is specific


def test_score_item_flags_deal_at_or_below_threshold(db):
    search, comp = make_search_with_comp(db, avg=200.0, median=200.0)
    item = Item(search_id=search.id, ebay_item_id="1", title="Widget X1 for parts",
                url="http://x", listing_type="FIXED_PRICE", current_price=100.0)
    db.add(item)
    db.flush()

    score_item(db, item, search)

    assert item.comp_id == comp.id
    assert item.ratio_to_avg == 0.5
    assert item.ratio_to_median == 0.5
    assert item.is_deal is True


def test_score_item_not_a_deal_above_threshold(db):
    search, comp = make_search_with_comp(db, avg=200.0, median=200.0)
    item = Item(search_id=search.id, ebay_item_id="2", title="Widget X1 for parts",
                url="http://x", listing_type="FIXED_PRICE", current_price=150.0)
    db.add(item)
    db.flush()

    score_item(db, item, search)

    assert item.is_deal is False


def test_score_item_deal_if_either_ratio_below_threshold(db):
    # avg says right at 50%, median says well below - OR semantics should flag it
    search, comp = make_search_with_comp(db, avg=200.0, median=400.0)
    item = Item(search_id=search.id, ebay_item_id="3", title="Widget X1 for parts",
                url="http://x", listing_type="FIXED_PRICE", current_price=100.0)
    db.add(item)
    db.flush()

    score_item(db, item, search)

    assert item.ratio_to_avg == 0.5
    assert item.ratio_to_median == 0.25
    assert item.is_deal is True


def test_score_item_no_comp_match_leaves_item_unscored(db):
    search, _ = make_search_with_comp(db)
    item = Item(search_id=search.id, ebay_item_id="4", title="Unrelated Gadget",
                url="http://x", listing_type="FIXED_PRICE", current_price=50.0)
    db.add(item)
    db.flush()

    score_item(db, item, search)

    assert item.comp_id is None
    assert item.is_deal is False


def test_score_item_uses_per_search_threshold_override(db):
    # override threshold to 80%: a 60%-of-avg price should now count as a deal
    search, comp = make_search_with_comp(db, avg=200.0, median=200.0, threshold_pct=80.0)
    item = Item(search_id=search.id, ebay_item_id="5", title="Widget X1 for parts",
                url="http://x", listing_type="FIXED_PRICE", current_price=120.0)
    db.add(item)
    db.flush()

    score_item(db, item, search)

    assert item.ratio_to_avg == 0.6
    assert item.is_deal is True
