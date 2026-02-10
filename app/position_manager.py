# app/position_manager.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Position:
    symbol: str
    direction: str
    qty: float
    entry: float
    sl: float
    tp: Optional[float]
    opened_at: float
    risk_usd: float


class PositionManager:
    def __init__(self, max_positions: int = 10, max_total_risk_usd: float = 300.0):
        self.max_positions = max_positions
        self.max_total_risk_usd = max_total_risk_usd
        self.positions: Dict[str, Position] = {}

    def total_risk(self) -> float:
        return sum(p.risk_usd for p in self.positions.values())

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def can_open(self, symbol: str, risk_usd: float) -> tuple[bool, str]:
        if self.has_position(symbol):
            return False, "position_exists"

        if len(self.positions) >= self.max_positions:
            return False, "max_positions_reached"

        if self.total_risk() + risk_usd > self.max_total_risk_usd:
            return False, "max_total_risk_reached"

        return True, "ok"

    def open(self, pos: Position) -> None:
        self.positions[pos.symbol] = pos

    def close(self, symbol: str) -> None:
        if symbol in self.positions:
            del self.positions[symbol]

    def snapshot(self) -> Dict[str, dict]:
        return {
            s: {
                "direction": p.direction,
                "qty": p.qty,
                "entry": p.entry,
                "sl": p.sl,
                "tp": p.tp,
                "opened_at": p.opened_at,
                "risk_usd": p.risk_usd,
            }
            for s, p in self.positions.items()
        }
