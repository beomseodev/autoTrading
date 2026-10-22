from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.schemas import RiskProfileRequest
from app.services.data_provider import DataProviderError, MarketData
from app.services.risk_profile import RiskProfileService


class FakeProvider:
    def __init__(self, prices: pd.DataFrame, dividends: pd.DataFrame | None = None) -> None:
        self.prices = prices
        self.dividends = dividends if dividends is not None else pd.DataFrame(0.0, index=prices.index, columns=prices.columns)

    def fetch_market_data(self, tickers: list[str], start_date: date, end_date: date) -> MarketData:
        prices = self.prices.loc[:, tickers]
        period_mask = (prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))
        prices = prices.loc[period_mask]
        dividends = self.dividends.loc[prices.index, tickers]
        return MarketData(prices=prices, dividends=dividends)


def request_for(
    start_date: str = "2024-01-02",
    end_date: str = "2024-01-05",
    weights_pct: list[float] | None = None,
) -> RiskProfileRequest:
    return RiskProfileRequest.model_validate(
        {
            "tickers": ["AAA", "BBB"],
            "weightsPct": weights_pct or [50, 50],
            "period": {"startDate": start_date, "endDate": end_date},
        }
    )


def prices_from_returns(returns: dict[str, list[float]]) -> pd.DataFrame:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"])
    values: dict[str, list[float]] = {}
    for ticker, ticker_returns in returns.items():
        series = [100.0]
        for daily_return in ticker_returns:
            series.append(series[-1] * (1.0 + daily_return))
        values[ticker] = series
    return pd.DataFrame(values, index=index, dtype=float)


def test_risk_profile_computes_positive_correlation() -> None:
    prices = prices_from_returns({"AAA": [0.1, 0.2, 0.3, 0.4], "BBB": [0.2, 0.4, 0.6, 0.8]})
    service = RiskProfileService(FakeProvider(prices))

    result = service.run(request_for(end_date="2024-01-08"))

    assert result.correlation == pytest.approx(1.0, abs=1e-6)


def test_risk_profile_computes_negative_correlation() -> None:
    prices = prices_from_returns({"AAA": [0.1, 0.2, 0.3, 0.4], "BBB": [-0.2, -0.4, -0.6, -0.8]})
    service = RiskProfileService(FakeProvider(prices))

    result = service.run(request_for(end_date="2024-01-08"))

    assert result.correlation == pytest.approx(-1.0, abs=1e-6)


def test_risk_profile_computes_near_zero_correlation() -> None:
    prices = prices_from_returns({"AAA": [0.1, -0.1, 0.1, -0.1], "BBB": [0.1, 0.1, -0.1, -0.1]})
    service = RiskProfileService(FakeProvider(prices))

    result = service.run(request_for(end_date="2024-01-08"))

    assert result.correlation == pytest.approx(0.0, abs=1e-6)


def test_risk_profile_metrics_match_expected_values() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-07-02", "2025-01-02"])
    prices = pd.DataFrame(
        {
            "AAA": [100.0, 110.0, 121.0],
            "BBB": [100.0, 90.0, 99.0],
        },
        index=index,
        dtype=float,
    )
    service = RiskProfileService(FakeProvider(prices))

    result = service.run(request_for(end_date="2025-01-02"))
    aaa = next(metric for metric in result.metrics if metric.ticker == "AAA")
    bbb = next(metric for metric in result.metrics if metric.ticker == "BBB")
    elapsed_years = (index[-1] - index[0]).days / 365.25

    assert aaa.totalReturnPct == pytest.approx(21.0, abs=1e-4)
    assert aaa.annualizedReturnPct == pytest.approx(((121 / 100) ** (1 / elapsed_years) - 1) * 100, abs=1e-4)
    assert aaa.annualizedVolatilityPct == pytest.approx(0.0, abs=1e-4)
    assert aaa.mddPct == pytest.approx(0.0, abs=1e-4)
    assert bbb.mddPct == pytest.approx(-10.0, abs=1e-4)
    assert result.normalizedPriceCurve[0].values == {"AAA": 100.0, "BBB": 100.0, "Portfolio": 100.0}


