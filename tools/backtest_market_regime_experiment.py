from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
class IndexContext:
    close: float
    vwap: float | None
    day_move_pct: float
    trend_6_pct: float | None


@dataclass(frozen=True)
class Variant:
    name: str
    require_vwap_side: bool = False
    min_abs_day_move_pct: float = 0.0
    min_abs_trend_6_pct: float = 0.0
    block_flat_index: bool = False
    exception_side: SignalSide | None = None
    exception_min_stock_vwap_distance_pct: float | None = None
    exception_min_follow_through_pct: float | None = None
    exception_min_signal_strength_pct: float | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest NIFTY market regime agreement gates")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--index-symbol", default="NIFTY 50")
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    args = parser.parse_args()

    config = load_config(args.config)
    kite = make_kite_client(load_runtime_credentials())
    candles_by_symbol = fetch_symbol_candles(kite, config, args.days, args.interval)
    index_candles = fetch_index_candles(kite, args.index_symbol, args.days, args.interval)
    index_context = build_index_context(index_candles)
    cost_config = CostConfig(slippage_bps=args.slippage_bps)

    variants = [
        Variant("baseline"),
        Variant("index_vwap_agreement", require_vwap_side=True),
        Variant("index_vwap_plus_day_move_0.05", require_vwap_side=True, min_abs_day_move_pct=0.05),
        Variant("index_vwap_plus_day_move_0.10", require_vwap_side=True, min_abs_day_move_pct=0.10),
        Variant("index_vwap_plus_trend6_0.05", require_vwap_side=True, min_abs_trend_6_pct=0.05),
        Variant(
            "regime_plus_strong_sell_exception_vwap_1.0_follow_0.50",
            require_vwap_side=True,
            min_abs_trend_6_pct=0.05,
            exception_side=SignalSide.SELL,
            exception_min_stock_vwap_distance_pct=1.0,
            exception_min_follow_through_pct=0.50,
        ),
        Variant(
            "regime_plus_strong_sell_exception_vwap_1.5_follow_0.50",
            require_vwap_side=True,
            min_abs_trend_6_pct=0.05,
            exception_side=SignalSide.SELL,
            exception_min_stock_vwap_distance_pct=1.5,
            exception_min_follow_through_pct=0.50,
        ),
        Variant(
            "regime_plus_strong_any_exception_vwap_1.0_follow_0.50",
            require_vwap_side=True,
            min_abs_trend_6_pct=0.05,
            exception_min_stock_vwap_distance_pct=1.0,
            exception_min_follow_through_pct=0.50,
        ),
        Variant(
            "regime_plus_sell_exception_strength_0.50_vwap_1.0",
            require_vwap_side=True,
            min_abs_trend_6_pct=0.05,
            exception_side=SignalSide.SELL,
            exception_min_stock_vwap_distance_pct=1.0,
            exception_min_signal_strength_pct=0.50,
        ),
        Variant("block_flat_index_0.05", block_flat_index=True, min_abs_day_move_pct=0.05),
        Variant("block_flat_index_0.10", block_flat_index=True, min_abs_day_move_pct=0.10),
    ]

    from_date = date.today() - timedelta(days=args.days)
    print(
        f"MARKET_REGIME_EXPERIMENT from={from_date} to={date.today()} "
        f"days={args.days} interval={args.interval} index={args.index_symbol} "
        f"index_rows={len(index_context)} symbols={','.join(candles_by_symbol)}"
    )

    baseline: Score | None = None
    for variant in variants:
        result = run_variant(config, candles_by_symbol, index_context, variant)
        score = score_result(result, cost_config)
        if baseline is None:
            baseline = score
        print(format_score(variant.name, score, baseline))
        print_recent_trades(result)


def fetch_symbol_candles(kite: Any, config: BotConfig, days: int, interval: str) -> dict[str, list[dict]]:
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


def fetch_index_candles(kite: Any, index_symbol: str, days: int, interval: str) -> list[dict]:
    index_symbol = index_symbol.upper()
    instruments = kite.instruments("NSE")
    matches = [
        item
        for item in instruments
        if str(item.get("tradingsymbol", "")).upper() == index_symbol
        or str(item.get("name", "")).upper() == index_symbol
    ]
    if not matches:
        sample = [
            item.get("tradingsymbol")
            for item in instruments
            if "NIFTY" in str(item.get("tradingsymbol", "")).upper()
        ][:20]
        raise RuntimeError(f"Could not find index {index_symbol}; sample NIFTY symbols={sample}")
    token = matches[0]["instrument_token"]
    return kite.historical_data(
        instrument_token=token,
        from_date=date.today() - timedelta(days=days),
        to_date=date.today() + timedelta(days=1),
        interval=interval,
        continuous=False,
        oi=False,
    )


