from __future__ import annotations

import os
from dataclasses import dataclass

# Auto-load .env if python-dotenv exists
try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(), override=True)
except Exception:
    pass


def _get(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    if v is None:
        return default
    v = v.strip()
    return v if v != "" else default


def _req(key: str) -> str:
    v = _get(key)
    if v is None:
        raise RuntimeError(f"[CONFIG] Missing env var: {key}")
    return v


def _i(key: str, default: int) -> int:
    v = _get(key, None)
    return int(v) if v is not None else int(default)


def _f(key: str, default: float) -> float:
    v = _get(key, None)
    return float(v) if v is not None else float(default)


@dataclass(frozen=True)
class Config:
    # Required core
    BINANCE_FUTURES_WS: str = _req("BINANCE_FUTURES_WS")
    TELEGRAM_BOT_TOKEN: str = _req("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: str = _req("TELEGRAM_CHAT_ID")

    # Runtime
    DEBUG_ENABLED: int = _i("DEBUG_ENABLED", 0)
    HEARTBEAT_SEC: int = _i("HEARTBEAT_SEC", 300)

    # EMA
    EMA_FAST: int = _i("EMA_FAST", 9)
    EMA_SLOW: int = _i("EMA_SLOW", 26)

    # EMA GAP
    REGIME_EMA_GAP_EARLY: float = _f("REGIME_EMA_GAP_EARLY", 0.0010)
    REGIME_EMA_GAP_MAIN: float = _f("REGIME_EMA_GAP", 0.0055)

    # Volume
    VOLUME_SMA_LEN: int = _i("VOLUME_SMA_LEN", 12)
    VOLUME_RATIO_EARLY: float = _f("VOLUME_RATIO_EARLY", 2.0)
    VOLUME_RATIO_MAIN: float = _f("VOLUME_RATIO_MAIN", 2.6)
    ENABLE_VOLUME: int = _i("ENABLE_VOLUME", 1)

    # Spread
    ENABLE_SPREAD: int = _i("ENABLE_SPREAD", 1)
    SPREAD_MAX: float = _f("SPREAD_MAX", 0.004)
    ENABLE_SPREAD_ADVANCED: int = _i("ENABLE_SPREAD_ADVANCED", 1)
    SPREAD_MAX_EARLY: float = _f("SPREAD_MAX_EARLY", 0.006)
    SPREAD_MAX_MAIN: float = _f("SPREAD_MAX_MAIN", 0.003)

    # Cooldown
    COOLDOWN_SEC_EARLY: int = _i("COOLDOWN_SEC_EARLY", 180)
    COOLDOWN_SEC_MAIN: int = _i("COOLDOWN_SEC_MAIN", 1200)

    # Filters
    ENABLE_ANTI_TRAP: int = _i("ENABLE_ANTI_TRAP", 1)
    ENABLE_FOLLOW_THROUGH: int = _i("ENABLE_FOLLOW_THROUGH", 1)
    FOLLOW_THROUGH_BARS_EARLY: int = _i("FOLLOW_THROUGH_BARS_EARLY", 2)
    FOLLOW_THROUGH_BARS_MAIN: int = _i("FOLLOW_THROUGH_BARS_MAIN", 4)

    ENABLE_WICK_FILTER: int = _i("ENABLE_WICK_FILTER", 1)
    WICK_MAX_RATIO_EARLY: float = _f("WICK_MAX_RATIO_EARLY", 0.45)
    WICK_MAX_RATIO_MAIN: float = _f("WICK_MAX_RATIO_MAIN", 0.30)

    ENABLE_MOMENTUM: int = _i("ENABLE_MOMENTUM", 1)
    MOMENTUM_MIN_EARLY: float = _f("MOMENTUM_MIN_EARLY", 0.003)
    MOMENTUM_MIN_MAIN: float = _f("MOMENTUM_MIN_MAIN", 0.007)

    # ATR compression
    ENABLE_ATR_COMPRESSION: int = _i("ENABLE_ATR_COMPRESSION", 1)
    ATR_SHORT: int = _i("ATR_SHORT", 5)
    ATR_LONG: int = _i("ATR_LONG", 20)
    ATR_COMPRESSION_RATIO: float = _f("ATR_COMPRESSION_RATIO", 0.70)

    # Regime config
    ENABLE_MARKET_REGIME: int = _i("ENABLE_MARKET_REGIME", 1)
    REGIME_PROXY_1: str = _get("REGIME_PROXY_1", "BTCUSDT")  # safe default
    REGIME_PROXY_2: str = _get("REGIME_PROXY_2", "ETHUSDT")

    PANIC_DROP_1H: float = _f("PANIC_DROP_1H", -0.04)
    PANIC_RISE_1H: float = _f("PANIC_RISE_1H", 0.02)
    RECOVERY_BARS_1H: int = _i("RECOVERY_BARS_1H", 2)

    TREND_EMA_GAP_4H: float = _f("TREND_EMA_GAP_4H", 0.0025)
    RANGE_ATR_RATIO_MAX: float = _f("RANGE_ATR_RATIO_MAX", 0.70)

    REGIME_MIN_HOLD_SEC: int = _i("REGIME_MIN_HOLD_SEC", 1800)
    REGIME_ALERT_COOLDOWN_SEC: int = _i("REGIME_ALERT_COOLDOWN_SEC", 900)
    REGIME_NOTIFY: int = _i("REGIME_NOTIFY", 1)

    # Portfolio / risk
    NAV_USD: float = _f("NAV_USD", 10000.0)
    MAX_POSITIONS: int = _i("MAX_POSITIONS", 8)
    MAX_TOTAL_RISK_PCT: float = _f("MAX_TOTAL_RISK_PCT", 3.0)
    MAX_CORRELATION: float = _f("MAX_CORRELATION", 0.85)

    # Simulation
    SIM_ENABLED: int = _i("SIM_ENABLED", 1)
    SIM_START_NAV: float = _f("SIM_START_NAV", 10000.0)
    SIM_RR: float = _f("SIM_RR", 2.0)

    NAV_REPORT_SEC: int = _i("NAV_REPORT_SEC", 3600)

    # Risk per trade logic
    RISK_EARLY: float = _f("RISK_EARLY", 0.25)
    RISK_MAIN: float = _f("RISK_MAIN", 0.50)
    RISK_MAX: float = _f("RISK_MAX", 1.0)

    SL_ATR_MULT: float = _f("SL_ATR_MULT", 1.5)
    TP_RR: float = _f("TP_RR", 2.0)

    # Liquidity / vol sizing
    MIN_LIQUIDITY_USD: float = _f("MIN_LIQUIDITY_USD", 5_000_000.0)
    TARGET_VOL_PCT: float = _f("TARGET_VOL_PCT", 0.015)

    # Entry mode
    ENTRY_MODE: str = _get("ENTRY_MODE", "adaptive")
    ENTRY_PULLBACK_PCT: float = _f("ENTRY_PULLBACK_PCT", 0.003)
    ENTRY_BREAKOUT_PCT: float = _f("ENTRY_BREAKOUT_PCT", 0.0015)

    # Slippage
    SLIPPAGE_PCT: float = _f("SLIPPAGE_PCT", 0.0002)  # 2 bps default

    # Drawdown
    DD_SOFT_PCT: float = _f("DD_SOFT_PCT", 0.06)
    DD_HARD_PCT: float = _f("DD_HARD_PCT", 0.10)
    DD_KILL_PCT: float = _f("DD_KILL_PCT", 0.18)
    DD_HARD_COOLDOWN_SEC: int = _i("DD_HARD_COOLDOWN_SEC", 6 * 60 * 60)


CFG = Config()
