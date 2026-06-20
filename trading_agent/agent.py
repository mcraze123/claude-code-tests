"""
Core agentic loop.

Architecture:
  - Claude drives all reasoning and decisions via the Anthropic API.
  - Robinhood MCP server is passed directly to the API so Claude can call
    get_accounts, get_equity_positions, get_equity_quotes, search,
    review_equity_order, place_equity_order, cancel_equity_order, etc.
  - Custom Python tools handle market data fetching and technical analysis
    (the Robinhood MCP only provides quotes and order management; it has
    no TA, screener, or multi-timeframe analysis built in).

Flow per cycle:
  1. screen_top_movers  →  ranked candidate list
  2. get_technical_analysis per top candidate
  3. get_accounts  (via Robinhood MCP)
  4. calculate_trade_parameters  →  size / stop / targets
  5. review_equity_order  (via Robinhood MCP) — ALWAYS before placing
  6. place_equity_order  (via Robinhood MCP) — only if review passes
"""

import json
import os
import traceback
import anthropic

from .config import (
    MODEL,
    ROBINHOOD_MCP_URL,
    ROBINHOOD_MCP_TOKEN,
    MIN_SCORE_TO_TRADE,
)
from .screener import run_screen
from .market_data import get_ohlcv, get_quote
from .analysis import full_analysis
from .risk import position_size, profit_targets, validate


_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = f"""You are an elite intraday trading agent operating a live Robinhood agentic account.

## STRATEGY

You combine two complementary approaches:

### Mean Reversion
Triggered when price is extended from key levels:
- >3% deviation from VWAP → fade back toward VWAP
- Price inside or just touching an unfilled FVG → target FVG fill
- Unfilled daily gap within 5% → trade toward gap fill
- RSI <30 or >70 with confluence → counter-trend scalp

### Trend Following
Triggered when structure is clear and momentum confirms:
- Order block retest in direction of higher-TF trend
- Break of Structure (BOS) + retest → continuation
- VWAP reclaim with volume → momentum entry
- EMA9/21 cross with price above/below VWAP

## MULTI-TIMEFRAME FRAMEWORK

Daily  → macro bias (is this a bull or bear stock today?)
4H     → intermediate structure (where are the significant OBs / FVGs?)
1H     → entry timeframe (what level are we reacting to?)
15M    → trigger (what candle pattern confirms the entry?)

Only trade when at least 3 of 4 timeframes agree on direction.

## EXECUTION RULES (NON-NEGOTIABLE)

1. ALWAYS call review_equity_order before place_equity_order — never skip.
2. Minimum R:R 2:1. If TP1 is not at least 2× the stop distance, skip the trade.
3. Stop placement: below the low of the triggering OB/FVG/level (longs);
   above the high (shorts). Never a round-number-only stop.
4. Max 2% account risk per trade; max $500 position value (agentic account cap).
5. Max 5 open positions at once — check get_equity_positions first.
6. When in doubt, skip. Missing a trade is better than a bad trade.

## STOCK SELECTION

Prefer:
- High relative volume (≥2×) — confirms conviction
- Clear technical setup on 1H with higher-TF alignment
- Price $0.50–$500 (cheap to large cap; ETFs are fine)
- Liquid: ≥300k avg daily volume

Avoid:
- News-driven gap-and-trap without structure
- Stocks approaching earnings within 48h (binary risk)
- Very low float meme stocks with no technical structure

## TOOL WORKFLOW

Step 1: screen_top_movers → pick top 3–5 by score ≥ {MIN_SCORE_TO_TRADE}
Step 2: get_technical_analysis on each
Step 3: get_accounts (Robinhood MCP) → check balance + open positions
Step 4: calculate_trade_parameters for best setup(s)
Step 5: review_equity_order (Robinhood MCP) for each planned trade
Step 6: place_equity_order (Robinhood MCP) only if review shows no blockers
Step 7: Summarize what you did and why

Always explain your reasoning at each step."""