def build_index_context(candles: list[dict]) -> dict[datetime, IndexContext]:
    context: dict[datetime, IndexContext] = {}
    by_day: dict[date, list[dict]] = {}
    for candle in candles:
        by_day.setdefault(candle_time(candle).date(), []).append(candle)

    for rows in by_day.values():
        rows.sort(key=candle_time)
        weighted_value = 0.0
        volume_total = 0.0
        typical_sum = 0.0
        day_open = float(rows[0]["open"])
        for index, candle in enumerate(rows):
            close = float(candle["close"])
            typical = (float(candle["high"]) + float(candle["low"]) + close) / 3
            typical_sum += typical
            volume = float(candle.get("volume", 0) or 0)
            if volume > 0:
                weighted_value += typical * volume
                volume_total += volume
            vwap = weighted_value / volume_total if volume_total > 0 else typical_sum / (index + 1)
            trend_6 = None
            if index >= 6:
                previous = float(rows[index - 6]["close"])
                if previous > 0:
                    trend_6 = ((close - previous) / previous) * 100
            context[candle_time(candle)] = IndexContext(
                close=close,
                vwap=vwap,
                day_move_pct=((close - day_open) / day_open) * 100 if day_open > 0 else 0.0,
                trend_6_pct=trend_6,
            )
    return context


def run_variant(
    config: BotConfig,
    candles_by_symbol: dict[str, list[dict]],
    index_context: dict[datetime, IndexContext],
    variant: Variant,
) -> BacktestResult:
    if variant.name == "baseline":
        return run_backtest(config, candles_by_symbol)

    original_open_position = backtest_module._open_position

    def open_position_with_market_gate(*args, **kwargs):
        candidate = args[1] if len(args) > 1 else kwargs["candidate"]
        tick, side, reason = candidate
        context = index_context.get(tick.timestamp)
        blocked = context is None or should_block(side, context, variant)
        if blocked and not strong_stock_exception(tick, side, reason, variant):
            return None
        return original_open_position(*args, **kwargs)

    try:
        backtest_module._open_position = open_position_with_market_gate
        return run_backtest(config, candles_by_symbol)
    finally:
        backtest_module._open_position = original_open_position


def should_block(side: SignalSide, context: IndexContext, variant: Variant) -> bool:
    signed_day_move = context.day_move_pct if side == SignalSide.BUY else -context.day_move_pct
    signed_trend = None
    if context.trend_6_pct is not None:
        signed_trend = context.trend_6_pct if side == SignalSide.BUY else -context.trend_6_pct

    if variant.block_flat_index and abs(context.day_move_pct) < variant.min_abs_day_move_pct:
        return True
    if variant.require_vwap_side:
        if context.vwap is None:
            return True
        if side == SignalSide.BUY and context.close < context.vwap:
            return True
        if side == SignalSide.SELL and context.close > context.vwap:
            return True
    if variant.min_abs_day_move_pct > 0 and signed_day_move < variant.min_abs_day_move_pct:
        return True
    if variant.min_abs_trend_6_pct > 0:
        if signed_trend is None or signed_trend < variant.min_abs_trend_6_pct:
            return True
    return False


def strong_stock_exception(tick: BacktestTick, side: SignalSide, reason: str, variant: Variant) -> bool:
    if variant.exception_side is not None and side != variant.exception_side:
        return False

    if variant.exception_min_stock_vwap_distance_pct is not None:
        distance = directional_vwap_distance_pct(tick, side)
        if distance is None or distance < variant.exception_min_stock_vwap_distance_pct:
            return False

    if variant.exception_min_follow_through_pct is not None:
        follow_through = parse_follow_through_pct(reason)
        if follow_through is None or follow_through < variant.exception_min_follow_through_pct:
            return False

    if variant.exception_min_signal_strength_pct is not None:
        strength = directional_candle_strength_pct(tick, side)
        if strength is None or strength < variant.exception_min_signal_strength_pct:
            return False

    return any(
        value is not None
        for value in [
            variant.exception_min_stock_vwap_distance_pct,
            variant.exception_min_follow_through_pct,
            variant.exception_min_signal_strength_pct,
        ]
    )


def directional_vwap_distance_pct(tick: BacktestTick, side: SignalSide) -> float | None:
    if tick.vwap is None or tick.vwap <= 0:
        return None
    raw = ((tick.price - tick.vwap) / tick.vwap) * 100
    if side == SignalSide.BUY:
        return raw
    if side == SignalSide.SELL:
        return -raw
    return None


def directional_candle_strength_pct(tick: BacktestTick, side: SignalSide) -> float | None:
    if tick.open is None or tick.open <= 0:
        return None
    if side == SignalSide.BUY:
        return ((tick.price - tick.open) / tick.open) * 100
    if side == SignalSide.SELL:
        return ((tick.open - tick.price) / tick.open) * 100
    return None


def parse_follow_through_pct(reason: str) -> float | None:
    match = re.search(r"follow-through\s+([0-9]+(?:\.[0-9]+)?)%", reason)
    if not match:
        return None
    return float(match.group(1))


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


def candle_time(candle: dict[str, Any]) -> datetime:
    value = candle["date"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


if __name__ == "__main__":
    main()
