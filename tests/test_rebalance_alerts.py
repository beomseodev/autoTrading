from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dashboard_schemas import PortfolioSnapshotRequest, SavedDashboard
from app.main import app
from app.services.rebalance_alerts import evaluate_rebalance
from app.services import notification_jobs as jobs
from conftest import session_cookie


NOW = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_rebalance_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or timezone.utc)
    monkeypatch.setattr('app.services.rebalance_alerts.datetime', Clock)


def test_accounts_cannot_read_or_overwrite_each_others_state(memory_store):
    store, records = memory_store
    owner = TestClient(app)
    state = SavedDashboard(portfolio=portfolio(emailAlerts=False)).model_dump(mode='json')
    owner_revision = owner.post('/api/dashboard/state', json={'state': state}).json()['revision']
    records['notifications/status.json'] = {'message': 'owner-only sentinel'}
    clients = []
    for sub in ('second-user', 'third-user'):
        client = TestClient(app)
        client.cookies.set('portfolio_session', session_cookie({'sub': sub, 'email': sub + '@gmail.com'}))
        result = client.get('/api/dashboard/state').json()
        assert result['state'] is None and result['notificationStatus'] is None and not result['emailReady']
        # Knowing the owner's revision never grants access to the owner's path.
        assert client.post('/api/dashboard/state', json={'state': state, 'revision': owner_revision}).status_code == 409
        saved = deepcopy(state)
        saved['portfolio']['cashUSD'] = len(clients) + 123
        assert client.post('/api/dashboard/state', json={'state': saved}).status_code == 200
        clients.append(client)
    assert owner.get('/api/dashboard/state').json()['state'] == state
    assert clients[0].get('/api/dashboard/state').json()['state']['portfolio']['cashUSD'] == 123
    assert clients[1].get('/api/dashboard/state').json()['state']['portfolio']['cashUSD'] == 124
    assert len([name for name in records if name.startswith('users/')]) == 2
    assert all('second-user' not in name for name in records)


def test_nonowner_cannot_trigger_owner_email_or_enable_alerts(memory_store, monkeypatch):
    def should_not_send(*args, **kwargs):
        pytest.fail('Nonowner reached owner email job')
    monkeypatch.setattr('app.dashboard_state.run_notification', should_not_send)
    client = TestClient(app)
    client.cookies.set('portfolio_session', session_cookie({'sub': 'other-user', 'email': 'other@gmail.com'}))
    assert client.post('/api/dashboard/notifications/test', json={}).status_code == 403
    state = SavedDashboard(portfolio=portfolio()).model_dump(mode='json')
    assert client.post('/api/dashboard/state', json={'state': state}).status_code == 403


def portfolio(**updates):
    value = {"positions": [{"ticker": "AAA", "quantity": 1, "averageCost": 50, "targetWeight": 50},
                           {"ticker": "BBB", "quantity": 1, "averageCost": 50, "targetWeight": 50}],
             "baseCurrency": "USD", "emailAlerts": True}
    return PortfolioSnapshotRequest(**(value | updates))


def snapshot(**updates):
    observed = NOW.date().isoformat()
    return {"complete": True, "totalValue": 100, "cashValue": 0, "fx": None,
            "holdings": [{"ticker": "AAA", "value": 80, "asOf": observed, "currency": "USD"},
                         {"ticker": "BBB", "value": 20, "asOf": observed, "currency": "USD"}]} | updates


def test_drift_and_cash_target_are_percentage_points():
    value = snapshot(holdings=[dict(snapshot()["holdings"][0], value=70), dict(snapshot()["holdings"][1], value=20)], cashValue=10)
    request = portfolio(positions=[{"ticker": "AAA", "quantity": 1, "averageCost": 10, "targetWeight": 60},
                                   {"ticker": "BBB", "quantity": 1, "averageCost": 10, "targetWeight": 20}])
    result = evaluate_rebalance(request, value)
    assert result["status"] == "rebalance_needed"
    assert result["items"][0]["driftPct"] == 10
    assert result["items"][0]["adjustmentValue"] == -10
    assert result["items"][-1]["targetWeightPct"] == 20
    assert sum(item["adjustmentValue"] for item in result["items"]) == pytest.approx(0)


