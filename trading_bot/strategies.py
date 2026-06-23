from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from statistics import mean
from typing import Literal, Protocol

from trading_bot.models import Signal, SignalSide, Tick


class Strategy(Protocol):
    def on_tick(self, tick: Tick) -> Signal:
        ...


@dataclass
class SmaCrossoverStrategy:
    fast_window: int
    slow_window: int
    prices: deque[float] = field(init=False)
    previous_fast_above: bool | None = None

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        self.prices = deque(maxlen=self.slow_window)

    def on_tick(self, tick: Tick) -> Signal:
        self.prices.append(tick.price)
        if len(self.prices) < self.slow_window:
            return Signal(SignalSide.HOLD, "warming up")

        values = list(self.prices)
        fast = mean(values[-self.fast_window :])
        slow = mean(values)
        fast_above = fast > slow

        if self.previous_fast_above is None:
            self.previous_fast_above = fast_above
            return Signal(SignalSide.HOLD, "baseline ready")

        crossed_up = fast_above and not self.previous_fast_above
        crossed_down = not fast_above and self.previous_fast_above
        self.previous_fast_above = fast_above

        if crossed_up:
            return Signal(SignalSide.BUY, f"fast SMA {fast:.2f} crossed above slow SMA {slow:.2f}")
        if crossed_down:
            return Signal(SignalSide.EXIT, f"fast SMA {fast:.2f} crossed below slow SMA {slow:.2f}")
        return Signal(SignalSide.HOLD, "no crossover")


@dataclass
class SmaTrendFilterStrategy:
    fast_window: int
    slow_window: int
    prices: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        self.prices = deque(maxlen=self.slow_window)

    def on_tick(self, tick: Tick) -> Signal:
        self.prices.append(tick.price)
        if len(self.prices) < self.slow_window:
            return Signal(SignalSide.HOLD, "trend filter warming up")

        values = list(self.prices)
        fast = mean(values[-self.fast_window :])
        slow = mean(values)
        if fast > slow:
            return Signal(SignalSide.BUY, f"bullish SMA trend fast={fast:.2f} slow={slow:.2f}")
        if fast < slow:
            return Signal(SignalSide.SELL, f"bearish SMA trend fast={fast:.2f} slow={slow:.2f}")
        return Signal(SignalSide.HOLD, "flat SMA trend")


@dataclass
class EmaCrossoverStrategy:
    fast_window: int
    slow_window: int
    fast_ema: float | None = None
    slow_ema: float | None = None
    previous_fast_above: bool | None = None
    ticks_seen: int = 0

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")

    def on_tick(self, tick: Tick) -> Signal:
        self.ticks_seen += 1
        self.fast_ema = _ema(self.fast_ema, tick.price, self.fast_window)
        self.slow_ema = _ema(self.slow_ema, tick.price, self.slow_window)
        if self.ticks_seen < self.slow_window or self.fast_ema is None or self.slow_ema is None:
            return Signal(SignalSide.HOLD, "EMA warming up")

        fast_above = self.fast_ema > self.slow_ema
        if self.previous_fast_above is None:
            self.previous_fast_above = fast_above
            return Signal(SignalSide.HOLD, "EMA baseline ready")

        crossed_up = fast_above and not self.previous_fast_above
        crossed_down = not fast_above and self.previous_fast_above
        self.previous_fast_above = fast_above
        if crossed_up:
            return Signal(SignalSide.BUY, f"fast EMA {self.fast_ema:.2f} crossed above slow EMA {self.slow_ema:.2f}")
        if crossed_down:
            return Signal(SignalSide.SELL, f"fast EMA {self.fast_ema:.2f} crossed below slow EMA {self.slow_ema:.2f}")
        return Signal(SignalSide.HOLD, "no EMA crossover")


