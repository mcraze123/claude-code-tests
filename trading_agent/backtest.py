"""
Walk-forward backtester for pluggable trading strategies.

Usage:
    from trading_agent.backtest import run_backtest
    results = run_backtest(["NVDA", "TSLA"], strategy_name="hmm_smc")

Strategy plugins live in trading_agent/strategies/.
At each bar only data up to that bar is visible — no look-ahead bias.
ML strategies are refitted every REFIT_EVERY bars on the expanding window.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict, field
from typing import Optional

from .analysis import ema
from .risk import position_size, profit_targets
from .strategies.base import BaseStrategy


# ── Trend helpers ─────────────────────────────────────────────────────────────

MAX_CONSEC_LOSSES = 3   # circuit breaker: pause after this many losses in a row


def _daily_trend(df_daily: pd.DataFrame, as_of: pd.Timestamp) -> str:
    subset = df_daily[df_daily.index.date < as_of.date()]
    if len(subset) < 21:
        return "neutral"
    e9    = float(ema(subset, 9).iloc[-1])
    e21   = float(ema(subset, 21).iloc[-1])
    price = float(subset["Close"].iloc[-1])
    if price > e9 > e21:
        return "bullish"
    if price < e9 < e21:
        return "bearish"
    return "neutral"


def _trend_bias(df: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """EMA9/21 trend on any timeframe — bars strictly before as_of."""
    subset = df[df.index < as_of]
    if len(subset) < 21:
        return "neutral"
    e9    = float(ema(subset, 9).iloc[-1])
    e21   = float(ema(subset, 21).iloc[-1])
    price = float(subset["Close"].iloc[-1])
    if price > e9 > e21:
        return "bullish"
    if price < e9 < e21:
        return "bearish"
    return "neutral"


# ── Trade dataclass ───────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol:       str
    direction:    str            # "long" / "short"
    signal_type:  str
    entry_bar:    int
    entry_time:   str
    entry_price:  float
    stop:         float
    tp1:          float
    tp2:          float
    exit_bar:     Optional[int]   = None
    exit_time:    Optional[str]   = None
    exit_price:   Optional[float] = None
    exit_reason:  Optional[str]   = None
    pnl_r:        Optional[float] = None
    shares:       int             = 0
    regime:       Optional[str]   = None
    atr:          float           = 0.0    # ATR at entry, used for hard 2×ATR cap
    initial_risk: float           = 0.0    # |entry - original_stop|, constant
    trail_to_tp2: bool            = False  # if True: BE trail at TP1 → aim for TP2
    be_trail:     bool            = False  # True once TP1 hit and stop moved to entry
    scale_out_trail:   bool  = False  # 50% at TP1 then ATR trailing stop on remainder
    partial_exit_done: bool  = False  # True after 50% scaled out at TP1
    trail_stop:        float = 0.0    # live trailing stop level for the remaining half
    partial_pnl_r:     float = 0.0    # R captured on the 50% exited at TP1


# ── Trade management ──────────────────────────────────────────────────────────

_TRAIL_ATR_MULT = 1.0   # trailing stop sits this many ATRs behind the extreme reached


def _blend(r1: float, r2: float) -> float:
    """Blend two R values as a 50/50 split (scale-out trade)."""
    return round((r1 + r2) / 2, 2)


def _manage_trade(trade: Trade, bar_high: float, bar_low: float,
                  bar_open: float) -> Optional[Trade]:
    """
    Three exit modes (checked in order):

    scale_out_trail=True  — exit 50% at TP1, then trail remaining with ATR stop.
    trail_to_tp2=True     — at TP1 move stop to BE, exit remaining at TP2.
    (default)             — exit full position at TP1.

    Hard 2×ATR cap applies to all modes.
    initial_risk is fixed at entry so pnl_r is always in original R units.
    """
    entry = trade.entry_price
    risk  = trade.initial_risk or abs(entry - trade.stop)
    if not risk:
        return None

    if trade.direction == "long":
        # Cap loss at 2R (not 2×ATR — that creates -4R losses when stops are tight)
        hard_cap = entry - 2.0 * risk

        # ── Hard cap (always checked first) ─────────────────────────────────
        if bar_low <= hard_cap:
            exit_p = min(bar_open, hard_cap)
            trade.exit_price  = round(exit_p, 4)
            trade.exit_reason = "max_loss"
            r = round((exit_p - entry) / risk, 2)
            trade.pnl_r = _blend(trade.partial_pnl_r, r) if trade.partial_exit_done else r
            return trade

        # ── Scale-out + ATR trail ────────────────────────────────────────────
        if trade.scale_out_trail:
            if not trade.partial_exit_done:
                if bar_low <= trade.stop:
                    exit_p = min(bar_open, trade.stop)
                    trade.exit_price  = round(exit_p, 4)
                    trade.exit_reason = "stop"
                    trade.pnl_r = round((exit_p - entry) / risk, 2)
                    return trade
                if bar_high >= trade.tp1:
                    trade.partial_exit_done = True
                    trade.partial_pnl_r = round((trade.tp1 - entry) / risk, 2)
                    trade.stop        = entry   # move stop to BE
                    trade.trail_stop  = entry   # initialise trail at BE

            if trade.partial_exit_done:
                # Ratchet trail up as price makes new highs
                new_trail = bar_high - trade.atr * _TRAIL_ATR_MULT
                trade.trail_stop = max(trade.trail_stop, new_trail, entry)
                if bar_low <= trade.trail_stop:
                    exit_p = bar_open if bar_open < trade.trail_stop else trade.trail_stop
                    r = round((exit_p - entry) / risk, 2)
                    trade.pnl_r = _blend(trade.partial_pnl_r, r)
                    trade.exit_price  = round(exit_p, 4)
                    trade.exit_reason = "trail_stop"
                    return trade

        # ── Legacy TP1 / TP2 modes ───────────────────────────────────────────
        else:
            if bar_low <= trade.stop:
                trade.exit_price  = min(bar_open, trade.stop)
                trade.exit_reason = "be_stop" if trade.be_trail else "stop"
                trade.pnl_r = round((trade.exit_price - entry) / risk, 2)
                return trade
            if trade.trail_to_tp2:
                if bar_high >= trade.tp2:
                    trade.exit_price  = trade.tp2
                    trade.exit_reason = "tp2"
                    trade.pnl_r = round((trade.tp2 - entry) / risk, 2)
                    return trade
                if not trade.be_trail and bar_high >= trade.tp1:
                    trade.be_trail = True
                    trade.stop     = entry
            else:
                if bar_high >= trade.tp1:
                    trade.exit_price  = trade.tp1
                    trade.exit_reason = "tp1"
                    trade.pnl_r = round((trade.tp1 - entry) / risk, 2)
                    return trade

    else:  # short
        hard_cap = entry + 2.0 * risk

        if bar_high >= hard_cap:
            exit_p = max(bar_open, hard_cap)
            trade.exit_price  = round(exit_p, 4)
            trade.exit_reason = "max_loss"
            r = round((entry - exit_p) / risk, 2)
            trade.pnl_r = _blend(trade.partial_pnl_r, r) if trade.partial_exit_done else r
            return trade

        if trade.scale_out_trail:
            if not trade.partial_exit_done:
                if bar_high >= trade.stop:
                    exit_p = max(bar_open, trade.stop)
                    trade.exit_price  = round(exit_p, 4)
                    trade.exit_reason = "stop"
                    trade.pnl_r = round((entry - exit_p) / risk, 2)
                    return trade
                if bar_low <= trade.tp1:
                    trade.partial_exit_done = True
                    trade.partial_pnl_r = round((entry - trade.tp1) / risk, 2)
                    trade.stop        = entry
                    trade.trail_stop  = entry

            if trade.partial_exit_done:
                # Ratchet trail down as price makes new lows
                new_trail = bar_low + trade.atr * _TRAIL_ATR_MULT
                trade.trail_stop = min(trade.trail_stop if trade.trail_stop > 0 else entry,
                                       new_trail, entry)
                if bar_high >= trade.trail_stop:
                    exit_p = bar_open if bar_open > trade.trail_stop else trade.trail_stop
                    r = round((entry - exit_p) / risk, 2)
                    trade.pnl_r = _blend(trade.partial_pnl_r, r)
                    trade.exit_price  = round(exit_p, 4)
                    trade.exit_reason = "trail_stop"
                    return trade

        else:
            if bar_high >= trade.stop:
                trade.exit_price  = max(bar_open, trade.stop)
                trade.exit_reason = "be_stop" if trade.be_trail else "stop"
                trade.pnl_r = round((entry - trade.exit_price) / risk, 2)
                return trade
            if trade.trail_to_tp2:
                if bar_low <= trade.tp2:
                    trade.exit_price  = trade.tp2
                    trade.exit_reason = "tp2"
                    trade.pnl_r = round((entry - trade.tp2) / risk, 2)
                    return trade
                if not trade.be_trail and bar_low <= trade.tp1:
                    trade.be_trail = True
                    trade.stop     = entry
            else:
                if bar_low <= trade.tp1:
                    trade.exit_price  = trade.tp1
                    trade.exit_reason = "tp1"
                    trade.pnl_r = round((entry - trade.tp1) / risk, 2)
                    return trade

    return None


# ── Symbol simulation ─────────────────────────────────────────────────────────

def simulate_symbol(symbol: str,
                    df_1h: pd.DataFrame,
                    df_daily: pd.DataFrame,
                    strategy: BaseStrategy,
                    df_15m: Optional[pd.DataFrame] = None,
                    df_4h:  Optional[pd.DataFrame] = None,
                    account_value: float = 10_000,
                    warmup_bars: int = 20,
                    max_hold_bars: int = 16,
                    refit_every: int = 50) -> list[dict]:
    # Strategy can override max_hold_bars (e.g. NY Open caps at 4 bars)
    max_hold_bars = getattr(strategy, "max_hold_bars", None) or max_hold_bars
    """
    Walk-forward simulation for one symbol.
    strategy.fit(df[:i]) is called every refit_every bars so ML strategies
    never see future data when learning their parameters.
    """
    trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    cooldown_until: int = 0

    # ── Circuit breaker state ────────────────────────────────────────────────
    consecutive_losses: int = 0
    circuit_breaker_until: Optional[object] = None   # date: resume on this day

    def _ts(idx: int) -> str:
        return str(df_1h.index[idx]) if idx < len(df_1h) else ""

    for i in range(warmup_bars, len(df_1h) - 1):

        # Walk-forward refit (no-op for SMC, meaningful for HMM / logistic)
        if i % refit_every == 0:
            strategy.fit(df_1h.iloc[:i])

        bar_time  = df_1h.index[i]
        next_open = float(df_1h["Open"].iloc[i + 1])
        next_high = float(df_1h["High"].iloc[i + 1])
        next_low  = float(df_1h["Low"].iloc[i + 1])

        # ── Manage open trade ────────────────────────────────────────────────
        if open_trade is not None:
            open_trade.exit_bar = i + 1
            closed = _manage_trade(open_trade, next_high, next_low, next_open)
            if closed:
                closed.exit_time   = _ts(i + 1)
                trades.append(asdict(closed))
                cooldown_until = i + 4
                open_trade = None
            elif strategy.should_force_close(open_trade, df_1h.index[i + 1],
                                             float(df_1h["Close"].iloc[i + 1])):
                xp   = float(df_1h["Close"].iloc[i + 1])
                risk = open_trade.initial_risk or abs(open_trade.entry_price - open_trade.stop)
                pnl  = (xp - open_trade.entry_price
                        if open_trade.direction == "long"
                        else open_trade.entry_price - xp)
                remaining_r = round(pnl / risk, 2) if risk else 0
                open_trade.exit_price  = round(xp, 4)
                open_trade.exit_reason = "forced_close"
                open_trade.exit_bar    = i + 1
                open_trade.exit_time   = _ts(i + 1)
                open_trade.pnl_r = (
                    _blend(open_trade.partial_pnl_r, remaining_r)
                    if open_trade.partial_exit_done else remaining_r
                )
                trades.append(asdict(open_trade))
                cooldown_until = i + 4
                open_trade = None
            elif (i + 1 - open_trade.entry_bar) >= max_hold_bars:
                xp   = float(df_1h["Close"].iloc[i + 1])
                risk = open_trade.initial_risk or abs(open_trade.entry_price - open_trade.stop)
                pnl  = (xp - open_trade.entry_price
                        if open_trade.direction == "long"
                        else open_trade.entry_price - xp)
                remaining_r = round(pnl / risk, 2) if risk else 0
                open_trade.exit_price  = round(xp, 4)
                open_trade.exit_reason = "timeout"
                open_trade.exit_bar    = i + 1
                open_trade.exit_time   = _ts(i + 1)
                open_trade.pnl_r = (
                    _blend(open_trade.partial_pnl_r, remaining_r)
                    if open_trade.partial_exit_done else remaining_r
                )
                trades.append(asdict(open_trade))
                cooldown_until = i + 4
                open_trade = None

            if open_trade is None and trades:
                last_pnl = trades[-1].get("pnl_r", 0) or 0
                if last_pnl < 0:
                    consecutive_losses += 1
                    if consecutive_losses >= MAX_CONSEC_LOSSES:
                        import datetime as _dt
                        exit_date = df_1h.index[min(i + 1, len(df_1h) - 1)].date()
                        circuit_breaker_until = exit_date + _dt.timedelta(days=1)
                else:
                    consecutive_losses = 0
            continue

        if i < cooldown_until:
            continue

        # ── Circuit breaker ──────────────────────────────────────────────────
        if circuit_breaker_until is not None:
            if bar_time.date() < circuit_breaker_until:
                continue
            else:
                circuit_breaker_until = None
                consecutive_losses    = 0

        # ── Look for new signal ──────────────────────────────────────────────
        df_slice     = df_1h.iloc[: i + 1]
        d_trend      = _daily_trend(df_daily, bar_time)
        df_15m_slice = (df_15m[df_15m.index <= bar_time] if df_15m is not None else None)
        df_4h_slice  = (df_4h[df_4h.index   <= bar_time] if df_4h  is not None else None)
        sig          = strategy.generate_signal(
                           df_slice, d_trend, df_15m=df_15m_slice, df_4h=df_4h_slice)

        if sig is None:
            continue

        entry = next_open
        stop  = sig["stop"]
        risk  = abs(entry - stop)

        if sig["direction"] == "long"  and stop >= entry:
            continue
        if sig["direction"] == "short" and stop <= entry:
            continue
        if risk < entry * 0.001:
            continue
        # Skip if actual fill risk is < 20% of ATR — gap-fill entries that would
        # create extreme R losses from the hard ATR cap
        if sig.get("atr") and risk < sig["atr"] * 0.20:
            continue

        tp_mult = float(sig.get("tp_mult", 2.0))
        tgts = profit_targets(entry, stop, sig["direction"], sig["atr"], tp_mult=tp_mult)
        pos  = position_size(account_value, entry, stop)

        rr = abs(tgts["tp1"] - entry) / risk
        if rr < 1.2:
            continue

        trail            = sig.get("trail", getattr(strategy, "trail_to_tp2", False))
        # Per-signal override takes priority; fall back to strategy-level flag
        scale_out_trail  = sig.get("scale_out_trail",
                                   getattr(strategy, "scale_out_trail", False))

        open_trade = Trade(
            symbol          = symbol,
            direction       = sig["direction"],
            signal_type     = sig["signal_type"],
            entry_bar       = i + 1,
            entry_time      = _ts(i + 1),
            entry_price     = round(entry, 4),
            stop            = round(stop, 4),
            tp1             = tgts["tp1"],
            tp2             = tgts["tp2"],
            shares          = pos["shares"],
            regime          = sig.get("regime"),
            atr             = sig["atr"],
            initial_risk    = risk,
            trail_to_tp2    = trail,
            scale_out_trail = scale_out_trail,
        )

    return trades


# ── Statistics ────────────────────────────────────────────────────────────────

def compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"error": "no trades generated"}

    df = pd.DataFrame(trades)
    df = df[df["exit_reason"].notna()]

    wins   = df[df["pnl_r"] > 0]
    losses = df[df["pnl_r"] <= 0]

    win_rate      = round(len(wins) / len(df) * 100, 1)
    avg_win_r     = round(wins["pnl_r"].mean(), 2)   if len(wins)   else 0
    avg_loss_r    = round(losses["pnl_r"].mean(), 2)  if len(losses) else 0
    total_r       = round(df["pnl_r"].sum(), 2)
    expectancy    = round(df["pnl_r"].mean(), 3)
    profit_factor = (
        round(wins["pnl_r"].sum() / abs(losses["pnl_r"].sum()), 2)
        if len(losses) and losses["pnl_r"].sum() != 0
        else float("inf")
    )

    equity = df["pnl_r"].cumsum().values
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    max_dd = round(float(dd.min()), 2)

    by_type = df.groupby("signal_type")["pnl_r"].agg(["count", "mean", "sum"]).round(2).to_dict()
    by_dir  = df.groupby("direction")["pnl_r"].agg(["count", "mean"]).round(2).to_dict()
    by_exit = df["exit_reason"].value_counts().to_dict()

    out = {
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

    # Regime breakdown (if any strategy populated it)
    if "regime" in df.columns and df["regime"].notna().any():
        out["by_regime"] = (
            df.dropna(subset=["regime"])
            .groupby("regime")["pnl_r"]
            .agg(["count", "mean", "sum"])
            .round(2)
            .to_dict()
        )

    return out


# ── Plotting ──────────────────────────────────────────────────────────────────

def plot_results(stats: dict, out_path: str = "backtest_results.png") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec

    trades = stats.get("trade_log", [])
    if not trades:
        raise ValueError("No trades to plot")

    df = pd.DataFrame(trades)
    pnl        = df["pnl_r"].values
    equity     = np.cumsum(pnl)
    peak       = np.maximum.accumulate(equity)
    dd         = equity - peak
    trade_nums = np.arange(1, len(pnl) + 1)

    fig = plt.figure(figsize=(14, 10))
    fig.patch.set_facecolor("#0d1117")
    gs = gridspec.GridSpec(2, 2, hspace=0.42, wspace=0.32)

    C = {
        "green":  "#2ecc71", "red":    "#e74c3c",
        "blue":   "#3498db", "orange": "#e67e22",
        "grey":   "#8b949e", "bg":     "#161b22", "text": "#c9d1d9",
    }

    def _style(ax, title):
        ax.set_facecolor(C["bg"])
        ax.set_title(title, color=C["text"], fontsize=11, pad=8)
        ax.tick_params(colors=C["grey"], labelsize=8)
        for sp in ax.spines.values():
            sp.set_edgecolor("#30363d")
        ax.yaxis.label.set_color(C["grey"])
        ax.xaxis.label.set_color(C["grey"])

    # 1. Equity curve
    ax1 = fig.add_subplot(gs[0, 0])
    color = C["green"] if equity[-1] >= 0 else C["red"]
    ax1.plot(trade_nums, equity, color=color, linewidth=1.8)
    ax1.axhline(0, color=C["grey"], linewidth=0.8, linestyle="--")
    ax1.fill_between(trade_nums, equity, 0, where=(equity >= 0), alpha=0.15, color=C["green"])
    ax1.fill_between(trade_nums, equity, 0, where=(equity < 0),  alpha=0.15, color=C["red"])
    ax1.set_xlabel("Trade #"); ax1.set_ylabel("Cumulative R")
    _style(ax1, f"Equity Curve  ({stats['total_r']:+.1f}R | E={stats['expectancy_r']:+.3f}R/trade)")

    # 2. Drawdown
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.fill_between(trade_nums, dd, 0, color=C["red"], alpha=0.6)
    ax2.plot(trade_nums, dd, color=C["red"], linewidth=1.0)
    ax2.axhline(0, color=C["grey"], linewidth=0.8, linestyle="--")
    ax2.set_xlabel("Trade #"); ax2.set_ylabel("Drawdown (R)")
    _style(ax2, f"Drawdown  (max {stats['max_drawdown_r']:.1f}R)")

    # 3. P&L distribution
    ax3 = fig.add_subplot(gs[1, 0])
    wins_pnl   = pnl[pnl > 0]
    losses_pnl = pnl[pnl <= 0]
    bins = np.linspace(pnl.min() - 0.2, pnl.max() + 0.2, 30)
    ax3.hist(losses_pnl, bins=bins, color=C["red"],   alpha=0.75, label="Losses")
    ax3.hist(wins_pnl,   bins=bins, color=C["green"], alpha=0.75, label="Wins")
    ax3.axvline(0, color=C["grey"], linewidth=1.0, linestyle="--")
    ax3.axvline(float(np.mean(pnl)), color=C["blue"], linewidth=1.4, linestyle="-",
                label=f"Mean {np.mean(pnl):+.2f}R")
    ax3.set_xlabel("P&L (R)"); ax3.set_ylabel("Frequency")
    ax3.legend(fontsize=7, facecolor=C["bg"], labelcolor=C["text"])
    _style(ax3, f"P&L Distribution  (WR={stats['win_rate_pct']}% | PF={stats['profit_factor']})")

    # 4. By signal type
    ax4 = fig.add_subplot(gs[1, 1])
    by_type = stats.get("by_signal_type", {})
    sig_labels = list(by_type.get("count", {}).keys())
    if sig_labels:
        counts   = [by_type["count"].get(s, 0)  for s in sig_labels]
        mean_pnl = [by_type["mean"].get(s, 0)   for s in sig_labels]
        x = np.arange(len(sig_labels))
        bar_colors = [C["green"] if v >= 0 else C["red"] for v in mean_pnl]
        bars = ax4.bar(x, mean_pnl, color=bar_colors, alpha=0.8, width=0.5)
        ax4.set_xticks(x)
        short_labels = [
            s.replace("_retest", "_OB").replace("_fill", "_FVG")
             .replace("vwap_reversion", "VWAP Rev")
            for s in sig_labels
        ]
        ax4.set_xticklabels(short_labels, fontsize=8)
        ax4.axhline(0, color=C["grey"], linewidth=0.8, linestyle="--")
        ax4.set_ylabel("Avg R per trade")
        for bar, cnt in zip(bars, counts):
            ax4.text(bar.get_x() + bar.get_width() / 2,
                     bar.get_height() + 0.02,
                     f"n={cnt}", ha="center", va="bottom",
                     color=C["text"], fontsize=7)
    _style(ax4, "Performance by Signal Type")

    wr  = stats.get("win_rate_pct", 0)
    exp = stats.get("expectancy_r", 0)
    mdd = stats.get("max_drawdown_r", 0)
    n   = stats.get("total_trades", 0)
    fig.suptitle(
        f"Backtest Results  —  {n} trades  |  WR {wr}%  |  E={exp:+.3f}R  |  Max DD {mdd:.1f}R",
        color=C["text"], fontsize=12, y=0.98,
    )

    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    return out_path


# ── 15M OB sweep simulation ──────────────────────────────────────────────────

def simulate_ob_sweeps_15m(symbol: str,
                           df_15m: pd.DataFrame,
                           df_1h: pd.DataFrame,
                           df_daily: pd.DataFrame,
                           df_4h: Optional[pd.DataFrame],
                           strategy,
                           account_value: float = 10_000,
                           warmup_bars: int = 30) -> list[dict]:
    """
    15M-primary walk-forward simulation for OB sweep signals.

    Entry fires at the NEXT 15M bar open (15 minutes after the sweep),
    not the next 1H open (60 minutes later).  This is critical because
    the institutional move following a stop-hunt sweep is typically
    complete within 1-2 15M bars.
    """
    from .strategies.ny_open import _to_et, KILL_ZONE_START, KILL_ZONE_END, FORCE_CLOSE_HR

    trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    cooldown_until: int = 0
    consecutive_losses: int = 0
    circuit_breaker_until = None
    _diag: dict = {}
    _kz_bars = 0

    def _ts(idx: int) -> str:
        return str(df_15m.index[idx]) if idx < len(df_15m) else ""

    for i in range(warmup_bars, len(df_15m) - 1):
        bar_time = df_15m.index[i]
        bar_et   = _to_et(bar_time)
        t        = bar_et.time()

        # Only inside kill zone
        if not (KILL_ZONE_START <= t < KILL_ZONE_END):
            continue

        _kz_bars += 1
        next_open = float(df_15m["Open"].iloc[i + 1])
        next_high = float(df_15m["High"].iloc[i + 1])
        next_low  = float(df_15m["Low"].iloc[i + 1])

        # ── Manage open trade ────────────────────────────────────────────────
        if open_trade is not None:
            open_trade.exit_bar = i + 1
            closed = _manage_trade(open_trade, next_high, next_low, next_open)
            if closed:
                closed.exit_time = _ts(i + 1)
                trades.append(asdict(closed))
                cooldown_until = i + 2   # 30-min cooldown
                open_trade = None
                last_pnl = trades[-1].get("pnl_r", 0) or 0
                if last_pnl < 0:
                    consecutive_losses += 1
                    if consecutive_losses >= MAX_CONSEC_LOSSES:
                        import datetime as _dt
                        circuit_breaker_until = bar_et.date() + _dt.timedelta(days=1)
                else:
                    consecutive_losses = 0
            else:
                # Force close at kill zone end
                next_et = _to_et(df_15m.index[i + 1])
                if next_et.hour >= FORCE_CLOSE_HR:
                    xp   = float(df_15m["Close"].iloc[i + 1])
                    risk = open_trade.initial_risk or abs(open_trade.entry_price - open_trade.stop)
                    mult = 1.0 if open_trade.direction == "long" else -1.0
                    remaining_r = round((xp - open_trade.entry_price) * mult / risk, 2) if risk else 0
                    open_trade.exit_price  = round(xp, 4)
                    open_trade.exit_reason = "forced_close"
                    open_trade.exit_bar    = i + 1
                    open_trade.exit_time   = _ts(i + 1)
                    open_trade.pnl_r       = (
                        _blend(open_trade.partial_pnl_r, remaining_r)
                        if open_trade.partial_exit_done else remaining_r
                    )
                    trades.append(asdict(open_trade))
                    cooldown_until = i + 2
                    open_trade = None
            continue

        if i < cooldown_until:
            continue

        # ── Circuit breaker ──────────────────────────────────────────────────
        if circuit_breaker_until is not None:
            if bar_et.date() < circuit_breaker_until:
                continue
            circuit_breaker_until = None
            consecutive_losses    = 0

        # ── Look for 15M velocity OB sweep ───────────────────────────────────
        df_15m_slice = df_15m.iloc[:i + 1]
        df_1h_slice  = df_1h[df_1h.index  <= bar_time]
        df_4h_slice  = df_4h[df_4h.index  <= bar_time] if df_4h is not None else None
        d_trend      = _daily_trend(df_daily, bar_time)

        sig = strategy.generate_ob_sweep_signal(
            df_15m_slice, df_1h_slice, d_trend, df_4h_slice, _diag=_diag
        )
        if sig is None:
            continue

        entry = next_open   # next 15M open — 15 minutes after sweep
        stop  = sig["stop"]
        risk  = abs(entry - stop)

        if sig["direction"] == "long"  and stop >= entry:
            continue
        if sig["direction"] == "short" and stop <= entry:
            continue
        if risk < entry * 0.001:
            continue

        atr_val = sig.get("atr", risk)
        if atr_val and risk < atr_val * 0.15:
            continue

        tp_mult = float(sig.get("tp_mult", 2.0))
        tgts    = profit_targets(entry, stop, sig["direction"], atr_val, tp_mult=tp_mult)
        pos     = position_size(account_value, entry, stop)

        rr = abs(tgts["tp1"] - entry) / risk
        if rr < 1.0:
            continue

        open_trade = Trade(
            symbol          = symbol,
            direction       = sig["direction"],
            signal_type     = sig["signal_type"],
            entry_bar       = i + 1,
            entry_time      = _ts(i + 1),
            entry_price     = round(entry, 4),
            stop            = round(stop, 4),
            tp1             = tgts["tp1"],
            tp2             = tgts["tp2"],
            shares          = pos["shares"],
            regime          = sig.get("regime", "trending"),
            atr             = atr_val,
            initial_risk    = risk,
            trail_to_tp2    = sig.get("trail", False),
            scale_out_trail = sig.get("scale_out_trail", False),
        )

    top_rejects = sorted(_diag.items(), key=lambda x: -x[1])[:5]
    print(f"    ob_sweep diag ({symbol}): {_kz_bars} kz bars | "
          f"{len(trades)} trades | filters: {dict(top_rejects)}", flush=True)
    return trades


# ── Entry point ───────────────────────────────────────────────────────────────

def run_backtest(symbols: list[str],
                 strategy_name: str = "smc",
                 account_value: float = 10_000) -> dict:
    from .market_data import get_ohlcv
    from .strategies import get_strategy

    strategy = get_strategy(strategy_name)
    print(f"Strategy: {strategy.name}", flush=True)

    needs_multi_tf = strategy_name == "ny_open"

    all_trades: list[dict] = []
    for sym in symbols:
        print(f"  backtesting {sym}...", flush=True)
        df_1h    = get_ohlcv(sym, "1h")
        df_daily = get_ohlcv(sym, "daily")
        if df_1h.empty or df_daily.empty:
            print(f"  {sym}: no data, skipping")
            continue

        # Symbol quality screen for NY Open: only trade stocks with real intraday
        # momentum. Low-ATR% choppy names produce noise FVG signals that stop out
        # immediately. Require avg daily range >= 1.0% of price over last 30 days.
        if needs_multi_tf and len(df_daily) >= 10:
            recent = df_daily.tail(30)
            avg_range_pct = (recent["High"] - recent["Low"]).mean() / recent["Close"].mean()
            if avg_range_pct < 0.010:
                print(f"  {sym}: avg daily range {avg_range_pct:.1%} < 1.0%, skipping")
                continue

        df_15m = get_ohlcv(sym, "15m") if needs_multi_tf else None
        df_4h  = get_ohlcv(sym, "4h")  if needs_multi_tf else None

        if needs_multi_tf:
            n15 = len(df_15m) if df_15m is not None else 0
            print(f"  {sym}: df_15m={n15} bars loaded", flush=True)

        # 1H simulation (FVG fill + session levels)
        trades = simulate_symbol(sym, df_1h, df_daily, strategy,
                                 df_15m, df_4h, account_value)

        # 15M-primary OB sweep simulation (enters 15 min after sweep)
        trades_ob: list[dict] = []
        if needs_multi_tf and df_15m is not None and len(df_15m) > 30:
            trades_ob = simulate_ob_sweeps_15m(
                sym, df_15m, df_1h, df_daily, df_4h, strategy, account_value
            )

        print(f"  {sym}: {len(trades)} 1H trades + {len(trades_ob)} ob_sweep trades")
        all_trades.extend(trades)
        all_trades.extend(trades_ob)

    return compute_stats(all_trades)
