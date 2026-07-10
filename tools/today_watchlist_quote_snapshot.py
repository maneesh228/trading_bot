from __future__ import annotations

from trading_bot.config import load_config
from trading_bot.token_store import load_runtime_credentials, make_kite_client


def classify_move(move_pct: float, threshold_pct: float = 0.35) -> str:
    if move_pct >= threshold_pct:
        return "uptrend"
    if move_pct <= -threshold_pct:
        return "downtrend"
    return "sideways"


def main() -> None:
    config = load_config("config.yaml")
    kite = make_kite_client(load_runtime_credentials())
    instruments = [f"{config.market.exchange}:{item.symbol}" for item in config.watchlist]
    quotes = kite.quote(instruments)

    print(f"TODAY_QUOTE_SNAPSHOT symbols={','.join(item.symbol for item in config.watchlist)}")
    for item in config.watchlist:
        key = f"{config.market.exchange}:{item.symbol}"
        quote = quotes.get(key)
        if not quote:
            print(f"{item.symbol}: no quote")
            continue
        price = float(quote["last_price"])
        ohlc = quote.get("ohlc", {})
        open_price = float(ohlc.get("open") or 0)
        high = float(ohlc.get("high") or 0)
        low = float(ohlc.get("low") or 0)
        previous_close = float(ohlc.get("close") or 0)
        day_move_pct = ((price - previous_close) / previous_close) * 100 if previous_close > 0 else 0.0
        open_move_pct = ((price - open_price) / open_price) * 100 if open_price > 0 else 0.0
        range_pct = ((high - low) / price) * 100 if price > 0 else 0.0
        trend = classify_move(open_move_pct)
        print(
            f"{item.symbol}: trend={trend} open_move={open_move_pct:.2f}% "
            f"day_move={day_move_pct:.2f}% ltp={price:.2f} open={open_price:.2f} "
            f"high={high:.2f} low={low:.2f} range={range_pct:.2f}%"
        )


if __name__ == "__main__":
    main()
