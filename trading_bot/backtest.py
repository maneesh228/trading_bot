from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from trading_bot.confirmation import PendingEntry, evaluate_entry_confirmation
from trading_bot.config import BotConfig, ConfirmationConfig
from trading_bot.execution import quantity_for_capital, signal_strength
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
        trailing_stop_loss_pct=config.risk.trailing_stop_loss_pct,
        max_daily_loss_amount=config.risk.max_daily_loss_amount,
        max_daily_losses=config.risk.max_daily_losses,
    )
    open_entries: dict[str, Position] = {}
    entry_reasons: dict[str, str] = {}
    pending_entries: dict[str, PendingEntry] = {}
    losses_by_symbol: dict[str, int] = {}
    trades: list[BacktestTrade] = []
    higher_trends = {
        symbol: _higher_timeframe_trend_pct(candles, trading_day)
        for symbol, candles in candles_by_symbol.items()
    }

    daily_ticks = []
    for symbol, candles in candles_by_symbol.items():
        weighted_value = 0.0
        volume_total = 0.0
        symbol_day_candles = sorted(
            [candle for candle in candles if _candle_time(candle).date() == trading_day],
            key=_candle_time,
        )
        for candle in symbol_day_candles:
            volume = float(candle.get("volume", 0) or 0)
            close = float(candle["close"])
            if volume > 0:
                typical_price = (float(candle["high"]) + float(candle["low"]) + close) / 3
                weighted_value += typical_price * volume
                volume_total += volume
            vwap = weighted_value / volume_total if volume_total > 0 else None
            timestamp = _candle_time(candle)
            daily_ticks.append(
                Tick(
                    symbol=symbol,
                    price=close,
                    timestamp=timestamp,
                    open=float(candle["open"]),
                    high=float(candle["high"]),
                    low=float(candle["low"]),
                    close=close,
                    volume=volume,
                    vwap=vwap,
                    higher_timeframe_trend_pct=higher_trends.get(symbol),
                )
            )
    daily_ticks.sort(key=lambda tick: tick.timestamp)

    square_off = _parse_time(config.market.square_off_time)
    for timestamp in sorted({tick.timestamp for tick in daily_ticks}):
        candidates: list[tuple[Tick, SignalSide, str]] = []
        ticks_at_time = [tick for tick in daily_ticks if tick.timestamp == timestamp]
        for tick in ticks_at_time:
            if tick.timestamp.time() >= square_off:
                _close_position(tick, risk, open_entries, entry_reasons, trades, losses_by_symbol, "scheduled square off")
                continue

            risk_reason = risk.exit_signal_for_risk(tick.symbol, tick.price)
            if risk_reason:
                pending_entries.pop(tick.symbol, None)
                _close_position(tick, risk, open_entries, entry_reasons, trades, losses_by_symbol, risk_reason)
                continue

            signal = strategies[tick.symbol].on_tick(tick)
            if signal.side in {SignalSide.HOLD, SignalSide.PASS}:
                pending = pending_entries.get(tick.symbol)
                if pending and tick.timestamp > pending.tick.timestamp:
                    decision = evaluate_entry_confirmation(
                        pending,
                        tick,
                        _confirmation_config_for_symbol(config, tick.symbol, losses_by_symbol),
                    )
                    if decision.confirmed:
                        pending_entries.pop(tick.symbol, None)
                        reason = (
                            f"{pending.reason} | confirmed by next candle price action: "
                            f"{signal.reason} | {decision.reason}"
                        )
                        if _entry_gate_reason(config, risk, tick.symbol, pending.side) is None:
                            candidates.append((tick, pending.side, reason))
                    else:
                        _keep_or_reject_pending(
                            config,
                            pending_entries,
                            tick,
                            pending,
                        )
                continue
            if signal.side == SignalSide.EXIT:
                pending_entries.pop(tick.symbol, None)
                _close_position(tick, risk, open_entries, entry_reasons, trades, losses_by_symbol, signal.reason)
                continue
            if tick.symbol in risk.positions:
                pending_entries.pop(tick.symbol, None)
                continue
            if _entry_gate_reason(config, risk, tick.symbol, signal.side) is not None:
                pending_entries.pop(tick.symbol, None)
                continue
            if config.execution.confirm_entries:
                pending = pending_entries.get(tick.symbol)
                if pending and pending.side == signal.side:
                    decision = evaluate_entry_confirmation(
                        pending,
                        tick,
                        _confirmation_config_for_symbol(config, tick.symbol, losses_by_symbol),
                    )
                    if decision.confirmed:
                        pending_entries.pop(tick.symbol, None)
                        reason = (
                            f"{pending.reason} | confirmed by next candle: "
                            f"{signal.reason} | {decision.reason}"
                        )
                        if _entry_gate_reason(config, risk, tick.symbol, signal.side) is None:
                            candidates.append((tick, signal.side, reason))
                    else:
                        _keep_or_reject_pending(
                            config,
                            pending_entries,
                            tick,
                            pending,
                        )
                    continue
                pending_entries[tick.symbol] = PendingEntry(signal.side, signal.reason, tick)
                continue
            candidates.append((tick, signal.side, signal.reason))

        if config.execution.trade_selection == "single_best":
            if risk.positions or not candidates:
                continue
            selected = max(
                candidates,
                key=lambda candidate: (
                    signal_strength(candidate[0], candidate[1]),
                    candidate[0].price,
                    candidate[0].symbol,
                ),
            )
            _open_position(config, selected, quantities, risk, open_entries, entry_reasons)
            continue

        for candidate in candidates:
            _open_position(config, candidate, quantities, risk, open_entries, entry_reasons)

    for tick in reversed(daily_ticks):
        if tick.symbol in open_entries:
            _close_position(tick, risk, open_entries, entry_reasons, trades, losses_by_symbol, "end of data square off")

    return trades


