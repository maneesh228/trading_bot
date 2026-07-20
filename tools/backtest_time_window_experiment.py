from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

import trading_bot.backtest as backtest_module
from trading_bot.backtest import BacktestResult, BacktestTrade, run_backtest
from trading_bot.config import BotConfig, load_config
from trading_bot.models import SignalSide
from trading_bot.token_store import load_runtime_credentials, make_kite_client


@dataclass(frozen=True)
class BacktestTick:
    symbol: str
    price: float
    timestamp: Any
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    vwap: float | None = None
    higher_timeframe_trend_pct: float | None = None


backtest_module.Tick = BacktestTick


@dataclass(frozen=True)
class CostConfig:
    brokerage_rate: float = 0.0003
    brokerage_cap: float = 20.0
    stt_sell_rate: float = 0.00025
    exchange_rate: float = 0.0000345
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00003
    gst_rate: float = 0.18
    slippage_bps: float = 1.0


@dataclass(frozen=True)
class Variant:
    name: str
    start_time: time | None = None
    end_time: time | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest entry time-window filters")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    args = parser.parse_args()

    config = load_config(args.config)
    candles_by_symbol = fetch_candles(config, args.days, args.interval)
    cost_config = CostConfig(slippage_bps=args.slippage_bps)
    variants = [
        Variant("baseline"),
        Variant("start_09_45", start_time=time(9, 45)),
        Variant("start_10_00", start_time=time(10, 0)),
        Variant("start_10_15", start_time=time(10, 15)),
        Variant("start_10_30", start_time=time(10, 30)),
        Variant("window_10_00_to_14_00", start_time=time(10, 0), end_time=time(14, 0)),
        Variant("window_10_15_to_14_00", start_time=time(10, 15), end_time=time(14, 0)),
    ]

    from_date = date.today() - timedelta(days=args.days)
    print(
        f"TIME_WINDOW_EXPERIMENT from={from_date} to={date.today()} "
        f"days={args.days} interval={args.interval} symbols={','.join(candles_by_symbol)}"
    )
    baseline: Score | None = None
    for variant in variants:
        result = run_variant(config, candles_by_symbol, variant)
        score = score_result(result, cost_config)
        if baseline is None:
            baseline = score
        print(format_score(variant.name, score, baseline))
        print_recent_trades(result)


def fetch_candles(config: BotConfig, days: int, interval: str) -> dict[str, list[dict]]:
    kite = make_kite_client(load_runtime_credentials())
    instruments = kite.instruments(config.market.exchange)
    token_by_symbol = {item["tradingsymbol"]: item["instrument_token"] for item in instruments}
    from_date = date.today() - timedelta(days=days)
    to_date = date.today() + timedelta(days=1)
    candles_by_symbol = {}
    for item in config.watchlist:
        token = token_by_symbol.get(item.symbol)
        if token is None:
            print(f"skip {item.symbol}: missing instrument token")
            continue
        candles_by_symbol[item.symbol] = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            continuous=False,
            oi=False,
        )
    return candles_by_symbol


def run_variant(config: BotConfig, candles_by_symbol: dict[str, list[dict]], variant: Variant) -> BacktestResult:
    if variant.start_time is None and variant.end_time is None:
        return run_backtest(config, candles_by_symbol)

    original_open_position = backtest_module._open_position

    def open_position_with_time_gate(*args, **kwargs):
        candidate = args[1] if len(args) > 1 else kwargs["candidate"]
        tick, _side, _reason = candidate
        tick_time = tick.timestamp.time()
        if variant.start_time is not None and tick_time < variant.start_time:
            return None
        if variant.end_time is not None and tick_time > variant.end_time:
            return None
        return original_open_position(*args, **kwargs)

    try:
        backtest_module._open_position = open_position_with_time_gate
        return run_backtest(config, candles_by_symbol)
    finally:
        backtest_module._open_position = original_open_position


@dataclass(frozen=True)
class Score:
    trades: int
    wins: int
    losses: int
    win_rate: float
    gross_pnl: float
    estimated_cost: float
    net_pnl: float
    turnover: float
    avg_net: float


def score_result(result: BacktestResult, cost_config: CostConfig) -> Score:
    gross = result.total_pnl
    costs = sum(estimate_cost(trade, cost_config) for trade in result.trades)
    turnover = sum(turnover_for_trade(trade) for trade in result.trades)
    net = gross - costs
    return Score(
        trades=len(result.trades),
        wins=result.wins,
        losses=result.losses,
        win_rate=result.win_rate,
        gross_pnl=gross,
        estimated_cost=costs,
        net_pnl=net,
        turnover=turnover,
        avg_net=net / len(result.trades) if result.trades else 0.0,
    )


def turnover_for_trade(trade: BacktestTrade) -> float:
    return (trade.entry_price + trade.exit_price) * trade.quantity


def estimate_cost(trade: BacktestTrade, config: CostConfig) -> float:
    entry_value = trade.entry_price * trade.quantity
    exit_value = trade.exit_price * trade.quantity
    turnover = entry_value + exit_value
    sell_value = exit_value if trade.side == SignalSide.BUY else entry_value
    buy_value = entry_value if trade.side == SignalSide.BUY else exit_value
    brokerage = min(entry_value * config.brokerage_rate, config.brokerage_cap)
    brokerage += min(exit_value * config.brokerage_rate, config.brokerage_cap)
    stt = sell_value * config.stt_sell_rate
    exchange = turnover * config.exchange_rate
    sebi = turnover * config.sebi_rate
    stamp = buy_value * config.stamp_buy_rate
    gst = (brokerage + exchange + sebi) * config.gst_rate
    slippage = turnover * (config.slippage_bps / 10000)
    return brokerage + stt + exchange + sebi + stamp + gst + slippage


def format_score(name: str, score: Score, baseline: Score) -> str:
    return (
        f"{name}: trades={score.trades} wins={score.wins} losses={score.losses} "
        f"win_rate={score.win_rate:.2f}% gross={score.gross_pnl:.2f} "
        f"cost={score.estimated_cost:.2f} net={score.net_pnl:.2f} "
        f"avg_net={score.avg_net:.2f} delta_net={score.net_pnl - baseline.net_pnl:.2f}"
    )


def print_recent_trades(result: BacktestResult) -> None:
    recent = [
        trade
        for trade in result.trades
        if trade.entry_time.date() >= date.today() - timedelta(days=2)
    ]
    if not recent:
        return
    print("  recent:")
    for trade in recent:
        print(
            f"    {trade.entry_time:%Y-%m-%d %H:%M} {trade.symbol} {trade.side.value} "
            f"{trade.entry_price:.2f}->{trade.exit_price:.2f} pnl={trade.pnl:.2f} "
            f"pct={trade.pnl_pct:.2f}% reason={trade.reason.split('| exit:')[-1].strip()}"
        )


if __name__ == "__main__":
    main()
