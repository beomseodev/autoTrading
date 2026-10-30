from datetime import date

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_dashboard_service
from app.dashboard_schemas import GoalProjectionRequest, PortfolioSnapshotRequest
from app.services.dashboard import DashboardService, macro_observation, parse_fred_csv, quote
from app.services.goals import month_date, project_goal


class FakeDashboard(DashboardService):
    def get_quote(self, symbol):
        if symbol == 'FAIL':
            raise ValueError('missing')
        values = {'KRW=X': (1400, 1300), 'AAPL': (200, 190), 'MSFT': (400, 390)}
        value, previous = values.get(symbol, (100, 99))
        return {'symbol': symbol, 'value': value, 'previous': previous, 'changePct': (value / previous - 1) * 100,
                'asOf': '2026-10-21', 'previousAsOf': '2026-10-20', 'currency': 'KRW' if symbol == 'KRW=X' else 'USD', 'fetchedAt': '2026-10-22T00:00:00Z'}

    def get_macro(self, series):
        if series == 'UNRATE':
            raise ValueError('unavailable')
        return {'value': 3.5, 'change': .1, 'asOf': '2026-09-01', 'fetchedAt': '2026-10-22T00:00:00Z', 'unit': '%'}


def holdings(**changes):
    payload = {'positions': [{'ticker': 'AAPL', 'quantity': 10, 'averageCost': 150}], 'cashKRW': 140000, 'cashUSD': 100, 'baseCurrency': 'KRW'}
    return PortfolioSnapshotRequest.model_validate({**payload, **changes})


def goal(**changes):
    return GoalProjectionRequest.model_validate({'initialAmount': 100, 'targetAmount': 120, 'monthlyContribution': 10, 'annualReturnPct': 0, **changes})


def test_owned_snapshot_converts_quantities_cash_and_unrealized_profit():
    result = FakeDashboard().portfolio_snapshot(holdings())
    assert result['complete']
    assert result['totalValue'] == 3_080_000
    assert result['cashValue'] == 280_000
    assert result['unrealizedProfit'] == 700_000
    assert result['unrealizedProfitPct'] == pytest.approx(100 / 3)
    assert result['holdings'][0]['weightPct'] == pytest.approx(2_800_000 / 3_080_000 * 100)
    assert result['dayChange'] == pytest.approx(340_000)


def test_usd_display_keeps_krw_cash_conversion_correct():
    result = FakeDashboard().portfolio_snapshot(holdings(baseCurrency='USD'))
    assert result['totalValue'] == 2200
    assert result['unrealizedProfit'] == 500
    assert result['cashValue'] == 200


def test_missing_quote_preserves_position_and_blocks_total():
    result = FakeDashboard().portfolio_snapshot(holdings(positions=[{'ticker': 'FAIL', 'quantity': 2, 'averageCost': 100}]))
    assert not result['complete']
    assert result['totalValue'] is None
    assert result['unrealizedProfit'] is None
    assert result['holdings'][0]['ticker'] == 'FAIL'
    assert result['holdings'][0]['weightPct'] is None


def test_missing_fx_blocks_mixed_assets_but_not_single_currency_cash():
    class NoFX(FakeDashboard):
        def get_quote(self, symbol):
            if symbol == 'KRW=X':
                raise ValueError('no fx')
            return super().get_quote(symbol)
    service = NoFX()
    assert not service.portfolio_snapshot(holdings())['complete']
    only_krw = service.portfolio_snapshot(holdings(positions=[], cashKRW=123, cashUSD=0))
    assert only_krw['complete'] and only_krw['totalValue'] == 123
    usd = service.portfolio_snapshot(holdings(baseCurrency='USD', cashKRW=0))
    assert usd['complete'] and usd['totalValue'] == 2100


