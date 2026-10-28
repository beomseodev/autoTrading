from datetime import date, datetime, timezone

import pytest
from app.services.alert_data import DataPending, adjusted_bars, parse_snapshot, expected_session, fingerprint

META = {"providerSymbol": "AAPL", "mic": "XNAS", "currency": "USD", "timezone": "America/New_York", "calendar": "XNYS"}
PROVIDER_META = {"symbol": "AAPL", "mic_code": "XNAS", "currency": "USD", "exchange_timezone": "America/New_York"}


def payload():
    return {"meta": PROVIDER_META, "values": [
        {"datetime": "2026-10-21", "open": "100", "high": "100", "low": "100", "close": "100", "volume": "10"},
        {"datetime": "2026-10-22", "open": "25", "high": "25", "low": "25", "close": "25", "volume": "40"}]}


def test_split_volume_and_no_future_actions():
    splits = {"meta": PROVIDER_META, "splits": [{"date": "2026-10-22", "ratio": .25}]}
    snapshot = parse_snapshot(META, payload(), splits, date(2026, 10, 22))
    adjusted = adjusted_bars(snapshot, "2026-10-22")
    assert adjusted[0]["close"] == adjusted[1]["close"] == 25
    assert adjusted[0]["volume"] == adjusted[1]["volume"] == 40
    assert adjusted_bars(snapshot, "2026-10-21")[0]["close"] == 100
    assert fingerprint(snapshot) == fingerprint(snapshot)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nonfinite", "metadata", "split"])
def test_reject_bad_provider_data(mutation):
    prices = payload(); splits = {"meta": PROVIDER_META, "splits": []}
    if mutation == "missing": prices["values"].pop()
    if mutation == "duplicate": prices["values"].append(prices["values"][0])
    if mutation == "nonfinite": prices["values"][0]["close"] = "NaN"
    if mutation == "metadata": prices["meta"] = {**PROVIDER_META, "currency": "KRW"}
    if mutation == "split": splits["splits"] = [{"date": "2026-10-22", "ratio": 0}]
    with pytest.raises(DataPending):
        parse_snapshot(META, prices, splits, date(2026, 10, 22))


def test_us_dst_and_weekend_cutoff():
    assert expected_session(META, datetime(2026, 3, 9, 23, tzinfo=timezone.utc)) == date(2026, 3, 9)
    assert expected_session(META, datetime(2026, 11, 2, 21, tzinfo=timezone.utc)) == date(2026, 10, 30)
    assert expected_session(META, datetime(2026, 10, 25, 23, tzinfo=timezone.utc)) == date(2026, 10, 23)


def test_confirmed_halt_does_not_invent_a_bar():
    prices = payload()
    prices["values"][0]["datetime"] = "2026-10-20"
    splits = {"meta": PROVIDER_META, "splits": []}
    with pytest.raises(DataPending, match="data_gap"):
        parse_snapshot(META, prices, splits, date(2026, 10, 22))
    snap = parse_snapshot(META, prices, splits, date(2026, 10, 22), ["2026-10-21"])
    assert len(snap["raw"]) == 2


def test_canonical_share_class_mapping(monkeypatch):
    from app.services import alert_data
    requests=[]
    def search(path, params):
        requests.append(params)
        return {"data":[{"symbol":"BRK.B","mic_code":"XNYS","currency":"USD","country":"United States",
                         "exchange_timezone":"America/New_York","instrument_type":"Common Stock","instrument_name":"Berkshire"}]}
    monkeypatch.setattr(alert_data,"provider_get",search)
    assert alert_data.resolve_instrument("BRK-B")["providerSymbol"] == "BRK.B"
    assert alert_data.resolve_instrument("BRK.B")["symbol"] == "BRK-B"


@pytest.mark.parametrize("symbol", ["QQQ", "QQQM"])
def test_nasdaq_global_market_etf(monkeypatch, symbol):
    from app.services import alert_data
    item = {"symbol": symbol, "mic_code": "XNMS", "currency": "USD", "country": "United States",
            "exchange_timezone": "America/New_York", "instrument_type": "ETF", "instrument_name": symbol}
    monkeypatch.setattr(alert_data, "provider_get", lambda *_: {"data": [item, {**item, "mic_code": "IEXG"}]})
    assert alert_data.resolve_instrument(symbol)["mic"] == "XNMS"


def test_provider_malformed_response_is_deferred(monkeypatch):
    import httpx
    from app.services import alert_data
    monkeypatch.setenv("TWELVE_DATA_API_KEY","fixture-key")
    monkeypatch.setattr(alert_data,"reserve_credits",lambda *_:None)
    monkeypatch.setattr(alert_data.httpx,"get",lambda *args,**kwargs:httpx.Response(200,json=[],request=httpx.Request("GET","https://example.com")))
    with pytest.raises(DataPending,match="invalid_provider_data"):
        alert_data.provider_get("time_series",{})
