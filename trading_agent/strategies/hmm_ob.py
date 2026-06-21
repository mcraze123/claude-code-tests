"""
HMM-gated OB-only strategy with quality filters.

Improvements over hmm_smc:
  1. Only OB retests — FVG fills and VWAP reversion disabled
  2. HMM must show "trending" regime (not just non-volatile)
  3. Daily trend must be confirmed — "neutral" is rejected
  4. OB recency: OB must be ≤ OB_RECENCY_BARS old
  5. Volume confirmation: impulse candle that created the OB must be ≥ IMPULSE_RVOL × avg vol
  6. Wider stop: OB_STOP_ATR × ATR below/above OB edge instead of fixed 0.3%
"""

import pandas as pd
from typing import Optional

from .base import BaseStrategy
from .hmm_filter import HMMRegimeFilter
from ..analysis import order_blocks, rsi as calc_rsi, atr as calc_atr

OB_RECENCY_BARS = 15    # reject OBs older than this many bars
IMPULSE_RVOL    = 1.5   # impulse candle must exceed this × 20-bar avg volume
OB_STOP_ATR     = 0.5   # stop = OB edge ± (OB_STOP_ATR × ATR)
RSI_LONG_MAX    = 60    # RSI ceiling for long entries
RSI_SHORT_MIN   = 40    # RSI floor for short entries


class HMMOBStrategy(BaseStrategy):
    name = "hmm_ob"

    def __init__(self) -> None:
        self._filter = HMMRegimeFilter(n_states=3, lookback=60)

    def fit(self, df: pd.DataFrame) -> None:
        self._filter.fit(df)

    def generate_signal(self, df_slice: pd.DataFrame, daily_trend: str) -> Optional[dict]:
        # Gate 1: regime must be trending
        regime = self._filter.get_regime(df_slice)
        if regime != "trending":
            return None

        # Gate 2: need a committed daily trend — no neutral
        if daily_trend not in ("bullish", "bearish"):
            return None

        if len(df_slice) < 22:
            return None

        price = float(df_slice["Close"].iloc[-1])
        rsi_s = calc_rsi(df_slice)
        rsi_v = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0
        atr_s = calc_atr(df_slice)
        atr_v = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else price * 0.01

        avg_vol = float(
            df_slice["Volume"].rolling(20, min_periods=5).mean().iloc[-1]
        )
        obs = order_blocks(df_slice, lookback=30)

        def _age(ob: dict) -> int:
            try:
                pos = df_slice.index.get_loc(ob["date"])
                return len(df_slice) - 1 - pos
            except KeyError:
                return 999

        def _impulse_rvol(ob: dict) -> float:
            try:
                pos = df_slice.index.get_loc(ob["date"])
                if pos + 1 >= len(df_slice):
                    return 0.0
                vol = float(df_slice["Volume"].iloc[pos + 1])
                return vol / avg_vol if avg_vol > 0 else 0.0
            except KeyError:
                return 0.0

        def _viable(stop: float) -> bool:
            risk = abs(price - stop)
            # TP1 = entry + 1×ATR  →  min R:R = ATR / risk ≥ 1.2
            return risk >= price * 0.001 and atr_v / risk >= 1.2

        # ── Bullish OB retest ─────────────────────────────────────────────────
        if daily_trend == "bullish" and rsi_v < RSI_LONG_MAX:
            candidates = [
                o for o in obs
                if o["type"] == "bullish"
                and o["low"] * 0.99 <= price <= o["high"] * 1.01
                and _age(o) <= OB_RECENCY_BARS
                and _impulse_rvol(o) >= IMPULSE_RVOL
            ]
            if candidates:
                ob   = candidates[-1]
                stop = round(ob["low"] - atr_v * OB_STOP_ATR, 4)
                if _viable(stop) and stop < price:
                    return {"direction": "long", "signal_type": "ob_retest",
                            "entry": price, "stop": stop, "atr": atr_v,
                            "regime": regime}

        # ── Bearish OB retest ─────────────────────────────────────────────────
        if daily_trend == "bearish" and rsi_v > RSI_SHORT_MIN:
            candidates = [
                o for o in obs
                if o["type"] == "bearish"
                and o["low"] * 0.99 <= price <= o["high"] * 1.01
                and _age(o) <= OB_RECENCY_BARS
                and _impulse_rvol(o) >= IMPULSE_RVOL
            ]
            if candidates:
                ob   = candidates[-1]
                stop = round(ob["high"] + atr_v * OB_STOP_ATR, 4)
                if _viable(stop) and stop > price:
                    return {"direction": "short", "signal_type": "ob_retest",
                            "entry": price, "stop": stop, "atr": atr_v,
                            "regime": regime}

        return None
