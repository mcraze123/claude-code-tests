#!/usr/bin/env python3
"""
Robinhood Agentic Trading System — entry point.

Usage:
    python -m trading_agent.main                # one-shot cycle
    python -m trading_agent.main --schedule     # recurring (market hours)
    python -m trading_agent.main --dry-run      # analysis only, no trades
    python -m trading_agent.main --prompt "..."  # custom prompt
"""

import sys
import time
import argparse
from datetime import datetime
import pytz
import schedule

from .config import ANTHROPIC_API_KEY, ROBINHOOD_MCP_TOKEN
from .agent import run_cycle


def _market_open() -> bool:
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    o = now.replace(hour=9, minute=30, second=0, microsecond=0)
    c = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return o <= now <= c


def _run_if_open(prompt: str = None):
    if _market_open():
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Market open — starting cycle")
        run_cycle(prompt)
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Market closed — skipping")


def main():
    parser = argparse.ArgumentParser(description="Robinhood Trading Agent")
    parser.add_argument("--schedule", action="store_true",
                        help="Run on a schedule during market hours")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analysis only — no trades (sets prompt accordingly)")
    parser.add_argument("--prompt", type=str, default=None,
                        help="Custom prompt to pass to the agent")
    args = parser.parse_args()

    print("=" * 60)
    print("  ROBINHOOD AGENTIC TRADING SYSTEM")
    print("=" * 60)

    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in environment / .env")
        sys.exit(1)

    if not ROBINHOOD_MCP_TOKEN:
        print("⚠  ROBINHOOD_MCP_TOKEN not set — analysis mode only.")
        print("   To enable live trading:")
        print("   1. Open Robinhood app → Settings → Agentic Trading")
        print("   2. Enable and authorise an agent")
        print("   3. Copy the token into .env as ROBINHOOD_MCP_TOKEN=...")
        print()

    prompt = args.prompt
    if args.dry_run:
        prompt = (
            "Analyse the top movers and identify the best potential trade setups. "
            "Do NOT place any orders — just report the setups you found, their scores, "
            "and what you would trade if this were live, with full reasoning."
        )
        print("[DRY RUN] Analysis only — no orders will be placed.\n")

    if args.schedule:
        print("Scheduling: every 30 min + key times during market hours.")
        schedule.every(30).minutes.do(_run_if_open, prompt=prompt)
        for t in ("09:35", "10:00", "11:30", "13:00", "14:30", "15:30"):
            schedule.every().day.at(t).do(_run_if_open, prompt=prompt)

        _run_if_open(prompt)
        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        run_cycle(prompt)


if __name__ == "__main__":
    main()
