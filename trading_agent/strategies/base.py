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
    """

    name: str = "base"

    def generate_signal(self, df_slice: pd.DataFrame, daily_trend: str) -> Optional[dict]:
        raise NotImplementedError

    def fit(self, df: pd.DataFrame) -> None:
        """Train / update the model on data seen so far.  Default: no-op."""
        pass

    def on_trade_close(self, trade: dict) -> None:
        """Called after each closed trade.  Default: no-op."""
        pass
