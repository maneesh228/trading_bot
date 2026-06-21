from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from trading_bot.config import BotConfig
from trading_bot.models import OrderRequest, Position, SignalSide, Tick
from trading_bot.risk import RiskManager
from trading_bot.strategies import build_strategy


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    side: SignalSide
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    quantity: int
    reason: str

    @property
    def pnl(self) -> float:
        if self.side == SignalSide.BUY:
            return (self.exit_price - self.entry_price) * self.quantity
        return (self.entry_price - self.exit_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.side == SignalSide.BUY:
            return ((self.exit_price - self.entry_price) / self.entry_price) * 100
        return ((self.entry_price - self.exit_price) / self.entry_price) * 100


@dataclass(frozen=True)
class BacktestResult:
    trades: list[BacktestTrade]
    days: int

    @property
    def total_pnl(self) -> float:
        return sum(trade.pnl for trade in self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl < 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return (self.wins / len(self.trades)) * 100


def run_backtest(config: BotConfig, candles_by_symbol: dict[str, list[dict[str, Any]]]) -> BacktestResult:
    trades: list[BacktestTrade] = []
    all_days = sorted(
        {
            _candle_time(candle).date()
            for candles in candles_by_symbol.values()
            for candle in candles
        }
    )

    for trading_day in all_days:
        trades.extend(_run_day(config, candles_by_symbol, trading_day))

    return BacktestResult(trades=trades, days=len(all_days))


def _run_day(
    config: BotConfig,
    candles_by_symbol: dict[str, list[dict[str, Any]]],
    trading_day: date,
) -> list[BacktestTrade]:
    strategies = {
        item.symbol: build_strategy(item.strategy.name, item.strategy.params)
        for item in config.watchlist
    }
    quantities = {item.symbol: item.quantity for item in config.watchlist}
    risk = RiskManager(
        max_trades_per_day=config.risk.max_trades_per_day,
        max_position_value=config.risk.max_position_value,
        stop_loss_pct=config.risk.per_trade_stop_loss_pct,
        target_pct=config.risk.per_trade_target_pct,
    )
    open_entries: dict[str, Position] = {}
    entry_reasons: dict[str, str] = {}
    trades: list[BacktestTrade] = []

    daily_ticks = []
    for symbol, candles in candles_by_symbol.items():
        for candle in candles:
            timestamp = _candle_time(candle)
            if timestamp.date() == trading_day:
                daily_ticks.append(
                    Tick(
                        symbol=symbol,
                        price=float(candle["close"]),
                        timestamp=timestamp,
                        open=float(candle["open"]),
                        high=float(candle["high"]),
                        low=float(candle["low"]),
                    )
                )
    daily_ticks.sort(key=lambda tick: tick.timestamp)

    square_off = _parse_time(config.market.square_off_time)
    for tick in daily_ticks:
        if tick.timestamp.time() >= square_off:
            _close_position(tick, risk, open_entries, entry_reasons, trades, "scheduled square off")
            continue

        risk_reason = risk.exit_signal_for_risk(tick.symbol, tick.price)
        if risk_reason:
            _close_position(tick, risk, open_entries, entry_reasons, trades, risk_reason)
            continue

        signal = strategies[tick.symbol].on_tick(tick)
        if signal.side == SignalSide.HOLD:
            continue
        if signal.side == SignalSide.EXIT:
            _close_position(tick, risk, open_entries, entry_reasons, trades, signal.reason)
            continue

        request = OrderRequest(
            symbol=tick.symbol,
            quantity=quantities[tick.symbol],
            side=signal.side,
            price=tick.price,
            reason=signal.reason,
        )
        allowed, _ = risk.can_place(request)
        if not allowed:
            continue

        position = Position(
            symbol=tick.symbol,
            quantity=request.quantity,
            side=request.side,
            entry_price=tick.price,
            entry_time=tick.timestamp,
        )
        risk.record_entry(position)
        open_entries[tick.symbol] = position
        entry_reasons[tick.symbol] = signal.reason

    for tick in reversed(daily_ticks):
        if tick.symbol in open_entries:
            _close_position(tick, risk, open_entries, entry_reasons, trades, "end of data square off")

    return trades


def _close_position(
    tick: Tick,
    risk: RiskManager,
    open_entries: dict[str, Position],
    entry_reasons: dict[str, str],
    trades: list[BacktestTrade],
    reason: str,
) -> None:
    position = open_entries.get(tick.symbol)
    if position is None:
        return

    allowed, _ = risk.can_exit(tick.symbol)
    if not allowed:
        return

    trades.append(
        BacktestTrade(
            symbol=tick.symbol,
            side=position.side,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=tick.timestamp,
            exit_price=tick.price,
            quantity=position.quantity,
            reason=f"{entry_reasons.get(tick.symbol, '')} | exit: {reason}",
        )
    )
    risk.record_exit(tick.symbol)
    del open_entries[tick.symbol]
    entry_reasons.pop(tick.symbol, None)


def _candle_time(candle: dict[str, Any]) -> datetime:
    value = candle["date"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))