def test_non_usd_position_is_not_assumed_to_be_usd():
    class NotUSD(FakeDashboard):
        def get_quote(self, symbol):
            item = super().get_quote(symbol)
            if symbol == 'AAPL':
                item['currency'] = 'GBP'
            return item
    assert not NotUSD().portfolio_snapshot(holdings())['complete']


def test_mismatched_dates_do_not_produce_daily_change():
    class OtherDate(FakeDashboard):
        def get_quote(self, symbol):
            item = super().get_quote(symbol)
            if symbol == 'MSFT':
                item['asOf'] = '2026-10-20'
            return item
    result = OtherDate().portfolio_snapshot(holdings(positions=[{'ticker': 'AAPL', 'quantity': 1, 'averageCost': 1}, {'ticker': 'MSFT', 'quantity': 1, 'averageCost': 1}]))
    assert result['complete'] and result['dayChange'] is None


@pytest.mark.parametrize('field', ['asOf', 'previousAsOf'])
@pytest.mark.parametrize('base', ['KRW', 'USD'])
def test_daily_change_requires_matching_fx_period_for_holdings_or_cash(field, base):
    class StaleFX(FakeDashboard):
        def get_quote(self, symbol):
            item = super().get_quote(symbol)
            if symbol == 'KRW=X':
                item[field] = '2026-10-19'
            return item
    result = StaleFX().portfolio_snapshot(holdings(baseCurrency=base))
    assert result['complete'] and result['totalValue'] > 0
    assert result['dayChange'] is None and result['dayChangePct'] is None
    # USD holdings and USD cash do not depend on FX.
    native = StaleFX().portfolio_snapshot(holdings(baseCurrency='USD', cashKRW=0))
    assert native['dayChange'] == 100
    cash = StaleFX().portfolio_snapshot(holdings(positions=[]))
    assert cash['dayChange'] == 10000


@pytest.mark.parametrize('changes', [
    {'cashUSD': 'NaN'}, {'cashKRW': 'Infinity'},
    {'positions': [{'ticker': 'AAPL', 'quantity': -1, 'averageCost': 1}]},
    {'positions': [{'ticker': 'AAPL', 'quantity': 1, 'averageCost': 1}] * 2},
])
def test_invalid_owned_inputs_are_rejected(changes):
    with pytest.raises(ValueError):
        holdings(**changes)


