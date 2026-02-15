from __future__ import annotations


def volatility_adjusted_risk(
    nav_usd: float,
    base_risk_pct: float,
    atr_pct: float,
    target_vol_pct: float,
) -> float:
    """
    Adjust risk allocation based on volatility.

    If ATR > target → reduce risk
    If ATR < target → keep base risk
    """

    if atr_pct <= 0:
        return nav_usd * base_risk_pct

    vol_factor = min(1.0, target_vol_pct / atr_pct)

    risk_usd = nav_usd * base_risk_pct * vol_factor

    return risk_usd