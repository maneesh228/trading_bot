from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from trading_bot.backtest import run_backtest
from trading_bot.config import load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    config = load_config("config.yaml")
    kite = make_kite_client(load_runtime_credentials())
    instruments = kite.instruments(config.market.exchange)
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in instruments
    }

    to_date = date.today()
    from_date = to_date - timedelta(days=60)
    candles_by_symbol = {}
    for item in config.watchlist:
        candles_by_symbol[item.symbol] = kite.historical_data(
            instrument_token=token_by_symbol[item.symbol],
            from_date=from_date,
            to_date=to_date,
            interval="5minute",
            continuous=False,
            oi=False,
        )

    result = run_backtest(config, candles_by_symbol)
    print(
        f"SUMMARY from={from_date} to={to_date} trading_days={result.days} "
        f"trades={len(result.trades)} wins={result.wins} losses={result.losses} "
        f"win_rate={result.win_rate:.2f}% pnl={result.total_pnl:.2f}"
    )

    by_symbol = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "buy": 0, "sell": 0})
    for trade in result.trades:
        summary = by_symbol[trade.symbol]
        summary["trades"] += 1
        summary["wins"] += int(trade.pnl > 0)
        summary["losses"] += int(trade.pnl < 0)
        summary["pnl"] += trade.pnl
        summary["buy"] += int(trade.side.value == "BUY")
        summary["sell"] += int(trade.side.value == "SELL")

    for symbol in sorted(by_symbol):
        summary = by_symbol[symbol]
        win_rate = (summary["wins"] / summary["trades"]) * 100 if summary["trades"] else 0.0
        print(
            f"{symbol}: trades={summary['trades']} wins={summary['wins']} "
            f"losses={summary['losses']} win_rate={win_rate:.2f}% "
            f"pnl={summary['pnl']:.2f} buy={summary['buy']} sell={summary['sell']}"
        )


if __name__ == "__main__":
    main()
