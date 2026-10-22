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


def test_private_routes_require_owner():
    client = TestClient(app)
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    for path in ("/api/toss/holdings", "/api/dashboard/markets"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    client.cookies.set("portfolio_session", session_cookie({"sub": "other", "email": "other@gmail.com"}))
    assert client.get("/api/toss/holdings").status_code == 401
    client.cookies.set("portfolio_session", "tampered")
    assert client.get("/api/toss/holdings").status_code == 401


def test_owner_session_logout_and_csrf():
    client = owner_client()
    assert client.get("/auth/session").json()["user"]["sub"] == "google-owner"
    assert client.post("/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/auth/logout", follow_redirects=False).status_code == 303
    assert client.get("/api/toss/holdings").status_code == 401


def test_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("GOOGLE_OWNER_EMAIL")
    client = owner_client()
    assert client.get("/auth/login").status_code == 503
    assert client.get("/api/toss/holdings").status_code == 401


@pytest.mark.parametrize("claims,expected", [
    ({"sub": "google-owner", "email": "owner@gmail.com", "email_verified": True}, 303),
    ({"sub": "other", "email": "other@gmail.com", "email_verified": True}, 403),
    ({"sub": "google-owner", "email": "owner@gmail.com", "email_verified": False}, 403),
])
def test_callback_owner_validation(monkeypatch, claims, expected):
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
    assert response.status_code == 503


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
