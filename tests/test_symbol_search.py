import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.symbol_search import search_symbols


def test_search_maps_korean_and_us_markets_and_filters_unsupported(monkeypatch):
    items = [
        {"code": "005930", "name": "삼성전자", "nationCode": "KOR", "typeCode": "KOSPI"},
        {"code": "247540", "name": "에코프로비엠", "nationCode": "KOR", "typeCode": "KOSDAQ"},
        {"code": "AAPL", "name": "애플", "nationCode": "USA", "typeCode": "NASDAQ"},
        {"code": "BRK.B", "name": "버크셔", "nationCode": "USA", "typeCode": "NYSE"},
        {"code": "2788", "name": "일본 종목", "nationCode": "JPN", "typeCode": "TOKYO"},
        {"code": "bad?", "name": "잘못된 코드", "nationCode": "USA", "typeCode": "NYSE"},
    ]
    items.append(items[0])
    calls = []

    def response(request, **kwargs):
        calls.append(request.full_url)
        assert kwargs["timeout"] == 12
        return BytesIO(json.dumps({"items": items}).encode())

    monkeypatch.setattr("app.services.symbol_search.urlopen", response)
    search_symbols.cache_clear()
    try:
        result = search_symbols("삼성", 1)
        assert [item["ticker"] for item in result] == ["005930.KS", "247540.KQ", "AAPL", "BRK-B"]
        assert [item["currency"] for item in result] == ["KRW", "KRW", "USD", "USD"]
        assert result[0]["name"] == "삼성전자"
        assert search_symbols("삼성", 1) == result
        assert len(calls) == 1
    finally:
        search_symbols.cache_clear()


def test_search_route_validates_query_and_reports_provider_failure(monkeypatch):
    client = TestClient(app)
    for query in ("", "   ", "x" * 81):
        assert client.get("/api/dashboard/symbols", params={"q": query}).status_code == 422
    monkeypatch.setattr("app.main.search_symbols", lambda query, bucket: [])
    assert client.get("/api/dashboard/symbols?q=unknown").json() == {"items": []}

    def unavailable(*args):
        raise OSError("private upstream details")

    monkeypatch.setattr("app.main.search_symbols", unavailable)
    result = client.get("/api/dashboard/symbols?q=삼성")
    assert result.status_code == 502
    assert "private" not in result.text
