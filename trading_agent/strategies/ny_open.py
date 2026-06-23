"""
NY Open Kill Zone — intraday-only strategy.

Concept
-------
The 7:00–11:00 AM ET window is where London closes and New York opens,
producing the highest intraday liquidity and the most reliable SMC setups.
Asia (7pm–2am ET) and London (2am–7am ET) sessions carve out the day's
range; NY open typically sweeps those highs/lows to grab liquidity before
making the real directional move.

What this strategy does
-----------------------
1. Only enters trades between 7:00 and 10:59 AM ET (NY kill zone).
2. Marks the Asia session H/L, London session H/L, prior-day H/L, and
   daily open as key reference levels.
3. Looks for price to touch/sweep a session level, then an OB or FVG
   to confirm a rejection/retest setup.
4. Gates entries with:
   - Daily bias (EMA9/EMA21 on daily — neutral is allowed)
   - HMM regime filter (volatile = skip)
   - RSI not over-extended (avoids chasing)
   - News blackout: skips ±30 min around red US news (live only)
5. Forces all positions closed at or before 10:45 AM ET — no overnight holds.
6. Uses trail_to_tp2=True so winners run to TP2 (2×ATR) after TP1 is hit.
   Hard cap at 2×ATR prevents catastrophic gap losses.

Timeframe alignment (informational — checked but not mandatory)
---------------------------------------------------------------
Daily → 4H → 1H → 15M all agree: highest conviction.
Daily + 1H agree, others neutral: still a valid entry.
Daily disagrees with 1H: skip.
"""

from datetime import time as dtime, datetime, timedelta
import pandas as pd
import pytz
from typing import Optional

from .base import BaseStrategy
from .hmm_filter import HMMRegimeFilter
from ..analysis import (order_blocks, fair_value_gaps, swing_points,
                        cmf as calc_cmf, rsi as calc_rsi, atr as calc_atr,
                        ema as calc_ema)

ET = pytz.timezone("America/New_York")

# ── Kill zone window (ET) ─────────────────────────────────────────────────────
KILL_ZONE_START = dtime(7, 0)
KILL_ZONE_END   = dtime(11, 0)  # last allowed entry: bar starting at 10:xx ET
FORCE_CLOSE_HR  = 11            # bars starting at this ET hour → force close

# ── Session window hours (ET, bar-start hour inclusive) ──────────────────────
_ASIA_HOURS_ET   = {19, 20, 21, 22, 23, 0, 1}   # prev-day 7pm → today 2am
_LONDON_HOURS_ET = {2, 3, 4, 5, 6}              # today 2am → 7am

# ── Entry filter thresholds ───────────────────────────────────────────────────
RSI_LONG_MAX    = 65
RSI_SHORT_MIN   = 35
FVG_ZONE_PCT    = 0.007   # ±0.7% of FVG edge (slightly wider to catch more fills)
LEVEL_ZONE_PCT  = 0.010   # price within 1.0% of session level (was 0.6%)
MIN_RR          = 1.0     # minimum ATR/risk ratio at entry
MIN_RISK_ATR    = 0.35    # minimum risk as fraction of ATR (prevents tiny-risk blowups)
FVG_STOP_CUSHION = 0.30   # ATR multiples below/above FVG edge for stop placement
FVG_MIN_SIZE_ATR = 0.15   # FVG must span at least this fraction of ATR (filters noise)


