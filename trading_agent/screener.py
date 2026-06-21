"""
Screener: fetches top movers, runs technical analysis, and produces a ranked
list with trade scores and directional bias.
"""

import json
from .market_data import get_top_movers, get_ohlcv, get_quote
from .analysis import full_analysis
from .config import MIN_PRICE, MAX_PRICE, MIN_AVG_DAILY_VOLUME, MIN_RELATIVE_VOLUME


def score_setup(mover: dict, ta: dict, sentiment: dict = None) -> dict:
    """
    Score a candidate 0–100 based on:
      - Multi-TF trend alignment        (40 pts)
      - Volume confirmation              (15 pts)
      - VWAP positioning / deviation     (10 pts)
      - Order block / FVG at price       (15 pts)
      - Liquidity & BOS signals          (10 pts)
      - RSI extremes                     (10 pts)
      - Social sentiment boost           (up to 15 pts, optional)
    Returns total score, list of signal strings, and directional bias.
    """
    score = 0
    signals: list[str] = []
    bull = 0
    bear = 0

    tfs = ta.get("timeframes", {})

    # ── Multi-TF trend alignment (40 pts) ────────────────────────────────────
    weights = {"daily": 16, "4h": 12, "1h": 8, "15m": 4}
    for tf, w in weights.items():
        d = tfs.get(tf, {})
        direction = d.get("trend", {}).get("direction", "neutral")
        if direction == "bullish":
            score += w
            bull += 1
            signals.append(f"{tf} bullish")
        elif direction == "bearish":
            score += int(w * 0.75)
            bear += 1
            signals.append(f"{tf} bearish")

    # ── Volume (15 pts) ───────────────────────────────────────────────────────
    rvol = mover.get("rel_volume") or 1.0
    if rvol >= 4:
        score += 15
        signals.append(f"extreme volume {rvol:.1f}x")
    elif rvol >= 2.5:
        score += 10
        signals.append(f"high volume {rvol:.1f}x")
    elif rvol >= 1.5:
        score += 5
        signals.append(f"above-avg volume {rvol:.1f}x")

    # ── VWAP (10 pts) ─────────────────────────────────────────────────────────
    tf1h = tfs.get("1h", {})
    vwap_dev = tf1h.get("vwap_dev_pct", 0) or 0
    if abs(vwap_dev) <= 0.5:
        score += 10
        signals.append(f"price at VWAP ({vwap_dev:+.1f}%)")
    elif abs(vwap_dev) <= 1.5:
        score += 6
        signals.append(f"near VWAP ({vwap_dev:+.1f}%)")
    elif abs(vwap_dev) > 4.0:
        score += 4
        signals.append(f"extended from VWAP ({vwap_dev:+.1f}%) — mean reversion")
        bull += (1 if vwap_dev < 0 else 0)
        bear += (1 if vwap_dev > 0 else 0)

    # ── Order blocks & FVG (15 pts) ───────────────────────────────────────────
    if tf1h.get("nearby_bullish_ob"):
        score += 6
        ob = tf1h["nearby_bullish_ob"]
        signals.append(f"1h bullish OB {ob['low']}–{ob['high']}")
        bull += 1
    if tf1h.get("nearby_bearish_ob"):
        score += 6
        ob = tf1h["nearby_bearish_ob"]
        signals.append(f"1h bearish OB {ob['low']}–{ob['high']}")
        bear += 1
    if tf1h.get("price_in_fvg"):
        score += 5
        signals.append("price inside 1h FVG — fill likely")
    elif tf1h.get("unfilled_bull_fvgs", 0) > 0:
        score += 3
        signals.append(f"{tf1h['unfilled_bull_fvgs']} unfilled bull FVG on 1h")
    elif tf1h.get("unfilled_bear_fvgs", 0) > 0:
        score += 3
        signals.append(f"{tf1h['unfilled_bear_fvgs']} unfilled bear FVG on 1h")

    # ── Liquidity & BOS (10 pts) ──────────────────────────────────────────────
    if tf1h.get("nearby_liquidity"):
        score += 5
        zones = tf1h["nearby_liquidity"]
        signals.append(f"liquidity zone within 4% on 1h ({zones[0]['type']})")
    bos = tf1h.get("break_of_structure", {})
    if bos.get("bos"):
        score += 5
        signals.append(f"BOS {bos['bos']} on 1h at {bos.get('level')}")
        bull += (1 if bos["bos"] == "bullish" else 0)
        bear += (1 if bos["bos"] == "bearish" else 0)

    # ── RSI extremes (10 pts) ─────────────────────────────────────────────────
    rsi = tf1h.get("rsi")
    if rsi is not None:
        if rsi < 28:
            score += 10
            signals.append(f"RSI deeply oversold {rsi:.0f} on 1h")
            bull += 2
        elif rsi < 38:
            score += 7
            signals.append(f"RSI oversold {rsi:.0f} on 1h")
            bull += 1
        elif rsi > 72:
            score += 10
            signals.append(f"RSI deeply overbought {rsi:.0f} on 1h")
            bear += 2
        elif rsi > 62:
            score += 7
            signals.append(f"RSI overbought {rsi:.0f} on 1h")
            bear += 1

    # ── Unfilled daily gap bonus ───────────────────────────────────────────────
    daily_gaps = tfs.get("daily", {}).get("unfilled_gaps", [])
    if daily_gaps:
        score += 5
        g = daily_gaps[-1]
        signals.append(f"unfilled daily gap {g['type']} ({g['gap_pct']:+.1f}%) at {g['level']}")

    # ── Social sentiment bonus (up to 15 pts) ────────────────────────────────
    if sentiment:
        s_score  = sentiment.get("composite_score", 50.0)
        st_bull  = sentiment.get("stocktwits", {}).get("bull_pct", 50.0)
        st_vol   = sentiment.get("stocktwits", {}).get("volume", 0)
        news_sent = sentiment.get("news", {}).get("sentiment", "neutral")

        if s_score >= 75:
            score += 15
            signals.append(f"high social buzz (sentiment {s_score:.0f}/100)")
        elif s_score >= 60:
            score += 8
            signals.append(f"elevated social sentiment ({s_score:.0f}/100)")
        elif s_score >= 45:
            score += 3

        # Directional weighting from StockTwits (need volume to trust it)
        if st_vol >= 5:
            if st_bull >= 68:
                bull += 1
                signals.append(f"StockTwits {st_bull:.0f}% bullish ({st_vol} msgs)")
            elif st_bull <= 32:
                bear += 1
                signals.append(f"StockTwits {st_bull:.0f}% bullish (bearish skew, {st_vol} msgs)")

        # News sentiment directional nudge
        if news_sent == "bullish":
            bull += 1
            signals.append("news sentiment bullish")
        elif news_sent == "bearish":
            bear += 1
            signals.append("news sentiment bearish")

    bias = "bullish" if bull > bear else ("bearish" if bear > bull else "neutral")
    return {"total": min(100, score), "signals": signals, "bias": bias}


