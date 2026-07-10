from trading_bot.config import load_config


def test_symbol_quality_config_normalizes_symbols(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
broker:
  name: zerodha
  live_trading: false
market:
  exchange: NSE
  poll_interval_seconds: 5
  square_off_time: "15:15"
risk:
  max_trades_per_day: 10
  max_position_value: 10000
  per_trade_stop_loss_pct: 0.6
  per_trade_target_pct: 1.0
execution:
  symbol_quality:
    enabled: true
    blocked_symbols:
      - idfcfirstb
    allowed_symbols:
      - infy
watchlist:
  - symbol: INFY
    quantity: 1
    strategy:
      name: support_resistance_breakout
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.execution.symbol_quality.enabled
    assert config.execution.symbol_quality.blocked_symbols == ["IDFCFIRSTB"]
    assert config.execution.symbol_quality.allowed_symbols == ["INFY"]
