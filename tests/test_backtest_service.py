from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.schemas import BacktestRequest
from app.services.backtest import BacktestService
from app.services.data_provider import MarketData


class FakeProvider:
    def __init__(
        self,
        prices: pd.DataFrame,
        dividends: pd.DataFrame | None = None,
    ) -> None:
        self.prices = prices
        self.dividends = dividends if dividends is not None else pd.DataFrame(0.0, index=prices.index, columns=prices.columns)

    def fetch_market_data(self, tickers: list[str], start_date: date, end_date: date) -> MarketData:
        return MarketData(
            prices=self.prices.loc[:, tickers],
            dividends=self.dividends.loc[:, tickers],
        )


def make_request(**overrides) -> BacktestRequest:
    payload = {
        "positions": [
            {"ticker": "AAA", "targetWeight": 60},
            {"ticker": "BBB", "targetWeight": 40},
        ],
        "initialCapital": 1000,
        "period": {"startDate": "2024-01-02", "endDate": "2024-01-10"},
        "rebalance": {"mode": "calendar", "frequency": "monthly"},
        "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
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


def test_monthly_contribution_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 100}],
                "initialCapital": 1000,
                "monthlyContribution": -1,
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-10"},
                "rebalance": {"mode": "calendar", "frequency": "monthly"},
            }
        )


def test_dividend_reinvestment_defaults_to_true() -> None:
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 100}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
        }
    )

    assert request.execution.dividendReinvestment is True


def test_positions_with_custom_weights_drive_results() -> None:
    prices = make_prices()
    service = BacktestService(FakeProvider(prices))
    result = service.run(make_request())

    assert result.summary.finalValue == pytest.approx(1080.0, rel=1e-4)
    assert result.holdingsSnapshot[0].weight == pytest.approx(60.0, rel=1e-4)
    assert result.holdingsSnapshot[1].weight == pytest.approx(40.0, rel=1e-4)


def test_zero_monthly_contribution_preserves_lump_sum_behavior() -> None:
    prices = make_prices()
    service = BacktestService(FakeProvider(prices))

    baseline = service.run(make_request())
    explicit_zero = service.run(make_request(monthlyContribution=0))

    assert explicit_zero.summary.finalValue == pytest.approx(baseline.summary.finalValue, rel=1e-6)
    assert explicit_zero.summary.totalContributed == pytest.approx(baseline.summary.initialCapital, rel=1e-6)
    assert explicit_zero.summary.cagrPct == pytest.approx(baseline.summary.cagrPct, rel=1e-6)
    assert explicit_zero.summary.xirrPct == pytest.approx(baseline.summary.xirrPct, rel=1e-6)
    assert explicit_zero.summary.realFinalValue == pytest.approx(baseline.summary.realFinalValue, rel=1e-6)
    assert explicit_zero.summary.realTotalReturnPct == pytest.approx(baseline.summary.realTotalReturnPct, rel=1e-6)


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
            "execution": {"fractionalShares": False, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
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
        make_request(
            execution={
                "fractionalShares": True,
                "dividendReinvestment": True,
                "feeRate": 0.01,
                "slippageRate": 0.01,
            }
        )
    )

    assert with_costs.summary.finalValue < no_costs.summary.finalValue


def test_monthly_contribution_starts_on_first_trading_day_of_next_month() -> None:
    index = pd.to_datetime(["2024-01-15", "2024-02-01", "2024-02-02", "2024-03-01", "2024-03-04"])
    prices = pd.DataFrame({"AAA": [100, 100, 100, 100, 100], "BBB": [100, 100, 100, 100, 100]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "monthlyContribution": 100,
            "period": {"startDate": "2024-01-15", "endDate": "2024-03-04"},
            "rebalance": {"mode": "calendar", "frequency": "yearly"},
        }
    )

    result = service.run(request)

    assert result.summary.totalContributed == pytest.approx(1200.0, rel=1e-6)
    assert result.summary.monthlyContribution == pytest.approx(100.0, rel=1e-6)
    assert result.summary.rebalanceCount == 2
    assert [event.date for event in result.rebalanceEvents] == [date(2024, 2, 1), date(2024, 3, 1)]
    assert all(event.reason == "contribution:monthly" for event in result.rebalanceEvents)


