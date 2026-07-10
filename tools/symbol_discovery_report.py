from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import trading_bot.backtest as backtest_module
from trading_bot.backtest import BacktestResult, BacktestTrade, run_backtest
from trading_bot.config import BotConfig, SymbolQualityConfig, WatchSymbol, load_config
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


DEFAULT_CANDIDATES = [
    "ASHOKLEY",
    "MOTHERSON",
    "CANBK",
    "INFY",
    "AARTIIND",
    "TATASTEEL",
    "HINDALCO",
    "JSWSTEEL",
    "VEDL",
    "NATIONALUM",
    "SAIL",
    "NMDC",
    "TATAMOTORS",
    "TATAPOWER",
    "BEL",
    "BHEL",
    "HAL",
    "SBIN",
    "PNB",
    "AXISBANK",
    "ICICIBANK",
    "HDFCBANK",
    "RELIANCE",
    "ONGC",
    "COALINDIA",
    "NTPC",
    "POWERGRID",
    "IRFC",
    "RVNL",
    "ADANIENT",
    "BAJFINANCE",
]


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
class SymbolScore:
    symbol: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    gross_pnl: float
    estimated_cost: float
    net_pnl: float
    turnover: float
    avg_net: float
    days: int


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline symbol discovery backtest with estimated net costs")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--symbols", help="comma-separated NSE symbols; defaults to a liquid/volatile basket")
    parser.add_argument("--min-trades", type=int, default=2)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    args = parser.parse_args()

    config = load_config(args.config)
    symbols = parse_symbols(args.symbols) or DEFAULT_CANDIDATES
    cost_config = CostConfig(slippage_bps=args.slippage_bps)

    candles_by_symbol = fetch_candles(config, symbols, args.days, args.interval)
    scores: list[SymbolScore] = []
    for symbol, candles in candles_by_symbol.items():
        symbol_config = config_for_symbol(config, symbol)
        result = run_backtest(symbol_config, {symbol: candles})
        if len(result.trades) < args.min_trades:
            continue
        scores.append(score_symbol(symbol, result, cost_config))

    scores.sort(key=lambda row: (row.net_pnl, row.win_rate, row.trades), reverse=True)
    print(
        f"SYMBOL_DISCOVERY interval={args.interval} days_requested={args.days} "
        f"tested={len(candles_by_symbol)} qualifying={len(scores)} "
        f"min_trades={args.min_trades} slippage_bps_per_side={args.slippage_bps:.2f}"
    )
    print("TOP_NET_SYMBOLS")
    for row in scores[: args.top]:
        print(format_score(row))

    print()
    print("BOTTOM_NET_SYMBOLS")
    for row in sorted(scores, key=lambda row: (row.net_pnl, row.win_rate, row.trades))[: args.top]:
        print(format_score(row))


def parse_symbols(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


def fetch_candles(
    config: BotConfig,
    symbols: list[str],
    days: int,
    interval: str,
) -> dict[str, list[dict[str, Any]]]:
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments(config.market.exchange)
    }
    to_date = date.today()
    from_date = to_date - timedelta(days=days)
    candles_by_symbol = {}
    for symbol in symbols:
        token = token_by_symbol.get(symbol)
        if token is None:
            print(f"SKIP missing_instrument symbol={symbol}")
            continue
        candles_by_symbol[symbol] = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            continuous=False,
            oi=False,
        )
    return candles_by_symbol


def config_for_symbol(config: BotConfig, symbol: str) -> BotConfig:
    template = config.watchlist[0]
    watchlist = [WatchSymbol(symbol=symbol, quantity=template.quantity, strategy=template.strategy)]
    execution = replace(
        config.execution,
        symbol_quality=SymbolQualityConfig(enabled=False, blocked_symbols=[], allowed_symbols=[]),
    )
    return replace(config, execution=execution, watchlist=watchlist)


def score_symbol(symbol: str, result: BacktestResult, cost_config: CostConfig) -> SymbolScore:
    gross = result.total_pnl
    turnover = sum(turnover_for_trade(trade) for trade in result.trades)
    estimated_cost = sum(estimate_cost(trade, cost_config) for trade in result.trades)
    net = gross - estimated_cost
    return SymbolScore(
        symbol=symbol,
        trades=len(result.trades),
        wins=result.wins,
        losses=result.losses,
        win_rate=result.win_rate,
        gross_pnl=gross,
        estimated_cost=estimated_cost,
        net_pnl=net,
        turnover=turnover,
        avg_net=net / len(result.trades),
        days=result.days,
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


def format_score(row: SymbolScore) -> str:
    return (
        f"{row.symbol}: trades={row.trades} wins={row.wins} losses={row.losses} "
        f"win_rate={row.win_rate:.2f}% gross={row.gross_pnl:.2f} "
        f"cost={row.estimated_cost:.2f} net={row.net_pnl:.2f} "
        f"avg_net={row.avg_net:.2f} turnover={row.turnover:.2f}"
    )


if __name__ == "__main__":
    main()
