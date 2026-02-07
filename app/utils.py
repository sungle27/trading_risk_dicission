from __future__ import annotations

import random


def backoff_s(n: int) -> float:
    base = min(60, 2**max(0, n))
    return base + random.random()
