from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import trading_bot.backtest as backtest_module
from trading_bot.backtest import BacktestResult, BacktestTrade, run_backtest
from trading_bot.config import BotConfig, RiskConfig, load_config
from trading_bot.models import Position, SignalSide, Tick
from trading_bot.risk import RiskManager
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
    trailing_pct: float | None = None
    profit_lock_activation_pct: float | None = None
    profit_lock_pct: float | None = None
    max_directional_vwap_distance_pct: float | None = None


class ProfitLockRiskManager(RiskManager):
    profit_lock_activation_pct: float | None = None
    profit_lock_pct: float | None = None

    def _trailing_stop_reason(self, position: Position, price: float) -> str | None:
        lock_reason = self._profit_lock_reason(position, price)
        if lock_reason:
            return lock_reason
        return super()._trailing_stop_reason(position, price)

    def _profit_lock_reason(self, position: Position, price: float) -> str | None:
        if self.profit_lock_activation_pct is None or self.profit_lock_pct is None:
            return None

        best_price = self.best_prices.get(position.symbol, position.entry_price)
        best_pnl_pct = position.pnl_pct(best_price)
        if best_pnl_pct < self.profit_lock_activation_pct:
            return None

        lock = abs(self.profit_lock_pct)
        if position.side == SignalSide.BUY:
            lock_price = position.entry_price * (1 + lock / 100)
            if price <= lock_price:
                return (
                    f"profit lock hit at {position.pnl_pct(price):.2f}% "
                    f"after best {best_price:.2f}"
                )
        elif position.side == SignalSide.SELL:
            lock_price = position.entry_price * (1 - lock / 100)
            if price >= lock_price:
                return (
                    f"profit lock hit at {position.pnl_pct(price):.2f}% "
                    f"after best {best_price:.2f}"
                )
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest exit and VWAP chase-control experiments")
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
        Variant("trail_0.20", trailing_pct=0.20),
        Variant("trail_0.25", trailing_pct=0.25),
        Variant("trail_0.30", trailing_pct=0.30),
        Variant("trail_0.35", trailing_pct=0.35),
        Variant("trail_0.50", trailing_pct=0.50),
        Variant("profit_lock_0.50_to_0.25", profit_lock_activation_pct=0.50, profit_lock_pct=0.25),
        Variant("profit_lock_0.40_to_0.20", profit_lock_activation_pct=0.40, profit_lock_pct=0.20),
        Variant("profit_lock_0.75_to_0.35", profit_lock_activation_pct=0.75, profit_lock_pct=0.35),
        Variant("profit_lock_0.75_to_0.50", profit_lock_activation_pct=0.75, profit_lock_pct=0.50),
        Variant("vwap_max_1.00", max_directional_vwap_distance_pct=1.00),
        Variant(
            "profit_lock_0.50_to_0.25_plus_vwap_max_1.00",
            profit_lock_activation_pct=0.50,
            profit_lock_pct=0.25,
            max_directional_vwap_distance_pct=1.00,
        ),
    ]

    from_date = date.today() - timedelta(days=args.days)
    print(
        f"EXIT_EXPERIMENT from={from_date} to={date.today()} "
        f"days={args.days} interval={args.interval} symbols={','.join(candles_by_symbol)}"
    )
    baseline: Score | None = None
    for variant in variants:
        result = run_variant(config, candles_by_symbol, variant)
        score = score_result(result, cost_config)
        if baseline is None:
            baseline = score
        print(format_score(variant.name, score, baseline))
        print_exit_mix(result)
        print_recent_trades(result)