def test_risk_profile_computes_weighted_portfolio_metrics() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    prices = pd.DataFrame(
        {
            "AAA": [100.0, 110.0, 120.0, 130.0],
            "BBB": [100.0, 90.0, 80.0, 70.0],
        },
        index=index,
        dtype=float,
    )
    service = RiskProfileService(FakeProvider(prices))

    result = service.run(request_for(weights_pct=[75, 25]))

    assert result.weightsPct == [75.0, 25.0]
    assert result.portfolioMetric.ticker == "Portfolio 75/25"
    assert result.portfolioMetric.totalReturnPct == pytest.approx(15.0, abs=1e-4)
    assert result.portfolioMetric.mddPct == pytest.approx(0.0, abs=1e-4)
    assert result.normalizedPriceCurve[-1].values["Portfolio"] == pytest.approx(115.0, abs=1e-4)


def test_risk_profile_uses_dividend_adjusted_total_return() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.DataFrame(
        {
            "AAA": [100.0, 100.0, 100.0],
            "BBB": [100.0, 100.0, 100.0],
        },
        index=index,
        dtype=float,
    )
    dividends = pd.DataFrame(
        {
            "AAA": [0.0, 10.0, 0.0],
            "BBB": [0.0, 0.0, 0.0],
        },
        index=index,
        dtype=float,
    )
    service = RiskProfileService(FakeProvider(prices, dividends=dividends))

    result = service.run(request_for(end_date="2024-01-04", weights_pct=[50, 50]))

    aaa = next(metric for metric in result.metrics if metric.ticker == "AAA")
    assert aaa.totalReturnPct == pytest.approx(10.0, abs=1e-4)
    assert result.portfolioMetric.totalReturnPct == pytest.approx(5.0, abs=1e-4)
    assert result.normalizedPriceCurve[-1].values["AAA"] == pytest.approx(110.0, abs=1e-4)
    assert result.normalizedPriceCurve[-1].values["Portfolio"] == pytest.approx(105.0, abs=1e-4)


def test_risk_profile_computes_allocation_sweep_in_10_pct_steps() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    prices = pd.DataFrame(
        {
            "AAA": [100.0, 110.0, 120.0, 130.0],
            "BBB": [100.0, 90.0, 80.0, 70.0],
        },
        index=index,
        dtype=float,
    )
    service = RiskProfileService(FakeProvider(prices))

    result = service.run(request_for(weights_pct=[60, 40]))

    assert len(result.allocationProfiles) == 11
    assert result.allocationProfiles[0].weightsPct == [0.0, 100.0]
    assert result.allocationProfiles[-1].weightsPct == [100.0, 0.0]
    assert [point.weightsPct[0] for point in result.allocationProfiles] == list(range(0, 101, 10))
    assert result.allocationProfiles[6].weightsPct == [60.0, 40.0]
    assert result.allocationProfiles[6].metric.totalReturnPct == pytest.approx(6.0, abs=1e-4)


def test_risk_profile_rejects_duplicate_tickers() -> None:
    with pytest.raises(ValueError, match="Duplicate tickers"):
        RiskProfileRequest.model_validate(
            {
                "tickers": ["AAA", " aaa "],
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-05"},
            }
        )


def test_risk_profile_rejects_weights_that_do_not_sum_to_100() -> None:
    with pytest.raises(ValueError, match="weightsPct must add up to 100"):
        RiskProfileRequest.model_validate(
            {
                "tickers": ["AAA", "BBB"],
                "weightsPct": [60, 30],
                "period": {"startDate": "2024-01-02", "endDate": "2024-01-05"},
            }
        )


def test_risk_profile_requires_enough_common_trading_days() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03"])
    prices = pd.DataFrame({"AAA": [100.0, 101.0], "BBB": [100.0, 99.0]}, index=index, dtype=float)
    service = RiskProfileService(FakeProvider(prices))

    with pytest.raises(DataProviderError, match="At least three common trading days"):
        service.run(request_for(end_date="2024-01-03"))
