from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

from trading_bot.backtest import run_backtest
from trading_bot.broker import ZerodhaBroker, make_kite_for_login
from trading_bot.config import load_config
from trading_bot.engine import TradingEngine
from trading_bot.journal import TradeJournal
from trading_bot.learning_report import generate_learning_report
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def login_url() -> None:
    kite = make_kite_for_login()
    print(kite.login_url())


def generate_token(request_token: str) -> None:
    load_dotenv()
    api_secret = os.getenv("KITE_API_SECRET")
    if not api_secret:
        raise RuntimeError("KITE_API_SECRET is required")
    kite = make_kite_for_login()
    session = kite.generate_session(request_token, api_secret=api_secret)
    print(session["access_token"])


def run(config_path: str, journal_path: str) -> None:
    config = load_config(config_path)
    if config.broker.name != "zerodha":
        raise ValueError(f"Unsupported broker: {config.broker.name}")
    broker = ZerodhaBroker(
        exchange=config.market.exchange,
        live_trading=config.broker.live_trading,
        market_protection_pct=config.broker.market_protection_pct,
    )
    TradingEngine(config=config, broker=broker, journal=TradeJournal(journal_path)).run_forever()


def backtest(config_path: str, days: int, interval: str) -> None:
    config = load_config(config_path)
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

    result = run_backtest(config, candles_by_symbol)
    print(f"Backtest interval={interval} days_requested={days} trading_days={result.days}")
    print(f"Trades={len(result.trades)} wins={result.wins} losses={result.losses} win_rate={result.win_rate:.2f}%")
    print(f"Total PnL={result.total_pnl:.2f}")
    for trade in result.trades:
        print(
            f"{trade.symbol} {trade.side.value} "
            f"entry={trade.entry_time:%Y-%m-%d %H:%M} @{trade.entry_price:.2f} "
            f"exit={trade.exit_time:%Y-%m-%d %H:%M} @{trade.exit_price:.2f} "
            f"qty={trade.quantity} pnl={trade.pnl:.2f} pnl_pct={trade.pnl_pct:.2f}%"
        )


def learning_report(journal_path: str, report_date: str | None) -> None:
    parsed_date = datetime.fromisoformat(report_date).date() if report_date else None
    print(generate_learning_report(journal_path, parsed_date))


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="trading-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login-url")

    token_parser = subparsers.add_parser("generate-token")
    token_parser.add_argument("--request-token", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", default="config.yaml")
    run_parser.add_argument("--journal", default="data/trade_journal.jsonl")

    backtest_parser = subparsers.add_parser("backtest")
    backtest_parser.add_argument("--config", default="config.yaml")
    backtest_parser.add_argument("--days", type=int, default=30)
    backtest_parser.add_argument("--interval", default="5minute")

    report_parser = subparsers.add_parser("learning-report")
    report_parser.add_argument("--journal", default="data/trade_journal.jsonl")
    report_parser.add_argument("--date")

    args = parser.parse_args()
    if args.command == "login-url":
        login_url()
    elif args.command == "generate-token":
        generate_token(args.request_token)
    elif args.command == "run":
        run(args.config, args.journal)
    elif args.command == "backtest":
        backtest(args.config, args.days, args.interval)
    elif args.command == "learning-report":
        learning_report(args.journal, args.date)


if __name__ == "__main__":
    main()
