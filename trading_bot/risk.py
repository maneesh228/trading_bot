from __future__ import annotations

from dataclasses import dataclass, field

from trading_bot.models import OrderRequest, Position, SignalSide


@dataclass
class RiskManager:
    max_trades_per_day: int
    max_position_value: float
    stop_loss_pct: float
    target_pct: float
    trailing_stop_loss_pct: float | None = None
    max_daily_loss_amount: float | None = None
    max_daily_losses: int | None = None
    trades_today: int = 0
    realized_pnl: float = 0.0
    realized_losses: int = 0
    positions: dict[str, Position] = field(default_factory=dict)
    best_prices: dict[str, float] = field(default_factory=dict)

    def can_place(self, request: OrderRequest) -> tuple[bool, str]:
        if request.side == SignalSide.HOLD:
            return False, "hold signal"
        if request.side == SignalSide.EXIT:
            return False, "exit must be converted to a closing order"
        if request.quantity <= 0:
            return False, "quantity must be greater than zero"
        daily_guard_reason = self.daily_guard_reason()
        if daily_guard_reason:
            return False, daily_guard_reason
        if self.trades_today >= self.max_trades_per_day:
            return False, "daily trade limit reached"
        if request.quantity * request.price > self.max_position_value:
            return False, "position value exceeds risk limit"
        if request.symbol in self.positions and request.side in {SignalSide.BUY, SignalSide.SELL}:
            return False, "position already open"
        return True, "allowed"

    def can_exit(self, symbol: str) -> tuple[bool, str]:
        if symbol not in self.positions:
            return False, "no open position"
        return True, "allowed"

    def exit_signal_for_risk(self, symbol: str, price: float) -> str | None:
        position = self.positions.get(symbol)
        if position is None:
            return None

        self._update_best_price(position, price)
        pnl_pct = position.pnl_pct(price)
        if pnl_pct <= -abs(self.stop_loss_pct):
            return f"stop loss hit at {pnl_pct:.2f}%"
        trailing_reason = self._trailing_stop_reason(position, price)
        if trailing_reason:
            return trailing_reason
        if pnl_pct >= abs(self.target_pct):
            return f"target hit at {pnl_pct:.2f}%"
        return None

    def record_entry(self, position: Position) -> None:
        self.positions[position.symbol] = position
        self.best_prices[position.symbol] = position.entry_price
        self.trades_today += 1

    def record_exit(self, symbol: str) -> None:
        if symbol in self.positions:
            del self.positions[symbol]
            self.best_prices.pop(symbol, None)
            self.trades_today += 1

    def record_realized_pnl(self, pnl: float) -> None:
        self.realized_pnl += pnl
        if pnl < 0:
            self.realized_losses += 1

    def daily_guard_reason(self) -> str | None:
        if self.max_daily_losses is not None and self.realized_losses >= self.max_daily_losses:
            return f"daily loss count limit reached ({self.realized_losses} losses)"
        if self.max_daily_loss_amount is not None and self.realized_pnl <= -abs(self.max_daily_loss_amount):
            return f"daily loss amount limit reached ({self.realized_pnl:.2f})"
        return None

    def _update_best_price(self, position: Position, price: float) -> None:
        current_best = self.best_prices.get(position.symbol, position.entry_price)
        if position.side == SignalSide.BUY:
            self.best_prices[position.symbol] = max(current_best, price)
        elif position.side == SignalSide.SELL:
            self.best_prices[position.symbol] = min(current_best, price)

    def _trailing_stop_reason(self, position: Position, price: float) -> str | None:
        if self.trailing_stop_loss_pct is None:
            return None

        trail = abs(self.trailing_stop_loss_pct)
        best_price = self.best_prices.get(position.symbol, position.entry_price)
        best_pnl_pct = position.pnl_pct(best_price)
        if best_pnl_pct <= 0:
            return None

        if position.side == SignalSide.BUY:
            stop_price = best_price * (1 - trail / 100)
            if price <= stop_price:
                return f"trailing stop hit at {position.pnl_pct(price):.2f}% from best {best_price:.2f}"
        elif position.side == SignalSide.SELL:
            stop_price = best_price * (1 + trail / 100)
            if price >= stop_price:
                return f"trailing stop hit at {position.pnl_pct(price):.2f}% from best {best_price:.2f}"
        return None
