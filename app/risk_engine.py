from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RiskPlan:
    # sizing
    nav_usd: float
    risk_pct: float          # % NAV (base, before vol-adjust)
    risk_usd: float          # after vol-adjust sizing

    # prices (filled/assumed)
    entry: float             # effective entry (after slippage)
    sl: float
    tp: Optional[float]

    # distance
    atr_value: float
    atr_pct: float
    sl_dist: float
    rr: float

    # quantity (coin units)
    qty: float

    # slippage metadata
    slippage_pct: float
    position_notional_usd: float

    # misc
    note: str


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _infer_side(direction: str) -> str:
    d = (direction or "").upper().strip()
    return d if d in ("LONG", "SHORT") else "LONG"


def _getf(cfg, key: str, default: float) -> float:
    try:
        return float(getattr(cfg, key))
    except Exception:
        return float(default)


def _geti(cfg, key: str, default: int) -> int:
    try:
        return int(getattr(cfg, key))
    except Exception:
        return int(default)


def build_risk_plan(
    *,
    symbol: str,
    direction: str,
    entry: float,                 # signal price (close candle)
    atr_value: float,             # ATR in price units (same scale as entry)
    nav_usd: float,
    mode: str,
    cfg,
    spread_pct: float = 0.0,       # e.g. 0.0018 (0.18%)
    avg_volume_usd: float = 0.0,   # average traded USD volume (same timeframe as signal)
    rr: Optional[float] = None,
) -> RiskPlan:
    """
    Build a risk plan with:
    - ATR-based SL
    - Volatility-adjusted sizing (risk_usd shrinks when ATR% rises)
    - Slippage-adjusted entry estimate (spread + ATR + market impact vs liquidity)

    Required cfg fields (recommended):
      RISK_EARLY / RISK_MAIN (% NAV)
      RISK_MAX (% NAV cap)
      SL_ATR_MULT
      TP_RR
      TARGET_VOL_PCT (e.g. 0.015 means 1.5% ATR target)
      ENABLE_SLIPPAGE_MODEL (0/1)
      SLIP_K_ATR, SLIP_K_IMPACT (optional tuning)
    """

    # -------- Imports placed inside to avoid import issues at startup
    from app.volatility_sizing import volatility_adjusted_risk
    from app.slippage_model import estimate_slippage_pct

    side = _infer_side(direction)
    m = (mode or "early").lower().strip()

    if entry <= 0:
        raise ValueError("entry must be > 0")

    # -----------------------------
    # 1) Base risk pct by mode (% NAV)
    # -----------------------------
    if m == "early":
        base_risk_pct = _getf(cfg, "RISK_EARLY", 0.25)
    else:
        base_risk_pct = _getf(cfg, "RISK_MAIN", 0.50)

    risk_max = _getf(cfg, "RISK_MAX", 1.0)
    base_risk_pct = _clamp(base_risk_pct, 0.01, risk_max)

    # -----------------------------
    # 2) Volatility-adjusted risk USD
    #    risk_usd = nav * (base_risk_pct/100) * min(1, target_vol/atr_pct)
    # -----------------------------
    atr_pct = (atr_value / entry) if atr_value > 0 else 0.0
    target_vol_pct = _getf(cfg, "TARGET_VOL_PCT", 0.015)

    risk_usd = volatility_adjusted_risk(
        nav_usd=nav_usd,
        base_risk_pct=(base_risk_pct / 100.0),
        atr_pct=atr_pct,
        target_vol_pct=target_vol_pct,
    )

    # Safety clamp
    if risk_usd < 0:
        risk_usd = 0.0

    # -----------------------------
    # 3) SL distance from ATR
    # -----------------------------
    sl_mult = _getf(cfg, "SL_ATR_MULT", 1.5)
    sl_dist = max(atr_value * sl_mult, entry * 0.001)  # >= 0.1% fallback

    # SL level based on signal entry (pre-slippage)
    if side == "LONG":
        sl = max(0.0, entry - sl_dist)
    else:
        sl = entry + sl_dist

    # -----------------------------
    # 4) Slippage model → effective entry (fill estimate)
    # -----------------------------
    enable_slippage = _geti(cfg, "ENABLE_SLIPPAGE_MODEL", 1) == 1

    # First pass qty estimate without slippage (to compute notional impact)
    per_unit_risk0 = abs(entry - sl)
    qty0 = (risk_usd / per_unit_risk0) if per_unit_risk0 > 0 else 0.0
    position_notional0 = qty0 * entry

    slippage_pct = 0.0
    eff_entry = entry

    if enable_slippage:
        slippage_pct = estimate_slippage_pct(
            spread_pct=float(spread_pct),
            atr_pct=float(atr_pct),
            position_notional_usd=float(position_notional0),
            avg_volume_usd=float(avg_volume_usd),
        )
        slippage_pct = max(0.0, slippage_pct)

        if side == "LONG":
            eff_entry = entry * (1.0 + slippage_pct)
        else:
            eff_entry = entry * (1.0 - slippage_pct)

    # -----------------------------
    # 5) Quantity recompute using eff_entry vs SL
    #    (keeps risk_usd stable after slippage)
    # -----------------------------
    per_unit_risk = abs(eff_entry - sl)
    qty = (risk_usd / per_unit_risk) if per_unit_risk > 0 else 0.0
    position_notional = qty * eff_entry

    # -----------------------------
    # 6) TP by RR (optional)
    # -----------------------------
    rr_val = float(rr if rr is not None else _getf(cfg, "TP_RR", 2.0))
    if rr_val <= 0:
        tp = None
    else:
        if side == "LONG":
            tp = eff_entry + rr_val * (eff_entry - sl)
        else:
            tp = eff_entry - rr_val * (sl - eff_entry)

    note = (
        f"{symbol} {side} | mode={m} | base_risk={base_risk_pct:.2f}% "
        f"| risk_usd={risk_usd:.2f} | atr={atr_value:.6f} ({atr_pct*100:.2f}%) "
        f"| sl_mult={sl_mult} rr={rr_val} | slip={slippage_pct*100:.2f}%"
    )

    return RiskPlan(
        nav_usd=nav_usd,
        risk_pct=base_risk_pct,
        risk_usd=risk_usd,
        entry=eff_entry,
        sl=sl,
        tp=tp,
        atr_value=atr_value,
        atr_pct=atr_pct,
        sl_dist=sl_dist,
        rr=rr_val,
        qty=qty,
        slippage_pct=slippage_pct,
        position_notional_usd=position_notional,
        note=note,
    )