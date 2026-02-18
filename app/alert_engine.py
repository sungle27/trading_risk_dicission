from __future__ import annotations

import time

from app.config import CFG
from app.filters import (
    pick_thresholds,
    filter_wick,
    filter_momentum,
    atr_compression,
)

_last_alert_time = {"early": 0.0, "main": 0.0}


def _get(name: str, default):
    return getattr(CFG, name, default)


# ============================================================
# BREAKOUT LEVEL CHECK
# ============================================================
def breakout_level(candles: list[dict], lookback: int = 20) -> bool:
    """
    Breakout thật = close phá high/low lookback gần nhất
    """
    if len(candles) < lookback + 1:
        return False

    last = candles[-1]
    highs = [c["high"] for c in candles[-lookback - 1 : -1]]
    lows = [c["low"] for c in candles[-lookback - 1 : -1]]

    return (last["close"] > max(highs)) or (last["close"] < min(lows))


# ============================================================
# SCORE ENGINE (giữ logic cũ)
# ============================================================
def score_signal(
    symbol: str,
    candles: list[dict],
    volumes: list[float],
    spread: float,
    mode: str,
) -> tuple[int, dict]:

    th = pick_thresholds(mode)
    score = 0
    reasons: dict = {}

    last = candles[-1]

    # ========================================================
    # EMA GAP
    # ========================================================
    ema_fast = candles[-1]["close"]
    ema_slow = candles[-2]["close"]
    gap = abs(ema_fast - ema_slow) / ema_slow if ema_slow else 0.0

    reasons["ema_gap"] = gap
    if gap >= th["ema_gap"]:
        score += 2

    # ========================================================
    # VOLUME SPIKE (MANDATORY)
    # ========================================================
    if len(volumes) < CFG.VOLUME_SMA_LEN:
        return 0, reasons

    avg = sum(volumes[-CFG.VOLUME_SMA_LEN :]) / CFG.VOLUME_SMA_LEN
    vol_ratio = volumes[-1] / max(avg, 1e-9)

    reasons["volume_ratio"] = vol_ratio

    # Volume bắt buộc theo env
    if vol_ratio < th["vol_ratio"]:
        return 0, reasons

    score += 3

    # ========================================================
    # WICK FILTER
    # ========================================================
    wick_ok = filter_wick(last, mode)
    reasons["wick_ok"] = wick_ok
    if wick_ok:
        score += 2

    # ========================================================
    # MOMENTUM FILTER
    # ========================================================
    mom_ok = filter_momentum(last, mode)
    reasons["momentum_ok"] = mom_ok
    if mom_ok:
        score += 2

    # ========================================================
    # ATR COMPRESSION (MAIN ONLY)
    # ========================================================
    squeeze_ok = True
    if mode == "main" and int(_get("ENABLE_ATR_COMPRESSION", 0)):
        squeeze_ok = atr_compression(candles)
        if squeeze_ok:
            score += 2
    reasons["atr_squeeze"] = squeeze_ok

    # ========================================================
    # BREAKOUT LEVEL (High/Low 20)
    # ========================================================
    breakout_ok = breakout_level(candles, 20)
    reasons["breakout_highlow"] = breakout_ok
    if breakout_ok:
        score += 3

    # ========================================================
    # SPREAD FILTER
    # ========================================================
    spread_ok = spread <= th["spread_max"]
    reasons["spread"] = spread
    reasons["spread_ok"] = spread_ok
    if spread_ok:
        score += 1

    return score, reasons


# ============================================================
# MAIN SIGNAL CHECK + REGIME GATE (mới)
# ============================================================
def check_signal(
    symbol: str,
    candles: list[dict],
    volumes: list[float],
    spread: float,
    mode: str = "early",
    market_regime: str = "NORMAL",   # NEW
    market_panic: bool = False,      # NEW
):

    now = time.time()
    th = pick_thresholds(mode)

    # ========================================================
    # COOLDOWN
    # ========================================================
    if now - _last_alert_time[mode] < th["cooldown"]:
        return None

    if len(candles) < 30:
        return None

    # ========================================================
    # DIRECTION
    # ========================================================
    last = candles[-1]
    direction = "LONG" if last["close"] > last["open"] else "SHORT"

    # ========================================================
    # REGIME HARD GATES (anti market crash)
    # ========================================================
    # PANIC: chặn toàn bộ LONG, EARLY tắt, MAIN chỉ cho SHORT thật chọn lọc
    if market_panic or market_regime == "PANIC":
        if direction == "LONG":
            return None
        if mode == "early":
            return None  # panic thì early bỏ luôn (đỡ nhiễu / đỡ bắt dao rơi)

    # RECOVERY: hạn chế short mạnh tay + hạn chế early (tránh whipsaw)
    if market_regime == "RECOVERY":
        if mode == "early":
            return None  # recovery chỉ quan sát MAIN để chắc tay hơn
        # recovery ưu tiên LONG; SHORT chỉ nếu cực mạnh
        if direction == "SHORT":
            pass  # xử lý bằng threshold dưới (tăng điểm yêu cầu)

    # RANGE: giảm nhiễu (early dễ fake)
    if market_regime == "RANGE" and mode == "early":
        return None

    # ========================================================
    # SCORE
    # ========================================================
    score, meta = score_signal(symbol, candles, volumes, spread, mode)

    # ========================================================
    # THRESHOLD BY MODE (cũ)
    # ========================================================
    base_early = int(_get("SCORE_MIN_EARLY", 6))
    base_main = int(_get("SCORE_MIN_MAIN", 10))

    min_score = base_early if mode == "early" else base_main

    # ========================================================
    # REGIME SOFT GATES (tăng min_score)
    # ========================================================
    if market_regime == "RANGE":
        if mode == "main":
            min_score += 1

    if market_regime == "RECOVERY":
        if mode == "main":
            min_score += 1
            if direction == "SHORT":
                min_score += 2

    if market_regime == "PANIC":
        # MAIN SHORT phải “cứng” hơn
        if mode == "main":
            min_score = max(min_score, int(_get("SCORE_MIN_MAIN_PANIC", 13)))

            # panic short: bắt buộc có breakout + (nếu bật ATR squeeze) thì squeeze phải ok
            if not meta.get("breakout_highlow", False):
                return None
            if int(_get("ENABLE_ATR_COMPRESSION", 0)) and (not meta.get("atr_squeeze", True)):
                return None

    if score < min_score:
        return None

    # ========================================================
    # HIGH CONFIDENCE
    # ========================================================
    high_conf = score >= int(_get("SCORE_HIGH_CONF", 14))

    # Save cooldown timestamp
    _last_alert_time[mode] = now

    return {
        "symbol": symbol,
        "mode": mode,
        "direction": direction,
        "score": score,
        "high_conf": high_conf,
        "market_regime": market_regime,
        "market_panic": bool(market_panic),
        **meta,
    }
