from __future__ import annotations

from dataclasses import dataclass

from app.market_regime import Regime


@dataclass
class Decision:
    allow: bool
    risk_mult: float
    reason: str


def decide(regime: Regime, mode: str, score: int, high_conf: bool) -> Decision:
    # PANIC: block all
    if regime == Regime.PANIC:
        return Decision(False, 0.0, "PANIC: block all signals")

    # RECOVERY: block EARLY, MAIN very selective
    if regime == Regime.RECOVERY:
        if mode == "early":
            return Decision(False, 0.0, "RECOVERY: block EARLY")
        if score >= 12 and high_conf:
            return Decision(True, 0.5, "RECOVERY: allow only high_conf MAIN (reduced risk)")
        return Decision(False, 0.0, "RECOVERY: MAIN not strong enough")

    # RANGE: block EARLY, MAIN selective
    if regime == Regime.RANGE:
        if mode == "early":
            return Decision(False, 0.0, "RANGE: block EARLY")
        if score >= 12:
            return Decision(True, 0.7, "RANGE: allow selective MAIN (reduced risk)")
        return Decision(False, 0.0, "RANGE: MAIN score too low")

    # TREND: allow, but EARLY selective
    if regime == Regime.TREND:
        if mode == "early":
            if score >= 7:
                return Decision(True, 0.6, "TREND: allow EARLY selective (reduced risk)")
            return Decision(False, 0.0, "TREND: EARLY score too low")
        return Decision(True, 1.0 if high_conf else 0.9, "TREND: allow MAIN")

    # NORMAL
    return Decision(True, 1.0 if high_conf else 0.8, "NORMAL: allow")
