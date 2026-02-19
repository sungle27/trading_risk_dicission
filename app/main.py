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
# Telegram queue (serialize sends to avoid rate-limit / lost awaits)
# ============================================================
_TG_Q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)

async def tg_worker():
    while True:
        msg = await _TG_Q.get()
        try:
            await send_telegram(msg)
        except Exception as e:
            print("[TELEGRAM ERROR]", e)
        finally:
            _TG_Q.task_done()
        await asyncio.sleep(0.2)

async def tg_send(msg: str) -> None:
    try:
        _TG_Q.put_nowait(msg)
    except asyncio.QueueFull:
        print("[TELEGRAM] queue full, dropped message")


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
    rr: float
    opened_at: float


class ExecutionSimulator:
    def __init__(self, nav_usd: float):
        self.nav = float(nav_usd)
        self.positions: Dict[str, SimPosition] = {}

        # performance stats
        self.trades_total = 0
        self.wins = 0
        self.losses = 0
        self.closed_total = 0
        self.pnl_total = 0.0

    def has_pos(self, symbol: str) -> bool:
        return symbol in self.positions

    def open(self, pos: SimPosition) -> None:
        self.positions[pos.symbol] = pos
        self.trades_total += 1

    def close(self, symbol: str) -> Optional[SimPosition]:
        return self.positions.pop(symbol, None)

    def update_by_candle(self, symbol: str, candle: dict) -> Optional[dict]:
        """
        Check SL/TP using candle high/low.
        Return dict: { "result": "SL"/"TP", "exit": float, "pnl": float, "rr": float } if closed, else None.
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
                self.pnl_total += pnl
                self.close(symbol)
                self.losses += 1
                self.closed_total += 1
                return {"result": "SL", "exit": pos.sl, "pnl": pnl, "rr": pos.rr}
            if high >= pos.tp:
                pnl = pos.risk_usd * pos.rr
                self.nav += pnl
                self.pnl_total += pnl
                self.close(symbol)
                self.wins += 1
                self.closed_total += 1
                return {"result": "TP", "exit": pos.tp, "pnl": pnl, "rr": pos.rr}

        # SHORT
        else:
            if high >= pos.sl:
                pnl = -pos.risk_usd
                self.nav += pnl
                self.pnl_total += pnl
                self.close(symbol)
                self.losses += 1
                self.closed_total += 1
                return {"result": "SL", "exit": pos.sl, "pnl": pnl, "rr": pos.rr}
            if low <= pos.tp:
                pnl = pos.risk_usd * pos.rr
                self.nav += pnl
                self.pnl_total += pnl
                self.close(symbol)
                self.wins += 1
                self.closed_total += 1
                return {"result": "TP", "exit": pos.tp, "pnl": pnl, "rr": pos.rr}

        return None

    def winrate(self) -> float:
        return (self.wins / self.closed_total * 100.0) if self.closed_total > 0 else 0.0


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

sim = ExecutionSimulator(nav_usd=SIM_START_NAV)
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

        # MAIN timeframe (CFG.MAIN_TF_SEC, default 15m)
        self.r_main = TimeframeResampler(int(getattr(CFG, "MAIN_TF_SEC", 15 * 60)))

        # history
        self.candles: List[dict] = []
        self.volumes: List[float] = []

        # cooldown
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
        except Exception as e:
            print("bookticker error:", e)
            await asyncio.sleep(5)


# ============================================================
# REGIME NOTIFY (optional)
# ============================================================
async def notify_regime_change(new_regime: str, reason: str):
    if not bool(int(getattr(CFG, "REGIME_NOTIFY", 1))):
        return
    await tg_send(
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
# Entry confirmation (wait a % move after signal)
# ============================================================
def compute_entry_confirm_pct(entry: float, atr_value: float) -> float:
    atr_pct = atr_value / max(entry, 1e-9)
    raw = 0.10 * atr_pct
    lo = float(getattr(CFG, "ENTRY_CONFIRM_MIN_PCT", 0.0003))
    hi = float(getattr(CFG, "ENTRY_CONFIRM_MAX_PCT", 0.0015))
    return max(lo, min(hi, raw))

def apply_entry_confirm(entry: float, direction: str, confirm_pct: float) -> float:
    if direction.upper() == "LONG":
        return entry * (1.0 + confirm_pct)
    return entry * (1.0 - confirm_pct)


# ============================================================
# Adaptive RR + SL multiplier (TP/SL adjustment)
# ============================================================
def adaptive_rr_and_sl(sig: dict, market_regime: str, market_panic: bool, candle: dict, atr_value: float) -> tuple[float, float, float]:
    """
    Returns: (rr, sl_atr_mult, risk_pct_mult)
    """
    rr = float(getattr(CFG, "SIM_RR", 2.0))
    sl_mult = float(getattr(CFG, "SL_ATR_MULT_MAIN", 1.0))
    risk_mult = 1.0

    o = float(candle["open"])
    c = float(candle["close"])
    body_pct = abs(c - o) / max(o, 1e-9)
    vol_ratio = float(sig.get("volume_ratio", 1.0))

    if sig.get("high_conf"):
        rr = max(rr, 2.4)
        risk_mult *= 1.20
        sl_mult *= 1.05

    if market_regime == "TREND":
        rr = max(rr, 2.2)
        risk_mult *= 1.10
        sl_mult *= 1.05

    if market_regime == "RANGE":
        rr = min(rr, 1.8)
        risk_mult *= 0.70
        sl_mult *= 0.95

    if market_panic:
        rr = min(rr, 1.7)
        risk_mult *= 0.60
        sl_mult *= 1.15

    if vol_ratio >= 4.0:
        rr = min(3.0, rr + 0.2)
        risk_mult *= 1.05

    if body_pct >= 0.0075:
        rr = max(1.5, rr - 0.2)
        sl_mult *= 1.10
        risk_mult *= 0.90

    atr_pct = atr_value / max(c, 1e-9)
    if atr_pct >= 0.02:
        rr = max(1.5, rr - 0.2)
        sl_mult *= 1.10
        risk_mult *= 0.85

    rr = max(1.2, min(3.0, rr))
    sl_mult = max(0.6, min(1.8, sl_mult))
    risk_mult = max(0.4, min(1.6, risk_mult))
    return rr, sl_mult, risk_mult


# ============================================================
# NAV MONITOR (periodic)
# ============================================================
async def nav_monitor():
    interval_sec = int(getattr(CFG, "NAV_REPORT_SEC", 60 * 60))
    while True:
        await asyncio.sleep(interval_sec)

        total_risk = pos_mgr.total_risk_usd()
        open_positions = len(sim.positions)

        await tg_send(
            "📊 SIM STATUS\n"
            f"NAV: {sim.nav:.2f} USDT\n"
            f"Open positions: {open_positions}\n"
            f"Total risk: {total_risk:.2f} USDT\n"
            f"Trades: {sim.trades_total} | Closed: {sim.closed_total}\n"
            f"Wins: {sim.wins} | Losses: {sim.losses} | Winrate: {sim.winrate():.1f}%\n"
            f"PnL total: {sim.pnl_total:.2f} USDT\n"
            f"Regime: {MARKET_REGIME} | Panic: {MARKET_PANIC}"
        )


# ============================================================
# WS: AGG TRADE (engine)
# ============================================================
async def ws_aggtrade(states: Dict[str, SymbolState], url: str):
    tf_sec = int(getattr(CFG, "MAIN_TF_SEC", 15 * 60))
    await tg_send(
        "✅ SIM TRADING BOT RUNNING\n"
        f"symbols={len(states)} | MAIN={tf_sec}s\n"
        f"SIM={'ON' if SIM_ENABLED else 'OFF'} | NAV={sim.nav:.2f} | BaseRR={SIM_RR}"
    )

    if "BTCUSDT" not in states or "ETHUSDT" not in states:
        raise RuntimeError("Regime proxies must be included in FALLBACK_SYMBOLS (BTCUSDT/ETHUSDT)")

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
                                        ps.candles_1h.append({"open": c1.open, "high": c1.high, "low": c1.low, "close": c1.close})
                                        ps.candles_1h = ps.candles_1h[-300:]

                                    c4, d4 = ps.r4h.update(st.cur_sec, mid, 0.0)
                                    if d4 and c4:
                                        ps.candles_4h.append({"open": c4.open, "high": c4.high, "low": c4.low, "close": c4.close})
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

                                # ================= MAIN candle =================
                                closed, did = st.r_main.update(st.cur_sec, mid, st.vol_bucket)
                                if did and closed:
                                    candle = {
                                        "open": closed.open,
                                        "high": closed.high,
                                        "low": closed.low,
                                        "close": closed.close,
                                    }
                                    st.candles.append(candle)
                                    st.volumes.append(closed.volume)
                                    st.candles = st.candles[-300:]
                                    st.volumes = st.volumes[-300:]

                                    # ---- SIM: update existing position first ----
                                    if SIM_ENABLED:
                                        close_info = sim.update_by_candle(sym, candle)
                                        if close_info:
                                            pos_mgr.close_position(sym)
                                            pos_mgr.update_nav(sim.nav)

                                            exit_price = float(close_info["exit"])
                                            pnl = float(close_info["pnl"])

                                            await tg_send(
                                                f"🔴 CLOSE {sym}\n"
                                                f"Exit: {exit_price:.6f}\n"
                                                f"Result: {close_info['result']}\n"
                                                f"PnL: {pnl:.2f} USDT\n"
                                                f"NAV: {sim.nav:.2f} USDT"
                                            )

                                    # ---- Decide open new trade only on candle close ----
                                    now = int(time.time())
                                    if now - st.last_main >= int(getattr(CFG, "COOLDOWN_SEC_MAIN", 60)):
                                        sig = check_signal(
                                            sym,
                                            st.candles,
                                            st.volumes,
                                            st.spread(),
                                            mode="main",
                                            market_regime=MARKET_REGIME,
                                            market_panic=MARKET_PANIC,
                                        )

                                        if sig:
                                            st.last_main = now

                                            if MARKET_PANIC and sig["direction"] == "LONG":
                                                st.vol_bucket = 0.0
                                                st.cur_sec += 1
                                                continue

                                            if SIM_ENABLED and sim.has_pos(sym):
                                                st.vol_bucket = 0.0
                                                st.cur_sec += 1
                                                continue

                                            atr_val = compute_atr(st.candles, int(getattr(CFG, "ATR_SHORT", 14)))
                                            if atr_val is None:
                                                st.vol_bucket = 0.0
                                                st.cur_sec += 1
                                                continue

                                            close_px = float(candle["close"])
                                            liq_usd = float(closed.volume) * close_px
                                            min_liq = float(getattr(CFG, "MIN_LIQUIDITY_USD", 0.0))
                                            if min_liq > 0 and liq_usd < min_liq:
                                                st.vol_bucket = 0.0
                                                st.cur_sec += 1
                                                continue

                                            rr, sl_mult, risk_mult = adaptive_rr_and_sl(sig, MARKET_REGIME, MARKET_PANIC, candle, float(atr_val))

                                            confirm_pct = compute_entry_confirm_pct(close_px, float(atr_val))
                                            planned_entry = apply_entry_confirm(close_px, sig["direction"], confirm_pct)

                                            rp = build_risk_plan(
                                                symbol=sym,
                                                direction=sig["direction"],
                                                entry=float(planned_entry),
                                                atr_value=float(atr_val),
                                                nav_usd=float(sim.nav),
                                                mode="main",
                                                cfg=CFG,
                                                rr=float(rr),
                                                risk_pct_mult=float(risk_mult),
                                                sl_atr_mult=float(sl_mult),
                                            )

                                            ok, reason = pos_mgr.can_open(
                                                symbol=sym,
                                                risk_usd=float(rp.risk_usd),
                                                new_prices=[c["close"] for c in st.candles[-60:]],
                                            )
                                            if not ok:
                                                st.vol_bucket = 0.0
                                                st.cur_sec += 1
                                                continue

                                            sim.open(
                                                SimPosition(
                                                    symbol=sym,
                                                    direction=rp.direction,
                                                    qty=float(rp.qty),
                                                    entry=float(rp.entry),
                                                    sl=float(rp.sl),
                                                    tp=float(rp.tp) if rp.tp is not None else float(rp.entry),
                                                    risk_usd=float(rp.risk_usd),
                                                    rr=float(rp.rr),
                                                    opened_at=time.time(),
                                                )
                                            )

                                            pos_mgr.open_position(
                                                symbol=sym,
                                                direction=rp.direction,
                                                qty=float(rp.qty),
                                                entry=float(rp.entry),
                                                sl=float(rp.sl),
                                                tp=float(rp.tp) if rp.tp is not None else None,
                                                risk_usd=float(rp.risk_usd),
                                                price_history=[c["close"] for c in st.candles[-60:]],
                                            )
                                            pos_mgr.update_nav(sim.nav)

                                            await tg_send(
                                                f"🟢 OPEN {rp.direction} {sym}\n"
                                                f"Entry: {rp.entry:.6f} (confirm {confirm_pct*100:.2f}%)\n"
                                                f"Qty: {rp.qty:.4f}\n"
                                                f"SL: {rp.sl:.6f} (ATR*{rp.sl_atr_mult:.2f})\n"
                                                f"TP: {rp.tp:.6f} (RR={rp.rr:.2f})\n"
                                                f"Risk: {rp.risk_usd:.2f} USDT ({rp.risk_pct:.2f}%)\n"
                                                f"NAV: {sim.nav:.2f} USDT\n"
                                                f"Regime: {MARKET_REGIME} | Panic: {MARKET_PANIC}\n"
                                                f"vol={sig.get('volume_ratio', 0):.2f}x | gap={sig.get('ema_gap', 0)*100:.2f}%"
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
        tg_worker(),
        ws_bookticker(states, url_book),
        ws_aggtrade(states, url_trade),
        nav_monitor(),
    )


if __name__ == "__main__":
    asyncio.run(main())