def test_monthly_contribution_is_invested_by_target_weights_immediately() -> None:
    index = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
    prices = pd.DataFrame({"AAA": [100, 100, 100], "BBB": [50, 50, 50]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 60}, {"ticker": "BBB", "targetWeight": 40}],
            "initialCapital": 1000,
            "monthlyContribution": 100,
            "period": {"startDate": "2024-01-31", "endDate": "2024-02-02"},
            "rebalance": {"mode": "calendar", "frequency": "yearly"},
        }
    )

    result = service.run(request)

    assert result.summary.totalContributed == pytest.approx(1100.0, rel=1e-6)
    assert result.summary.finalValue == pytest.approx(1100.0, rel=1e-6)
    assert result.summary.cagrPct is None
    assert result.summary.xirrPct == pytest.approx(0.0, abs=1e-6)
    assert result.summary.rebalanceCount == 1
    assert result.rebalanceEvents[0].reason == "contribution:monthly"
    assert result.holdingsSnapshot[0].shares == pytest.approx(6.6, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(8.8, rel=1e-6)


def test_dividends_accumulate_as_cash_when_auto_reinvest_disabled() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame({"AAA": [100, 100, 100], "BBB": [100, 100, 100]}, index=index, dtype=float)
    dividends = pd.DataFrame({"AAA": [0, 2, 0], "BBB": [0, 0, 0]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices, dividends=dividends))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "dividendReinvestment": False, "feeRate": 0, "slippageRate": 0},
        }
    )

    result = service.run(request)

    assert result.summary.finalValue == pytest.approx(1010.0, rel=1e-6)
    assert result.summary.rebalanceCount == 0
    assert result.holdingsSnapshot[0].shares == pytest.approx(5.0, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(5.0, rel=1e-6)


def test_ter_daily_drag_reduces_final_value_and_tracks_total_expense() -> None:
    """수정: 2026-04-24 — 연 TER 일일 차감 시 최종 가치 감소 및 totalExpensePaid 누적 검증."""
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame({"AAA": [100, 100, 100], "BBB": [100, 100, 100]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request_no_ter = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "feeRate": 0, "slippageRate": 0},
        }
    )
    request_ter = BacktestRequest.model_validate(
        {
            "positions": [
                {"ticker": "AAA", "targetWeight": 50, "annualExpenseRatio": 0.003},
                {"ticker": "BBB", "targetWeight": 50, "annualExpenseRatio": 0.003},
            ],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "feeRate": 0, "slippageRate": 0},
        }
    )

    out0 = service.run(request_no_ter)
    out1 = service.run(request_ter)

    assert out0.summary.totalExpensePaid == 0
    assert out1.summary.totalExpensePaid > 0
    assert out1.summary.finalValue < out0.summary.finalValue
    # 3거래일 × 시총 약 1000 × 가중 평균 TER 0.003 / 252
    assert out1.summary.totalExpensePaid == pytest.approx(3 * 1000 * 0.003 / 252, rel=0.02)