@dataclass
class MacdStrategy:
    fast_window: int = 12
    slow_window: int = 26
    signal_window: int = 9
    mode: Literal["crossover", "trend"] = "crossover"
    fast_ema: float | None = None
    slow_ema: float | None = None
    signal_ema: float | None = None
    previous_macd_above_signal: bool | None = None
    ticks_seen: int = 0

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        if self.signal_window <= 1:
            raise ValueError("signal_window must be greater than 1")
        if self.mode not in {"crossover", "trend"}:
            raise ValueError("MACD mode must be crossover or trend")

    def on_tick(self, tick: Tick) -> Signal:
        self.ticks_seen += 1
        self.fast_ema = _ema(self.fast_ema, tick.price, self.fast_window)
        self.slow_ema = _ema(self.slow_ema, tick.price, self.slow_window)
        if self.ticks_seen < self.slow_window or self.fast_ema is None or self.slow_ema is None:
            return Signal(SignalSide.HOLD, "MACD warming up")

        macd = self.fast_ema - self.slow_ema
        self.signal_ema = _ema(self.signal_ema, macd, self.signal_window)
        if self.signal_ema is None:
            return Signal(SignalSide.HOLD, "MACD signal warming up")

        macd_above_signal = macd > self.signal_ema
        histogram = macd - self.signal_ema
        if self.mode == "trend":
            if macd_above_signal and histogram > 0:
                return Signal(SignalSide.BUY, f"MACD bullish histogram={histogram:.4f}")
            if not macd_above_signal and histogram < 0:
                return Signal(SignalSide.SELL, f"MACD bearish histogram={histogram:.4f}")
            return Signal(SignalSide.HOLD, f"MACD flat histogram={histogram:.4f}")

        if self.previous_macd_above_signal is None:
            self.previous_macd_above_signal = macd_above_signal
            return Signal(SignalSide.HOLD, "MACD baseline ready")

        crossed_up = macd_above_signal and not self.previous_macd_above_signal
        crossed_down = not macd_above_signal and self.previous_macd_above_signal
        self.previous_macd_above_signal = macd_above_signal
        if crossed_up:
            return Signal(SignalSide.BUY, f"MACD crossed above signal histogram={histogram:.4f}")
        if crossed_down:
            return Signal(SignalSide.SELL, f"MACD crossed below signal histogram={histogram:.4f}")
        return Signal(SignalSide.HOLD, f"no MACD crossover histogram={histogram:.4f}")


@dataclass
class OpeningRangeBreakoutStrategy:
    opening_minutes: int
    session_start: datetime | None = None
    range_high: float | None = None
    range_low: float | None = None
    triggered: bool = False

    def on_tick(self, tick: Tick) -> Signal:
        if self.session_start is None:
            self.session_start = tick.timestamp

        range_end = self.session_start + timedelta(minutes=self.opening_minutes)
        if tick.timestamp <= range_end:
            self.range_high = tick.price if self.range_high is None else max(self.range_high, tick.price)
            self.range_low = tick.price if self.range_low is None else min(self.range_low, tick.price)
            return Signal(SignalSide.HOLD, "building opening range")

        if self.triggered or self.range_high is None or self.range_low is None:
            return Signal(SignalSide.HOLD, "breakout already handled")

        if tick.price > self.range_high:
            self.triggered = True
            return Signal(SignalSide.BUY, f"price broke opening high {self.range_high:.2f}")
        if tick.price < self.range_low:
            self.triggered = True
            return Signal(SignalSide.SELL, f"price broke opening low {self.range_low:.2f}")
        return Signal(SignalSide.HOLD, "inside opening range")


@dataclass
class OpenHighLowStrategy:
    tolerance: float = 0.0

    def on_tick(self, tick: Tick) -> Signal:
        if tick.open is None or tick.high is None or tick.low is None:
            return Signal(SignalSide.HOLD, "OHLC data unavailable")

        if abs(tick.open - tick.low) <= self.tolerance:
            return Signal(SignalSide.BUY, f"open equals low {tick.low:.2f}")
        if abs(tick.open - tick.high) <= self.tolerance:
            return Signal(SignalSide.SELL, f"open equals high {tick.high:.2f}")
        return Signal(SignalSide.HOLD, "open is neither high nor low")


@dataclass
class TimeAfterStrategy:
    after: str

    def __post_init__(self) -> None:
        self._after_time = _parse_time(self.after)

    def on_tick(self, tick: Tick) -> Signal:
        if tick.timestamp.time() >= self._after_time:
            return Signal(SignalSide.BUY, f"time is after {self.after}")
        return Signal(SignalSide.HOLD, f"waiting until {self.after}")