def grouped_portfolio(**updates):
    return portfolio(positions=[{"ticker": ticker, "quantity": 1, "averageCost": 0} for ticker in ("AAA", "BBB")],
                     rebalanceGroups=[{"name": "배당 ETF", "tickers": ["AAA", "BBB"], "targetWeight": 60}], **updates)


def test_group_uses_combined_value_and_preserves_cash_and_individual_targets():
    request = grouped_portfolio()
    value = snapshot(cashValue=40, holdings=[dict(snapshot()["holdings"][0], value=50), dict(snapshot()["holdings"][1], value=10)])
    result = evaluate_rebalance(request, value)
    assert result['status'] == 'balanced'
    assert len(result['items']) == 2
    group, cash = result['items']
    assert group['ticker'] == 'AAA + BBB' and '배당 ETF' in group['name']
    assert group['weightPct'] == group['targetWeightPct'] == 60
    assert cash['targetWeightPct'] == 40
    # Shifting value within the group must not trigger a rebalance.
    value['holdings'][0]['value'], value['holdings'][1]['value'] = 5, 55
    assert evaluate_rebalance(request, value)['status'] == 'balanced'
    request.positions.append(type(request.positions[0])(ticker='CCC', quantity=1, averageCost=0, targetWeight=20))
    value['holdings'].append(dict(value['holdings'][0], ticker='CCC', value=10))
    value['cashValue'] = 30
    mixed = evaluate_rebalance(request, value)
    assert mixed['status'] == 'rebalance_needed'
    assert mixed['items'][0]['ticker'] == 'CCC'
    assert mixed['items'][-1]['targetWeightPct'] == 20
    assert sum(row['adjustmentValue'] for row in mixed['items']) == pytest.approx(0)
    assert not evaluate_rebalance(request, value | {'complete':False})['ready']


@pytest.mark.parametrize('groups', [
    [{'name':'x','tickers':['AAA'],'targetWeight':60}],
    [{'name':'x','tickers':['AAA','AAA'],'targetWeight':60}],
    [{'name':'x','tickers':['AAA','CCC'],'targetWeight':60}],
    [{'name':'x','tickers':['AAA','BBB'],'targetWeight':60}] * 2,
    [{'name':' ','tickers':['AAA','BBB'],'targetWeight':60}],
    [{'name':'x','tickers':['AAA','BBB'],'targetWeight':101}],
    [{'name':'x','tickers':['AAA','BBB'],'targetWeight':float('nan')}],
])
def test_invalid_groups_rejected(groups):
    with pytest.raises(ValueError):
        portfolio(positions=[{'ticker':ticker,'quantity':1,'averageCost':0} for ticker in ('AAA','BBB')], rebalanceGroups=groups)


def test_group_member_cannot_also_have_individual_target():
    with pytest.raises(ValueError):
        portfolio(rebalanceGroups=[{'name':'x','tickers':['AAA','BBB'],'targetWeight':60}])


def test_group_state_roundtrip(memory_store):
    client = TestClient(app)
    state = SavedDashboard(portfolio=grouped_portfolio(emailAlerts=False)).model_dump(mode='json')
    assert client.post('/api/dashboard/state', json={'state':state}).status_code == 200
    assert client.get('/api/dashboard/state').json()['state'] == state


def test_rebalance_items_keep_names_and_ticker_identifiers():
    value = snapshot()
    value['holdings'][0]['name'] = '삼성전자'
    result = evaluate_rebalance(portfolio(), value)
    assert result['items'][0]['ticker'] == 'AAA'
    assert result['items'][0]['name'] == '삼성전자'
    assert result['items'][-1]['name'] == '현금'