def test_zero_return_monthly_contributions_and_calendar_months():
    result = project_goal(goal(), date(2026, 1, 31))
    base = result.scenarios[1]
    assert base.monthsToGoal == 2
    assert base.reachedDate == date(2026, 3, 31)
    assert month_date(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert month_date(date(2026, 1, 15), 1) == date(2026, 2, 28)


def test_goal_already_reached_and_unreachable_are_explicit():
    start = date(2026, 1, 1)
    reached = project_goal(goal(initialAmount=120), start).scenarios[1]
    assert reached.monthsToGoal == 0 and reached.reachedDate == start
    impossible = project_goal(goal(monthlyContribution=0), start).scenarios[1]
    assert impossible.reachedDate is None and impossible.monthsToGoal is None
    negative = project_goal(goal(monthlyContribution=0, annualReturnPct=-10), start).scenarios[1]
    assert negative.reachedDate is None


@pytest.mark.parametrize('changes', [
    {'annualReturnPct': 'Infinity'}, {'initialAmount': 'NaN'},
    {'monthlyContribution': -1}, {'targetAmount': 0}, {'annualReturnPct': -51},
])
def test_invalid_goal_inputs_are_rejected(changes):
    with pytest.raises(ValueError):
        goal(**changes)


def test_goal_monthly_compounding_matches_one_year_formula():
    result = project_goal(goal(initialAmount=100, targetAmount=112, monthlyContribution=0, annualReturnPct=12), date(2026, 1, 1))
    base = result.scenarios[1]
    one_year = next(p for p in base.points if p.date == date(2027, 1, 31))
    assert one_year.value == pytest.approx(112)
    assert base.monthsToGoal == 12


def test_fred_parse_missing_values_and_yoy_uses_exact_calendar_month(monkeypatch):
    rows = 'observation_date,CPIAUCSL\n2024-01-01,100\n2024-02-01,200\n2025-01-01,110\n2025-02-01,220\n2025-03-01,.\n'
    assert len(parse_fred_csv(rows, 'CPIAUCSL')) == 4
    monkeypatch.setattr('app.services.dashboard.requests.get', lambda *args, **kwargs: httpx.Response(200, content=rows.encode(), request=httpx.Request('GET', 'https://fred.stlouisfed.org/graph/fredgraph.csv')))
    macro_observation.cache_clear()
    item = macro_observation('CPIAUCSL', 1)
    assert item['asOf'] == '2025-02-01'
    assert item['value'] == pytest.approx(10)
    assert item['change'] == pytest.approx(0)
    macro_observation.cache_clear()


def test_quote_rejects_nonfinite_prices_and_cache_is_bounded(monkeypatch):
    class Ticker:
        def history(self, **kwargs):
            return pd.DataFrame({'Close': [100, float('inf')]}, index=pd.date_range('2026-01-01', periods=2))
    monkeypatch.setattr('app.services.dashboard.yf.Ticker', lambda symbol: Ticker())
    quote.cache_clear()
    with pytest.raises(ValueError):
        quote('AAA', 1)
    assert quote.cache_info().maxsize == 256


def test_market_partial_failure_does_not_invent_macro_value():
    result = FakeDashboard().market_overview()
    assert len(result['markets']) == 4
    failed = next(item for item in result['macro'] if item['id'] == 'UNRATE')
    assert not failed['available'] and 'value' not in failed


def test_fred_transport_failures_are_visible_and_not_cached(monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise TimeoutError('upstream read timeout')
    monkeypatch.setattr('app.services.dashboard.requests.get', unavailable)
    monkeypatch.setattr(DashboardService, 'get_quote', lambda self, symbol: {})
    macro_observation.cache_clear()
    result = DashboardService().market_overview()
    assert all(not item['available'] and 'value' not in item for item in result['macro'])
    assert macro_observation.cache_info().currsize == 0
    assert all(f'FRED observation failed: {series}' in caplog.text for series in ['DFF','DGS10','CPIAUCSL','UNRATE'])


@pytest.mark.parametrize('ticker,currency,name', [('005930.KS','KRW','삼성전자'), ('AAPL','USD','애플')])
def test_holding_preserves_resolved_stock_name(ticker, currency, name):
    class Named(FakeDashboard):
        def get_quote(self, symbol):
            result = super().get_quote(symbol)
            if symbol == ticker:
                result.update(name=name, currency=currency)
            return result
    result = Named().portfolio_snapshot(holdings(positions=[{'ticker':ticker,'quantity':1,'averageCost':50}]))
    assert result['holdings'][0]['ticker'] == ticker
    assert result['holdings'][0]['name'] == name


def test_quote_includes_name_and_caches_lookup(monkeypatch):
    monkeypatch.setattr('app.services.dashboard.quote_date_problem', lambda *args: None)
    class Ticker:
        def history(self, **kwargs):
            return pd.DataFrame({'Close':[100,110]}, index=pd.date_range('2026-10-20',periods=2))
        def get_history_metadata(self):
            return {'currency':'USD'}
    calls = []
    monkeypatch.setattr('app.services.dashboard.yf.Ticker', lambda symbol: Ticker())
    monkeypatch.setattr('app.services.dashboard.stock_name', lambda symbol,bucket: calls.append(symbol) or '애플')
    quote.cache_clear()
    try:
        assert quote('AAPL',1)['name'] == '애플'
        assert quote('AAPL',1)['name'] == '애플'
        quote('KRW=X',1)
        assert calls == ['AAPL']
    finally:
        quote.cache_clear()


def test_dashboard_routes_and_lab_are_independent():
    app.dependency_overrides[get_dashboard_service] = lambda: FakeDashboard()
    try:
        client = TestClient(app)
        assert '/static/dashboard.js' in client.get('/').text
        assert '/static/app.js' in client.get('/lab').text
        assert client.get('/api/dashboard/markets').status_code == 200
        result = client.post('/api/dashboard/portfolio', json=holdings().model_dump())
        assert result.status_code == 200 and result.json()['complete']
        result = client.post('/api/dashboard/goal', json=goal().model_dump())
        assert result.status_code == 200 and len(result.json()['scenarios']) == 3
    finally:
        app.dependency_overrides.clear()


def test_guest_can_search_value_holdings_and_project_goals_without_creating_session(monkeypatch):
    monkeypatch.setattr('app.main.search_symbols', lambda query, bucket: [{'ticker': 'AAPL', 'name': 'Apple'}])
    app.dependency_overrides[get_dashboard_service] = lambda: FakeDashboard()
    try:
        client = TestClient(app)
        client.cookies.clear()
        assert client.get('/').status_code == 200
        assert client.get('/api/dashboard/markets').status_code == 200
        assert client.get('/api/dashboard/symbols?q=Apple').json()['items'][0]['ticker'] == 'AAPL'
        result = client.post('/api/dashboard/portfolio', json=holdings().model_dump())
        assert result.status_code == 200 and result.json()['complete']
        assert 'set-cookie' not in result.headers
        result = client.post('/api/dashboard/goal', json=goal().model_dump())
        assert result.status_code == 200 and len(result.json()['scenarios']) == 3
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("base, expected, profit, daily", [
    ("KRW", 2_940_000, 728_000, 340_000),
    ("USD", 2100, 520, 2100 - (1900 + 130000 / 1300)),
])
def test_korean_and_us_positions_use_their_own_currency(base, expected, profit, daily):
    class Korean(FakeDashboard):
        def get_quote(self, symbol):
            item = super().get_quote(symbol)
            if symbol.endswith((".KS", ".KQ")):
                item.update(value=70000, previous=65000, currency="KRW")
            return item
    result = Korean().portfolio_snapshot(holdings(
        positions=[{"ticker": "AAPL", "quantity": 10, "averageCost": 150},
                   {"ticker": "005930.ks", "quantity": 1, "averageCost": 56000},
                   {"ticker": "247540.KQ", "quantity": 1, "averageCost": 56000}],
        cashKRW=0, cashUSD=0, baseCurrency=base))
    assert result["complete"]
    assert result["totalValue"] == pytest.approx(expected)
    assert result["unrealizedProfit"] == pytest.approx(profit)
    assert result["dayChange"] == pytest.approx(daily)
    assert result["holdings"][1]["currency"] == "KRW"
    assert result["holdings"][1]["ticker"] == "005930.KS"


def test_korean_only_valuation_does_not_require_fx():
    class Korean(DashboardService):
        def get_quote(self, symbol):
            if symbol == "KRW=X":
                raise ValueError("missing fx")
            return {"value": 70000, "previous": 65000, "currency": "KRW",
                    "asOf": "2026-10-21", "previousAsOf": "2026-10-20"}
    request = holdings(positions=[{"ticker": "005930.KS", "quantity": 2, "averageCost": 60000}], cashKRW=100, cashUSD=0)
    result = Korean().portfolio_snapshot(request)
    assert result["complete"] and result["totalValue"] == 140100
    assert result["unrealizedProfit"] == 20000
    assert result["dayChange"] == 10000
    assert not Korean().portfolio_snapshot(request.model_copy(update={"baseCurrency": "USD"}))["complete"]


@pytest.mark.parametrize("ticker", ["005930", "00593.KS", "005930.L", "1234567.KQ"])
def test_ambiguous_korean_tickers_are_rejected(ticker):
    with pytest.raises(ValueError):
        holdings(positions=[{"ticker": ticker, "quantity": 1, "averageCost": 1}])
