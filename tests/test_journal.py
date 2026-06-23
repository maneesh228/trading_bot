from datetime import datetime
from pathlib import Path

from trading_bot.journal import TradeJournal, classify_candle, indicator_snapshot
from trading_bot.learning_report import generate_learning_report
from trading_bot.models import OrderRequest, OrderResult, SignalSide, Tick


def test_classify_basic_candle_patterns():
    now = datetime.now()

    bullish = classify_candle(Tick("IRCON", 105, now, open=100, high=106, low=99))
    bearish = classify_candle(Tick("IRCON", 95, now, open=100, high=101, low=94))
    doji = classify_candle(Tick("IRCON", 100.1, now, open=100, high=101, low=99))

    assert bullish["name"] == "bullish_candle"
    assert bearish["name"] == "bearish_candle"
    assert doji["name"] == "doji"


def test_indicator_snapshot_includes_vwap_and_body():
    tick = Tick(
        "IRCON",
        105,
        datetime.now(),
        open=100,
        high=106,
        low=99,
        close=105,
        volume=1000,
        vwap=102,
    )

    snapshot = indicator_snapshot(tick)

    assert snapshot["body_pct"] == 71.43
    assert snapshot["price_vs_vwap_pct"] == 2.9412


def test_learning_report_summarizes_closed_trade():
    journal_path = Path("test_learning_report_journal.jsonl")
    journal_path.write_text("", encoding="utf-8")
    journal = TradeJournal(journal_path)
    entry_tick = Tick("IRCON", 100, datetime(2026, 6, 22, 10, 0), open=99, high=101, low=98, close=100, volume=1000, vwap=99)
    exit_tick = Tick("IRCON", 102, datetime(2026, 6, 22, 10, 30), open=101, high=103, low=100, close=102, volume=1200, vwap=100)
    request = OrderRequest("IRCON", 10, SignalSide.BUY, 100, "test entry")
    journal.write("signal", {"symbol": "IRCON", "tick": entry_tick})
    journal.write("order_placed", {"order": OrderResult("dryrun-test", request, False), "tick": entry_tick})
    journal.write(
        "position_closed",
        {
            "order": OrderResult("dryrun-exit", OrderRequest("IRCON", 10, SignalSide.SELL, 102, "target"), False),
            "position": {
                "symbol": "IRCON",
                "quantity": 10,
                "side": "BUY",
                "entry_price": 100,
                "entry_time": "2026-06-22T10:00:00",
            },
            "exit_price": 102,
            "exit_reason": "target",
            "pnl_pct": 2.0,
            "tick": exit_tick,
        },
    )

    report = generate_learning_report(journal_path, datetime(2026, 6, 22).date())

    assert "trades=1" in report
    assert "gross_pnl=20.00" in report
    assert "IRCON" in report
