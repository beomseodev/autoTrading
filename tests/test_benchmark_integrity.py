from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_backtest_service
from app.schemas import BacktestRequest, RiskProfileRequest
from app.services.backtest import BacktestService
from app.services.data_provider import DataProviderError, MarketData, YFinanceDataProvider


def request_data():
    return {
        "positions": [{"ticker": "AAA", "targetWeight": 100}],
        "initialCapital": 1000,
        "monthlyContribution": 1000,
        "period": {"startDate": "2024-01-31", "endDate": "2024-02-02"},
        "rebalance": {"mode": "calendar", "frequency": "yearly"},
    }


class Provider:
    def fetch_market_data(self, tickers, start_date, end_date):
        dates = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
        prices = pd.DataFrame({ticker: [100, 50, 50] for ticker in tickers}, index=dates)
        if "LATE" in tickers:
            prices = prices.iloc[1:]
        return MarketData(prices, prices * 0)


def test_contribution_does_not_hide_drawdown():
    result = BacktestService(Provider()).run(BacktestRequest.model_validate(request_data()))
    assert result.summary.finalValue == 1500
    assert result.summary.totalContributed == 2000
    assert result.summary.mddPct == pytest.approx(-50)


def test_comparison_rejects_different_actual_trading_dates():
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(Provider())
    try:
        response = TestClient(app).post('/api/backtests/compare', json={"runs": [
            {"label": "Original", "request": request_data()},
            {"label": "Late listing", "request": {**request_data(), "positions": [{"ticker": "LATE", "targetWeight": 100}]}},
        ]})
        assert response.status_code == 400
        assert 'Late listing' in response.json()['detail']
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize('value', ['Infinity', 'NaN', '-Infinity'])
def test_api_rejects_nonfinite_money(value):
    response = TestClient(app).post('/api/backtests', json={**request_data(), 'initialCapital': value})
    assert response.status_code == 422


def test_nonfinite_risk_weight_is_rejected():
    with pytest.raises(ValueError):
        RiskProfileRequest.model_validate({'tickers': ['AAA', 'BBB'], 'weightsPct': ['NaN', 50], 'period': request_data()['period']})


def test_missing_price_day_is_not_silently_dropped_with_dividend(monkeypatch):
    dates = pd.date_range('2024-01-01', periods=3)
    prices = pd.DataFrame({'AAA': [100, 99, 99], 'BBB': [100, float('nan'), 100]}, index=dates)
    dividends = pd.DataFrame({'AAA': [0, 1, 0], 'BBB': [0, 0, 0]}, index=dates)
    frame = pd.concat({'Close': prices, 'Dividends': dividends}, axis=1)
    monkeypatch.setattr('app.services.data_provider.yf.download', lambda **kwargs: frame)
    with pytest.raises(DataProviderError, match='누락'):
        YFinanceDataProvider().fetch_market_data(['AAA', 'BBB'], date(2024, 1, 1), date(2024, 1, 3))


def test_edge_alignment_preserves_dividends(monkeypatch):
    dates = pd.date_range('2024-01-01', periods=3)
    prices = pd.DataFrame({'AAA': [100, 99, 99], 'BBB': [float('nan'), 100, 100]}, index=dates)
    dividends = pd.DataFrame({'AAA': [0, 1, 0], 'BBB': [0, 0, 0]}, index=dates)
    monkeypatch.setattr('app.services.data_provider.yf.download', lambda **kwargs: pd.concat({'Close': prices, 'Dividends': dividends}, axis=1))
    data = YFinanceDataProvider().fetch_market_data(['AAA', 'BBB'], date(2024, 1, 1), date(2024, 1, 3))
    assert len(data.prices) == 2
    assert data.dividends['AAA'].sum() == 1


def test_integer_shares_with_share_deducting_fee_is_rejected():
    payload = {**request_data(), 'positions': [{'ticker': 'AAA', 'targetWeight': 100, 'annualExpenseRatio': .001}], 'execution': {'fractionalShares': False}}
    with pytest.raises(ValueError):
        BacktestRequest.model_validate(payload)


def test_guard_cannot_restore_risk_above_its_limit():
    payload = {**request_data(), 'positions': [{'ticker': 'AAA', 'targetWeight': 80}, {'ticker': 'BBB', 'targetWeight': 20}],
        'rebalance': {'mode': 'etf_guard', 'guardRiskTicker': 'AAA', 'guardCoreTicker': 'BBB', 'guardDefensiveTicker': 'CCC', 'guardRiskMaxWeightPct': 60}}
    with pytest.raises(ValueError):
        BacktestRequest.model_validate(payload)
