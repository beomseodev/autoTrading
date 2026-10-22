import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import auth
from app.services.toss import TossService, TossError
from joserfc.errors import BadSignatureError
from conftest import session_cookie


def owner_client():
    client = TestClient(app, headers={"Origin": "http://localhost:8000"})
    client.cookies.set("portfolio_session", session_cookie(), domain="testserver.local", path="/")
    return client


def test_saved_data_routes_require_login_but_pages_are_public():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/lab").status_code == 200
    for path in ("/api/toss/holdings", "/api/dashboard/state", "/api/dashboard/notifications/test"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    client.cookies.set("portfolio_session", session_cookie({"sub": "other", "email": "other@gmail.com"}))
    assert client.get("/lab").status_code == 200
    assert client.get("/api/toss/holdings").status_code == 404
    client.cookies.set("portfolio_session", "tampered")
    assert client.get("/api/toss/holdings").status_code == 401


def test_guest_cannot_persist_or_send_email(monkeypatch):
    def private_access(*args, **kwargs):
        pytest.fail('Guest reached private storage or mail')
    monkeypatch.setattr('app.dashboard_state.PortfolioStore', private_access)
    monkeypatch.setattr('app.dashboard_state.run_notification', private_access)
    client = TestClient(app, headers={'Origin': 'http://localhost:8000'})
    for path in ('/api/dashboard/state', '/api/dashboard/notifications/test'):
        assert client.post(path, json={}).status_code == 401
    assert client.get('/api/dashboard/state').status_code == 401
    assert client.get('/api/cron/rebalance').status_code == 401
    assert client.get('/api/dashboard/state/', follow_redirects=False).status_code == 401
    assert client.get('/api/unknown-future-endpoint').status_code == 401
    assert client.get('/auth/session').json()['user'] is None


def test_guest_computation_retains_origin_and_stale_account_checks():
    client = TestClient(app)
    assert client.post('/api/dashboard/goal', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    response = client.post('/api/dashboard/goal', json={}, headers={
        'Origin': 'http://localhost:8000', 'X-Portfolio-Account': 'previous-user'})
    assert response.status_code == 409


def test_owner_session_logout_and_csrf():
    client = owner_client()
    assert client.get("/auth/session").json()["user"]["sub"] == "google-owner"
    assert client.post("/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/auth/logout", follow_redirects=False).status_code == 303
    assert client.get("/api/toss/holdings").status_code == 401


def test_configuration_fails_closed(monkeypatch):
    client = owner_client()
    monkeypatch.delenv("SESSION_SECRET")
    assert client.get("/auth/login").status_code == 503
    assert client.get("/api/toss/holdings").status_code == 401


@pytest.mark.parametrize("claims,expected", [
    ({"sub": "google-owner", "email": "owner@gmail.com", "email_verified": True}, 303),
    ({"sub": "other", "email": "other@gmail.com", "email_verified": True}, 303),
    ({"sub": "google-owner", "email": "owner@gmail.com", "email_verified": False}, 403),
])
def test_callback_verified_account_validation(monkeypatch, claims, expected):
    async def exchange(request):
        return {"userinfo": claims, "access_token": "must-not-be-in-cookie"}
    monkeypatch.setattr(auth.oauth.google, "authorize_access_token", exchange)
    client = TestClient(app)
    response = client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == expected
    assert "must-not-be-in-cookie" not in response.headers.get("set-cookie", "")
    assert bool(client.get("/auth/session").json()["user"]) == (expected == 303)


def test_forged_callback_rejected():
    response = TestClient(app).get("/auth/callback?state=forged&code=forged")
    assert response.status_code == 400


def test_invalid_token_signature_rejected(monkeypatch):
    async def exchange(request):
        raise BadSignatureError()
    monkeypatch.setattr(auth.oauth.google, "authorize_access_token", exchange)
    client = TestClient(app)
    assert client.get("/auth/callback").status_code == 400
    assert client.get("/auth/session").json()["user"] is None


def test_login_redirect_has_state_and_nonce(monkeypatch):
    async def metadata():
        return {"authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth"}
    monkeypatch.setattr(auth.oauth.google, "load_server_metadata", metadata)
    response = TestClient(app).get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert "state=" in response.headers["location"]
    assert "nonce=" in response.headers["location"]
    assert "httponly" in response.headers["set-cookie"].lower()


def test_public_login_does_not_require_operator_configuration(monkeypatch):
    monkeypatch.delenv('GOOGLE_OWNER_EMAIL')
    client = owner_client()
    assert client.get('/lab').status_code == 200
    session = client.get('/auth/session').json()
    assert session['configured'] and session['user'] and not session['isOwner']


def test_stale_tab_account_header_prevents_cross_account_save():
    client = owner_client()
    client.cookies.set('portfolio_session', session_cookie({'sub': 'second-user', 'email': 'second@gmail.com'}), domain='testserver.local', path='/')
    response = client.post('/api/dashboard/state', json={}, headers={'X-Portfolio-Account': 'google-owner'})
    assert response.status_code == 409


@pytest.mark.parametrize('claims', [
    {'email': 'user@gmail.com', 'email_verified': True},
    {'sub': '', 'email': 'user@gmail.com', 'email_verified': True},
    {'sub': 'user', 'email': '', 'email_verified': True},
    {'sub': ['bad'], 'email': 'user@gmail.com', 'email_verified': True},
])
def test_callback_requires_valid_identity(monkeypatch, claims):
    async def exchange(request):
        return {'userinfo': claims}
    monkeypatch.setattr(auth.oauth.google, 'authorize_access_token', exchange)
    client = TestClient(app)
    assert client.get('/auth/callback', follow_redirects=False).status_code == 403
    assert client.get('/auth/session').json()['user'] is None


def test_toss_normalization_and_masking(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "test-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "test-secret")
    def respond(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "private-token"})
        assert request.headers["authorization"] == "Bearer private-token"
        if request.url.path == "/api/v1/accounts":
            return httpx.Response(200, json={"result": [{"accountSeq": "42", "accountNo": "private-account"}]})
        assert request.headers["X-Tossinvest-Account"] == "42"
        return httpx.Response(200, json={"result": {"items": [{"symbol": "AAPL", "name": "Apple", "currency": "USD", "quantity": "1.5", "averagePurchasePrice": "100", "marketValue": {"amount": "180"}, "profitLoss": {"amount": "30", "rate": "0.2"}}]}})
    original = httpx.Client
    monkeypatch.setattr("app.services.toss.httpx.Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    result = TossService().holdings()
    assert result["items"][0]["profitLossPct"] == "20.0"
    assert result["items"][0]["quantity"] == "1.5"
    assert "private-" not in json.dumps(result)


def test_toss_missing_credentials(monkeypatch):
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    response = owner_client().get("/api/toss/holdings")
    assert response.status_code == 404


@pytest.mark.parametrize("mode", ["timeout", "429", "malformed", "partial"])
def test_toss_errors_never_return_partial_portfolio(monkeypatch, mode):
    monkeypatch.setenv("TOSS_CLIENT_ID", "test-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "test-secret")
    def respond(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "private-token"})
        if mode == "timeout":
            raise httpx.ReadTimeout("private-details", request=request)
        if mode == "429":
            return httpx.Response(429, text="private-details")
        if mode == "malformed":
            return httpx.Response(200, json={"private-details": 1})
        if request.url.path == "/api/v1/accounts":
            return httpx.Response(200, json={"result": [{"accountSeq": "1"}, {"accountSeq": "2"}]})
        return httpx.Response(200, json={"result": {"items": []}}) if request.headers["X-Tossinvest-Account"] == "1" else httpx.Response(500)
    original = httpx.Client
    monkeypatch.setattr("app.services.toss.httpx.Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    with pytest.raises(TossError) as error:
        TossService().holdings()
    assert "private" not in str(error.value)
