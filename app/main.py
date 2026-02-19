from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import aiohttp

from app.config import CFG
from app.telegram import send_telegram
from app.resample import TimeframeResampler
from app.alert_engine import check_signal
from app.symbols import FALLBACK_SYMBOLS
from app.utils import backoff_s

from app.market_regime import MarketRegimeEngine
from app.risk_engine import build_risk_plan, RiskPlan
from app.position_manager import PositionManager
from app.drawdown_manager import DrawdownManager
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
    rr: float


class ExecutionSimulator:
    def __init__(self, nav_usd: float, slippage_pct: float = 0.0):
        self.nav = float(nav_usd)
        self.slippage_pct = float(slippage_pct)
        self.positions: Dict[str, SimPosition] = {}

        # stats
        self.total_trades = 0
        self.win_trades = 0
        self.loss_trades = 0
        self.total_pnl = 0.0

    def has_pos(self, symbol: str) -> bool:
        return symbol in self.positions

    def _apply_slippage_open(self, direction: str, entry: float) -> float:
        """
        Slippage against you:
        - LONG: filled a bit higher
        - SHORT: filled a bit lower
        """
        s = self.slippage_pct
        if s <= 0:
            return entry
        if direction == "LONG":
            return entry * (1 + s)
        return entry * (1 - s)

    def _apply_slippage_exit(self, direction: str, exit_price: float, result: str) -> float:
        """
        Exit slippage (simple):
        - SL: worse fill
        - TP: slightly worse too (still realistic)
        """
        s = self.slippage_pct
        if s <= 0:
            return exit_price

        if direction == "LONG":
            # SL exits lower, TP exits lower
            return exit_price * (1 - s)
        else:
            # SHORT SL exits higher, TP exits higher
            return exit_price * (1 + s)

    def open(self, pos: SimPosition) -> None:
        self.positions[pos.symbol] = pos

    def close(self, symbol: str) -> Optional[SimPosition]:
        return self.positions.pop(symbol, None)

    def update_by_candle(self, symbol: str, candle: dict) -> Optional[dict]:
        """
        Check SL/TP using candle high/low.
        Return dict: {result, exit, pnl, rr}
        """
        pos = self.positions.get(symbol)
        if not pos:
            return None

        high = float(candle["high"])
        low = float(candle["low"])

        result: Optional[str] = None
        exit_price: Optional[float] = None
        pnl = 0.0

        # LONG
        if pos.direction == "LONG":
            if low <= pos.sl:
                result = "SL"
                exit_price = pos.sl
                pnl = -pos.risk_usd
            elif high >= pos.tp:
                result = "TP"
                exit_price = pos.tp
                pnl = pos.risk_usd * pos.rr

        # SHORT
        else:
            if high >= pos.sl:
                result = "SL"
                exit_price = pos.sl
                pnl = -pos.risk_usd
            elif low <= pos.tp:
                result = "TP"
                exit_price = pos.tp
                pnl = pos.risk_usd * pos.rr

        if not result:
            return None

        # apply exit slippage
        exit_filled = self._apply_slippage_exit(pos.direction, float(exit_price), result)

        # NAV update
        self.nav += pnl

        # stats
        self.total_trades += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.win_trades += 1
        else:
            self.loss_trades += 1

        self.close(symbol)

        return {
            "result": result,
            "exit": exit_filled,
            "pnl": pnl,
            "rr": pos.rr,
        }

    def summary(self) -> dict:
        winrate = (self.win_trades / self.total_trades * 100.0) if self.total_trades > 0 else 0.0
        return {
            "total": self.total_trades,
            "wins": self.win_trades,
            "losses": self.loss_trades,
            "winrate": winrate,
            "pnl": self.total_pnl,
            "nav": self.nav,
        }


# ============================================================
# GLOBAL: Position Manager
# ============================================================
pos_mgr = PositionManager(
    nav_usd=float(getattr(CFG, "NAV_USD", SIM_START_NAV)),
    max_positions=int(getattr(CFG, "MAX_POSITIONS", 10)),
    max_total_risk_pct=getattr(CFG, "MAX_TOTAL_RISK_PCT", None),
    max_correlation=getattr(CFG, "MAX_CORRELATION", None),
    cfg=CFG,
)

