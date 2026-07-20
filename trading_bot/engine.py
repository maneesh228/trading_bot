from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime

from trading_bot.broker import Broker
from trading_bot.confirmation import PendingEntry, evaluate_entry_confirmation
from trading_bot.config import BotConfig
from trading_bot.execution import quantity_for_capital, signal_strength
from trading_bot.journal import TradeJournal
from trading_bot.models import OrderRequest, Position, Signal, SignalSide, Tick
from trading_bot.risk import RiskManager
from trading_bot.strategies import Strategy, build_strategy

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, config: BotConfig, broker: Broker, journal: TradeJournal | None = None) -> None:
        self.config = config
        self.broker = broker
        self.journal = journal
        self.strategies: dict[str, Strategy] = {
            item.symbol: build_strategy(item.strategy.name, item.strategy.params)
            for item in config.watchlist
        }
        self.quantities = {item.symbol: item.quantity for item in config.watchlist}
        self.pending_entries: dict[str, PendingEntry] = {}
        self.risk = RiskManager(
            max_trades_per_day=config.risk.max_trades_per_day,
            max_position_value=config.risk.max_position_value,
            stop_loss_pct=config.risk.per_trade_stop_loss_pct,
            target_pct=config.risk.per_trade_target_pct,
            trailing_stop_loss_pct=config.risk.trailing_stop_loss_pct,
            max_daily_loss_amount=config.risk.max_daily_loss_amount,
            max_daily_losses=config.risk.max_daily_losses,
        )

    def run_forever(self) -> None:
        symbols = list(self.strategies)
        logger.info("Starting bot for %s", ", ".join(symbols))
        self._journal(
            "bot_started",
            {
                "symbols": symbols,
                "live_trading": self.config.broker.live_trading,
                "square_off_time": self.config.market.square_off_time,
            },
        )
        while True:
            self.run_once(symbols)
            if self._should_square_off():
                self.square_off_all()
                return
            time.sleep(self.config.market.poll_interval_seconds)

    def run_once(self, symbols: list[str]) -> None:
        ticks = self.broker.ltp(symbols)
        candidates: list[tuple[str, Tick, Signal]] = []
        for symbol, tick in ticks.items():
            risk_reason = self.risk.exit_signal_for_risk(symbol, tick.price)
            if risk_reason:
                self.pending_entries.pop(symbol, None)
                self._exit_position(symbol, tick.price, risk_reason, tick)
                continue

            signal = self.strategies[symbol].on_tick(tick)
            self._journal(
                "signal",
                {
                    "symbol": symbol,
                    "tick": tick,
                    "signal": signal,
                    "open_position": self.risk.positions.get(symbol),
                },
            )
            if signal.side in {SignalSide.HOLD, SignalSide.PASS}:
                if self.config.execution.confirm_entries and symbol not in self.risk.positions:
                    confirmed_signal = self._confirm_pending_entry_from_price_action(symbol, tick, signal.reason)
                    if confirmed_signal is not None:
                        candidates.append((symbol, tick, confirmed_signal))
                    if symbol in self.pending_entries:
                        continue
                    if confirmed_signal is not None:
                        continue
                self._cancel_pending_entry(symbol, tick, signal.reason)
                logger.debug("%s hold: %s", symbol, signal.reason)
                continue
            if signal.side == SignalSide.EXIT:
                self.pending_entries.pop(symbol, None)
                self._exit_position(symbol, tick.price, signal.reason, tick)
                continue
            if symbol in self.risk.positions:
                self.pending_entries.pop(symbol, None)
                continue
            if self.config.execution.confirm_entries:
                confirmed_signal = self._confirm_entry_signal(symbol, tick, signal)
                if confirmed_signal is None:
                    continue
                signal = confirmed_signal

            candidates.append((symbol, tick, signal))

        if self.config.execution.trade_selection == "single_best":
            self._place_best_candidate(candidates)
            return

        for symbol, tick, signal in candidates:
            self._place_entry(symbol, tick, signal)

    def square_off_all(self) -> None:
        logger.info("Square-off time reached")
        self._journal("square_off_started", {"symbols": list(self.risk.positions)})
        ticks = self.broker.ltp(list(self.risk.positions))
        for symbol, position in list(self.risk.positions.items()):
            self._exit_position(symbol, ticks[symbol].price, "scheduled square off", ticks[symbol])

    def _exit_position(self, symbol: str, price: float, reason: str, tick: Tick | None = None) -> None:
        position = self.risk.positions.get(symbol)
        if position is None:
            logger.info("No open position to exit for %s", symbol)
            return

        side = SignalSide.SELL if position.side == SignalSide.BUY else SignalSide.BUY
        request = OrderRequest(
            symbol=symbol,
            quantity=position.quantity,
            side=side,
            price=price,
            reason=reason,
        )
        allowed, risk_reason = self.risk.can_exit(symbol)
        if not allowed:
            logger.info("Skipped exit %s: %s", symbol, risk_reason)
            self._journal(
                "exit_skipped",
                {
                    "symbol": symbol,
                    "price": price,
                    "reason": risk_reason,
                },
            )
            return
        order = self.broker.place_order(request)
        pnl = position.pnl_pct(price)
        pnl_amount = self._position_pnl(position, price)
        self._journal(
            "position_closed",
            {
                "order": order,
                "position": position,
                "exit_price": price,
                "exit_reason": reason,
                "pnl_pct": pnl,
                "pnl": pnl_amount,
                "tick": tick,
            },
        )
        self.risk.record_realized_pnl(pnl_amount)
        self.risk.record_exit(symbol)

    def _place_best_candidate(self, candidates: list[tuple[str, Tick, Signal]]) -> None:
        if not candidates:
            return
        if self.risk.positions:
            for symbol, tick, signal in candidates:
                request = self._entry_request(symbol, tick, signal)
                self._journal(
                    "order_skipped",
                    {
                        "request": request,
                        "reason": "single_best mode already has an open position",
                        "tick": tick,
                    },
                )
            return

        symbol, tick, signal = max(
            candidates,
            key=lambda candidate: (
                signal_strength(candidate[1], candidate[2].side),
                candidate[1].price,
                candidate[0],
            ),
        )
        strength = signal_strength(tick, signal.side)
        selected_signal = Signal(
            signal.side,
            f"{signal.reason} | selected best candidate strength={strength:.2f}%",
        )
        self._place_entry(symbol, tick, selected_signal)

    def _place_entry(self, symbol: str, tick: Tick, signal: Signal) -> None:
        gate_reason = self._entry_gate_reason(symbol, signal.side, tick=tick, signal_reason=signal.reason)
        if gate_reason:
            request = self._entry_request(symbol, tick, signal)
            logger.info("Skipped %s %s: %s", signal.side, symbol, gate_reason)
            self._journal(
                "order_skipped",
                {
                    "request": request,
                    "reason": gate_reason,
                    "tick": tick,
                },
            )
            return

        request = self._entry_request(symbol, tick, signal)
        allowed, reason = self.risk.can_place(request)
        if not allowed:
            logger.info("Skipped %s %s: %s", signal.side, symbol, reason)
            self._journal(
                "order_skipped",
                {
                    "request": request,
                    "reason": reason,
                    "tick": tick,
                },
            )
            return

        order = self.broker.place_order(request)
        self._journal(
            "order_placed",
            {
                "order": order,
                "tick": tick,
            },
        )
        self.risk.record_entry(
            Position(
                symbol=symbol,
                quantity=request.quantity,
                side=request.side,
                entry_price=tick.price,
                entry_time=tick.timestamp,
            )
        )

    def _confirm_entry_signal(self, symbol: str, tick: Tick, signal: Signal) -> Signal | None:
        gate_reason = self._entry_gate_reason(symbol, signal.side, include_market_regime=False)
        if gate_reason:
            self.pending_entries.pop(symbol, None)
            self._journal(
                "entry_signal_blocked",
                {
                    "symbol": symbol,
                    "tick": tick,
                    "signal": signal,
                    "reason": gate_reason,
                },
            )
            return None

        pending = self.pending_entries.get(symbol)
        if pending and pending.side == signal.side:
            return self._evaluate_pending_entry(symbol, tick, signal.side, f"next candle signal: {signal.reason}")

        self.pending_entries[symbol] = PendingEntry(signal.side, signal.reason, tick)
        self._journal(
            "entry_signal_pending",
            {
                "symbol": symbol,
                "tick": tick,
                "signal": signal,
                "reason": "waiting for next candle confirmation",
            },
        )
        return None

    def _confirm_pending_entry_from_price_action(self, symbol: str, tick: Tick, reason: str) -> Signal | None:
        pending = self.pending_entries.get(symbol)
        if pending is None:
            return None
        if tick.timestamp <= pending.tick.timestamp:
            return None
        if tick.timestamp == pending.last_confirmation_timestamp:
            return None
        return self._evaluate_pending_entry(symbol, tick, pending.side, f"next candle price action: {reason}")

    def _evaluate_pending_entry(self, symbol: str, tick: Tick, side: SignalSide, reason: str) -> Signal | None:
        pending = self.pending_entries.get(symbol)
        if pending is None:
            return None
        if tick.timestamp <= pending.tick.timestamp:
            return None
        if tick.timestamp == pending.last_confirmation_timestamp:
            return None

        decision = evaluate_entry_confirmation(
            pending,
            tick,
            self.config.execution.confirmation,
        )
        if decision.confirmed:
            self.pending_entries.pop(symbol, None)
            confirmed = Signal(
                side,
                f"{pending.reason} | confirmed by {reason} | {decision.reason}",
            )
            self._journal(
                "entry_signal_confirmed",
                {
                    "symbol": symbol,
                    "signal_tick": pending.tick,
                    "confirmation_tick": tick,
                    "signal": confirmed,
                },
            )
            return confirmed

        candles_seen = pending.confirmation_candles_seen
        if tick.timestamp != pending.last_confirmation_timestamp:
            candles_seen += 1
        if candles_seen < self.config.execution.confirmation.max_confirmation_candles:
            self.pending_entries[symbol] = replace(
                pending,
                confirmation_candles_seen=candles_seen,
                last_confirmation_timestamp=tick.timestamp,
            )
            self._journal(
                "entry_signal_still_pending",
                {
                    "symbol": symbol,
                    "signal_tick": pending.tick,
                    "confirmation_tick": tick,
                    "pending_side": pending.side,
                    "pending_reason": pending.reason,
                    "attempt": candles_seen,
                    "max_attempts": self.config.execution.confirmation.max_confirmation_candles,
                    "reason": decision.reason,
                },
            )
            return None

        self.pending_entries.pop(symbol, None)
        self._journal(
            "entry_signal_rejected",
            {
                "symbol": symbol,
                "signal_tick": pending.tick,
                "confirmation_tick": tick,
                "tick": tick,
                "pending_side": pending.side,
                "pending_reason": pending.reason,
                "reason": decision.reason,
            },
        )
        return None

    def _cancel_pending_entry(self, symbol: str, tick: Tick, reason: str) -> None:
        pending = self.pending_entries.pop(symbol, None)
        if not pending:
            return
        self._journal(
            "entry_signal_cancelled",
            {
                "symbol": symbol,
                "tick": tick,
                "pending_side": pending.side,
                "pending_reason": pending.reason,
                "signal_tick": pending.tick,
                "reason": reason,
            },
        )

    def _entry_request(self, symbol: str, tick: Tick, signal: Signal) -> OrderRequest:
        quantity = self.quantities[symbol]
        if self.config.execution.position_sizing == "max_position_value":
            quantity = quantity_for_capital(tick.price, self.config.risk.max_position_value)
        return OrderRequest(
            symbol=symbol,
            quantity=quantity,
            side=signal.side,
            price=tick.price,
            reason=signal.reason,
        )

    def _entry_gate_reason(
        self,
        symbol: str,
        side: SignalSide,
        *,
        tick: Tick | None = None,
        signal_reason: str = "",
        include_market_regime: bool = True,
    ) -> str | None:
        if side not in {SignalSide.BUY, SignalSide.SELL}:
            return None

        daily_guard_reason = self.risk.daily_guard_reason()
        if daily_guard_reason:
            return daily_guard_reason

        symbol_quality = self.config.execution.symbol_quality
        if symbol_quality.enabled:
            if symbol in set(symbol_quality.blocked_symbols):
                return f"symbol quality gate blocked {symbol}"
            allowed_symbols = set(symbol_quality.allowed_symbols)
            if allowed_symbols and symbol not in allowed_symbols:
                return f"symbol quality gate allows only {', '.join(sorted(allowed_symbols))}"

        if not include_market_regime:
            return None

        market_regime_reason = self._market_regime_gate_reason(side, tick=tick, signal_reason=signal_reason)
        if market_regime_reason:
            return market_regime_reason
        return None

    def _market_regime_gate_reason(
        self,
        side: SignalSide,
        *,
        tick: Tick | None,
        signal_reason: str,
    ) -> str | None:
        market_regime = self.config.execution.market_regime
        if not market_regime.enabled:
            return None

        snapshot = self.broker.market_regime(market_regime.index_symbol)
        if snapshot is None:
            reason = f"market regime blocked: {market_regime.index_symbol} context unavailable"
            return None if self._strong_stock_exception(side, tick, signal_reason, reason) else reason

        if market_regime.require_average_side:
            if snapshot.average_price is None:
                reason = f"market regime blocked: {snapshot.symbol} average unavailable"
                return None if self._strong_stock_exception(side, tick, signal_reason, reason) else reason
            if side == SignalSide.BUY and snapshot.close < snapshot.average_price:
                reason = (
                    f"market regime blocked: BUY needs {snapshot.symbol} above average "
                    f"(close={snapshot.close:.2f}, avg={snapshot.average_price:.2f})"
                )
                return None if self._strong_stock_exception(side, tick, signal_reason, reason) else reason
            if side == SignalSide.SELL and snapshot.close > snapshot.average_price:
                reason = (
                    f"market regime blocked: SELL needs {snapshot.symbol} below average "
                    f"(close={snapshot.close:.2f}, avg={snapshot.average_price:.2f})"
                )
                return None if self._strong_stock_exception(side, tick, signal_reason, reason) else reason

        if market_regime.min_trend_6_pct > 0:
            if snapshot.trend_6_pct is None:
                reason = f"market regime blocked: {snapshot.symbol} 6-candle trend unavailable"
                return None if self._strong_stock_exception(side, tick, signal_reason, reason) else reason
            signed_trend = snapshot.trend_6_pct if side == SignalSide.BUY else -snapshot.trend_6_pct
            if signed_trend < market_regime.min_trend_6_pct:
                reason = (
                    f"market regime blocked: {side.value} needs {snapshot.symbol} "
                    f"6-candle trend >= {market_regime.min_trend_6_pct:.2f}% "
                    f"(actual={signed_trend:.2f}%, raw={snapshot.trend_6_pct:.2f}%, "
                    f"day={snapshot.day_move_pct:.2f}%)"
                )
                return None if self._strong_stock_exception(side, tick, signal_reason, reason) else reason

        return None

    def _strong_stock_exception(self, side: SignalSide, tick: Tick | None, signal_reason: str, block_reason: str) -> bool:
        market_regime = self.config.execution.market_regime
        if not market_regime.allow_strong_stock_exception or tick is None:
            return False
        if side.value != market_regime.exception_side.upper():
            return False

        vwap_distance = self._directional_vwap_distance_pct(side, tick)
        if vwap_distance is None or vwap_distance < market_regime.exception_min_stock_vwap_distance_pct:
            return False

        signal_strength_pct = self._directional_signal_strength_pct(side, tick)
        if signal_strength_pct is None or signal_strength_pct < market_regime.exception_min_signal_strength_pct:
            return False

        logger.info(
            "Market regime exception allowed %s: %s; stock_vwap_distance=%.2f%% strength=%.2f%% reason=%s",
            tick.symbol,
            block_reason,
            vwap_distance,
            signal_strength_pct,
            signal_reason,
        )
        return True

    @staticmethod
    def _directional_vwap_distance_pct(side: SignalSide, tick: Tick) -> float | None:
        if tick.vwap is None or tick.vwap <= 0:
            return None
        raw = ((tick.price - tick.vwap) / tick.vwap) * 100
        if side == SignalSide.BUY:
            return raw
        if side == SignalSide.SELL:
            return -raw
        return None

    @staticmethod
    def _directional_signal_strength_pct(side: SignalSide, tick: Tick) -> float | None:
        if tick.open is None or tick.open <= 0:
            return None
        if side == SignalSide.BUY:
            return ((tick.price - tick.open) / tick.open) * 100
        if side == SignalSide.SELL:
            return ((tick.open - tick.price) / tick.open) * 100
        return None

    @staticmethod
    def _position_pnl(position: Position, price: float) -> float:
        if position.side == SignalSide.BUY:
            return (price - position.entry_price) * position.quantity
        if position.side == SignalSide.SELL:
            return (position.entry_price - price) * position.quantity
        return 0.0

    def _should_square_off(self) -> bool:
        now = datetime.now().strftime("%H:%M")
        return now >= self.config.market.square_off_time

    def _journal(self, event_type: str, payload: dict) -> None:
        if self.journal:
            self.journal.write(event_type, payload)