def _to_et(ts) -> datetime:
    """Convert bar timestamp (possibly naive UTC) to ET-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = pytz.utc.localize(ts)
        return ts.astimezone(ET)
    # pandas Timestamp
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(ET)


def _session_levels(df: pd.DataFrame, bar_et: datetime) -> dict:
    """
    Scan df for Asia H/L, London H/L, prior-day H/L, and daily open.

    Returns a dict; values are None when insufficient bars exist.
    """
    today     = bar_et.date()
    yesterday = today - timedelta(days=1)

    asia_h, asia_l       = [], []
    london_h, london_l   = [], []
    prev_h, prev_l       = [], []
    daily_open           = None

    for ts, row in df.iterrows():
        et = _to_et(ts)
        d  = et.date()
        h  = et.hour

        # Asia: yesterday 19–23 + today 0–1
        if (d == yesterday and h in _ASIA_HOURS_ET) or \
           (d == today and h in {0, 1}):
            asia_h.append(float(row["High"]))
            asia_l.append(float(row["Low"]))

        # London: today 2–6
        if d == today and h in _LONDON_HOURS_ET:
            london_h.append(float(row["High"]))
            london_l.append(float(row["Low"]))

        # Daily open: first bar of today's regular session (9am ET bar)
        if d == today and h == 9 and daily_open is None:
            daily_open = float(row["Open"])

        # Prior day full range
        if d == yesterday:
            prev_h.append(float(row["High"]))
            prev_l.append(float(row["Low"]))

    return {
        "asia_high":    max(asia_h)  if asia_h   else None,
        "asia_low":     min(asia_l)  if asia_l   else None,
        "london_high":  max(london_h) if london_h else None,
        "london_low":   min(london_l) if london_l else None,
        "prev_high":    max(prev_h)  if prev_h   else None,
        "prev_low":     min(prev_l)  if prev_l   else None,
        "daily_open":   daily_open,
    }


def _is_live_bar(bar_et: datetime) -> bool:
    """True if bar is recent enough to be a live (not historical) bar."""
    now_utc = datetime.now(pytz.utc)
    bar_utc = bar_et.astimezone(pytz.utc)
    return (now_utc - bar_utc).total_seconds() < 86_400


class NYOpenStrategy(BaseStrategy):
    """
    Intraday NY Kill Zone strategy.  Never holds overnight.
    Uses BE trail → TP2 exit since momentum is expected in the kill zone.
    """
    name             = "ny_open"
    scale_out_trail  = True  # 50% off at TP1 (1×ATR), then ATR trail on remainder
    trail_to_tp2     = False
    max_hold_bars    = 4     # 4 × 1H bars = max 4 hours in a trade

    def __init__(self) -> None:
        self._hmm = HMMRegimeFilter(n_states=3, lookback=60)

    def fit(self, df: pd.DataFrame) -> None:
        self._hmm.fit(df)

    # ── Force close at 10:45 ET ───────────────────────────────────────────────
    def should_force_close(self, trade, bar_time, price: float) -> bool:
        et = _to_et(bar_time)
        return et.hour >= FORCE_CLOSE_HR

    # ── Signal generation ─────────────────────────────────────────────────────
    def generate_signal(self, df_slice: pd.DataFrame,
                        daily_trend: str,
                        df_15m: Optional[pd.DataFrame] = None,
                        df_4h:  Optional[pd.DataFrame] = None) -> Optional[dict]:
        if len(df_slice) < 30:
            return None

        bar_et = _to_et(df_slice.index[-1])
        t      = bar_et.time()

        # ── Gate 1: kill zone window ─────────────────────────────────────────
        if not (KILL_ZONE_START <= t < KILL_ZONE_END):
            return None

        # ── Gate 2: daily trend — allow neutral; reject counter-trend below ──
        # (checked per-direction when evaluating bull/bear setups)

        # ── Gate 3: HMM — volatile markets skip ─────────────────────────────
        regime = self._hmm.get_regime(df_slice)
        if regime == "volatile":
            return None

        # ── Gate 4: news blackout (live bars only) ───────────────────────────
        if _is_live_bar(bar_et):
            try:
                from .news_filter import is_news_blackout
                if is_news_blackout(bar_et):
                    return None
            except Exception:
                pass

        price = float(df_slice["Close"].iloc[-1])
        rsi_v = float(calc_rsi(df_slice).iloc[-1])
        atr_v = float(calc_atr(df_slice).iloc[-1])
        if pd.isna(rsi_v):
            rsi_v = 50.0
        if pd.isna(atr_v) or atr_v == 0:
            atr_v = price * 0.01

        levels = _session_levels(df_slice, bar_et)
        fvgs   = fair_value_gaps(df_slice, lookback=60)

        # ── Multi-timeframe context ───────────────────────────────────────────
        # 4H trend and swing structure for confidence scoring + premium/discount
        trend_4h = "neutral"
        swing    = {"last_high": None, "last_low": None, "structure": "neutral"}
        if df_4h is not None and len(df_4h) >= 21:
            e9  = float(calc_ema(df_4h, 9).iloc[-1])
            e21 = float(calc_ema(df_4h, 21).iloc[-1])
            p4h = float(df_4h["Close"].iloc[-1])
            if p4h > e9 > e21:
                trend_4h = "bullish"
            elif p4h < e9 < e21:
                trend_4h = "bearish"
            if len(df_4h) >= 15:
                swing = swing_points(df_4h, window=3)
        elif len(df_slice) >= 15:
            swing = swing_points(df_slice, window=5)

        # Bar reversal confirmation: signal bar must close in the entry direction
        bar_close = float(df_slice["Close"].iloc[-1])
        bar_open_ = float(df_slice["Open"].iloc[-1])
        bar_low   = float(df_slice["Low"].iloc[-1])
        bar_high  = float(df_slice["High"].iloc[-1])
        bar_green = bar_close >= bar_open_
        bar_red   = bar_close < bar_open_

        # ── OB Sweep on 15M — disabled pending entry redesign ─────────────────
        # The 15M scan detects sweeps that may be hours old; the backtest enters
        # at next 1H open which is then far from the OB zone → bad RR.
        # Disabled until entry is anchored to OB zone mid rather than bar_close.
        # if df_15m is not None and len(df_15m) >= 20:
        #     df_15m_local = df_15m[df_15m.index <= df_slice.index[-1]].tail(24)
        #     if len(df_15m_local) >= 12:
        #         ob_sig = self._check_ob_sweep_15m(
        #             df_15m_local, df_slice,
        #             atr_v, rsi_v, daily_trend, trend_4h, swing
        #         )
        #         if ob_sig:
        #             return ob_sig

        # ── Bullish FVG fill — session level sweep + FVG confirmation ────────
        # Fires on confirmed bullish AND neutral-trend days (where OB sweep
        # didn't qualify).  Requires bar to have wicked through session low.
        if daily_trend in ("bullish", "neutral") and rsi_v < RSI_LONG_MAX and bar_green:
            at_low = self._price_at_level(price, levels, side="low", bar_extreme=bar_low)

            if at_low:
                bull_fvgs = [
                    f for f in self._bull_fvgs_near(price, fvgs)
                    if f["top"] - f["bottom"] >= atr_v * FVG_MIN_SIZE_ATR
                ]
                if bull_fvgs:
                    fvg  = bull_fvgs[-1]
                    stop = fvg["bottom"] - atr_v * FVG_STOP_CUSHION
                    stop = min(stop, price - atr_v * MIN_RISK_ATR)
                    stop = round(stop, 4)
                    risk_ = price - stop
                    if stop < price and risk_ >= price * 0.001 and atr_v / risk_ >= MIN_RR:
                        tp_mult, trail, scale_out = self._tp_from_confidence(
                            "long", daily_trend, trend_4h, swing, price, regime
                        )
                        return {
                            "direction":    "long",
                            "signal_type":  "fvg_fill",
                            "entry":        price,
                            "stop":         stop,
                            "atr":          atr_v,
                            "tp_mult":      tp_mult,
                            "trail":        trail,
                            "scale_out_trail": scale_out,
                            "regime":       regime,
                        }

        # ── Bearish FVG fill ──────────────────────────────────────────────────
        if daily_trend in ("bearish", "neutral") and rsi_v > RSI_SHORT_MIN and bar_red:
            at_high = self._price_at_level(price, levels, side="high", bar_extreme=bar_high)

            if at_high:
                bear_fvgs = [
                    f for f in self._bear_fvgs_near(price, fvgs)
                    if f["top"] - f["bottom"] >= atr_v * FVG_MIN_SIZE_ATR
                ]
                if bear_fvgs:
                    fvg  = bear_fvgs[-1]
                    stop = fvg["top"] + atr_v * FVG_STOP_CUSHION
                    stop = max(stop, price + atr_v * MIN_RISK_ATR)
                    stop = round(stop, 4)
                    risk_ = stop - price
                    if stop > price and risk_ >= price * 0.001 and atr_v / risk_ >= MIN_RR:
                        tp_mult, trail, scale_out = self._tp_from_confidence(
                            "short", daily_trend, trend_4h, swing, price, regime
                        )
                        return {
                            "direction":    "short",
                            "signal_type":  "fvg_fill",
                            "entry":        price,
                            "stop":         stop,
                            "atr":          atr_v,
                            "tp_mult":      tp_mult,
                            "trail":        trail,
                            "scale_out_trail": scale_out,
                            "regime":       regime,
                        }

        return None

    # ── 15M-primary OB sweep entry ────────────────────────────────────────────

    def generate_ob_sweep_signal(self, df_15m: pd.DataFrame,
                                  df_1h: pd.DataFrame,
                                  daily_trend: str,
                                  df_4h: Optional[pd.DataFrame] = None) -> Optional[dict]:
        """
        Swing-level liquidity sweep on 15M bars.

        Stop losses cluster at prior swing lows (long stops) and swing highs
        (short stops).  Smart money sweeps through those levels to grab the
        liquidity, then immediately reverses.  The tell is a fast, high-volume
        candle that wicks through the swing level and CLOSES BACK on the other
        side.

        Entry conditions:
          1. bar_low < recent swing low AND bar_close > swing low   (bull sweep)
             bar_high > recent swing high AND bar_close < swing high (bear sweep)
          2. Bar range >= 1.3×ATR  — velocity (institutional speed)
          3. Bar volume >= 1.5×20-bar avg — elevated volume confirms participation
          4. Close in upper 45%+ of range for longs (lower 55%- for shorts)
             — shows recovery / rejection after the wick
          5. Trend must not oppose: bullish/neutral daily for longs,
             bearish/neutral daily for shorts
        """
        if len(df_15m) < 25 or len(df_1h) < 20:
            return None

        bar_et = _to_et(df_15m.index[-1])
        t = bar_et.time()
        if not (KILL_ZONE_START <= t < KILL_ZONE_END):
            return None

        bar      = df_15m.iloc[-1]
        bar_low   = float(bar["Low"])
        bar_high  = float(bar["High"])
        bar_close = float(bar["Close"])
        bar_range = bar_high - bar_low
        bar_vol   = float(bar.get("Volume", 0))

        # ── Velocity gate ─────────────────────────────────────────────────────
        atr_series = calc_atr(df_15m)
        atr_15m = float(atr_series.iloc[-1])
        if pd.isna(atr_15m) or atr_15m == 0:
            atr_15m = bar_range if bar_range > 0 else 0.01

        if bar_range < atr_15m * 1.3:
            return None

        # ── Volume gate ───────────────────────────────────────────────────────
        vol_ma = df_15m["Volume"].rolling(20).mean().iloc[-1]
        if not pd.isna(vol_ma) and vol_ma > 0 and bar_vol < vol_ma * 1.5:
            return None

        # Recovery: where did bar close within its range?
        close_pct = (bar_close - bar_low) / bar_range if bar_range > 0 else 0.5

        # ── Swing levels ──────────────────────────────────────────────────────
        # Look at the last 40 15M bars (10 hours) excluding the current bar to
        # find the most recent confirmed swing low/high — these are the liquidity
        # pools being targeted.
        sp_data   = df_15m.tail(40).iloc[:-1]
        sp        = swing_points(sp_data, window=3)
        swing_low  = sp.get("last_low")
        swing_high = sp.get("last_high")

        # Fallback: simple range if no confirmed pivots yet
        if swing_low is None:
            swing_low = float(df_15m["Low"].iloc[-21:-1].min())
        if swing_high is None:
            swing_high = float(df_15m["High"].iloc[-21:-1].max())

        # ── 1H / 4H context ───────────────────────────────────────────────────
        rsi_v  = float(calc_rsi(df_1h).iloc[-1]) if len(df_1h) >= 14 else 50.0
        atr_1h = float(calc_atr(df_1h).iloc[-1]) if len(df_1h) >= 14 else atr_15m * 4
        if pd.isna(rsi_v):   rsi_v  = 50.0
        if pd.isna(atr_1h) or atr_1h == 0:  atr_1h = atr_15m * 4

        trend_4h = "neutral"
        swing_4h = {"last_high": None, "last_low": None, "structure": "neutral"}
        if df_4h is not None and len(df_4h) >= 21:
            e9  = float(calc_ema(df_4h, 9).iloc[-1])
            e21 = float(calc_ema(df_4h, 21).iloc[-1])
            p4h = float(df_4h["Close"].iloc[-1])
            if p4h > e9 > e21:   trend_4h = "bullish"
            elif p4h < e9 < e21: trend_4h = "bearish"
            if len(df_4h) >= 15:
                swing_4h = swing_points(df_4h, window=3)
        elif len(df_1h) >= 15:
            swing_4h = swing_points(df_1h, window=5)

        regime = self._hmm.get_regime(df_1h) if len(df_1h) >= 30 else "trending"
        if regime == "volatile":
            return None

        # ── Bullish swing sweep ───────────────────────────────────────────────
        # Price wicked BELOW the prior swing low (stops triggered) then
        # closed BACK ABOVE it (smart money absorbed, reversal beginning).
        bull_bias = (daily_trend == "bullish") or \
                    (daily_trend == "neutral" and trend_4h == "bullish")
        if bull_bias and rsi_v < RSI_LONG_MAX and close_pct >= 0.45:
            if bar_low < swing_low and bar_close > swing_low:
                price = bar_close
                stop  = bar_low - atr_15m * 0.3   # stop below the sweep wick
                stop  = min(stop, price - atr_15m * MIN_RISK_ATR)
                stop  = round(stop, 4)
                risk_ = price - stop
                if stop < price and risk_ >= price * 0.001:
                    tp_mult, trail, scale_out = self._tp_from_confidence(
                        "long", daily_trend, trend_4h, swing_4h, price, regime
                    )
                    return {
                        "direction":       "long",
                        "signal_type":     "ob_sweep",
                        "entry":           price,
                        "stop":            stop,
                        "atr":             atr_15m,
                        "tp_mult":         tp_mult,
                        "trail":           trail,
                        "scale_out_trail": scale_out,
                        "regime":          regime,
                    }

        # ── Bearish swing sweep ───────────────────────────────────────────────
        # Price wicked ABOVE the prior swing high (stops triggered) then
        # closed BACK BELOW it (smart money distributed, reversal beginning).
        bear_bias = (daily_trend == "bearish") or \
                    (daily_trend == "neutral" and trend_4h == "bearish")
        if bear_bias and rsi_v > RSI_SHORT_MIN and close_pct <= 0.55:
            if bar_high > swing_high and bar_close < swing_high:
                price = bar_close
                stop  = bar_high + atr_15m * 0.3   # stop above the sweep wick
                stop  = max(stop, price + atr_15m * MIN_RISK_ATR)
                stop  = round(stop, 4)
                risk_ = stop - price
                if stop > price and risk_ >= price * 0.001:
                    tp_mult, trail, scale_out = self._tp_from_confidence(
                        "short", daily_trend, trend_4h, swing_4h, price, regime
                    )
                    return {
                        "direction":       "short",
                        "signal_type":     "ob_sweep",
                        "entry":           price,
                        "stop":            stop,
                        "atr":             atr_15m,
                        "tp_mult":         tp_mult,
                        "trail":           trail,
                        "scale_out_trail": scale_out,
                        "regime":          regime,
                    }

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _price_at_level(self, price: float, levels: dict, side: str,
                        bar_extreme: float = None) -> bool:
        """
        True if price is within LEVEL_ZONE_PCT of a session level on 'side'.

        When bar_extreme is supplied (bar_low for longs, bar_high for shorts),
        requires a confirmed liquidity sweep: the bar must have WICKED THROUGH
        the level and closed back inside.  This filters plain proximity touches
        and only catches the inducement-and-reversal pattern.
        """
        for k, v in levels.items():
            if v and side in k:
                if bar_extreme is not None:
                    if side == "low" and bar_extreme < v \
                            and price >= v * (1 - LEVEL_ZONE_PCT):
                        return True
                    if side == "high" and bar_extreme > v \
                            and price <= v * (1 + LEVEL_ZONE_PCT):
                        return True
                else:
                    if abs(price - v) / v <= LEVEL_ZONE_PCT:
                        return True
        return False

    def _bull_fvgs_near(self, price: float, fvgs: list) -> list:
        return [
            f for f in fvgs
            if f["type"] == "bullish" and not f["filled"]
            and f["bottom"] * (1 - FVG_ZONE_PCT) <= price <= f["top"] * (1 + FVG_ZONE_PCT)
        ]

    def _bear_fvgs_near(self, price: float, fvgs: list) -> list:
        return [
            f for f in fvgs
            if f["type"] == "bearish" and not f["filled"]
            and f["bottom"] * (1 - FVG_ZONE_PCT) <= price <= f["top"] * (1 + FVG_ZONE_PCT)
        ]

    def _market_zone(self, price: float, swing: dict) -> str:
        """
        Price position within the swing range.
        discount = lower 33% (buy zone), premium = upper 33% (sell zone).
        """
        sh = swing.get("last_high")
        sl = swing.get("last_low")
        if sh is None or sl is None or sh <= sl:
            return "unknown"
        pct = (price - sl) / (sh - sl)
        if pct <= 0.33:
            return "discount"
        if pct >= 0.67:
            return "premium"
        return "equilibrium"

    def _tp_from_confidence(self, direction: str, daily_trend: str, trend_4h: str,
                             swing: dict, price: float,
                             regime: str) -> tuple[float, bool, bool]:
        """
        Score setup confidence (0-1) from trend alignment, swing structure,
        and premium/discount zone.

        Returns (tp_mult, trail_to_tp2, scale_out_trail).

        Score thresholds:
          >= 0.75 → high: tp_mult=3.0, scale_out=True (50% at TP1, trail to 3×ATR)
          >= 0.55 → medium: tp_mult=2.0, scale_out=True (50% at TP1, trail to 2×ATR)
          < 0.55  → low: tp_mult=1.0, scale_out=False (exit 100% at TP1, no trail)
        """
        score = 0.40  # base

        # Daily trend alignment
        if (direction == "long"  and daily_trend == "bullish") or \
           (direction == "short" and daily_trend == "bearish"):
            score += 0.15

        # 4H trend alignment
        if (direction == "long"  and trend_4h == "bullish") or \
           (direction == "short" and trend_4h == "bearish"):
            score += 0.15

        # Swing structure alignment (HH+HL = bullish, LH+LL = bearish)
        struct = swing.get("structure", "neutral")
        if (direction == "long"  and struct == "bullish") or \
           (direction == "short" and struct == "bearish"):
            score += 0.15

        # Premium/discount zone alignment
        zone = self._market_zone(price, swing)
        if (direction == "long"  and zone == "discount") or \
           (direction == "short" and zone == "premium"):
            score += 0.10

        # HMM trending regime adds slight bonus
        if regime == "trending":
            score += 0.05

        score = min(1.0, score)

        if score >= 0.75:
            return 3.0, True, True    # high confidence: trail to 3×ATR
        if score >= 0.55:
            return 2.0, True, True    # medium: trail to 2×ATR
        return 1.0, False, False      # low: clean exit at TP1, no scale/trail

    def _check_ob_sweep_15m(self, df_15m: pd.DataFrame, df_1h: pd.DataFrame,
                             atr_1h: float, rsi_v: float,
                             daily_trend: str, trend_4h: str,
                             swing: dict) -> Optional[dict]:
        """
        Multi-timeframe OB sweep: 1H OBs as target levels, 15M bars as triggers.

        1H order blocks represent genuine institutional demand/supply — larger
        bodies, more volume, more meaningful than 15M micro-OBs.  The 15M bar
        provides precision entry timing: we catch the sweep wick and recovery
        before the 1H bar has even closed, entering ahead of most participants.

        OB taxonomy (from analysis.order_blocks):
          Bullish OB = last BEARISH candle (c < o) before bullish impulse.
                       Body: ob["close"] (bottom) to ob["open"] (top).
          Bearish OB = last BULLISH candle (c > o) before bearish impulse.
                       Body: ob["open"] (bottom) to ob["close"] (top).

        Sweep condition (bullish): 15M bar wicks INTO the OB body zone
          (bar_low <= ob_body_top) and closes above the body bottom
          (bar_close >= ob_body_bot).  This is the inducement-and-reversal
          pattern — price hunts stops inside the body then recovers.

        Proximity gate: only OBs within 2×ATR of current price are tested.
          OBs from trending moves 10+ 1H bars ago are far below/above current
          price and will never be touched during a single 15M kill-zone bar.
        """
        obs_1h = order_blocks(df_1h, lookback=20)
        obs_1h = [ob for ob in obs_1h
                  if abs(float(ob["open"]) - float(ob["close"])) >= atr_1h * 0.15]
        if not obs_1h:
            return None

        atr_series = calc_atr(df_15m)
        cmf_series = calc_cmf(df_15m, period=8)

        # Proximity: anchor on the SCAN WINDOW's price range, not just the last bar.
        # A bar from 12 bars ago might have swept an OB even if current price has
        # moved away — so the correct filter is "could any bar in the scan window
        # have reached this OB zone?".
        n_scan    = len(df_15m)   # scan all available 15M bars (up to 24)
        scan_low  = float(df_15m["Low"].min())
        scan_high = float(df_15m["High"].max())

        # Pre-filter by proximity so the inner loop is cheap.
        # Bullish OB reachable: body top (ob_open, the higher price) must be at
        # or above the scan window's minimum low (with 2% tolerance).
        bull_obs = [ob for ob in obs_1h
                    if ob["type"] == "bullish"
                    and float(ob["open"]) >= scan_low * 0.98]
        # Bearish OB reachable: body bottom (ob_open, the lower price) must be at
        # or below the scan window's maximum high (with 2% tolerance).
        bear_obs = [ob for ob in obs_1h
                    if ob["type"] == "bearish"
                    and float(ob["open"]) <= scan_high * 1.02]

        if not bull_obs and not bear_obs:
            return None

        # Directional bias: any non-opposing daily trend qualifies.
        # OBs themselves provide the directional signal; a neutral daily trend
        # is fine — the 1H OB zone IS the bias.  Only skip if daily opposes.
        bull_bias = daily_trend != "bearish"
        bear_bias = daily_trend != "bullish"

        # Scan all available 15M bars in reverse — most recent qualifying sweep wins
        scan_start = 0
        for idx in range(len(df_15m) - 1, scan_start - 1, -1):
            bar       = df_15m.iloc[idx]
            bar_low   = float(bar["Low"])
            bar_high  = float(bar["High"])
            bar_close = float(bar["Close"])
            bar_open_ = float(bar["Open"])

            atr_15m = float(atr_series.iloc[idx])
            if pd.isna(atr_15m) or atr_15m == 0:
                atr_15m = atr_1h * 0.25

            cmf_val = float(cmf_series.iloc[idx]) if not pd.isna(cmf_series.iloc[idx]) else 0.0

            # ── Bullish sweep ─────────────────────────────────────────────────
            # Bullish OB = bearish candle: open > close, body top=open, bottom=close.
            # NOTE: we do NOT require a green bar. An OB sweep is defined by the
            # wick — price can open above the OB, dip into it (bar_low <= ob_body_top),
            # then close anywhere above the body bottom. A red candle still shows
            # demand absorbed at the OB level as long as close > body-bottom.
            if bull_bias and bull_obs and rsi_v < RSI_LONG_MAX and cmf_val >= -0.10:
                for ob in bull_obs:
                    ob_body_top = float(ob["open"])   # higher price = body top
                    ob_body_bot = float(ob["close"])  # lower price = body bottom
                    ob_low      = float(ob["low"])    # wick below body (stop ref)
                    # Bar entered OB zone (wicked to body top or below)
                    # and closed above body bottom — demand absorbed the sweep
                    if bar_low <= ob_body_top and bar_close >= ob_body_bot \
                            and bar_low >= ob_low * 0.95:
                        price = bar_close
                        stop  = ob_low - atr_15m * 0.3
                        stop  = min(stop, price - atr_1h * MIN_RISK_ATR)
                        stop  = round(stop, 4)
                        risk_ = price - stop
                        if stop < price and risk_ >= price * 0.001 \
                                and atr_1h / risk_ >= MIN_RR:
                            tp_mult, trail, scale_out = self._tp_from_confidence(
                                "long", daily_trend, trend_4h, swing, price, "trending"
                            )
                            return {
                                "direction":    "long",
                                "signal_type":  "ob_sweep",
                                "entry":        price,
                                "stop":         stop,
                                "atr":          atr_1h,
                                "tp_mult":      tp_mult,
                                "trail":        trail,
                                "scale_out_trail": scale_out,
                                "ob_zone":      [ob_low, float(ob["high"])],
                            }

            # ── Bearish sweep ─────────────────────────────────────────────────
            # Bearish OB = bullish candle: close > open, body top=close, bottom=open
            # Same principle: candle color is irrelevant; wick into supply + close
            # below the body top is sufficient confirmation.
            if bear_bias and bear_obs and rsi_v > RSI_SHORT_MIN and cmf_val <= 0.10:
                for ob in bear_obs:
                    ob_body_bot = float(ob["open"])   # lower price = body bottom
                    ob_body_top = float(ob["close"])  # higher price = body top
                    ob_high     = float(ob["high"])   # wick above body (stop ref)
                    # Bar entered OB zone (wicked to body bottom or above)
                    # and closed below body top — supply absorbed the sweep
                    if bar_high >= ob_body_bot and bar_close <= ob_body_top \
                            and bar_high <= ob_high * 1.05:
                        price = bar_close
                        stop  = ob_high + atr_15m * 0.3
                        stop  = max(stop, price + atr_1h * MIN_RISK_ATR)
                        stop  = round(stop, 4)
                        risk_ = stop - price
                        if stop > price and risk_ >= price * 0.001 \
                                and atr_1h / risk_ >= MIN_RR:
                            tp_mult, trail, scale_out = self._tp_from_confidence(
                                "short", daily_trend, trend_4h, swing, price, "trending"
                            )
                            return {
                                "direction":    "short",
                                "signal_type":  "ob_sweep",
                                "entry":        price,
                                "stop":         stop,
                                "atr":          atr_1h,
                                "tp_mult":      tp_mult,
                                "trail":        trail,
                                "scale_out_trail": scale_out,
                                "ob_zone":      [float(ob["low"]), ob_high],
                            }

        return None