@dataclass
class MinVolumeStrategy:
    min_volume: float

    def on_tick(self, tick: Tick) -> Signal:
        if tick.volume is None:
            return Signal(SignalSide.HOLD, "volume unavailable")
        if tick.volume >= self.min_volume:
            return Signal(SignalSide.BUY, f"volume {tick.volume:.0f} >= {self.min_volume:.0f}")
        return Signal(SignalSide.HOLD, f"volume {tick.volume:.0f} below {self.min_volume:.0f}")


@dataclass
class VolumeSpikeStrategy:
    lookback: int = 20
    multiplier: float = 1.5
    min_volume: float = 0.0
    volumes: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        if self.lookback <= 1:
            raise ValueError("lookback must be greater than 1")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be greater than 0")
        self.volumes = deque(maxlen=self.lookback)

    def on_tick(self, tick: Tick) -> Signal:
        if tick.volume is None:
            return Signal(SignalSide.HOLD, "volume unavailable")
        current_volume = float(tick.volume)
        if len(self.volumes) < self.lookback:
            self.volumes.append(current_volume)
            return Signal(SignalSide.HOLD, "volume spike warming up")

        average_volume = mean(self.volumes)
        self.volumes.append(current_volume)
        required_volume = max(self.min_volume, average_volume * self.multiplier)
        if current_volume >= required_volume:
            return Signal(SignalSide.BUY, f"volume spike {current_volume:.0f} >= {required_volume:.0f}")
        return Signal(SignalSide.HOLD, f"volume {current_volume:.0f} below spike threshold {required_volume:.0f}")


@dataclass
class CandleBodyFilterStrategy:
    min_body_pct: float = 5.0
    reject_flat: bool = True

    def on_tick(self, tick: Tick) -> Signal:
        if tick.open is None or tick.high is None or tick.low is None:
            return Signal(SignalSide.HOLD, "OHLC data unavailable")
        close = tick.close if tick.close is not None else tick.price
        candle_range = tick.high - tick.low
        if candle_range <= 0:
            if self.reject_flat:
                return Signal(SignalSide.HOLD, "flat candle rejected")
            return Signal(SignalSide.BUY, "flat candle allowed")
        body_pct = (abs(close - tick.open) / candle_range) * 100
        if body_pct >= self.min_body_pct:
            return Signal(SignalSide.BUY, f"body {body_pct:.2f}% >= {self.min_body_pct:.2f}%")
        return Signal(SignalSide.HOLD, f"body {body_pct:.2f}% below {self.min_body_pct:.2f}%")


@dataclass
class VwapFilterStrategy:
    min_distance_pct: float = 0.0

    def on_tick(self, tick: Tick) -> Signal:
        if tick.vwap is None:
            return Signal(SignalSide.HOLD, "VWAP unavailable")
        distance_pct = ((tick.price - tick.vwap) / tick.vwap) * 100
        if distance_pct >= self.min_distance_pct:
            return Signal(SignalSide.BUY, f"price {tick.price:.2f} above VWAP {tick.vwap:.2f}")
        if distance_pct <= -self.min_distance_pct:
            return Signal(SignalSide.SELL, f"price {tick.price:.2f} below VWAP {tick.vwap:.2f}")
        return Signal(SignalSide.HOLD, f"price too close to VWAP distance={distance_pct:.2f}%")


@dataclass
class SupportResistanceBreakoutStrategy:
    lookback: int = 20
    buffer_pct: float = 0.05
    prices: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        if self.lookback <= 1:
            raise ValueError("lookback must be greater than 1")
        self.prices = deque(maxlen=self.lookback)

    def on_tick(self, tick: Tick) -> Signal:
        if len(self.prices) < self.lookback:
            self.prices.append(tick.price)
            return Signal(SignalSide.HOLD, "support/resistance warming up")

        support = min(self.prices)
        resistance = max(self.prices)
        self.prices.append(tick.price)
        buy_level = resistance * (1 + abs(self.buffer_pct) / 100)
        sell_level = support * (1 - abs(self.buffer_pct) / 100)
        if tick.price >= buy_level:
            return Signal(SignalSide.BUY, f"price broke resistance {resistance:.2f}")
        if tick.price <= sell_level:
            return Signal(SignalSide.SELL, f"price broke support {support:.2f}")
        return Signal(SignalSide.HOLD, f"inside support {support:.2f} resistance {resistance:.2f}")


