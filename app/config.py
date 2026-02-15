from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# ============================================================
# Load .env (ROBUST, CROSS-PLATFORM)
# ============================================================
try:
    from dotenv import load_dotenv  # type: ignore

    # project_root/app/config.py → project_root/.env
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    ENV_PATH = PROJECT_ROOT / ".env"

    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
    else:
        print(f"[WARN] .env not found at {ENV_PATH}")

except Exception as e:
    print(f"[WARN] dotenv load failed: {e}")


# ============================================================
# Helpers (STRICT)
# ============================================================
def _require(key: str) -> str:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        raise RuntimeError(f"[CONFIG] Missing env var: {key}")
    return val.strip()


def _i(key: str) -> int:
    return int(_require(key))


def _f(key: str) -> float:
    return float(_require(key))


# ============================================================
# CONFIG
# ============================================================
@dataclass(frozen=True)
class Config:
    # ================= CORE =================
    BINANCE_FUTURES_WS: str = _require("BINANCE_FUTURES_WS")

    TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: str = _require("TELEGRAM_CHAT_ID")

    DEBUG_ENABLED: int = _i("DEBUG_ENABLED")
    HEARTBEAT_SEC: int = _i("HEARTBEAT_SEC")

    # ================= EMA / REGIME =================
    EMA_FAST: int = _i("EMA_FAST")
    EMA_SLOW: int = _i("EMA_SLOW")

    REGIME_EMA_GAP_EARLY: float = _f("REGIME_EMA_GAP_EARLY")
    REGIME_EMA_GAP_MAIN: float = _f("REGIME_EMA_GAP")

    # ================= VOLUME =================
    VOLUME_SMA_LEN: int = _i("VOLUME_SMA_LEN")
    VOLUME_RATIO_EARLY: float = _f("VOLUME_RATIO_EARLY")
    VOLUME_RATIO_MAIN: float = _f("VOLUME_RATIO_MAIN")
    ENABLE_VOLUME: int = _i("ENABLE_VOLUME")

    # ================= SPREAD =================
    ENABLE_SPREAD: int = _i("ENABLE_SPREAD")
    SPREAD_MAX: float = _f("SPREAD_MAX")

    ENABLE_SPREAD_ADVANCED: int = _i("ENABLE_SPREAD_ADVANCED")
    SPREAD_MAX_EARLY: float = _f("SPREAD_MAX_EARLY")
    SPREAD_MAX_MAIN: float = _f("SPREAD_MAX_MAIN")

    # ================= COOLDOWN =================
    COOLDOWN_SEC_EARLY: int = _i("COOLDOWN_SEC_EARLY")
    COOLDOWN_SEC_MAIN: int = _i("COOLDOWN_SEC_MAIN")

    # ================= FILTERS =================
    ENABLE_ANTI_TRAP: int = _i("ENABLE_ANTI_TRAP")

    ENABLE_FOLLOW_THROUGH: int = _i("ENABLE_FOLLOW_THROUGH")
    FOLLOW_THROUGH_BARS_EARLY: int = _i("FOLLOW_THROUGH_BARS_EARLY")
    FOLLOW_THROUGH_BARS_MAIN: int = _i("FOLLOW_THROUGH_BARS_MAIN")

    ENABLE_WICK_FILTER: int = _i("ENABLE_WICK_FILTER")
    WICK_MAX_RATIO_EARLY: float = _f("WICK_MAX_RATIO_EARLY")
    WICK_MAX_RATIO_MAIN: float = _f("WICK_MAX_RATIO_MAIN")

    ENABLE_MOMENTUM: int = _i("ENABLE_MOMENTUM")
    MOMENTUM_MIN_EARLY: float = _f("MOMENTUM_MIN_EARLY")
    MOMENTUM_MIN_MAIN: float = _f("MOMENTUM_MIN_MAIN")

    # ================= ATR COMPRESSION =================
    ENABLE_ATR_COMPRESSION: int = _i("ENABLE_ATR_COMPRESSION")
    ATR_SHORT: int = _i("ATR_SHORT")
    ATR_LONG: int = _i("ATR_LONG")
    ATR_COMPRESSION_RATIO: float = _f("ATR_COMPRESSION_RATIO")

    # ================= MARKET REGIME =================
    ENABLE_MARKET_REGIME: int = _i("ENABLE_MARKET_REGIME")
    REGIME_PROXY_1: str = _require("REGIME_PROXY_1")
    REGIME_PROXY_2: str = _require("REGIME_PROXY_2")

    PANIC_DROP_1H: float = _f("PANIC_DROP_1H")
    PANIC_RISE_1H: float = _f("PANIC_RISE_1H")
    RECOVERY_BARS_1H: int = _i("RECOVERY_BARS_1H")

    TREND_EMA_GAP_4H: float = _f("TREND_EMA_GAP_4H")
    RANGE_ATR_RATIO_MAX: float = _f("RANGE_ATR_RATIO_MAX")

    REGIME_MIN_HOLD_SEC: int = _i("REGIME_MIN_HOLD_SEC")
    REGIME_ALERT_COOLDOWN_SEC: int = _i("REGIME_ALERT_COOLDOWN_SEC")

    # ================= ALERT MODES =================
    ALERT_MODE_DECISION: int = _i("ALERT_MODE_DECISION")
    ALERT_MODE_EXECUTION: int = _i("ALERT_MODE_EXECUTION")

       # ====================================================
    # CAPITAL / RISK MANAGEMENT
    # ====================================================
    NAV_USD: float = _f("NAV_USD")

    MAX_POSITIONS: int = _i("MAX_POSITIONS")
    MAX_TOTAL_RISK_PCT: float = _f("MAX_TOTAL_RISK_PCT")

    RISK_PER_TRADE_PCT: float = _f("RISK_PER_TRADE_PCT")
CFG = Config()

# ================= Liquidity =================
MIN_LIQUIDITY_USD: float = _f("MIN_LIQUIDITY_USD")

# ================= Volatility sizing =================
TARGET_VOL_PCT: float = _f("TARGET_VOL_PCT")

# ================= Correlation =================
MAX_CORRELATION: float = _f("MAX_CORRELATION")