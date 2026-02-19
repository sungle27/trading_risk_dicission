from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ============================================================
# Risk Plan
# ============================================================
@dataclass(frozen=True)
class RiskPlan:
    symbol: str
    direction: str          # "LONG" | "SHORT"
    entry: float            # effective entry (after confirmation+slippage)
    sl: float
    tp: Optional[float]
    qty: float

    rr: float               # take-profit RR used
    risk_usd: float
    risk_pct: float

    sl_atr_mult: float
    atr_value: float
    atr_pct: float

    notes: str = ""

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _slippage_bps_for(mode: str, cfg) -> float:
    if mode == "early":
        return float(getattr(cfg, "ENTRY_SLIPPAGE_BPS_EARLY", 4.0))
    return float(getattr(cfg, "ENTRY_SLIPPAGE_BPS_MAIN", 2.0))

def _sl_atr_mult_for(mode: str, cfg) -> float:
    if mode == "early":
        return float(getattr(cfg, "SL_ATR_MULT_EARLY", 0.9))
    return float(getattr(cfg, "SL_ATR_MULT_MAIN", 1.0))

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
    risk_pct_mult: float = 1.0,
    sl_atr_mult: Optional[float] = None,
    entry_slippage_bps: Optional[float] = None,
) -> RiskPlan:
    """
    Core sizing:
    - Base risk per trade (%NAV) with optional volatility adjustment
    - SL distance = ATR * SL_ATR_MULT
    - Qty = risk_usd / SL_distance
    - TP uses RR: TP_distance = SL_distance * RR
    """

    entry = float(entry)
    atr_value = float(atr_value)
    nav_usd = float(nav_usd)

    if entry <= 0 or atr_value <= 0 or nav_usd <= 0:
        raise ValueError("Invalid entry/atr/nav")

    # Base risk (% NAV)
    base_risk_pct = float(getattr(cfg, "RISK_PER_TRADE_PCT", 0.25))
    risk_pct = base_risk_pct * float(risk_pct_mult)

    # Volatility-adjust sizing (optional)
    if int(getattr(cfg, "ENABLE_VOL_ADJ_SIZING", 1)) == 1:
        atr_pct = atr_value / entry
        target = float(getattr(cfg, "TARGET_VOL_PCT", 0.010))
        scale = target / max(1e-9, atr_pct)
        scale = _clamp(scale, 0.5, 1.5)
        risk_pct *= scale
    else:
        atr_pct = atr_value / entry

    # Clamp risk % to sane band
    risk_pct = _clamp(risk_pct, 0.05, 2.0)

    # SL distance
    if sl_atr_mult is None:
        sl_atr_mult = _sl_atr_mult_for(mode, cfg)
    sl_atr_mult = float(sl_atr_mult)

    sl_dist = atr_value * sl_atr_mult
    sl_dist = max(sl_dist, entry * 0.0002)  # prevent ultra-tight stops on micro ATR

    # Risk USD
    risk_usd = nav_usd * (risk_pct / 100.0)

    # Qty
    qty = risk_usd / sl_dist
    qty = max(qty, 0.0)

    # TP
    rr = float(rr)
    rr = _clamp(rr, 1.2, 3.0)
    tp_dist = sl_dist * rr

    if direction.upper() == "LONG":
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:
        sl = entry + sl_dist
        tp = entry - tp_dist

    # Slippage on entry (bps)
    if entry_slippage_bps is None:
        entry_slippage_bps = _slippage_bps_for(mode, cfg)
    bps = float(entry_slippage_bps)

    if direction.upper() == "LONG":
        eff_entry = entry * (1.0 + bps / 10000.0)
    else:
        eff_entry = entry * (1.0 - bps / 10000.0)

    # Shift SL/TP by the same entry delta (keep distances)
    delta = eff_entry - entry
    sl += delta
    tp = tp + delta if tp is not None else None

    return RiskPlan(
        symbol=symbol,
        direction=direction.upper(),
        entry=float(eff_entry),
        sl=float(sl),
        tp=float(tp) if tp is not None else None,
        qty=float(qty),
        rr=float(rr),
        risk_usd=float(risk_usd),
        risk_pct=float(risk_pct),
        sl_atr_mult=float(sl_atr_mult),
        atr_value=float(atr_value),
        atr_pct=float(atr_pct),
        notes="vol_adj=on" if int(getattr(cfg, "ENABLE_VOL_ADJ_SIZING", 1)) == 1 else "vol_adj=off",
    )
