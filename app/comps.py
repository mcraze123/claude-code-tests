"""Comp (reference) price abstraction.

eBay's official sold-listings API (Marketplace Insights) is a Limited Release that
requires business approval and isn't available on a standard developer key. Until/unless
that's approved, comp prices are manually maintained: CompSample rows the user enters
themselves (source="manual_sold"), optionally bootstrapped by fetching current *asking*
prices from the Browse API (source="fetched_asking") as a rough starting point.

The math (trimmed mean + median, outlier pruning) is shared by both paths, so swapping
providers later doesn't change how stats are computed - only where samples come from.
"""
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import settings
from app.ebay_client import ebay_client


@dataclass
class CompStats:
    avg_price: float | None
    median_price: float | None
    sample_count: int


def compute_stats(prices: list[float]) -> CompStats:
    """Median of all samples; average is a trimmed mean (drops the single highest and
    lowest sample when there are enough of them) so one outlier listing doesn't skew it."""
    if not prices:
        return CompStats(avg_price=None, median_price=None, sample_count=0)

    median_price = statistics.median(prices)

    if len(prices) >= 5:
        trimmed = sorted(prices)[1:-1]
    else:
        trimmed = prices
    avg_price = sum(trimmed) / len(trimmed)

    return CompStats(avg_price=round(avg_price, 2), median_price=round(median_price, 2), sample_count=len(prices))


class CompPriceProvider(ABC):
    @abstractmethod
    def fetch_reference_prices(self, match_keywords: list[str], *, limit: int = 20) -> list[float]:
        """Return a list of reference prices for a model. Implementations decide what
        those prices represent (asking price vs. confirmed sold price)."""
        raise NotImplementedError


class ManualCompProvider(CompPriceProvider):
    """Default provider. Fetches CURRENT ASKING prices for working (non-parts) condition
    listings as a rough starting estimate the user can edit - these are clearly NOT sold
    prices and are stored with source="fetched_asking" so the UI can label them as such."""

    EXCLUDE_TERMS = ["-parts", "-repair", "-broken", "-\"not working\"", "-\"for parts\"", "-cracked", "-\"as is\"", "-faulty"]

    def fetch_reference_prices(self, match_keywords: list[str], *, limit: int = 20) -> list[float]:
        query = " ".join(match_keywords) + " " + " ".join(self.EXCLUDE_TERMS)
        listings = ebay_client.search(
            query,
            buying_options=["FIXED_PRICE"],
            limit=limit,
            sort="price",
        )
        return [l.current_price for l in listings if l.current_price]


class MarketplaceInsightsCompProvider(CompPriceProvider):
    """Stub for eBay's Marketplace Insights API (sold items). Wire this up once eBay
    approves Limited Release access for this app - the rest of the app (scoring,
    dashboard, email) does not need to change, only COMP_PROVIDER=marketplace_insights
    in .env and the implementation below."""

    def fetch_reference_prices(self, match_keywords: list[str], *, limit: int = 20) -> list[float]:
        raise NotImplementedError(
            "Marketplace Insights API access has not been configured yet. "
            "Apply for Limited Release access at developer.ebay.com, then implement this "
            "provider against the /buy/marketplace_insights/v1_beta/item_sales/search endpoint."
        )


def get_provider() -> CompPriceProvider:
    if settings.comp_provider == "marketplace_insights":
        return MarketplaceInsightsCompProvider()
    return ManualCompProvider()