def _confirmation_config_for_symbol(
    config: BotConfig,
    symbol: str,
    losses_by_symbol: dict[str, int],
) -> ConfirmationConfig:
    retry = config.execution.retry_after_loss
    if not retry.enabled or losses_by_symbol.get(symbol, 0) < retry.losses_before_strict:
        return config.execution.confirmation

    return replace(
        config.execution.confirmation,
        min_follow_through_pct=retry.min_follow_through_pct,
        min_close_strength_pct=retry.min_close_strength_pct,
        min_confirmation_volume_ratio=retry.min_confirmation_volume_ratio,
    )


def _keep_or_reject_pending(
    config: BotConfig,
    pending_entries: dict[str, PendingEntry],
    tick: Tick,
    pending: PendingEntry,
) -> None:
    candles_seen = pending.confirmation_candles_seen
    if tick.timestamp != pending.last_confirmation_timestamp:
        candles_seen += 1
    if candles_seen < config.execution.confirmation.max_confirmation_candles:
        pending_entries[tick.symbol] = replace(
            pending,
            confirmation_candles_seen=candles_seen,
            last_confirmation_timestamp=tick.timestamp,
        )
        return
    pending_entries.pop(tick.symbol, None)


def _open_position(
    config: BotConfig,
    candidate: tuple[Tick, SignalSide, str],
    quantities: dict[str, int],
    risk: RiskManager,
    open_entries: dict[str, Position],
    entry_reasons: dict[str, str],
) -> None:
    tick, side, reason = candidate
    quantity = quantities[tick.symbol]
    if config.execution.position_sizing == "max_position_value":
        quantity = quantity_for_capital(tick.price, config.risk.max_position_value)

    request = OrderRequest(
        symbol=tick.symbol,
        quantity=quantity,
        side=side,
        price=tick.price,
        reason=reason,
    )
    allowed, _ = risk.can_place(request)
    if not allowed:
        return

    position = Position(
        symbol=tick.symbol,
        quantity=request.quantity,
        side=request.side,
        entry_price=tick.price,
        entry_time=tick.timestamp,
    )
    risk.record_entry(position)
    open_entries[tick.symbol] = position
    entry_reasons[tick.symbol] = reason


def _close_position(
    tick: Tick,
    risk: RiskManager,
    open_entries: dict[str, Position],
    entry_reasons: dict[str, str],
    trades: list[BacktestTrade],
    losses_by_symbol: dict[str, int],
    reason: str,
) -> None:
    position = open_entries.get(tick.symbol)
    if position is None:
        return

    allowed, _ = risk.can_exit(tick.symbol)
    if not allowed:
        return

    trade = BacktestTrade(
        symbol=tick.symbol,
        side=position.side,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=tick.timestamp,
        exit_price=tick.price,
        quantity=position.quantity,
        reason=f"{entry_reasons.get(tick.symbol, '')} | exit: {reason}",
    )
    trades.append(trade)
    if trade.pnl < 0:
        losses_by_symbol[tick.symbol] = losses_by_symbol.get(tick.symbol, 0) + 1
    risk.record_realized_pnl(trade.pnl)
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


def _entry_gate_reason(config: BotConfig, risk: RiskManager, symbol: str, side: SignalSide) -> str | None:
    if side not in {SignalSide.BUY, SignalSide.SELL}:
        return None

    daily_guard_reason = risk.daily_guard_reason()
    if daily_guard_reason:
        return daily_guard_reason

    symbol_quality = config.execution.symbol_quality
    if not symbol_quality.enabled:
        return None
    if symbol in set(symbol_quality.blocked_symbols):
        return f"symbol quality gate blocked {symbol}"
    allowed_symbols = set(symbol_quality.allowed_symbols)
    if allowed_symbols and symbol not in allowed_symbols:
        return f"symbol quality gate allows only {', '.join(sorted(allowed_symbols))}"
    return None


def _higher_timeframe_trend_pct(candles: list[dict[str, Any]], trading_day: date) -> float | None:
    daily_closes: dict[date, float] = {}
    first_allowed_day = trading_day - timedelta(days=14)
    for candle in candles:
        candle_day = _candle_time(candle).date()
        if candle_day >= trading_day or candle_day < first_allowed_day:
            continue
        daily_closes[candle_day] = float(candle["close"])

    days = sorted(daily_closes)
    if len(days) < 2:
        return None

    first_day = days[0]
    last_day = days[-1]
    first = daily_closes[first_day]
    last = daily_closes[last_day]
    if first <= 0:
        return None
    return ((last - first) / first) * 100
