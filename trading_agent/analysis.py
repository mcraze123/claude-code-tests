"""
Technical analysis: indicators, Smart Money Concepts, and multi-timeframe synthesis.

Implements:
  - VWAP, EMA (9/21/50/200), RSI, ATR, Bollinger Bands
  - Order Blocks (bullish / bearish)
  - Fair Value Gaps (FVG)
  - Session gaps and fill status
  - Liquidity pools (equal highs / equal lows)
  - Break of Structure (BOS)
  - Multi-timeframe trend + confluence scoring
"""

import pandas as pd
import numpy as np


# ── Indicators ────────────────────────────────────────────────────────────────

def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    return (typical * df["Volume"]).cumsum() / df["Volume"].cumsum()


def intraday_vwap(df: pd.DataFrame) -> float:
    """Session VWAP anchored to the most recent trading day in df."""
    last_date = df.index[-1].date()
    today = df[df.index.date == last_date]
    if today.empty or today["Volume"].sum() == 0:
        today = df.tail(20)
    typical = (today["High"] + today["Low"] + today["Close"]) / 3
    return float((typical * today["Volume"]).sum() / today["Volume"].sum())


def ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df["Close"].ewm(span=period, adjust=False).mean()


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(period).mean()


def bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = df["Close"].rolling(period).mean()
    s = df["Close"].rolling(period).std()
    return pd.DataFrame({"upper": mid + std * s, "mid": mid, "lower": mid - std * s})


# ── Smart Money Concepts ──────────────────────────────────────────────────────

def order_blocks(df: pd.DataFrame, lookback: int = 30) -> list[dict]:
    """
    Bullish OB: last bearish candle before a 3-candle bullish impulse.
    Bearish OB: last bullish candle before a 3-candle bearish impulse.
    Returns most recent OBs within `lookback` candles.
    """
    obs = []
    n = len(df)
    start = max(1, n - lookback - 3)

    for i in range(start, n - 2):
        o, c = df["Open"].iloc[i], df["Close"].iloc[i]
        h, l = df["High"].iloc[i], df["Low"].iloc[i]
        next_body = abs(df["Close"].iloc[i + 1] - df["Open"].iloc[i + 1])
        this_body = abs(c - o)

        # Bullish OB
        if c < o and next_body > this_body * 1.2 and df["Close"].iloc[i + 1] > df["Open"].iloc[i + 1]:
            obs.append({"type": "bullish", "high": h, "low": l,
                        "mid": (h + l) / 2, "date": df.index[i]})

        # Bearish OB
        if c > o and next_body > this_body * 1.2 and df["Close"].iloc[i + 1] < df["Open"].iloc[i + 1]:
            obs.append({"type": "bearish", "high": h, "low": l,
                        "mid": (h + l) / 2, "date": df.index[i]})

    return obs


def fair_value_gaps(df: pd.DataFrame, lookback: int = 40) -> list[dict]:
    """
    Bullish FVG: Low[i+1] > High[i-1]  →  untouched gap below current price.
    Bearish FVG: High[i+1] < Low[i-1]  →  untouched gap above current price.
    """
    fvgs = []
    n = len(df)
    start = max(1, n - lookback)
    price = df["Close"].iloc[-1]

    for i in range(start, n - 1):
        if df["Low"].iloc[i + 1] > df["High"].iloc[i - 1]:
            top = df["Low"].iloc[i + 1]
            bot = df["High"].iloc[i - 1]
            fvgs.append({
                "type": "bullish",
                "top": top, "bottom": bot, "mid": (top + bot) / 2,
                "date": df.index[i],
                "filled": price <= top and price >= bot,
                "size_pct": round((top - bot) / bot * 100, 3),
            })
        elif df["High"].iloc[i + 1] < df["Low"].iloc[i - 1]:
            top = df["Low"].iloc[i - 1]
            bot = df["High"].iloc[i + 1]
            fvgs.append({
                "type": "bearish",
                "top": top, "bottom": bot, "mid": (top + bot) / 2,
                "date": df.index[i],
                "filled": price <= top and price >= bot,
                "size_pct": round((top - bot) / bot * 100, 3),
            })

    return fvgs


