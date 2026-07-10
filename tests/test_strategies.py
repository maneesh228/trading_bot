from datetime import datetime, timedelta

from trading_bot.models import SignalSide, Tick
from trading_bot.strategies import (
    CandleBodyFilterStrategy,
    CompositeStrategy,
    EmaCrossoverStrategy,
    HigherTimeframeTrendFilterStrategy,
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
    TimeBeforeStrategy,
    TrendRegimeStrategy,
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

    assert TimeAfterStrategy("09:45").on_tick(tick).side == SignalSide.PASS
    assert TimeBeforeStrategy("14:30").on_tick(tick).side == SignalSide.PASS
    assert MinVolumeStrategy(50000).on_tick(tick).side == SignalSide.PASS
    assert CandleBodyFilterStrategy(min_body_pct=10).on_tick(tick).side == SignalSide.PASS
    assert VwapFilterStrategy(min_distance_pct=0.02).on_tick(tick).side == SignalSide.BUY


def test_time_before_blocks_late_entries():
    late_tick = Tick("CANBK", 101, datetime(2026, 6, 22, 14, 30))

    assert TimeBeforeStrategy("14:30").on_tick(late_tick).side == SignalSide.HOLD


def test_trend_regime_classifies_uptrend_downtrend_and_sideways():
    now = datetime.now()
    uptrend = TrendRegimeStrategy(lookback=3, min_trend_pct=1)
    downtrend = TrendRegimeStrategy(lookback=3, min_trend_pct=1)
    sideways = TrendRegimeStrategy(lookback=3, min_trend_pct=1)

    for price in [100, 100.5]:
        assert uptrend.on_tick(Tick("INFY", price, now)).side == SignalSide.HOLD

    assert uptrend.on_tick(Tick("INFY", 101.2, now)).side == SignalSide.BUY

    for price in [100, 99.5, 98.8]:
        signal = downtrend.on_tick(Tick("INFY", price, now))
    assert signal.side == SignalSide.SELL

    for price in [100, 100.2, 100.4]:
        signal = sideways.on_tick(Tick("INFY", price, now))
    assert signal.side == SignalSide.HOLD


def test_higher_timeframe_trend_filter_blocks_counter_trend_side():
    strategy = HigherTimeframeTrendFilterStrategy(min_trend_pct=2.0)
    now = datetime.now()

    assert strategy.on_tick(Tick("INFY", 100, now, higher_timeframe_trend_pct=-12.0)).side == SignalSide.SELL
    assert strategy.on_tick(Tick("INFY", 100, now, higher_timeframe_trend_pct=3.0)).side == SignalSide.BUY
    assert strategy.on_tick(Tick("INFY", 100, now, higher_timeframe_trend_pct=1.0)).side == SignalSide.PASS


def test_composite_blocks_buy_against_higher_timeframe_downtrend():
    now = datetime(2026, 6, 30, 11, 20)
    strategy = CompositeStrategy(
        mode="all",
        strategies=[
            VwapFilterStrategy(min_distance_pct=0.1),
            HigherTimeframeTrendFilterStrategy(min_trend_pct=2.0),
        ],
    )

    signal = strategy.on_tick(
        Tick("INFY", 1016.7, now, vwap=1013.8, higher_timeframe_trend_pct=-12.0)
    )

    assert signal.side == SignalSide.HOLD
    assert "higher timeframe downtrend" in signal.reason


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

    assert strategy.on_tick(Tick("INFY", 100, now, volume=250)).side == SignalSide.PASS


def test_support_resistance_breakout_buy_signal():
    strategy = SupportResistanceBreakoutStrategy(lookback=3, buffer_pct=0.0)
    now = datetime.now()

    for price in [100, 101, 102]:
        assert strategy.on_tick(Tick("INFY", price, now)).side == SignalSide.HOLD

    assert strategy.on_tick(Tick("INFY", 103, now)).side == SignalSide.BUY


def test_composite_all_allows_pass_filters_for_sell_signal():
    now = datetime(2026, 6, 24, 10, 0)
    strategy = CompositeStrategy(
        mode="all",
        strategies=[
            SupportResistanceBreakoutStrategy(lookback=3, buffer_pct=0.0),
            TrendRegimeStrategy(lookback=3, min_trend_pct=1),
            TimeAfterStrategy("09:15"),
            TimeBeforeStrategy("14:30"),
            VolumeSpikeStrategy(lookback=3, multiplier=1.1, min_volume=1),
            CandleBodyFilterStrategy(min_body_pct=10),
            VwapFilterStrategy(min_distance_pct=0.02),
        ],
    )

    warming_ticks = [
        Tick("WIPRO", 100, now, open=100, high=101, low=99, close=100, volume=100, vwap=100),
        Tick("WIPRO", 99, now + timedelta(minutes=5), open=100, high=100, low=99, close=99, volume=100, vwap=100),
        Tick("WIPRO", 98, now + timedelta(minutes=10), open=99, high=99, low=98, close=98, volume=100, vwap=100),
    ]
    for tick in warming_ticks:
        assert strategy.on_tick(tick).side == SignalSide.HOLD

    signal = strategy.on_tick(
        Tick(
            "WIPRO",
            96.8,
            now + timedelta(minutes=15),
            open=98,
            high=98,
            low=96.8,
            close=96.8,
            volume=150,
            vwap=100,
        )
    )

    assert signal.side == SignalSide.SELL
