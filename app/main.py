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

# ============================================================
# Market Regime (global)
# ============================================================
MRE = MarketRegimeEngine()
MARKET_REGIME = "NORMAL"
MARKET_PANIC = False
LAST_REGIME = None   # 👈 NEW


# ============================================================
# SYMBOL STATE
# ============================================================
class SymbolState:
    def __init__(self):
        # bid/ask realtime
        self.bid = None
        self.ask = None
        self.cur_sec = None

        # volume bucket per candle
        self.vol_bucket = 0.0

        # resample timeframes
        self.r5m = TimeframeResampler(5 * 60)      # EARLY
        self.r15m = TimeframeResampler(15 * 60)    # MAIN

        # candle history
        self.candles_5m: List[dict] = []
        self.volumes_5m: List[float] = []

        self.candles_15m: List[dict] = []
        self.volumes_15m: List[float] = []

        # cooldown tracking
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
# Proxy (BTC/ETH) regime state
# ============================================================
class ProxyState:
    def __init__(self):
        self.r1h = TimeframeResampler(60 * 60)
        self.r4h = TimeframeResampler(4 * 60 * 60)

        self.candles_1h: List[dict] = []
        self.candles_4h: List[dict] = []


# ============================================================
# WS: BOOK TICKER (spread)
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


async def notify_regime_change(new_regime: str, panic: bool, reason: str):
    if new_regime == "PANIC":
        msg = (
            "🔴 MARKET PANIC DETECTED\n"
            "BTC/ETH volatility spike\n"
            "→ LONG DISABLED\n"
            "→ EARLY OFF\n"
            "→ SHORT ONLY (strict)\n\n"
            f"Reason: {reason}"
        )

    elif new_regime == "RECOVERY":
        msg = (
            "🟣 MARKET RECOVERY MODE\n"
            "Volatility cooling down\n"
            "→ EARLY OFF\n"
            "→ MAIN ONLY (cautious)\n\n"
            f"Reason: {reason}"
        )

    elif new_regime == "RANGE":
        msg = (
            "🟡 MARKET RANGE MODE\n"
            "Low volatility / choppy\n"
            "→ EARLY OFF\n"
            "→ MAIN selective\n\n"
            f"Reason: {reason}"
        )

    elif new_regime == "TREND":
        msg = (
            "🟢 MARKET TREND MODE\n"
            "BTC/ETH trending\n"
            "→ Follow trend\n"
            "→ Signals boosted\n\n"
            f"Reason: {reason}"
        )

    else:  # NORMAL
        msg = (
            "🔵 MARKET NORMAL MODE\n"
            "Conditions stabilized\n"
            "→ System fully enabled\n\n"
            f"Reason: {reason}"
        )

    await send_telegram(msg)