def test_dividend_tax_reduces_cash_and_reinvestment() -> None:
    """수정: 2026-04-23 — 배당소득세율 적용 시 순배당만 현금·재투자에 반영되는지 검증."""
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame({"AAA": [100, 100, 100], "BBB": [100, 100, 100]}, index=index, dtype=float)
    dividends = pd.DataFrame({"AAA": [0, 2, 0], "BBB": [0, 0, 0]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices, dividends=dividends))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {
                "fractionalShares": True,
                "dividendReinvestment": True,
                "dividendTaxRate": 0.154,
                "feeRate": 0,
                "slippageRate": 0,
            },
        }
    )

    result = service.run(request)

    assert result.summary.finalValue == pytest.approx(1008.46, rel=1e-6)
    assert result.holdingsSnapshot[0].shares == pytest.approx(5.0423, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(5.0423, rel=1e-6)


def test_dividends_are_reinvested_by_target_weights_when_enabled() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame({"AAA": [100, 100, 100], "BBB": [100, 100, 100]}, index=index, dtype=float)
    dividends = pd.DataFrame({"AAA": [0, 2, 0], "BBB": [0, 0, 0]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices, dividends=dividends))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
        }
    )

    result = service.run(request)

    assert result.summary.finalValue == pytest.approx(1010.0, rel=1e-6)
    assert result.summary.rebalanceCount == 1
    assert result.rebalanceEvents[0].reason == "dividend-reinvest"
    assert result.holdingsSnapshot[0].shares == pytest.approx(5.05, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(5.05, rel=1e-6)


def test_monthly_contribution_happens_before_calendar_rebalance_on_same_day() -> None:
    index = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
    prices = pd.DataFrame({"AAA": [100, 200, 200], "BBB": [100, 100, 100]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "monthlyContribution": 100,
            "period": {"startDate": "2024-01-31", "endDate": "2024-02-02"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
        }
    )

    result = service.run(request)

    assert result.summary.rebalanceCount == 2
    assert result.rebalanceEvents[0].date == date(2024, 2, 1)
    assert result.rebalanceEvents[0].reason == "contribution:monthly"
    assert result.rebalanceEvents[1].date == date(2024, 2, 1)
    assert result.rebalanceEvents[1].reason == "calendar:monthly"


def test_dividend_reinvestment_combines_multiple_dividend_sources_once() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame({"AAA": [100, 100, 100], "BBB": [100, 100, 100]}, index=index, dtype=float)
    dividends = pd.DataFrame({"AAA": [0, 1, 0], "BBB": [0, 2, 0]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices, dividends=dividends))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 60}, {"ticker": "BBB", "targetWeight": 40}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
        }
    )

    result = service.run(request)

    assert result.summary.rebalanceCount == 1
    assert result.holdingsSnapshot[0].shares == pytest.approx(6.084, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(4.056, rel=1e-6)


def test_dividend_reinvestment_with_integer_shares_leaves_cash_residual() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame({"AAA": [10, 10, 10], "BBB": [50, 50, 50]}, index=index, dtype=float)
    dividends = pd.DataFrame({"AAA": [0, 0.25, 0], "BBB": [0, 1.25, 0]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices, dividends=dividends))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 60}, {"ticker": "BBB", "targetWeight": 40}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": False, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
        }
    )

    result = service.run(request)

    assert result.summary.finalValue == pytest.approx(1025.0, rel=1e-6)
    assert result.holdingsSnapshot[0].shares == pytest.approx(61.0, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(8.0, rel=1e-6)


def test_monthly_contribution_with_integer_shares_leaves_cash_residual() -> None:
    index = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
    prices = pd.DataFrame({"AAA": [10, 10, 10], "BBB": [50, 50, 50]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 60}, {"ticker": "BBB", "targetWeight": 40}],
            "initialCapital": 1000,
            "monthlyContribution": 25,
            "period": {"startDate": "2024-01-31", "endDate": "2024-02-02"},
            "rebalance": {"mode": "calendar", "frequency": "yearly"},
            "execution": {"fractionalShares": False, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
        }
    )

    result = service.run(request)

    assert result.summary.totalContributed == pytest.approx(1025.0, rel=1e-6)
    assert result.summary.deployedCapital == pytest.approx(1010.0, rel=1e-6)
    assert result.summary.finalValue == pytest.approx(1025.0, rel=1e-6)
    assert result.summary.xirrPct == pytest.approx(0.0, abs=1e-6)
    assert result.summary.rebalanceCount == 1
    assert result.holdingsSnapshot[0].shares == pytest.approx(61.0, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(8.0, rel=1e-6)


def test_dividend_reinvestment_respects_trading_costs() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame({"AAA": [10, 10, 10]}, index=index, dtype=float)
    dividends = pd.DataFrame({"AAA": [0, 0.1, 0]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices, dividends=dividends))

    no_costs = service.run(
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 100}],
                "initialCapital": 1000,
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
                "rebalance": {"mode": "calendar", "frequency": "monthly"},
                "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
            }
        )
    )
    with_costs = service.run(
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 100}],
                "initialCapital": 1000,
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
                "rebalance": {"mode": "calendar", "frequency": "monthly"},
                "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0.1, "slippageRate": 0},
            }
        )
    )

    assert with_costs.holdingsSnapshot[0].shares < no_costs.holdingsSnapshot[0].shares
    assert with_costs.summary.finalValue < no_costs.summary.finalValue


def test_monthly_contribution_respects_trading_costs() -> None:
    index = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
    prices = pd.DataFrame({"AAA": [10, 10, 10]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))

    no_costs = service.run(
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 100}],
                "initialCapital": 1000,
                "monthlyContribution": 100,
                "period": {"startDate": "2024-01-31", "endDate": "2024-02-02"},
                "rebalance": {"mode": "calendar", "frequency": "yearly"},
                "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
            }
        )
    )
    with_costs = service.run(
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 100}],
                "initialCapital": 1000,
                "monthlyContribution": 100,
                "period": {"startDate": "2024-01-31", "endDate": "2024-02-02"},
                "rebalance": {"mode": "calendar", "frequency": "yearly"},
                "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0.1, "slippageRate": 0},
            }
        )
    )

    assert with_costs.holdingsSnapshot[0].shares < no_costs.holdingsSnapshot[0].shares
    assert with_costs.summary.finalValue < no_costs.summary.finalValue


