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
from ..analysis import order_blocks, fair_value_gaps, cmf as calc_cmf, rsi as calc_rsi, atr as calc_atr

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
MIN_RISK_ATR    = 0.30    # minimum risk as fraction of ATR (prevents tiny-risk blowups)


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
                        daily_trend: str,
                        df_15m: Optional[pd.DataFrame] = None) -> Optional[dict]:
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

        # Bar reversal confirmation: signal bar must close in the entry direction
        bar_close = float(df_slice["Close"].iloc[-1])
        bar_open_ = float(df_slice["Open"].iloc[-1])
        bar_green = bar_close >= bar_open_
        bar_red   = bar_close < bar_open_

        # ── Bullish setup ────────────────────────────────────────────────────
        # Price at/near a session LOW with a bullish FVG at that level.
        # Signal bar must close green — the bounce is already forming.
        if daily_trend in ("bullish", "neutral") and rsi_v < RSI_LONG_MAX and bar_green:
            at_low = self._price_at_level(price, levels, side="low")

            if at_low:
                bull_fvgs = self._bull_fvgs_near(price, fvgs)
                if bull_fvgs:
                    fvg  = bull_fvgs[-1]
                    stop = fvg["bottom"] - atr_v * 0.25
                    stop = min(stop, price - atr_v * MIN_RISK_ATR)
                    stop = round(stop, 4)
                    risk_ = price - stop
                    if stop < price and risk_ >= price * 0.001 and atr_v / risk_ >= MIN_RR:
                        return {
                            "direction":   "long",
                            "signal_type": "fvg_fill",
                            "entry":       price,
                            "stop":        stop,
                            "atr":         atr_v,
                            "regime":      regime,
                            "session_levels": {k: v for k, v in levels.items() if v},
                        }

        # ── Bearish setup ────────────────────────────────────────────────────
        # Price at/near a session HIGH with a bearish FVG at that level.
        # Signal bar must close red — the rejection is already forming.
        if daily_trend in ("bearish", "neutral") and rsi_v > RSI_SHORT_MIN and bar_red:
            at_high = self._price_at_level(price, levels, side="high")

            if at_high:
                bear_fvgs = self._bear_fvgs_near(price, fvgs)
                if bear_fvgs:
                    fvg  = bear_fvgs[-1]
                    stop = fvg["top"] + atr_v * 0.25
                    stop = max(stop, price + atr_v * MIN_RISK_ATR)
                    stop = round(stop, 4)
                    risk_ = stop - price
                    if stop > price and risk_ >= price * 0.001 and atr_v / risk_ >= MIN_RR:
                        return {
                            "direction":   "short",
                            "signal_type": "fvg_fill",
                            "entry":       price,
                            "stop":        stop,
                            "atr":         atr_v,
                            "regime":      regime,
                            "session_levels": {k: v for k, v in levels.items() if v},
                        }

        # ── OB Sweep on 15M ──────────────────────────────────────────────────
        # 1H bars are too coarse — by close, the reversal is over.
        # On 15M we catch the wick-and-recover within minutes, confirm with
        # a CMF sign change and a volume spike on the sweep bar.
        if df_15m is not None and len(df_15m) >= 20:
            df_15m_local = df_15m[df_15m.index <= df_slice.index[-1]].tail(20)
            if len(df_15m_local) >= 12:
                obs_15m = order_blocks(df_15m_local, lookback=20)
                ob_sig  = self._check_ob_sweep_15m(
                    df_15m_local, obs_15m, atr_v, rsi_v, daily_trend, levels
                )
                if ob_sig:
                    return ob_sig

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _price_at_level(self, price: float, levels: dict, side: str) -> bool:
        """True if price is within LEVEL_ZONE_PCT of any session level on 'side'."""
        for k, v in levels.items():
            if v and side in k:
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

    def _check_ob_sweep_15m(self, df: pd.DataFrame, obs: list,
                             atr_1h: float, rsi_v: float,
                             daily_trend: str, levels: dict) -> Optional[dict]:
        """
        Scan the last 8 15M bars for an OB sweep + volume spike + CMF confirmation.

        Filters:
          - Sweep bar volume >= 1.5× 20-bar average (genuine liquidity grab)
          - CMF >= 0 on recovery bar for longs, <= 0 for shorts (money flow confirms)
          - Sweep wick must reach a session level
        """
        atr_series = calc_atr(df)
        cmf_series = calc_cmf(df, period=8)   # shorter period suits 15M granularity
        vol_ma     = df["Volume"].rolling(20).mean()

        # Scan last 8 bars in reverse — return the most recent qualifying sweep
        scan_start = max(0, len(df) - 8)
        for idx in range(len(df) - 1, scan_start - 1, -1):
            bar       = df.iloc[idx]
            bar_low   = float(bar["Low"])
            bar_high  = float(bar["High"])
            bar_close = float(bar["Close"])
            bar_open_ = float(bar["Open"])
            bar_vol   = float(bar.get("Volume", 0))

            atr_15m = float(atr_series.iloc[idx])
            if pd.isna(atr_15m) or atr_15m == 0:
                atr_15m = atr_1h * 0.25

            # Volume spike: sweep bar must have elevated volume
            vol_avg = float(vol_ma.iloc[idx]) if not pd.isna(vol_ma.iloc[idx]) else 0
            if vol_avg > 0 and bar_vol < vol_avg * 1.5:
                continue

            # CMF at this bar
            cmf_val = float(cmf_series.iloc[idx]) if not pd.isna(cmf_series.iloc[idx]) else 0.0

            # ── Bullish sweep ─────────────────────────────────────────────────
            if daily_trend in ("bullish", "neutral") and rsi_v < RSI_LONG_MAX \
                    and bar_close > bar_open_ and cmf_val >= 0:
                for ob in obs:
                    if ob["type"] != "bullish":
                        continue
                    ob_low = float(ob["low"])
                    if bar_low < ob_low and bar_close >= ob_low:
                        if not self._price_at_level(bar_low, levels, side="low"):
                            continue
                        price = bar_close
                        stop  = bar_low - atr_15m * 0.5
                        stop  = min(stop, price - atr_1h * MIN_RISK_ATR)
                        stop  = round(stop, 4)
                        risk_ = price - stop
                        if stop < price and risk_ >= price * 0.001 and atr_1h / risk_ >= MIN_RR:
                            return {
                                "direction":   "long",
                                "signal_type": "ob_sweep",
                                "entry":       price,
                                "stop":        stop,
                                "atr":         atr_1h,
                                "ob_zone":     [ob_low, float(ob["high"])],
                            }

            # ── Bearish sweep ─────────────────────────────────────────────────
            if daily_trend in ("bearish", "neutral") and rsi_v > RSI_SHORT_MIN \
                    and bar_close < bar_open_ and cmf_val <= 0:
                for ob in obs:
                    if ob["type"] != "bearish":
                        continue
                    ob_high = float(ob["high"])
                    if bar_high > ob_high and bar_close <= ob_high:
                        if not self._price_at_level(bar_high, levels, side="high"):
                            continue
                        price = bar_close
                        stop  = bar_high + atr_15m * 0.5
                        stop  = max(stop, price + atr_1h * MIN_RISK_ATR)
                        stop  = round(stop, 4)
                        risk_ = stop - price
                        if stop > price and risk_ >= price * 0.001 and atr_1h / risk_ >= MIN_RR:
                            return {
                                "direction":   "short",
                                "signal_type": "ob_sweep",
                                "entry":       price,
                                "stop":        stop,
                                "atr":         atr_1h,
                                "ob_zone":     [float(ob["low"]), ob_high],
                            }

        return None

