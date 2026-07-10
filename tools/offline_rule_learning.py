from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class TradeRow:
    symbol: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    candle_name: str
    candle_direction: str
    body_pct: float | None
    upper_wick_pct: float | None
    lower_wick_pct: float | None
    vwap_distance_pct: float | None
    price_vs_open_pct: float | None
    volume: float | None


@dataclass(frozen=True)
class CostConfig:
    brokerage_rate: float = 0.0003
    brokerage_cap: float = 20.0
    stt_sell_rate: float = 0.00025
    exchange_rate: float = 0.0000297
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00003
    gst_rate: float = 0.18
    slippage_bps: float = 1.0


@dataclass(frozen=True)
class CostEstimate:
    turnover: float
    entry_value: float
    exit_value: float
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float
    slippage: float

    @property
    def statutory_charges(self) -> float:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst

    @property
    def total_cost(self) -> float:
        return self.statutory_charges + self.slippage


@dataclass
class GroupStats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0

    def add(self, trade: TradeRow) -> None:
        self.trades += 1
        self.pnl += trade.pnl
        if trade.pnl > 0:
            self.wins += 1
        elif trade.pnl < 0:
            self.losses += 1

    @property
    def win_rate(self) -> float:
        if self.trades == 0:
            return 0.0
        return (self.wins / self.trades) * 100

    @property
    def avg_pnl(self) -> float:
        if self.trades == 0:
            return 0.0
        return self.pnl / self.trades


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline rule-learning report from trade journal")
    parser.add_argument("--journal", default="data/trade_journal.jsonl")
    parser.add_argument("--from-date", help="YYYY-MM-DD market date inclusive")
    parser.add_argument("--to-date", help="YYYY-MM-DD market date inclusive")
    parser.add_argument("--min-trades", type=int, default=2)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--slippage-bps", type=float, default=1.0, help="estimated slippage per side in basis points")
    args = parser.parse_args()

    from_date = _parse_date(args.from_date)
    to_date = _parse_date(args.to_date)
    trades = load_trade_rows(Path(args.journal), from_date=from_date, to_date=to_date)
    cost_config = CostConfig(slippage_bps=args.slippage_bps)
    print_report(
        trades,
        min_trades=args.min_trades,
        top=args.top,
        from_date=from_date,
        to_date=to_date,
        cost_config=cost_config,
    )


def load_trade_rows(path: Path, *, from_date: date | None, to_date: date | None) -> list[TradeRow]:
    open_entries: dict[str, dict[str, Any]] = {}
    rows: list[TradeRow] = []

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not _line_maybe_in_range(line, from_date, to_date):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

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
            entry_event = open_entries.pop(symbol, None)
            if not symbol or entry_event is None:
                continue

            row = make_trade_row(entry_event, event)
            if row is None:
                continue
            market_day = row.exit_time.date()
            if from_date and market_day < from_date:
                continue
            if to_date and market_day > to_date:
                continue
            rows.append(row)

    return rows


def make_trade_row(entry_event: dict[str, Any], exit_event: dict[str, Any]) -> TradeRow | None:
    request = entry_event.get("order", {}).get("request", {})
    position = exit_event.get("position", {})
    symbol = request.get("symbol") or position.get("symbol")
    side = request.get("side") or position.get("side")
    if not symbol or side not in {"BUY", "SELL"}:
        return None

    quantity = int(request.get("quantity") or position.get("quantity") or 0)
    entry_price = float(request.get("price") or position.get("entry_price") or 0)
    exit_price = float(exit_event.get("exit_price") or 0)
    if quantity <= 0 or entry_price <= 0 or exit_price <= 0:
        return None

    entry_time = _parse_datetime(entry_event.get("tick", {}).get("timestamp") or entry_event.get("recorded_at"))
    exit_time = _parse_datetime(exit_event.get("tick", {}).get("timestamp") or exit_event.get("recorded_at"))
    candle = entry_event.get("candle_pattern", {}) or {}
    snapshot = entry_event.get("indicator_snapshot", {}) or {}
    pnl = _pnl(side, entry_price, exit_price, quantity)

    return TradeRow(
        symbol=str(symbol),
        side=str(side),
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        pnl=pnl,
        pnl_pct=float(exit_event.get("pnl_pct") or _pnl_pct(side, entry_price, exit_price)),
        exit_reason=str(exit_event.get("exit_reason") or "unknown"),
        candle_name=str(candle.get("name") or "unknown"),
        candle_direction=str(candle.get("direction") or "unknown"),
        body_pct=_num(snapshot.get("body_pct") if snapshot else candle.get("body_pct")),
        upper_wick_pct=_num(candle.get("upper_wick_pct")),
        lower_wick_pct=_num(candle.get("lower_wick_pct")),
        vwap_distance_pct=_num(snapshot.get("price_vs_vwap_pct")),
        price_vs_open_pct=_num(snapshot.get("price_vs_open_pct")),
        volume=_num(snapshot.get("volume")),
    )


