from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import subprocess
import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_dashboard_service
from app.services import dashboard
from app.dashboard_schemas import PortfolioSnapshotRequest


@pytest.fixture
def source(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 30, 1, 30, tzinfo=timezone.utc).astimezone(tz)

    metadata = {'currency': 'USD', 'exchangeTimezoneName': 'America/New_York',
                'regularMarketTime': datetime(2026, 10, 29, 20, tzinfo=timezone.utc).timestamp(),
                'regularMarketPrice': 348.91}
    frame = pd.DataFrame({'Close': [347.1, 343.83, float('nan')]},
                         index=pd.date_range('2026-10-27', periods=3))

    class Ticker:
        def history(self, **kwargs):
            return frame

        def get_history_metadata(self):
            return metadata

    monkeypatch.setattr(dashboard, 'datetime', Clock)
    monkeypatch.setattr(dashboard.yf, 'Ticker', lambda symbol: Ticker())
    monkeypatch.setattr(dashboard, 'stock_name', lambda symbol, bucket: symbol)
    dashboard.quote.cache_clear()
    yield metadata, frame
    dashboard.quote.cache_clear()


def test_missing_latest_daily_close_uses_dated_regular_market_price(source):
    result = dashboard.quote('AAPL', 1)
    assert (result['value'], result['asOf']) == (348.91, '2026-10-29')
    assert (result['previous'], result['previousAsOf']) == (343.83, '2026-10-28')


@pytest.mark.parametrize('changes', [
    {'regularMarketPrice': value} for value in [None, 0, -1, float('nan'), float('inf')]
] + [
    {'regularMarketTime': None}, {'regularMarketTime': 0},
    {'regularMarketTime': datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()},
    {'exchangeTimezoneName': 'invalid'},
])
def test_invalid_metadata_does_not_disguise_stale_daily_price(source, changes):
    metadata, _ = source
    metadata.update(changes)
    with pytest.raises(ValueError, match='기준일'):
        dashboard.quote('AAPL', 1)
    assert dashboard.quote.cache_info().currsize == 0


def test_missing_metadata_and_latest_bar_reports_unavailable(source):
    metadata, _ = source
    metadata.clear()
    result = dashboard.DashboardService().portfolio_snapshot(PortfolioSnapshotRequest(
        positions=[{'ticker': 'AAPL', 'quantity': 1, 'averageCost': 100}], baseCurrency='USD'))
    assert not result['holdings'][0]['available']
    assert not result['complete'] and result['totalValue'] is None


def test_normal_daily_data_survives_missing_optional_metadata(source):
    metadata, frame = source
    metadata.clear()
    frame.loc[frame.index[-1], 'Close'] = 348.5
    result = dashboard.quote('AAPL', 1)
    assert result['value'] == 348.5 and result['previous'] == 343.83


def test_metadata_uses_exchange_date_and_previous_distinct_day(source):
    metadata, frame = source
    metadata.update(currency='KRW', exchangeTimezoneName='Asia/Seoul',
                    regularMarketTime=datetime(2026, 10, 30, 1, tzinfo=timezone.utc).timestamp())
    frame.loc[frame.index[-1], 'Close'] = 345
    result = dashboard.quote('005930.KS', 1)
    assert result['asOf'] == '2026-10-30' and result['previousAsOf'] == '2026-10-29'
    assert result['previous'] == 345


@pytest.mark.parametrize('now', ['2026-10-31T12:00:00+00:00', '2026-11-01T12:00:00+00:00'])
def test_weekend_keeps_friday_price(source, monkeypatch, now):
    metadata, frame = source
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(now).astimezone(tz)
    monkeypatch.setattr(dashboard, 'datetime', Clock)
    metadata['regularMarketTime'] = datetime(2026, 10, 30, 20, tzinfo=timezone.utc).timestamp()
    assert dashboard.quote('AAPL', 1)['asOf'] == '2026-10-30'


def test_manual_refresh_bypasses_only_its_request_and_preserves_cached_time(source):
    metadata, frame = source
    service = dashboard.DashboardService()
    request = PortfolioSnapshotRequest(positions=[{'ticker': 'AAPL', 'quantity': 1, 'averageCost': 100}], baseCurrency='USD')
    first = service.portfolio_snapshot(request)
    metadata['regularMarketPrice'] = 350
    fresh = service.portfolio_snapshot(request, refresh=True)
    cached = service.portfolio_snapshot(request)
    assert fresh['holdings'][0]['price'] == 350
    assert first['holdings'][0]['price'] == cached['holdings'][0]['price'] == 348.91
    assert cached['holdings'][0]['fetchedAt'] == first['holdings'][0]['fetchedAt']
    assert not hasattr(service, '_refresh')


def test_overlapping_refresh_requests_are_isolated():
    barrier = Barrier(2)
    class Service(dashboard.DashboardService):
        def get_quote(self, symbol):
            if symbol == 'KRW=X':
                barrier.wait(timeout=5)
            return {'value': 200 if self._refresh else 100, 'previous': 99,
                    'currency': 'KRW' if symbol == 'KRW=X' else 'USD',
                    'asOf': '2026-10-29', 'previousAsOf': '2026-10-28'}
    service = Service()
    request = PortfolioSnapshotRequest(positions=[{'ticker': 'AAPL', 'quantity': 1, 'averageCost': 1}], baseCurrency='USD')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda refresh: service.portfolio_snapshot(request, refresh=refresh), [True, False]))
    assert [result['holdings'][0]['price'] for result in results] == [200, 100]
    assert not hasattr(service, '_refresh')


def test_concurrent_korean_calendar_initialization_in_fresh_process():
    # A new process exposes the shared holiday-table race hidden by warmed caches.
    result = subprocess.run([sys.executable, '-c', '''
from app.services.alert_data import calendar
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
barrier = Barrier(6)
def load(_):
    barrier.wait(timeout=10)
    return len(calendar('XKRX', 2026).schedule)
with ThreadPoolExecutor(6) as pool:
    assert all(size > 2000 for size in pool.map(load, range(6)))
'''], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_routes_forward_optional_refresh_without_changing_defaults():
    calls = []
    class Service:
        def market_overview(self, *, refresh=False):
            calls.append(refresh)
            return {}
        def portfolio_snapshot(self, request, *, refresh=False):
            calls.append(refresh)
            return {}
    app.dependency_overrides[get_dashboard_service] = Service
    try:
        client = TestClient(app)
        for suffix in ['', '?refresh=true']:
            assert client.get('/api/dashboard/markets' + suffix).status_code == 200
            assert client.post('/api/dashboard/portfolio' + suffix, json={}).status_code == 200
        assert calls == [False, False, True, True]
    finally:
        app.dependency_overrides.clear()
