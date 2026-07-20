from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, timedelta
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest daily loss guard settings")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    args = parser.parse_args()

    config = load_config(args.config)
    candles_by_symbol = fetch_candles(config, args.days, args.interval)
    cost_config = CostConfig(slippage_bps=args.slippage_bps)
    variants = [None, 1, 2, 3]
    baseline: Score | None = None
    print(f"DAILY_LOSS_GUARD_EXPERIMENT days={args.days} interval={args.interval}")
    for max_losses in variants:
        variant_config = config
        name = "baseline_config" if max_losses is None else f"max_daily_losses_{max_losses}"
        if max_losses is not None:
            variant_config = replace(config, risk=replace(config.risk, max_daily_losses=max_losses))
        result = run_backtest(variant_config, candles_by_symbol)
        score = score_result(result, cost_config)
        if baseline is None:
            baseline = score
        print(format_score(name, score, baseline))


def fetch_candles(config: BotConfig, days: int, interval: str) -> dict[str, list[dict]]:
    kite = make_kite_client(load_runtime_credentials())
    instruments = kite.instruments(config.market.exchange)
    token_by_symbol = {item["tradingsymbol"]: item["instrument_token"] for item in instruments}
    from_date = date.today() - timedelta(days=days)
    to_date = date.today() + timedelta(days=1)
    candles_by_symbol = {}
    for item in config.watchlist:
        token = token_by_symbol.get(item.symbol)
        if token is not None:
            candles_by_symbol[item.symbol] = kite.historical_data(token, from_date, to_date, args.interval if False else interval)
    return candles_by_symbol


@dataclass(frozen=True)
class Score:
    trades: int
    wins: int
    losses: int
    win_rate: float
    gross_pnl: float
    estimated_cost: float
    net_pnl: float
    avg_net: float


def score_result(result: BacktestResult, cost_config: CostConfig) -> Score:
    gross = result.total_pnl
    costs = sum(estimate_cost(trade, cost_config) for trade in result.trades)
    net = gross - costs
    return Score(
        trades=len(result.trades),
        wins=result.wins,
        losses=result.losses,
        win_rate=result.win_rate,
        gross_pnl=gross,
        estimated_cost=costs,
        net_pnl=net,
        avg_net=net / len(result.trades) if result.trades else 0.0,
    )


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


if __name__ == "__main__":
    main()
