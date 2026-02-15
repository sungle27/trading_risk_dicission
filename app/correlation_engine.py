from __future__ import annotations

import numpy as np
from typing import List


def returns_from_prices(prices: List[float]) -> np.ndarray:
    if len(prices) < 2:
        return np.array([])

    arr = np.array(prices)
    return np.diff(arr) / arr[:-1]


def correlation(
    prices_a: List[float],
    prices_b: List[float],
) -> float:
    """
    Compute Pearson correlation between two return series.
    """

    ret_a = returns_from_prices(prices_a)
    ret_b = returns_from_prices(prices_b)

    if len(ret_a) < 5 or len(ret_b) < 5:
        return 0.0

    min_len = min(len(ret_a), len(ret_b))

    ret_a = ret_a[-min_len:]
    ret_b = ret_b[-min_len:]

    return float(np.corrcoef(ret_a, ret_b)[0, 1])