# ============================================================
# WS: AGG TRADE (engine)
# ============================================================
async def ws_aggtrade(states: Dict[str, SymbolState], url: str):

    # ====================================================
    # STARTUP MESSAGE (ONLY ONCE)
    # ====================================================
    await send_telegram(
        "✅ Crypto Alert Bot is RUNNING\n"
        f"symbols={len(states)} | EARLY=5m | MAIN=15m"
    )

    # Ensure regime proxies
    if "BTCUSDT" not in states or "ETHUSDT" not in states:
        raise RuntimeError(
            "Regime proxies must be included in FALLBACK_SYMBOLS (BTCUSDT/ETHUSDT)"
        )

    proxy_syms = ("BTCUSDT", "ETHUSDT")
    proxy_states = {s: ProxyState() for s in proxy_syms}

    global MARKET_REGIME, MARKET_PANIC

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

                        # ====================================================
                        # ADVANCE TIME LOOP
                        # ====================================================
                        while sec > st.cur_sec:

                            mid = st.mid()
                            if mid:

                                # -----------------------------
                                # Update regime proxies first
                                # -----------------------------
                                if sym in proxy_states:
                                    ps = proxy_states[sym]

                                    closed1h, did1h = ps.r1h.update(st.cur_sec, mid, 0.0)
                                    if did1h and closed1h:
                                        ps.candles_1h.append({
                                            "open": closed1h.open,
                                            "high": closed1h.high,
                                            "low": closed1h.low,
                                            "close": closed1h.close,
                                        })
                                        ps.candles_1h = ps.candles_1h[-300:]

                                    closed4h, did4h = ps.r4h.update(st.cur_sec, mid, 0.0)
                                    if did4h and closed4h:
                                        ps.candles_4h.append({
                                            "open": closed4h.open,
                                            "high": closed4h.high,
                                            "low": closed4h.low,
                                            "close": closed4h.close,
                                        })
                                        ps.candles_4h = ps.candles_4h[-300:]

                                    # Recompute regime ONLY when any 1H candle closes
                                    if did1h and closed1h:
                                        c1 = {k: v.candles_1h for k, v in proxy_states.items()}
                                        c4 = {k: v.candles_4h for k, v in proxy_states.items()}
                                        rr = MRE.update(c1, c4)

                                        # ====================================================
                                        # REGIME CHANGE NOTIFICATION (ONLY ON CHANGE)
                                        # ====================================================
                                        if rr.regime != LAST_REGIME:
                                            await notify_regime_change(
                                                rr.regime,
                                                rr.panic,
                                                rr.reason,
                                            )
                                            LAST_REGIME = rr.regime

                                        MARKET_REGIME = rr.regime
                                        MARKET_PANIC = rr.panic


                                        # Optional: log local (không gửi telegram để tránh spam)
                                        if CFG.DEBUG_ENABLED:
                                            print(f"[REGIME] {MARKET_REGIME} | panic={MARKET_PANIC} | {rr.reason}")

                                # ====================================================
                                # EARLY MODE (5m)
                                # ====================================================
                                closed5, did5 = st.r5m.update(
                                    st.cur_sec, mid, st.vol_bucket
                                )

                                if did5 and closed5:
                                    st.candles_5m.append({
                                        "open": closed5.open,
                                        "high": closed5.high,
                                        "low": closed5.low,
                                        "close": closed5.close,
                                    })
                                    st.volumes_5m.append(closed5.volume)

                                    st.candles_5m = st.candles_5m[-60:]
                                    st.volumes_5m = st.volumes_5m[-60:]

                                    spread = st.spread()
                                    now = int(time.time())

                                    if now - st.last_early >= CFG.COOLDOWN_SEC_EARLY:
                                        sig = check_signal(
                                            sym,
                                            st.candles_5m,
                                            st.volumes_5m,
                                            spread,
                                            mode="early",
                                            market_regime=MARKET_REGIME,
                                            market_panic=MARKET_PANIC,
                                        )

                                        if sig:
                                            st.last_early = now
                                            price = closed5.close

                                            msg = (
                                                f"🔔 EARLY {sig['direction']} {sym} @ {price:.4f}\n"
                                                f"(Score={sig['score']}/17) | REGIME={sig['market_regime']}\n"
                                                f"gap={sig['ema_gap']*100:.2f}% | "
                                                f"vol={sig['volume_ratio']:.2f}x"
                                            )
                                            await send_telegram(msg)

                                # ====================================================
                                # MAIN MODE (15m)
                                # ====================================================
                                closed15, did15 = st.r15m.update(
                                    st.cur_sec, mid, st.vol_bucket
                                )

                                if did15 and closed15:
                                    st.candles_15m.append({
                                        "open": closed15.open,
                                        "high": closed15.high,
                                        "low": closed15.low,
                                        "close": closed15.close,
                                    })
                                    st.volumes_15m.append(closed15.volume)

                                    st.candles_15m = st.candles_15m[-60:]
                                    st.volumes_15m = st.volumes_15m[-60:]

                                    spread = st.spread()
                                    now = int(time.time())

                                    if now - st.last_main >= CFG.COOLDOWN_SEC_MAIN:
                                        sig = check_signal(
                                            sym,
                                            st.candles_15m,
                                            st.volumes_15m,
                                            spread,
                                            mode="main",
                                            market_regime=MARKET_REGIME,
                                            market_panic=MARKET_PANIC,
                                        )

                                        if sig:
                                            st.last_main = now
                                            price = closed15.close

                                            tag = "🔥 HIGH CONF" if sig["high_conf"] else "🚨 MAIN"

                                            msg = (
                                                f"{tag} {sig['direction']} {sym} @ {price:.4f}\n"
                                                f"(Score={sig['score']}/17) | REGIME={sig['market_regime']}\n"
                                                f"ema_gap={sig['ema_gap']*100:.2f}%\n"
                                                f"vol={sig['volume_ratio']:.2f}x\n"
                                                f"spread={sig['spread']:.4f}\n"
                                                f"ATR squeeze={'✅' if sig['atr_squeeze'] else '❌'}\n"
                                                f"BreakHigh20={'✅' if sig.get('breakout_highlow') else '❌'}"
                                            )
                                            await send_telegram(msg)

                                # reset volume bucket after candle close
                                st.vol_bucket = 0.0

                            st.cur_sec += 1

                        # accumulate volume
                        st.vol_bucket += qty

        except Exception as e:
            print("aggtrade error:", e)
            await asyncio.sleep(backoff_s(1))


# ============================================================
# MAIN ENTRYPOINT
# ============================================================
async def main():
    symbols = FALLBACK_SYMBOLS
    states = {s: SymbolState() for s in symbols}

    ws_base = CFG.BINANCE_FUTURES_WS

    url_book = ws_base + "?streams=" + "/".join(
        f"{s.lower()}@bookTicker" for s in symbols
    )

    url_trade = ws_base + "?streams=" + "/".join(
        f"{s.lower()}@aggTrade" for s in symbols
    )

    await asyncio.gather(
        ws_bookticker(states, url_book),
        ws_aggtrade(states, url_trade),
    )


if __name__ == "__main__":
    asyncio.run(main())
