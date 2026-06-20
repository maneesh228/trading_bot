from datetime import datetime, timedelta

from trading_bot.models import SignalSide, Tick
from trading_bot.strategies import OpeningRangeBreakoutStrategy, SmaCrossoverStrategy


def test_sma_crossover_buy_signal():
    strategy = SmaCrossoverStrategy(fast_window=2, slow_window=3)
    now = datetime.now()

    assert strategy.on_tick(Tick("INFY", 10, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("INFY", 9, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("INFY", 8, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("INFY", 12, now)).side == SignalSide.BUY


def test_opening_range_breakout_buy_signal():
    strategy = OpeningRangeBreakoutStrategy(opening_minutes=1)
    now = datetime.now()

    assert strategy.on_tick(Tick("RELIANCE", 100, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("RELIANCE", 102, now + timedelta(seconds=30))).side == SignalSide.HOLD
    signal = strategy.on_tick(Tick("RELIANCE", 103, now + timedelta(minutes=2)))

    assert signal.side == SignalSide.BUY
