from __future__ import annotations

from trading_bot.config import load_config


def main() -> None:
    config = load_config("/opt/ai_trading_agent/config.yaml")
    print(config.execution.market_regime)
    print(f"live_trading={config.broker.live_trading}")
    print(f"watchlist={','.join(item.symbol for item in config.watchlist)}")


if __name__ == "__main__":
    main()
