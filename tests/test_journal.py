from datetime import datetime

from trading_bot.journal import classify_candle
from trading_bot.models import Tick


def test_classify_basic_candle_patterns():
    now = datetime.now()

    bullish = classify_candle(Tick("IRCON", 105, now, open=100, high=106, low=99))
    bearish = classify_candle(Tick("IRCON", 95, now, open=100, high=101, low=94))
    doji = classify_candle(Tick("IRCON", 100.1, now, open=100, high=101, low=99))

    assert bullish["name"] == "bullish_candle"
    assert bearish["name"] == "bearish_candle"
    assert doji["name"] == "doji"
