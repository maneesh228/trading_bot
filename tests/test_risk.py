from datetime import datetime

from trading_bot.models import OrderRequest, Position, SignalSide
from trading_bot.risk import RiskManager


def test_exit_is_allowed_even_after_entry_trade_limit_is_reached():
    risk = RiskManager(
        max_trades_per_day=1,
        max_position_value=100,
        stop_loss_pct=1,
        target_pct=2,
    )
    risk.record_entry(
        Position(
            symbol="INFY",
            quantity=1,
            side=SignalSide.BUY,
            entry_price=100,
            entry_time=datetime.now(),
        )
    )

    entry = OrderRequest("RELIANCE", 1, SignalSide.BUY, 100, "new entry")
    assert risk.can_place(entry) == (False, "daily trade limit reached")
    assert risk.can_exit("INFY") == (True, "allowed")
