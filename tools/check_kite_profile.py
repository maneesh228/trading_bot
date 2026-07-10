from trading_bot.token_store import load_runtime_credentials, make_kite_client


profile = make_kite_client(load_runtime_credentials()).profile()
print("kite_ok user_id=" + profile.get("user_id", "unknown"))
