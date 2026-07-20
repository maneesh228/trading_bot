from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SignalSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    PASS = "PASS"
    EXIT = "EXIT"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: float
    timestamp: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    vwap: float | None = None
    higher_timeframe_trend_pct: float | None = None


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    symbol: str
    timestamp: datetime
    close: float
    average_price: float | None
    day_move_pct: float
    trend_6_pct: float | None


@dataclass(frozen=True)
class Signal:
    side: SignalSide
    reason: str = ""


@dataclass
class Position:
    symbol: str
    quantity: int
    side: SignalSide
    entry_price: float
    entry_time: datetime

    def pnl_pct(self, current_price: float) -> float:
        if self.side == SignalSide.BUY:
            return ((current_price - self.entry_price) / self.entry_price) * 100
        if self.side == SignalSide.SELL:
            return ((self.entry_price - current_price) / self.entry_price) * 100
        return 0.0


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    quantity: int
    side: SignalSide
    price: float
    reason: str


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    request: OrderRequest
    live: bool
