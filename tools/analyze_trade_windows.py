from __future__ import annotations

from datetime import datetime

from trading_bot.token_store import load_runtime_credentials, make_kite_client


TRADES = {
    "ASHOKLEY": ("2026-06-25 11:45", "2026-06-25 14:25"),
    "WIPRO": ("2026-06-25 13:40", "2026-06-25 14:30"),
}


def main() -> None:
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments("NSE")
    }
    for symbol, (start_raw, end_raw) in TRADES.items():
        print(f"--- {symbol} ---")
        candles = kite.historical_data(
            instrument_token=token_by_symbol[symbol],
            from_date=datetime.strptime(start_raw, "%Y-%m-%d %H:%M"),
            to_date=datetime.strptime(end_raw, "%Y-%m-%d %H:%M"),
            interval="5minute",
            continuous=False,
            oi=False,
        )
        weighted_value = 0.0
        volume_total = 0.0
        for candle in candles:
            volume = float(candle.get("volume", 0) or 0)
            close = float(candle["close"])
            if volume > 0:
                typical = (float(candle["high"]) + float(candle["low"]) + close) / 3
                weighted_value += typical * volume
                volume_total += volume
            vwap = weighted_value / volume_total if volume_total > 0 else None
            high = float(candle["high"])
            low = float(candle["low"])
            open_price = float(candle["open"])
            candle_range = max(high - low, 0.01)
            body_pct = abs(close - open_price) / candle_range * 100
            close_strength = (close - low) / candle_range * 100
            print(
                candle["date"].strftime("%H:%M"),
                f"o={open_price:.2f}",
                f"h={high:.2f}",
                f"l={low:.2f}",
                f"c={close:.2f}",
                f"v={volume:.0f}",
                f"vwap={vwap:.2f}" if vwap is not None else "vwap=n/a",
                f"body={body_pct:.1f}%",
                f"close_strength={close_strength:.1f}%",
            )


if __name__ == "__main__":
    main()
