from __future__ import annotations

import argparse
from datetime import datetime
from uuid import uuid4

from trading_bot.broker import ZerodhaBroker
from trading_bot.config import load_config
from trading_bot.journal import TradeJournal
from trading_bot.models import OrderRequest, OrderResult, Position, SignalSide


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--journal", default="data/trade_journal.jsonl")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=["BUY", "SELL"], required=True)
    parser.add_argument("--quantity", type=int, required=True)
    parser.add_argument("--entry-price", type=float, required=True)
    parser.add_argument("--entry-time", required=True)
    parser.add_argument("--reason", default="manual dry-run exit")
    args = parser.parse_args()

    config = load_config(args.config)
    broker = ZerodhaBroker(
        exchange=config.market.exchange,
        live_trading=False,
        market_protection_pct=config.broker.market_protection_pct,
    )
    tick = broker.ltp([args.symbol]).get(args.symbol)
    if tick is None:
        raise RuntimeError(f"Could not fetch latest candle for {args.symbol}")

    entry_side = SignalSide(args.side)
    exit_side = SignalSide.SELL if entry_side == SignalSide.BUY else SignalSide.BUY
    position = Position(
        symbol=args.symbol,
        quantity=args.quantity,
        side=entry_side,
        entry_price=args.entry_price,
        entry_time=datetime.fromisoformat(args.entry_time),
    )
    request = OrderRequest(
        symbol=args.symbol,
        quantity=args.quantity,
        side=exit_side,
        price=tick.price,
        reason=args.reason,
    )
    order = OrderResult(
        order_id=f"manual-dryrun-{uuid4()}",
        request=request,
        live=False,
    )
    TradeJournal(args.journal).write(
        "position_closed",
        {
            "order": order,
            "position": position,
            "exit_price": tick.price,
            "exit_reason": args.reason,
            "pnl_pct": position.pnl_pct(tick.price),
            "tick": tick,
        },
    )
    print(f"closed {args.symbol} {args.side} at {tick.price:.2f} candle={tick.timestamp.isoformat()}")


if __name__ == "__main__":
    main()
