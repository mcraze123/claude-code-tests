"""
Market data layer — OHLCV via yfinance, current quotes, and top-movers screening.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import Optional
from .config import TIMEFRAME_PARAMS, CORE_WATCHLIST, MIN_PRICE, MAX_PRICE, MIN_AVG_DAILY_VOLUME


def get_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV for a symbol at the given timeframe key."""
    params = TIMEFRAME_PARAMS.get(timeframe, TIMEFRAME_PARAMS["1h"])
    try:
        df = yf.download(
            symbol,
            period=params["period"],
            interval=params["interval"],
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    # Flatten multi-level columns that yfinance sometimes returns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.index = pd.to_datetime(df.index)
    df = df.dropna(subset=["Close", "Volume"])

    if timeframe == "4h":
        df = (
            df.resample("4h")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna()
        )

    return df


def get_quote(symbol: str) -> dict:
    """Return a lightweight current quote dict."""
    try:
        info = yf.Ticker(symbol).fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
        prev_close = getattr(info, "previous_close", None)
        volume = getattr(info, "last_volume", None)
        avg_volume = getattr(info, "three_month_average_volume", None)
        market_cap = getattr(info, "market_cap", None)
        return {
            "symbol": symbol,
            "price": price,
            "prev_close": prev_close,
            "volume": volume,
            "avg_volume": avg_volume,
            "market_cap": market_cap,
            "pct_change": round((price - prev_close) / prev_close * 100, 2)
            if price and prev_close
            else None,
            "rel_volume": round(volume / avg_volume, 2)
            if volume and avg_volume and avg_volume > 0
            else None,
        }
    except Exception:
        return {"symbol": symbol, "price": None, "error": "quote_failed"}


def get_top_movers(n: int = 25) -> list[dict]:
    """
    Build a scored universe of stocks from the core watchlist.
    Sort by |pct_change| * relative_volume so the biggest expected moves surface first.
    """
    results = []
    for sym in CORE_WATCHLIST:
        q = get_quote(sym)
        price = q.get("price")
        if not price or price < MIN_PRICE or price > MAX_PRICE:
            continue
        avg_vol = q.get("avg_volume") or 0
        if avg_vol < MIN_AVG_DAILY_VOLUME:
            continue
        pct = abs(q.get("pct_change") or 0)
        rvol = q.get("rel_volume") or 1.0
        q["move_score"] = pct * rvol
        results.append(q)

    results.sort(key=lambda x: x.get("move_score", 0), reverse=True)
    return results[:n]
