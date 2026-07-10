from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Append paper-test symbols to a bot config watchlist")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--symbols", required=True, help="comma-separated NSE symbols")
    args = parser.parse_args()

    path = Path(args.config)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    watchlist = raw.setdefault("watchlist", [])
    if not watchlist:
        raise RuntimeError("watchlist is empty; cannot copy strategy template")

    existing = {str(item["symbol"]).upper() for item in watchlist}
    template_strategy = watchlist[0]["strategy"]
    added = []
    for symbol in parse_symbols(args.symbols):
        if symbol in existing:
            continue
        watchlist.append(
            {
                "symbol": symbol,
                "quantity": 1,
                "strategy": template_strategy,
            }
        )
        existing.add(symbol)
        added.append(symbol)

    broker = raw.setdefault("broker", {})
    broker["live_trading"] = False
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    print(f"added={','.join(added) if added else 'none'}")
    print("watchlist=" + ",".join(str(item["symbol"]).upper() for item in watchlist))
    print(f"live_trading={broker.get('live_trading')}")


def parse_symbols(raw: str) -> list[str]:
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


if __name__ == "__main__":
    main()
