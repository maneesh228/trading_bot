from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, timedelta

from trading_bot.backtest import run_backtest
from trading_bot.config import StrategyConfig, WatchSymbol, load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


VARIANTS = {
    "OHL + SMA": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "open_high_low", "tolerance": 0},
            {"name": "sma_trend_filter", "fast_window": 5, "slow_window": 20},
        ],
    },
    "OHL + SMA + VWAP strict": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "open_high_low", "tolerance": 0},
            {"name": "sma_trend_filter", "fast_window": 5, "slow_window": 20},
            {"name": "time_after", "after": "09:45"},
            {"name": "min_volume", "min_volume": 50000},
            {"name": "candle_body_filter", "min_body_pct": 10, "reject_flat": True},
            {"name": "vwap_filter", "min_distance_pct": 0.02},
        ],
    },
    "ORB + VWAP": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "opening_range_breakout", "opening_minutes": 15},
            {"name": "time_after", "after": "09:45"},
            {"name": "min_volume", "min_volume": 50000},
            {"name": "candle_body_filter", "min_body_pct": 10, "reject_flat": True},
            {"name": "vwap_filter", "min_distance_pct": 0.02},
        ],
    },
    "EMA crossover + RSI": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "ema_crossover", "fast_window": 5, "slow_window": 20},
            {"name": "rsi_filter", "period": 14, "buy_min": 50, "buy_max": 75, "sell_min": 25, "sell_max": 50},
            {"name": "time_after", "after": "09:45"},
            {"name": "min_volume", "min_volume": 50000},
            {"name": "candle_body_filter", "min_body_pct": 10, "reject_flat": True},
            {"name": "vwap_filter", "min_distance_pct": 0.02},
        ],
    },
    "MACD + RSI + VWAP": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "macd", "fast_window": 12, "slow_window": 26, "signal_window": 9, "mode": "trend"},
            {"name": "rsi_filter", "period": 14, "buy_min": 50, "buy_max": 75, "sell_min": 25, "sell_max": 50},
            {"name": "time_after", "after": "09:45"},
            {"name": "volume_spike", "lookback": 20, "multiplier": 1.3, "min_volume": 50000},
            {"name": "candle_body_filter", "min_body_pct": 10, "reject_flat": True},
            {"name": "vwap_filter", "min_distance_pct": 0.02},
        ],
    },
    "Support/Resistance + MACD + Volume": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "support_resistance_breakout", "lookback": 20, "buffer_pct": 0.03},
            {"name": "macd", "fast_window": 12, "slow_window": 26, "signal_window": 9, "mode": "trend"},
            {"name": "time_after", "after": "09:45"},
            {"name": "volume_spike", "lookback": 20, "multiplier": 1.3, "min_volume": 50000},
            {"name": "candle_body_filter", "min_body_pct": 10, "reject_flat": True},
            {"name": "vwap_filter", "min_distance_pct": 0.02},
        ],
    },
    "Support/Resistance + MACD + Volume + RSI": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "support_resistance_breakout", "lookback": 20, "buffer_pct": 0.03},
            {"name": "macd", "fast_window": 12, "slow_window": 26, "signal_window": 9, "mode": "trend"},
            {"name": "rsi_filter", "period": 14, "buy_min": 50, "buy_max": 75, "sell_min": 25, "sell_max": 50},
            {"name": "time_after", "after": "09:45"},
            {"name": "volume_spike", "lookback": 20, "multiplier": 1.3, "min_volume": 50000},
            {"name": "candle_body_filter", "min_body_pct": 10, "reject_flat": True},
            {"name": "vwap_filter", "min_distance_pct": 0.02},
        ],
    },
    "Support/Resistance + MACD + EMA + RSI + Volume": {
        "name": "composite",
        "mode": "all",
        "strategies": [
            {"name": "support_resistance_breakout", "lookback": 20, "buffer_pct": 0.03},
            {"name": "macd", "fast_window": 12, "slow_window": 26, "signal_window": 9, "mode": "trend"},
            {"name": "ema_crossover", "fast_window": 5, "slow_window": 20},
            {"name": "rsi_filter", "period": 14, "buy_min": 50, "buy_max": 75, "sell_min": 25, "sell_max": 50},
            {"name": "time_after", "after": "09:45"},
            {"name": "volume_spike", "lookback": 20, "multiplier": 1.3, "min_volume": 50000},
            {"name": "candle_body_filter", "min_body_pct": 10, "reject_flat": True},
            {"name": "vwap_filter", "min_distance_pct": 0.02},
        ],
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="compare-backtests")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    config = load_config(args.config)
    kite = make_kite_client(load_runtime_credentials())
    instruments = kite.instruments(config.market.exchange)
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in instruments
    }

    symbols = [item.symbol for item in config.watchlist]
    from_date = date.today() - timedelta(days=args.days)
    to_date = date.today()
    candles_by_symbol = {
        symbol: kite.historical_data(
            instrument_token=token_by_symbol[symbol],
            from_date=from_date,
            to_date=to_date,
            interval="5minute",
            continuous=False,
            oi=False,
        )
        for symbol in symbols
    }

    print(f"Backtest comparison symbols={','.join(symbols)} from={from_date} to={to_date}")
    for name, strategy in VARIANTS.items():
        watchlist = [
            WatchSymbol(
                symbol=symbol,
                quantity=1,
                strategy=StrategyConfig(
                    name=strategy["name"],
                    params={key: value for key, value in strategy.items() if key != "name"},
                ),
            )
            for symbol in symbols
        ]
        result = run_backtest(replace(config, watchlist=watchlist), candles_by_symbol)
        print(
            f"{name}: days={result.days} trades={len(result.trades)} "
            f"wins={result.wins} losses={result.losses} "
            f"win_rate={result.win_rate:.2f}% pnl={result.total_pnl:.2f}"
        )
        for trade in result.trades[-5:]:
            print(
                f"  {trade.symbol} {trade.side.value} "
                f"{trade.entry_time:%Y-%m-%d %H:%M} @{trade.entry_price:.2f} "
                f"-> {trade.exit_time:%H:%M} @{trade.exit_price:.2f} "
                f"qty={trade.quantity} pnl={trade.pnl:.2f}"
            )


if __name__ == "__main__":
    main()
