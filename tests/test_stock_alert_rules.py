import math

import pytest
from app.alert_rules import PARAMS, evaluate_rule, minimum_bars, normalize_rule


def bars(prices, volume=10):
    return [{"close": x, "volume": volume} for x in prices]


@pytest.mark.parametrize("kind,params,prices,expected", [
    ("price_ma_cross", {"period": 2}, [3, 2, 4], ["up"]),
    ("price_ma_cross", {"period": 2}, [1, 2, 3], []),
    ("ma_cross", {"shortPeriod": 2, "longPeriod": 3}, [4, 3, 2, 6], ["up"]),
    ("price_cross", {"price": 10}, [10, 11], ["up"]),
    ("price_cross", {"price": 10}, [9, 10], []),
    ("daily_change", {"thresholdPct": 5}, [100, 105], ["up"]),
    ("range_breakout", {"period": 2}, [3, 2, 4], ["up"]),
    ("range_breakout", {"period": 2}, [3, 2, 3], []),
    ("volume_spike", {"period": 2}, [3, 2, 4], []),
    ("drawdown_cross", {"period": 3, "thresholdPct": 10}, [100, 100, 100, 90], ["drawdown"]),
    ("rsi_cross", {"period": 2}, [100] * 5, []),
    ("macd_cross", {"fastPeriod": 2, "slowPeriod": 3, "signalPeriod": 2}, [3] * 6, []),
    ("bollinger_cross", {"period": 2, "stddev": .5}, [3, 2, 4], ["upper_exit"]),
])
def test_signals(kind, params, prices, expected):
    result = evaluate_rule(kind, params, bars(prices))
    assert result["triggers"] == expected
    assert result["status"] == ("signal" if expected else "no_signal")


@pytest.mark.parametrize("kind", list(PARAMS))
def test_minimum_and_validation(kind):
    params = {"price": 10} if kind == "price_cross" else {}
    p = normalize_rule(kind, params)
    n = minimum_bars(kind, p)
    assert evaluate_rule(kind, p, bars([10] * (n - 1)))["status"] == "insufficient_data"
    assert evaluate_rule(kind, p, bars([10] * n))["status"] == "no_signal"
    with pytest.raises(ValueError):
        normalize_rule(kind, {**params, "userId": "forged"})


@pytest.mark.parametrize("price", [math.nan, math.inf, 0, -1])
def test_invalid_prices(price):
    assert evaluate_rule("price_cross", {"price": 10}, bars([10, price]))["status"] == "invalid_data"


def test_volume_and_rsi_boundaries():
    data = bars([10, 10, 10]); data[-1]["volume"] = 20
    assert evaluate_rule("volume_spike", {"period": 2}, data)["triggers"] == ["volume"]
    assert evaluate_rule("volume_spike", {"period": 2}, bars([10] * 3, 0))["status"] == "invalid_data"
    assert evaluate_rule("rsi_cross", {"period": 2}, bars([10] * 4))["values"]["value"] == 50
    assert evaluate_rule("rsi_cross", {"period": 2}, bars([10, 11, 12, 13]))["values"]["value"] == 100


def test_200_day_cross_and_direction():
    data = bars([10] * 200 + [11])
    assert evaluate_rule("price_ma_cross", {}, data)["triggers"] == ["up"]
    assert evaluate_rule("price_ma_cross", {"direction": "down"}, data)["triggers"] == []
    assert evaluate_rule("price_ma_cross", {}, data + bars([12]))["triggers"] == []
