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
    def __init__(self, nav_usd: float, rr: float = 2.0):
        self.nav = float(nav_usd)
        self.base_rr = float(rr)
        self.positions: Dict[str, SimPosition] = {}

        # ==== STAT TRACKING ====
        self.total_trades = 0
        self.win_trades = 0
        self.loss_trades = 0
        self.total_pnl = 0.0

    def has_pos(self, symbol: str) -> bool:
        return symbol in self.positions

    def open(self, pos: SimPosition) -> None:
        self.positions[pos.symbol] = pos

    def close(self, symbol: str) -> Optional[SimPosition]:
        if symbol in self.positions:
            return self.positions.pop(symbol)
        return None

    def update_by_candle(self, symbol: str, candle: dict) -> Optional[dict]:
        pos = self.positions.get(symbol)
        if not pos:
            return None

        high = float(candle["high"])
        low = float(candle["low"])

        result = None
        exit_price = None
        pnl = 0.0

        # LONG
        if pos.direction == "LONG":
            if low <= pos.sl:
                pnl = -pos.risk_usd
                exit_price = pos.sl
                result = "SL"
            elif high >= pos.tp:
                pnl = pos.risk_usd * self.base_rr
                exit_price = pos.tp
                result = "TP"

        # SHORT
        else:
            if high >= pos.sl:
                pnl = -pos.risk_usd
                exit_price = pos.sl
                result = "SL"
            elif low <= pos.tp:
                pnl = pos.risk_usd * self.base_rr
                exit_price = pos.tp
                result = "TP"

        if result:
            self.nav += pnl
            self.total_trades += 1
            self.total_pnl += pnl

            if pnl > 0:
                self.win_trades += 1
            else:
                self.loss_trades += 1

            self.close(symbol)

            return {
                "result": result,
                "exit": exit_price,
                "pnl": pnl,
            }

        return None

    # ===============================
    # Performance summary
    # ===============================
    def summary(self) -> dict:
        winrate = (
            (self.win_trades / self.total_trades) * 100
            if self.total_trades > 0
            else 0.0
        )

        return {
            "total": self.total_trades,
            "wins": self.win_trades,
            "losses": self.loss_trades,
            "winrate": winrate,
            "pnl": self.total_pnl,
            "nav": self.nav,
        }