def fetch_candles(config: BotConfig, days: int, interval: str) -> dict[str, list[dict]]:
    kite = make_kite_client(load_runtime_credentials())
    instruments = kite.instruments(config.market.exchange)
    token_by_symbol = {item["tradingsymbol"]: item["instrument_token"] for item in instruments}
    from_date = date.today() - timedelta(days=days)
    to_date = date.today()
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
    risk_config = config.risk
    if variant.trailing_pct is not None:
        risk_config = replace(risk_config, trailing_stop_loss_pct=variant.trailing_pct)
    variant_config = replace(config, risk=risk_config)

    original_risk_manager = backtest_module.RiskManager
    original_open_position = backtest_module._open_position

    def risk_manager_factory(*args, **kwargs):
        manager = ProfitLockRiskManager(*args, **kwargs)
        manager.profit_lock_activation_pct = variant.profit_lock_activation_pct
        manager.profit_lock_pct = variant.profit_lock_pct
        return manager

    def open_position_with_vwap_gate(*args, **kwargs):
        candidate = args[1] if len(args) > 1 else kwargs["candidate"]
        tick, side, _reason = candidate
        if should_skip_for_vwap(tick, side, variant.max_directional_vwap_distance_pct):
            return None
        return original_open_position(*args, **kwargs)

    try:
        if variant.profit_lock_activation_pct is not None:
            backtest_module.RiskManager = risk_manager_factory
        if variant.max_directional_vwap_distance_pct is not None:
            backtest_module._open_position = open_position_with_vwap_gate
        return run_backtest(variant_config, candles_by_symbol)
    finally:
        backtest_module.RiskManager = original_risk_manager
        backtest_module._open_position = original_open_position


def should_skip_for_vwap(tick: Tick, side: SignalSide, max_distance_pct: float | None) -> bool:
    if max_distance_pct is None or tick.vwap is None or tick.vwap <= 0:
        return False
    distance_pct = ((tick.price - tick.vwap) / tick.vwap) * 100
    if side == SignalSide.BUY:
        return distance_pct > max_distance_pct
    if side == SignalSide.SELL:
        return -distance_pct > max_distance_pct
    return False


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
    avg_net = net / len(result.trades) if result.trades else 0.0
    return Score(
        trades=len(result.trades),
        wins=result.wins,
        losses=result.losses,
        win_rate=result.win_rate,
        gross_pnl=gross,
        estimated_cost=costs,
        net_pnl=net,
        turnover=turnover,
        avg_net=avg_net,
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
    net_delta = score.net_pnl - baseline.net_pnl
    gross_delta = score.gross_pnl - baseline.gross_pnl
    return (
        f"{label}: trades={score.trades} wins={score.wins} losses={score.losses} "
        f"win_rate={score.win_rate:.2f}% gross={score.gross_pnl:.2f} "
        f"net={score.net_pnl:.2f} avg_net={score.avg_net:.2f} "
        f"cost={score.estimated_cost:.2f} turnover={score.turnover:.2f} "
        f"delta_gross={gross_delta:+.2f} delta_net={net_delta:+.2f}"
    )


def print_exit_mix(result: BacktestResult) -> None:
    buckets: dict[str, list[BacktestTrade]] = {}
    for trade in result.trades:
        bucket = exit_bucket(trade.reason)
        buckets.setdefault(bucket, []).append(trade)
    parts = []
    for bucket in sorted(buckets):
        trades = buckets[bucket]
        parts.append(f"{bucket}:{len(trades)} pnl={sum(trade.pnl for trade in trades):.2f}")
    print("  exits " + " | ".join(parts))


def print_recent_trades(result: BacktestResult) -> None:
    for trade in result.trades[-4:]:
        print(
            f"  {trade.symbol} {trade.side.value} "
            f"{trade.entry_time:%Y-%m-%d %H:%M} @{trade.entry_price:.2f} "
            f"-> {trade.exit_time:%H:%M} @{trade.exit_price:.2f} "
            f"pnl={trade.pnl:.2f} exit={exit_bucket(trade.reason)}"
        )


def exit_bucket(reason: str) -> str:
    lower = reason.lower()
    if "profit lock" in lower:
        return "profit_lock"
    if "target hit" in lower:
        return "target"
    if "trailing stop" in lower:
        return "trailing_stop"
    if "stop loss" in lower:
        return "stop_loss"
    if "square off" in lower:
        return "square_off"
    return "other"


if __name__ == "__main__":
    main()
