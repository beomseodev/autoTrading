import base64
import json
import os

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

# Set before app import; tests never use developer credentials or a real OAuth client.
os.environ["SESSION_SECRET"] = "test-session-secret-with-at-least-32-characters"
os.environ["GOOGLE_CLIENT_ID"] = "test-client"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["GOOGLE_OWNER_EMAIL"] = "owner@gmail.com"
os.environ["APP_BASE_URL"] = "http://localhost:8000"


def session_cookie(user=None):
    payload = {"user": user or {"sub": "google-owner", "email": "owner@gmail.com"}}
    return TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps(payload).encode())).decode()


@pytest.fixture(autouse=True)
def authenticated_existing_clients(monkeypatch, request):
    if request.module.__name__.endswith("test_auth_toss"):
        return
    original = TestClient.__init__

    def initialize(self, *args, **kwargs):
        kwargs.setdefault("headers", {"Origin": os.environ["APP_BASE_URL"]})
        kwargs.setdefault("cookies", {"portfolio_session": session_cookie()})
        original(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", initialize)
