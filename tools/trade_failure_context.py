from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import mean, median
from typing import Any

from trading_bot.config import load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


TODAY_TRADES = [
    ("BHEL", "BUY", "2026-07-13 09:35", 405.70, "loss"),
    ("CANBK", "BUY", "2026-07-13 09:45", 128.69, "loss"),
    ("ASHOKLEY", "BUY", "2026-07-13 09:50", 156.55, "win"),
    ("MOTHERSON", "BUY", "2026-07-13 10:00", 142.74, "loss"),
    ("BHEL", "BUY", "2026-07-13 10:25", 406.80, "loss"),
]


@dataclass(frozen=True)
class ContextRow:
    symbol: str
    side: str
    timestamp: datetime
    close: float
    result: str
    pnl_pct: float
    vwap_dist: float
    prior_3_move: float
    prior_6_move: float
    day_move_from_open: float
    range_position: float
    volume_ratio_20: float
    body_pct: float
    close_strength: float
    upper_wick_pct: float
    lower_wick_pct: float
    opening_range_break_pct: float
    next_1_move: float
    next_3_move: float
    next_6_move: float


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose failed trade context against historical signals")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--today-only", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments(config.market.exchange)
    }

    symbols = [item.symbol for item in config.watchlist]
    from_date = date.today() - timedelta(days=args.days)
    to_date = date.today() + timedelta(days=1)
    candles_by_symbol = {}
    for symbol in symbols:
        token = token_by_symbol.get(symbol)
        if token is None:
            continue
        candles_by_symbol[symbol] = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=args.interval,
            continuous=False,
            oi=False,
        )

    print(f"TRADE_FAILURE_CONTEXT from={from_date} to={to_date} interval={args.interval}")
    print_today_trades(candles_by_symbol)
    if not args.today_only:
        rows = collect_breakout_context(candles_by_symbol)
        print_population_summary(rows)
        print_rule_slices(rows)


def print_today_trades(candles_by_symbol: dict[str, list[dict[str, Any]]]) -> None:
    print("Today's trade contexts:")
    for symbol, side, raw_time, entry, result in TODAY_TRADES:
        ts = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
        day_candles = day_rows(candles_by_symbol.get(symbol, []), ts.date())
        add_vwap(day_candles)
        index = next(
            (
                i
                for i, candle in enumerate(day_candles)
                if candle_time(candle).date() == ts.date() and candle_time(candle).time() == ts.time()
            ),
            None,
        )
        if index is None:
            print(f"- {symbol} {raw_time}: missing candle")
            continue
        row = context_for_index(symbol, side, day_candles, index, entry, result)
        print(format_context(row))


def collect_breakout_context(candles_by_symbol: dict[str, list[dict[str, Any]]]) -> list[ContextRow]:
    rows: list[ContextRow] = []
    for symbol, candles in candles_by_symbol.items():
        by_day: dict[date, list[dict[str, Any]]] = {}
        for candle in candles:
            by_day.setdefault(candle_time(candle).date(), []).append(candle)
        for day_candles in by_day.values():
            day_candles.sort(key=candle_time)
            add_vwap(day_candles)
            for index in range(20, len(day_candles) - 6):
                ts = candle_time(day_candles[index])
                if ts.time() < time(9, 30) or ts.time() > time(14, 15):
                    continue
                side = breakout_side(day_candles[index], day_candles[index - 20 : index])
                if side is None:
                    continue
                outcome, pnl_pct = simulate_outcome(side, float(day_candles[index]["close"]), day_candles[index + 1 :])
                rows.append(
                    context_for_index(
                        symbol,
                        side,
                        day_candles,
                        index,
                        float(day_candles[index]["close"]),
                        outcome,
                        pnl_pct=pnl_pct,
                    )
                )
    return rows


