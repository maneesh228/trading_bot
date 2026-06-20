from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean
from typing import Protocol

from trading_bot.models import Signal, SignalSide, Tick


class Strategy(Protocol):
    def on_tick(self, tick: Tick) -> Signal:
        ...


@dataclass
class SmaCrossoverStrategy:
    fast_window: int
    slow_window: int
    prices: deque[float] = field(init=False)
    previous_fast_above: bool | None = None

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        self.prices = deque(maxlen=self.slow_window)

    def on_tick(self, tick: Tick) -> Signal:
        self.prices.append(tick.price)
        if len(self.prices) < self.slow_window:
            return Signal(SignalSide.HOLD, "warming up")

        values = list(self.prices)
        fast = mean(values[-self.fast_window :])
        slow = mean(values)
        fast_above = fast > slow

        if self.previous_fast_above is None:
            self.previous_fast_above = fast_above
            return Signal(SignalSide.HOLD, "baseline ready")

        crossed_up = fast_above and not self.previous_fast_above
        crossed_down = not fast_above and self.previous_fast_above
        self.previous_fast_above = fast_above

        if crossed_up:
            return Signal(SignalSide.BUY, f"fast SMA {fast:.2f} crossed above slow SMA {slow:.2f}")
        if crossed_down:
            return Signal(SignalSide.EXIT, f"fast SMA {fast:.2f} crossed below slow SMA {slow:.2f}")
        return Signal(SignalSide.HOLD, "no crossover")


@dataclass
class OpeningRangeBreakoutStrategy:
    opening_minutes: int
    session_start: datetime | None = None
    range_high: float | None = None
    range_low: float | None = None
    triggered: bool = False

    def on_tick(self, tick: Tick) -> Signal:
        if self.session_start is None:
            self.session_start = tick.timestamp

        range_end = self.session_start + timedelta(minutes=self.opening_minutes)
        if tick.timestamp <= range_end:
            self.range_high = tick.price if self.range_high is None else max(self.range_high, tick.price)
            self.range_low = tick.price if self.range_low is None else min(self.range_low, tick.price)
            return Signal(SignalSide.HOLD, "building opening range")

        if self.triggered or self.range_high is None or self.range_low is None:
            return Signal(SignalSide.HOLD, "breakout already handled")

        if tick.price > self.range_high:
            self.triggered = True
            return Signal(SignalSide.BUY, f"price broke opening high {self.range_high:.2f}")
        if tick.price < self.range_low:
            self.triggered = True
            return Signal(SignalSide.SELL, f"price broke opening low {self.range_low:.2f}")
        return Signal(SignalSide.HOLD, "inside opening range")


def build_strategy(name: str, params: dict) -> Strategy:
    if name == "sma_crossover":
        return SmaCrossoverStrategy(
            fast_window=int(params.get("fast_window", 5)),
            slow_window=int(params.get("slow_window", 13)),
        )
    if name == "opening_range_breakout":
        return OpeningRangeBreakoutStrategy(opening_minutes=int(params.get("opening_minutes", 15)))
    raise ValueError(f"Unsupported strategy: {name}")

