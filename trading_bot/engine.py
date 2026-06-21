from __future__ import annotations

import logging
import time
from datetime import datetime

from trading_bot.broker import Broker
from trading_bot.config import BotConfig
from trading_bot.journal import TradeJournal
from trading_bot.models import OrderRequest, Position, SignalSide
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
        self.risk = RiskManager(
            max_trades_per_day=config.risk.max_trades_per_day,
            max_position_value=config.risk.max_position_value,
            stop_loss_pct=config.risk.per_trade_stop_loss_pct,
            target_pct=config.risk.per_trade_target_pct,
            trailing_stop_loss_pct=config.risk.trailing_stop_loss_pct,
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
        for symbol, tick in ticks.items():
            risk_reason = self.risk.exit_signal_for_risk(symbol, tick.price)
            if risk_reason:
                self._exit_position(symbol, tick.price, risk_reason)
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
            if signal.side == SignalSide.HOLD:
                logger.debug("%s hold: %s", symbol, signal.reason)
                continue
            if signal.side == SignalSide.EXIT:
                self._exit_position(symbol, tick.price, signal.reason)
                continue

            request = OrderRequest(
                symbol=symbol,
                quantity=self.quantities[symbol],
                side=signal.side,
                price=tick.price,
                reason=signal.reason,
            )
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
                continue

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

    def square_off_all(self) -> None:
        logger.info("Square-off time reached")
        self._journal("square_off_started", {"symbols": list(self.risk.positions)})
        ticks = self.broker.ltp(list(self.risk.positions))
        for symbol, position in list(self.risk.positions.items()):
            self._exit_position(symbol, ticks[symbol].price, "scheduled square off")

    def _exit_position(self, symbol: str, price: float, reason: str) -> None:
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
        self._journal(
            "position_closed",
            {
                "order": order,
                "position": position,
                "exit_price": price,
                "exit_reason": reason,
                "pnl_pct": pnl,
            },
        )
        self.risk.record_exit(symbol)

    def _should_square_off(self) -> bool:
        now = datetime.now().strftime("%H:%M")
        return now >= self.config.market.square_off_time

    def _journal(self, event_type: str, payload: dict) -> None:
        if self.journal:
            self.journal.write(event_type, payload)
