#!/usr/bin/env python3
"""
Trading analysis runner — called by the Claude agent to get market data.

Usage:
    python -m trading_agent.run screen                          # screen top movers
    python -m trading_agent.run analyze TSLA                    # full TA on one symbol
    python -m trading_agent.run quote TSLA AAPL                 # quick quotes
    python -m trading_agent.run size TSLA 45.20 43.80 bull 8000 4000
    python -m trading_agent.run backtest NVDA TSLA AMD SPY                        # backtest (default: smc)
    python -m trading_agent.run backtest NVDA TSLA --strategy hmm_ob --plot       # strategies: smc | hmm_smc | hmm_ob | logistic

Output is JSON to stdout so Claude can parse and act on it.
"""

import sys
import json
from .screener import run_screen
from .market_data import get_ohlcv, get_quote
from .analysis import full_analysis
from .risk import position_size, profit_targets, validate


def cmd_screen():
    results = run_screen(max_candidates=25)
    out = []
    for r in results[:15]:
        out.append({
            "symbol":     r["symbol"],
            "price":      r["price"],
            "pct_change": r["pct_change"],
            "rel_volume": r["rel_volume"],
            "score":      r["score"],
            "bias":       r["bias"],
            "signals":    r["signals"],
        })
    print(json.dumps(out, indent=2, default=str))


def cmd_analyze(symbol: str):
    df_map = {tf: get_ohlcv(symbol, tf) for tf in ("daily", "4h", "1h", "15m")}
    ta = full_analysis(symbol, df_map)
    print(json.dumps(ta, indent=2, default=str))


def cmd_quote(*symbols):
    out = {sym: get_quote(sym) for sym in symbols}
    print(json.dumps(out, indent=2, default=str))


def cmd_size(symbol, entry, stop, bias, account_value, buying_power, atr=None):
    entry, stop = float(entry), float(stop)
    account_value, buying_power = float(account_value), float(buying_power)
    atr = float(atr) if atr else None

    tgt = profit_targets(entry, stop, bias, atr)
    val = validate(entry, stop, tgt["tp1"], account_value, buying_power)
    pos = position_size(account_value, entry, stop)

    print(json.dumps({"position": pos, "targets": tgt, "validation": val}, indent=2))


