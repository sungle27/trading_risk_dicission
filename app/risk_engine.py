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


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


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
    risk_multiplier: float = 1.0,
    sl_atr_mult: Optional[float] = None,
    target_vol_pct: Optional[float] = None,
) -> RiskPlan:
    """
    Compute position sizing + SL/TP based on ATR + risk% + RR.

    - rr: take-profit RR
    - risk_multiplier: final multiplier (regime/dd/etc)
    - target_vol_pct: volatility-adjust sizing (optional)
    """

    entry = float(entry)
    atr_value = float(atr_value)
    nav_usd = float(nav_usd)

    # base risk (%NAV) from env
    if mode == "early":
        base_risk_pct = float(getattr(cfg, "RISK_EARLY", 0.25))
    else:
        base_risk_pct = float(getattr(cfg, "RISK_MAIN", 0.50))

    max_risk_pct = float(getattr(cfg, "RISK_MAX", 1.0))
    base_risk_pct = _clamp(base_risk_pct, 0.01, max_risk_pct)

    # RR
    rr_final = float(rr if rr is not None else float(getattr(cfg, "TP_RR", 2.0)))

    # SL ATR mult
    sl_mult = float(sl_atr_mult if sl_atr_mult is not None else float(getattr(cfg, "SL_ATR_MULT", 1.5)))
    sl_mult = _clamp(sl_mult, 0.6, 4.0)

    # risk in USD
    risk_pct = _clamp(base_risk_pct * float(risk_multiplier), 0.01, max_risk_pct)
    risk_usd = nav_usd * (risk_pct / 100.0)

    # distance to SL (price units)
    sl_dist = max(1e-12, atr_value * sl_mult)

    # SL / TP
    if direction == "LONG":
        sl = entry - sl_dist
        tp = entry + rr_final * sl_dist
    else:
        sl = entry + sl_dist
        tp = entry - rr_final * sl_dist

    # qty so that loss at SL ~= risk_usd
    per_unit_loss = abs(entry - sl)
    qty = risk_usd / per_unit_loss if per_unit_loss > 0 else 0.0

    # ---------------------------
    # Volatility-adjust sizing (optional)
    # If ATR% is high -> reduce qty; if low -> increase qty.
    # ---------------------------
    notes = ""
    if target_vol_pct is None:
        target_vol_pct = getattr(cfg, "TARGET_VOL_PCT", None)

    if target_vol_pct is not None:
        try:
            target_vol_pct = float(target_vol_pct)
            atr_pct = atr_value / entry if entry > 0 else 0.0
            if atr_pct > 0 and target_vol_pct > 0:
                vol_mult = target_vol_pct / atr_pct
                vol_mult = _clamp(vol_mult, 0.5, 1.5)
                qty *= vol_mult
                notes += f"vol_mult={vol_mult:.2f} "
        except Exception:
            pass

    return RiskPlan(
        symbol=symbol,
        direction=direction,
        entry=entry,
        sl=float(sl),
        tp=float(tp),
        qty=float(qty),
        risk_usd=float(risk_usd),
        risk_pct=float(risk_pct),
        rr=float(rr_final),
        notes=notes.strip(),
    )