def test_dividend_reinvestment_happens_before_calendar_rebalance_on_same_day() -> None:
    index = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
    prices = pd.DataFrame({"AAA": [100, 200, 200], "BBB": [100, 100, 100]}, index=index, dtype=float)
    dividends = pd.DataFrame({"AAA": [0, 20, 0], "BBB": [0, 0, 0]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices, dividends=dividends))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-31", "endDate": "2024-02-02"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "dividendReinvestment": True, "feeRate": 0, "slippageRate": 0},
        }
    )

    result = service.run(request)

    assert result.summary.rebalanceCount == 2
    assert result.rebalanceEvents[0].date == date(2024, 2, 1)
    assert result.rebalanceEvents[0].reason == "dividend-reinvest"
    assert result.rebalanceEvents[1].date == date(2024, 2, 1)
    assert result.rebalanceEvents[1].reason == "calendar:monthly"


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


def test_rsi_single_scope_requires_trigger_ticker() -> None:
    with pytest.raises(ValueError, match="rsiTriggerTicker is required"):
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
                "initialCapital": 1000,
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-10"},
                "rebalance": {
                    "mode": "rsi",
                    "rsiPeriod": 2,
                    "lower": 30,
                    "upper": 70,
                    "rsiSignalScope": "single",
                },
            }
        )


def test_rsi_single_scope_rejects_unknown_trigger_ticker() -> None:
    with pytest.raises(ValueError, match="rsiTriggerTicker must match"):
        BacktestRequest.model_validate(
            {
                "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
                "initialCapital": 1000,
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-10"},
                "rebalance": {
                    "mode": "rsi",
                    "rsiPeriod": 2,
                    "lower": 30,
                    "upper": 70,
                    "rsiSignalScope": "single",
                    "rsiTriggerTicker": "ZZZ",
                },
            }
        )


def test_rsi_single_scope_only_uses_selected_ticker_signal() -> None:
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
            "AAA": [100, 100, 100, 100, 100, 100, 100],
            "BBB": [100, 101, 102, 103, 90, 88, 87],
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
            "rebalance": {
                "mode": "rsi",
                "rsiPeriod": 2,
                "lower": 30,
                "upper": 70,
                "rsiSignalScope": "single",
                "rsiTriggerTicker": " aaa ",
            },
        }
    )

    result = service.run(request)

    assert result.summary.rebalanceCount == 0


def test_rsi_single_scope_rebalances_full_portfolio_when_selected_ticker_signals() -> None:
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
            "BBB": [100, 120, 140, 160, 200, 210, 220],
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
            "rebalance": {
                "mode": "rsi",
                "rsiPeriod": 2,
                "lower": 30,
                "upper": 70,
                "rsiSignalScope": "single",
                "rsiTriggerTicker": "AAA",
            },
        }
    )

    result = service.run(request)

    assert result.summary.rebalanceCount == 1
    assert result.rebalanceEvents[0].reason.startswith("rsi:AAA")
    assert result.holdingsSnapshot[0].shares == pytest.approx(8.46590909, rel=1e-6)
    assert result.holdingsSnapshot[1].shares == pytest.approx(3.54761905, rel=1e-6)


def test_split_day_close_prices_do_not_create_artificial_jump() -> None:
    index = pd.to_datetime(["2021-01-20", "2021-01-21", "2021-01-22"])
    prices = pd.DataFrame({"TQQQ": [24.745001, 25.360001, 25.129999]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "TQQQ", "targetWeight": 100}],
            "initialCapital": 1000,
            "period": {"startDate": "2021-01-20", "endDate": "2021-01-22"},
            "rebalance": {"mode": "calendar", "frequency": "yearly"},
        }
    )

    result = service.run(request)

    assert result.equityCurve[1].value == pytest.approx(1024.8537, rel=1e-4)
    assert result.equityCurve[2].value == pytest.approx(1015.5595, rel=1e-4)