sim = ExecutionSimulator(nav_usd=SIM_START_NAV, slippage_pct=float(getattr(CFG, "SLIPPAGE_PCT", 0.0)))
pos_mgr.update_nav(sim.nav)

ddm = DrawdownManager(
    start_nav=SIM_START_NAV,
    dd_soft_pct=float(getattr(CFG, "DD_SOFT_PCT", 0.06)),
    dd_hard_pct=float(getattr(CFG, "DD_HARD_PCT", 0.10)),
    dd_kill_pct=float(getattr(CFG, "DD_KILL_PCT", 0.18)),
    hard_cooldown_sec=int(getattr(CFG, "DD_HARD_COOLDOWN_SEC", 6 * 60 * 60)),
    min_risk_mult=0.35,
)
ddm.update(sim.nav)


# ============================================================
# SYMBOL STATE
# ============================================================
class SymbolState:
    def __init__(self):
        self.bid = None
        self.ask = None
        self.cur_sec = None

        self.vol_bucket = 0.0

        # MAIN timeframe
        self.r_main = TimeframeResampler(15 * 60)

        self.candles: List[dict] = []
        self.volumes: List[float] = []

        self.last_main = 0

    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return (float(self.bid) + float(self.ask)) / 2.0

    def spread(self) -> float:
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
# Helpers
# ============================================================
def compute_atr(candles: List[dict], period: int) -> Optional[float]:
    if len(candles) < period + 2:
        return None
    atr = ATR(period)
    atr_val = None
    for c in candles:
        atr_val = atr.update(float(c["high"]), float(c["low"]), float(c["close"]))
    return atr_val


def liquidity_usd_last_n(candles: List[dict], volumes: List[float], n: int = 20) -> float:
    """
    Very simple liquidity proxy: sum(volume) * close.
    (Works acceptably for filtering low-liquidity alts.)
    """
    if not candles or not volumes:
        return 0.0
    n = min(n, len(candles), len(volumes))
    close = float(candles[-1]["close"])
    v_sum = float(sum(volumes[-n:]))
    return v_sum * close


def choose_rr_and_sl(sig: dict) -> Tuple[float, float]:
    """
    Dynamic RR & SL multiplier (ATR multiple) using existing signal fields.
    """
    base_rr = float(getattr(CFG, "SIM_RR", 2.0))
    sl_mult = float(getattr(CFG, "SL_ATR_MULT", 1.5))

    # High confidence -> go for bigger RR
    if sig.get("high_conf"):
        base_rr = 2.5
        sl_mult *= 1.05

    # Regime adjust
    if MARKET_REGIME == "TREND":
        base_rr = max(base_rr, 2.2)
        sl_mult *= 1.10
    elif MARKET_REGIME == "RANGE":
        base_rr = min(base_rr, 1.6)
        sl_mult *= 0.90

    # Micro-adjust by volume & momentum if present
    vr = float(sig.get("volume_ratio", 1.0))
    mom = float(sig.get("momentum", 0.0)) if "momentum" in sig else 0.0

    if vr >= 3.0:
        base_rr += 0.2
    if mom >= 0.010:
        base_rr += 0.2

    # ATR squeeze -> breakout style: tighter SL, higher RR
    if sig.get("atr_squeeze"):
        sl_mult *= 0.90
        base_rr += 0.2

    # clamp
    base_rr = max(1.2, min(3.0, base_rr))
    sl_mult = max(0.8, min(2.8, sl_mult))

    return base_rr, sl_mult