def test_email_preserves_stock_names_and_codes(memory_store, monkeypatch):
    store, _ = memory_store
    store.write('dashboard.json', SavedDashboard(portfolio=portfolio()).model_dump(mode='json'))
    value = snapshot()
    value['holdings'][0]['name'] = '애플'
    monkeypatch.setattr(jobs.DashboardService, 'portfolio_snapshot', lambda self, request:
                        value | {'rebalance': evaluate_rebalance(request, value)})
    sent = []
    monkeypatch.setattr(jobs, 'send_email', lambda subject, body: sent.append(body))
    assert jobs.run_notification()['outcome'] == 'sent'
    assert '애플 (AAA)' in sent[0]
    assert '종목별 시세와 환율 기준일은 대시보드에서 확인' in sent[0]


def test_consolidated_rebalance_email_explains_mixed_price_basis(memory_store, monkeypatch):
    from app.services.alert_jobs import legacy_section
    from app.services.alert_email import make_payload
    store, _ = memory_store
    store.write('dashboard.json', SavedDashboard(portfolio=portfolio()).model_dump(mode='json'))
    monkeypatch.setattr('app.services.portfolio_store.storage_ready', lambda: True)
    monkeypatch.setattr('app.services.portfolio_store.PortfolioStore', type(store))
    monkeypatch.setenv('ALERT_UNSUBSCRIBE_SECRET', 'test-unsubscribe-secret-more-than-32-chars')
    monkeypatch.setenv('ALERT_EMAIL_FROM', 'alerts@example.com')
    check = {'ready': True, 'status': 'rebalance_needed', 'quoteDates': ['2026-10-23', '2026-10-26'],
             'items': [{'ticker': 'AAA', 'weightPct': 80, 'targetWeightPct': 50, 'needsRebalance': True}]}
    monkeypatch.setattr(jobs.DashboardService, 'portfolio_snapshot', lambda self, request: {'rebalance': check})
    user = {'id': 'test', 'email': 'owner@gmail.com', 'unsubscribe_version': 1}
    payload = make_payload(user, NOW.date(), [], legacy=legacy_section(user))
    assert '2026-10-23 / 2026-10-26' in payload['text']
    assert '장중 가격이 포함될 수 있습니다' in payload['text']
    assert '종목별 시세와 환율 기준일은 대시보드에서 확인' in payload['text']


def test_group_email_uses_combined_target_and_sends_once(memory_store, monkeypatch):
    store, _ = memory_store
    store.write('dashboard.json', SavedDashboard(portfolio=grouped_portfolio()).model_dump(mode='json'))
    sent = []
    monkeypatch.setattr(jobs, 'send_email', lambda subject, body: sent.append(body))
    assert jobs.run_notification()['outcome'] == 'sent'
    assert jobs.run_notification()['outcome'] == 'already_attempted'
    assert len(sent) == 1
    assert '배당 ETF (AAA + BBB)' in sent[0]
    assert '현재 100.00% / 목표 60.00%' in sent[0]


def test_group_and_individual_targets_cannot_exceed_total():
    with pytest.raises(ValueError, match='100%'):
        portfolio(positions=[{'ticker': ticker, 'quantity': 1, 'averageCost': 0,
                              'targetWeight': 50 if ticker == 'CCC' else None}
                             for ticker in ('AAA', 'BBB', 'CCC')],
                  rebalanceGroups=[{'name': 'group', 'tickers': ['AAA', 'BBB'], 'targetWeight': 60}])


def test_exact_band_boundary_does_not_trigger():
    value = snapshot(holdings=[dict(snapshot()["holdings"][0], value=55), dict(snapshot()["holdings"][1], value=45)])
    assert evaluate_rebalance(portfolio(), value)["status"] == "balanced"


@pytest.mark.parametrize("value", [snapshot(complete=False), snapshot(totalValue=0), snapshot(totalValue=None)])
def test_incomplete_or_zero_valuation_never_alerts(value):
    assert not evaluate_rebalance(portfolio(), value)["ready"]


def test_prices_older_than_latest_confirmed_session_defer():
    value = snapshot()
    value["holdings"][0]["asOf"] = (NOW.date() - timedelta(days=8)).isoformat()
    assert evaluate_rebalance(portfolio(), value)["status"] == "stale"
    value["holdings"][0]["asOf"] = (NOW.date() - timedelta(days=4)).isoformat()
    assert evaluate_rebalance(portfolio(), value)["status"] == "stale"


