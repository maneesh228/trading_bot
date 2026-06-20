from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from trading_bot.broker import ZerodhaBroker, make_kite_for_login
from trading_bot.config import load_config
from trading_bot.engine import TradingEngine


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


def run(config_path: str) -> None:
    config = load_config(config_path)
    if config.broker.name != "zerodha":
        raise ValueError(f"Unsupported broker: {config.broker.name}")
    broker = ZerodhaBroker(
        exchange=config.market.exchange,
        live_trading=config.broker.live_trading,
    )
    TradingEngine(config=config, broker=broker).run_forever()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="trading-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login-url")

    token_parser = subparsers.add_parser("generate-token")
    token_parser.add_argument("--request-token", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", default="config.yaml")

    args = parser.parse_args()
    if args.command == "login-url":
        login_url()
    elif args.command == "generate-token":
        generate_token(args.request_token)
    elif args.command == "run":
        run(args.config)


if __name__ == "__main__":
    main()

