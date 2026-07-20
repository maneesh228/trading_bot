from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from trading_bot.config import load_config
from trading_bot.execution import quantity_for_capital
from trading_bot.models import SignalSide
from trading_bot.token_store import load_runtime_credentials, make_kite_client


@dataclass(frozen=True)
class LearningExample:
    source: str
    symbol: str
    side: str
    entry_time: str
    exit_time: str | None
    entry_price: float
    exit_price: float | None
    quantity: int
    pnl: float
    pnl_pct: float
    label: str
    exit_reason: str
    block_reason: str | None
    candle_name: str
    body_pct: float | None
    upper_wick_pct: float | None
    lower_wick_pct: float | None
    vwap_distance_pct: float | None
    price_vs_open_pct: float | None
    volume: float | None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline learning examples from executed and blocked trades")
    parser.add_argument("--config", default="/opt/ai_trading_agent/config.yaml")
    parser.add_argument("--journal", default="/opt/ai_trading_agent/data/trade_journal.jsonl")
    parser.add_argument("--output", default="/opt/ai_trading_agent/data/learning_examples.jsonl")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)
    journal = Path(args.journal)

    executed = load_executed_examples(journal, from_date, to_date)
    blocked = load_blocked_signals(journal, from_date, to_date)
    blocked_examples = replay_blocked_examples(config, blocked)
    examples = sorted(executed + blocked_examples, key=lambda item: item.entry_time)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(to_jsonable(asdict(example)), separators=(",", ":")) + "\n")

    print(f"LEARNING_DATASET output={output}")
    print_summary("executed", executed)
    print_summary("blocked_replay", blocked_examples)
    print_summary("combined", examples)


def load_executed_examples(path: Path, from_date: date, to_date: date) -> list[LearningExample]:
    open_entries: dict[str, dict[str, Any]] = {}
    examples: list[LearningExample] = []
    for event in iter_events(path, from_date, to_date):
        event_type = event.get("event_type")
        if event_type == "order_placed":
            request = event.get("order", {}).get("request", {})
            symbol = request.get("symbol")
            if symbol:
                open_entries[symbol] = event
            continue
        if event_type != "position_closed":
            continue
        position = event.get("position", {})
        symbol = position.get("symbol")
        entry = open_entries.pop(symbol, None)
        if entry is None:
            continue
        example = executed_example(entry, event)
        if example is not None and from_date <= datetime.fromisoformat(example.exit_time).date() <= to_date:
            examples.append(example)
    return examples


def executed_example(entry: dict[str, Any], exit_event: dict[str, Any]) -> LearningExample | None:
    request = entry.get("order", {}).get("request", {})
    tick = entry.get("tick", {})
    candle = entry.get("candle_pattern", {}) or {}
    snapshot = entry.get("indicator_snapshot", {}) or {}
    symbol = request.get("symbol")
    side = request.get("side")
    if not symbol or side not in {"BUY", "SELL"}:
        return None
    entry_price = float(request.get("price") or 0)
    exit_price = float(exit_event.get("exit_price") or 0)
    quantity = int(request.get("quantity") or 0)
    pnl = float(exit_event.get("pnl") or pnl_for(side, entry_price, exit_price, quantity))
    pnl_pct = float(exit_event.get("pnl_pct") or pnl_pct_for(SignalSide(side), entry_price, exit_price))
    return LearningExample(
        source="executed",
        symbol=symbol,
        side=side,
        entry_time=str(tick.get("timestamp") or entry.get("recorded_at")),
        exit_time=str(exit_event.get("tick", {}).get("timestamp") or exit_event.get("recorded_at")),
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        pnl=pnl,
        pnl_pct=pnl_pct,
        label=label_for(pnl),
        exit_reason=str(exit_event.get("exit_reason") or "unknown"),
        block_reason=None,
        candle_name=str(candle.get("name") or "unknown"),
        body_pct=num(snapshot.get("body_pct") if snapshot else candle.get("body_pct")),
        upper_wick_pct=num(candle.get("upper_wick_pct")),
        lower_wick_pct=num(candle.get("lower_wick_pct")),
        vwap_distance_pct=num(snapshot.get("price_vs_vwap_pct")),
        price_vs_open_pct=num(snapshot.get("price_vs_open_pct")),
        volume=num(snapshot.get("volume")),
    )


def load_blocked_signals(path: Path, from_date: date, to_date: date) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in iter_events(path, from_date, to_date):
        if event.get("event_type") != "entry_signal_blocked":
            continue
        reason = str(event.get("reason") or "")
        if "market regime blocked" not in reason:
            continue
        signal = event.get("signal") or {}
        tick = event.get("tick") or {}
        key = (str(event.get("symbol")), str(signal.get("side")), str(tick.get("timestamp")))
        if key in seen:
            continue
        seen.add(key)
        blocked.append(event)
    return blocked


