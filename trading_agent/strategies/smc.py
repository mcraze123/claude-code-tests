"""
Smart Money Concepts strategy — order blocks, FVGs, intraday VWAP reversion.
Extracted from backtest.py into a pluggable strategy class.
"""

import pandas as pd
from typing import Optional

from .base import BaseStrategy
from ..analysis import (
    order_blocks, fair_value_gaps,
    rsi as calc_rsi, atr as calc_atr,
    intraday_vwap,
)


class SMCStrategy(BaseStrategy):
    name = "smc"

    def generate_signal(self, df_slice: pd.DataFrame, daily_trend: str) -> Optional[dict]:
        if len(df_slice) < 20:
            return None

        price = float(df_slice["Close"].iloc[-1])
        hi    = float(df_slice["High"].iloc[-1])
        lo    = float(df_slice["Low"].iloc[-1])

        rsi_s = calc_rsi(df_slice)
        rsi_v = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0
        atr_s = calc_atr(df_slice)
        atr_v = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else price * 0.01

        vwap_v   = intraday_vwap(df_slice)
        vwap_dev = (price - vwap_v) / vwap_v * 100

        obs  = order_blocks(df_slice, lookback=30)
        fvgs = fair_value_gaps(df_slice, lookback=40)

        def _viable(stop: float) -> bool:
            risk = abs(price - stop)
            return risk >= price * 0.001 and (atr_v * 1.5) / risk >= 1.4

        # ── Bullish OB retest ────────────────────────────────────────────────
        if daily_trend in ("bullish", "neutral") and rsi_v < 62:
            bull_obs = [
                o for o in obs
                if o["type"] == "bullish"
                and o["low"] * 0.99 <= price <= o["high"] * 1.01
            ]
            if bull_obs:
                ob   = bull_obs[-1]
                stop = ob["low"] * 0.997
                if _viable(stop):
                    return {"direction": "long", "signal_type": "ob_retest",
                            "entry": price, "stop": round(stop, 4), "atr": atr_v}

        # ── Bearish OB retest ────────────────────────────────────────────────
        if daily_trend in ("bearish", "neutral") and rsi_v > 38:
            bear_obs = [
                o for o in obs
                if o["type"] == "bearish"
                and o["low"] * 0.99 <= price <= o["high"] * 1.01
            ]
            if bear_obs:
                ob   = bear_obs[-1]
                stop = ob["high"] * 1.003
                if _viable(stop):
                    return {"direction": "short", "signal_type": "ob_retest",
                            "entry": price, "stop": round(stop, 4), "atr": atr_v}

        # ── Bullish FVG fill ─────────────────────────────────────────────────
        if daily_trend in ("bullish", "neutral") and rsi_v < 58:
            bull_fvgs = [
                f for f in fvgs
                if f["type"] == "bullish" and not f["filled"]
                and f["bottom"] * 0.997 <= price <= f["top"] * 1.003
            ]
            if bull_fvgs:
                fvg  = bull_fvgs[-1]
                stop = fvg["bottom"] * 0.996
                if _viable(stop):
                    return {"direction": "long", "signal_type": "fvg_fill",
                            "entry": price, "stop": round(stop, 4), "atr": atr_v}

        # ── Bearish FVG fill ─────────────────────────────────────────────────
        if daily_trend in ("bearish", "neutral") and rsi_v > 42:
            bear_fvgs = [
                f for f in fvgs
                if f["type"] == "bearish" and not f["filled"]
                and f["bottom"] * 0.997 <= price <= f["top"] * 1.003
            ]
            if bear_fvgs:
                fvg  = bear_fvgs[-1]
                stop = fvg["top"] * 1.004
                if _viable(stop):
                    return {"direction": "short", "signal_type": "fvg_fill",
                            "entry": price, "stop": round(stop, 4), "atr": atr_v}

        # ── Intraday VWAP mean reversion ─────────────────────────────────────
        if vwap_dev < -2.0 and rsi_v < 38:
            stop = lo - atr_v * 0.5
            if _viable(stop):
                return {"direction": "long", "signal_type": "vwap_reversion",
                        "entry": price, "stop": round(stop, 4), "atr": atr_v}

        if vwap_dev > 2.0 and rsi_v > 62:
            stop = hi + atr_v * 0.5
            if _viable(stop):
                return {"direction": "short", "signal_type": "vwap_reversion",
                        "entry": price, "stop": round(stop, 4), "atr": atr_v}

        return None