@dataclass
class RsiMeanReversionStrategy:
    period: int
    oversold: float
    exit_level: float
    prices: deque[float] = field(init=False)
    in_position: bool = False

    def __post_init__(self) -> None:
        if self.period <= 1:
            raise ValueError("period must be greater than 1")
        if self.oversold >= self.exit_level:
            raise ValueError("oversold must be smaller than exit_level")
        self.prices = deque(maxlen=self.period + 1)

    def on_tick(self, tick: Tick) -> Signal:
        self.prices.append(tick.price)
        if len(self.prices) < self.period + 1:
            return Signal(SignalSide.HOLD, "RSI warming up")

        rsi = _rsi(list(self.prices))
        if not self.in_position and rsi <= self.oversold:
            self.in_position = True
            return Signal(SignalSide.BUY, f"RSI {rsi:.2f} below oversold {self.oversold:.2f}")
        if self.in_position and rsi >= self.exit_level:
            self.in_position = False
            return Signal(SignalSide.EXIT, f"RSI {rsi:.2f} recovered above {self.exit_level:.2f}")
        return Signal(SignalSide.HOLD, f"RSI {rsi:.2f}")

@dataclass
class RsiFilterStrategy:
    period: int = 14
    buy_min: float = 50
    buy_max: float = 75
    sell_min: float = 25
    sell_max: float = 50
    prices: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        if self.period <= 1:
            raise ValueError("period must be greater than 1")
        self.prices = deque(maxlen=self.period + 1)

    def on_tick(self, tick: Tick) -> Signal:
        self.prices.append(tick.price)
        if len(self.prices) < self.period + 1:
            return Signal(SignalSide.HOLD, "RSI filter warming up")

        rsi = _rsi(list(self.prices))
        if self.buy_min <= rsi <= self.buy_max:
            return Signal(SignalSide.BUY, f"RSI {rsi:.2f} in buy range")
        if self.sell_min <= rsi <= self.sell_max:
            return Signal(SignalSide.SELL, f"RSI {rsi:.2f} in sell range")
        return Signal(SignalSide.HOLD, f"RSI {rsi:.2f} outside filter ranges")