def print_report(
    trades: list[TradeRow],
    *,
    min_trades: int,
    top: int,
    from_date: date | None,
    to_date: date | None,
    cost_config: CostConfig,
) -> None:
    print(
        "OFFLINE_RULE_LEARNING "
        f"from={from_date.isoformat() if from_date else 'all'} "
        f"to={to_date.isoformat() if to_date else 'all'}"
    )
    print_summary("ALL", trades)
    if not trades:
        print("No closed trades found.")
        return
    print_cost_summary(trades, cost_config)

    group_specs: list[tuple[str, Callable[[TradeRow], str]]] = [
        ("symbol", lambda trade: trade.symbol),
        ("side", lambda trade: trade.side),
        ("symbol+side", lambda trade: f"{trade.symbol} {trade.side}"),
        ("entry_time_bucket", lambda trade: time_bucket(trade.entry_time)),
        ("candle", lambda trade: trade.candle_name),
        ("vwap_distance", lambda trade: numeric_bucket(trade.vwap_distance_pct, [-1.0, -0.5, -0.2, 0.0, 0.2, 0.5, 1.0], "%")),
        ("body_pct", lambda trade: numeric_bucket(trade.body_pct, [10, 25, 50, 75], "%")),
        ("upper_wick_pct", lambda trade: numeric_bucket(trade.upper_wick_pct, [10, 30, 55, 75], "%")),
        ("lower_wick_pct", lambda trade: numeric_bucket(trade.lower_wick_pct, [10, 30, 55, 75], "%")),
        ("exit_reason", lambda trade: exit_bucket(trade.exit_reason)),
    ]

    for title, key_fn in group_specs:
        print()
        print_group(title, trades, key_fn, min_trades=min_trades, top=top)

    print()
    print("Rule candidates:")
    for line in rule_candidates(trades, min_trades=min_trades):
        print(f"- {line}")

    print()
    print("Watchlist checks:")
    for line in watchlist_checks(trades):
        print(f"- {line}")


def print_summary(label: str, trades: list[TradeRow]) -> None:
    stats = GroupStats()
    for trade in trades:
        stats.add(trade)
    print(
        f"{label}: trades={stats.trades} wins={stats.wins} losses={stats.losses} "
        f"win_rate={stats.win_rate:.2f}% pnl={stats.pnl:.2f} avg_pnl={stats.avg_pnl:.2f}"
    )


def print_cost_summary(trades: list[TradeRow], config: CostConfig) -> None:
    costs = [estimate_cost(trade, config) for trade in trades]
    gross_pnl = sum(trade.pnl for trade in trades)
    turnover = sum(cost.turnover for cost in costs)
    brokerage = sum(cost.brokerage for cost in costs)
    statutory = sum(cost.statutory_charges for cost in costs)
    slippage = sum(cost.slippage for cost in costs)
    total_cost = sum(cost.total_cost for cost in costs)
    net_pnl = gross_pnl - total_cost
    peak_exposure = estimate_peak_exposure(trades)
    net_return = 0.0 if peak_exposure <= 0 else (net_pnl / peak_exposure) * 100
    gross_return = 0.0 if peak_exposure <= 0 else (gross_pnl / peak_exposure) * 100

    print(
        "ESTIMATED_COSTS "
        f"turnover={turnover:.2f} peak_exposure={peak_exposure:.2f} "
        f"brokerage={brokerage:.2f} statutory={statutory:.2f} "
        f"slippage={slippage:.2f} total_cost={total_cost:.2f} "
        f"gross_pnl={gross_pnl:.2f} net_pnl={net_pnl:.2f} "
        f"gross_return={gross_return:.2f}% net_return={net_return:.2f}% "
        f"slippage_bps_per_side={config.slippage_bps:.2f}"
    )


