from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional

from app.config import CFG
from app.indicators import ATR

REGIMES = ("NORMAL", "TREND", "RANGE", "PANIC", "RECOVERY")


def _get(name: str, default):
    return getattr(CFG, name, default)


@dataclass
class RegimeResult:
    regime: str
    panic: bool
    risk_mult: float
    reason: str


class MarketRegimeEngine:
    """
    Regime engine dùng BTC/ETH làm proxy.
    - Input: candles_1h, candles_4h cho BTCUSDT/ETHUSDT
    - Output: NORMAL / TREND / RANGE / PANIC / RECOVERY
    """

    def __init__(self):
        self.regime: str = "NORMAL"
        self.panic: bool = False
        self.last_reason: str = "init"

    # -----------------------------
    # Helpers
    # -----------------------------
    @staticmethod
    def _atr_pct(candles: List[dict], period: int) -> Optional[float]:
        """
        ATR% = ATR / close
        """
        if len(candles) < period + 2:
            return None
        atr = ATR(period)
        v = None
        for c in candles:
            v = atr.update(c["high"], c["low"], c["close"])
        if v is None:
            return None
        last_close = candles[-1]["close"]
        if not last_close:
            return None
        return v / last_close

    @staticmethod
    def _ema(series: List[float], period: int) -> Optional[float]:
        if len(series) < period:
            return None
        mult = 2.0 / (period + 1.0)
        val = series[0]
        for x in series[1:]:
            val = (x - val) * mult + val
        return val

    @staticmethod
    def _ema_gap(candles: List[dict], fast: int, slow: int) -> Optional[float]:
        closes = [c["close"] for c in candles]
        ef = MarketRegimeEngine._ema(closes[-slow:], fast)
        es = MarketRegimeEngine._ema(closes[-slow:], slow)
        if ef is None or es is None or es == 0:
            return None
        return abs(ef - es) / es

    @staticmethod
    def _trend_dir(candles: List[dict], fast: int, slow: int) -> Optional[str]:
        closes = [c["close"] for c in candles]
        if len(closes) < slow:
            return None
        ef = MarketRegimeEngine._ema(closes[-slow:], fast)
        es = MarketRegimeEngine._ema(closes[-slow:], slow)
        if ef is None or es is None:
            return None
        return "UP" if ef > es else "DOWN"

    # -----------------------------
    # Main update
    # -----------------------------
    def update(
        self,
        candles_1h: Dict[str, List[dict]],
        candles_4h: Dict[str, List[dict]],
    ) -> RegimeResult:
        proxies = ("BTCUSDT", "ETHUSDT")

        # soft requirements
        if any(sym not in candles_1h or sym not in candles_4h for sym in proxies):
            # Không đủ dữ liệu regime => giữ NORMAL để không block vô lý
            self.regime = "NORMAL"
            self.panic = False
            self.last_reason = "missing proxies data"
            return RegimeResult(self.regime, self.panic, 1.0, self.last_reason)

        # thresholds (safe defaults, không cần thêm env nếu bạn chưa muốn)
        PANIC_ATR_RATIO = float(_get("PANIC_ATR_RATIO", 1.6))          # ATR5/ATR20 (1H)
        PANIC_DROP_PCT = float(_get("PANIC_DROP_PCT", 0.03))          # 1H đỏ > 3%
        RECOVERY_ATR_RATIO = float(_get("RECOVERY_ATR_RATIO", 1.15))  # hạ nhiệt vol
        TREND_EMA_FAST = int(_get("TREND_EMA_FAST", 20))
        TREND_EMA_SLOW = int(_get("TREND_EMA_SLOW", 50))
        TREND_GAP_MIN = float(_get("TREND_GAP_MIN", 0.0015))          # gap trên 4H
        RANGE_ATR_MAX = float(_get("RANGE_ATR_MAX", 0.006))           # ATR% 4H thấp => range
        RANGE_GAP_MAX = float(_get("RANGE_GAP_MAX", 0.0010))          # EMA gap thấp

        # --- Panic checks (1H): ATR ratio + dump candle
        atr_ratios = []
        drop_flags = []
        for sym in proxies:
            c1 = candles_1h[sym]
            atr5 = self._atr_pct(c1, 5)
            atr20 = self._atr_pct(c1, 20)
            if atr5 is None or atr20 is None or atr20 == 0:
                continue
            atr_ratios.append(atr5 / atr20)

            last = c1[-1]
            o = last["open"]
            cl = last["close"]
            if o and (cl - o) / o <= -PANIC_DROP_PCT:
                drop_flags.append(True)
            else:
                drop_flags.append(False)

        atr_ratio = max(atr_ratios) if atr_ratios else 0.0
        dump = any(drop_flags) if drop_flags else False

        panic_now = (atr_ratio >= PANIC_ATR_RATIO) or dump

        # --- Recovery check: từ PANIC chuyển sang RECOVERY khi vol hạ + có nến hồi
        if self.regime == "PANIC":
            # recovery: atr_ratio đã hạ + BTC/ETH có candle xanh (1H)
            green_ok = True
            for sym in proxies:
                last = candles_1h[sym][-1]
                if last["close"] <= last["open"]:
                    green_ok = False
                    break
            if (atr_ratio > 0 and atr_ratio <= RECOVERY_ATR_RATIO) and green_ok:
                self.regime = "RECOVERY"
                self.panic = False
                self.last_reason = f"recovery: atr_ratio={atr_ratio:.2f}, green_ok={green_ok}"
                return RegimeResult(self.regime, self.panic, 0.5, self.last_reason)

        if panic_now:
            self.regime = "PANIC"
            self.panic = True
            self.last_reason = f"panic: atr_ratio={atr_ratio:.2f}, dump={dump}"
            return RegimeResult(self.regime, self.panic, 0.0, self.last_reason)

        # --- Trend / Range (4H)
        gaps = []
        dirs = []
        atr4s = []
        for sym in proxies:
            c4 = candles_4h[sym]
            gap = self._ema_gap(c4, TREND_EMA_FAST, TREND_EMA_SLOW)
            d = self._trend_dir(c4, TREND_EMA_FAST, TREND_EMA_SLOW)
            atr4 = self._atr_pct(c4, 14)  # ATR14% 4H
            if gap is not None:
                gaps.append(gap)
            if d is not None:
                dirs.append(d)
            if atr4 is not None:
                atr4s.append(atr4)

        gap_avg = sum(gaps) / len(gaps) if gaps else 0.0
        atr4_avg = sum(atr4s) / len(atr4s) if atr4s else 0.0
        same_dir = (len(set(dirs)) == 1) if dirs else False

        # RANGE: vol thấp + ema gap thấp
        if atr4_avg > 0 and atr4_avg <= RANGE_ATR_MAX and gap_avg <= RANGE_GAP_MAX:
            self.regime = "RANGE"
            self.panic = False
            self.last_reason = f"range: atr4%={atr4_avg:.4f}, gap={gap_avg:.4f}"
            return RegimeResult(self.regime, self.panic, 0.7, self.last_reason)

        # TREND: gap đủ + BTC/ETH cùng hướng
        if gap_avg >= TREND_GAP_MIN and same_dir:
            self.regime = "TREND"
            self.panic = False
            self.last_reason = f"trend: dir={dirs[0] if dirs else 'NA'}, gap={gap_avg:.4f}"
            return RegimeResult(self.regime, self.panic, 1.0, self.last_reason)

        # NORMAL default
        self.regime = "NORMAL"
        self.panic = False
        self.last_reason = f"normal: atr_ratio={atr_ratio:.2f}, gap={gap_avg:.4f}"
        return RegimeResult(self.regime, self.panic, 1.0, self.last_reason)
