from __future__ import annotations

from datetime import datetime

from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments("NSE")
    }
    candles = kite.historical_data(
        instrument_token=token_by_symbol["CANBK"],
        from_date=datetime(2026, 6, 24, 9, 15),
        to_date=datetime(2026, 6, 24, 15, 15),
        interval="5minute",
        continuous=False,
        oi=False,
    )
    for candle in candles:
        ts = candle["date"]
        label = ts.strftime("%H:%M")
        if "11:20" <= label <= "12:45":
            body_pct = abs(candle["close"] - candle["open"]) / max(candle["high"] - candle["low"], 0.01) * 100
            close_strength = (candle["close"] - candle["low"]) / max(candle["high"] - candle["low"], 0.01) * 100
            print(
                label,
                f"o={candle['open']:.2f}",
                f"h={candle['high']:.2f}",
                f"l={candle['low']:.2f}",
                f"c={candle['close']:.2f}",
                f"v={candle['volume']}",
                f"body={body_pct:.1f}%",
                f"close_strength={close_strength:.1f}%",
            )


if __name__ == "__main__":
    main()