def print_group(
    title: str,
    trades: list[TradeRow],
    key_fn: Callable[[TradeRow], str],
    *,
    min_trades: int,
    top: int,
) -> None:
    groups: dict[str, GroupStats] = defaultdict(GroupStats)
    for trade in trades:
        groups[key_fn(trade)].add(trade)

    rows = [
        (key, stats)
        for key, stats in groups.items()
        if stats.trades >= min_trades
    ]
    rows.sort(key=lambda item: (item[1].pnl, item[1].win_rate), reverse=True)

    print(f"By {title} - best:")
    for key, stats in rows[:top]:
        print(_format_group_row(key, stats))

    print(f"By {title} - worst:")
    for key, stats in sorted(rows, key=lambda item: (item[1].pnl, item[1].win_rate))[:top]:
        print(_format_group_row(key, stats))


def rule_candidates(trades: list[TradeRow], *, min_trades: int) -> list[str]:
    candidates = []
    checks: list[tuple[str, Callable[[TradeRow], str]]] = [
        ("symbol", lambda trade: trade.symbol),
        ("symbol+side", lambda trade: f"{trade.symbol} {trade.side}"),
        ("time", lambda trade: time_bucket(trade.entry_time)),
        ("vwap", lambda trade: numeric_bucket(trade.vwap_distance_pct, [-1.0, -0.5, -0.2, 0.0, 0.2, 0.5, 1.0], "%")),
        ("candle", lambda trade: trade.candle_name),
    ]
    for title, key_fn in checks:
        groups: dict[str, GroupStats] = defaultdict(GroupStats)
        for trade in trades:
            groups[key_fn(trade)].add(trade)
        for key, stats in groups.items():
            if stats.trades < min_trades:
                continue
            if stats.win_rate <= 35 or stats.pnl < 0:
                candidates.append(
                    f"review/block weak {title}={key}: trades={stats.trades} "
                    f"win_rate={stats.win_rate:.2f}% pnl={stats.pnl:.2f}"
                )
            elif stats.win_rate >= 60 and stats.pnl > 0:
                candidates.append(
                    f"prefer strong {title}={key}: trades={stats.trades} "
                    f"win_rate={stats.win_rate:.2f}% pnl={stats.pnl:.2f}"
                )
    candidates.sort()
    return candidates or ["not enough repeated patterns yet"]


def watchlist_checks(trades: list[TradeRow]) -> list[str]:
    lines: list[str] = []
    lines.extend(target_reentry_checks(trades))
    lines.extend(trailing_giveback_checks(trades))
    return lines or ["no target re-entry or trailing-giveback cases found"]


def target_reentry_checks(trades: list[TradeRow]) -> list[str]:
    lines: list[str] = []
    by_day_symbol: dict[tuple[date, str], list[TradeRow]] = defaultdict(list)
    for trade in trades:
        by_day_symbol[(trade.entry_time.date(), trade.symbol)].append(trade)

    for (market_day, symbol), symbol_trades in sorted(by_day_symbol.items()):
        symbol_trades.sort(key=lambda trade: trade.entry_time)
        target_hit_seen = False
        for trade in symbol_trades:
            if target_hit_seen:
                lines.append(
                    "same-symbol re-entry after target "
                    f"{market_day.isoformat()} {symbol} {trade.side} "
                    f"entry={trade.entry_time.strftime('%H:%M')} "
                    f"exit={trade.exit_time.strftime('%H:%M')} "
                    f"pnl={trade.pnl:.2f} exit={exit_bucket(trade.exit_reason)}"
                )
            if exit_bucket(trade.exit_reason) == "target":
                target_hit_seen = True
    return lines


def trailing_giveback_checks(trades: list[TradeRow]) -> list[str]:
    lines: list[str] = []
    for trade in trades:
        if exit_bucket(trade.exit_reason) != "trailing_stop":
            continue
        best_price = parse_best_price(trade.exit_reason)
        if best_price is None:
            lines.append(
                f"trailing stop {trade.symbol} {trade.side} "
                f"{trade.entry_time.date().isoformat()} pnl={trade.pnl:.2f}; best price unavailable"
            )
            continue
        potential = abs(best_price - trade.entry_price) * trade.quantity
        giveback = potential - trade.pnl
        captured_pct = 0.0 if potential <= 0 else (trade.pnl / potential) * 100
        lines.append(
            f"trailing giveback {trade.entry_time.date().isoformat()} {trade.symbol} {trade.side} "
            f"entry={trade.entry_price:.2f} best={best_price:.2f} exit={trade.exit_price:.2f} "
            f"potential={potential:.2f} captured={trade.pnl:.2f} "
            f"giveback={giveback:.2f} captured_pct={captured_pct:.1f}%"
        )
    return lines


