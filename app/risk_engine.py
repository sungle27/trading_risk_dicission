from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RiskPlan:
    symbol: str
    direction: str
    entry: float
    sl: float
    tp: float
    qty: float
    risk_usd: float
    risk_pct: float
    rr: float
    notes: str = ""


def _getf(cfg, key: str, default: float) -> float:
    try:
        return float(getattr(cfg, key))
    except Exception:
        return float(default)


def build_risk_plan(
    *,
    symbol: str,
    direction: str,
    entry: float,
    atr_value: float,
    nav_usd: float,
    mode: str,
    cfg,
    rr: float = 2.0,
    risk_multiplier: float = 1.0,
    sl_atr_mult: Optional[float] = None,
    target_vol_pct: Optional[float] = None,
) -> RiskPlan:
    """
    Build position sizing with:
    - risk% per trade (RISK_EARLY / RISK_MAIN) capped by RISK_MAX
    - ATR-based SL distance
    - optional volatility-adjust sizing (TARGET_VOL_PCT)
    """
    direction = direction.upper()
    entry = float(entry)
    atr_value = float(atr_value)
    nav_usd = float(nav_usd)

    risk_early = _getf(cfg, "RISK_EARLY", 0.25)   # percent NAV
    risk_main = _getf(cfg, "RISK_MAIN", 0.50)     # percent NAV
    risk_max = _getf(cfg, "RISK_MAX", 1.00)       # percent NAV cap

    base_risk_pct = risk_early if mode.lower() == "early" else risk_main
    risk_pct = base_risk_pct * float(risk_multiplier)
    risk_pct = max(0.01, min(risk_max, risk_pct))

    # SL ATR mult
    if sl_atr_mult is None:
        sl_atr_mult = _getf(cfg, "SL_ATR_MULT", 1.5)
    sl_atr_mult = float(sl_atr_mult)

    stop_dist = max(atr_value * sl_atr_mult, entry * 0.0005)  # avoid too tiny stops
    risk_usd = nav_usd * (risk_pct / 100.0)

    # volatility adjusted sizing (scale qty only, keep risk_usd cap)
    vol_scale = 1.0
    if target_vol_pct is not None and entry > 0:
        atr_pct = atr_value / entry
        if atr_pct > 0:
            vol_scale = float(target_vol_pct) / atr_pct
            vol_scale = max(0.50, min(2.00, vol_scale))

    qty = (risk_usd / stop_dist) * vol_scale

    # SL / TP
    rr = float(rr)
    rr = max(1.0, min(4.0, rr))
    tp_dist = stop_dist * rr

    if direction == "LONG":
        sl = entry - stop_dist
        tp = entry + tp_dist
    else:
        sl = entry + stop_dist
        tp = entry - tp_dist

    notes = f"risk_pct={risk_pct:.2f}% stop={stop_dist:.6f} vol_scale={vol_scale:.2f}"
    return RiskPlan(
        symbol=symbol,
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        qty=qty,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
        rr=rr,
        notes=notes,
    )
