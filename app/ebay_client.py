"""Thin wrapper around eBay's Browse API (active listings only - see app/comps.py
for why sold-price data is handled separately)."""
import base64
import datetime
import logging
import time
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_HOSTS = {
    "production": "https://api.ebay.com",
    "sandbox": "https://api.sandbox.ebay.com",
}


@dataclass
class EbayListing:
    ebay_item_id: str
    title: str
    url: str
    image_url: str | None
    listing_type: str  # FIXED_PRICE | AUCTION
    current_price: float | None
    currency: str
    bid_count: int | None
    watcher_count: int | None
    condition: str | None
    description_snippet: str | None
    seller_username: str | None
    listed_at: datetime.datetime | None
    end_time: datetime.datetime | None
    raw: dict = field(default_factory=dict, repr=False)


class EbayClient:
    def __init__(self):
        self._host = _HOSTS.get(settings.ebay_env, _HOSTS["production"])
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._http = httpx.Client(timeout=20.0)

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        if not settings.ebay_client_id or not settings.ebay_client_secret:
            raise RuntimeError(
                "eBay API credentials are not configured. Set EBAY_CLIENT_ID / EBAY_CLIENT_SECRET in .env"
            )

        creds = base64.b64encode(
            f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode()
        ).decode()
        resp = self._http.post(
            f"{self._host}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + payload.get("expires_in", 7200)
        return self._token

    def search(
        self,
        query_text: str,
        *,
        buying_options: list[str],  # ["FIXED_PRICE"] or ["AUCTION"]
        category_ids: str | None = None,
        condition_ids: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        limit: int = 50,
        sort: str | None = None,
    ) -> list[EbayListing]:
        """Runs one Browse API item_summary/search call and returns parsed listings.
        Note: the `q` param supports quotes and `-exclude` like eBay's search box, but not
        full parenthetical (a,b) OR-grouping - use multiple QueryVariant rows for that."""
        token = self._get_token()
        filters = [f"buyingOptions:{{{'|'.join(buying_options)}}}"]
        if condition_ids:
            filters.append(f"conditionIds:{{{condition_ids.replace(',', '|')}}}")
        if min_price is not None or max_price is not None:
            lo = "" if min_price is None else str(min_price)
            hi = "" if max_price is None else str(max_price)
            filters.append(f"price:[{lo}..{hi}]")
            filters.append("priceCurrency:USD")

        params = {
            "q": query_text,
            "limit": str(limit),
            "filter": ",".join(filters),
        }
        if category_ids:
            params["category_ids"] = category_ids
        if sort:
            params["sort"] = sort

        resp = self._http.get(
            f"{self._host}/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
            },
            params=params,
        )
        if resp.status_code == 401:
            # token may have been invalidated server-side; refresh once and retry
            self._token = None
            token = self._get_token()
            resp = self._http.get(
                f"{self._host}/buy/browse/v1/item_summary/search",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
                },
                params=params,
            )
        resp.raise_for_status()
        data = resp.json()
        return [self._parse_item_summary(raw) for raw in data.get("itemSummaries", [])]

    def get_item_detail(self, item_id: str) -> EbayListing | None:
        """Refetch a single item (used to refresh bid/watcher counts for ending-soon auctions)."""
        token = self._get_token()
        resp = self._http.get(
            f"{self._host}/buy/browse/v1/item/{item_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
            },
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._parse_item_detail(resp.json())

    @staticmethod
    def _parse_item_summary(raw: dict) -> EbayListing:
        price = raw.get("price") or {}
        buying_options = raw.get("buyingOptions", [])
        listing_type = "AUCTION" if "AUCTION" in buying_options else "FIXED_PRICE"
        bidding = raw.get("bidding") or {}
        image = raw.get("image") or {}
        seller = raw.get("seller") or {}
        return EbayListing(
            ebay_item_id=raw["itemId"],
            title=raw.get("title", ""),
            url=raw.get("itemWebUrl", ""),
            image_url=image.get("imageUrl"),
            listing_type=listing_type,
            current_price=float(price["value"]) if price.get("value") else None,
            currency=price.get("currency", "USD"),
            bid_count=bidding.get("bidCount"),
            watcher_count=raw.get("watchCount"),
            condition=raw.get("condition"),
            description_snippet=raw.get("shortDescription"),
            seller_username=seller.get("username"),
            listed_at=_parse_dt(raw.get("itemCreationDate")),
            end_time=_parse_dt(raw.get("itemEndDate")),
            raw=raw,
        )

    @staticmethod
    def _parse_item_detail(raw: dict) -> EbayListing:
        price = raw.get("price") or {}
        buying_options = raw.get("buyingOptions", [])
        listing_type = "AUCTION" if "AUCTION" in buying_options else "FIXED_PRICE"
        bidding = raw.get("bidding") or {}
        image = raw.get("image") or {}
        seller = raw.get("seller") or {}
        return EbayListing(
            ebay_item_id=raw["itemId"],
            title=raw.get("title", ""),
            url=raw.get("itemWebUrl", ""),
            image_url=image.get("imageUrl"),
            listing_type=listing_type,
            current_price=float((bidding.get("currentPrice") or price).get("value")) if (bidding.get("currentPrice") or price).get("value") else None,
            currency=price.get("currency", "USD"),
            bid_count=bidding.get("bidCount"),
            watcher_count=raw.get("watchCount") or raw.get("estimatedAvailabilities", [{}])[0].get("estimatedAvailableQuantity"),
            condition=raw.get("condition"),
            description_snippet=raw.get("description", "")[:500] if raw.get("description") else None,
            seller_username=seller.get("username"),
            listed_at=_parse_dt(raw.get("itemCreationDate")),
            end_time=_parse_dt(raw.get("itemEndDate")),
            raw=raw,
        )


def _parse_dt(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


ebay_client = EbayClient()
