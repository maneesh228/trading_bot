from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class BrokerConfig:
    name: str
    live_trading: bool
    market_protection_pct: float | None = None


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
    trailing_stop_loss_pct: float | None = None
    max_daily_loss_amount: float | None = None
    max_daily_losses: int | None = None


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
class ConfirmationConfig:
    require_close_beyond_breakout: bool = True
    min_follow_through_pct: float = 0.10
    min_close_strength_pct: float = 70.0
    require_vwap_side: bool = True
    min_confirmation_volume_ratio: float = 0.6
    max_confirmation_candles: int = 2
    strong_trend_pct: float = 0.40


@dataclass(frozen=True)
class RetryAfterLossConfig:
    enabled: bool = False
    losses_before_strict: int = 1
    min_follow_through_pct: float = 0.20
    min_close_strength_pct: float = 80.0
    min_confirmation_volume_ratio: float = 0.8


@dataclass(frozen=True)
class SymbolQualityConfig:
    enabled: bool = False
    blocked_symbols: list[str] = field(default_factory=list)
    allowed_symbols: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionConfig:
    trade_selection: str = "per_symbol"
    position_sizing: str = "configured_quantity"
    confirm_entries: bool = False
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    retry_after_loss: RetryAfterLossConfig = field(default_factory=RetryAfterLossConfig)
    symbol_quality: SymbolQualityConfig = field(default_factory=SymbolQualityConfig)


@dataclass(frozen=True)
class BotConfig:
    broker: BrokerConfig
    market: MarketConfig
    risk: RiskConfig
    execution: ExecutionConfig
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

    execution_raw = dict(raw.get("execution", {}))
    confirmation_raw = execution_raw.pop("confirmation", {})
    retry_after_loss_raw = execution_raw.pop("retry_after_loss", {})
    symbol_quality_raw = execution_raw.pop("symbol_quality", {})

    return BotConfig(
        broker=BrokerConfig(**raw["broker"]),
        market=MarketConfig(**raw["market"]),
        risk=RiskConfig(**raw["risk"]),
        execution=ExecutionConfig(
            **execution_raw,
            confirmation=ConfirmationConfig(**confirmation_raw),
            retry_after_loss=RetryAfterLossConfig(**retry_after_loss_raw),
            symbol_quality=SymbolQualityConfig(
                **{
                    **symbol_quality_raw,
                    "blocked_symbols": [
                        str(symbol).upper()
                        for symbol in symbol_quality_raw.get("blocked_symbols", [])
                    ],
                    "allowed_symbols": [
                        str(symbol).upper()
                        for symbol in symbol_quality_raw.get("allowed_symbols", [])
                    ],
                }
            ),
        ),
        watchlist=watchlist,
    )
