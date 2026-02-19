from __future__ import annotations

import os
from dataclasses import dataclass

# ============================================================
# Load .env (AUTO, SAFE)
# ============================================================
try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(), override=True)
except Exception as e:
    # Don't crash if python-dotenv is missing
    print(f"[WARN] dotenv load failed: {e}")

def _require(key: str) -> str:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        raise RuntimeError(f"[CONFIG] Missing env var: {key}")
    return val.strip()

def _get(key: str, default: str) -> str:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return val.strip()

def _oi(key: str, default: int) -> int:
    return int(_get(key, str(default)))

def _of(key: str, default: float) -> float:
    return float(_get(key, str(default)))

@dataclass(frozen=True)
class Config:
    # ================= CORE =================
    BINANCE_FUTURES_WS: str = _require("BINANCE_FUTURES_WS")
    TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: str = _require("TELEGRAM_CHAT_ID")

    DEBUG_ENABLED: int = _oi("DEBUG_ENABLED", 0)
    HEARTBEAT_SEC: int = _oi("HEARTBEAT_SEC", 30)

    # ================= EMA + GAP =================
    EMA_FAST: int = _oi("EMA_FAST", 20)
    EMA_SLOW: int = _oi("EMA_SLOW", 50)

    REGIME_EMA_GAP_EARLY: float = _of("REGIME_EMA_GAP_EARLY", 0.0030)
    REGIME_EMA_GAP_MAIN: float = _of("REGIME_EMA_GAP_MAIN", _of("REGIME_EMA_GAP", 0.0040))

    # ================= VOLUME =================
    VOLUME_SMA_LEN: int = _oi("VOLUME_SMA_LEN", 20)
    VOLUME_RATIO_EARLY: float = _of("VOLUME_RATIO_EARLY", 2.5)
    VOLUME_RATIO_MAIN: float = _of("VOLUME_RATIO_MAIN", 3.0)
    ENABLE_VOLUME: int = _oi("ENABLE_VOLUME", 1)

    # ================= SPREAD =================
    ENABLE_SPREAD: int = _oi("ENABLE_SPREAD", 1)
    SPREAD_MAX: float = _of("SPREAD_MAX", 0.0020)

    ENABLE_SPREAD_ADVANCED: int = _oi("ENABLE_SPREAD_ADVANCED", 1)
    SPREAD_MAX_EARLY: float = _of("SPREAD_MAX_EARLY", 0.0025)
    SPREAD_MAX_MAIN: float = _of("SPREAD_MAX_MAIN", 0.0018)

    # ================= TIMEFRAMES =================
    MAIN_TF_SEC: int = _oi("MAIN_TF_SEC", 15 * 60)

    # ================= COOLDOWN =================
    COOLDOWN_SEC_EARLY: int = _oi("COOLDOWN_SEC_EARLY", 60)
    COOLDOWN_SEC_MAIN: int = _oi("COOLDOWN_SEC_MAIN", 60)

    # ================= FILTERS =================
    ENABLE_ANTI_TRAP: int = _oi("ENABLE_ANTI_TRAP", 1)

    ENABLE_FOLLOW_THROUGH: int = _oi("ENABLE_FOLLOW_THROUGH", 1)
    FOLLOW_THROUGH_BARS_EARLY: int = _oi("FOLLOW_THROUGH_BARS_EARLY", 1)
    FOLLOW_THROUGH_BARS_MAIN: int = _oi("FOLLOW_THROUGH_BARS_MAIN", 1)

    ENABLE_WICK_FILTER: int = _oi("ENABLE_WICK_FILTER", 1)
    WICK_MAX_RATIO_EARLY: float = _of("WICK_MAX_RATIO_EARLY", 0.55)
    WICK_MAX_RATIO_MAIN: float = _of("WICK_MAX_RATIO_MAIN", 0.45)

    ENABLE_MOMENTUM: int = _oi("ENABLE_MOMENTUM", 1)
    MOMENTUM_MIN_EARLY: float = _of("MOMENTUM_MIN_EARLY", 0.60)
    MOMENTUM_MIN_MAIN: float = _of("MOMENTUM_MIN_MAIN", 0.70)

    # ================= ATR COMPRESSION =================
    ENABLE_ATR_COMPRESSION: int = _oi("ENABLE_ATR_COMPRESSION", 1)
    ATR_SHORT: int = _oi("ATR_SHORT", 14)
    ATR_LONG: int = _oi("ATR_LONG", 50)
    ATR_COMPRESSION_RATIO: float = _of("ATR_COMPRESSION_RATIO", 0.75)

    # ================= MARKET REGIME =================
    ENABLE_MARKET_REGIME: int = _oi("ENABLE_MARKET_REGIME", 1)
    REGIME_PROXY_1: str = _get("REGIME_PROXY_1", "BTCUSDT")
    REGIME_PROXY_2: str = _get("REGIME_PROXY_2", "ETHUSDT")

    PANIC_DROP_1H: float = _of("PANIC_DROP_1H", -0.03)
    PANIC_RISE_1H: float = _of("PANIC_RISE_1H", 0.03)
    RECOVERY_BARS_1H: int = _oi("RECOVERY_BARS_1H", 6)

    TREND_EMA_GAP_4H: float = _of("TREND_EMA_GAP_4H", 0.0060)
    RANGE_ATR_RATIO_MAX: float = _of("RANGE_ATR_RATIO_MAX", 0.60)

    REGIME_MIN_HOLD_SEC: int = _oi("REGIME_MIN_HOLD_SEC", 60 * 20)
    REGIME_ALERT_COOLDOWN_SEC: int = _oi("REGIME_ALERT_COOLDOWN_SEC", 60 * 10)
    REGIME_NOTIFY: int = _oi("REGIME_NOTIFY", 1)

    # ====================================================
    # CAPITAL / RISK MANAGEMENT
    # ====================================================
    NAV_USD: float = _of("NAV_USD", 10000.0)
    MAX_POSITIONS: int = _oi("MAX_POSITIONS", 8)
    MAX_TOTAL_RISK_PCT: float = _of("MAX_TOTAL_RISK_PCT", 3.0)
    MAX_CORRELATION: float = _of("MAX_CORRELATION", 0.85)

    # Base risk per trade (percent of NAV)
    RISK_PER_TRADE_PCT: float = _of("RISK_PER_TRADE_PCT", 0.25)

    # ATR-based stop distance (multipliers)
    SL_ATR_MULT_MAIN: float = _of("SL_ATR_MULT_MAIN", 1.0)
    SL_ATR_MULT_EARLY: float = _of("SL_ATR_MULT_EARLY", 0.9)

    # Liquidity filter (approx USD/candle)
    MIN_LIQUIDITY_USD: float = _of("MIN_LIQUIDITY_USD", 25_000.0)

    # Volatility-adjusted risk sizing
    ENABLE_VOL_ADJ_SIZING: int = _oi("ENABLE_VOL_ADJ_SIZING", 1)
    TARGET_VOL_PCT: float = _of("TARGET_VOL_PCT", 0.010)  # ATR% target (1%)

    # Slippage model (bps)
    ENTRY_SLIPPAGE_BPS_MAIN: float = _of("ENTRY_SLIPPAGE_BPS_MAIN", 2.0)
    ENTRY_SLIPPAGE_BPS_EARLY: float = _of("ENTRY_SLIPPAGE_BPS_EARLY", 4.0)

    # Entry confirmation: wait for price move after signal
    ENTRY_CONFIRM_MIN_PCT: float = _of("ENTRY_CONFIRM_MIN_PCT", 0.0003)
    ENTRY_CONFIRM_MAX_PCT: float = _of("ENTRY_CONFIRM_MAX_PCT", 0.0015)

    # SIM
    SIM_ENABLED: int = _oi("SIM_ENABLED", 1)
    SIM_START_NAV: float = _of("SIM_START_NAV", 10000.0)
    SIM_RR: float = _of("SIM_RR", 2.0)
    NAV_REPORT_SEC: int = _oi("NAV_REPORT_SEC", 60 * 60)

CFG = Config()
