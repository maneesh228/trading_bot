from __future__ import annotations

from trading_bot.config import EarlyMomentumExitConfig
from trading_bot.models import Position, SignalSide, Tick


def early_momentum_exit_reason(
    position: Position,
    tick: Tick,
    config: EarlyMomentumExitConfig,
    candle_minutes: int = 5,
) -> str | None:
    if not config.enabled:
        return None
    if tick.timestamp <= position.entry_time:
        return None
    if tick.high is None or tick.low is None or tick.close is None:
        return None

    elapsed_seconds = (tick.timestamp - position.entry_time).total_seconds()
    candle_index = int(elapsed_seconds // (candle_minutes * 60))
    if candle_index < 1 or candle_index > config.max_candles:
        return None

    candle_range = tick.high - tick.low
    if candle_range <= 0:
        return None

    close_strength = ((tick.close - tick.low) / candle_range) * 100
    threshold = config.min_close_strength_pct

    if position.side == SignalSide.BUY and close_strength < threshold:
        if config.require_adverse_price and tick.close >= position.entry_price:
            return None
        return (
            f"early momentum exit: BUY candle closed weak "
            f"{close_strength:.2f}% < {threshold:.2f}%"
        )
    if position.side == SignalSide.SELL and close_strength > (100 - threshold):
        if config.require_adverse_price and tick.close <= position.entry_price:
            return None
        return (
            f"early momentum exit: SELL candle closed weak "
            f"{close_strength:.2f}% > {100 - threshold:.2f}%"
        )
    return None
