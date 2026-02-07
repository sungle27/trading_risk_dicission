from __future__ import annotations

from app.config import CFG
from app.indicators import wick_ratio, momentum, ATR


def pick_thresholds(mode: str) -> dict:
    if mode == "early":
        return {
            "ema_gap": CFG.REGIME_EMA_GAP_EARLY,
            "vol_ratio": CFG.VOLUME_RATIO_EARLY,
            "wick_max": CFG.WICK_MAX_RATIO_EARLY,
            "mom_min": CFG.MOMENTUM_MIN_EARLY,
            "spread_max": CFG.SPREAD_MAX_EARLY,
            "cooldown": CFG.COOLDOWN_SEC_EARLY,
        }

    return {
        "ema_gap": CFG.REGIME_EMA_GAP_MAIN,
        "vol_ratio": CFG.VOLUME_RATIO_MAIN,
        "wick_max": CFG.WICK_MAX_RATIO_MAIN,
        "mom_min": CFG.MOMENTUM_MIN_MAIN,
        "spread_max": CFG.SPREAD_MAX_MAIN,
        "cooldown": CFG.COOLDOWN_SEC_MAIN,
    }


def filter_wick(candle: dict, mode: str) -> bool:
    if not CFG.ENABLE_WICK_FILTER:
        return True
    th = pick_thresholds(mode)
    return wick_ratio(candle) <= th["wick_max"]


def filter_momentum(candle: dict, mode: str) -> bool:
    if not CFG.ENABLE_MOMENTUM:
        return True
    th = pick_thresholds(mode)
    return momentum(candle) >= th["mom_min"]


def atr_compression(candles: list[dict]) -> tuple[bool, float | None, float | None, float | None]:
    """
    returns (ok, atr_short_pct, atr_long_pct, squeeze_ratio)
    ATR values are returned as % of close for readability.
    """
    if not CFG.ENABLE_ATR_COMPRESSION:
        return True, None, None, None

    if len(candles) < CFG.ATR_LONG + 2:
        return False, None, None, None

    atr_s = ATR(CFG.ATR_SHORT)
    atr_l = ATR(CFG.ATR_LONG)

    a_s = a_l = None
    last_close = float(candles[-1]["close"])

    for c in candles[-(CFG.ATR_LONG + 2):]:
        a_s = atr_s.update(float(c["high"]), float(c["low"]), float(c["close"]))
        a_l = atr_l.update(float(c["high"]), float(c["low"]), float(c["close"]))

    if a_s is None or a_l is None or a_l == 0 or last_close == 0:
        return False, None, None, None

    squeeze_ok = a_s < CFG.ATR_COMPRESSION_RATIO * a_l
    atr_s_pct = a_s / last_close
    atr_l_pct = a_l / last_close
    ratio = a_s / a_l
    return squeeze_ok, atr_s_pct, atr_l_pct, ratio
