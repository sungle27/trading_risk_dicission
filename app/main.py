from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp

from app.config import CFG
from app.telegram import send_telegram
from app.resample import TimeframeResampler
from app.alert_engine import check_signal
from app.symbols import FALLBACK_SYMBOLS
from app.utils import backoff_s

from app.market_regime import MarketRegimeEngine
from app.risk_engine import build_risk_plan
from app.position_manager import PositionManager
from app.indicators import ATR


# ============================================================
# Market Regime (global)
# ============================================================
MRE = MarketRegimeEngine()
MARKET_REGIME = "NORMAL"
MARKET_PANIC = False
LAST_REGIME: Optional[str] = None


# ============================================================
# SIM SETTINGS
# ============================================================
SIM_ENABLED = bool(int(getattr(CFG, "SIM_ENABLED", 1)))
SIM_START_NAV = float(getattr(CFG, "SIM_START_NAV", 10000.0))
SIM_RR = float(getattr(CFG, "SIM_RR", 2.0))

# If True: bot will only open trades in MAIN timeframe (15m)
SIM_TRADE_ON_MAIN_ONLY = True


# ============================================================
# SIM EXECUTION (Paper trading)
# ============================================================
@dataclass
class SimPosition:
    symbol: str
    direction: str  # "LONG" | "SHORT"
    qty: float
    entry: float
    sl: float
    tp: float
    risk_usd: float
    opened_at: float


class ExecutionSimulator:
    def __init__(self, nav_usd: float, rr: float = 2.0):
        self.nav = float(nav_usd)
        self.rr = float(rr)
        self.positions: Dict[str, SimPosition] = {}

    def has_pos(self, symbol: str) -> bool:
        return symbol in self.positions

    def open(self, pos: SimPosition) -> None:
        self.positions[pos.symbol] = pos

    def close(self, symbol: str) -> Optional[SimPosition]:
        if symbol in self.positions:
            return self.positions.pop(symbol)
        return None

    def update_by_candle(self, symbol: str, candle: dict) -> Optional[dict]:
        """
        Check SL/TP using candle high/low.
        Return dict: { "result": "SL"/"TP", "exit": float, "pnl": float } if closed, else None.
        """
        pos = self.positions.get(symbol)
        if not pos:
            return None

        high = float(candle["high"])
        low = float(candle["low"])

        # LONG
        if pos.direction == "LONG":
            if low <= pos.sl:
                pnl = -pos.risk_usd
                self.nav += pnl
                self.close(symbol)
                return {"result": "SL", "exit": pos.sl, "pnl": pnl}
            if high >= pos.tp:
                pnl = pos.risk_usd * self.rr
                self.nav += pnl
                self.close(symbol)
                return {"result": "TP", "exit": pos.tp, "pnl": pnl}

        # SHORT
        else:
            if high >= pos.sl:
                pnl = -pos.risk_usd
                self.nav += pnl
                self.close(symbol)
                return {"result": "SL", "exit": pos.sl, "pnl": pnl}
            if low <= pos.tp:
                pnl = pos.risk_usd * self.rr
                self.nav += pnl
                self.close(symbol)
                return {"result": "TP", "exit": pos.tp, "pnl": pnl}

        return None


# ============================================================
# GLOBAL: Position Manager (risk & correlation gates)
# ============================================================
pos_mgr = PositionManager(
    nav_usd=float(getattr(CFG, "NAV_USD", SIM_START_NAV)),
    max_positions=int(getattr(CFG, "MAX_POSITIONS", 10)),
    max_total_risk_pct=getattr(CFG, "MAX_TOTAL_RISK_PCT", None),
    max_correlation=getattr(CFG, "MAX_CORRELATION", None),
    cfg=CFG,
)

sim = ExecutionSimulator(nav_usd=SIM_START_NAV, rr=SIM_RR)
pos_mgr.update_nav(sim.nav)


# ============================================================
# SYMBOL STATE
# ============================================================
class SymbolState:
    def __init__(self):
        self.bid = None
        self.ask = None
        self.cur_sec = None

        self.vol_bucket = 0.0

        # decision TFs
        self.r5m = TimeframeResampler(5 * 60)     # EARLY
        self.r15m = TimeframeResampler(15 * 60)   # MAIN

        # history
        self.candles_5m: List[dict] = []
        self.volumes_5m: List[float] = []

        self.candles_15m: List[dict] = []
        self.volumes_15m: List[float] = []

        # cooldown
        self.last_early = 0
        self.last_main = 0

    def mid(self):
        if self.bid is None or self.ask is None:
            return None
        return (float(self.bid) + float(self.ask)) / 2.0

    def spread(self):
        m = self.mid()
        if not m:
            return 0.0
        return (float(self.ask) - float(self.bid)) / float(m)


