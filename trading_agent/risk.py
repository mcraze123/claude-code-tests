"""
Risk management: position sizing, target calculation, and pre-trade validation.
"""

from .config import MAX_RISK_PER_TRADE, MIN_RISK_REWARD, MAX_POSITION_VALUE_USD


def position_size(account_value: float, entry: float, stop: float) -> dict:
    """
    Fixed-fractional sizing: risk MAX_RISK_PER_TRADE of account per trade.
    Returns shares, dollar value, and actual risk amount.
    """
    risk_dollars = account_value * MAX_RISK_PER_TRADE
    risk_per_share = abs(entry - stop)

    if risk_per_share < 0.001:
        return {"shares": 0, "position_value": 0, "risk_amount": 0, "error": "stop too close to entry"}

    shares = int(risk_dollars / risk_per_share)

    # Cap by max position value
    if shares * entry > MAX_POSITION_VALUE_USD:
        shares = max(1, int(MAX_POSITION_VALUE_USD / entry))

    return {
        "shares": shares,
        "position_value": round(shares * entry, 2),
        "risk_amount": round(shares * risk_per_share, 2),
        "risk_pct": round(shares * risk_per_share / account_value * 100, 2),
    }


def profit_targets(entry: float, stop: float, bias: str, atr_val: float = None) -> dict:
    """
    Three tiered targets:
      TP1 = 1.5R  (or 1× ATR)  — scale out 50%
      TP2 = 2.5R  (or 2× ATR)  — scale out 30%
      TP3 = 4R    (or 3× ATR)  — runner
    """
    risk = abs(entry - stop)
    direction = 1 if bias in ("bullish", "long") else -1

    if atr_val and atr_val > 0:
        tp1 = entry + direction * atr_val * 1.0
        tp2 = entry + direction * atr_val * 2.0
        tp3 = entry + direction * atr_val * 3.5
    else:
        tp1 = entry + direction * risk * 1.5
        tp2 = entry + direction * risk * 2.5
        tp3 = entry + direction * risk * 4.0

    return {
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "rr_tp1": round(abs(tp1 - entry) / risk, 2) if risk else 0,
        "rr_tp2": round(abs(tp2 - entry) / risk, 2) if risk else 0,
    }


def validate(entry: float, stop: float, tp1: float,
             account_value: float, buying_power: float) -> dict:
    """
    Check that the trade meets minimum R:R, we have enough buying power,
    and the position size is at least 1 share.
    """
    risk = abs(entry - stop)
    reward = abs(tp1 - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0

    issues: list[str] = []
    if rr < MIN_RISK_REWARD:
        issues.append(f"R:R {rr:.1f} below minimum {MIN_RISK_REWARD}")

    pos = position_size(account_value, entry, stop)
    if pos.get("error"):
        issues.append(pos["error"])
    elif pos["shares"] < 1:
        issues.append("position rounds to 0 shares")
    elif pos["position_value"] > buying_power:
        issues.append(f"need ${pos['position_value']:.2f} but only ${buying_power:.2f} buying power")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "risk_reward": rr,
        "position": pos,
    }
