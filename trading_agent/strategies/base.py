"""Base class that every pluggable strategy must implement."""

import pandas as pd
from typing import Optional


class BaseStrategy:
    """
    A strategy produces trading signals from a price slice.

    Signal dict format (returned by generate_signal):
        direction:   "long" | "short"
        signal_type: str   — label used in stats/charts
        entry:       float — expected fill price (usually current close)
        stop:        float — stop-loss price
        atr:         float — ATR at signal bar (used for target calculation)
        regime:      str   — optional, populated by strategies that track regime

    Strategies that need historical fitting (HMM, logistic) implement fit().
    The backtester calls fit(df[:i]) every refit_every bars so the model
    only ever sees data up to the current bar — no look-ahead in parameters.

    Optional overrides:
        trail_to_tp2    — if True: trail stop to BE after TP1, exit at TP2
                          if False (default): exit the full position at TP1
        max_hold_bars   — override the simulator's default max hold duration
        should_force_close — return True to close the trade at a specific bar
    """

    name: str = "base"

    # When True: at TP1 move stop to breakeven and aim for TP2.
    # When False (default): exit the full position at TP1.
    trail_to_tp2: bool = False

    # When True: exit 50% at TP1, then trail remainder with ATR stop.
    # Takes priority over trail_to_tp2 when both are set.
    scale_out_trail: bool = False

    # Override the simulator default (16 bars). None = use simulator default.
    max_hold_bars: Optional[int] = None

    def generate_signal(self, df_slice: pd.DataFrame, daily_trend: str,
                        df_15m: "Optional[pd.DataFrame]" = None,
                        df_4h:  "Optional[pd.DataFrame]" = None) -> Optional[dict]:
        raise NotImplementedError

    def fit(self, df: pd.DataFrame) -> None:
        pass

    def on_trade_close(self, trade: dict) -> None:
        pass

    def should_force_close(self, trade, bar_time, price: float) -> bool:
        """Return True to force-close an open trade at bar_time's close price."""
        return False
