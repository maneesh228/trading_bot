from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from trading_bot.config import ConfirmationConfig
from trading_bot.models import SignalSide, Tick


@dataclass(frozen=True)
class PendingEntry:
    side: SignalSide
    reason: str
    tick: Tick
    confirmation_candles_seen: int = 0
    last_confirmation_timestamp: datetime | None = None


@dataclass(frozen=True)
class ConfirmationDecision:
    confirmed: bool
    reason: str


def evaluate_entry_confirmation(
    pending: PendingEntry,
    confirmation_tick: Tick,
    config: ConfirmationConfig,
) -> ConfirmationDecision:
    checks: list[str] = []
    strong_trend = _has_strong_trend(pending.reason, config.strong_trend_pct)

    if config.require_close_beyond_breakout:
        passed, reason = _close_beyond_signal_range(pending, confirmation_tick)
        if not passed:
            return ConfirmationDecision(False, reason)
        checks.append(reason)

    if config.min_follow_through_pct > 0:
        passed, reason = _min_follow_through(pending, confirmation_tick, config.min_follow_through_pct)
        if not passed:
            return ConfirmationDecision(False, reason)
        checks.append(reason)

    if config.min_close_strength_pct > 0:
        passed, reason = _close_strength(pending.side, confirmation_tick, config.min_close_strength_pct)
        if not passed:
            return ConfirmationDecision(False, reason)
        checks.append(reason)

    if config.require_vwap_side:
        passed, reason = _vwap_side(pending.side, confirmation_tick)
        if not passed:
            return ConfirmationDecision(False, reason)
        checks.append(reason)

    min_volume_ratio = config.min_confirmation_volume_ratio
    if strong_trend:
        min_volume_ratio = min(min_volume_ratio, 0.4)
        checks.append(f"strong trend >= {config.strong_trend_pct:.2f}%")

    if min_volume_ratio > 0:
        passed, reason = _volume_continuation(
            pending.tick,
            confirmation_tick,
            min_volume_ratio,
        )
        if not passed:
            return ConfirmationDecision(False, reason)
        checks.append(reason)

    return ConfirmationDecision(True, "; ".join(checks))


def _close_beyond_signal_range(pending: PendingEntry, confirmation_tick: Tick) -> tuple[bool, str]:
    close = _price(confirmation_tick)
    if pending.side == SignalSide.BUY:
        signal_high = pending.tick.high
        if signal_high is None:
            return True, "signal high unavailable"
        if close <= signal_high:
            return False, f"confirmation close {close:.2f} did not stay above signal high {signal_high:.2f}"
        return True, f"confirmation close stayed above signal high {signal_high:.2f}"

    signal_low = pending.tick.low
    if signal_low is None:
        return True, "signal low unavailable"
    if close >= signal_low:
        return False, f"confirmation close {close:.2f} did not stay below signal low {signal_low:.2f}"
    return True, f"confirmation close stayed below signal low {signal_low:.2f}"


def _min_follow_through(
    pending: PendingEntry,
    confirmation_tick: Tick,
    min_follow_through_pct: float,
) -> tuple[bool, str]:
    signal_price = _price(pending.tick)
    confirmation_price = _price(confirmation_tick)
    if signal_price <= 0:
        return False, "signal price is invalid for follow-through check"

    if pending.side == SignalSide.BUY:
        move_pct = ((confirmation_price - signal_price) / signal_price) * 100
    else:
        move_pct = ((signal_price - confirmation_price) / signal_price) * 100

    if move_pct < min_follow_through_pct:
        return False, f"follow-through {move_pct:.2f}% below required {min_follow_through_pct:.2f}%"
    return True, f"follow-through {move_pct:.2f}%"


def _vwap_side(side: SignalSide, tick: Tick) -> tuple[bool, str]:
    if tick.vwap is None:
        return True, "VWAP unavailable"
    price = _price(tick)
    if side == SignalSide.BUY:
        if price <= tick.vwap:
            return False, f"confirmation price {price:.2f} not above VWAP {tick.vwap:.2f}"
        return True, f"confirmation price above VWAP {tick.vwap:.2f}"
    if price >= tick.vwap:
        return False, f"confirmation price {price:.2f} not below VWAP {tick.vwap:.2f}"
    return True, f"confirmation price below VWAP {tick.vwap:.2f}"


def _close_strength(side: SignalSide, tick: Tick, min_close_strength_pct: float) -> tuple[bool, str]:
    if tick.high is None or tick.low is None:
        return True, "confirmation candle range unavailable"
    candle_range = tick.high - tick.low
    if candle_range <= 0:
        return False, "confirmation candle range is flat"

    close = _price(tick)
    close_position_pct = ((close - tick.low) / candle_range) * 100
    if side == SignalSide.BUY:
        if close_position_pct < min_close_strength_pct:
            return (
                False,
                f"confirmation close strength {close_position_pct:.2f}% below required {min_close_strength_pct:.2f}%",
            )
        return True, f"confirmation close strength {close_position_pct:.2f}%"

    max_sell_close_strength = 100 - min_close_strength_pct
    if close_position_pct > max_sell_close_strength:
        return (
            False,
            f"confirmation close weakness {100 - close_position_pct:.2f}% below required {min_close_strength_pct:.2f}%",
        )
    return True, f"confirmation close weakness {100 - close_position_pct:.2f}%"


def _volume_continuation(
    signal_tick: Tick,
    confirmation_tick: Tick,
    min_confirmation_volume_ratio: float,
) -> tuple[bool, str]:
    signal_volume = signal_tick.volume
    confirmation_volume = confirmation_tick.volume
    if signal_volume is None or signal_volume <= 0 or confirmation_volume is None:
        return True, "volume comparison unavailable"

    required_volume = signal_volume * min_confirmation_volume_ratio
    if confirmation_volume < required_volume:
        return (
            False,
            f"confirmation volume {confirmation_volume:.0f} below required {required_volume:.0f}",
        )
    return True, f"confirmation volume {confirmation_volume:.0f} held against signal volume {signal_volume:.0f}"


def _price(tick: Tick) -> float:
    return tick.close if tick.close is not None else tick.price


def _has_strong_trend(reason: str, strong_trend_pct: float) -> bool:
    if strong_trend_pct <= 0:
        return False
    match = re.search(r"(?:uptrend|downtrend)\s+(-?\d+(?:\.\d+)?)%", reason)
    if not match:
        return False
    return abs(float(match.group(1))) >= strong_trend_pct