def cmd_diagnose(symbol: str):
    """Print exactly what the signal detector sees on the last 60 bars of real data."""
    from .market_data import get_ohlcv
    from .analysis import order_blocks, fair_value_gaps, rsi as calc_rsi, atr as calc_atr, intraday_vwap
    from .strategies.smc import SMCStrategy
    _smc = SMCStrategy()

    df = get_ohlcv(symbol, "1h")
    if df.empty:
        print(f"No data for {symbol}")
        return

    print(f"{symbol}  —  {len(df)} 1H bars  ({df.index[0].date()} → {df.index[-1].date()})")
    print(f"Price range: {df['Close'].min():.2f} – {df['Close'].max():.2f}")
    print()

    signals_found = 0
    # Scan last 60 bars
    start = max(30, len(df) - 60)
    for i in range(start, len(df) - 1):
        sl    = df.iloc[:i + 1]
        price = float(sl["Close"].iloc[-1])
        obs   = order_blocks(sl, lookback=30)
        fvgs  = fair_value_gaps(sl, lookback=40)
        vwap_v = intraday_vwap(sl)
        rsi_v  = float(calc_rsi(sl).iloc[-1])
        atr_v  = float(calc_atr(sl).iloc[-1])
        vwap_dev = (price - vwap_v) / vwap_v * 100

        bull_ob_near = [o for o in obs if o["type"] == "bullish"
                        and o["low"] * 0.99 <= price <= o["high"] * 1.01]
        bear_ob_near = [o for o in obs if o["type"] == "bearish"
                        and o["low"] * 0.99 <= price <= o["high"] * 1.01]
        bull_fvg_near = [f for f in fvgs if f["type"] == "bullish" and not f["filled"]
                         and f["bottom"] * 0.997 <= price <= f["top"] * 1.003]
        bear_fvg_near = [f for f in fvgs if f["type"] == "bearish" and not f["filled"]
                         and f["bottom"] * 0.997 <= price <= f["top"] * 1.003]

        sig = _smc.generate_signal(sl, "neutral")
        if sig or bull_ob_near or bear_ob_near or bull_fvg_near or bear_fvg_near or abs(vwap_dev) > 2.0:
            signals_found += 1
            bar_time = sl.index[-1].strftime("%m-%d %H:%M")
            print(f"  bar {i:3d} {bar_time}  price={price:.2f}  RSI={rsi_v:.0f}  "
                  f"ATR={atr_v:.3f}  VWAP_dev={vwap_dev:+.1f}%")
            print(f"    OBs total={len(obs)}  bull_near={len(bull_ob_near)}  bear_near={len(bear_ob_near)}")
            print(f"    FVGs total={len(fvgs)}  bull_near={len(bull_fvg_near)}  bear_near={len(bear_fvg_near)}")
            if sig:
                print(f"    ✓ SIGNAL: {sig['signal_type']} {sig['direction']}  "
                      f"stop={sig['stop']}  atr={sig['atr']:.3f}")
            else:
                # Show why each signal type failed
                reasons = []
                if bull_ob_near and rsi_v >= 62:
                    reasons.append(f"bull OB blocked: RSI {rsi_v:.0f} ≥ 62")
                if bull_fvg_near and rsi_v >= 58:
                    reasons.append(f"bull FVG blocked: RSI {rsi_v:.0f} ≥ 58")
                if vwap_dev < -2.0 and rsi_v >= 38:
                    reasons.append(f"VWAP long blocked: RSI {rsi_v:.0f} ≥ 38")
                if vwap_dev > 2.0 and rsi_v <= 62:
                    reasons.append(f"VWAP short blocked: RSI {rsi_v:.0f} ≤ 62")
                if reasons:
                    print(f"    ✗ near signal but blocked: {'; '.join(reasons)}")
            print()

    if signals_found == 0:
        print("No near-signal bars found in last 60 bars.")
        print(f"\nSample bar stats (last bar):")
        sl = df
        obs  = order_blocks(sl, lookback=30)
        fvgs = fair_value_gaps(sl, lookback=40)
        print(f"  OBs found (lookback=30): {len(obs)}")
        print(f"  FVGs found (lookback=40): {len(fvgs)}")
        price = float(sl["Close"].iloc[-1])
        for o in obs[:5]:
            dist = (price - o["mid"]) / price * 100
            print(f"  OB {o['type']:8s}  {o['low']:.2f}–{o['high']:.2f}  dist from price: {dist:+.1f}%")


def cmd_backtest(symbols: list[str], plot: bool = False,
                 account_value: float = 10_000, strategy: str = "smc"):
    from .backtest import run_backtest, plot_results
    print(f"Running backtest on: {', '.join(symbols)}  [{strategy}]", flush=True)
    results = run_backtest(symbols, strategy_name=strategy, account_value=account_value)
    trade_log = results.pop("trade_log", [])
    print("\n=== BACKTEST RESULTS ===")
    print(json.dumps(results, indent=2, default=str))
    print(f"\n=== TRADE LOG ({len(trade_log)} trades) ===")
    print(json.dumps(trade_log, indent=2, default=str))
    if plot and trade_log:
        results["trade_log"] = trade_log  # restore for plot
        path = plot_results(results, out_path="backtest_results.png")
        print(f"\nChart saved → {path}")


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python -m trading_agent.run <command> [args...]", file=sys.stderr)
        sys.exit(1)

    cmd = args[0].lower()
    if cmd == "screen":
        cmd_screen()
    elif cmd == "analyze" and len(args) >= 2:
        cmd_analyze(args[1].upper())
    elif cmd == "quote" and len(args) >= 2:
        cmd_quote(*[s.upper() for s in args[1:]])
    elif cmd == "size" and len(args) >= 7:
        cmd_size(*args[1:])
    elif cmd == "diagnose" and len(args) >= 2:
        cmd_diagnose(args[1].upper())
    elif cmd == "backtest" and len(args) >= 2:
        do_plot  = "--plot" in args
        strategy = "smc"
        syms     = []
        toks     = iter(args[1:])
        for tok in toks:
            if tok == "--plot":
                pass
            elif tok == "--strategy":
                strategy = next(toks, "smc")
            else:
                syms.append(tok.upper())
        cmd_backtest(syms, plot=do_plot, strategy=strategy)
    else:
        print(f"Unknown command or missing args: {args}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
