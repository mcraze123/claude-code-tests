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

def get_tiktok_mentions(symbol: str) -> dict:
    """
    TikTok has no free official content-search API.
    Options if you need this:
      - RapidAPI "TikTok Scraper" (~$10–30/mo)
      - Unofficial pyktok library (fragile, grey-area ToS)
    Returns a stub until configured.
    """
    # Future: check for a TIKTOK_API_KEY env var and call RapidAPI here
    return {"available": False,
            "note": "No free TikTok API — see sentiment.py for options"}


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
    trends_val = get_google_trends([symbol]).get(symbol, 0.0) if fetch_trends else None

    # Normalise to 0–100
    st_vol_score    = min(st["message_volume"] / 30 * 100, 100.0)
    st_bull_score   = st["bull_pct"]                                     # already 0–100
    reddit_score    = min(reddit["mention_count"] / 20 * 100, 100.0)
    news_score      = news["bull_pct"]                                   # 0–100
    trends_score    = trends_val if trends_val is not None else None
    tw_score        = (min(twitter.get("mention_count", 0) / 50 * 100, 100.0)
                       if twitter.get("available") else None)

    # Build weighted average from available sources
    sources = [
        (st_vol_score,  0.25),
        (st_bull_score, 0.25),
        (reddit_score,  0.20),
        (news_score,    0.15),
    ]
    if trends_score is not None:
        sources.append((trends_score, 0.15))
    else:
        # Redistribute the 15% trends weight to StockTwits + Reddit
        sources = [
            (st_vol_score,  0.30),
            (st_bull_score, 0.28),
            (reddit_score,  0.25),
            (news_score,    0.17),
        ]
    if tw_score is not None:
        # Add twitter and rescale others by 0.8
        sources = [(v, w * 0.8) for v, w in sources]
        sources.append((tw_score, 0.20))

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
        "twitter":  twitter,
        "tiktok":   get_tiktok_mentions(symbol),
    }


# ── Trending ticker discovery ─────────────────────────────────────────────────

def get_trending_tickers(watchlist: list[str] = None) -> list[str]:
    """
    Merge trending tickers from StockTwits + Reddit hot posts.
    Results are deduplicated and filtered to alpha-only uppercase symbols.
    """
    st_trend     = get_stocktwits_trending()
    reddit_trend = get_reddit_trending()

    seen, merged = set(), []
    for t in (st_trend + reddit_trend + (watchlist or [])):
        clean = t.strip().upper()
        if clean and clean.isalpha() and clean not in seen:
            seen.add(clean)
            merged.append(clean)

    return merged
