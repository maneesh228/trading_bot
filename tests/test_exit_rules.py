from __future__ import annotations

from datetime import datetime, timedelta

from trading_bot.config import EarlyMomentumExitConfig
from trading_bot.exit_rules import early_momentum_exit_reason
from trading_bot.models import Position, SignalSide, Tick


def test_early_momentum_exit_flags_weak_buy_follow_through() -> None:
    entry_time = datetime(2026, 6, 24, 11, 45)
    position = Position("CANBK", 76, SignalSide.BUY, 130.72, entry_time)
    tick = Tick(
        "CANBK",
        130.41,
        entry_time + timedelta(minutes=25),
        high=130.83,
        low=130.38,
        close=130.41,
    )

    reason = early_momentum_exit_reason(
        position,
        tick,
        EarlyMomentumExitConfig(enabled=True, max_candles=5),
    )

    assert reason is not None
    assert "BUY candle closed weak" in reason


def test_early_momentum_exit_flags_weak_sell_follow_through() -> None:
    entry_time = datetime(2026, 6, 24, 11, 45)
    position = Position("CANBK", 76, SignalSide.SELL, 130.72, entry_time)
    tick = Tick(
        "CANBK",
        130.90,
        entry_time + timedelta(minutes=5),
        high=130.95,
        low=130.70,
        close=130.90,
    )

    reason = early_momentum_exit_reason(position, tick, EarlyMomentumExitConfig(enabled=True))

    assert reason is not None
    assert "SELL candle closed weak" in reason


def test_early_momentum_exit_keeps_weak_buy_candle_above_entry() -> None:
    entry_time = datetime(2026, 6, 24, 11, 45)
    position = Position("CANBK", 76, SignalSide.BUY, 130.72, entry_time)
    tick = Tick(
        "CANBK",
        130.75,
        entry_time + timedelta(minutes=10),
        high=130.95,
        low=130.73,
        close=130.75,
    )

    reason = early_momentum_exit_reason(position, tick, EarlyMomentumExitConfig(enabled=True))

    assert reason is None


def test_early_momentum_exit_keeps_strong_buy_follow_through() -> None:
    entry_time = datetime(2026, 6, 24, 11, 45)
    position = Position("CANBK", 76, SignalSide.BUY, 130.72, entry_time)
    tick = Tick(
        "CANBK",
        131.10,
        entry_time + timedelta(minutes=5),
        high=131.12,
        low=130.75,
        close=131.10,
    )

    reason = early_momentum_exit_reason(position, tick, EarlyMomentumExitConfig(enabled=True))

    assert reason is None
