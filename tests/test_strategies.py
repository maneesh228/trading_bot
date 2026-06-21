from datetime import datetime, timedelta

from trading_bot.models import SignalSide, Tick
from trading_bot.strategies import (
    CompositeStrategy,
    OpenHighLowStrategy,
    OpeningRangeBreakoutStrategy,
    RsiMeanReversionStrategy,
    SmaCrossoverStrategy,
    SmaTrendFilterStrategy,
)


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


def test_open_high_low_buy_and_sell_signals():
    strategy = OpenHighLowStrategy()
    now = datetime.now()

    assert strategy.on_tick(Tick("TITAN", 101, now, open=100, high=102, low=100)).side == SignalSide.BUY
    assert strategy.on_tick(Tick("TITAN", 99, now, open=100, high=100, low=98)).side == SignalSide.SELL


def test_rsi_mean_reversion_buy_and_exit_signal():
    strategy = RsiMeanReversionStrategy(period=3, oversold=30, exit_level=50)
    now = datetime.now()

    assert strategy.on_tick(Tick("INFY", 100, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("INFY", 95, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("INFY", 90, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("INFY", 85, now)).side == SignalSide.BUY
    assert strategy.on_tick(Tick("INFY", 95, now)).side == SignalSide.EXIT


def test_composite_requires_all_strategies_to_agree():
    strategy = CompositeStrategy(
        mode="all",
        strategies=[
            OpeningRangeBreakoutStrategy(opening_minutes=1),
            SmaTrendFilterStrategy(fast_window=2, slow_window=3),
        ],
    )
    now = datetime.now()

    assert strategy.on_tick(Tick("RELIANCE", 100, now)).side == SignalSide.HOLD
    assert strategy.on_tick(Tick("RELIANCE", 101, now + timedelta(seconds=30))).side == SignalSide.HOLD
    signal = strategy.on_tick(Tick("RELIANCE", 103, now + timedelta(minutes=2)))

    assert signal.side == SignalSide.BUY
