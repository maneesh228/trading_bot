from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from trading_bot.config import load_config
from trading_bot.execution import quantity_for_capital
from trading_bot.models import SignalSide
from trading_bot.token_store import load_runtime_credentials, make_kite_client


@dataclass(frozen=True)
class BlockedSignal:
    symbol: str
    side: SignalSide
    timestamp: datetime
    price: float
    reason: str


@dataclass(frozen=True)
class Outcome:
    signal: BlockedSignal
    exit_time: datetime | None
    exit_price: float
    exit_reason: str
    pnl_pct: float
    pnl: float
    best_pct: float
    worst_pct: float


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay market-regime-blocked signals against actual candles")
    parser.add_argument("--config", default="/opt/ai_trading_agent/config.yaml")
    parser.add_argument("--journal", default="/opt/ai_trading_agent/data/trade_journal.jsonl")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    config = load_config(args.config)
    trade_date = date.fromisoformat(args.date)
    blocked = load_blocked_signals(Path(args.journal), trade_date)
    if not blocked:
        print(f"No market-regime-blocked signals found for {trade_date}")
        return

    candles_by_symbol = fetch_candles(config, trade_date)
    outcomes = [
        simulate_outcome(signal, candles_by_symbol.get(signal.symbol, []), config)
        for signal in blocked
    ]
    outcomes = [item for item in outcomes if item is not None]

    wins = [item for item in outcomes if item.pnl > 0]
    losses = [item for item in outcomes if item.pnl < 0]
    flat = [item for item in outcomes if item.pnl == 0]
    print(f"BLOCKED_REPLAY date={trade_date} signals={len(blocked)} simulated={len(outcomes)}")
    print(
        f"would_win={len(wins)} would_loss={len(losses)} flat={len(flat)} "
        f"gross_pnl={sum(item.pnl for item in outcomes):.2f} "
        f"avg_pnl={sum(item.pnl for item in outcomes) / len(outcomes):.2f}"
    )
    print()
    for item in outcomes:
        signal = item.signal
        print(
            f"{signal.timestamp:%H:%M} {signal.symbol:10} {signal.side.value:4} "
            f"{signal.price:8.2f}->{item.exit_price:8.2f} "
            f"pnl={item.pnl:8.2f} pct={item.pnl_pct:6.2f}% "
            f"best={item.best_pct:6.2f}% worst={item.worst_pct:6.2f}% "
            f"exit={item.exit_reason}"
        )


def load_blocked_signals(path: Path, trade_date: date) -> list[BlockedSignal]:
    signals: list[BlockedSignal] = []
    seen: set[tuple[str, str, datetime]] = set()
    for line in path.open("r", encoding="utf-8"):
        if trade_date.isoformat() not in line:
            continue
        event = json.loads(line)
        if event.get("event_type") != "entry_signal_blocked":
            continue
        reason = str(event.get("reason", ""))
        if "market regime blocked" not in reason:
            continue
        signal = event.get("signal") or {}
        tick = event.get("tick") or {}
        side = SignalSide(signal["side"])
        timestamp = datetime.fromisoformat(tick["timestamp"])
        key = (event["symbol"], side.value, timestamp)
        if key in seen:
            continue
        seen.add(key)
        signals.append(
            BlockedSignal(
                symbol=event["symbol"],
                side=side,
                timestamp=timestamp,
                price=float(tick["price"]),
                reason=reason,
            )
        )
    return sorted(signals, key=lambda item: item.timestamp)


def fetch_candles(config: Any, trade_date: date) -> dict[str, list[dict[str, Any]]]:
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments(config.market.exchange)
    }
    candles_by_symbol = {}
    for item in config.watchlist:
        token = token_by_symbol.get(item.symbol)
        if token is None:
            continue
        candles_by_symbol[item.symbol] = kite.historical_data(
            instrument_token=token,
            from_date=trade_date,
            to_date=trade_date + timedelta(days=1),
            interval="5minute",
            continuous=False,
            oi=False,
        )
    return candles_by_symbol


def simulate_outcome(
    signal: BlockedSignal,
    candles: list[dict[str, Any]],
    config: Any,
) -> Outcome | None:
    future = [
        candle
        for candle in candles
        if candle_time(candle) > signal.timestamp and candle_time(candle).time().strftime("%H:%M") <= config.market.square_off_time
    ]
    if not future:
        return None

    stop_loss = abs(config.risk.per_trade_stop_loss_pct)
    target = abs(config.risk.per_trade_target_pct)
    trailing = config.risk.trailing_stop_loss_pct
    best_price = signal.price
    best_pct = 0.0
    worst_pct = 0.0

    for candle in future:
        price = float(candle["close"])
        pnl_pct = pnl_pct_for(signal.side, signal.price, price)
        best_price = better_price(signal.side, best_price, price)
        best_pct = max(best_pct, pnl_pct_for(signal.side, signal.price, best_price))
        worst_pct = min(worst_pct, pnl_pct)

        if pnl_pct <= -stop_loss:
            return build_outcome(signal, candle, price, "stop loss", pnl_pct, config, best_pct, worst_pct)
        if trailing is not None and best_pct > 0:
            trail_pct = pnl_pct_for(signal.side, best_price, price)
            if trail_pct <= -abs(trailing):
                return build_outcome(signal, candle, price, "trailing stop", pnl_pct, config, best_pct, worst_pct)
        if pnl_pct >= target:
            return build_outcome(signal, candle, price, "target", pnl_pct, config, best_pct, worst_pct)

    last = future[-1]
    price = float(last["close"])
    pnl_pct = pnl_pct_for(signal.side, signal.price, price)
    return build_outcome(signal, last, price, "square off", pnl_pct, config, best_pct, worst_pct)


def build_outcome(
    signal: BlockedSignal,
    candle: dict[str, Any],
    price: float,
    reason: str,
    pnl_pct: float,
    config: Any,
    best_pct: float,
    worst_pct: float,
) -> Outcome:
    quantity = quantity_for_capital(signal.price, config.risk.max_position_value)
    if signal.side == SignalSide.BUY:
        pnl = (price - signal.price) * quantity
    else:
        pnl = (signal.price - price) * quantity
    return Outcome(signal, candle_time(candle), price, reason, pnl_pct, pnl, best_pct, worst_pct)


def pnl_pct_for(side: SignalSide, entry: float, price: float) -> float:
    if side == SignalSide.BUY:
        return ((price - entry) / entry) * 100
    return ((entry - price) / entry) * 100


def better_price(side: SignalSide, current: float, price: float) -> float:
    if side == SignalSide.BUY:
        return max(current, price)
    return min(current, price)


def candle_time(candle: dict[str, Any]) -> datetime:
    value = candle["date"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


if __name__ == "__main__":
    main()
