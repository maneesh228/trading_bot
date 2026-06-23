from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LearnedTrade:
    symbol: str
    side: str
    quantity: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None
    exit_price: float | None
    entry_reason: str
    exit_reason: str | None
    entry_candle: str
    entry_vwap_distance_pct: float | None
    entry_volume: float | None
    pnl: float | None
    pnl_pct: float | None
    mfe: float | None
    mae: float | None


def generate_learning_report(journal_path: str | Path, report_date: date | None = None) -> str:
    events = _load_events(journal_path)
    if report_date is not None:
        events = [
            event for event in events
            if _event_market_date(event) == report_date
        ]

    trades = _build_trades(events)
    lines = [
        f"Learning report date={report_date.isoformat() if report_date else 'all'}",
        f"Events={len(events)} trades={len(trades)}",
    ]
    closed = [trade for trade in trades if trade.pnl is not None]
    if closed:
        total_pnl = sum(trade.pnl or 0 for trade in closed)
        wins = sum(1 for trade in closed if (trade.pnl or 0) > 0)
        losses = sum(1 for trade in closed if (trade.pnl or 0) < 0)
        win_rate = (wins / len(closed)) * 100
        lines.append(
            f"Closed trades={len(closed)} wins={wins} losses={losses} "
            f"win_rate={win_rate:.2f}% gross_pnl={total_pnl:.2f}"
        )
    else:
        lines.append("Closed trades=0")

    open_trades = [trade for trade in trades if trade.pnl is None]
    if open_trades:
        lines.append(f"Open trades={len(open_trades)}")

    lines.extend(_group_summary(closed, "By symbol", lambda trade: trade.symbol))
    lines.extend(_group_summary(closed, "By entry candle", lambda trade: trade.entry_candle))
    lines.extend(_lesson_lines(closed))

    if trades:
        lines.append("Trades:")
        for trade in trades:
            pnl = "open" if trade.pnl is None else f"{trade.pnl:.2f} ({trade.pnl_pct:.2f}%)"
            mfe = "n/a" if trade.mfe is None else f"{trade.mfe:.2f}"
            mae = "n/a" if trade.mae is None else f"{trade.mae:.2f}"
            exit_text = "open" if trade.exit_time is None else f"{trade.exit_time:%H:%M} @{trade.exit_price:.2f}"
            lines.append(
                f"- {trade.symbol} {trade.side} qty={trade.quantity} "
                f"entry={trade.entry_time:%H:%M} @{trade.entry_price:.2f} "
                f"exit={exit_text} pnl={pnl} mfe={mfe} mae={mae} "
                f"candle={trade.entry_candle} vwap_dist={_fmt_pct(trade.entry_vwap_distance_pct)} "
                f"volume={_fmt_number(trade.entry_volume)}"
            )

    return "\n".join(lines)


def _load_events(journal_path: str | Path) -> list[dict[str, Any]]:
    path = Path(journal_path)
    if not path.exists():
        return []
    events = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _build_trades(events: list[dict[str, Any]]) -> list[LearnedTrade]:
    open_by_symbol: dict[str, dict[str, Any]] = {}
    ticks_by_symbol: dict[str, list[dict[str, Any]]] = {}
    trades: list[LearnedTrade] = []

    for event in events:
        tick = event.get("tick")
        symbol = event.get("symbol") or _request_symbol(event)
        if isinstance(tick, dict):
            symbol = tick.get("symbol", symbol)
            if symbol:
                ticks_by_symbol.setdefault(symbol, []).append(tick)

        if event.get("event_type") == "order_placed":
            order = event["order"]
            request = order["request"]
            open_by_symbol[request["symbol"]] = event
            continue

        if event.get("event_type") == "position_closed":
            position = event["position"]
            symbol = position["symbol"]
            entry_event = open_by_symbol.pop(symbol, None)
            if entry_event is None:
                continue
            trades.append(_make_trade(entry_event, event, ticks_by_symbol.get(symbol, [])))

    for entry_event in open_by_symbol.values():
        trades.append(_make_trade(entry_event, None, ticks_by_symbol.get(_request_symbol(entry_event), [])))

    return trades


