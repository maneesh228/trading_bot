from __future__ import annotations

import argparse
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Variant:
    name: str
    block_price_action_confirmation: bool = False
    block_price_action_when_volume_hold: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest stricter confirmation behavior")
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
        Variant("strict_full_signal_confirmation", block_price_action_confirmation=True),
        Variant("price_action_requires_volume_pass", block_price_action_when_volume_hold=True),
    ]

    from_date = date.today() - timedelta(days=args.days)
    print(
        f"CONFIRMATION_EXPERIMENT from={from_date} to={date.today()} "
        f"days={args.days} interval={args.interval} symbols={','.join(candles_by_symbol)}"
    )

    baseline: Score | None = None
    for variant in variants:
        result = run_variant(config, candles_by_symbol, variant)
        score = score_result(result, cost_config)
        if baseline is None:
            baseline = score
        print(format_score(variant.name, score, baseline))
        print_reason_mix(result)
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


def run_variant(
    config: BotConfig,
    candles_by_symbol: dict[str, list[dict]],
    variant: Variant,
) -> BacktestResult:
    if not variant.block_price_action_confirmation and not variant.block_price_action_when_volume_hold:
        return run_backtest(config, candles_by_symbol)

    original_open_position = backtest_module._open_position

    def open_position_with_confirmation_gate(*args, **kwargs):
        candidate = args[1] if len(args) > 1 else kwargs["candidate"]
        _tick, _side, reason = candidate
        lower = reason.lower()
        price_action = "confirmed by next candle price action" in lower
        volume_hold = "hold: volume" in lower and "below spike threshold" in lower
        if variant.block_price_action_confirmation and price_action:
            return None
        if variant.block_price_action_when_volume_hold and price_action and volume_hold:
            return None
        return original_open_position(*args, **kwargs)

    try:
        backtest_module._open_position = open_position_with_confirmation_gate
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


def format_score(label: str, score: Score, baseline: Score) -> str:
    return (
        f"{label}: trades={score.trades} wins={score.wins} losses={score.losses} "
        f"win_rate={score.win_rate:.2f}% gross={score.gross_pnl:.2f} "
        f"net={score.net_pnl:.2f} avg_net={score.avg_net:.2f} "
        f"cost={score.estimated_cost:.2f} turnover={score.turnover:.2f} "
        f"delta_gross={score.gross_pnl - baseline.gross_pnl:+.2f} "
        f"delta_net={score.net_pnl - baseline.net_pnl:+.2f}"
    )


def print_reason_mix(result: BacktestResult) -> None:
    price_action = [trade for trade in result.trades if "confirmed by next candle price action" in trade.reason.lower()]
    full_signal = [trade for trade in result.trades if "confirmed by next candle:" in trade.reason.lower()]
    volume_hold = [
        trade
        for trade in price_action
        if "hold: volume" in trade.reason.lower() and "below spike threshold" in trade.reason.lower()
    ]
    print(
        "  confirmation_mix "
        f"price_action={len(price_action)} pnl={sum(trade.pnl for trade in price_action):.2f} | "
        f"full_signal={len(full_signal)} pnl={sum(trade.pnl for trade in full_signal):.2f} | "
        f"price_action_volume_hold={len(volume_hold)} pnl={sum(trade.pnl for trade in volume_hold):.2f}"
    )


def print_recent_trades(result: BacktestResult) -> None:
    for trade in result.trades[-5:]:
        print(
            f"  {trade.symbol} {trade.side.value} "
            f"{trade.entry_time:%Y-%m-%d %H:%M} @{trade.entry_price:.2f} "
            f"-> {trade.exit_time:%H:%M} @{trade.exit_price:.2f} "
            f"pnl={trade.pnl:.2f}"
        )


if __name__ == "__main__":
    main()
