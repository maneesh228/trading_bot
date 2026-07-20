from __future__ import annotations

from pathlib import Path

import yaml


CONFIG_PATH = Path("/opt/ai_trading_agent/config.yaml")


def main() -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    execution = raw.setdefault("execution", {})
    market_regime = execution.setdefault("market_regime", {})
    market_regime.update(
        {
            "enabled": True,
            "index_symbol": "NIFTY 50",
            "require_average_side": True,
            "min_trend_6_pct": 0.05,
            "allow_strong_stock_exception": True,
            "exception_side": "SELL",
            "exception_min_stock_vwap_distance_pct": 1.0,
            "exception_min_signal_strength_pct": 0.50,
        }
    )
    CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    print("enabled market_regime strong SELL exception in", CONFIG_PATH)


if __name__ == "__main__":
    main()