def session_gaps(df: pd.DataFrame) -> list[dict]:
    """Detect open price gaps between consecutive daily bars."""
    gaps = []
    for i in range(1, len(df)):
        prev_c = df["Close"].iloc[i - 1]
        curr_o = df["Open"].iloc[i]
        pct = (curr_o - prev_c) / prev_c * 100

        if abs(pct) < 0.25:
            continue

        # Filled if price retraced to prev_close within the bar
        filled = (
            df["Low"].iloc[i] <= prev_c
            if pct > 0
            else df["High"].iloc[i] >= prev_c
        )
        gaps.append({
            "type": "up" if pct > 0 else "down",
            "prev_close": round(prev_c, 2),
            "open": round(curr_o, 2),
            "gap_pct": round(pct, 2),
            "date": df.index[i],
            "filled": filled,
        })
    return gaps


def liquidity_zones(df: pd.DataFrame, lookback: int = 60, tol: float = 0.003) -> list[dict]:
    """
    Equal highs → buy-side liquidity (stops sitting above).
    Equal lows  → sell-side liquidity (stops sitting below).
    Returns unique zones within `tol` proximity.
    """
    highs = df["High"].tail(lookback)
    lows = df["Low"].tail(lookback)
    zones: list[dict] = []

    # Sweep to find matching pairs
    for series, zone_type, desc in [
        (highs, "buy_side", "Equal highs — stop-hunt target above"),
        (lows, "sell_side", "Equal lows — stop-hunt target below"),
    ]:
        vals = series.values
        for i in range(len(vals) - 1):
            for j in range(i + 1, len(vals)):
                if abs(vals[i] - vals[j]) / (vals[i] + 1e-9) < tol:
                    level = round((vals[i] + vals[j]) / 2, 2)
                    zones.append({"type": zone_type, "level": level, "description": desc})
                    break  # one match per index is enough

    # Deduplicate levels that are within 0.5% of each other
    unique: list[dict] = []
    for z in zones:
        if not any(abs(z["level"] - u["level"]) / (z["level"] + 1e-9) < 0.005 for u in unique):
            unique.append(z)

    return unique


def break_of_structure(df: pd.DataFrame, window: int = 10) -> dict:
    """
    Detect the most recent Break of Structure.
    BOS bullish: price closes above the last swing high.
    BOS bearish: price closes below the last swing low.
    """
    if len(df) < window + 2:
        return {"bos": None}

    recent = df.tail(window + 2)
    swing_high = recent["High"].iloc[:-1].max()
    swing_low = recent["Low"].iloc[:-1].min()
    close = recent["Close"].iloc[-1]

    if close > swing_high:
        return {"bos": "bullish", "level": round(swing_high, 2)}
    if close < swing_low:
        return {"bos": "bearish", "level": round(swing_low, 2)}
    return {"bos": None, "swing_high": round(swing_high, 2), "swing_low": round(swing_low, 2)}


# ── Per-timeframe summary ─────────────────────────────────────────────────────

