from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Decision:
    allow: bool
    risk_mult: float
    rr: float
    sl_atr_mult: float
    reason: str


def decide_trade(
    *,
    market_regime: str,
    market_panic: bool,
    mode: str,
    direction: str,
    score: int,
    high_conf: bool,
    base_rr: float,
    base_sl_atr_mult: float,
) -> Decision:
    """
    Centralized decision policy:
    - block/allow
    - adjust risk multiplier
    - choose RR + SL(ATR mult)
    """

    regime = (market_regime or "NORMAL").upper()

    # -------------------------
    # PANIC policy
    # -------------------------
    if market_panic:
        if direction.upper() == "LONG":
            return Decision(False, 0.0, base_rr, base_sl_atr_mult, "PANIC: block LONG")
        # allow SHORT but reduce risk and RR a bit
        rr = min(base_rr, 1.8)
        slm = base_sl_atr_mult * 1.05
        return Decision(True, 0.60, rr, slm, "PANIC: allow SHORT (reduced risk)")

    # -------------------------
    # Mode-specific policy
    # -------------------------
    # If later bạn muốn block EARLY trong RANGE/RECOVERY thì xử lý ở đây
    if mode.lower() == "early":
        # conservative for early signals
        if score < 7 and not high_conf:
            return Decision(False, 0.0, base_rr, base_sl_atr_mult, "EARLY: score too low")
        # early allowed but smaller size
        rr = max(1.6, base_rr)
        return Decision(True, 0.75, rr, base_sl_atr_mult, "EARLY: allow (reduced risk)")

    # -------------------------
    # MAIN policy by regime
    # -------------------------
    risk_mult = 1.0
    rr = float(base_rr)
    slm = float(base_sl_atr_mult)

    if high_conf:
        rr = max(rr, 2.5)
        risk_mult *= 1.20
        slm *= 1.05

    if regime == "TREND":
        rr = max(rr, 2.2)
        risk_mult *= 1.10
        slm *= 1.10
        if score < 10 and not high_conf:
            return Decision(False, 0.0, rr, slm, "TREND: MAIN not strong enough")

    elif regime == "RANGE":
        rr = min(rr, 1.6)
        risk_mult *= 0.75
        slm *= 0.90
        if score < 12:
            return Decision(False, 0.0, rr, slm, "RANGE: MAIN score too low")

    elif regime == "RECOVERY":
        rr = min(rr, 1.7)
        risk_mult *= 0.55
        slm *= 0.95
        if score < 12 or not high_conf:
            return Decision(False, 0.0, rr, slm, "RECOVERY: require high_conf & strong score")

    else:
        # NORMAL (default)
        rr = max(rr, 1.8)
        risk_mult *= (1.0 if high_conf else 0.90)

    rr = max(1.2, min(3.0, rr))
    slm = max(0.8, min(2.8, slm))

    return Decision(True, risk_mult, rr, slm, f"{regime}: allow")
