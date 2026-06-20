from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from trading_bot.models import OrderRequest, OrderResult, SignalSide, Tick
from trading_bot.token_store import load_runtime_credentials, make_kite_client

logger = logging.getLogger(__name__)


class Broker:
    def ltp(self, symbols: list[str]) -> dict[str, Tick]:
        raise NotImplementedError

    def place_order(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError


@dataclass
class ZerodhaBroker(Broker):
    exchange: str
    live_trading: bool

    def __post_init__(self) -> None:
        self.kite = make_kite_client(load_runtime_credentials())

    def ltp(self, symbols: list[str]) -> dict[str, Tick]:
        keys = [f"{self.exchange}:{symbol}" for symbol in symbols]
        quotes = self.kite.ltp(keys)
        now = datetime.now()
        ticks = {}
        for symbol in symbols:
            key = f"{self.exchange}:{symbol}"
            ticks[symbol] = Tick(symbol=symbol, price=float(quotes[key]["last_price"]), timestamp=now)
        return ticks

    def place_order(self, request: OrderRequest) -> OrderResult:
        if not self.live_trading:
            order_id = f"dryrun-{uuid4()}"
            logger.info("DRY RUN %s %s x%s at %.2f: %s", request.side, request.symbol, request.quantity, request.price, request.reason)
            return OrderResult(order_id=order_id, request=request, live=False)

        if request.side not in {SignalSide.BUY, SignalSide.SELL}:
            raise ValueError(f"Cannot place live order for side {request.side}")

        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=self.exchange,
            tradingsymbol=request.symbol,
            transaction_type=request.side.value,
            quantity=request.quantity,
            product=self.kite.PRODUCT_MIS,
            order_type=self.kite.ORDER_TYPE_MARKET,
            validity=self.kite.VALIDITY_DAY,
            tag="intraday_bot",
        )
        logger.info("LIVE ORDER %s %s x%s order_id=%s", request.side, request.symbol, request.quantity, order_id)
        return OrderResult(order_id=str(order_id), request=request, live=True)


def make_kite_for_login():
    credentials = load_local_credentials()
    return make_kite_client(credentials)
