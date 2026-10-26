"""Rebalance freshness follows each market, not elapsed calendar days."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, current_thread

import pandas as pd
import pytest

from app.dashboard_schemas import PortfolioSnapshotRequest
from app.services import rebalance_alerts as rebalance
from app.services.dashboard import DashboardService, quote


@pytest.fixture
def clock(monkeypatch):
    def freeze(value):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(value).astimezone(tz or timezone.utc)
        monkeypatch.setattr(rebalance, 'datetime', FixedDatetime)
    return freeze


def inputs(us='2026-10-23', kr='2026-10-26', fx='2026-10-26'):
    request = PortfolioSnapshotRequest(baseCurrency='USD', positions=[
        {'ticker': 'AAPL', 'quantity': 1, 'averageCost': 100, 'targetWeight': 50},
        {'ticker': '005930.KS', 'quantity': 1, 'averageCost': 100, 'targetWeight': 50},
    ])
    snapshot = {'complete': True, 'totalValue': 100, 'cashValue': 0,
                'fx': {'asOf': fx}, 'holdings': [
                    {'ticker': 'AAPL', 'currency': 'USD', 'asOf': us, 'value': 80},
                    {'ticker': '005930.KS', 'currency': 'KRW', 'asOf': kr, 'value': 20}]}
    return request, snapshot


@pytest.mark.parametrize('instant,us,kr,fx', [
    ('2026-10-26T03:00:00+00:00', '2026-10-23', '2026-10-26', '2026-10-26'),
    ('2026-10-26T00:00:00+00:00', '2026-10-23', '2026-10-23', '2026-10-23'),
    # Korean Chuseok and National Foundation Day bridge a long closure in 2025.
    ('2025-10-09T03:00:00+00:00', '2025-10-08', '2025-10-02', '2025-10-09'),
    # US Independence Day, while Korea trades normally.
    ('2026-07-06T03:00:00+00:00', '2026-07-02', '2026-07-06', '2026-07-06'),
])
def test_latest_market_sessions_allow_different_dates(clock, instant, us, kr, fx):
    clock(instant)
    result = rebalance.evaluate_rebalance(*inputs(us, kr, fx))
    assert result['ready'], result['message']
    assert result['status'] == 'rebalance_needed'
    assert result['quoteDates'] == sorted({us, kr})


def test_same_market_can_mix_live_and_last_confirmed_prices(clock):
    clock('2026-10-26T15:00:00+00:00')
    request, snapshot = inputs()
    request.positions[1].ticker = 'MSFT'
    snapshot['holdings'][1].update(ticker='MSFT', currency='USD', asOf='2026-10-26')
    assert rebalance.evaluate_rebalance(request, snapshot)['ready']


@pytest.mark.parametrize('field,value,label', [
    ('us', '2026-10-22', 'AAPL'),
    ('kr', '2026-10-22', '005930.KS'),
    ('kr', '2026-10-27', '005930.KS'),
    ('kr', '2026-10-25', '005930.KS'),
    ('kr', None, '005930.KS'),
    ('kr', 'bad-date', '005930.KS'),
    ('fx', '2026-10-22', '환율'),
    ('fx', '2026-10-27', '환율'),
    ('fx', None, '환율'),
])
def test_invalid_or_outdated_inputs_defer_with_specific_reason(clock, field, value, label):
    clock('2026-10-26T03:00:00+00:00')
    result = rebalance.evaluate_rebalance(*inputs(**{field: value}))
    assert result['status'] == 'stale'
    assert not result['ready']
    assert label in result['message']
    assert '기준일' in result['message']
    assert '유효하지 않' in result['message']
    assert '5분 후 새로고침' in result['message']


def test_current_session_before_open_is_not_accepted(clock):
    clock('2026-10-26T12:00:00+00:00')
    result = rebalance.evaluate_rebalance(*inputs(us='2026-10-26'))
    assert not result['ready']


def test_calendar_failure_defers_without_crashing(clock, monkeypatch):
    clock('2026-10-26T03:00:00+00:00')
    monkeypatch.setattr('app.services.alert_data.calendar', lambda *args: (_ for _ in ()).throw(ValueError('unsupported')))
    result = rebalance.evaluate_rebalance(*inputs())
    assert not result['ready']
    assert '거래일' in result['message']


@pytest.mark.parametrize('instant,price,ready', [
    ('2026-10-26T20:30:00+00:00', '2026-10-23', True),
    ('2026-10-26T21:30:00+00:00', '2026-10-23', False),
    ('2026-10-26T21:30:00+00:00', '2026-10-26', True),
    ('2026-10-25T03:00:00+00:00', '2026-10-23', True),
])
def test_confirmed_close_grace_and_weekend(clock, instant, price, ready):
    clock(instant)
    request, snapshot = inputs(us=price, kr='2026-10-23')
    request.positions.pop()
    snapshot['holdings'].pop()
    snapshot['fx'] = None
    assert rebalance.evaluate_rebalance(request, snapshot)['ready'] is ready


def test_invalid_fx_does_not_block_native_currency(clock):
    clock('2026-10-26T03:00:00+00:00')
    request, snapshot = inputs(fx=None)
    request.positions.pop()
    snapshot['holdings'].pop()
    assert rebalance.evaluate_rebalance(request, snapshot)['ready']


def test_snapshot_shares_cache_bucket_without_mutating_service(clock, monkeypatch):
    clock('2026-10-26T03:00:00+00:00')
    ticks = iter([299, 300, 601, 902])
    monkeypatch.setattr('app.services.dashboard.time', lambda: next(ticks))
    calls = []
    def price(symbol, bucket):
        calls.append((symbol, bucket))
        return {'value': 100, 'previous': 99, 'currency': 'KRW' if symbol == 'KRW=X' else 'USD',
                'asOf': '2026-10-23', 'previousAsOf': '2026-10-22'}
    monkeypatch.setattr('app.services.dashboard.quote', price)
    request, _ = inputs()
    request.positions[1].ticker = 'MSFT'
    service = DashboardService()
    assert service.portfolio_snapshot(request)['rebalance']['ready']
    assert {bucket for _, bucket in calls} == {0}
    assert {symbol for symbol, _ in calls} == {'KRW=X', 'AAPL', 'MSFT'}
    assert not hasattr(service, '_quote_bucket')


def test_portfolio_change_reloads_and_checks_mixed_markets(clock):
    clock('2026-10-26T03:00:00+00:00')
    class Prices(DashboardService):
        def get_quote(self, symbol):
            kr = symbol.endswith('.KS')
            return {'value': 1400 if symbol == 'KRW=X' else 100, 'previous': 100,
                    'currency': 'KRW' if kr or symbol == 'KRW=X' else 'USD',
                    'asOf': '2026-10-26' if kr or symbol == 'KRW=X' else '2026-10-23',
                    'previousAsOf': '2026-10-22', 'name': symbol}
    request, _ = inputs()
    service = Prices()
    request.positions.pop()
    assert service.portfolio_snapshot(request)['rebalance']['ready']
    request.positions.append(inputs()[0].positions[1])
    result = service.portfolio_snapshot(request)
    assert result['complete'] and result['rebalance']['ready']
    assert result['rebalance']['quoteDates'] == ['2026-10-23', '2026-10-26']
    assert result['dayChange'] is None


def test_overlapping_snapshots_keep_their_own_cache_buckets(clock, monkeypatch):
    clock('2026-10-26T03:00:00+00:00')
    barrier = Barrier(2)
    calls = []
    monkeypatch.setattr('app.services.dashboard.time',
                        lambda: 299 if current_thread().name == 'snapshot_0' else 301)
    def price(symbol, bucket):
        calls.append((symbol, bucket))
        if symbol == 'KRW=X':
            barrier.wait(timeout=5)
        return {'value': 100, 'previous': 99, 'currency': 'KRW' if symbol == 'KRW=X' else 'USD',
                'asOf': '2026-10-23', 'previousAsOf': '2026-10-22'}
    monkeypatch.setattr('app.services.dashboard.quote', price)
    first, _ = inputs()
    first.positions.pop()
    second = first.model_copy(deep=True)
    second.positions[0].ticker = 'MSFT'
    service = DashboardService()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='snapshot') as pool:
        results = list(pool.map(service.portfolio_snapshot, [first, second]))
    assert all(result['rebalance']['ready'] for result in results)
    assert ('AAPL', 0) in calls and ('MSFT', 1) in calls
    assert {bucket for symbol, bucket in calls if symbol == 'KRW=X'} == {0, 1}
    assert not hasattr(service, '_quote_bucket')


def test_quote_fetches_two_sessions_across_long_holiday(monkeypatch):
    class Ticker:
        def history(self, **kwargs):
            assert kwargs['period'] == '1mo'
            return pd.DataFrame({'Close': [100, 110]}, index=pd.to_datetime(['2025-10-01', '2025-10-02']))
        def get_history_metadata(self):
            return {'currency': 'KRW'}
    monkeypatch.setattr('app.services.dashboard.yf.Ticker', lambda symbol: Ticker())
    monkeypatch.setattr('app.services.dashboard.stock_name', lambda *args: '삼성전자')
    quote.cache_clear()
    try:
        assert quote('005930.KS', 1)['asOf'] == '2025-10-02'
    finally:
        quote.cache_clear()