def parse_best_price(reason: str) -> float | None:
    match = re.search(r"\bfrom best\s+([0-9]+(?:\.[0-9]+)?)", reason.lower())
    if not match:
        return None
    return float(match.group(1))


def estimate_cost(trade: TradeRow, config: CostConfig) -> CostEstimate:
    entry_value = trade.entry_price * trade.quantity
    exit_value = trade.exit_price * trade.quantity
    turnover = entry_value + exit_value
    sell_value = exit_value if trade.side == "BUY" else entry_value
    buy_value = entry_value if trade.side == "BUY" else exit_value

    brokerage = min(entry_value * config.brokerage_rate, config.brokerage_cap)
    brokerage += min(exit_value * config.brokerage_rate, config.brokerage_cap)
    stt = sell_value * config.stt_sell_rate
    exchange = turnover * config.exchange_rate
    sebi = turnover * config.sebi_rate
    stamp = buy_value * config.stamp_buy_rate
    gst = (brokerage + exchange + sebi) * config.gst_rate
    slippage = turnover * (config.slippage_bps / 10000)
    return CostEstimate(
        turnover=turnover,
        entry_value=entry_value,
        exit_value=exit_value,
        brokerage=brokerage,
        stt=stt,
        exchange=exchange,
        sebi=sebi,
        stamp=stamp,
        gst=gst,
        slippage=slippage,
    )


def estimate_peak_exposure(trades: list[TradeRow]) -> float:
    events: list[tuple[datetime, int, float]] = []
    for trade in trades:
        exposure = trade.entry_price * trade.quantity
        events.append((trade.entry_time, 1, exposure))
        events.append((trade.exit_time, -1, exposure))

    peak = 0.0
    current = 0.0
    for _, direction, exposure in sorted(events, key=lambda item: (item[0], -item[1])):
        current += direction * exposure
        peak = max(peak, current)
    return peak


def time_bucket(value: datetime) -> str:
    hour = value.hour
    minute = 30 if value.minute >= 30 else 0
    return f"{hour:02d}:{minute:02d}-{hour:02d}:{minute + 29:02d}"


def numeric_bucket(value: float | None, thresholds: list[float], suffix: str = "") -> str:
    if value is None:
        return "unknown"
    previous = None
    for threshold in thresholds:
        if value < threshold:
            if previous is None:
                return f"<{threshold:g}{suffix}"
            return f"{previous:g}{suffix}..{threshold:g}{suffix}"
        previous = threshold
    return f">={thresholds[-1]:g}{suffix}"


def exit_bucket(reason: str) -> str:
    lower = reason.lower()
    if "stop loss" in lower:
        return "stop_loss"
    if "trailing stop" in lower:
        return "trailing_stop"
    if "square off" in lower:
        return "square_off"
    if "target" in lower:
        return "target"
    return "other"


def _format_group_row(key: str, stats: GroupStats) -> str:
    return (
        f"- {key}: trades={stats.trades} wins={stats.wins} losses={stats.losses} "
        f"win_rate={stats.win_rate:.2f}% pnl={stats.pnl:.2f} avg={stats.avg_pnl:.2f}"
    )


def _line_maybe_in_range(line: str, from_date: date | None, to_date: date | None) -> bool:
    if from_date is None and to_date is None:
        return True
    # Cheap prefilter only. Final filtering uses parsed trade exit date.
    for day in _candidate_dates(from_date, to_date):
        if day in line:
            return True
    return False


def _candidate_dates(from_date: date | None, to_date: date | None) -> list[str]:
    if from_date is None or to_date is None:
        return []
    days = []
    current = from_date
    while current <= to_date:
        days.append(current.isoformat())
        current = date.fromordinal(current.toordinal() + 1)
    return days


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw).date()


def _parse_datetime(raw: str) -> datetime:
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


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