def test_schd_tqqq_rsi_single_does_not_spike_on_known_tqqq_split_days() -> None:
    index = pd.to_datetime(
        [
            "2021-01-20",
            "2021-01-21",
            "2021-01-22",
            "2022-01-12",
            "2022-01-13",
            "2022-01-14",
            "2025-11-19",
            "2025-11-20",
            "2025-11-21",
        ]
    )
    prices = pd.DataFrame(
        {
            "SCHD": [22.129999, 22.07, 21.906668, 27.299999, 27.263332, 27.236668, 26.93, 26.620001, 27.10],
            "TQQQ": [24.745001, 25.360001, 25.129999, 38.169998, 35.400002, 35.935001, 50.025002, 46.450001, 47.48],
        },
        index=index,
        dtype=float,
    )
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "SCHD", "targetWeight": 50}, {"ticker": "TQQQ", "targetWeight": 50}],
            "initialCapital": 10000,
            "period": {"startDate": "2021-01-20", "endDate": "2025-11-21"},
            "rebalance": {
                "mode": "rsi",
                "rsiPeriod": 2,
                "lower": 30,
                "upper": 70,
                "rsiSignalScope": "single",
                "rsiTriggerTicker": "TQQQ",
            },
        }
    )

    result = service.run(request)
    equity_by_date = {point.date: point.value for point in result.equityCurve}

    split_day_changes = [
        equity_by_date[date(2021, 1, 21)] / equity_by_date[date(2021, 1, 20)] - 1,
        equity_by_date[date(2022, 1, 13)] / equity_by_date[date(2022, 1, 12)] - 1,
        equity_by_date[date(2025, 11, 20)] / equity_by_date[date(2025, 11, 19)] - 1,
    ]

    for change in split_day_changes:
        assert abs(change) < 0.06


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
    assert result.summary.xirrPct == pytest.approx(10.0, abs=0.05)
    assert result.summary.mddPct == pytest.approx(0.0, abs=1e-6)


def test_real_equity_curve_starts_equal_and_stays_below_nominal_curve() -> None:
    prices = make_prices()
    service = BacktestService(FakeProvider(prices))

    result = service.run(make_request())

    assert result.realEquityCurve[0].value == pytest.approx(result.equityCurve[0].value, rel=1e-6)
    assert len(result.realEquityCurve) == len(result.equityCurve)
    for nominal_point, real_point in zip(result.equityCurve, result.realEquityCurve):
        assert real_point.date == nominal_point.date
        assert real_point.value <= nominal_point.value + 1e-6


def test_real_value_and_return_match_expected_inflation_adjustment() -> None:
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
    elapsed_years = (date(2025, 1, 2) - date(2024, 1, 2)).days / 365.25
    expected_real_final = 1100 / ((1 + service.INFLATION_RATE) ** elapsed_years)
    expected_real_return = ((expected_real_final / 1000) - 1) * 100

    assert result.summary.inflationRatePct == pytest.approx(3.0, rel=1e-6)
    assert result.summary.realFinalValue == pytest.approx(expected_real_final, abs=1e-4)
    assert result.summary.realTotalReturnPct == pytest.approx(expected_real_return, abs=1e-4)


def test_real_total_return_uses_inflation_adjusted_contributions() -> None:
    index = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-03-01", "2024-03-04"])
    prices = pd.DataFrame({"AAA": [100, 100, 100, 100], "BBB": [100, 100, 100, 100]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "monthlyContribution": 100,
            "period": {"startDate": "2024-01-31", "endDate": "2024-03-04"},
            "rebalance": {"mode": "calendar", "frequency": "yearly"},
        }
    )

    result = service.run(request)
    base_date = date(2024, 1, 31)
    real_total_contributed = 1000
    for contribution_date in [date(2024, 2, 1), date(2024, 3, 1)]:
        elapsed_years = (contribution_date - base_date).days / 365.25
        real_total_contributed += 100 / ((1 + service.INFLATION_RATE) ** elapsed_years)
    expected_real_return = ((result.summary.realFinalValue / real_total_contributed) - 1) * 100

    assert result.summary.totalContributed == pytest.approx(1200.0, rel=1e-6)
    assert result.summary.realTotalReturnPct == pytest.approx(expected_real_return, abs=1e-4)


def test_xirr_reflects_staggered_monthly_cash_flows() -> None:
    index = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-03-01", "2025-03-03"])
    prices = pd.DataFrame({"AAA": [100, 100, 100, 120], "BBB": [100, 100, 100, 120]}, index=index, dtype=float)
    service = BacktestService(FakeProvider(prices))
    request = BacktestRequest.model_validate(
        {
            "positions": [{"ticker": "AAA", "targetWeight": 50}, {"ticker": "BBB", "targetWeight": 50}],
            "initialCapital": 1000,
            "monthlyContribution": 100,
            "period": {"startDate": "2024-01-31", "endDate": "2025-03-03"},
            "rebalance": {"mode": "calendar", "frequency": "yearly"},
        }
    )

    result = service.run(request)

    assert result.summary.totalContributed == pytest.approx(1300.0, rel=1e-6)
    assert result.summary.finalValue == pytest.approx(1540.0, rel=1e-6)
    assert result.summary.cagrPct is None
    assert result.summary.xirrPct == pytest.approx(18.3922, abs=0.05)
