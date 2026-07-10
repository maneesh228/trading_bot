from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
from statistics import mean, median
from typing import Any

from trading_bot.config import load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--interval", default="5minute")
    args = parser.parse_args()

    config = load_config(args.config)
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments(config.market.exchange)
    }

    to_date = date.today()
    from_date = to_date - timedelta(days=args.days)
    print(f"VWAP distance probe interval={args.interval} from={from_date} to={to_date}")
    print("distance_pct = abs(close - intraday_vwap) / intraday_vwap * 100")
    print("entry_distance_pct = directional VWAP distance for breakout-like candidates")

    all_distances: list[float] = []
    all_entry_distances: list[float] = []
    for watch in config.watchlist:
        token = token_by_symbol.get(watch.symbol)
        if token is None:
            print(f"{watch.symbol}: missing instrument token")
            continue
        candles = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=args.interval,
            continuous=False,
            oi=False,
        )
        distances, entry_distances = analyze_symbol(candles)
        all_distances.extend(distances)
        all_entry_distances.extend(entry_distances)
        print_stats(watch.symbol, distances, entry_distances)
    print_stats("TOTAL", all_distances, all_entry_distances)


def analyze_symbol(candles: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    by_day: dict[date, list[dict[str, Any]]] = {}
    for candle in candles:
        ts = candle_time(candle)
        by_day.setdefault(ts.date(), []).append(candle)

    distances: list[float] = []
    entry_distances: list[float] = []
    for day_candles in by_day.values():
        day_candles.sort(key=candle_time)
        add_vwap(day_candles)
        for index, candle in enumerate(day_candles):
            ts = candle_time(candle)
            if ts.time() < time(9, 15) or ts.time() >= time(15, 15):
                continue
            vwap = candle.get("_vwap")
            if vwap is None or vwap <= 0:
                continue
            close = float(candle["close"])
            distances.append(abs(close - vwap) / vwap * 100)

            if index >= 20 and ts.time() < time(14, 30):
                signal = breakout_signal(candle, day_candles[index - 20:index])
                if signal is not None:
                    side = signal
                    if side == "BUY" and close > vwap:
                        entry_distances.append((close - vwap) / vwap * 100)
                    elif side == "SELL" and close < vwap:
                        entry_distances.append((vwap - close) / vwap * 100)
    return distances, entry_distances


def breakout_signal(candle: dict[str, Any], previous: list[dict[str, Any]]) -> str | None:
    close = float(candle["close"])
    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    volume = float(candle.get("volume", 0) or 0)
    avg_volume = mean(float(item.get("volume", 0) or 0) for item in previous)
    candle_range = high - low
    if candle_range <= 0:
        return None
    body_pct = abs(close - open_price) / candle_range * 100
    if body_pct < 10 or volume < avg_volume * 1.3:
        return None

    resistance = max(float(item["high"]) for item in previous)
    support = min(float(item["low"]) for item in previous)
    buffer = 0.0005
    vwap = candle.get("_vwap")
    if close > resistance * (1 + buffer) and (vwap is None or close > vwap):
        return "BUY"
    if close < support * (1 - buffer) and (vwap is None or close < vwap):
        return "SELL"
    return None


def print_stats(symbol: str, distances: list[float], entry_distances: list[float]) -> None:
    print(
        f"{symbol}: candles={len(distances)} avg={avg(distances):.3f}% "
        f"median={pct(distances, 50):.3f}% p75={pct(distances, 75):.3f}% "
        f"p90={pct(distances, 90):.3f}% p95={pct(distances, 95):.3f}% "
        f"entry_candidates={len(entry_distances)} entry_avg={avg(entry_distances):.3f}% "
        f"entry_median={pct(entry_distances, 50):.3f}% entry_p75={pct(entry_distances, 75):.3f}% "
        f"entry_p90={pct(entry_distances, 90):.3f}% entry_p95={pct(entry_distances, 95):.3f}%"
    )


def avg(values: list[float]) -> float:
    return mean(values) if values else 0.0


def pct(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile / 100)
    return ordered[index]


def add_vwap(candles: list[dict[str, Any]]) -> None:
    weighted_value = 0.0
    volume_total = 0.0
    for candle in candles:
        volume = float(candle.get("volume", 0) or 0)
        close = float(candle["close"])
        if volume > 0:
            typical = (float(candle["high"]) + float(candle["low"]) + close) / 3
            weighted_value += typical * volume
            volume_total += volume
        candle["_vwap"] = weighted_value / volume_total if volume_total > 0 else None


def candle_time(candle: dict[str, Any]) -> datetime:
    value = candle["date"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


if __name__ == "__main__":
    main()
