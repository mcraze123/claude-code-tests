"""
Social sentiment data layer.

Sources (free, no API keys required by default):
  Google Trends  — relative search interest via pytrends
  StockTwits     — message volume + bull/bear ratio (public API, no auth)
  Reddit         — mention counts from r/wallstreetbets + r/stocks (public JSON)
  Yahoo Finance  — recent news headlines with keyword sentiment
  X.com          — STUB: set TWITTER_BEARER_TOKEN in .env ($100/mo API tier)
  TikTok         — STUB: no free official search API exists

NOTE: All sources return CURRENT data only.
      Historical sentiment is not available from free sources, so sentiment
      cannot be replayed in the backtester.  Use for live screening only.
"""

import os
import re
import time
import requests
import pandas as pd
from datetime import datetime
from typing import Optional

_CACHE: dict = {}
_CACHE_TTL = 30 * 60        # 30 minutes


# ── Cache helper ──────────────────────────────────────────────────────────────

def _cached(key: str, fn, ttl: int = _CACHE_TTL):
    entry = _CACHE.get(key)
    if entry and (datetime.now() - entry["ts"]).seconds < ttl:
        return entry["data"]
    data = fn()
    _CACHE[key] = {"data": data, "ts": datetime.now()}
    return data


# ── Google Trends ─────────────────────────────────────────────────────────────

