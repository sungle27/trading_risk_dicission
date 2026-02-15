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

    def close(self, symbol: str) -> None:
        if symbol in self.positions:
            del self.positions[symbol]

    def update_by_candle(self, symbol: str, candle: dict) -> Optional[str]:
        """
        Check SL/TP using candle high/low.
        Return "SL" or "TP" if closed, else None.
        """
        pos = self.positions.get(symbol)
        if not pos:
            return None

        high = candle["high"]
        low = candle["low"]

        # LONG
        if pos.direction == "LONG":
            if low <= pos.sl:
                self.nav -= pos.risk_usd
                self.close(symbol)
                return "SL"
            if high >= pos.tp:
                self.nav += pos.risk_usd * self.rr
                self.close(symbol)
                return "TP"

        # SHORT
        else:
            if high >= pos.sl:
                self.nav -= pos.risk_usd
                self.close(symbol)
                return "SL"
            if low <= pos.tp:
                self.nav += pos.risk_usd * self.rr
                self.close(symbol)
                return "TP"

        return None


# ============================================================
# Position Manager (GLOBAL) - use app.position_manager.PositionManager
# ============================================================
pos_mgr = PositionManager(
    nav_usd=getattr(CFG, "NAV_USD", 0.0),
    max_positions=getattr(CFG, "MAX_POSITIONS", 10),
    max_total_risk_pct=getattr(CFG, "MAX_TOTAL_RISK_PCT", None),
    max_correlation=getattr(CFG, "MAX_CORRELATION", None),
    cfg=CFG,
)

SIM_ENABLED = bool(int(getattr(CFG, "SIM_ENABLED", 1)))
SIM_START_NAV = float(getattr(CFG, "SIM_START_NAV", 1000.0))
SIM_RR = float(getattr(CFG, "SIM_RR", 2.0))

sim = ExecutionSimulator(nav_usd=SIM_START_NAV, rr=SIM_RR)

# sync PM NAV with simulator NAV
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
        return (self.bid + self.ask) / 2.0

    def spread(self):
        m = self.mid()
        if not m:
            return 0.0
        return (self.ask - self.bid) / m


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
# REGIME NOTIFY
# ============================================================
async def notify_regime_change(new_regime: str, reason: str):
    await send_telegram(
        "📡 MARKET REGIME CHANGED\n"
        f"→ {new_regime}\n"
        f"Reason: {reason}"
    )


# ============================================================
# ATR helper (compute latest ATR value from candle list)
# ============================================================
def compute_atr(candles: List[dict], period: int) -> Optional[float]:
    if len(candles) < period + 2:
        return None

    atr = ATR(period)
    atr_val = None
    for c in candles:
        atr_val = atr.update(c["high"], c["low"], c["close"])
    return atr_val


