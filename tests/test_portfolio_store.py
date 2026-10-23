import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app

from app.services.portfolio_store import PortfolioStore


@pytest.fixture
def blob_store(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_test_secret")
    saved = {"value": {"cashUSD": 100}, "etag": '"r1"', "version": 1}

    def handle(request):
        if request.method == "GET":
            assert request.url.params["cache"] == "0"
            # Compressed private reads weaken the ETag used by conditional writes.
            etag = saved["etag"] if request.headers.get("accept-encoding") == "identity" else "W/" + saved["etag"]
            return httpx.Response(200, json=saved["value"], headers={"etag": etag})
        assert request.headers["x-allow-overwrite"] == "1"
        if request.headers.get("x-if-match") != saved["etag"]:
            return httpx.Response(412)
        saved["version"] += 1
        saved.update(value=json.loads(request.content), etag=f'"r{saved["version"]}"')
        return httpx.Response(200, json={"etag": saved["etag"]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr("app.services.portfolio_store.httpx.get", client.get)
        monkeypatch.setattr("app.services.portfolio_store.httpx.put", client.put)
        yield PortfolioStore(), saved


def test_read_revision_supports_edit_and_repeated_save(blob_store):
    store, saved = blob_store
    value, revision = store.read("dashboard.json")
    value["cashUSD"] = 200
    revision = store.write("dashboard.json", value, revision)
    assert saved["value"] == {"cashUSD": 200}
    assert store.read("dashboard.json")[1] == revision
    value["cashUSD"] = 300
    assert store.write("dashboard.json", value, revision) == saved["etag"]
    assert saved["value"] == {"cashUSD": 300}


def test_changed_revision_still_blocks_overwrite(blob_store):
    store, saved = blob_store
    value, revision = store.read("dashboard.json")
    saved.update(value={"cashUSD": 500}, etag='"other-device"')
    with pytest.raises(HTTPException) as error:
        store.write("dashboard.json", value, revision)
    assert error.value.status_code == 409
    assert saved["value"] == {"cashUSD": 500}


def test_api_rejects_empty_revision_before_blob_and_preserves_internal_writes(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_test_secret")
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"etag": '"r1"'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr("app.services.portfolio_store.httpx.put", transport.put)
        client = TestClient(app)
        payload = {"state": {"portfolio": {"cashUSD": 100}}, "revision": ""}
        assert client.post('/api/dashboard/state', json=payload).status_code == 422
        assert not requests
        payload['revision'] = None
        assert client.post('/api/dashboard/state', json=payload).status_code == 200
        assert requests[-1].headers['x-allow-overwrite'] == '0'
        assert 'x-if-match' not in requests[-1].headers
        payload['revision'] = '"r1"'
        assert client.post('/api/dashboard/state', json=payload).status_code == 200
        assert requests[-1].headers['x-if-match'] == '"r1"'
        PortfolioStore().write('notifications/status.json', {'message': 'checked'})
        assert requests[-1].headers['x-allow-overwrite'] == '1'
        assert 'x-if-match' not in requests[-1].headers
