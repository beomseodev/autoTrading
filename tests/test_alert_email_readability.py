from datetime import date
from app.services.alert_email import make_payload


def test_technical_email_explains_values_parameters_and_combined_conditions(monkeypatch):
    monkeypatch.setenv('ALERT_UNSUBSCRIBE_SECRET','test-unsubscribe-secret-more-than-32-chars')
    monkeypatch.setenv('ALERT_EMAIL_FROM','alerts@example.com')
    child={'type':'daily_change','params':{'thresholdPct':5,'direction':'both'},
        'result':{'values':{'previousValue':0,'value':7,'threshold':0},'reason':'matched'}}
    event={'session_date':date(2026,10,23),'trigger_kind':'all','payload':{'symbol':'AAPL','currency':'USD','kind':'all_of','label':'같은 날 모든 조건 충족 (AND)',
        'params':{'conditions':[{'type':child['type'],'params':child['params']}]},'result':{'values':{},'conditions':[child]}}}
    email=make_payload({'id':'test','email':'user@example.com','unsubscribe_version':1},date(2026,10,26),[event])['text']
    assert 'thresholdPct' not in email and 'previousValue' not in email and "{'" not in email
    assert '5%' in email and '7%' in email and '기준: 0' not in email
    assert '같은 거래일' in email and '이메일 알림 해지' in email


def test_technical_email_uses_currency_and_trade_session_units(monkeypatch):
    monkeypatch.setenv('ALERT_UNSUBSCRIBE_SECRET','test-unsubscribe-secret-more-than-32-chars')
    monkeypatch.setenv('ALERT_EMAIL_FROM','alerts@example.com')
    event={'session_date':date(2026,10,23),'trigger_kind':'up','payload':{'symbol':'AAPL','currency':'USD','kind':'price_ma_cross','label':'종가와 이동평균 교차',
        'params':{'average':'sma','period':200,'direction':'up'},'result':{'values':{'previousValue':99,'value':101,'previousThreshold':100,'threshold':100}}}}
    email=make_payload({'id':'test','email':'user@example.com','unsubscribe_version':1},date(2026,10,26),[event])['text']
    assert '200거래일' in email and '101 USD' in email and '단순이동평균' in email
    assert 'previousThreshold' not in email and "'direction'" not in email