def get_google_trends(symbols: list[str], timeframe: str = "now 1-d") -> dict[str, float]:
    """
    Returns {symbol: interest (0–100)} for each symbol.
    Batches up to 5 symbols per request (pytrends API limit).
    Requires: pip install pytrends
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        return {s: 0.0 for s in symbols}

    pytrends = TrendReq(hl="en-US", tz=300, timeout=(10, 25))
    scores: dict[str, float] = {}

    for i in range(0, len(symbols), 5):
        batch = symbols[i:i + 5]
        key   = f"gtrends:{','.join(batch)}:{timeframe}"

        def _fetch(b=batch):
            try:
                pytrends.build_payload(b, timeframe=timeframe, geo="US", gprop="")
                df = pytrends.interest_over_time()
                if df.empty:
                    return {s: 0.0 for s in b}
                return {s: float(df[s].iloc[-1]) if s in df.columns else 0.0
                        for s in b}
            except Exception:
                return {s: 0.0 for s in b}

        scores.update(_cached(key, _fetch))
        if i + 5 < len(symbols):
            time.sleep(1.2)     # stay under pytrends rate limit

    return scores


# ── StockTwits ────────────────────────────────────────────────────────────────

_ST_HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_stocktwits(symbol: str) -> dict:
    """Message volume + bull/bear ratio from StockTwits (no auth required)."""
    def _fetch():
        try:
            url  = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
            resp = requests.get(url, timeout=10, headers=_ST_HEADERS)
            if resp.status_code != 200:
                return _st_empty()
            msgs = resp.json().get("messages", [])
            bull = sum(1 for m in msgs
                       if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
            bear = sum(1 for m in msgs
                       if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
            tagged = bull + bear
            return {
                "message_volume": len(msgs),
                "bull_count":     bull,
                "bear_count":     bear,
                "bull_pct":       round(bull / tagged * 100, 1) if tagged else 50.0,
                "tagged_pct":     round(tagged / max(len(msgs), 1) * 100, 1),
            }
        except Exception:
            return _st_empty()

    return _cached(f"st:{symbol}", _fetch)


def _st_empty() -> dict:
    return {"message_volume": 0, "bull_count": 0, "bear_count": 0,
            "bull_pct": 50.0, "tagged_pct": 0.0}


def get_stocktwits_trending() -> list[str]:
    """Tickers currently trending on StockTwits."""
    def _fetch():
        try:
            url  = "https://api.stocktwits.com/api/2/trending/symbols.json"
            resp = requests.get(url, timeout=10, headers=_ST_HEADERS)
            if resp.status_code != 200:
                return []
            return [s["symbol"] for s in resp.json().get("symbols", [])]
        except Exception:
            return []

    return _cached("st_trending", _fetch)


# ── Reddit ────────────────────────────────────────────────────────────────────

_REDDIT_HEADERS = {"User-Agent": "StockScreener/1.0"}
_REDDIT_SUBS    = "wallstreetbets+stocks+investing+StockMarket"

# Common uppercase words that look like tickers but aren't
_NON_TICKERS = {
    "I", "A", "THE", "FOR", "AND", "OR", "IS", "IN", "ON", "TO", "AT",
    "BE", "BY", "OF", "IF", "AS", "IT", "MY", "NO", "SO", "US", "UP",
    "WSB", "DD", "OP", "EV", "AI", "IPO", "ETF", "CEO", "SEC", "FDA",
    "FED", "GDP", "IRS", "NYSE", "NASDAQ", "IMO", "TBH", "EOD", "EPS",
    "ATH", "PE", "TA", "TD", "IV", "DCA", "RSI", "ITM", "OTM", "ATM",
    "PUT", "CALL", "YOLO", "HOD", "LOD", "BO", "EMA", "SMA", "VWAP",
}


def get_reddit_mentions(symbol: str) -> dict:
    """Count recent Reddit mentions of a symbol across investing subreddits."""
    def _fetch():
        try:
            url  = (f"https://www.reddit.com/r/{_REDDIT_SUBS}/search.json"
                    f"?q=%24{symbol}+OR+%22+{symbol}+%22&sort=new&t=day&limit=100")
            resp = requests.get(url, timeout=10, headers=_REDDIT_HEADERS)
            if resp.status_code != 200:
                return _reddit_empty()
            posts = resp.json().get("data", {}).get("children", [])
            scores = [p["data"].get("score", 0) for p in posts]
            pos    = sum(1 for s in scores if s > 10)
            neg    = sum(1 for s in scores if s < 0)
            tagged = pos + neg
            return {
                "mention_count":   len(posts),
                "total_upvotes":   sum(scores),
                "sentiment_score": round(pos / tagged * 100, 1) if tagged else 50.0,
            }
        except Exception:
            return _reddit_empty()

    return _cached(f"reddit:{symbol}", _fetch)


def _reddit_empty() -> dict:
    return {"mention_count": 0, "total_upvotes": 0, "sentiment_score": 50.0}


def get_reddit_trending() -> list[str]:
    """Extract tickers mentioned in hot r/wallstreetbets posts."""
    def _fetch():
        try:
            url  = "https://www.reddit.com/r/wallstreetbets/hot.json?limit=50"
            resp = requests.get(url, timeout=10, headers=_REDDIT_HEADERS)
            if resp.status_code != 200:
                return []
            posts = resp.json().get("data", {}).get("children", [])
            found = []
            for p in posts:
                title = p["data"].get("title", "")
                # Match $TICKER or standalone 2–5 letter uppercase words
                hits = re.findall(r'\$([A-Z]{1,5})\b|\b([A-Z]{2,5})\b', title)
                for h in hits:
                    t = h[0] or h[1]
                    if t and t not in _NON_TICKERS:
                        found.append(t)
            # Deduplicate while preserving order
            seen, unique = set(), []
            for t in found:
                if t not in seen:
                    seen.add(t)
                    unique.append(t)
            return unique
        except Exception:
            return []

    return _cached("reddit_trending", _fetch)


# ── Yahoo Finance news sentiment ──────────────────────────────────────────────

_BULL_WORDS = {
    "surge", "rally", "beat", "beats", "strong", "record", "bullish",
    "upgrade", "buy", "outperform", "raise", "raised", "gain", "gains",
    "breakout", "high", "growth", "profit", "positive", "up", "rose",
    "climbs", "soars", "jumps", "exceeds", "top",
}
_BEAR_WORDS = {
    "drop", "fall", "falls", "miss", "misses", "weak", "cut", "cuts",
    "bearish", "downgrade", "sell", "underperform", "lower", "lowered",
    "loss", "negative", "down", "fell", "plunges", "sinks", "warning",
    "investigation", "lawsuit", "recall", "below", "decline",
}


def get_yahoo_news_sentiment(symbol: str) -> dict:
    """Score recent news headlines from Yahoo Finance using keyword matching."""
    def _fetch():
        try:
            import yfinance as yf
            news = yf.Ticker(symbol).news or []
        except Exception:
            return {"article_count": 0, "bull_score": 0, "bear_score": 0,
                    "sentiment": "neutral"}

        bull_hits, bear_hits = 0, 0
        for article in news[:20]:
            title = (article.get("title") or "").lower()
            bull_hits += sum(1 for w in _BULL_WORDS if w in title)
            bear_hits += sum(1 for w in _BEAR_WORDS if w in title)

        total = bull_hits + bear_hits
        if total == 0:
            sentiment = "neutral"
        elif bull_hits / total >= 0.65:
            sentiment = "bullish"
        elif bear_hits / total >= 0.65:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        return {
            "article_count": len(news),
            "bull_hits":     bull_hits,
            "bear_hits":     bear_hits,
            "sentiment":     sentiment,
            "bull_pct":      round(bull_hits / total * 100, 1) if total else 50.0,
        }

    return _cached(f"ynews:{symbol}", _fetch)


# ── X.com (Twitter) ──────────────────────────────────────────────────────────

def get_twitter_mentions(symbol: str) -> dict:
    """
    Cashtag search via Twitter API v2.
    Requires TWITTER_BEARER_TOKEN in .env (Basic tier, ~$100/mo).

    To enable:
      1. Apply at developer.twitter.com
      2. Set TWITTER_BEARER_TOKEN=<your token> in .env
    """
    token = os.environ.get("TWITTER_BEARER_TOKEN")
    if not token:
        return {"available": False,
                "note": "Set TWITTER_BEARER_TOKEN in .env to enable X.com data"}

    def _fetch():
        try:
            url     = "https://api.twitter.com/2/tweets/search/recent"
            params  = {
                "query":         f"${symbol} lang:en -is:retweet",
                "max_results":   100,
                "tweet.fields":  "public_metrics",
            }
            headers = {"Authorization": f"Bearer {token}"}
            resp    = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code != 200:
                return {"available": False, "error": resp.status_code}
            tweets = resp.json().get("data", [])
            likes  = sum(t.get("public_metrics", {}).get("like_count", 0)
                         for t in tweets)
            return {
                "available":     True,
                "mention_count": len(tweets),
                "total_likes":   likes,
            }
        except Exception:
            return {"available": False}

    return _cached(f"tw:{symbol}", _fetch)


# ── TikTok ───────────────────────────────────────────────────────────────────
#
# Two implementations, tried in order:
#   1. Unofficial TikTokApi (free, Playwright-based, may break when TikTok
#      updates its anti-bot fingerprinting).
#      Install: pip install TikTokApi && python -m playwright install chromium
#
#   2. RapidAPI TikTok Scraper (reliable, ~$10/mo).
#      Enable: set RAPIDAPI_KEY in .env
#
# get_tiktok_mentions() tries #2 first if RAPIDAPI_KEY is set, else #1.


def _run_async(coro):
    """Run an async coroutine from synchronous code."""
    import asyncio
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Already inside a running loop (e.g. Jupyter) — use nest_asyncio
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except ImportError:
            pass
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)


def get_tiktok_unofficial(symbol: str) -> dict:
    """
    Scrape TikTok via the unofficial TikTokApi (Playwright-based).

    Setup (one time):
        pip install TikTokApi
        python -m playwright install chromium

    Searches hashtags #{symbol} and #{symbol}stock for recent videos.
    Fragile: TikTok actively fights bot fingerprinting and this library
    breaks every few months.  Pin to a working version if it stops working.
    """
    try:
        from TikTokApi import TikTokApi
    except ImportError:
        return {
            "available": False,
            "source":    "unofficial",
            "note":      "pip install TikTokApi && python -m playwright install chromium",
        }

    async def _fetch():
        video_count  = 0
        total_views  = 0
        total_likes  = 0

        try:
            async with TikTokApi() as api:
                await api.create_sessions(
                    num_sessions=1,
                    sleep_after=3,
                    headless=True,
                )

                # Try a few hashtag variants; stop at the first that returns results
                for term in [symbol.lower(), f"{symbol.lower()}stock",
                             f"{symbol.lower()}options"]:
                    try:
                        tag = api.hashtag(name=term)
                        async for video in tag.videos(count=20):
                            stats = (video.as_dict or {}).get("stats", {})
                            total_views += stats.get("playCount", 0)
                            total_likes += stats.get("diggCount", 0)
                            video_count += 1
                        if video_count:
                            break
                    except Exception:
                        continue
        except Exception as e:
            return {"available": False, "source": "unofficial", "error": str(e)[:200]}

        return {
            "available":       True,
            "source":          "unofficial",
            "video_count":     video_count,
            "total_views":     total_views,
            "total_likes":     total_likes,
            "views_per_video": round(total_views / max(video_count, 1)),
        }

    try:
        return _run_async(_fetch())
    except Exception as e:
        return {"available": False, "source": "unofficial", "error": str(e)[:200]}


def get_tiktok_rapidapi(symbol: str) -> dict:
    """
    TikTok data via RapidAPI (tiktok-scraper7.p.rapidapi.com).
    Set RAPIDAPI_KEY in .env to activate (~$10/mo for light usage).
    """
    api_key = os.environ.get("RAPIDAPI_KEY")
    if not api_key:
        return {
            "available": False,
            "source":    "rapidapi",
            "note":      "Set RAPIDAPI_KEY in .env to enable",
        }

    def _fetch():
        try:
            url     = "https://tiktok-scraper7.p.rapidapi.com/hashtag/posts"
            headers = {
                "X-RapidAPI-Key":  api_key,
                "X-RapidAPI-Host": "tiktok-scraper7.p.rapidapi.com",
            }
            params = {"name": symbol.lower(), "count": "20"}
            resp   = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                return {"available": False, "source": "rapidapi",
                        "error": f"HTTP {resp.status_code}"}
            videos      = resp.json().get("data", {}).get("itemList", [])
            total_views = sum(v.get("stats", {}).get("playCount", 0) for v in videos)
            total_likes = sum(v.get("stats", {}).get("diggCount", 0) for v in videos)
            return {
                "available":       True,
                "source":          "rapidapi",
                "video_count":     len(videos),
                "total_views":     total_views,
                "total_likes":     total_likes,
                "views_per_video": round(total_views / max(len(videos), 1)),
            }
        except Exception as e:
            return {"available": False, "source": "rapidapi", "error": str(e)[:200]}

    return _cached(f"tiktok_ra:{symbol}", _fetch, ttl=45 * 60)


def _get_tiktok_trending_unofficial() -> list[str]:
    """Extract tickers from finance hashtags on TikTok via unofficial API."""
    try:
        from TikTokApi import TikTokApi
    except ImportError:
        return []

    async def _fetch():
        tickers = []
        try:
            async with TikTokApi() as api:
                await api.create_sessions(num_sessions=1, sleep_after=3, headless=True)
                for tag_name in ("stocktok", "investing", "stocks", "wallstreetbets"):
                    try:
                        tag = api.hashtag(name=tag_name)
                        async for video in tag.videos(count=30):
                            desc = (video.as_dict or {}).get("desc", "")
                            found = re.findall(r'\$([A-Z]{1,5})\b|\b([A-Z]{2,5})\b', desc)
                            for f in found:
                                t = f[0] or f[1]
                                if t and t not in _NON_TICKERS and t.isalpha():
                                    tickers.append(t)
                    except Exception:
                        continue
        except Exception:
            pass
        return tickers

    try:
        return _run_async(_fetch())
    except Exception:
        return []


def get_tiktok_mentions(symbol: str) -> dict:
    """
    Returns TikTok mention data for a symbol.
    Tries RapidAPI first (if RAPIDAPI_KEY set), then unofficial TikTokApi.
    """
    def _fetch():
        if os.environ.get("RAPIDAPI_KEY"):
            result = get_tiktok_rapidapi(symbol)
            if result.get("available"):
                return result

        result = get_tiktok_unofficial(symbol)
        if result.get("available"):
            return result

        # Neither configured — return helpful stub
        notes = []
        if not os.environ.get("RAPIDAPI_KEY"):
            notes.append("paid: set RAPIDAPI_KEY in .env (~$10/mo)")
        notes.append("free: pip install TikTokApi && python -m playwright install chromium")
        return {"available": False, "options": notes}

    return _cached(f"tiktok:{symbol}", _fetch, ttl=45 * 60)


# ── Composite score ───────────────────────────────────────────────────────────

def score_sentiment(symbol: str, fetch_trends: bool = False) -> dict:
    """
    Composite sentiment score 0–100 for a single symbol.

    Weights (without Twitter):
      StockTwits volume (normalised)  25%
      StockTwits bull ratio           25%
      Reddit mentions (normalised)    20%
      Yahoo news sentiment            15%
      Google Trends                   15%  (off by default — slow, rate-limited)

    With TWITTER_BEARER_TOKEN configured, Twitter gets 20% and other
    weights are reduced proportionally.
    """
    st      = get_stocktwits(symbol)
    reddit  = get_reddit_mentions(symbol)
    news    = get_yahoo_news_sentiment(symbol)
    twitter = get_twitter_mentions(symbol)
    tiktok  = get_tiktok_mentions(symbol)
    trends_val = get_google_trends([symbol]).get(symbol, 0.0) if fetch_trends else None

    # Normalise each source to 0–100
    st_vol_score  = min(st["message_volume"] / 30 * 100, 100.0)
    st_bull_score = st["bull_pct"]
    reddit_score  = min(reddit["mention_count"] / 20 * 100, 100.0)
    news_score    = news["bull_pct"]
    trends_score  = trends_val if trends_val is not None else None
    tw_score      = (min(twitter.get("mention_count", 0) / 50 * 100, 100.0)
                     if twitter.get("available") else None)
    tt_score      = (min(tiktok.get("video_count", 0) / 20 * 100, 100.0)
                     if tiktok.get("available") else None)

    # Base sources — always present
    sources: list[tuple[float, float]] = [
        (st_vol_score,  0.25),
        (st_bull_score, 0.25),
        (reddit_score,  0.20),
        (news_score,    0.15),
    ]
    remaining_weight = 0.15   # slot for trends / twitter / tiktok

    if trends_score is not None:
        sources.append((trends_score, remaining_weight))
        remaining_weight = 0.0

    # Optional sources: each takes 15% and rescales the rest proportionally
    for optional_score in filter(None, [tw_score, tt_score]):
        factor = 1 - 0.15
        sources = [(v, w * factor) for v, w in sources]
        sources.append((optional_score, 0.15))

    total_weight = sum(w for _, w in sources)
    composite    = sum(v * w for v, w in sources) / total_weight

    return {
        "symbol":          symbol,
        "composite_score": round(composite, 1),
        "stocktwits": {
            "volume":   st["message_volume"],
            "bull_pct": st["bull_pct"],
            "score":    round(st_vol_score, 1),
        },
        "reddit": {
            "mentions": reddit["mention_count"],
            "upvotes":  reddit["total_upvotes"],
            "score":    round(reddit_score, 1),
        },
        "news": news,
        "google_trends": ({"interest": round(trends_score, 1)}
                          if trends_score is not None else {"note": "not fetched"}),
        "twitter": twitter,
        "tiktok":  tiktok,
    }


# ── Trending ticker discovery ─────────────────────────────────────────────────

def get_trending_tickers(watchlist: list[str] = None) -> list[str]:
    """
    Merge trending tickers from StockTwits, Reddit, and TikTok (if configured).
    Results are deduplicated and filtered to alpha-only uppercase symbols.
    """
    st_trend     = get_stocktwits_trending()
    reddit_trend = get_reddit_trending()
    tt_trend     = _cached("tiktok_trending",
                           _get_tiktok_trending_unofficial, ttl=60 * 60)

    seen, merged = set(), []
    for t in (st_trend + reddit_trend + tt_trend + (watchlist or [])):
        clean = t.strip().upper()
        if clean and clean.isalpha() and clean not in seen and clean not in _NON_TICKERS:
            seen.add(clean)
            merged.append(clean)

    return merged