# ============================================================
# Proxy (BTC / ETH) for regime
# ============================================================
class ProxyState:
    def __init__(self):
        self.r1h = TimeframeResampler(60 * 60)
        self.r4h = TimeframeResampler(4 * 60 * 60)
        self.candles_1h: List[dict] = []
        self.candles_4h: List[dict] = []


# ============================================================
# WS: BOOK TICKER
# ============================================================
async def ws_bookticker(states: Dict[str, SymbolState], url: str):
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(url, heartbeat=30) as ws:
                    async for msg in ws:
                        data = json.loads(msg.data).get("data", {})
                        sym = data.get("s")
                        if sym in states:
                            states[sym].bid = float(data["b"])
                            states[sym].ask = float(data["a"])
        except Exception:
            await asyncio.sleep(5)


# ============================================================
# REGIME NOTIFY (optional)
# ============================================================
async def notify_regime_change(new_regime: str, reason: str):
    # Bạn có thể tắt hẳn notify regime bằng cách return ngay
    if not bool(int(getattr(CFG, "REGIME_NOTIFY", 1))):
        return

    await send_telegram(
        "📡 MARKET REGIME CHANGED\n"
        f"→ {new_regime}\n"
        f"Reason: {reason}"
    )


# ============================================================
# ATR helper
# ============================================================
def compute_atr(candles: List[dict], period: int) -> Optional[float]:
    if len(candles) < period + 2:
        return None

    atr = ATR(period)
    atr_val = None
    for c in candles:
        atr_val = atr.update(float(c["high"]), float(c["low"]), float(c["close"]))
    return atr_val


# ============================================================
# NAV MONITOR (hourly)
# ============================================================
async def nav_monitor():
    while True:
        await asyncio.sleep(60 * 60)  # 1 hour

        total_risk = pos_mgr.total_risk_usd() if hasattr(pos_mgr, "total_risk_usd") else 0.0
        open_positions = len(sim.positions) if SIM_ENABLED else 0

        await send_telegram(
            f"📊 SIM STATUS (Hourly)\n"
            f"NAV: {sim.nav:.2f} USDT\n"
            f"Open positions: {open_positions}\n"
            f"Total risk: {total_risk:.2f} USDT\n"
            f"Regime: {MARKET_REGIME}"
        )

