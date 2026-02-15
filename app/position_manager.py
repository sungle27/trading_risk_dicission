from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ============================================================
# Position Model
# ============================================================

@dataclass
class Position:
    symbol: str
    direction: str
    qty: float
    entry: float
    sl: float
    tp: Optional[float]
    opened_at: float
    risk_usd: float
    price_history: List[float] = field(default_factory=list)


# ============================================================
# Position Manager
# ============================================================

class PositionManager:

    def __init__(
        self,
        *,
        nav_usd: float = 0.0,
        max_positions: int = 10,
        max_total_risk_pct: Optional[float] = None,
        max_total_risk_usd: Optional[float] = None,
        max_correlation: Optional[float] = None,
        cfg=None,
    ):
        self.nav_usd = float(nav_usd)
        self.max_positions = int(max_positions)

        # Active positions
        self.positions: Dict[str, Position] = {}

        # -----------------------------
        # Risk limit config
        # -----------------------------
        if max_total_risk_pct is None and cfg is not None:
            max_total_risk_pct = getattr(cfg, "MAX_TOTAL_RISK_PCT", None)

        self.max_total_risk_pct = (
            float(max_total_risk_pct) if max_total_risk_pct is not None else None
        )

        self.max_total_risk_usd = (
            float(max_total_risk_usd) if max_total_risk_usd is not None else None
        )

        # -----------------------------
        # Correlation limit
        # -----------------------------
        if max_correlation is None and cfg is not None:
            max_correlation = getattr(cfg, "MAX_CORRELATION", None)

        self.max_correlation = (
            float(max_correlation) if max_correlation is not None else None
        )

    # ============================================================
    # NAV & Risk
    # ============================================================

    def update_nav(self, nav_usd: float) -> None:
        self.nav_usd = float(nav_usd)

    def total_risk_usd(self) -> float:
        return sum(p.risk_usd for p in self.positions.values())

    def risk_limit_usd(self) -> Optional[float]:
        if self.max_total_risk_pct is not None and self.nav_usd > 0:
            return self.nav_usd * (self.max_total_risk_pct / 100.0)

        if self.max_total_risk_usd is not None:
            return self.max_total_risk_usd

        return None

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    # ============================================================
    # Correlation Filter
    # ============================================================

    def _corr_ok(
        self,
        new_prices: Optional[List[float]],
    ) -> Tuple[bool, str]:

        if self.max_correlation is None:
            return True, "ok"

        if not new_prices or len(new_prices) < 20:
            return True, "ok"

        try:
            from app.correlation_engine import correlation
        except Exception:
            return True, "ok"

        for p in self.positions.values():
            if not p.price_history or len(p.price_history) < 20:
                continue

            try:
                c = correlation(new_prices, p.price_history)
            except Exception:
                continue

            if c >= self.max_correlation:
                return False, f"correlation_block({p.symbol},{c:.2f})"

        return True, "ok"

    # ============================================================
    # Gatekeeping
    # ============================================================

    def can_open(
        self,
        *,
        symbol: str,
        risk_usd: float,
        new_prices: Optional[List[float]] = None,
    ) -> Tuple[bool, str]:

        if self.has_position(symbol):
            return False, "position_exists"

        if len(self.positions) >= self.max_positions:
            return False, "max_positions_reached"

        # Risk limit check
        limit_usd = self.risk_limit_usd()
        if limit_usd is not None:
            projected = self.total_risk_usd() + float(risk_usd)
            if projected > limit_usd:
                return False, "max_total_risk_reached"

        # Correlation check
        ok_corr, reason = self._corr_ok(new_prices)
        if not ok_corr:
            return False, reason

        return True, "ok"

    # ============================================================
    # Open / Close
    # ============================================================

    def open_position(
        self,
        *,
        symbol: str,
        direction: str,
        qty: float,
        entry: float,
        sl: float,
        tp: Optional[float],
        risk_usd: float,
        price_history: Optional[List[float]] = None,
    ) -> None:

        self.positions[symbol] = Position(
            symbol=symbol,
            direction=direction,
            qty=float(qty),
            entry=float(entry),
            sl=float(sl),
            tp=float(tp) if tp is not None else None,
            opened_at=time.time(),
            risk_usd=float(risk_usd),
            price_history=list(price_history) if price_history else [],
        )

    def close_position(self, symbol: str) -> None:
        if symbol in self.positions:
            del self.positions[symbol]

    # ============================================================
    # Snapshot
    # ============================================================

    def snapshot(self) -> Dict[str, dict]:
        return {
            s: {
                "direction": p.direction,
                "qty": p.qty,
                "entry": p.entry,
                "sl": p.sl,
                "tp": p.tp,
                "opened_at": p.opened_at,
                "risk_usd": p.risk_usd,
            }
            for s, p in self.positions.items()
        }