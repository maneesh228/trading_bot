from datetime import datetime, timedelta

from trading_bot.models import SignalSide, Tick
from trading_bot.strategies import (
    CandleBodyFilterStrategy,
    CompositeStrategy,
    EmaCrossoverStrategy,
    MacdStrategy,
    MinVolumeStrategy,
    OpenHighLowStrategy,
    OpeningRangeBreakoutStrategy,
    RsiFilterStrategy,
    RsiMeanReversionStrategy,
    SmaCrossoverStrategy,
    SmaTrendFilterStrategy,
    SupportResistanceBreakoutStrategy,
    TimeAfterStrategy,
    VolumeSpikeStrategy,
    VwapFilterStrategy,
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


def test_time_volume_body_and_vwap_filters():
    now = datetime(2026, 6, 22, 9, 46)
    tick = Tick("CANBK", 101, now, open=100, high=102, low=99, close=101, volume=100000, vwap=100)

    assert TimeAfterStrategy("09:45").on_tick(tick).side == SignalSide.BUY
    assert MinVolumeStrategy(50000).on_tick(tick).side == SignalSide.BUY
    assert CandleBodyFilterStrategy(min_body_pct=10).on_tick(tick).side == SignalSide.BUY
    assert VwapFilterStrategy(min_distance_pct=0.02).on_tick(tick).side == SignalSide.BUY


def test_candle_body_filter_rejects_flat_candle():
    tick = Tick("CANBK", 100, datetime.now(), open=100, high=100, low=100, close=100, volume=100000)

    assert CandleBodyFilterStrategy().on_tick(tick).side == SignalSide.HOLD


def test_ema_crossover_and_rsi_filter_build_signals():
    ema = EmaCrossoverStrategy(fast_window=2, slow_window=3)
    rsi = RsiFilterStrategy(period=3, buy_min=50, buy_max=100)
    now = datetime.now()

    for price in [10, 9, 8]:
        assert ema.on_tick(Tick("INFY", price, now)).side == SignalSide.HOLD
        assert rsi.on_tick(Tick("INFY", price, now)).side == SignalSide.HOLD

    assert ema.on_tick(Tick("INFY", 12, now)).side == SignalSide.BUY
    assert rsi.on_tick(Tick("INFY", 13, now)).side == SignalSide.BUY


def test_macd_trend_can_be_bullish():
    strategy = MacdStrategy(fast_window=2, slow_window=4, signal_window=2, mode="trend")
    now = datetime.now()

    signal = SignalSide.HOLD
    for price in [100, 99, 98, 100, 102, 104]:
        signal = strategy.on_tick(Tick("INFY", price, now)).side

    assert signal == SignalSide.BUY


def test_volume_spike_filter():
    strategy = VolumeSpikeStrategy(lookback=3, multiplier=2)
    now = datetime.now()

    for volume in [100, 110, 90]:
        assert strategy.on_tick(Tick("INFY", 100, now, volume=volume)).side == SignalSide.HOLD

    assert strategy.on_tick(Tick("INFY", 100, now, volume=250)).side == SignalSide.BUY


def test_support_resistance_breakout_buy_signal():
    strategy = SupportResistanceBreakoutStrategy(lookback=3, buffer_pct=0.0)
    now = datetime.now()

    for price in [100, 101, 102]:
        assert strategy.on_tick(Tick("INFY", price, now)).side == SignalSide.HOLD

    assert strategy.on_tick(Tick("INFY", 103, now)).side == SignalSide.BUY