@dataclass
class CompositeStrategy:
    strategies: list[Strategy]
    mode: Literal["all", "majority"] = "all"

    def __post_init__(self) -> None:
        if not self.strategies:
            raise ValueError("composite strategy requires at least one child strategy")
        if self.mode not in {"all", "majority"}:
            raise ValueError("composite mode must be all or majority")

    def on_tick(self, tick: Tick) -> Signal:
        signals = [strategy.on_tick(tick) for strategy in self.strategies]
        exit_signals = [signal for signal in signals if signal.side == SignalSide.EXIT]
        if exit_signals:
            return Signal(SignalSide.EXIT, self._reason("exit", exit_signals))

        if self.mode == "majority":
            return self._majority_signal(signals)
        return self._all_signal(signals)

    def _all_signal(self, signals: list[Signal]) -> Signal:
        buy_signals = [signal for signal in signals if signal.side == SignalSide.BUY]
        sell_signals = [signal for signal in signals if signal.side == SignalSide.SELL]
        if len(buy_signals) == len(signals):
            return Signal(SignalSide.BUY, self._reason("all buy", buy_signals))
        if len(sell_signals) == len(signals):
            return Signal(SignalSide.SELL, self._reason("all sell", sell_signals))
        return Signal(SignalSide.HOLD, self._reason("composite hold", signals))

    def _majority_signal(self, signals: list[Signal]) -> Signal:
        buy_signals = [signal for signal in signals if signal.side == SignalSide.BUY]
        sell_signals = [signal for signal in signals if signal.side == SignalSide.SELL]
        threshold = (len(signals) // 2) + 1
        if len(buy_signals) >= threshold and len(buy_signals) > len(sell_signals):
            return Signal(SignalSide.BUY, self._reason("majority buy", buy_signals))
        if len(sell_signals) >= threshold and len(sell_signals) > len(buy_signals):
            return Signal(SignalSide.SELL, self._reason("majority sell", sell_signals))
        return Signal(SignalSide.HOLD, self._reason("composite hold", signals))

    @staticmethod
    def _reason(prefix: str, signals: list[Signal]) -> str:
        details = "; ".join(f"{signal.side.value}: {signal.reason}" for signal in signals)
        return f"{prefix}: {details}"


def build_strategy(name: str, params: dict) -> Strategy:
    if name == "sma_crossover":
        return SmaCrossoverStrategy(
            fast_window=int(params.get("fast_window", 5)),
            slow_window=int(params.get("slow_window", 13)),
        )
    if name == "sma_trend_filter":
        return SmaTrendFilterStrategy(
            fast_window=int(params.get("fast_window", 5)),
            slow_window=int(params.get("slow_window", 13)),
        )
    if name == "ema_crossover":
        return EmaCrossoverStrategy(
            fast_window=int(params.get("fast_window", 5)),
            slow_window=int(params.get("slow_window", 13)),
        )
    if name == "macd":
        return MacdStrategy(
            fast_window=int(params.get("fast_window", 12)),
            slow_window=int(params.get("slow_window", 26)),
            signal_window=int(params.get("signal_window", 9)),
            mode=str(params.get("mode", "crossover")),
        )
    if name == "opening_range_breakout":
        return OpeningRangeBreakoutStrategy(opening_minutes=int(params.get("opening_minutes", 15)))
    if name == "open_high_low":
        return OpenHighLowStrategy(tolerance=float(params.get("tolerance", 0.0)))
    if name == "time_after":
        return TimeAfterStrategy(after=str(params.get("after", "09:30")))
    if name == "min_volume":
        return MinVolumeStrategy(min_volume=float(params.get("min_volume", 0)))
    if name == "volume_spike":
        return VolumeSpikeStrategy(
            lookback=int(params.get("lookback", 20)),
            multiplier=float(params.get("multiplier", 1.5)),
            min_volume=float(params.get("min_volume", 0)),
        )
    if name == "candle_body_filter":
        return CandleBodyFilterStrategy(
            min_body_pct=float(params.get("min_body_pct", 5.0)),
            reject_flat=bool(params.get("reject_flat", True)),
        )
    if name == "vwap_filter":
        return VwapFilterStrategy(min_distance_pct=float(params.get("min_distance_pct", 0.0)))
    if name == "support_resistance_breakout":
        return SupportResistanceBreakoutStrategy(
            lookback=int(params.get("lookback", 20)),
            buffer_pct=float(params.get("buffer_pct", 0.05)),
        )
    if name == "rsi_mean_reversion":
        return RsiMeanReversionStrategy(
            period=int(params.get("period", 14)),
            oversold=float(params.get("oversold", 30)),
            exit_level=float(params.get("exit_level", 50)),
        )
    if name == "rsi_filter":
        return RsiFilterStrategy(
            period=int(params.get("period", 14)),
            buy_min=float(params.get("buy_min", 50)),
            buy_max=float(params.get("buy_max", 75)),
            sell_min=float(params.get("sell_min", 25)),
            sell_max=float(params.get("sell_max", 50)),
        )
    if name == "composite":
        raw_strategies = params.get("strategies", [])
        return CompositeStrategy(
            strategies=[
                build_strategy(str(strategy["name"]), {key: value for key, value in strategy.items() if key != "name"})
                for strategy in raw_strategies
            ],
            mode=str(params.get("mode", "all")),
        )
    raise ValueError(f"Unsupported strategy: {name}")


def _ema(previous: float | None, price: float, window: int) -> float:
    if previous is None:
        return price
    multiplier = 2 / (window + 1)
    return (price * multiplier) + (previous * (1 - multiplier))


def _rsi(values: list[float]) -> float:
    gains = []
    losses = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = mean(gains)
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))
