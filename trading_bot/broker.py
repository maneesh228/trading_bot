from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from datetime import datetime, timedelta
from uuid import uuid4

from trading_bot.models import MarketRegimeSnapshot, OrderRequest, OrderResult, SignalSide, Tick
from trading_bot.token_store import load_local_credentials, load_runtime_credentials, make_kite_client

logger = logging.getLogger(__name__)


class Broker:
    def ltp(self, symbols: list[str]) -> dict[str, Tick]:
        raise NotImplementedError

    def market_regime(self, index_symbol: str) -> MarketRegimeSnapshot | None:
        return None

    def place_order(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError


@dataclass
class ZerodhaBroker(Broker):
    exchange: str
    live_trading: bool
    market_protection_pct: float | None = None

    def __post_init__(self) -> None:
        self.kite = make_kite_client(load_runtime_credentials())
        self._instrument_tokens: dict[str, int] | None = None
        self._ohlc_cache: dict[str, tuple[datetime, Tick]] = {}
        self._market_regime_cache: dict[tuple[str, datetime], MarketRegimeSnapshot | None] = {}
        self._higher_trend_cache: dict[tuple[str, date], float | None] = {}

    def ltp(self, symbols: list[str]) -> dict[str, Tick]:
        now = datetime.now()
        ticks = {}
        for symbol in symbols:
            tick = self._latest_completed_candle_tick(symbol, now)
            if tick is not None:
                ticks[symbol] = tick
        return ticks

    def _latest_completed_candle_tick(self, symbol: str, now: datetime) -> Tick | None:
        current_bucket = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        completed_bucket = current_bucket - timedelta(minutes=5)
        cached = self._ohlc_cache.get(symbol)
        if cached and cached[0] == completed_bucket:
            return cached[1]

        token = self._instrument_token(symbol)
        if token is None:
            return None
        try:
            candles = self.kite.historical_data(
                instrument_token=token,
                from_date=now.replace(hour=9, minute=15, second=0, microsecond=0),
                to_date=completed_bucket,
                interval="5minute",
                continuous=False,
                oi=False,
            )
        except Exception as exc:
            logger.warning("Could not fetch OHLC candle for %s: %s", symbol, exc)
            return None

        if not candles:
            return None
        candle = candles[-1]
        candle_time = candle["date"]
        if not isinstance(candle_time, datetime):
            candle_time = datetime.fromisoformat(str(candle_time))
        close = float(candle["close"])
        tick = Tick(
            symbol=symbol,
            price=close,
            timestamp=candle_time,
            open=float(candle["open"]),
            high=float(candle["high"]),
            low=float(candle["low"]),
            close=close,
            volume=float(candle.get("volume", 0) or 0),
            vwap=_vwap(candles),
            higher_timeframe_trend_pct=self._higher_timeframe_trend_pct(symbol, now),
        )
        self._ohlc_cache[symbol] = (completed_bucket, tick)
        return tick

    def _higher_timeframe_trend_pct(self, symbol: str, now: datetime) -> float | None:
        cache_key = (symbol, now.date())
        if cache_key in self._higher_trend_cache:
            return self._higher_trend_cache[cache_key]

        token = self._instrument_token(symbol)
        if token is None:
            self._higher_trend_cache[cache_key] = None
            return None

        try:
            candles = self.kite.historical_data(
                instrument_token=token,
                from_date=now.date() - timedelta(days=14),
                to_date=now.date() - timedelta(days=1),
                interval="day",
                continuous=False,
                oi=False,
            )
        except Exception as exc:
            logger.warning("Could not fetch higher timeframe trend for %s: %s", symbol, exc)
            self._higher_trend_cache[cache_key] = None
            return None

        trend_pct = _close_to_close_trend_pct(candles)
        self._higher_trend_cache[cache_key] = trend_pct
        return trend_pct

    def _instrument_token(self, symbol: str) -> int | None:
        if self._instrument_tokens is None:
            self._instrument_tokens = {
                item["tradingsymbol"]: item["instrument_token"]
                for item in self.kite.instruments(self.exchange)
            }
        return self._instrument_tokens.get(symbol)

    def market_regime(self, index_symbol: str) -> MarketRegimeSnapshot | None:
        now = datetime.now()
        current_bucket = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        completed_bucket = current_bucket - timedelta(minutes=5)
        cache_key = (index_symbol, completed_bucket)
        if cache_key in self._market_regime_cache:
            return self._market_regime_cache[cache_key]

        token = self._instrument_token(index_symbol)
        if token is None:
            logger.warning("Could not find market regime instrument token for %s", index_symbol)
            self._market_regime_cache[cache_key] = None
            return None

        try:
            candles = self.kite.historical_data(
                instrument_token=token,
                from_date=now.replace(hour=9, minute=15, second=0, microsecond=0),
                to_date=completed_bucket,
                interval="5minute",
                continuous=False,
                oi=False,
            )
        except Exception as exc:
            logger.warning("Could not fetch market regime candles for %s: %s", index_symbol, exc)
            self._market_regime_cache[cache_key] = None
            return None

        snapshot = _market_regime_snapshot(index_symbol, candles)
        self._market_regime_cache[cache_key] = snapshot
        return snapshot

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
            market_protection=self.market_protection_pct,
        )
        logger.info("LIVE ORDER %s %s x%s order_id=%s", request.side, request.symbol, request.quantity, order_id)
        return OrderResult(order_id=str(order_id), request=request, live=True)


def make_kite_for_login():
    credentials = load_local_credentials()
    return make_kite_client(credentials)


def _vwap(candles: list[dict]) -> float | None:
    weighted_value = 0.0
    volume_total = 0.0
    for candle in candles:
        volume = float(candle.get("volume", 0) or 0)
        if volume <= 0:
            continue
        typical_price = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        weighted_value += typical_price * volume
        volume_total += volume
    if volume_total <= 0:
        return None
    return weighted_value / volume_total


def _close_to_close_trend_pct(candles: list[dict]) -> float | None:
    if len(candles) < 2:
        return None
    first = float(candles[0]["close"])
    last = float(candles[-1]["close"])
    if first <= 0:
        return None
    return ((last - first) / first) * 100


def _market_regime_snapshot(symbol: str, candles: list[dict]) -> MarketRegimeSnapshot | None:
    if not candles:
        return None

    typical_sum = 0.0
    for candle in candles:
        typical_sum += (
            float(candle["high"]) + float(candle["low"]) + float(candle["close"])
        ) / 3

    latest = candles[-1]
    candle_time = latest["date"]
    if not isinstance(candle_time, datetime):
        candle_time = datetime.fromisoformat(str(candle_time))
    close = float(latest["close"])
    day_open = float(candles[0]["open"])
    trend_6_pct = None
    if len(candles) > 6:
        previous = float(candles[-7]["close"])
        if previous > 0:
            trend_6_pct = ((close - previous) / previous) * 100

    return MarketRegimeSnapshot(
        symbol=symbol,
        timestamp=candle_time,
        close=close,
        average_price=typical_sum / len(candles),
        day_move_pct=((close - day_open) / day_open) * 100 if day_open > 0 else 0.0,
        trend_6_pct=trend_6_pct,
    )
