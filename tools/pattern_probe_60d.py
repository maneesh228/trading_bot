from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import mean
from typing import Any

from trading_bot.config import load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


@dataclass(frozen=True)
class Outcome:
    pnl_pct: float
    result: str


@dataclass
class PatternStats:
    candidates: int = 0
    wins: int = 0
    losses: int = 0
    failed_breakouts: int = 0
    failed_wins: int = 0
    failed_losses: int = 0
    retests: int = 0
    retest_wins: int = 0
    retest_losses: int = 0
    pnl_pct: list[float] | None = None
    retest_pnl_pct: list[float] | None = None

    def __post_init__(self) -> None:
        self.pnl_pct = []
        self.retest_pnl_pct = []


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
    print(f"Pattern probe interval={args.interval} from={from_date} to={to_date}")
    print("Rules: 20-candle S/R breakout, 0.05% buffer, body>=10%, volume spike>=1.3x, VWAP side, retest within 3 candles")

    total = PatternStats()
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
        stats = analyze_symbol(candles)
        add_stats(total, stats)
        print_stats(watch.symbol, stats)
    print_stats("TOTAL", total)


def analyze_symbol(candles: list[dict[str, Any]]) -> PatternStats:
    stats = PatternStats()
    by_day: dict[date, list[dict[str, Any]]] = {}
    for candle in candles:
        ts = candle_time(candle)
        by_day.setdefault(ts.date(), []).append(candle)

    for day_candles in by_day.values():
        day_candles.sort(key=candle_time)
        add_vwap(day_candles)
        for index in range(20, len(day_candles) - 1):
            candle = day_candles[index]
            ts = candle_time(candle)
            if ts.time() < time(9, 15) or ts.time() >= time(14, 30):
                continue
            previous = day_candles[index - 20 : index]
            signal = breakout_signal(candle, previous)
            if signal is None:
                continue

            side, level = signal
            stats.candidates += 1
            outcome = simulate_outcome(side, float(candle["close"]), day_candles[index + 1 :])
            record_outcome(stats, outcome)

            next_candle = day_candles[index + 1]
            if is_failed_breakout(side, level, candle, next_candle):
                stats.failed_breakouts += 1
                if outcome.result == "win":
                    stats.failed_wins += 1
                elif outcome.result == "loss":
                    stats.failed_losses += 1

            retest_index = find_retest(side, level, day_candles[index + 1 : index + 4])
            if retest_index is not None:
                retest_candle = day_candles[index + 1 + retest_index]
                retest_outcome = simulate_outcome(
                    side,
                    float(retest_candle["close"]),
                    day_candles[index + 2 + retest_index :],
                )
                stats.retests += 1
                stats.retest_pnl_pct.append(retest_outcome.pnl_pct)
                if retest_outcome.result == "win":
                    stats.retest_wins += 1
                elif retest_outcome.result == "loss":
                    stats.retest_losses += 1
    return stats


def breakout_signal(candle: dict[str, Any], previous: list[dict[str, Any]]) -> tuple[str, float] | None:
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
        return "BUY", resistance
    if close < support * (1 - buffer) and (vwap is None or close < vwap):
        return "SELL", support
    return None


def is_failed_breakout(side: str, level: float, signal_candle: dict[str, Any], next_candle: dict[str, Any]) -> bool:
    signal_close = float(signal_candle["close"])
    next_close = float(next_candle["close"])
    if side == "BUY":
        return next_close <= level or next_close < signal_close
    return next_close >= level or next_close > signal_close


def find_retest(side: str, level: float, candles: list[dict[str, Any]]) -> int | None:
    tolerance = 0.0005
    for index, candle in enumerate(candles):
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        if side == "BUY" and low <= level * (1 + tolerance) and close > level:
            return index
        if side == "SELL" and high >= level * (1 - tolerance) and close < level:
            return index
    return None


def simulate_outcome(side: str, entry: float, future: list[dict[str, Any]]) -> Outcome:
    stop_pct = 0.6
    target_pct = 1.0
    last_close = entry
    for candle in future:
        high = float(candle["high"])
        low = float(candle["low"])
        last_close = float(candle["close"])
        if side == "BUY":
            if low <= entry * (1 - stop_pct / 100):
                return Outcome(-stop_pct, "loss")
            if high >= entry * (1 + target_pct / 100):
                return Outcome(target_pct, "win")
        else:
            if high >= entry * (1 + stop_pct / 100):
                return Outcome(-stop_pct, "loss")
            if low <= entry * (1 - target_pct / 100):
                return Outcome(target_pct, "win")
    pnl_pct = ((last_close - entry) / entry) * 100
    if side == "SELL":
        pnl_pct *= -1
    if pnl_pct > 0:
        return Outcome(pnl_pct, "win")
    if pnl_pct < 0:
        return Outcome(pnl_pct, "loss")
    return Outcome(0.0, "flat")


def record_outcome(stats: PatternStats, outcome: Outcome) -> None:
    stats.pnl_pct.append(outcome.pnl_pct)
    if outcome.result == "win":
        stats.wins += 1
    elif outcome.result == "loss":
        stats.losses += 1


def add_stats(total: PatternStats, stats: PatternStats) -> None:
    total.candidates += stats.candidates
    total.wins += stats.wins
    total.losses += stats.losses
    total.failed_breakouts += stats.failed_breakouts
    total.failed_wins += stats.failed_wins
    total.failed_losses += stats.failed_losses
    total.retests += stats.retests
    total.retest_wins += stats.retest_wins
    total.retest_losses += stats.retest_losses
    total.pnl_pct.extend(stats.pnl_pct)
    total.retest_pnl_pct.extend(stats.retest_pnl_pct)


def print_stats(symbol: str, stats: PatternStats) -> None:
    win_rate = percentage(stats.wins, stats.candidates)
    failed_loss_rate = percentage(stats.failed_losses, stats.failed_breakouts)
    retest_win_rate = percentage(stats.retest_wins, stats.retests)
    avg_pnl = mean(stats.pnl_pct) if stats.pnl_pct else 0.0
    retest_avg_pnl = mean(stats.retest_pnl_pct) if stats.retest_pnl_pct else 0.0
    print(
        f"{symbol}: breakouts={stats.candidates} win_rate={win_rate:.2f}% "
        f"avg_pnl={avg_pnl:.3f}% failed={stats.failed_breakouts} "
        f"failed_loss_rate={failed_loss_rate:.2f}% retests={stats.retests} "
        f"retest_win_rate={retest_win_rate:.2f}% retest_avg_pnl={retest_avg_pnl:.3f}%"
    )


def percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator * 100


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
