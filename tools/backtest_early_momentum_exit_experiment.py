from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import trading_bot.backtest as backtest_module
from trading_bot.backtest import BacktestResult, BacktestTrade, run_backtest
from trading_bot.config import BotConfig, load_config
from trading_bot.models import Position, SignalSide, Tick
from trading_bot.risk import RiskManager
from trading_bot.token_store import load_runtime_credentials, make_kite_client


class BacktestTick:
    def __init__(
        self,
        symbol: str,
        price: float,
        timestamp: Any,
        open: float | None = None,
        high: float | None = None,
        low: float | None = None,
        close: float | None = None,
        volume: float | None = None,
        vwap: float | None = None,
        higher_timeframe_trend_pct: float | None = None,
    ) -> None:
        self.symbol = symbol
        self._price = price
        self.timestamp = timestamp
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.vwap = vwap
        self.higher_timeframe_trend_pct = higher_timeframe_trend_pct

    @property
    def price(self) -> float:
        CURRENT_TICK_BY_SYMBOL[self.symbol] = self
        return self._price


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
    max_candles: int | None = None
    min_favorable_pct: float = 0.0
    weak_close_threshold_pct: float | None = None
    require_adverse_price: bool = True


class EarlyExitRiskManager(RiskManager):
    max_candles: int | None = None
    min_favorable_pct: float = 0.0
    weak_close_threshold_pct: float | None = None
    require_adverse_price: bool = True
    candle_minutes: int = 5

    def exit_signal_for_risk(self, symbol: str, price: float) -> str | None:
        position = self.positions.get(symbol)
        if position is None:
            return None

        # Keep best-price tracking identical to production before testing the early exit.
        self._update_best_price(position, price)
        tick = CURRENT_TICK_BY_SYMBOL.get(symbol)
        early_reason = self._early_exit_reason(position, tick)
        if early_reason:
            return early_reason

        pnl_pct = position.pnl_pct(price)
        if pnl_pct <= -abs(self.stop_loss_pct):
            return f"stop loss hit at {pnl_pct:.2f}%"
        trailing_reason = self._trailing_stop_reason(position, price)
        if trailing_reason:
            return trailing_reason
        if pnl_pct >= abs(self.target_pct):
            return f"target hit at {pnl_pct:.2f}%"
        return None

    def _early_exit_reason(self, position: Position, tick: Tick | None) -> str | None:
        if self.max_candles is None or tick is None or tick.timestamp <= position.entry_time:
            return None
        elapsed_seconds = (tick.timestamp - position.entry_time).total_seconds()
        candle_index = int(elapsed_seconds // (self.candle_minutes * 60))
        if candle_index < 1 or candle_index > self.max_candles:
            return None

        pnl_pct = position.pnl_pct(tick.price)
        if pnl_pct >= self.min_favorable_pct:
            return None
        if self.require_adverse_price and pnl_pct >= 0:
            return None

        if self.weak_close_threshold_pct is not None:
            if tick.high is None or tick.low is None or tick.close is None:
                return None
            candle_range = tick.high - tick.low
            if candle_range <= 0:
                return None
            close_strength = ((tick.close - tick.low) / candle_range) * 100
            if position.side == SignalSide.BUY and close_strength >= self.weak_close_threshold_pct:
                return None
            if position.side == SignalSide.SELL and close_strength <= (100 - self.weak_close_threshold_pct):
                return None
            return (
                f"early failure exit after {candle_index} candle(s): "
                f"pnl {pnl_pct:.2f}%, close_strength {close_strength:.2f}%"
            )

        return f"early failure exit after {candle_index} candle(s): pnl {pnl_pct:.2f}%"


CURRENT_TICK_BY_SYMBOL: dict[str, Tick] = {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest early failed-follow-through exits")
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
        Variant("exit_1candle_adverse", max_candles=1),
        Variant("exit_2candles_adverse", max_candles=2),
        Variant("exit_1candle_no_0.10pct_followthrough", max_candles=1, min_favorable_pct=0.10, require_adverse_price=False),
        Variant("exit_2candles_no_0.10pct_followthrough", max_candles=2, min_favorable_pct=0.10, require_adverse_price=False),
        Variant("weak_close_1candle", max_candles=1, weak_close_threshold_pct=35.0),
        Variant("weak_close_2candles", max_candles=2, weak_close_threshold_pct=35.0),
    ]

    from_date = date.today() - timedelta(days=args.days)
    print(
        f"EARLY_MOMENTUM_EXIT_EXPERIMENT from={from_date} to={date.today()} "
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
    if variant.max_candles is None:
        return run_backtest(config, candles_by_symbol)

    original_risk_manager = backtest_module.RiskManager

    def risk_manager_factory(*args, **kwargs):
        manager = EarlyExitRiskManager(*args, **kwargs)
        manager.max_candles = variant.max_candles
        manager.min_favorable_pct = variant.min_favorable_pct
        manager.weak_close_threshold_pct = variant.weak_close_threshold_pct
        manager.require_adverse_price = variant.require_adverse_price
        return manager

    try:
        backtest_module.RiskManager = risk_manager_factory
        return run_backtest(config, candles_by_symbol)
    finally:
        backtest_module.RiskManager = original_risk_manager
        CURRENT_TICK_BY_SYMBOL.clear()


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


def print_exit_mix(result: BacktestResult) -> None:
    counts: dict[str, int] = {}
    for trade in result.trades:
        reason = trade.reason.split("| exit:")[-1].strip()
        key = reason.split(" at ", 1)[0].split(" after ", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    print("  exits=" + ", ".join(f"{key}:{value}" for key, value in sorted(counts.items())))


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
