"""
Walk-forward logistic regression strategy.

Features computed at each bar (no look-ahead):
  RSI (normalised), intraday VWAP deviation, ATR/price, EMA9 slope,
  EMA21 slope, Bollinger %B, price vs EMA9, EMA9 vs EMA21.

Labels (computed on training data using future price):
  +1 (long)  if close[i + HORIZON] > close[i] + 1 × ATR[i]
  +2 (short) if close[i + HORIZON] < close[i] - 1 × ATR[i]
   0 (flat)  otherwise

Walk-forward discipline:
  - The backtester calls fit(df[:i]) every REFIT_EVERY bars.
  - fit() trains only on bars where the HORIZON-bar label is already known,
    i.e., bars 0 .. len-HORIZON-1.  No look-ahead in parameters.
  - A signal is emitted only when P(long | features) > THRESHOLD or
    P(short | features) > THRESHOLD.
"""

import numpy as np
import pandas as pd
from typing import Optional

from .base import BaseStrategy
from ..analysis import (
    rsi as calc_rsi, atr as calc_atr, ema, bollinger,
    intraday_vwap,
)

HORIZON   = 8     # bars to look forward for labelling
THRESHOLD = 0.42  # minimum probability to emit a signal
MIN_TRAIN = 60    # minimum labelled examples before the model is used


class LogisticStrategy(BaseStrategy):
    name = "logistic"

    def __init__(self) -> None:
        self._model   = None
        self._classes = None

    # ── Feature engineering ──────────────────────────────────────────────────

    def _bar_features(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        """Return feature vector for the last bar of df, or None if not ready."""
        if len(df) < 22:
            return None

        price = float(df["Close"].iloc[-1])

        rsi_s = calc_rsi(df)
        atr_s = calc_atr(df)
        e9_s  = ema(df, 9)
        e21_s = ema(df, 21)
        bb    = bollinger(df, period=20)

        rsi_v = float(rsi_s.iloc[-1])
        atr_v = float(atr_s.iloc[-1])
        e9    = float(e9_s.iloc[-1])
        e21   = float(e21_s.iloc[-1])
        e9_2  = float(e9_s.iloc[-3]) if len(df) >= 3 else e9
        e21_4 = float(e21_s.iloc[-5]) if len(df) >= 5 else e21

        bb_u = float(bb["upper"].iloc[-1])
        bb_l = float(bb["lower"].iloc[-1])
        bb_r = bb_u - bb_l
        bb_pct = (price - bb_l) / bb_r if bb_r > 0 else 0.5

        vwap_v   = intraday_vwap(df)
        vwap_dev = (price - vwap_v) / vwap_v

        if any(np.isnan(v) for v in [rsi_v, atr_v, e9, e21]):
            return None

        return np.array([
            rsi_v / 100,
            float(np.clip(vwap_dev, -0.1, 0.1)),
            atr_v / price if price > 0 else 0,
            (e9 - e9_2)   / price if price > 0 else 0,   # EMA9 slope
            (e21 - e21_4) / price if price > 0 else 0,   # EMA21 slope
            float(np.clip(bb_pct, 0.0, 1.0)),
            (price - e9)  / price if price > 0 else 0,
            (e9 - e21)    / price if price > 0 else 0,
        ], dtype=float)

    def _build_dataset(self, df: pd.DataFrame):
        """Build (X, y) from df using HORIZON-bar labels.  No look-ahead."""
        n_label = len(df) - HORIZON   # last HORIZON bars have no label yet
        if n_label < MIN_TRAIN:
            return None, None

        atr_s = calc_atr(df)
        X_rows, y_rows = [], []

        for i in range(21, n_label):
            feat = self._bar_features(df.iloc[:i + 1])
            if feat is None:
                continue
            atr_v = float(atr_s.iloc[i])
            if np.isnan(atr_v) or atr_v <= 0:
                continue

            price_now    = float(df["Close"].iloc[i])
            price_future = float(df["Close"].iloc[i + HORIZON])
            move         = price_future - price_now

            if move > atr_v:
                label = 1   # long
            elif move < -atr_v:
                label = 2   # short
            else:
                label = 0   # flat

            X_rows.append(feat)
            y_rows.append(label)

        if len(X_rows) < MIN_TRAIN:
            return None, None
        return np.array(X_rows), np.array(y_rows)

    # ── Fit ──────────────────────────────────────────────────────────────────

    def fit(self, df: pd.DataFrame) -> None:
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
        except ImportError:
            return

        X, y = self._build_dataset(df)
        if X is None or len(np.unique(y)) < 2:
            return

        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf",    LogisticRegression(
                max_iter=500,
                class_weight="balanced",   # compensates for rare long/short signals
                random_state=42,
                C=0.5,                     # light regularisation
            )),
        ])
        try:
            pipe.fit(X, y)
            self._model   = pipe
            self._classes = pipe.named_steps["clf"].classes_.tolist()
        except Exception:
            pass

    # ── Signal generation ────────────────────────────────────────────────────

    def generate_signal(self, df_slice: pd.DataFrame, daily_trend: str) -> Optional[dict]:
        if self._model is None:
            return None

        feat = self._bar_features(df_slice)
        if feat is None:
            return None

        probs = self._model.predict_proba(feat.reshape(1, -1))[0]

        long_p  = probs[self._classes.index(1)] if 1 in self._classes else 0.0
        short_p = probs[self._classes.index(2)] if 2 in self._classes else 0.0

        price = float(df_slice["Close"].iloc[-1])
        atr_s = calc_atr(df_slice)
        atr_v = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else price * 0.01

        if long_p > THRESHOLD and long_p > short_p:
            # Only trade with daily trend or neutral
            if daily_trend == "bearish":
                return None
            stop = price - atr_v * 1.5
            return {"direction": "long",  "signal_type": "logistic",
                    "entry": price, "stop": round(stop, 4), "atr": atr_v,
                    "prob": round(float(long_p), 3)}

        if short_p > THRESHOLD and short_p > long_p:
            if daily_trend == "bullish":
                return None
            stop = price + atr_v * 1.5
            return {"direction": "short", "signal_type": "logistic",
                    "entry": price, "stop": round(stop, 4), "atr": atr_v,
                    "prob": round(float(short_p), 3)}

        return None