def test_single_currency_needs_no_fx_but_cross_currency_does():
    assert evaluate_rebalance(portfolio(cashUSD=10), snapshot())["ready"]
    assert not evaluate_rebalance(portfolio(cashKRW=100), snapshot())["ready"]


def test_mixed_markets_accept_latest_sessions():
    request = portfolio(positions=[{"ticker": "AAA", "quantity": 1, "averageCost": 10, "targetWeight": 50},
                                   {"ticker": "005930.KS", "quantity": 1, "averageCost": 10, "targetWeight": 50}])
    value = snapshot()
    value["fx"] = {"asOf": NOW.date().isoformat()}
    value["holdings"][0]['asOf'] = (NOW.date() - timedelta(days=1)).isoformat()
    value["holdings"][1].update(ticker="005930.KS", currency="KRW", asOf=NOW.date().isoformat())
    assert evaluate_rebalance(request, value)["ready"]


@pytest.mark.parametrize("change", [{"positions": []}, {"rebalanceTolerancePct": 0}, {"rebalanceTolerancePct": float('nan')},
    {"positions": [{"ticker": "AAA", "quantity": 1, "averageCost": 1}]},
    {"positions": [{"ticker": "AAA", "quantity": 1, "averageCost": 1, "targetWeight": 70},
                   {"ticker": "BBB", "quantity": 1, "averageCost": 1, "targetWeight": 40}]}])
def test_invalid_email_configuration_rejected(change):
    with pytest.raises(ValueError):
        portfolio(**change)


@pytest.fixture
def memory_store(monkeypatch):
    records = {}
    revisions = {}
    class Store:
        def read(self, name):
            return deepcopy(records.get(name)), revisions.get(name)
        def write(self, name, value, revision=None, *, create=False):
            if (create and name in records) or (revision is not None and revision != revisions.get(name)):
                raise HTTPException(409, "conflict")
            records[name] = deepcopy(value)
            revisions[name] = str(int(revisions.get(name, '0')) + 1)
            return revisions[name]
    monkeypatch.setattr('app.dashboard_state.PortfolioStore', Store)
    monkeypatch.setattr(jobs, 'PortfolioStore', Store)
    monkeypatch.setattr('app.dashboard_state.storage_ready', lambda: True)
    monkeypatch.setattr('app.dashboard_state.email_ready', lambda: True)
    monkeypatch.setattr(jobs, 'email_ready', lambda: True)
    monkeypatch.setattr(jobs.DashboardService, 'portfolio_snapshot', lambda self, request: snapshot() | {'rebalance': evaluate_rebalance(request, snapshot())})
    return Store(), records


def test_cloud_save_requires_matching_revision_and_preserves_values(memory_store):
    store, records = memory_store
    client = TestClient(app)
    state = SavedDashboard(portfolio=portfolio()).model_dump(mode='json')
    first = client.post('/api/dashboard/state', json={'state': state}).json()['revision']
    assert client.post('/api/dashboard/state', json={'state': state}).status_code == 409
    changed = deepcopy(state)
    changed['portfolio']['cashUSD'] = 100
    assert client.post('/api/dashboard/state', json={'state': changed, 'revision': ''}).status_code == 422
    assert client.post('/api/dashboard/state', json={'state': changed, 'revision': 'stale'}).status_code == 409
    assert records['dashboard.json'] == state
    assert client.post('/api/dashboard/state', json={'state': changed, 'revision': first}).status_code == 200
    assert client.get('/api/dashboard/state').json()['state']['portfolio']['cashUSD'] == 100
    client.cookies.clear()
    assert client.get('/api/dashboard/state').status_code == 401


