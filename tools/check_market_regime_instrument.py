from __future__ import annotations

from trading_bot.token_store import load_runtime_credentials, make_kite_client


def main() -> None:
    kite = make_kite_client(load_runtime_credentials())
    matches = [
        item
        for item in kite.instruments("NSE")
        if item.get("tradingsymbol") == "NIFTY 50"
    ]
    if not matches:
        raise SystemExit("NIFTY 50 instrument not found")
    item = matches[0]
    print(f"NIFTY 50 token={item['instrument_token']} segment={item.get('segment')}")


if __name__ == "__main__":
    main()
