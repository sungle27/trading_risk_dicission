from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, List

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
LAST_REGIME = None


# ============================================================
# Position Manager (GLOBAL)
# ============================================================
pos_mgr = PositionManager(
    nav_usd=CFG.NAV_USD,
    max_positions=CFG.MAX_POSITIONS,
    max_total_risk_pct=CFG.MAX_TOTAL_RISK_PCT,
)


# ============================================================
# SYMBOL STATE
# ============================================================
class SymbolState:
    def __init__(self):
        self.bid = None
        self.ask = None
        self.cur_sec = None

        self.vol_bucket = 0.0

        self.r5m = TimeframeResampler(5 * 60)
        self.r15m = TimeframeResampler(15 * 60)

        self.candles_5m: List[dict] = []
        self.volumes_5m: List[float] = []

        self.candles_15m: List[dict] = []
        self.volumes_15m: List[float] = []

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
        f"📡 MARKET REGIME CHANGED\n"
        f"→ {new_regime}\n"
        f"Reason: {reason}"
    )


# ============================================================
# WS: AGG TRADE
# ============================================================
async def ws_aggtrade(states: Dict[str, SymbolState], url: str):

    await send_telegram(
        "✅ Crypto Decision & Risk Bot RUNNING\n"
        "EARLY=5m | MAIN=15m"
    )

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

                                # ================= REGIME UPDATE =================
                                if sym in proxy_states:
                                    ps = proxy_states[sym]

                                    c1, d1 = ps.r1h.update(st.cur_sec, mid, 0.0)
                                    if d1 and c1:
                                        ps.candles_1h.append(c1.__dict__)
                                        ps.candles_1h = ps.candles_1h[-300:]

                                    c4, d4 = ps.r4h.update(st.cur_sec, mid, 0.0)
                                    if d4 and c4:
                                        ps.candles_4h.append(c4.__dict__)
                                        ps.candles_4h = ps.candles_4h[-300:]

                                    if d1:
                                        rr = MRE.update(
                                            {k: v.candles_1h for k, v in proxy_states.items()},
                                            {k: v.candles_4h for k, v in proxy_states.items()},
                                        )
                                        MARKET_REGIME = rr.regime
                                        MARKET_PANIC = rr.panic

                                        if rr.regime != LAST_REGIME:
                                            await notify_regime_change(rr.regime, rr.reason)
                                            LAST_REGIME = rr.regime

                                # ================= EARLY (5m) =================
                                closed5, did5 = st.r5m.update(st.cur_sec, mid, st.vol_bucket)
                                if did5 and closed5:
                                    st.candles_5m.append(closed5.__dict__)
                                    st.volumes_5m.append(closed5.volume)
                                    st.candles_5m = st.candles_5m[-60:]
                                    st.volumes_5m = st.volumes_5m[-60:]

                                    if not MARKET_PANIC:
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
                                            atr = ATR(CFG.ATR_SHORT)
                                            atr_val = None
                                            for c in st.candles_5m:
                                                atr_val = atr.update(c["high"], c["low"], c["close"])
                                            if atr_val:
                                                rp = build_risk_plan(
                                                    symbol=sym,
                                                    direction=sig["direction"],
                                                    entry=closed5.close,
                                                    atr_value=atr_val,
                                                    nav_usd=CFG.NAV_USD,
                                                    mode="early",
                                                    cfg=CFG,
                                                )
                                                ok, _ = pos_mgr.can_open(sym, rp.risk_usd)
                                                if ok:
                                                    await send_telegram(
                                                        f"🔔 EARLY {sig['direction']} {sym}\n"
                                                        f"Entry≈{rp.entry:.4f} | SL={rp.sl:.4f}\n"
                                                        f"Risk={rp.risk_pct:.2f}% | Qty≈{rp.qty:.4f}\n"
                                                        f"Regime={MARKET_REGIME}"
                                                    )

                                # ================= MAIN (15m) =================
                                closed15, did15 = st.r15m.update(st.cur_sec, mid, st.vol_bucket)
                                if did15 and closed15:
                                    st.candles_15m.append(closed15.__dict__)
                                    st.volumes_15m.append(closed15.volume)
                                    st.candles_15m = st.candles_15m[-60:]
                                    st.volumes_15m = st.volumes_15m[-60:]

                                    if not MARKET_PANIC:
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
                                            atr = ATR(CFG.ATR_SHORT)
                                            atr_val = None
                                            for c in st.candles_15m:
                                                atr_val = atr.update(c["high"], c["low"], c["close"])
                                            if atr_val:
                                                rp = build_risk_plan(
                                                    symbol=sym,
                                                    direction=sig["direction"],
                                                    entry=closed15.close,
                                                    atr_value=atr_val,
                                                    nav_usd=CFG.NAV_USD,
                                                    mode="main",
                                                    cfg=CFG,
                                                )
                                                ok, _ = pos_mgr.can_open(sym, rp.risk_usd)
                                                if ok:
                                                    tag = "🔥 HIGH CONF" if sig["high_conf"] else "🚨 MAIN"
                                                    await send_telegram(
                                                        f"{tag} {sig['direction']} {sym}\n"
                                                        f"Entry≈{rp.entry:.4f}\n"
                                                        f"SL={rp.sl:.4f} | Qty≈{rp.qty:.4f}\n"
                                                        f"Risk={rp.risk_pct:.2f}% | RR={rp.rr}\n"
                                                        f"Regime={MARKET_REGIME}"
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
