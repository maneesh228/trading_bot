from datetime import datetime, timedelta

from trading_bot.config import ConfirmationConfig
from trading_bot.confirmation import PendingEntry, evaluate_entry_confirmation
from trading_bot.models import SignalSide, Tick


def test_confirmation_requires_follow_through_above_signal_candle():
    signal_tick = Tick(
        "IOC",
        145.67,
        datetime(2026, 6, 23, 9, 40),
        open=145.20,
        high=145.80,
        low=145.10,
        close=145.67,
        volume=100000,
        vwap=145.00,
    )
    confirmation_tick = Tick(
        "IOC",
        145.55,
        signal_tick.timestamp + timedelta(minutes=5),
        open=145.70,
        high=145.75,
        low=145.40,
        close=145.55,
        volume=90000,
        vwap=145.10,
    )

    decision = evaluate_entry_confirmation(
        PendingEntry(SignalSide.BUY, "breakout", signal_tick),
        confirmation_tick,
        ConfirmationConfig(),
    )

    assert not decision.confirmed
    assert "did not stay above signal high" in decision.reason


def test_confirmation_passes_when_next_candle_has_strength():
    signal_tick = Tick(
        "CANBK",
        130.00,
        datetime(2026, 6, 23, 10, 0),
        open=129.50,
        high=130.10,
        low=129.40,
        close=130.00,
        volume=100000,
        vwap=129.60,
    )
    confirmation_tick = Tick(
        "CANBK",
        130.35,
        signal_tick.timestamp + timedelta(minutes=5),
        open=130.05,
        high=130.40,
        low=130.00,
        close=130.35,
        volume=85000,
        vwap=129.80,
    )

    decision = evaluate_entry_confirmation(
        PendingEntry(SignalSide.BUY, "breakout", signal_tick),
        confirmation_tick,
        ConfirmationConfig(),
    )

    assert decision.confirmed
    assert "follow-through" in decision.reason


def test_confirmation_rejects_buy_when_close_is_not_near_high():
    signal_tick = Tick(
        "CANBK",
        130.00,
        datetime(2026, 6, 23, 10, 0),
        open=129.50,
        high=130.10,
        low=129.40,
        close=130.00,
        volume=100000,
        vwap=129.60,
    )
    confirmation_tick = Tick(
        "CANBK",
        130.25,
        signal_tick.timestamp + timedelta(minutes=5),
        open=130.10,
        high=130.80,
        low=130.00,
        close=130.25,
        volume=85000,
        vwap=129.80,
    )

    decision = evaluate_entry_confirmation(
        PendingEntry(SignalSide.BUY, "breakout", signal_tick),
        confirmation_tick,
        ConfirmationConfig(),
    )

    assert not decision.confirmed
    assert "close strength" in decision.reason


def test_confirmation_accepts_sell_when_close_is_near_low():
    signal_tick = Tick(
        "WIPRO",
        174.00,
        datetime(2026, 6, 23, 10, 0),
        open=174.50,
        high=174.60,
        low=173.90,
        close=174.00,
        volume=100000,
        vwap=174.50,
    )
    confirmation_tick = Tick(
        "WIPRO",
        173.70,
        signal_tick.timestamp + timedelta(minutes=5),
        open=174.00,
        high=174.10,
        low=173.60,
        close=173.70,
        volume=85000,
        vwap=174.40,
    )

    decision = evaluate_entry_confirmation(
        PendingEntry(SignalSide.SELL, "breakdown", signal_tick),
        confirmation_tick,
        ConfirmationConfig(),
    )

    assert decision.confirmed
    assert "close weakness" in decision.reason