CUSTOM_TOOLS = [
    {
        "name": "screen_top_movers",
        "description": (
            "Screen the top movers universe and score each symbol for trade potential "
            "using multi-timeframe technical analysis. Returns a ranked list (score 0–100) "
            "with bias (bullish/bearish/neutral) and the signals that drove the score. "
            "Run this first every cycle."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_technical_analysis",
        "description": (
            "Full technical analysis for a symbol across daily / 4H / 1H / 15M timeframes. "
            "Returns: trend direction, VWAP + deviation, RSI, ATR, nearest order blocks, "
            "unfilled FVGs, daily gaps, liquidity zones, and Break of Structure. "
            "Use this after screening to deep-dive on top candidates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "Ticker e.g. AAPL"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_current_quote",
        "description": "Lightweight real-time quote: price, pct_change, volume, rel_volume, market_cap.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "calculate_trade_parameters",
        "description": (
            "Calculate position size, stop loss, take-profit targets (TP1/TP2/TP3), "
            "and risk/reward ratio. Validates the trade against account constraints. "
            "Returns position (shares, value, risk$), targets, and validation result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":        {"type": "string"},
                "entry_price":   {"type": "number"},
                "stop_price":    {"type": "number"},
                "bias":          {"type": "string", "enum": ["bullish", "bearish"]},
                "account_value": {"type": "number"},
                "buying_power":  {"type": "number"},
                "atr":           {"type": "number", "description": "ATR for target calc (optional)"},
            },
            "required": ["symbol", "entry_price", "stop_price", "bias",
                         "account_value", "buying_power"],
        },
    },
]


def _run_custom_tool(name: str, inp: dict) -> str:
    if name == "screen_top_movers":
        results = run_screen(max_candidates=20)
        # Trim ta from serialized output (too large); keep summary fields
        trimmed = []
        for r in results[:12]:
            trimmed.append({
                "symbol":     r["symbol"],
                "price":      r["price"],
                "pct_change": r["pct_change"],
                "rel_volume": r["rel_volume"],
                "score":      r["score"],
                "bias":       r["bias"],
                "signals":    r["signals"],
            })
        return json.dumps(trimmed, default=str)

    if name == "get_technical_analysis":
        sym = inp["symbol"]
        df_map = {tf: get_ohlcv(sym, tf) for tf in ("daily", "4h", "1h", "15m")}
        ta = full_analysis(sym, df_map)
        return json.dumps(ta, default=str)

    if name == "get_current_quote":
        return json.dumps(get_quote(inp["symbol"]), default=str)

    if name == "calculate_trade_parameters":
        entry   = inp["entry_price"]
        stop    = inp["stop_price"]
        bias    = inp["bias"]
        acct    = inp["account_value"]
        bp      = inp["buying_power"]
        atr_val = inp.get("atr")

        targets = profit_targets(entry, stop, bias, atr_val)
        v       = validate(entry, stop, targets["tp1"], acct, bp)
        pos     = position_size(acct, entry, stop)

        return json.dumps({"position": pos, "targets": targets, "validation": v})

    return json.dumps({"error": f"unknown tool: {name}"})


def _build_kwargs(messages: list) -> dict:
    kwargs = {
        "model":      MODEL,
        "max_tokens": 4096,
        "system":     SYSTEM_PROMPT,
        "tools":      CUSTOM_TOOLS,
        "messages":   messages,
    }

    if ROBINHOOD_MCP_TOKEN:
        kwargs["mcp_servers"] = [
            {
                "type":                "url",
                "url":                 ROBINHOOD_MCP_URL,
                "name":                "robinhood-trading",
                "authorization_token": ROBINHOOD_MCP_TOKEN,
            }
        ]
        kwargs["betas"] = ["mcp-client-2025-04-04"]

    return kwargs


def run_cycle(user_prompt: str = None) -> list[dict]:
    """
    Run one complete trading cycle. Returns the full message history.
    """
    if user_prompt is None:
        user_prompt = (
            f"Run a full trading cycle:\n"
            f"1. Screen top movers — identify the top 3–5 candidates with score ≥ {MIN_SCORE_TO_TRADE}.\n"
            f"2. Deep-dive technical analysis on each top candidate.\n"
            f"3. Check my Robinhood account balance and open positions.\n"
            f"4. For each viable setup: calculate trade params, review the order, then place it if valid.\n"
            f"5. Summarize: what you found, what you traded (if anything), and the reasoning.\n\n"
            f"Quality over quantity — skip if the setup isn't clean."
        )

    messages = [{"role": "user", "content": user_prompt}]
    max_iter = 30

    for i in range(max_iter):
        kwargs = _build_kwargs(messages)
        use_beta = "betas" in kwargs

        try:
            if use_beta:
                response = _client.beta.messages.create(**kwargs)
            else:
                response = _client.messages.create(**kwargs)
        except Exception as exc:
            print(f"[ERROR] API call failed: {exc}")
            break

        # Print any text the agent produced
        for block in response.content:
            if hasattr(block, "text") and block.text:
                print(f"\n{'─'*60}\nAGENT:\n{block.text}")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            print(f"[WARN] unexpected stop_reason: {response.stop_reason}")
            break

        # Handle tool calls
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tname = block.name
            tinput = block.input
            tid = block.id
            print(f"\n[TOOL] {tname}({json.dumps(tinput)[:120]})")

            try:
                result = _run_custom_tool(tname, tinput)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tid,
                    "content":     result[:10_000],
                })
                print(f"  → {result[:200]}…")
            except Exception:
                err = traceback.format_exc()
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tid,
                    "content":     f"Error:\n{err}",
                    "is_error":    True,
                })
                print(f"  [ERROR] {err[:200]}")

        messages.append({"role": "assistant", "content": response.content})
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
    else:
        print("[WARN] max iterations reached")

    return messages
