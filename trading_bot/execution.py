from __future__ import annotations

from trading_bot.models import SignalSide, Tick


def signal_strength(tick: Tick, side: SignalSide) -> float:
    if tick.open is None or tick.open <= 0:
        return 0.0
    if side == SignalSide.BUY:
        return ((tick.price - tick.open) / tick.open) * 100
    if side == SignalSide.SELL:
        return ((tick.open - tick.price) / tick.open) * 100
    return 0.0


def quantity_for_capital(price: float, capital: float) -> int:
    if price <= 0:
        return 0
    return int(capital // price)
