"""
HMM-gated SMC strategy.

Uses the existing SMC signal generator but gates each signal through the
HMM regime filter:

  ob_retest      → only in "trending" regime
  fvg_fill       → only in "ranging" regime
  vwap_reversion → only in "ranging" regime
  any signal     → blocked in "volatile" regime

Adds `regime` key to every signal dict so it appears in the trade log.
"""

import pandas as pd
from typing import Optional

from .base import BaseStrategy
from .smc import SMCStrategy
from .hmm_filter import HMMRegimeFilter


# Which signal types are allowed in each regime
_REGIME_ALLOW: dict[str, set[str]] = {
    "trending": {"ob_retest"},
    "ranging":  {"fvg_fill", "vwap_reversion", "ob_retest"},
    "volatile": set(),   # no new entries
}


class HMMSMCStrategy(BaseStrategy):
    name = "hmm_smc"

    def __init__(self) -> None:
        self._smc    = SMCStrategy()
        self._filter = HMMRegimeFilter(n_states=3, lookback=60)

    def fit(self, df: pd.DataFrame) -> None:
        self._filter.fit(df)

    def generate_signal(self, df_slice: pd.DataFrame, daily_trend: str) -> Optional[dict]:
        sig = self._smc.generate_signal(df_slice, daily_trend)
        if sig is None:
            return None

        regime  = self._filter.get_regime(df_slice)
        allowed = _REGIME_ALLOW.get(regime, set())
        if sig["signal_type"] not in allowed:
            return None

        sig["regime"] = regime
        return sig
