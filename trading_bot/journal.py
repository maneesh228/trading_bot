from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from trading_bot.models import Tick


class TradeJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        payload = _enrich_payload(payload)
        record = {
            "event_type": event_type,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_to_jsonable(record), separators=(",", ":")) + "\n")


def _enrich_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tick = payload.get("tick")
    if isinstance(tick, Tick):
        return {
            **payload,
            "candle_pattern": classify_candle(tick),
            "indicator_snapshot": indicator_snapshot(tick),
        }
    return payload


def indicator_snapshot(tick: Tick) -> dict[str, Any]:
    close = tick.close if tick.close is not None else tick.price
    snapshot: dict[str, Any] = {
        "symbol": tick.symbol,
        "price": tick.price,
        "timestamp": tick.timestamp,
        "open": tick.open,
        "high": tick.high,
        "low": tick.low,
        "close": close,
        "volume": tick.volume,
        "vwap": tick.vwap,
    }

    if tick.open is not None and tick.high is not None and tick.low is not None:
        candle_range = tick.high - tick.low
        snapshot["candle_range"] = round(candle_range, 4)
        if candle_range > 0:
            snapshot["body_pct"] = round((abs(close - tick.open) / candle_range) * 100, 2)
        else:
            snapshot["body_pct"] = 0.0

    if tick.vwap is not None and tick.vwap > 0:
        snapshot["price_vs_vwap_pct"] = round(((tick.price - tick.vwap) / tick.vwap) * 100, 4)

    if tick.open is not None and tick.open > 0:
        snapshot["price_vs_open_pct"] = round(((tick.price - tick.open) / tick.open) * 100, 4)

    return snapshot


def classify_candle(tick: Tick) -> dict[str, Any]:
    if tick.open is None or tick.high is None or tick.low is None:
        return {
            "available": False,
            "name": "unavailable",
            "reason": "tick has no OHLC values",
        }

    close = tick.price
    open_price = tick.open
    high = tick.high
    low = tick.low
    candle_range = max(high - low, 0.0)
    body = abs(close - open_price)
    upper_wick = max(high - max(open_price, close), 0.0)
    lower_wick = max(min(open_price, close) - low, 0.0)

    if candle_range == 0:
        return {
            "available": True,
            "name": "flat",
            "direction": "neutral",
            "body_pct": 0.0,
            "upper_wick_pct": 0.0,
            "lower_wick_pct": 0.0,
        }

    body_pct = (body / candle_range) * 100
    upper_wick_pct = (upper_wick / candle_range) * 100
    lower_wick_pct = (lower_wick / candle_range) * 100

    if body_pct <= 10:
        name = "doji"
        direction = "neutral"
    elif close > open_price:
        name = "bullish_candle"
        direction = "bullish"
    elif close < open_price:
        name = "bearish_candle"
        direction = "bearish"
    else:
        name = "doji"
        direction = "neutral"

    if lower_wick_pct >= 55 and body_pct <= 35:
        name = "hammer" if direction != "bearish" else "hanging_man"
    elif upper_wick_pct >= 55 and body_pct <= 35:
        name = "shooting_star" if direction != "bullish" else "inverted_hammer"

    return {
        "available": True,
        "name": name,
        "direction": direction,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "body_pct": round(body_pct, 2),
        "upper_wick_pct": round(upper_wick_pct, 2),
        "lower_wick_pct": round(lower_wick_pct, 2),
    }


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
