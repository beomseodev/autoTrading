from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.schemas import BacktestRequest
from app.services.backtest import BacktestService


class FakeProvider:
    def __init__(self, prices: pd.DataFrame) -> None:
        self.prices = prices

    def fetch_adjusted_close(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        return self.prices.loc[:, tickers]


def make_request(**overrides) -> BacktestRequest:
    payload = {
        "positions": [
            {"ticker": "AAA", "targetWeight": 60},
            {"ticker": "BBB", "targetWeight": 40},
        ],
        "initialCapital": 1000,
        "period": {"startDate": "2024-01-02", "endDate": "2024-01-10"},
        "rebalance": {"mode": "calendar", "frequency": "monthly"},
        "execution": {"fractionalShares": True, "feeRate": 0, "slippageRate": 0},
    }
    payload.update(overrides)
    return BacktestRequest.model_validate(payload)


def make_prices() -> pd.DataFrame:
    index = pd.to_datetime(
        [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-09",
            "2024-01-10",
        ]
    )
    return pd.DataFrame(
        {
            "AAA": [100, 102, 104, 103, 105, 106, 108],
            "BBB": [50, 49, 48, 50, 51, 52, 54],
        },
        index=index,
        dtype=float,
    )


def test_weight_sum_validation() -> None:
    with pytest.raises(ValueError):
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 70}, {"ticker": "BBB", "targetWeight": 20}],
                "initialCapital": 1000,
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-10"},
                "rebalance": {"mode": "calendar", "frequency": "monthly"},
            }
        )


def test_period_validation_rejects_mixed_modes() -> None:
    with pytest.raises(ValueError):
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 100}],
                "initialCapital": 1000,
                "period": {
                    "startDate": "2024-01-02",
                    "endDate": "2024-01-10",
                    "lookbackYears": 1,
                },
                "rebalance": {"mode": "calendar", "frequency": "monthly"},
            }
        )


def test_positions_with_custom_weights_drive_results() -> None:
    prices = make_prices()
    service = BacktestService(FakeProvider(prices))
    result = service.run(make_request())

    assert result.summary.finalValue == pytest.approx(1080.0, rel=1e-4)
    assert result.holdingsSnapshot[0].weight == pytest.approx(60.0, rel=1e-4)
    assert result.holdingsSnapshot[1].weight == pytest.approx(40.0, rel=1e-4)


def test_fractional_share_toggle_changes_deployed_capital() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame(
        {
            "AAA": [101, 102, 103],
            "BBB": [53, 54, 55],
        },
        index=index,
        dtype=float,
    )
    fractional_service = BacktestService(FakeProvider(prices))
    integer_service = BacktestService(FakeProvider(prices))

    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 60}, {"ticker": "BBB", "targetWeight": 40}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
        }
    )

    fractional_result = fractional_service.run(request)
    integer_request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 60}, {"ticker": "BBB", "targetWeight": 40}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": False, "feeRate": 0, "slippageRate": 0},
        }
    )
    integer_result = integer_service.run(integer_request)

    assert fractional_result.summary.deployedCapital > integer_result.summary.deployedCapital
    assert integer_result.summary.finalValue < fractional_result.summary.finalValue


def test_trading_costs_reduce_performance() -> None:
    prices = make_prices()
    service = BacktestService(FakeProvider(prices))
    no_costs = service.run(make_request())
    with_costs = service.run(
        make_request(execution={"fractionalShares": True, "feeRate": 0.01, "slippageRate": 0.01})
    )

    assert with_costs.summary.finalValue < no_costs.summary.finalValue


def test_calendar_rebalance_uses_first_trading_day_of_next_period() -> None:
    index = pd.to_datetime(
        [
            "2024-01-30",
            "2024-01-31",
            "2024-02-01",
            "2024-02-02",
            "2024-02-05",
        ]
    )
    prices = pd.DataFrame({"AAA": [100, 110, 120, 120, 120], "BBB": [100, 90, 80, 80, 80]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-30", "endDate": "2024-02-05"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
        }
    )

    result = service.run(request)

    assert result.summary.rebalanceCount == 1
    assert result.rebalanceEvents[0].date == date(2024, 2, 1)


def test_rsi_rebalance_triggers_only_on_threshold_cross() -> None:
    index = pd.to_datetime(
        [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-09",
            "2024-01-10",
        ]
    )
    prices = pd.DataFrame(
        {
            "AAA": [100, 101, 102, 103, 90, 88, 87],
            "BBB": [100, 100, 100, 100, 100, 100, 100],
        },
        index=index,
        dtype=float,
    )
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-10"},
            "rebalance": {"mode": "rsi", "rsiPeriod": 2, "lower": 30, "upper": 70},
        }
    )

    result = service.run(request)

    assert result.summary.rebalanceCount == 1
    assert result.rebalanceEvents[0].date == date(2024, 1, 9)
    assert result.rebalanceEvents[0].reason.startswith("rsi:AAA")


def test_cagr_and_mdd_match_expected_values() -> None:
    index = pd.to_datetime(["2024-01-02", "2025-01-02"])
    prices = pd.DataFrame({"AAA": [100, 110]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 100}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2025-01-02"},
            "rebalance": {"mode": "calendar", "frequency": "yearly"},
        }
    )

    result = service.run(request)

    assert result.summary.finalValue == pytest.approx(1100.0, rel=1e-4)
    assert result.summary.cagrPct == pytest.approx(10.0, abs=0.05)
    assert result.summary.mddPct == pytest.approx(0.0, abs=1e-6)
