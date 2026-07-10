from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import mean
from typing import Any

from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    kite = make_kite_client(load_runtime_credentials())
    token_by_symbol = {
        item["tradingsymbol"]: item["instrument_token"]
        for item in kite.instruments("NSE")
    }
    candles = kite.historical_data(
        instrument_token=token_by_symbol["ASHOKLEY"],
        from_date=date.today() - timedelta(days=60),
        to_date=date.today(),
        interval="5minute",
        continuous=False,
        oi=False,
    )
    rows = find_patterns(candles)
    print("ASHOKLEY false-breakout-like probe, last 60 calendar days")
    print("Pattern: 20-candle breakout + volume spike + strong candle, then next 1-3 candles fail/reverse")
    print(f"matches={len(rows)}")
    for row in rows:
        print(
            f"{row['time']} side={row['side']} entry={row['entry']:.2f} "
            f"vwap_dist={row['vwap_dist']:.2f}% prior_3_move={row['prior_3_move']:.2f}% "
            f"next1={row['next1']:.2f}% next3={row['next3']:.2f}% outcome={row['outcome']} "
            f"reason={row['reason']}"
        )


def find_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[date, list[dict[str, Any]]] = {}
    for candle in candles:
        by_day.setdefault(candle_time(candle).date(), []).append(candle)

    rows: list[dict[str, Any]] = []
    for day_candles in by_day.values():
        day_candles.sort(key=candle_time)
        add_vwap(day_candles)
        for index in range(23, len(day_candles) - 3):
            candle = day_candles[index]
            ts = candle_time(candle)
            if ts.time() < time(9, 45) or ts.time() >= time(14, 30):
                continue

            previous = day_candles[index - 20:index]
            side = breakout_side(candle, previous)
            if side is None:
                continue

            close = float(candle["close"])
            open_price = float(candle["open"])
            high = float(candle["high"])
            low = float(candle["low"])
            candle_range = max(high - low, 0.01)
            body_pct = abs(close - open_price) / candle_range * 100
            close_strength = (close - low) / candle_range * 100
            if body_pct < 55:
                continue
            if side == "BUY" and close_strength < 70:
                continue
            if side == "SELL" and close_strength > 30:
                continue

            volume = float(candle.get("volume", 0) or 0)
            avg_volume = mean(float(item.get("volume", 0) or 0) for item in previous)
            if volume < avg_volume * 1.3:
                continue

            prior_3_start = float(day_candles[index - 3]["close"])
            prior_3_move = directional_pct(side, prior_3_start, close)
            vwap = candle.get("_vwap")
            vwap_dist = directional_vwap_pct(side, close, vwap)

            next1 = directional_pct(side, close, float(day_candles[index + 1]["close"]))
            next3 = directional_pct(side, close, float(day_candles[index + 3]["close"]))
            weak_next = next1 <= 0
            reversed_next3 = next3 <= -0.25
            stretched = prior_3_move >= 0.8 or vwap_dist >= 1.2
            if not (stretched and (weak_next or reversed_next3)):
                continue

            rows.append(
                {
                    "time": ts.strftime("%Y-%m-%d %H:%M"),
                    "side": side,
                    "entry": close,
                    "vwap_dist": vwap_dist,
                    "prior_3_move": prior_3_move,
                    "next1": next1,
                    "next3": next3,
                    "outcome": "bad" if reversed_next3 else "stalled",
                    "reason": reason(stretched, weak_next, reversed_next3),
                }
            )
    return rows


def breakout_side(candle: dict[str, Any], previous: list[dict[str, Any]]) -> str | None:
    close = float(candle["close"])
    resistance = max(float(item["high"]) for item in previous)
    support = min(float(item["low"]) for item in previous)
    buffer = 0.0005
    vwap = candle.get("_vwap")
    if close > resistance * (1 + buffer) and (vwap is None or close > vwap):
        return "BUY"
    if close < support * (1 - buffer) and (vwap is None or close < vwap):
        return "SELL"
    return None


def directional_pct(side: str, start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    if side == "BUY":
        return (end - start) / start * 100
    return (start - end) / start * 100


def directional_vwap_pct(side: str, close: float, vwap: float | None) -> float:
    if vwap is None or vwap <= 0:
        return 0.0
    if side == "BUY":
        return (close - vwap) / vwap * 100
    return (vwap - close) / vwap * 100


def reason(stretched: bool, weak_next: bool, reversed_next3: bool) -> str:
    parts = []
    if stretched:
        parts.append("stretched")
    if weak_next:
        parts.append("next candle stalled")
    if reversed_next3:
        parts.append("reversed within 3 candles")
    return ", ".join(parts)


def add_vwap(candles: list[dict[str, Any]]) -> None:
    weighted_value = 0.0
    volume_total = 0.0
    for candle in candles:
        volume = float(candle.get("volume", 0) or 0)
        close = float(candle["close"])
        if volume > 0:
            typical = (float(candle["high"]) + float(candle["low"]) + close) / 3
            weighted_value += typical * volume
            volume_total += volume
        candle["_vwap"] = weighted_value / volume_total if volume_total > 0 else None


def candle_time(candle: dict[str, Any]) -> datetime:
    value = candle["date"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


if __name__ == "__main__":
    main()