def context_for_index(
    symbol: str,
    side: str,
    day_candles: list[dict[str, Any]],
    index: int,
    entry: float,
    result: str,
    pnl_pct: float | None = None,
) -> ContextRow:
    candle = day_candles[index]
    ts = candle_time(candle)
    close = float(candle["close"])
    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    candle_range = max(high - low, 0.01)
    body_pct = abs(close - open_price) / candle_range * 100
    close_strength = (close - low) / candle_range * 100
    upper_wick_pct = (high - max(open_price, close)) / candle_range * 100
    lower_wick_pct = (min(open_price, close) - low) / candle_range * 100
    previous_20 = day_candles[max(0, index - 20) : index]
    avg_volume = mean(float(item.get("volume", 0) or 0) for item in previous_20) if previous_20 else 0.0
    volume = float(candle.get("volume", 0) or 0)
    volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0
    vwap = candle.get("_vwap")
    vwap_dist = directional_vwap_pct(side, close, vwap)
    prior_3_move = prior_move(side, day_candles, index, 3)
    prior_6_move = prior_move(side, day_candles, index, 6)
    day_open = float(day_candles[0]["open"])
    day_move = directional_pct(side, day_open, close)
    day_high = max(float(item["high"]) for item in day_candles[: index + 1])
    day_low = min(float(item["low"]) for item in day_candles[: index + 1])
    range_position = (close - day_low) / max(day_high - day_low, 0.01) * 100
    if side == "SELL":
        range_position = 100 - range_position
    opening_rows = [item for item in day_candles if candle_time(item).time() <= time(9, 30)]
    opening_high = max(float(item["high"]) for item in opening_rows) if opening_rows else day_high
    opening_low = min(float(item["low"]) for item in opening_rows) if opening_rows else day_low
    opening_level = opening_high if side == "BUY" else opening_low
    opening_break_pct = directional_pct(side, opening_level, close)
    next_1 = future_move(side, close, day_candles, index, 1)
    next_3 = future_move(side, close, day_candles, index, 3)
    next_6 = future_move(side, close, day_candles, index, 6)
    if pnl_pct is None:
        pnl_pct = 0.0
    return ContextRow(
        symbol=symbol,
        side=side,
        timestamp=ts,
        close=entry,
        result=result,
        pnl_pct=pnl_pct,
        vwap_dist=vwap_dist,
        prior_3_move=prior_3_move,
        prior_6_move=prior_6_move,
        day_move_from_open=day_move,
        range_position=range_position,
        volume_ratio_20=volume_ratio,
        body_pct=body_pct,
        close_strength=close_strength,
        upper_wick_pct=upper_wick_pct,
        lower_wick_pct=lower_wick_pct,
        opening_range_break_pct=opening_break_pct,
        next_1_move=next_1,
        next_3_move=next_3,
        next_6_move=next_6,
    )


def print_population_summary(rows: list[ContextRow]) -> None:
    print(f"\nHistorical breakout-like population: rows={len(rows)}")
    wins = [row for row in rows if row.result == "win"]
    losses = [row for row in rows if row.result == "loss"]
    print(f"wins={len(wins)} losses={len(losses)} win_rate={percentage(len(wins), len(rows)):.2f}%")
    for metric in [
        "vwap_dist",
        "prior_3_move",
        "prior_6_move",
        "day_move_from_open",
        "range_position",
        "volume_ratio_20",
        "body_pct",
        "close_strength",
        "upper_wick_pct",
        "lower_wick_pct",
        "opening_range_break_pct",
        "next_1_move",
        "next_3_move",
    ]:
        print_metric(metric, wins, losses)


def print_rule_slices(rows: list[ContextRow]) -> None:
    print("\nRule slices:")
    slices = [
        ("vwap_dist >= 1.0", lambda row: row.vwap_dist >= 1.0),
        ("vwap_dist 0.5..1.0", lambda row: 0.5 <= row.vwap_dist < 1.0),
        ("prior_3_move >= 0.7", lambda row: row.prior_3_move >= 0.7),
        ("prior_6_move >= 1.0", lambda row: row.prior_6_move >= 1.0),
        ("day_move >= 1.0", lambda row: row.day_move_from_open >= 1.0),
        ("range_position >= 90", lambda row: row.range_position >= 90),
        ("volume_ratio < 1.0", lambda row: row.volume_ratio_20 < 1.0),
        ("volume_ratio < 1.3", lambda row: row.volume_ratio_20 < 1.3),
        ("upper_wick >= 10", lambda row: row.upper_wick_pct >= 10),
        ("next_1 <= 0", lambda row: row.next_1_move <= 0),
        ("next_3 <= -0.2", lambda row: row.next_3_move <= -0.2),
    ]
    for label, predicate in slices:
        subset = [row for row in rows if predicate(row)]
        if len(subset) < 5:
            continue
        wins = sum(1 for row in subset if row.result == "win")
        avg = mean(row.pnl_pct for row in subset)
        print(f"- {label}: n={len(subset)} win_rate={percentage(wins, len(subset)):.2f}% avg_pnl={avg:.3f}%")


