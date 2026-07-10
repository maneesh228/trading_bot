from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, timedelta
from itertools import combinations
from typing import Any

from trading_bot.backtest import BacktestResult, run_backtest
from trading_bot.config import BotConfig, load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--max-remove", type=int, default=2)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    config = load_config(args.config)
    candles_by_symbol = fetch_candles(config, args.days, args.interval)
    baseline = run_backtest(config, candles_by_symbol)
    print(f"PRUNE interval={args.interval} days_requested={args.days} trading_days={baseline.days}")
    print("BASELINE " + format_result(baseline, "all symbols"))

    candidates = []
    symbols = [item.symbol for item in config.watchlist]
    for remove_count in range(1, args.max_remove + 1):
        for removed in combinations(symbols, remove_count):
            kept = [item for item in config.watchlist if item.symbol not in removed]
            pruned_config = replace(config, watchlist=kept)
            pruned_candles = {symbol: candles_by_symbol[symbol] for symbol in symbols if symbol not in removed}
            result = run_backtest(pruned_config, pruned_candles)
            candidates.append((score(result), removed, result))

    candidates.sort(reverse=True)
    for _, removed, result in candidates[: args.top]:
        delta_wr = result.win_rate - baseline.win_rate
        delta_pnl = result.total_pnl - baseline.total_pnl
        print(
            format_result(result, f"remove={','.join(removed)}")
            + f" delta_win_rate={delta_wr:+.2f}pp delta_pnl={delta_pnl:+.2f}"
        )


def fetch_candles(config: BotConfig, days: int, interval: str) -> dict[str, list[dict[str, Any]]]:
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments(config.market.exchange)
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


def score(result: BacktestResult) -> tuple[float, float, int]:
    return (result.win_rate, result.total_pnl, len(result.trades))


def format_result(result: BacktestResult, label: str) -> str:
    return (
        f"{label}: trades={len(result.trades)} wins={result.wins} losses={result.losses} "
        f"win_rate={result.win_rate:.2f}% pnl={result.total_pnl:.2f}"
    )


if __name__ == "__main__":
    main()
