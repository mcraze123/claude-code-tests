"""
HMM-based market regime detector.

Trains a Gaussian HMM on volatility/return features and classifies each bar
into one of three regimes:

  "trending"  — directional, moderate volatility.  Good for OB retests.
  "ranging"   — low vol, mean-reverting.  Good for FVG fills / VWAP reversion.
  "volatile"  — high vol / erratic.  Skip new entries.

The model is fitted once per symbol (or on an expanding window) so parameters
never see future data relative to the bar being evaluated.
"""

import numpy as np
import pandas as pd


class HMMRegimeFilter:
    def __init__(self, n_states: int = 3, lookback: int = 60):
        self.n_states  = n_states
        self.lookback  = lookback
        self._model    = None
        self._state_map: dict[int, str] = {}

    # ── Feature engineering ──────────────────────────────────────────────────

    def _features(self, df: pd.DataFrame) -> np.ndarray:
        rets     = df["Close"].pct_change().fillna(0).values
        roll_vol = pd.Series(rets).rolling(5, min_periods=1).std().fillna(0).values
        atr_pct  = ((df["High"] - df["Low"]) / df["Close"].clip(lower=1e-9)).fillna(0).values
        return np.column_stack([rets, roll_vol, atr_pct])

    # ── Fit ──────────────────────────────────────────────────────────────────

    def fit(self, df: pd.DataFrame) -> None:
        try:
            from hmmlearn import hmm
        except ImportError:
            return  # graceful degradation — filter becomes a no-op

        if len(df) < 30:
            return

        X = self._features(df)
        model = hmm.GaussianHMM(
            n_components=self.n_states,
            covariance_type="diag",
            n_iter=100,
            random_state=42,
        )
        import warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model.fit(X)
        except Exception:
            return

        self._model = model

        # Label states by mean rolling-volatility (column 1).
        # Lowest vol → "ranging", highest → "volatile", middle → "trending".
        vol_means = model.means_[:, 1]
        order = np.argsort(vol_means)          # low → high
        labels = ["ranging", "trending", "volatile"]
        self._state_map = {int(s): labels[i] for i, s in enumerate(order)}

    # ── Inference ────────────────────────────────────────────────────────────

    def get_regime(self, df_slice: pd.DataFrame) -> str:
        if self._model is None:
            return "trending"           # no model yet — allow all signals

        tail = df_slice.tail(self.lookback)
        if len(tail) < 10:
            return "trending"

        X = self._features(tail)
        try:
            states  = self._model.predict(X)
            return self._state_map.get(int(states[-1]), "ranging")
        except Exception:
            return "ranging"
