from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, timedelta
from typing import Iterable

from trading_bot.backtest import BacktestResult, BacktestTrade, run_backtest
from trading_bot.config import BotConfig, load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--interval", default="5minute")
    args = parser.parse_args()

    config = load_config(args.config)
    candles_by_symbol = fetch_candles(config, args.days, args.interval)

    baseline_config = set_retry_enabled(config, False)
    strict_retry_config = set_retry_enabled(config, True)

    baseline = run_backtest(baseline_config, candles_by_symbol)
    strict_retry = run_backtest(strict_retry_config, candles_by_symbol)

    print(f"Backtest interval={args.interval} days_requested={args.days} trading_days={baseline.days}")
    print("Baseline:")
    print_summary(baseline)
    print("Strict retry after symbol loss:")
    print_summary(strict_retry)
    print(
        "Delta: "
        f"trades={len(strict_retry.trades) - len(baseline.trades)} "
        f"wins={strict_retry.wins - baseline.wins} "
        f"losses={strict_retry.losses - baseline.losses} "
        f"pnl={strict_retry.total_pnl - baseline.total_pnl:.2f} "
        f"win_rate={strict_retry.win_rate - baseline.win_rate:.2f}pp"
    )
    print()
    print("Per symbol:")
    symbols = sorted({trade.symbol for trade in baseline.trades + strict_retry.trades})
    for symbol in symbols:
        base_trades = [trade for trade in baseline.trades if trade.symbol == symbol]
        retry_trades = [trade for trade in strict_retry.trades if trade.symbol == symbol]
        print_symbol(symbol, base_trades, retry_trades)


def fetch_candles(config: BotConfig, days: int, interval: str) -> dict[str, list[dict]]:
    kite = make_kite_client(load_runtime_credentials())
    instruments = kite.instruments(config.market.exchange)
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in instruments
    }
    to_date = date.today()
    from_date = to_date - timedelta(days=days)
    candles_by_symbol = {}
    for item in config.watchlist:
        token = token_by_symbol.get(item.symbol)
        if token is None:
            raise RuntimeError(f"Could not find instrument token for {item.symbol}")
        candles_by_symbol[item.symbol] = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            continuous=False,
            oi=False,
        )
    return candles_by_symbol


def set_retry_enabled(config: BotConfig, enabled: bool) -> BotConfig:
    return replace(
        config,
        execution=replace(
            config.execution,
            retry_after_loss=replace(config.execution.retry_after_loss, enabled=enabled),
        ),
    )


def print_summary(result: BacktestResult) -> None:
    print(
        f"  trades={len(result.trades)} wins={result.wins} losses={result.losses} "
        f"win_rate={result.win_rate:.2f}% pnl={result.total_pnl:.2f}"
    )


def print_symbol(symbol: str, baseline: list[BacktestTrade], strict_retry: list[BacktestTrade]) -> None:
    base = stats(baseline)
    retry = stats(strict_retry)
    print(
        f"- {symbol}: "
        f"baseline trades={base['trades']} win_rate={base['win_rate']:.2f}% pnl={base['pnl']:.2f}; "
        f"strict trades={retry['trades']} win_rate={retry['win_rate']:.2f}% pnl={retry['pnl']:.2f}; "
        f"delta_trades={retry['trades'] - base['trades']} delta_pnl={retry['pnl'] - base['pnl']:.2f}"
    )


def stats(trades: Iterable[BacktestTrade]) -> dict[str, float]:
    items = list(trades)
    wins = sum(1 for trade in items if trade.pnl > 0)
    pnl = sum(trade.pnl for trade in items)
    return {
        "trades": len(items),
        "wins": wins,
        "losses": sum(1 for trade in items if trade.pnl < 0),
        "win_rate": (wins / len(items) * 100) if items else 0.0,
        "pnl": pnl,
    }


if __name__ == "__main__":
    main()