def run_screen(max_candidates: int = 20, with_sentiment: bool = False) -> list[dict]:
    """
    Full screen: top movers → (optional social discovery) → TA → scoring.

    with_sentiment=True fetches StockTwits + Reddit + Yahoo news for each
    candidate and boosts scores accordingly.  Also discovers additional
    tickers from StockTwits trending and WSB hot posts.
    Note: Google Trends is NOT fetched here (too slow per-ticker);
    call get_google_trends() separately for a batch if desired.
    """
    movers = get_top_movers(30)

    # Social ticker discovery — surfaces tickers not already in top movers
    if with_sentiment:
        from .sentiment import get_trending_tickers
        social_tickers = get_trending_tickers()
        existing = {m["symbol"] for m in movers if m.get("symbol")}
        extras   = [t for t in social_tickers if t not in existing]
        for sym in extras[:15]:
            q = get_quote(sym)
            price = q.get("price")
            if price and MIN_PRICE <= price <= MAX_PRICE:
                q.setdefault("move_score", 0)   # social tickers start with base score
                movers.append(q)

    results = []
    for m in movers[:max_candidates]:
        sym = m.get("symbol")
        if not sym:
            continue
        try:
            df_map = {
                "daily": get_ohlcv(sym, "daily"),
                "4h":    get_ohlcv(sym, "4h"),
                "1h":    get_ohlcv(sym, "1h"),
                "15m":   get_ohlcv(sym, "15m"),
            }
            ta        = full_analysis(sym, df_map)
            sentiment = None
            if with_sentiment:
                from .sentiment import score_sentiment
                sentiment = score_sentiment(sym, fetch_trends=False)
            scored = score_setup(m, ta, sentiment=sentiment)
            entry = {
                "symbol":     sym,
                "price":      m.get("price"),
                "pct_change": m.get("pct_change"),
                "rel_volume": m.get("rel_volume"),
                "score":      scored["total"],
                "bias":       scored["bias"],
                "signals":    scored["signals"],
                "ta":         ta,
            }
            if sentiment:
                entry["sentiment"] = {
                    "composite":  sentiment["composite_score"],
                    "st_bull_pct": sentiment["stocktwits"]["bull_pct"],
                    "st_volume":   sentiment["stocktwits"]["volume"],
                    "reddit_mentions": sentiment["reddit"]["mentions"],
                    "news":        sentiment["news"]["sentiment"],
                }
            results.append(entry)
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return results