def print_metric(metric: str, wins: list[ContextRow], losses: list[ContextRow]) -> None:
    win_values = [float(getattr(row, metric)) for row in wins]
    loss_values = [float(getattr(row, metric)) for row in losses]
    if not win_values or not loss_values:
        return
    print(
        f"{metric}: win_med={median(win_values):.3f} "
        f"loss_med={median(loss_values):.3f} "
        f"win_avg={mean(win_values):.3f} loss_avg={mean(loss_values):.3f}"
    )


def format_context(row: ContextRow) -> str:
    return (
        f"- {row.symbol} {row.side} {row.timestamp:%H:%M} result={row.result} "
        f"entry={row.close:.2f} vwap_dist={row.vwap_dist:.2f}% "
        f"prior3={row.prior_3_move:.2f}% prior6={row.prior_6_move:.2f}% "
        f"day_move={row.day_move_from_open:.2f}% range_pos={row.range_position:.1f}% "
        f"vol_ratio={row.volume_ratio_20:.2f} body={row.body_pct:.1f}% "
        f"close_strength={row.close_strength:.1f}% upper_wick={row.upper_wick_pct:.1f}% "
        f"next1={row.next_1_move:.2f}% next3={row.next_3_move:.2f}%"
    )


def day_rows(candles: list[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    rows = [candle for candle in candles if candle_time(candle).date() == day]
    rows.sort(key=candle_time)
    return rows


def breakout_side(candle: dict[str, Any], previous: list[dict[str, Any]]) -> str | None:
    if len(previous) < 20:
        return None
    close = float(candle["close"])
    resistance = max(float(item["high"]) for item in previous)
    support = min(float(item["low"]) for item in previous)
    buffer = 0.0005
    vwap = candle.get("_vwap")
    if close > resistance * (1 + buffer) and (vwap is None or close > vwap):
        return "BUY"
    if close < support * (1 - buffer) and (vwap is None or close < vwap):
        return "SELL"
    return None


def simulate_outcome(side: str, entry: float, future: list[dict[str, Any]]) -> tuple[str, float]:
    stop_pct = 0.6
    target_pct = 1.0
    last_close = entry
    for candle in future:
        high = float(candle["high"])
        low = float(candle["low"])
        last_close = float(candle["close"])
        if side == "BUY":
            if low <= entry * (1 - stop_pct / 100):
                return "loss", -stop_pct
            if high >= entry * (1 + target_pct / 100):
                return "win", target_pct
        else:
            if high >= entry * (1 + stop_pct / 100):
                return "loss", -stop_pct
            if low <= entry * (1 - target_pct / 100):
                return "win", target_pct
    pnl_pct = directional_pct(side, entry, last_close)
    if pnl_pct > 0:
        return "win", pnl_pct
    if pnl_pct < 0:
        return "loss", pnl_pct
    return "flat", 0.0


def prior_move(side: str, day_candles: list[dict[str, Any]], index: int, candles_back: int) -> float:
    start_index = max(0, index - candles_back)
    start = float(day_candles[start_index]["close"])
    end = float(day_candles[index]["close"])
    return directional_pct(side, start, end)


def future_move(side: str, entry: float, day_candles: list[dict[str, Any]], index: int, candles_forward: int) -> float:
    future_index = min(len(day_candles) - 1, index + candles_forward)
    end = float(day_candles[future_index]["close"])
    return directional_pct(side, entry, end)


def directional_pct(side: str, start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    if side == "BUY":
        return (end - start) / start * 100
    return (start - end) / start * 100


def directional_vwap_pct(side: str, close: float, vwap: float | None) -> float:
    if vwap is None or vwap <= 0:
        return 0.0
    if side == "BUY":
        return (close - vwap) / vwap * 100
    return (vwap - close) / vwap * 100


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