def replay_blocked_examples(config: Any, blocked: list[dict[str, Any]]) -> list[LearningExample]:
    if not blocked:
        return []
    candles_by_symbol = fetch_candles(config, {
        datetime.fromisoformat(event["tick"]["timestamp"]).date()
        for event in blocked
    })
    examples = []
    for event in blocked:
        example = replay_blocked_example(config, event, candles_by_symbol.get(event["symbol"], []))
        if example is not None:
            examples.append(example)
    return examples


def fetch_candles(config: Any, days: set[date]) -> dict[str, list[dict[str, Any]]]:
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments(config.market.exchange)
    }
    from_date = min(days)
    to_date = max(days) + timedelta(days=1)
    candles_by_symbol = {}
    for item in config.watchlist:
        token = token_by_symbol.get(item.symbol)
        if token is None:
            continue
        candles_by_symbol[item.symbol] = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval="5minute",
            continuous=False,
            oi=False,
        )
    return candles_by_symbol


def replay_blocked_example(config: Any, event: dict[str, Any], candles: list[dict[str, Any]]) -> LearningExample | None:
    signal = event.get("signal") or {}
    tick = event.get("tick") or {}
    side = SignalSide(signal["side"])
    entry_time = datetime.fromisoformat(tick["timestamp"])
    entry_price = float(tick["price"])
    quantity = quantity_for_capital(entry_price, config.risk.max_position_value)
    future = [
        candle
        for candle in candles
        if candle_time(candle) > entry_time
        and candle_time(candle).date() == entry_time.date()
        and candle_time(candle).time().strftime("%H:%M") <= config.market.square_off_time
    ]
    if not future:
        return None

    stop_loss = abs(config.risk.per_trade_stop_loss_pct)
    target = abs(config.risk.per_trade_target_pct)
    trailing = config.risk.trailing_stop_loss_pct
    best_price = entry_price
    exit_candle = future[-1]
    exit_price = float(exit_candle["close"])
    exit_reason = "square off replay"

    for candle in future:
        price = float(candle["close"])
        best_price = better_price(side, best_price, price)
        pnl_pct = pnl_pct_for(side, entry_price, price)
        best_pct = pnl_pct_for(side, entry_price, best_price)
        if pnl_pct <= -stop_loss:
            exit_candle, exit_price, exit_reason = candle, price, "stop loss replay"
            break
        if trailing is not None and best_pct > 0 and pnl_pct_for(side, best_price, price) <= -abs(trailing):
            exit_candle, exit_price, exit_reason = candle, price, "trailing stop replay"
            break
        if pnl_pct >= target:
            exit_candle, exit_price, exit_reason = candle, price, "target replay"
            break

    pnl = pnl_for(side.value, entry_price, exit_price, quantity)
    candle = event.get("candle_pattern", {}) or {}
    snapshot = event.get("indicator_snapshot", {}) or {}
    return LearningExample(
        source="blocked_replay",
        symbol=event["symbol"],
        side=side.value,
        entry_time=tick["timestamp"],
        exit_time=candle_time(exit_candle).isoformat(),
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        pnl=pnl,
        pnl_pct=pnl_pct_for(side, entry_price, exit_price),
        label=label_for(pnl),
        exit_reason=exit_reason,
        block_reason=str(event.get("reason") or ""),
        candle_name=str(candle.get("name") or "unknown"),
        body_pct=num(snapshot.get("body_pct") if snapshot else candle.get("body_pct")),
        upper_wick_pct=num(candle.get("upper_wick_pct")),
        lower_wick_pct=num(candle.get("lower_wick_pct")),
        vwap_distance_pct=num(snapshot.get("price_vs_vwap_pct")),
        price_vs_open_pct=num(snapshot.get("price_vs_open_pct")),
        volume=num(snapshot.get("volume")),
    )


def iter_events(path: Path, from_date: date, to_date: date):
    candidates = date_strings(from_date, to_date)
    for line in path.open("r", encoding="utf-8"):
        if not any(day in line for day in candidates):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def print_summary(label: str, examples: list[LearningExample]) -> None:
    wins = sum(1 for item in examples if item.pnl > 0)
    losses = sum(1 for item in examples if item.pnl < 0)
    pnl = sum(item.pnl for item in examples)
    win_rate = (wins / len(examples) * 100) if examples else 0.0
    print(f"{label}: examples={len(examples)} wins={wins} losses={losses} win_rate={win_rate:.2f}% pnl={pnl:.2f}")


def date_strings(from_date: date, to_date: date) -> list[str]:
    values = []
    current = from_date
    while current <= to_date:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def pnl_for(side: str, entry: float, exit_price: float, quantity: int) -> float:
    if side == "BUY":
        return (exit_price - entry) * quantity
    return (entry - exit_price) * quantity


def pnl_pct_for(side: SignalSide, entry: float, price: float) -> float:
    if side == SignalSide.BUY:
        return ((price - entry) / entry) * 100
    return ((entry - price) / entry) * 100


def better_price(side: SignalSide, current: float, price: float) -> float:
    if side == SignalSide.BUY:
        return max(current, price)
    return min(current, price)


def label_for(pnl: float) -> str:
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "flat"


def candle_time(candle: dict[str, Any]) -> datetime:
    value = candle["date"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


if __name__ == "__main__":
    main()
