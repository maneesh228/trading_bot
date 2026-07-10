from __future__ import annotations

import argparse
import copy
from dataclasses import replace
from datetime import date, timedelta
from itertools import product
from typing import Any

from trading_bot.backtest import BacktestResult, run_backtest
from trading_bot.config import BotConfig, WatchSymbol, load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--min-trades", type=int, default=25)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--preset", choices=["tiny", "quick", "full"], default="quick")
    args = parser.parse_args()

    config = load_config(args.config)
    candles_by_symbol = fetch_candles(config, args.days, args.interval)

    baseline = run_backtest(config, candles_by_symbol)
    print(f"SWEEP interval={args.interval} days_requested={args.days} trading_days={baseline.days}")
    print("BASELINE " + format_result(baseline, label="current"))
    print()

    results = []
    for variant_name, variant_config in variants(config, preset=args.preset):
        result = run_backtest(variant_config, candles_by_symbol)
        if len(result.trades) < args.min_trades:
            continue
        results.append((score(result), variant_name, result))

    results.sort(reverse=True)
    print(f"TOP candidates min_trades={args.min_trades} tested={len(results)}")
    for _, name, result in results[: args.top]:
        delta_wr = result.win_rate - baseline.win_rate
        delta_pnl = result.total_pnl - baseline.total_pnl
        print(
            format_result(result, label=name)
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


def variants(config: BotConfig, *, preset: str):
    if preset == "tiny":
        support_buffers = [0.05, 0.10]
        trend_thresholds = [0.15, 0.25]
        volume_multipliers = [1.3]
        vwap_distances = [0.10, 0.20, 0.30]
        body_thresholds = [10, 20]
    else:
        support_buffers = [0.05, 0.08, 0.10, 0.15]
        trend_thresholds = [0.15, 0.20, 0.25, 0.30]
        volume_multipliers = [1.2, 1.3, 1.4, 1.5]
        vwap_distances = [0.10, 0.15, 0.20, 0.25]
        body_thresholds = [10, 15, 20]
    stop_losses = [config.risk.per_trade_stop_loss_pct] if preset != "full" else [0.5, 0.6, 0.7]
    targets = [config.risk.per_trade_target_pct] if preset != "full" else [0.8, 1.0, 1.2]

    for values in product(
        support_buffers,
        trend_thresholds,
        volume_multipliers,
        vwap_distances,
        body_thresholds,
        stop_losses,
        targets,
    ):
        (
            support_buffer,
            trend_threshold,
            volume_multiplier,
            vwap_distance,
            body_threshold,
            stop_loss,
            target,
        ) = values
        name = (
            f"sr={support_buffer:.2f} trend={trend_threshold:.2f} "
            f"vol={volume_multiplier:.1f} vwap={vwap_distance:.2f} "
            f"body={body_threshold:g} sl={stop_loss:.1f} tgt={target:.1f}"
        )
        yield name, mutate_config(
            config,
            support_buffer=support_buffer,
            trend_threshold=trend_threshold,
            volume_multiplier=volume_multiplier,
            vwap_distance=vwap_distance,
            body_threshold=body_threshold,
            stop_loss=stop_loss,
            target=target,
        )


def mutate_config(
    config: BotConfig,
    *,
    support_buffer: float,
    trend_threshold: float,
    volume_multiplier: float,
    vwap_distance: float,
    body_threshold: float,
    stop_loss: float,
    target: float,
) -> BotConfig:
    watchlist = []
    for item in config.watchlist:
        strategy = copy.deepcopy(item.strategy)
        for child in strategy.params.get("strategies", []):
            if child.get("name") == "support_resistance_breakout":
                child["buffer_pct"] = support_buffer
            elif child.get("name") == "trend_regime":
                child["min_trend_pct"] = trend_threshold
            elif child.get("name") == "volume_spike":
                child["multiplier"] = volume_multiplier
            elif child.get("name") == "vwap_filter":
                child["min_distance_pct"] = vwap_distance
            elif child.get("name") == "candle_body_filter":
                child["min_body_pct"] = body_threshold
        watchlist.append(WatchSymbol(item.symbol, item.quantity, strategy))

    risk = replace(
        config.risk,
        per_trade_stop_loss_pct=stop_loss,
        per_trade_target_pct=target,
    )
    return replace(config, risk=risk, watchlist=watchlist)


def score(result: BacktestResult) -> tuple[float, float, int]:
    return (result.win_rate, result.total_pnl, len(result.trades))


def format_result(result: BacktestResult, *, label: str) -> str:
    return (
        f"{label}: trades={len(result.trades)} wins={result.wins} losses={result.losses} "
        f"win_rate={result.win_rate:.2f}% pnl={result.total_pnl:.2f}"
    )


if __name__ == "__main__":
    main()
