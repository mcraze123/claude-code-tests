"""Seeds the starter set of searches on first run. Every row here is just an
ordinary, fully-editable SearchQuery - there's no hardcoded category logic in the
app itself, so add/edit/delete freely from Settings > Searches afterward.

Each search combines a few base terms with a few "signal" phrases (parts / repair /
"not working") as separate QueryVariant rows, since the Browse API's `q` param
doesn't support (a,b) OR-grouping the way eBay's website search box does - running
one call per variant and merging results into the same table gets the same effect.
Comp entries are intentionally left empty: seeding fabricated price data would be
worse than no data, so add real reference prices for the specific models you care
about from Settings > Searches once the app is running.
"""
from sqlalchemy.orm import Session

from app.models import QueryVariant, SearchQuery

SIGNALS = ["parts", "repair", '"not working"']


def _variants(base_terms: list[str]) -> list[str]:
    return [f"{term} {signal}" for term in base_terms for signal in SIGNALS]


DEFAULT_SEARCHES: list[dict] = [
    {
        "name": "Laptops (Alienware / MacBook / Gaming)",
        "base_terms": ["alienware laptop", "macbook", "gaming laptop"],
    },
    {
        "name": "Car Amps & Vintage/Tube Audio",
        "base_terms": ["alpine amplifier", "vintage stereo receiver", "tube amplifier"],
    },
    {
        "name": "Tools & Test Equipment",
        "base_terms": ["power tool", "oscilloscope", "soldering station"],
    },
    {
        # Only smartwatch brands are searched (never generic "watch"), which is what
        # keeps traditional watches out without any special-case filtering code.
        "name": "Smart Watches (Apple / Android only)",
        "base_terms": ["apple watch", "samsung galaxy watch", "wear os watch"],
    },
    {
        "name": "Networking & Server Gear",
        "base_terms": ["synology nas", "ubiquiti unifi", "server rack"],
    },
    {
        "name": "3D Printers (Bambu / Prusa)",
        "base_terms": ["bambu lab printer", "prusa printer", "3d printer"],
    },
    {
        "name": "VCR & VCR/DVD Combo Units",
        "base_terms": ["vcr dvd combo", "vcr player"],
    },
    {
        "name": "Game Consoles",
        "base_terms": ["ps5 console", "xbox series x", "nintendo switch"],
    },
    {
        "name": "Camera & Camcorder Gear",
        "base_terms": ["dslr camera", "camcorder", "mirrorless camera"],
    },
    {
        "name": "Robot Vacuums & Small Appliances",
        "base_terms": ["robot vacuum", "espresso machine"],
    },
]


def seed_default_searches(db: Session) -> None:
    if db.query(SearchQuery).count() > 0:
        return  # already seeded (or user has their own searches) - never overwrite

    for idx, spec in enumerate(DEFAULT_SEARCHES):
        search = SearchQuery(name=spec["name"], sort_order=idx)
        db.add(search)
        db.flush()
        for query_text in _variants(spec["base_terms"]):
            db.add(QueryVariant(search_id=search.id, query_text=query_text))
    db.commit()
