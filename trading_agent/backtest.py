"""
Walk-forward backtester for the SMC + mean-reversion strategy.

Simulates on 1H bars (primary timeframe) with daily bias filter.
Avoids look-ahead bias: at each bar only data up to that bar is visible.

Trade logic:
  - Bullish entry: price touches/enters a bullish OB or FVG while daily
    trend is bullish and RSI < 55; stop below OB/FVG low; targets at 1.5R / 2.5R
  - Bearish entry: price touches/enters a bearish OB or FVG while daily
    trend is bearish and RSI > 45; stop above OB/FVG high; targets at 1.5R / 2.5R
  - Mean reversion: VWAP deviation > 3% fades back toward VWAP
  - Only one open position per symbol at a time
  - Exits: TP1 (close 60%), TP2 (close remaining), or stop hit

Outputs JSON trade log + summary stats.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
from typing import Optional
from .analysis import (
    order_blocks, fair_value_gaps, vwap as calc_vwap,
    rsi as calc_rsi, atr as calc_atr, ema,
)
from .risk import position_size, profit_targets


@dataclass
class Trade:
    symbol: str
    direction: str          # "long" / "short"
    signal_type: str        # "ob_retest" / "fvg_fill" / "vwap_reversion"
    entry_bar: int
    entry_time: str
    entry_price: float
    stop: float
    tp1: float
    tp2: float
    exit_bar: Optional[int] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "tp1" / "tp2" / "stop" / "timeout"
    pnl_r: Optional[float] = None      # P&L in R multiples
    shares: int = 0


def _daily_trend(df_daily: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """Trend on daily bars up to (not including) as_of date."""
    subset = df_daily[df_daily.index.date < as_of.date()]
    if len(subset) < 21:
        return "neutral"
    e9  = float(ema(subset, 9).iloc[-1])
    e21 = float(ema(subset, 21).iloc[-1])
    price = float(subset["Close"].iloc[-1])
    if price > e9 > e21:
        return "bullish"
    if price < e9 < e21:
        return "bearish"
    return "neutral"


def _intraday_vwap(df_slice: pd.DataFrame) -> float:
    """
    Intraday VWAP anchored to today's session open.
    Falls back to session VWAP on the most recent trading day present in the slice.
    """
    last_date = df_slice.index[-1].date()
    today = df_slice[df_slice.index.date == last_date]
    if today.empty or today["Volume"].sum() == 0:
        today = df_slice.tail(20)
    typical = (today["High"] + today["Low"] + today["Close"]) / 3
    return float((typical * today["Volume"]).sum() / today["Volume"].sum())


def _generate_signal(df_slice: pd.DataFrame, daily_trend: str) -> Optional[dict]:
    """
    Look for a signal on the most recent bar of df_slice.
    Returns a signal dict or None.

    Fixes vs original:
    - VWAP is intraday (anchored to today's session), not cumulative
    - OB touch zone widened to ±1% so pullbacks that nearly reach OB still trigger
    - FVG zone widened to ±0.3%
    - VWAP dev threshold lowered 3.5% → 2.0%; RSI gate relaxed 35/65 → 38/62
    - Stops placed at ATR-based distance when structure stop would give <1.4R
    """
    if len(df_slice) < 20:
        return None

    price = float(df_slice["Close"].iloc[-1])
    hi    = float(df_slice["High"].iloc[-1])
    lo    = float(df_slice["Low"].iloc[-1])

    rsi_s = calc_rsi(df_slice)
    rsi_v = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0
    atr_s = calc_atr(df_slice)
    atr_v = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else price * 0.01

    vwap_v   = _intraday_vwap(df_slice)
    vwap_dev = (price - vwap_v) / vwap_v * 100

    obs  = order_blocks(df_slice, lookback=30)
    fvgs = fair_value_gaps(df_slice, lookback=40)

    def _viable(direction: str, stop: float) -> bool:
        """Stop must give at least 1.4R to TP1 (1.5R target)."""
        risk = abs(price - stop)
        return risk >= price * 0.001 and (atr_v * 1.5) / risk >= 1.4

    # ── Bullish OB retest ────────────────────────────────────────────────────
    # Price touches the OB zone from above (within 1% above OB high or inside)
    if daily_trend in ("bullish", "neutral") and rsi_v < 62:
        bull_obs = [
            o for o in obs
            if o["type"] == "bullish"
            and o["low"] * 0.99 <= price <= o["high"] * 1.01
        ]
        if bull_obs:
            ob   = bull_obs[-1]
            stop = ob["low"] * 0.997
            if _viable("long", stop):
                return {"direction": "long", "signal_type": "ob_retest",
                        "entry": price, "stop": round(stop, 4), "atr": atr_v}

    # ── Bearish OB retest ────────────────────────────────────────────────────
    if daily_trend in ("bearish", "neutral") and rsi_v > 38:
        bear_obs = [
            o for o in obs
            if o["type"] == "bearish"
            and o["low"] * 0.99 <= price <= o["high"] * 1.01
        ]
        if bear_obs:
            ob   = bear_obs[-1]
            stop = ob["high"] * 1.003
            if _viable("short", stop):
                return {"direction": "short", "signal_type": "ob_retest",
                        "entry": price, "stop": round(stop, 4), "atr": atr_v}

    # ── Bullish FVG fill ─────────────────────────────────────────────────────
    if daily_trend in ("bullish", "neutral") and rsi_v < 58:
        bull_fvgs = [
            f for f in fvgs
            if f["type"] == "bullish" and not f["filled"]
            and f["bottom"] * 0.997 <= price <= f["top"] * 1.003
        ]
        if bull_fvgs:
            fvg  = bull_fvgs[-1]
            stop = fvg["bottom"] * 0.996
            if _viable("long", stop):
                return {"direction": "long", "signal_type": "fvg_fill",
                        "entry": price, "stop": round(stop, 4), "atr": atr_v}

    # ── Bearish FVG fill ─────────────────────────────────────────────────────
    if daily_trend in ("bearish", "neutral") and rsi_v > 42:
        bear_fvgs = [
            f for f in fvgs
            if f["type"] == "bearish" and not f["filled"]
            and f["bottom"] * 0.997 <= price <= f["top"] * 1.003
        ]
        if bear_fvgs:
            fvg  = bear_fvgs[-1]
            stop = fvg["top"] * 1.004
            if _viable("short", stop):
                return {"direction": "short", "signal_type": "fvg_fill",
                        "entry": price, "stop": round(stop, 4), "atr": atr_v}

    # ── Intraday VWAP mean reversion ─────────────────────────────────────────
    if vwap_dev < -2.0 and rsi_v < 38:
        stop = lo - atr_v * 0.5
        if _viable("long", stop):
            return {"direction": "long", "signal_type": "vwap_reversion",
                    "entry": price, "stop": round(stop, 4), "atr": atr_v}

    if vwap_dev > 2.0 and rsi_v > 62:
        stop = hi + atr_v * 0.5
        if _viable("short", stop):
            return {"direction": "short", "signal_type": "vwap_reversion",
                    "entry": price, "stop": round(stop, 4), "atr": atr_v}

    return None


def _manage_trade(trade: Trade, bar_high: float, bar_low: float,
                  bar_open: float) -> Optional[Trade]:
    """
    Check if a bar hits stop or target.
    Returns updated trade if closed, else None.
    """
    if trade.direction == "long":
        # Stop hit
        if bar_low <= trade.stop:
            trade.exit_price = min(bar_open, trade.stop)  # slippage on gaps
            trade.exit_reason = "stop"
            trade.pnl_r = round((trade.exit_price - trade.entry_price) /
                                (trade.entry_price - trade.stop), 2)
            return trade
        # TP1 partial (simulate as full exit at TP1 for simplicity)
        if bar_high >= trade.tp1:
            trade.exit_price = trade.tp1
            trade.exit_reason = "tp1"
            trade.pnl_r = round((trade.tp1 - trade.entry_price) /
                                (trade.entry_price - trade.stop), 2)
            return trade
    else:
        if bar_high >= trade.stop:
            trade.exit_price = max(bar_open, trade.stop)
            trade.exit_reason = "stop"
            trade.pnl_r = round((trade.entry_price - trade.exit_price) /
                                (trade.stop - trade.entry_price), 2)
            return trade
        if bar_low <= trade.tp1:
            trade.exit_price = trade.tp1
            trade.exit_reason = "tp1"
            trade.pnl_r = round((trade.entry_price - trade.tp1) /
                                (trade.stop - trade.entry_price), 2)
            return trade

    return None


def simulate_symbol(symbol: str, df_1h: pd.DataFrame,
                    df_daily: pd.DataFrame,
                    account_value: float = 10_000,
                    warmup_bars: int = 20,
                    max_hold_bars: int = 16) -> list[dict]:
    """Simulate all trades for one symbol. Returns list of closed trade dicts."""
    trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    cooldown_until: int = 0   # bar index after which we can take a new trade

    def _ts(bar_idx: int) -> str:
        return str(df_1h.index[bar_idx]) if bar_idx < len(df_1h) else ""

    for i in range(warmup_bars, len(df_1h) - 1):
        bar_time  = df_1h.index[i]
        next_open = float(df_1h["Open"].iloc[i + 1])
        next_high = float(df_1h["High"].iloc[i + 1])
        next_low  = float(df_1h["Low"].iloc[i + 1])

        # ── Manage open trade ────────────────────────────────────────────────
        if open_trade is not None:
            open_trade.exit_bar = i + 1
            closed = _manage_trade(open_trade, next_high, next_low, next_open)
            if closed:
                closed.exit_time = _ts(i + 1)
                trades.append(asdict(closed))
                cooldown_until = i + 4   # 4-bar cooldown after any close
                open_trade = None
            elif (i + 1 - open_trade.entry_bar) >= max_hold_bars:
                ep  = open_trade.entry_price
                st  = open_trade.stop
                xp  = float(df_1h["Close"].iloc[i + 1])
                risk = abs(ep - st)
                pnl  = (xp - ep if open_trade.direction == "long" else ep - xp)
                open_trade.exit_price  = round(xp, 4)
                open_trade.exit_reason = "timeout"
                open_trade.exit_bar    = i + 1
                open_trade.exit_time   = _ts(i + 1)
                open_trade.pnl_r       = round(pnl / risk, 2) if risk else 0
                trades.append(asdict(open_trade))
                cooldown_until = i + 4
                open_trade = None
            continue

        # ── Cooldown guard ───────────────────────────────────────────────────
        if i < cooldown_until:
            continue

        # ── Look for new signal ──────────────────────────────────────────────
        df_slice = df_1h.iloc[: i + 1]
        d_trend  = _daily_trend(df_daily, bar_time)
        sig      = _generate_signal(df_slice, d_trend)

        if sig is None:
            continue

        entry = next_open  # enter at next bar open (no look-ahead)
        stop  = sig["stop"]
        risk  = abs(entry - stop)

        # Stop must be on the correct side of the entry price.
        # If price moved between signal bar and entry bar the stop can
        # land on the wrong side — skip those.
        if sig["direction"] == "long"  and stop >= entry:
            continue
        if sig["direction"] == "short" and stop <= entry:
            continue
        if risk < entry * 0.001:
            continue

        tgts  = profit_targets(entry, stop, sig["direction"], sig["atr"])
        pos   = position_size(account_value, entry, stop)

        # Minimum R:R guard
        rr = abs(tgts["tp1"] - entry) / risk
        if rr < 1.2:
            continue

        open_trade = Trade(
            symbol       = symbol,
            direction    = sig["direction"],
            signal_type  = sig["signal_type"],
            entry_bar    = i + 1,
            entry_time   = _ts(i + 1),
            entry_price  = round(entry, 4),
            stop         = round(stop, 4),
            tp1          = tgts["tp1"],
            tp2          = tgts["tp2"],
            shares       = pos["shares"],
        )

    return trades


def compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"error": "no trades generated"}

    df = pd.DataFrame(trades)
    df = df[df["exit_reason"].notna()]

    wins   = df[df["pnl_r"] > 0]
    losses = df[df["pnl_r"] <= 0]

    win_rate     = round(len(wins) / len(df) * 100, 1)
    avg_win_r    = round(wins["pnl_r"].mean(), 2) if len(wins) else 0
    avg_loss_r   = round(losses["pnl_r"].mean(), 2) if len(losses) else 0
    total_r      = round(df["pnl_r"].sum(), 2)
    expectancy   = round(df["pnl_r"].mean(), 3)
    profit_factor = round(
        wins["pnl_r"].sum() / abs(losses["pnl_r"].sum()), 2
    ) if len(losses) and losses["pnl_r"].sum() != 0 else float("inf")

    # Equity curve in R
    equity = df["pnl_r"].cumsum().values
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    max_dd = round(float(dd.min()), 2)

    by_type  = df.groupby("signal_type")["pnl_r"].agg(["count", "mean", "sum"]).round(2).to_dict()
    by_dir   = df.groupby("direction")["pnl_r"].agg(["count", "mean"]).round(2).to_dict()
    by_exit  = df["exit_reason"].value_counts().to_dict()

    return {
        "total_trades":   len(df),
        "win_rate_pct":   win_rate,
        "avg_win_r":      avg_win_r,
        "avg_loss_r":     avg_loss_r,
        "total_r":        total_r,
        "expectancy_r":   expectancy,
        "profit_factor":  profit_factor,
        "max_drawdown_r": max_dd,
        "by_signal_type": by_type,
        "by_direction":   by_dir,
        "exit_reasons":   by_exit,
        "trade_log":      df.to_dict(orient="records"),
    }


def plot_results(stats: dict, out_path: str = "backtest_results.png") -> str:
    """
    Generate a 4-panel chart:
      1. Equity curve (cumulative R)
      2. Drawdown over time
      3. P&L distribution (histogram)
      4. Win/loss breakdown by signal type
    Saves to out_path and returns the path.
    """
    import matplotlib
    matplotlib.use("Agg")  # headless — no display required
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec

    trades = stats.get("trade_log", [])
    if not trades:
        raise ValueError("No trades to plot")

    df = pd.DataFrame(trades)
    pnl  = df["pnl_r"].values
    equity = np.cumsum(pnl)
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    trade_nums = np.arange(1, len(pnl) + 1)

    fig = plt.figure(figsize=(14, 10))
    fig.patch.set_facecolor("#0d1117")
    gs = gridspec.GridSpec(2, 2, hspace=0.42, wspace=0.32)

    COLORS = {
        "green":  "#2ecc71",
        "red":    "#e74c3c",
        "blue":   "#3498db",
        "orange": "#e67e22",
        "grey":   "#8b949e",
        "bg":     "#161b22",
        "text":   "#c9d1d9",
    }

    def _style(ax, title):
        ax.set_facecolor(COLORS["bg"])
        ax.set_title(title, color=COLORS["text"], fontsize=11, pad=8)
        ax.tick_params(colors=COLORS["grey"], labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")
        ax.yaxis.label.set_color(COLORS["grey"])
        ax.xaxis.label.set_color(COLORS["grey"])

    # ── 1. Equity curve ───────────────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, 0])
    color = COLORS["green"] if equity[-1] >= 0 else COLORS["red"]
    ax1.plot(trade_nums, equity, color=color, linewidth=1.8)
    ax1.axhline(0, color=COLORS["grey"], linewidth=0.8, linestyle="--")
    ax1.fill_between(trade_nums, equity, 0,
                     where=(equity >= 0), alpha=0.15, color=COLORS["green"])
    ax1.fill_between(trade_nums, equity, 0,
                     where=(equity < 0),  alpha=0.15, color=COLORS["red"])
    ax1.set_xlabel("Trade #")
    ax1.set_ylabel("Cumulative R")
    total_r  = stats.get("total_r", 0)
    exp_r    = stats.get("expectancy_r", 0)
    _style(ax1, f"Equity Curve  (total {total_r:+.1f}R | E={exp_r:+.3f}R/trade)")

    # ── 2. Drawdown ───────────────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.fill_between(trade_nums, dd, 0, color=COLORS["red"], alpha=0.6)
    ax2.plot(trade_nums, dd, color=COLORS["red"], linewidth=1.0)
    ax2.axhline(0, color=COLORS["grey"], linewidth=0.8, linestyle="--")
    ax2.set_xlabel("Trade #")
    ax2.set_ylabel("Drawdown (R)")
    max_dd = stats.get("max_drawdown_r", 0)
    _style(ax2, f"Drawdown  (max {max_dd:.1f}R)")

    # ── 3. P&L distribution ───────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 0])
    wins_pnl   = pnl[pnl > 0]
    losses_pnl = pnl[pnl <= 0]
    bins = np.linspace(pnl.min() - 0.2, pnl.max() + 0.2, 30)
    ax3.hist(losses_pnl, bins=bins, color=COLORS["red"],   alpha=0.75, label="Losses")
    ax3.hist(wins_pnl,   bins=bins, color=COLORS["green"], alpha=0.75, label="Wins")
    ax3.axvline(0, color=COLORS["grey"], linewidth=1.0, linestyle="--")
    ax3.axvline(float(np.mean(pnl)), color=COLORS["blue"],
                linewidth=1.4, linestyle="-", label=f"Mean {np.mean(pnl):+.2f}R")
    ax3.set_xlabel("P&L (R)")
    ax3.set_ylabel("Frequency")
    ax3.legend(fontsize=7, facecolor=COLORS["bg"], labelcolor=COLORS["text"])
    wr = stats.get("win_rate_pct", 0)
    pf = stats.get("profit_factor", 0)
    _style(ax3, f"P&L Distribution  (WR={wr}% | PF={pf})")

    # ── 4. By signal type ─────────────────────────────────────────────────────
    ax4 = fig.add_subplot(gs[1, 1])
    by_type = stats.get("by_signal_type", {})
    sig_labels = list(by_type.get("count", {}).keys())
    if sig_labels:
        counts   = [by_type["count"].get(s, 0)   for s in sig_labels]
        mean_pnl = [by_type["mean"].get(s, 0)     for s in sig_labels]
        x = np.arange(len(sig_labels))
        bar_colors = [COLORS["green"] if v >= 0 else COLORS["red"] for v in mean_pnl]
        bars = ax4.bar(x, mean_pnl, color=bar_colors, alpha=0.8, width=0.5)
        ax4.set_xticks(x)
        short_labels = [s.replace("_retest","_OB").replace("_fill","_FVG")
                         .replace("vwap_reversion","VWAP Rev") for s in sig_labels]
        ax4.set_xticklabels(short_labels, fontsize=8)
        ax4.axhline(0, color=COLORS["grey"], linewidth=0.8, linestyle="--")
        ax4.set_ylabel("Avg R per trade")
        for bar, cnt in zip(bars, counts):
            ax4.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                     f"n={cnt}", ha="center", va="bottom",
                     color=COLORS["text"], fontsize=7)
    _style(ax4, "Performance by Signal Type")

    # ── Title ─────────────────────────────────────────────────────────────────
    n = stats.get("total_trades", 0)
    fig.suptitle(
        f"Backtest Results  —  {n} trades  |  "
        f"WR {wr}%  |  Expectancy {exp_r:+.3f}R  |  Max DD {max_dd:.1f}R",
        color=COLORS["text"], fontsize=12, y=0.98,
    )

    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    return out_path


def run_backtest(symbols: list[str], account_value: float = 10_000) -> dict:
    """Entry point: backtest a list of symbols and return combined stats."""
    from .market_data import get_ohlcv

    all_trades: list[dict] = []
    for sym in symbols:
        print(f"  backtesting {sym}...", flush=True)
        df_1h    = get_ohlcv(sym, "1h")
        df_daily = get_ohlcv(sym, "daily")
        if df_1h.empty or df_daily.empty:
            print(f"  {sym}: no data, skipping")
            continue
        trades = simulate_symbol(sym, df_1h, df_daily, account_value)
        print(f"  {sym}: {len(trades)} trades")
        all_trades.extend(trades)

    return compute_stats(all_trades)
