from __future__ import annotations

from dataclasses import dataclass, field

from trading_bot.models import OrderRequest, Position, SignalSide


@dataclass
class RiskManager:
    max_trades_per_day: int
    max_position_value: float
    stop_loss_pct: float
    target_pct: float
    trades_today: int = 0
    positions: dict[str, Position] = field(default_factory=dict)

    def can_place(self, request: OrderRequest) -> tuple[bool, str]:
        if request.side == SignalSide.HOLD:
            return False, "hold signal"
        if request.side == SignalSide.EXIT:
            return False, "exit must be converted to a closing order"
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

        pnl_pct = position.pnl_pct(price)
        if pnl_pct <= -abs(self.stop_loss_pct):
            return f"stop loss hit at {pnl_pct:.2f}%"
        if pnl_pct >= abs(self.target_pct):
            return f"target hit at {pnl_pct:.2f}%"
        return None

    def record_entry(self, position: Position) -> None:
        self.positions[position.symbol] = position
        self.trades_today += 1

    def record_exit(self, symbol: str) -> None:
        if symbol in self.positions:
            del self.positions[symbol]
            self.trades_today += 1
