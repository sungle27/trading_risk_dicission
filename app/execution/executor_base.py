from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    direction: str      # LONG/SHORT
    qty: float
    entry: float
    sl: float
    tp: Optional[float]
    mode: str           # early/main
    reason: str         # optional note


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    order_id: Optional[str]
    message: str


class ExecutorBase(Protocol):
    async def place_order(self, intent: OrderIntent) -> OrderResult: ...
