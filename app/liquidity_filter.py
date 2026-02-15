from __future__ import annotations


def liquidity_ok(
    avg_volume_usd: float,
    min_required_usd: float,
) -> bool:
    """
    Check if average traded USD volume is sufficient.
    """

    if avg_volume_usd is None:
        return False

    return avg_volume_usd >= min_required_usd