def test_account_layout_roundtrip_and_invalid_sizes(memory_store):
    client = TestClient(app)
    state = SavedDashboard(portfolio=portfolio(emailAlerts=False)).model_dump(mode='json')
    state['layout'] = {'goal': {'span': 12, 'height': 600, 'order': 4, 'hidden': True}, 'holdings': {'span': 8, 'height': None, 'order': None, 'hidden': False}}
    assert client.post('/api/dashboard/state', json={'state': state}).status_code == 200
    assert client.get('/api/dashboard/state').json()['state']['layout'] == state['layout']
    for layout in ({'unknown': {'span': 4}}, {'goal': {'span': 0}}, {'goal': {'span': 13}}, {'goal': {'span': 4, 'height': 0}}, {'goal': {'span': 4, 'height': 1601}}, {'goal': {'span': 4, 'order': -1}}, {'goal': {'span': 4, 'order': 8}}, {'goal': {'span': 4, 'hidden': 'false'}}):
        invalid = dict(state, layout=layout)
        assert client.post('/api/dashboard/state', json={'state': invalid}).status_code == 422


def test_cron_secret_only_authorizes_exact_cron_route(monkeypatch):
    monkeypatch.setenv('CRON_SECRET', 'test-cron-secret')
    monkeypatch.setattr('app.dashboard_state.run_notification', lambda: {'outcome': 'checked'})
    client = TestClient(app)
    client.cookies.clear()
    assert client.get('/api/cron/rebalance').status_code == 401
    assert client.get('/api/cron/rebalance', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    headers = {'Authorization': 'Bearer test-cron-secret'}
    assert client.get('/api/cron/rebalance', headers=headers).json()['outcome'] == 'checked'
    assert client.get('/api/dashboard/state', headers=headers).status_code == 401
    monkeypatch.delenv('CRON_SECRET')
    assert client.get('/api/cron/rebalance', headers=headers).status_code == 401


def test_one_email_per_quote_date_and_no_email_for_balanced_portfolio(memory_store, monkeypatch):
    store, records = memory_store
    store.write('dashboard.json', SavedDashboard(portfolio=portfolio()).model_dump(mode='json'))
    sent = []
    monkeypatch.setattr(jobs, 'send_email', lambda subject, body: sent.append((subject, body)))
    assert jobs.run_notification()['outcome'] == 'sent'
    assert jobs.run_notification()['outcome'] == 'already_attempted'
    assert len(sent) == 1 and 'AAA' in sent[0][1] and 'BBB' in sent[0][1]
    assert '평가금액' not in sent[0][1]


def test_email_failure_is_visible_and_not_blindly_retried(memory_store, monkeypatch):
    store, records = memory_store
    store.write('dashboard.json', SavedDashboard(portfolio=portfolio()).model_dump(mode='json'))
    calls = []
    def fail(*args):
        calls.append(1)
        raise HTTPException(502, 'SMTP error')
    monkeypatch.setattr(jobs, 'send_email', fail)
    assert jobs.run_notification()['outcome'] == 'send_failed'
    assert jobs.run_notification()['outcome'] == 'already_attempted'
    assert len(calls) == 1


def test_revision_change_during_quote_fetch_defers_mail(memory_store, monkeypatch):
    store, records = memory_store
    state = SavedDashboard(portfolio=portfolio()).model_dump(mode='json')
    store.write('dashboard.json', state)
    def calculate(self, request):
        store.write('dashboard.json', state)
        return snapshot() | {'rebalance': evaluate_rebalance(request, snapshot())}
    monkeypatch.setattr(jobs.DashboardService, 'portfolio_snapshot', calculate)
    assert jobs.run_notification()['outcome'] == 'deferred'
    assert not any(name.startswith('notifications/alert-') for name in records)


@pytest.mark.parametrize('mode', ['balanced', 'missing', 'disabled'])
def test_no_action_means_no_mail(memory_store, monkeypatch, mode):
    store, records = memory_store
    if mode != 'missing':
        request = portfolio(emailAlerts=mode != 'disabled')
        store.write('dashboard.json', SavedDashboard(portfolio=request).model_dump(mode='json'))
    if mode == 'balanced':
        monkeypatch.setattr(jobs.DashboardService, 'portfolio_snapshot', lambda self, request: {'rebalance': {'ready': True, 'status': 'balanced', 'message': 'balanced'}})
    monkeypatch.setattr(jobs, 'send_email', lambda *args: pytest.fail('Email should not be sent'))
    assert jobs.run_notification()['outcome'] in ('balanced', 'no_portfolio', 'disabled')