def _tf_summary(df: pd.DataFrame, include_gaps: bool = False) -> dict:
    if df is None or df.empty or len(df) < 15:
        return {"error": "insufficient_data"}

    price = float(df["Close"].iloc[-1])
    e9 = float(ema(df, 9).iloc[-1])
    e21 = float(ema(df, 21).iloc[-1])
    e50 = float(ema(df, 50).iloc[-1]) if len(df) >= 50 else None
    vwap_val = float(vwap(df).iloc[-1])
    rsi_val = float(rsi(df).iloc[-1]) if len(df) >= 14 else None
    atr_val = float(atr(df).iloc[-1]) if len(df) >= 14 else None

    # Trend score 0–5
    score = sum([price > e9, price > e21, price > (e50 or e21),
                 e9 > e21, e21 > (e50 or e21)])
    if score >= 4:
        trend_dir = "bullish"
    elif score <= 1:
        trend_dir = "bearish"
    else:
        trend_dir = "neutral"

    # Higher highs / higher lows
    recent = df.tail(12)
    hh = float(recent["High"].iloc[-1]) > float(recent["High"].iloc[:-2].max())
    hl = float(recent["Low"].iloc[-1]) > float(recent["Low"].iloc[:-2].min())
    ll = float(recent["Low"].iloc[-1]) < float(recent["Low"].iloc[:-2].min())
    lh = float(recent["High"].iloc[-1]) < float(recent["High"].iloc[:-2].max())
    structure = "uptrend" if (hh and hl) else ("downtrend" if (ll and lh) else "ranging")

    # Order blocks near price (within 3%)
    obs = order_blocks(df)
    nearby_bull_ob = next(
        (o for o in reversed(obs)
         if o["type"] == "bullish" and o["low"] <= price and price <= o["high"] * 1.03),
        None
    )
    nearby_bear_ob = next(
        (o for o in reversed(obs)
         if o["type"] == "bearish" and o["high"] >= price and price >= o["low"] * 0.97),
        None
    )

    # FVGs
    fvgs = fair_value_gaps(df)
    unfilled_bull_fvg = [f for f in fvgs if f["type"] == "bullish" and not f["filled"]]
    unfilled_bear_fvg = [f for f in fvgs if f["type"] == "bearish" and not f["filled"]]
    in_fvg = any(f["filled"] for f in fvgs)  # price currently inside an FVG

    # Nearest unfilled FVGs as targets
    bull_fvg_targets = sorted(
        [f for f in unfilled_bull_fvg if f["mid"] < price],
        key=lambda x: price - x["mid"],
    )
    bear_fvg_targets = sorted(
        [f for f in unfilled_bear_fvg if f["mid"] > price],
        key=lambda x: x["mid"] - price,
    )

    # Liquidity
    liq = liquidity_zones(df)
    nearby_liq = [z for z in liq if abs(z["level"] - price) / price < 0.04]

    # BOS
    bos = break_of_structure(df)

    # Volume
    vol_ma = float(df["Volume"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else 1
    rel_vol = round(float(df["Volume"].iloc[-1]) / vol_ma, 2) if vol_ma > 0 else 1.0

    out = {
        "price": round(price, 2),
        "trend": {"direction": trend_dir, "structure": structure, "score": score},
        "ema": {
            "9": round(e9, 2),
            "21": round(e21, 2),
            "50": round(e50, 2) if e50 else None,
        },
        "vwap": round(vwap_val, 2),
        "vwap_dev_pct": round((price - vwap_val) / vwap_val * 100, 2),
        "rsi": round(rsi_val, 1) if rsi_val and not pd.isna(rsi_val) else None,
        "atr": round(atr_val, 3) if atr_val and not pd.isna(atr_val) else None,
        "rel_volume": rel_vol,
        "nearby_bullish_ob": {"high": round(nearby_bull_ob["high"], 2),
                               "low": round(nearby_bull_ob["low"], 2)} if nearby_bull_ob else None,
        "nearby_bearish_ob": {"high": round(nearby_bear_ob["high"], 2),
                               "low": round(nearby_bear_ob["low"], 2)} if nearby_bear_ob else None,
        "unfilled_bull_fvgs": len(unfilled_bull_fvg),
        "unfilled_bear_fvgs": len(unfilled_bear_fvg),
        "price_in_fvg": in_fvg,
        "nearest_bull_fvg_below": {"mid": round(bull_fvg_targets[0]["mid"], 2),
                                    "top": round(bull_fvg_targets[0]["top"], 2),
                                    "bottom": round(bull_fvg_targets[0]["bottom"], 2)}
                                   if bull_fvg_targets else None,
        "nearest_bear_fvg_above": {"mid": round(bear_fvg_targets[0]["mid"], 2),
                                    "top": round(bear_fvg_targets[0]["top"], 2),
                                    "bottom": round(bear_fvg_targets[0]["bottom"], 2)}
                                   if bear_fvg_targets else None,
        "nearby_liquidity": nearby_liq,
        "break_of_structure": bos,
    }

    if include_gaps:
        gaps = session_gaps(df)
        unfilled = [g for g in gaps if not g["filled"]]
        out["unfilled_gaps"] = [
            {"type": g["type"], "level": g["prev_close"], "gap_pct": g["gap_pct"]}
            for g in unfilled[-3:]
        ]

    return out


# ── Full multi-timeframe analysis ─────────────────────────────────────────────

def full_analysis(symbol: str, df_map: dict) -> dict:
    """
    df_map keys: "daily", "4h", "1h", "15m"
    Returns comprehensive TA across all timeframes.
    """
    result = {"symbol": symbol, "timeframes": {}}
    for tf, df in df_map.items():
        result["timeframes"][tf] = _tf_summary(df, include_gaps=(tf == "daily"))
    return result
