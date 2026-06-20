from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class BrokerConfig:
    name: str
    live_trading: bool


@dataclass(frozen=True)
class MarketConfig:
    exchange: str
    poll_interval_seconds: int
    square_off_time: str


@dataclass(frozen=True)
class RiskConfig:
    max_trades_per_day: int
    max_position_value: float
    per_trade_stop_loss_pct: float
    per_trade_target_pct: float


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    params: dict[str, Any]


@dataclass(frozen=True)
class WatchSymbol:
    symbol: str
    quantity: int
    strategy: StrategyConfig


@dataclass(frozen=True)
class BotConfig:
    broker: BrokerConfig
    market: MarketConfig
    risk: RiskConfig
    watchlist: list[WatchSymbol]


def load_config(path: str | Path) -> BotConfig:
    with Path(path).open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    watchlist = []
    for item in raw.get("watchlist", []):
        strategy = item.get("strategy", {})
        name = strategy.get("name")
        if not name:
            raise ValueError(f"Missing strategy.name for {item.get('symbol')}")
        params = {key: value for key, value in strategy.items() if key != "name"}
        watchlist.append(
            WatchSymbol(
                symbol=str(item["symbol"]).upper(),
                quantity=int(item["quantity"]),
                strategy=StrategyConfig(name=name, params=params),
            )
        )

    if not watchlist:
        raise ValueError("watchlist must contain at least one symbol")

    return BotConfig(
        broker=BrokerConfig(**raw["broker"]),
        market=MarketConfig(**raw["market"]),
        risk=RiskConfig(**raw["risk"]),
        watchlist=watchlist,
    )