def compute_entry(close_price: float, direction: str) -> float:
    mode = str(getattr(CFG, "ENTRY_MODE", "adaptive")).lower()
    pullback = float(getattr(CFG, "ENTRY_PULLBACK_PCT", 0.003))
    breakout = float(getattr(CFG, "ENTRY_BREAKOUT_PCT", 0.0015))

    if mode != "adaptive":
        return close_price

    # TREND: breakout entry
    if MARKET_REGIME == "TREND":
        return close_price * (1 + breakout) if direction == "LONG" else close_price * (1 - breakout)

    # RANGE/NORMAL: pullback entry
    if MARKET_REGIME in ("NORMAL", "RANGE"):
        return close_price * (1 - pullback) if direction == "LONG" else close_price * (1 + pullback)

    # PANIC: breakout (but you already block LONG in panic below)
    return close_price * (1 + breakout) if direction == "LONG" else close_price * (1 - breakout)


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
# NAV MONITOR
# ============================================================
async def nav_monitor():
    interval_sec = int(getattr(CFG, "NAV_REPORT_SEC", 3600))
    while True:
        await asyncio.sleep(interval_sec)

        stats = sim.summary()
        dd = ddm.state()
        total_risk = pos_mgr.total_risk_usd() if hasattr(pos_mgr, "total_risk_usd") else 0.0

        await send_telegram(
            f"📊 SIM STATUS\n"
            f"NAV: {stats['nav']:.2f} USDT | Peak: {dd.peak_nav:.2f}\n"
            f"DD: {dd.dd_pct*100:.2f}% | Regime: {MARKET_REGIME} | Panic: {MARKET_PANIC}\n"
            f"Open positions: {len(sim.positions)} | Total risk: {total_risk:.2f} USDT\n\n"
            f"📈 Performance\n"
            f"Trades: {stats['total']} | Wins: {stats['wins']} | Losses: {stats['losses']}\n"
            f"Winrate: {stats['winrate']:.2f}% | Total PnL: {stats['pnl']:.2f} USDT"
        )


