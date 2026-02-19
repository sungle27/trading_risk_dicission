from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class DrawdownState:
    peak_nav: float
    nav: float
    dd_pct: float
    soft: bool
    hard: bool
    kill: bool
    halted_until: float


class DrawdownManager:
    """
    Track portfolio drawdown and decide if we should reduce risk or stop trading.

    - soft: reduce risk
    - hard: stop trading for cooldown window
    - kill: stop trading until manual reset (or long cooldown)
    """

    def __init__(
        self,
        *,
        start_nav: float,
        dd_soft_pct: float = 0.06,     # 6%
        dd_hard_pct: float = 0.10,     # 10%
        dd_kill_pct: float = 0.18,     # 18%
        hard_cooldown_sec: int = 6 * 60 * 60,
        min_risk_mult: float = 0.35,  # risk floor when dd grows
    ):
        self.start_nav = float(start_nav)
        self.peak_nav = float(start_nav)
        self.nav = float(start_nav)

        self.dd_soft_pct = float(dd_soft_pct)
        self.dd_hard_pct = float(dd_hard_pct)
        self.dd_kill_pct = float(dd_kill_pct)

        self.hard_cooldown_sec = int(hard_cooldown_sec)
        self.min_risk_mult = float(min_risk_mult)

        self._halted_until = 0.0
        self._killed = False

    def update(self, nav: float) -> DrawdownState:
        nav = float(nav)
        self.nav = nav

        if nav > self.peak_nav:
            self.peak_nav = nav

        dd_pct = 0.0
        if self.peak_nav > 0:
            dd_pct = max(0.0, (self.peak_nav - nav) / self.peak_nav)

        # kill switch
        if dd_pct >= self.dd_kill_pct:
            self._killed = True
            self._halted_until = float("inf")

        # hard stop -> cooldown
        if (not self._killed) and dd_pct >= self.dd_hard_pct:
            self._halted_until = max(self._halted_until, time.time() + self.hard_cooldown_sec)

        soft = dd_pct >= self.dd_soft_pct
        hard = dd_pct >= self.dd_hard_pct
        kill = self._killed
        halted_until = self._halted_until

        return DrawdownState(
            peak_nav=self.peak_nav,
            nav=self.nav,
            dd_pct=dd_pct,
            soft=soft,
            hard=hard,
            kill=kill,
            halted_until=halted_until,
        )

    def can_trade(self) -> Tuple[bool, str]:
        st = self.update(self.nav)
        now = time.time()

        if st.kill:
            return False, "dd_kill"
        if now < st.halted_until:
            return False, "dd_hard_cooldown"
        return True, "ok"

    def risk_multiplier(self) -> float:
        """
        Gradually reduce risk as drawdown increases (after soft).
        """
        st = self.update(self.nav)

        if st.dd_pct < self.dd_soft_pct:
            return 1.0

        # map dd from [soft..hard] -> [1.0 .. min_risk_mult]
        soft = self.dd_soft_pct
        hard = max(self.dd_hard_pct, soft + 1e-9)
        x = min(1.0, max(0.0, (st.dd_pct - soft) / (hard - soft)))
        mult = 1.0 - x * (1.0 - self.min_risk_mult)

        return max(self.min_risk_mult, min(1.0, mult))

    def state(self) -> DrawdownState:
        return self.update(self.nav)

    def reset_peak(self) -> None:
        """
        Manual: set peak = current nav (use when you want to restart evaluation).
        """
        self.peak_nav = self.nav
        self._halted_until = 0.0
        self._killed = False