# ============================================================
# WS: AGG TRADE (engine)
# ============================================================
async def ws_aggtrade(states: Dict[str, SymbolState], url: str):
    await send_telegram(
        "✅ SIM TRADING BOT RUNNING\n"
        f"symbols={len(states)} | MAIN=15m\n"
        f"SIM={'ON' if SIM_ENABLED else 'OFF'} | NAV={sim.nav:.2f} | RR={SIM_RR}"
    )

    # Ensure proxies exist
    if "BTCUSDT" not in states or "ETHUSDT" not in states:
        raise RuntimeError(
            "Regime proxies must be included in FALLBACK_SYMBOLS (BTCUSDT/ETHUSDT)"
        )

    proxy_states = {s: ProxyState() for s in ("BTCUSDT", "ETHUSDT")}
    global MARKET_REGIME, MARKET_PANIC, LAST_REGIME

    while True:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(url, heartbeat=30) as ws:
                    async for msg in ws:
                        data = json.loads(msg.data).get("data", {})
                        sym = data.get("s")
                        if sym not in states:
                            continue

                        st = states[sym]
                        sec = data["T"] // 1000
                        qty = float(data["q"])

                        if st.cur_sec is None:
                            st.cur_sec = sec

                        while sec > st.cur_sec:
                            mid = st.mid()
                            if mid:

                                # ================= REGIME UPDATE (BTC/ETH only) =================
                                if sym in proxy_states:
                                    ps = proxy_states[sym]

                                    c1, d1 = ps.r1h.update(st.cur_sec, mid, 0.0)
                                    if d1 and c1:
                                        ps.candles_1h.append({
                                            "open": c1.open, "high": c1.high, "low": c1.low, "close": c1.close
                                        })
                                        ps.candles_1h = ps.candles_1h[-300:]

                                    c4, d4 = ps.r4h.update(st.cur_sec, mid, 0.0)
                                    if d4 and c4:
                                        ps.candles_4h.append({
                                            "open": c4.open, "high": c4.high, "low": c4.low, "close": c4.close
                                        })
                                        ps.candles_4h = ps.candles_4h[-300:]

                                    if d1 and c1:
                                        rr = MRE.update(
                                            {k: v.candles_1h for k, v in proxy_states.items()},
                                            {k: v.candles_4h for k, v in proxy_states.items()},
                                        )
                                        MARKET_REGIME = rr.regime
                                        MARKET_PANIC = rr.panic

                                        if rr.regime != LAST_REGIME:
                                            await notify_regime_change(rr.regime, rr.reason)
                                            LAST_REGIME = rr.regime

                                # ================= MAIN (15m) =================
                                closed15, did15 = st.r15m.update(st.cur_sec, mid, st.vol_bucket)
                                if did15 and closed15:
                                    candle15 = {
                                        "open": closed15.open,
                                        "high": closed15.high,
                                        "low": closed15.low,
                                        "close": closed15.close,
                                    }
                                    st.candles_15m.append(candle15)
                                    st.volumes_15m.append(closed15.volume)
                                    st.candles_15m = st.candles_15m[-300:]
                                    st.volumes_15m = st.volumes_15m[-300:]

                                    # ---- SIM: update existing position first ----
                                    if SIM_ENABLED:
                                        close_info = sim.update_by_candle(sym, candle15)
                                        if close_info:
                                            pos_mgr.close(sym)
                                            pos_mgr.update_nav(sim.nav)

                                            await send_telegram(
                                                f"🔴 CLOSE {sym}\n"
                                                f"Exit: {close_info['exit']:.6f}\n"
                                                f"Result: {close_info['result']}\n"
                                                f"PnL: {close_info['pnl']:.2f} USDT\n"
                                                f"NAV: {sim.nav:.2f} USDT"
                                            )

                                    # ---- Decide open new trade only on 15m close ----
                                    now = int(time.time())
                                    if now - st.last_main >= CFG.COOLDOWN_SEC_MAIN:
                                        sig = check_signal(
                                            sym,
                                            st.candles_15m,
                                            st.volumes_15m,
                                            st.spread(),
                                            mode="main",
                                            market_regime=MARKET_REGIME,
                                            market_panic=MARKET_PANIC,
                                        )
                                        if sig:
                                            st.last_main = now

                                            # Policy: During panic, block LONG
                                            if MARKET_PANIC and sig["direction"] == "LONG":
                                                continue

                                            if SIM_ENABLED and sim.has_pos(sym):
                                                continue  # already in position

                                            atr_val = compute_atr(st.candles_15m, CFG.ATR_SHORT)
                                            if atr_val is None:
                                                continue

                                            rp = build_risk_plan(
                                                symbol=sym,
                                                direction=sig["direction"],
                                                entry=float(closed15.close),
                                                atr_value=float(atr_val),
                                                nav_usd=float(sim.nav),
                                                mode="main",
                                                cfg=CFG,
                                            )

                                            ok, reason = pos_mgr.can_open(
                                                symbol=sym,
                                                risk_usd=float(rp.risk_usd),
                                                new_prices=[c["close"] for c in st.candles_15m[-60:]],
                                            )
                                            if not ok:
                                                continue

                                            # ---- SIM open ----
                                            if SIM_ENABLED:
                                                print(f"DEBUG: OPEN TRIGGERED {sym}")
                                                sim.open(
                                                    SimPosition(
                                                        symbol=sym,
                                                        direction=sig["direction"],
                                                        qty=float(rp.qty),
                                                        entry=float(rp.entry),
                                                        sl=float(rp.sl),
                                                        tp=float(rp.tp),
                                                        risk_usd=float(rp.risk_usd),
                                                        opened_at=time.time(),
                                                    )
                                                )
                                                print(f"OPENED {sym} at {rp.entry}")
                                                await send_telegram("DEBUG: OPEN CALLED")
                                                
                                                pos_mgr.open(
                                                    symbol=sym,
                                                    direction=sig["direction"],
                                                    qty=float(rp.qty),
                                                    entry=float(rp.entry),
                                                    sl=float(rp.sl),
                                                    tp=float(rp.tp),
                                                    risk_usd=float(rp.risk_usd),
                                                    price_history=[c["close"] for c in st.candles_15m[-60:]],
                                                )
                                                pos_mgr.update_nav(sim.nav)

                                                await send_telegram(
                                                    f"🟢 OPEN {sig['direction']} {sym}\n"
                                                    f"Entry: {rp.entry:.6f}\n"
                                                    f"Qty: {rp.qty:.4f}\n"
                                                    f"SL: {rp.sl:.6f}\n"
                                                    f"TP: {rp.tp:.6f}\n"
                                                    f"NAV: {sim.nav:.2f} USDT"
                                                )

                                st.vol_bucket = 0.0

                            st.cur_sec += 1

                        st.vol_bucket += qty

        except Exception as e:
            print("aggtrade error:", e)
            await asyncio.sleep(backoff_s(1))


# ============================================================
# MAIN
# ============================================================
async def main():
    states = {s: SymbolState() for s in FALLBACK_SYMBOLS}

    ws_base = CFG.BINANCE_FUTURES_WS
    url_book = ws_base + "?streams=" + "/".join(f"{s.lower()}@bookTicker" for s in states)
    url_trade = ws_base + "?streams=" + "/".join(f"{s.lower()}@aggTrade" for s in states)

    await asyncio.gather(
        ws_bookticker(states, url_book),
        ws_aggtrade(states, url_trade),
        nav_monitor(),  # 👈 thêm dòng này
    )


if __name__ == "__main__":
    asyncio.run(main())