# ============================================================
# WS: AGG TRADE (engine)
# ============================================================
async def ws_aggtrade(states: Dict[str, SymbolState], url: str):
    await send_telegram(
        "✅ SIM TRADING BOT RUNNING\n"
        f"symbols={len(states)} | MAIN=15m\n"
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

                                # ---------------- REGIME UPDATE (BTC/ETH) ----------------
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
                                        rr_state = MRE.update(
                                            {k: v.candles_1h for k, v in proxy_states.items()},
                                            {k: v.candles_4h for k, v in proxy_states.items()},
                                        )
                                        MARKET_REGIME = rr_state.regime
                                        MARKET_PANIC = rr_state.panic

                                        if rr_state.regime != LAST_REGIME:
                                            LAST_REGIME = rr_state.regime

                                # ---------------- MAIN candle close ----------------
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
                                    st.candles = st.candles[-400:]
                                    st.volumes = st.volumes[-400:]

                                    # 1) update existing position
                                    if SIM_ENABLED:
                                        close_info = sim.update_by_candle(sym, candle)
                                        if close_info:
                                            pos_mgr.close_position(sym)
                                            pos_mgr.update_nav(sim.nav)

                                            ddm.update(sim.nav)
                                            stats = sim.summary()
                                            dd = ddm.state()

                                            await send_telegram(
                                                f"🔴 CLOSE {sym}\n"
                                                f"Exit: {float(close_info['exit']):.6f}\n"
                                                f"Result: {close_info['result']} | PnL: {float(close_info['pnl']):.2f} USDT\n"
                                                f"NAV: {sim.nav:.2f} | DD: {dd.dd_pct*100:.2f}%\n"
                                                f"Trades: {stats['total']} | W/L: {stats['wins']}/{stats['losses']} ({stats['winrate']:.1f}%)"
                                            )

                                    # 2) decide open
                                    now = int(time.time())
                                    if now - st.last_main >= int(getattr(CFG, "COOLDOWN_SEC_MAIN", 900)):
                                        st.last_main = now

                                        # drawdown gate
                                        ddm.update(sim.nav)
                                        can, dd_reason = ddm.can_trade()
                                        if not can:
                                            continue

                                        sig = check_signal(
                                            sym,
                                            st.candles,
                                            st.volumes,
                                            st.spread(),
                                            mode="main",
                                            market_regime=MARKET_REGIME,
                                            market_panic=MARKET_PANIC,
                                        )
                                        if not sig:
                                            continue

                                        # panic policy: block LONG
                                        if MARKET_PANIC and sig.get("direction") == "LONG":
                                            continue

                                        if sim.has_pos(sym):
                                            continue

                                        # liquidity filter
                                        min_liq = float(getattr(CFG, "MIN_LIQUIDITY_USD", 5_000_000.0))
                                        liq = liquidity_usd_last_n(st.candles, st.volumes, n=20)
                                        if liq < min_liq:
                                            continue

                                        atr_val = compute_atr(st.candles, int(getattr(CFG, "ATR_SHORT", 5)))
                                        if atr_val is None:
                                            continue

                                        # dynamic RR & SL
                                        rr, sl_mult = choose_rr_and_sl(sig)

                                        entry = compute_entry(float(closed.close), sig["direction"])

                                        # risk multiplier from drawdown + regime
                                        risk_mult = ddm.risk_multiplier()

                                        if sig.get("high_conf"):
                                            risk_mult *= 1.20
                                        if MARKET_REGIME == "TREND":
                                            risk_mult *= 1.10
                                        if MARKET_REGIME == "RANGE":
                                            risk_mult *= 0.75
                                        if MARKET_PANIC:
                                            risk_mult *= 0.60

                                        rp: RiskPlan = build_risk_plan(
                                            symbol=sym,
                                            direction=sig["direction"],
                                            entry=entry,
                                            atr_value=float(atr_val),
                                            nav_usd=float(sim.nav),
                                            mode="main",
                                            cfg=CFG,
                                            rr=rr,
                                            risk_multiplier=risk_mult,
                                            sl_atr_mult=sl_mult,
                                            target_vol_pct=float(getattr(CFG, "TARGET_VOL_PCT", 0.015)),
                                        )

                                        # PM gate
                                        ok, reason = pos_mgr.can_open(
                                            symbol=sym,
                                            risk_usd=float(rp.risk_usd),
                                            new_prices=[c["close"] for c in st.candles[-80:]],
                                        )
                                        if not ok:
                                            continue

                                        # apply slippage to filled entry
                                        filled_entry = sim._apply_slippage_open(rp.direction, rp.entry)

                                        # keep SL/TP distances same (shifted)
                                        dist_sl = abs(rp.entry - rp.sl)
                                        dist_tp = abs(rp.tp - rp.entry)

                                        if rp.direction == "LONG":
                                            sl = filled_entry - dist_sl
                                            tp = filled_entry + dist_tp
                                        else:
                                            sl = filled_entry + dist_sl
                                            tp = filled_entry - dist_tp

                                        # open sim
                                        sim.open(
                                            SimPosition(
                                                symbol=sym,
                                                direction=rp.direction,
                                                qty=float(rp.qty),
                                                entry=float(filled_entry),
                                                sl=float(sl),
                                                tp=float(tp),
                                                risk_usd=float(rp.risk_usd),
                                                opened_at=time.time(),
                                                rr=float(rp.rr),
                                            )
                                        )

                                        # open PM
                                        pos_mgr.open_position(
                                            symbol=sym,
                                            direction=rp.direction,
                                            qty=float(rp.qty),
                                            entry=float(filled_entry),
                                            sl=float(sl),
                                            tp=float(tp),
                                            risk_usd=float(rp.risk_usd),
                                            price_history=[c["close"] for c in st.candles[-80:]],
                                        )
                                        pos_mgr.update_nav(sim.nav)

                                        ddm.update(sim.nav)
                                        dd = ddm.state()

                                        await send_telegram(
                                            f"🟢 OPEN {rp.direction} {sym}\n"
                                            f"Entry: {filled_entry:.6f}\n"
                                            f"Qty: {rp.qty:.4f}\n"
                                            f"SL: {sl:.6f}\n"
                                            f"TP: {tp:.6f}\n"
                                            f"Risk: {rp.risk_usd:.2f} USDT | RR: {rp.rr:.2f}\n"
                                            f"NAV: {sim.nav:.2f} | DD: {dd.dd_pct*100:.2f}%\n"
                                            f"liq≈{liq:,.0f}$ | {rp.notes}"
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
    # enforce timeframe from config if you want
    states = {s: SymbolState() for s in FALLBACK_SYMBOLS}

    ws_base = CFG.BINANCE_FUTURES_WS
    url_book = ws_base + "?streams=" + "/".join(f"{s.lower()}@bookTicker" for s in states)
    url_trade = ws_base + "?streams=" + "/".join(f"{s.lower()}@aggTrade" for s in states)

    await asyncio.gather(
        ws_bookticker(states, url_book),
        ws_aggtrade(states, url_trade),
        nav_monitor(),
    )


if __name__ == "__main__":
    asyncio.run(main())