# ============================================================
# WS: AGG TRADE
# ============================================================
async def ws_aggtrade(states: Dict[str, SymbolState], url: str):
    await send_telegram(
        "✅ Crypto Decision & Risk Bot RUNNING\n"
        f"symbols={len(states)} | EARLY=5m | MAIN=15m\n"
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

                                        if getattr(CFG, "DEBUG_ENABLED", 0):
                                            print(f"[REGIME] {MARKET_REGIME} panic={MARKET_PANIC} | {rr.reason}")

                                # ================= EARLY (5m) =================
                                closed5, did5 = st.r5m.update(st.cur_sec, mid, st.vol_bucket)
                                if did5 and closed5:
                                    st.candles_5m.append({
                                        "open": closed5.open,
                                        "high": closed5.high,
                                        "low": closed5.low,
                                        "close": closed5.close,
                                    })
                                    st.volumes_5m.append(closed5.volume)
                                    st.candles_5m = st.candles_5m[-200:]
                                    st.volumes_5m = st.volumes_5m[-200:]

                                    now = int(time.time())
                                    if now - st.last_early >= CFG.COOLDOWN_SEC_EARLY:
                                        sig = check_signal(
                                            sym,
                                            st.candles_5m,
                                            st.volumes_5m,
                                            st.spread(),
                                            mode="early",
                                            market_regime=MARKET_REGIME,
                                            market_panic=MARKET_PANIC,
                                        )
                                        if sig:
                                            st.last_early = now

                                            # optional: block early in panic to reduce noise
                                            if MARKET_PANIC:
                                                pass
                                            else:
                                                atr_val = compute_atr(st.candles_5m, CFG.ATR_SHORT)
                                                if atr_val is not None:
                                                    rp = build_risk_plan(
                                                        symbol=sym,
                                                        direction=sig["direction"],
                                                        entry=closed5.close,
                                                        atr_value=atr_val,
                                                        nav_usd=sim.nav if SIM_ENABLED else getattr(CFG, "NAV_USD", 0.0),
                                                        mode="early",
                                                        cfg=CFG,
                                                    )

                                                    ok, reason = pos_mgr.can_open(
                                                        symbol=sym,
                                                        risk_usd=rp.risk_usd,
                                                        new_prices=[c["close"] for c in st.candles_5m[-60:]],
                                                    )
                                                    if ok:
                                                        await send_telegram(
                                                            f"🔔 EARLY {sig['direction']} {sym} @ {closed5.close:.4f}\n"
                                                            f"Regime={MARKET_REGIME}\n"
                                                            f"Entry≈{rp.entry:.4f} | SL={rp.sl:.4f}\n"
                                                            f"Risk={rp.risk_pct:.2f}% | Qty≈{rp.qty:.4f}\n"
                                                            f"gap={sig['ema_gap']*100:.2f}% | vol={sig['volume_ratio']:.2f}x"
                                                        )
                                                    else:
                                                        if getattr(CFG, "DEBUG_ENABLED", 0):
                                                            print(f"[SKIP] {sym} early blocked: {reason}")

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
                                    st.candles_15m = st.candles_15m[-200:]
                                    st.volumes_15m = st.volumes_15m[-200:]

                                    # ---- SIM: update existing position first ----
                                    if SIM_ENABLED:
                                        result = sim.update_by_candle(sym, candle15)
                                        if result:
                                            pos_mgr.close(sym)
                                            pos_mgr.update_nav(sim.nav)

                                            await send_telegram(
                                                f"📊 SIM CLOSE {sym} → {result}\n"
                                                f"NAV={sim.nav:.2f} USDT | Regime={MARKET_REGIME}"
                                            )

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

                                            # PANIC policy:
                                            # - LONG disabled
                                            # - SHORT allowed (you can also tighten in alert_engine)
                                            if MARKET_PANIC and sig["direction"] == "LONG":
                                                continue

                                            atr_val = compute_atr(st.candles_15m, CFG.ATR_SHORT)
                                            if atr_val is None:
                                                continue

                                            rp = build_risk_plan(
                                                symbol=sym,
                                                direction=sig["direction"],
                                                entry=closed15.close,
                                                atr_value=atr_val,
                                                nav_usd=sim.nav if SIM_ENABLED else getattr(CFG, "NAV_USD", 0.0),
                                                mode="main",
                                                cfg=CFG,
                                            )

                                            ok, reason = pos_mgr.can_open(
                                                symbol=sym,
                                                risk_usd=rp.risk_usd,
                                                new_prices=[c["close"] for c in st.candles_15m[-60:]],
                                            )
                                            if not ok:
                                                if getattr(CFG, "DEBUG_ENABLED", 0):
                                                    print(f"[SKIP] {sym} main blocked: {reason}")
                                                continue

                                            tag = "🔥 HIGH CONF" if sig.get("high_conf") else "🚨 MAIN"

                                            # ---- SIM open position ----
                                            if SIM_ENABLED:
                                                # avoid double-open in simulator
                                                if sim.has_pos(sym):
                                                    continue

                                                sim.open(
                                                    SimPosition(
                                                        symbol=sym,
                                                        direction=sig["direction"],
                                                        qty=rp.qty,
                                                        entry=rp.entry,
                                                        sl=rp.sl,
                                                        tp=rp.tp,
                                                        risk_usd=rp.risk_usd,
                                                        opened_at=time.time(),
                                                    )
                                                )
                                                pos_mgr.open(
                                                    symbol=sym,
                                                    direction=sig["direction"],
                                                    qty=rp.qty,
                                                    entry=rp.entry,
                                                    sl=rp.sl,
                                                    tp=rp.tp,
                                                    risk_usd=rp.risk_usd,
                                                    price_history=[c["close"] for c in st.candles_15m[-60:]],
                                                )
                                                pos_mgr.update_nav(sim.nav)

                                                await send_telegram(
                                                    f"📈 SIM OPEN {sig['direction']} {sym} @ {closed15.close:.4f}\n"
                                                    f"Regime={MARKET_REGIME} | Panic={MARKET_PANIC}\n"
                                                    f"(Score={sig['score']}/17) | {tag}\n"
                                                    f"Entry≈{rp.entry:.4f}\n"
                                                    f"SL={rp.sl:.4f} | TP={rp.tp:.4f}\n"
                                                    f"Qty≈{rp.qty:.4f} | Risk={rp.risk_usd:.2f} USDT\n"
                                                    f"NAV={sim.nav:.2f}\n"
                                                    f"ema_gap={sig['ema_gap']*100:.2f}% | vol={sig['volume_ratio']:.2f}x\n"
                                                    f"ATR squeeze={'✅' if sig.get('atr_squeeze') else '❌'} | BreakHigh20={'✅' if sig.get('breakout_highlow') else '❌'}"
                                                )
                                            else:
                                                # decision-only alert mode
                                                await send_telegram(
                                                    f"{tag} {sig['direction']} {sym} @ {closed15.close:.4f}\n"
                                                    f"Regime={MARKET_REGIME} | Panic={MARKET_PANIC}\n"
                                                    f"(Score={sig['score']}/17)\n"
                                                    f"Entry≈{rp.entry:.4f}\n"
                                                    f"SL={rp.sl:.4f} | TP={rp.tp:.4f}\n"
                                                    f"Qty≈{rp.qty:.4f} | Risk={rp.risk_usd:.2f} USDT\n"
                                                    f"ema_gap={sig['ema_gap']*100:.2f}% | vol={sig['volume_ratio']:.2f}x\n"
                                                    f"ATR squeeze={'✅' if sig.get('atr_squeeze') else '❌'} | BreakHigh20={'✅' if sig.get('breakout_highlow') else '❌'}"
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
    )


if __name__ == "__main__":
    asyncio.run(main())