from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RiskPlan:
    # sizing
    nav_usd: float
    risk_pct: float          # % NAV
    risk_usd: float

    # prices
    entry: float
    sl: float
    tp: Optional[float]

    # distance
    atr_value: float
    sl_dist: float
    rr: float

    # quantity (in coin units)
    qty: float

    # metadata
    note: str


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _infer_side(direction: str) -> str:
    d = direction.upper().strip()
    if d not in ("LONG", "SHORT"):
        return "LONG"
    return d


def build_risk_plan(
    *,
    symbol: str,
    direction: str,
    entry: float,
    atr_value: float,
    nav_usd: float,
    mode: str,
    cfg,
    rr: Optional[float] = None,
) -> RiskPlan:

    side = _infer_side(direction)
    m = mode.lower().strip()

    # ---- Risk % NAV
    if m == "early":
        risk_pct = float(getattr(cfg, "RISK_EARLY", 0.25))
    else:
        risk_pct = float(getattr(cfg, "RISK_MAIN", 0.50))

    # optional cap
    risk_max = float(getattr(cfg, "RISK_MAX", 1.0))
    risk_pct = _clamp(risk_pct, 0.01, risk_max)

    risk_usd = nav_usd * (risk_pct / 100.0)

    # ---- ATR-based SL
    sl_mult = float(getattr(cfg, "SL_ATR_MULT", 1.5))
    sl_dist = max(atr_value * sl_mult, entry * 0.001)  # fallback >= 0.1%

    if side == "LONG":
        sl = max(0.0, entry - sl_dist)
    else:
        sl = entry + sl_dist

    # ---- Quantity from risk
    per_unit_risk = abs(entry - sl)
    qty = (risk_usd / per_unit_risk) if per_unit_risk > 0 else 0.0

    # ---- TP by RR (optional)
    rr_val = float(rr if rr is not None else getattr(cfg, "TP_RR", 2.0))
    tp: Optional[float]
    if rr_val <= 0:
        tp = None
    else:
        if side == "LONG":
            tp = entry + rr_val * (entry - sl)
        else:
            tp = entry - rr_val * (sl - entry)

    note = (
        f"{symbol} {side} | mode={m} | risk={risk_pct:.2f}% "
        f"| atr={atr_value:.6f} sl_mult={sl_mult} rr={rr_val}"
    )

    return RiskPlan(
        nav_usd=nav_usd,
        risk_pct=risk_pct,
        risk_usd=risk_usd,
        entry=entry,
        sl=sl,
        tp=tp,
        atr_value=atr_value,
        sl_dist=sl_dist,
        rr=rr_val,
        qty=qty,
        note=note,
    )
