from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from trading_bot.models import OrderRequest, OrderResult, SignalSide, Tick
from trading_bot.token_store import load_local_credentials, load_runtime_credentials, make_kite_client

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
        self._instrument_tokens: dict[str, int] | None = None
        self._ohlc_cache: dict[str, tuple[datetime, Tick]] = {}

    def ltp(self, symbols: list[str]) -> dict[str, Tick]:
        keys = [f"{self.exchange}:{symbol}" for symbol in symbols]
        quotes = self.kite.ltp(keys)
        now = datetime.now()
        ticks = {}
        for symbol in symbols:
            key = f"{self.exchange}:{symbol}"
            price = float(quotes[key]["last_price"])
            ticks[symbol] = self._latest_tick_with_ohlc(symbol, price, now)
        return ticks

    def _latest_tick_with_ohlc(self, symbol: str, price: float, now: datetime) -> Tick:
        bucket = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        cached = self._ohlc_cache.get(symbol)
        if cached and cached[0] == bucket:
            cached_tick = cached[1]
            return Tick(
                symbol=symbol,
                price=price,
                timestamp=now,
                open=cached_tick.open,
                high=cached_tick.high,
                low=cached_tick.low,
            )

        token = self._instrument_token(symbol)
        if token is None:
            return Tick(symbol=symbol, price=price, timestamp=now)
        try:
            candles = self.kite.historical_data(
                instrument_token=token,
                from_date=now - timedelta(minutes=30),
                to_date=now,
                interval="5minute",
                continuous=False,
                oi=False,
            )
        except Exception as exc:
            logger.warning("Could not fetch OHLC candle for %s: %s", symbol, exc)
            return Tick(symbol=symbol, price=price, timestamp=now)

        if not candles:
            return Tick(symbol=symbol, price=price, timestamp=now)
        candle = candles[-1]
        tick = Tick(
            symbol=symbol,
            price=price,
            timestamp=now,
            open=float(candle["open"]),
            high=float(candle["high"]),
            low=float(candle["low"]),
        )
        self._ohlc_cache[symbol] = (bucket, tick)
        return tick

    def _instrument_token(self, symbol: str) -> int | None:
        if self._instrument_tokens is None:
            self._instrument_tokens = {
                item["tradingsymbol"]: item["instrument_token"]
                for item in self.kite.instruments(self.exchange)
            }
        return self._instrument_tokens.get(symbol)

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
