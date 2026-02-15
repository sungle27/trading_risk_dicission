from __future__ import annotations


def estimate_slippage_pct(
    spread_pct: float,
    atr_pct: float,
    position_notional_usd: float,
    avg_volume_usd: float,
) -> float:
    """
    Estimate slippage as percentage of price.

    Components:
    - spread cost
    - volatility impact (ATR)
    - market impact (size vs liquidity)
    """

    if avg_volume_usd <= 0:
        return spread_pct

    # Weight factors (tunable)
    k_atr = 0.4
    k_impact = 0.3

    impact_ratio = position_notional_usd / avg_volume_usd

    slippage_pct = (
        spread_pct
        + k_atr * atr_pct
        + k_impact * impact_ratio
    )

    return max(slippage_pct, spread_pct)