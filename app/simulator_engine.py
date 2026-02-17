from __future__ import annotations

import time
from typing import Dict
from app.telegram import send_telegram


class Simulator:

    def __init__(self, initial_nav: float):
        self.nav = initial_nav
        self.positions: Dict[str, dict] = {}

    # ===============================
    # OPEN POSITION
    # ===============================
    async def open_position(
        self,
        symbol: str,
        direction: str,
        qty: float,
        entry: float,
        sl: float,
        tp: float,
    ):
        if symbol in self.positions:
            return

        self.positions[symbol] = {
            "symbol": symbol,
            "direction": direction,
            "qty": qty,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "opened_at": time.time(),
        }

        await send_telegram(
            f"🟢 OPEN {direction} {symbol}\n"
            f"Entry: {entry:.6f}\n"
            f"Qty: {qty:.4f}\n"
            f"SL: {sl:.6f}\n"
            f"TP: {tp:.6f}\n"
            f"NAV: {self.nav:.2f} USDT"
        )

    # ===============================
    # UPDATE PRICE
    # ===============================
    async def update_price(self, symbol: str, price: float):

        if symbol not in self.positions:
            return

        p = self.positions[symbol]

        hit_sl = (
            price <= p["sl"] if p["direction"] == "LONG"
            else price >= p["sl"]
        )

        hit_tp = (
            price >= p["tp"] if p["direction"] == "LONG"
            else price <= p["tp"]
        )

        if hit_sl or hit_tp:
            await self.close_position(symbol, price)

    # ===============================
    # CLOSE POSITION
    # ===============================
    async def close_position(self, symbol: str, exit_price: float):

        p = self.positions[symbol]

        pnl = (
            (exit_price - p["entry"]) * p["qty"]
            if p["direction"] == "LONG"
            else (p["entry"] - exit_price) * p["qty"]
        )

        self.nav += pnl

        await send_telegram(
            f"🔴 CLOSE {p['direction']} {symbol}\n"
            f"Exit: {exit_price:.6f}\n"
            f"PnL: {pnl:.2f} USDT\n"
            f"NAV: {self.nav:.2f} USDT"
        )

        del self.positions[symbol]