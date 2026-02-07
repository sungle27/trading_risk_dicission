from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    start_ts: int
    end_ts: int


class TimeframeResampler:
    def __init__(self, tf_sec: int):
        self.tf = tf_sec
        self.cur_start: int | None = None
        self.o = self.h = self.l = self.c = None
        self.vol = 0.0

    def update(self, sec: int, price: float, vol: float) -> tuple[Candle | None, bool]:
        bucket_start = (sec // self.tf) * self.tf

        if self.cur_start is None:
            self.cur_start = bucket_start
            self.o = self.h = self.l = self.c = price
            self.vol = vol
            return None, False

        # still in same candle
        if bucket_start == self.cur_start:
            self.c = price
            self.h = max(self.h, price)  # type: ignore[arg-type]
            self.l = min(self.l, price)  # type: ignore[arg-type]
            self.vol += vol
            return None, False

        # close previous candle
        closed = Candle(
            open=float(self.o), high=float(self.h), low=float(self.l), close=float(self.c),
            volume=float(self.vol),
            start_ts=self.cur_start,
            end_ts=self.cur_start + self.tf,
        )

        # start new candle
        self.cur_start = bucket_start
        self.o = self.h = self.l = self.c = price
        self.vol = vol

        return closed, True
