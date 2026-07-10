from __future__ import annotations

from datetime import datetime, timedelta

from trading_bot.config import load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def classify_trend(candles: list[dict], threshold_pct: float = 0.35) -> tuple[str, float]:
    if len(candles) < 2:
        return "not enough data", 0.0
    first = float(candles[0]["close"])
    last = float(candles[-1]["close"])
    if first <= 0:
        return "not enough data", 0.0
    move_pct = ((last - first) / first) * 100
    if move_pct >= threshold_pct:
        return "uptrend", move_pct
    if move_pct <= -threshold_pct:
        return "downtrend", move_pct
    return "sideways", move_pct


def intraday_vwap(candles: list[dict]) -> float | None:
    weighted_value = 0.0
    volume_total = 0.0
    for candle in candles:
        volume = float(candle.get("volume", 0) or 0)
        if volume <= 0:
            continue
        typical = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        weighted_value += typical * volume
        volume_total += volume
    if volume_total <= 0:
        return None
    return weighted_value / volume_total


def main() -> None:
    config = load_config("config.yaml")
    kite = make_kite_client(load_runtime_credentials())
    instruments = kite.instruments(config.market.exchange)
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in instruments
    }

    now = datetime.now()
    session_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    current_bucket = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
    completed_bucket = current_bucket - timedelta(minutes=5)
    print(
        f"TODAY_MARKET_SNAPSHOT from={session_start} to={completed_bucket} "
        f"symbols={','.join(item.symbol for item in config.watchlist)}"
    )
    for item in config.watchlist:
        token = token_by_symbol[item.symbol]
        candles = kite.historical_data(
            instrument_token=token,
            from_date=session_start,
            to_date=completed_bucket,
            interval="5minute",
            continuous=False,
            oi=False,
        )
        if not candles:
            print(f"{item.symbol}: no candles yet")
            continue
        trend, move_pct = classify_trend(candles)
        last = candles[-1]
        close = float(last["close"])
        high = max(float(candle["high"]) for candle in candles)
        low = min(float(candle["low"]) for candle in candles)
        vwap = intraday_vwap(candles)
        vwap_distance = ((close - vwap) / vwap) * 100 if vwap else 0.0
        day_range_pct = ((high - low) / close) * 100 if close > 0 else 0.0
        last_time = last["date"]
        print(
            f"{item.symbol}: trend={trend} move={move_pct:.2f}% close={close:.2f} "
            f"vwap_dist={vwap_distance:.2f}% range={day_range_pct:.2f}% candles={len(candles)} last={last_time}"
        )


if __name__ == "__main__":
    main()
