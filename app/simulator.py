from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class SimPosition:
    symbol: str
    direction: str
    qty: float
    entry: float
    sl: float
    tp: float
    risk_usd: float


class ExecutionSimulator:

    def __init__(self, nav_usd: float):
        self.nav = nav_usd
        self.positions: Dict[str, SimPosition] = {}

    # --------------------------------------------------
    # OPEN POSITION
    # --------------------------------------------------
    def open_position(self, pos: SimPosition):
        self.positions[pos.symbol] = pos

    # --------------------------------------------------
    # UPDATE POSITIONS (called every candle close)
    # --------------------------------------------------
    def update(self, symbol: str, candle: dict):
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]

        high = candle["high"]
        low = candle["low"]

        # LONG
        if pos.direction == "LONG":

            if low <= pos.sl:
                self.nav -= pos.risk_usd
                del self.positions[symbol]
                return "SL"

            if high >= pos.tp:
                self.nav += pos.risk_usd * 2
                del self.positions[symbol]
                return "TP"

        # SHORT
        else:

            if high >= pos.sl:
                self.nav -= pos.risk_usd
                del self.positions[symbol]
                return "SL"

            if low <= pos.tp:
                self.nav += pos.risk_usd * 2
                del self.positions[symbol]
                return "TP"

        return None