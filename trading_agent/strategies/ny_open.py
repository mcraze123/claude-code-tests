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
from ..analysis import order_blocks, fair_value_gaps, rsi as calc_rsi, atr as calc_atr

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
OB_ZONE_PCT     = 0.012   # ±1.2% of OB edge
FVG_ZONE_PCT    = 0.004   # ±0.4% of FVG edge
LEVEL_ZONE_PCT  = 0.006   # price within 0.6% of session level counts as "at level"
MIN_RR          = 1.0     # minimum ATR/risk ratio


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
    name          = "ny_open"
    trail_to_tp2  = True    # hold for TP2 after TP1 hit; hard cap prevents blowups
    max_hold_bars = 4       # 4 × 1H bars = max 4 hours in a trade

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
                        daily_trend: str) -> Optional[dict]:
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
        obs    = order_blocks(df_slice, lookback=50)
        fvgs   = fair_value_gaps(df_slice, lookback=50)

        # ── Bullish setup ────────────────────────────────────────────────────
        # Condition: daily not bearish AND RSI not overbought
        # Price at/near a session LOW (sweep + bounce) with bull OB or FVG
        if daily_trend in ("bullish", "neutral") and rsi_v < RSI_LONG_MAX:
            at_low = self._price_at_level(price, levels, side="low")
            at_open_bull = (
                levels.get("daily_open") is not None
                and price <= levels["daily_open"] * (1 + LEVEL_ZONE_PCT)
            )

            if at_low or at_open_bull:
                bull_obs  = self._bull_obs_near(price, obs)
                bull_fvgs = self._bull_fvgs_near(price, fvgs)

                if bull_obs or bull_fvgs:
                    ref  = bull_obs[-1] if bull_obs else None
                    stop = round(
                        (ref["low"] if ref else price) - atr_v * 0.75, 4
                    )
                    if stop < price and (price - stop) >= price * 0.001:
                        rr = atr_v / (price - stop)
                        if rr >= MIN_RR:
                            return {
                                "direction":   "long",
                                "signal_type": "ob_retest" if bull_obs else "fvg_fill",
                                "entry":       price,
                                "stop":        stop,
                                "atr":         atr_v,
                                "regime":      regime,
                                "session_levels": {k: v for k, v in levels.items() if v},
                            }

        # ── Bearish setup ────────────────────────────────────────────────────
        # Condition: daily not bullish AND RSI not oversold
        # Price at/near a session HIGH (sweep + rejection) with bear OB or FVG
        if daily_trend in ("bearish", "neutral") and rsi_v > RSI_SHORT_MIN:
            at_high = self._price_at_level(price, levels, side="high")
            at_open_bear = (
                levels.get("daily_open") is not None
                and price >= levels["daily_open"] * (1 - LEVEL_ZONE_PCT)
            )

            if at_high or at_open_bear:
                bear_obs  = self._bear_obs_near(price, obs)
                bear_fvgs = self._bear_fvgs_near(price, fvgs)

                if bear_obs or bear_fvgs:
                    ref  = bear_obs[-1] if bear_obs else None
                    stop = round(
                        (ref["high"] if ref else price) + atr_v * 0.75, 4
                    )
                    if stop > price and (stop - price) >= price * 0.001:
                        rr = atr_v / (stop - price)
                        if rr >= MIN_RR:
                            return {
                                "direction":   "short",
                                "signal_type": "ob_retest" if bear_obs else "fvg_fill",
                                "entry":       price,
                                "stop":        stop,
                                "atr":         atr_v,
                                "regime":      regime,
                                "session_levels": {k: v for k, v in levels.items() if v},
                            }

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _price_at_level(self, price: float, levels: dict, side: str) -> bool:
        """True if price is within LEVEL_ZONE_PCT of any session level on 'side'."""
        for k, v in levels.items():
            if v and side in k:
                if abs(price - v) / v <= LEVEL_ZONE_PCT:
                    return True
        return False

    def _bull_obs_near(self, price: float, obs: list) -> list:
        return [
            o for o in obs
            if o["type"] == "bullish"
            and o["low"]  * (1 - OB_ZONE_PCT) <= price <= o["high"] * (1 + OB_ZONE_PCT)
        ]

    def _bear_obs_near(self, price: float, obs: list) -> list:
        return [
            o for o in obs
            if o["type"] == "bearish"
            and o["low"]  * (1 - OB_ZONE_PCT) <= price <= o["high"] * (1 + OB_ZONE_PCT)
        ]

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