def _make_trade(
    entry_event: dict[str, Any],
    exit_event: dict[str, Any] | None,
    ticks: list[dict[str, Any]],
) -> LearnedTrade:
    order = entry_event["order"]
    request = order["request"]
    symbol = request["symbol"]
    side = request["side"]
    quantity = int(request["quantity"])
    entry_price = float(request["price"])
    entry_time = _parse_time(entry_event.get("tick", {}).get("timestamp") or entry_event["recorded_at"])
    entry_snapshot = entry_event.get("indicator_snapshot", {})
    entry_candle = entry_event.get("candle_pattern", {}).get("name", "unknown")

    exit_time = None
    exit_price = None
    exit_reason = None
    pnl = None
    pnl_pct = None
    if exit_event is not None:
        exit_price = float(exit_event["exit_price"])
        exit_time = _parse_time(exit_event.get("tick", {}).get("timestamp") or exit_event["recorded_at"])
        exit_reason = exit_event.get("exit_reason")
        pnl = _pnl(side, entry_price, exit_price, quantity)
        pnl_pct = float(exit_event.get("pnl_pct", _pnl_pct(side, entry_price, exit_price)))

    mfe, mae = _mfe_mae(side, entry_price, entry_time, exit_time, ticks)
    return LearnedTrade(
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        entry_reason=request.get("reason", ""),
        exit_reason=exit_reason,
        entry_candle=entry_candle,
        entry_vwap_distance_pct=entry_snapshot.get("price_vs_vwap_pct"),
        entry_volume=entry_snapshot.get("volume"),
        pnl=pnl,
        pnl_pct=pnl_pct,
        mfe=mfe,
        mae=mae,
    )


def _mfe_mae(
    side: str,
    entry_price: float,
    entry_time: datetime,
    exit_time: datetime | None,
    ticks: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    relevant_prices = []
    for tick in ticks:
        tick_time = _parse_time(tick["timestamp"])
        if tick_time < entry_time:
            continue
        if exit_time is not None and tick_time > exit_time:
            continue
        relevant_prices.append(float(tick["price"]))

    if not relevant_prices:
        return None, None

    if side == "BUY":
        favorable = max(relevant_prices) - entry_price
        adverse = min(relevant_prices) - entry_price
    else:
        favorable = entry_price - min(relevant_prices)
        adverse = entry_price - max(relevant_prices)
    return round(favorable, 2), round(adverse, 2)


def _group_summary(
    trades: list[LearnedTrade],
    title: str,
    key_fn,
) -> list[str]:
    if not trades:
        return []
    groups: dict[str, list[LearnedTrade]] = {}
    for trade in trades:
        groups.setdefault(str(key_fn(trade)), []).append(trade)

    lines = [f"{title}:"]
    for key, group in sorted(groups.items()):
        pnl = sum(trade.pnl or 0 for trade in group)
        wins = sum(1 for trade in group if (trade.pnl or 0) > 0)
        win_rate = (wins / len(group)) * 100
        lines.append(f"- {key}: trades={len(group)} win_rate={win_rate:.2f}% pnl={pnl:.2f}")
    return lines


def _lesson_lines(trades: list[LearnedTrade]) -> list[str]:
    if not trades:
        return ["Lessons: no closed trades yet"]
    best = max(trades, key=lambda trade: trade.pnl or 0)
    worst = min(trades, key=lambda trade: trade.pnl or 0)
    avg_mfe = _average([trade.mfe for trade in trades if trade.mfe is not None])
    avg_mae = _average([trade.mae for trade in trades if trade.mae is not None])
    return [
        "Lessons:",
        f"- Best trade: {best.symbol} pnl={best.pnl:.2f} candle={best.entry_candle}",
        f"- Worst trade: {worst.symbol} pnl={worst.pnl:.2f} candle={worst.entry_candle}",
        f"- Average favorable move={avg_mfe:.2f} average adverse move={avg_mae:.2f}",
    ]


def _event_market_date(event: dict[str, Any]) -> date | None:
    raw = event.get("tick", {}).get("timestamp") or event.get("recorded_at")
    if not raw:
        return None
    return _parse_time(raw).date()


def _request_symbol(event: dict[str, Any]) -> str | None:
    request = event.get("request") or event.get("order", {}).get("request")
    if isinstance(request, dict):
        return request.get("symbol")
    return None


def _parse_time(raw: str) -> datetime:
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def _pnl(side: str, entry_price: float, exit_price: float, quantity: int) -> float:
    if side == "BUY":
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity


def _pnl_pct(side: str, entry_price: float, exit_price: float) -> float:
    if side == "BUY":
        return ((exit_price - entry_price) / entry_price) * 100
    return ((entry_price - exit_price) / entry_price) * 100


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}%"


def _fmt_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}"
