#!/usr/bin/env python3
"""
Trading analysis runner — called by the Claude agent to get market data.

Usage:
    python -m trading_agent.run screen              # screen top movers
    python -m trading_agent.run analyze TSLA        # full TA on one symbol
    python -m trading_agent.run quote TSLA AAPL     # quick quotes
    python -m trading_agent.run size TSLA 45.20 43.80 bull 8000 4000

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
    else:
        print(f"Unknown command or missing args: {args}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
