from __future__ import annotations

import os
import secrets
from urllib.parse import urlsplit
import httpx

from authlib.integrations.starlette_client import OAuth
from authlib.common.errors import AuthlibBaseError
from joserfc.errors import JoseError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware


router = APIRouter(prefix="/auth")
oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email", "timeout": 15},
)


def configured() -> bool:
    return all(os.getenv(key) for key in (
        "APP_BASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
        "SESSION_SECRET",
    )) and len(os.getenv("SESSION_SECRET", "")) >= 32


def is_owner(user: dict | None) -> bool:
    return bool(configured() and user and user.get("sub") and
                user.get("email", "").casefold() == os.getenv("GOOGLE_OWNER_EMAIL", "").casefold() and
                (not os.getenv("GOOGLE_OWNER_SUB") or user["sub"] == os.environ["GOOGLE_OWNER_SUB"]))


def is_authenticated(user: dict | None) -> bool:
    return bool(configured() and isinstance(user, dict) and
                isinstance(user.get("sub"), str) and user["sub"] and
                isinstance(user.get("email"), str) and user["email"])


def install_auth(app):
    public_api = {
        ("GET", "/api/dashboard/markets"), ("GET", "/api/dashboard/symbols"),
        ("POST", "/api/dashboard/portfolio"), ("POST", "/api/dashboard/goal"),
        ("POST", "/api/backtests"), ("POST", "/api/backtests/compare"),
        ("POST", "/api/risk-profile"),
    }

    @app.middleware("http")
    async def protect(request: Request, call_next):
        cron = request.url.path in ("/api/cron/rebalance", "/api/cron/stock-alerts")
        signed_callback = (request.method == "POST" and request.url.path == "/api/webhooks/alert-email")
        unsubscribe = request.url.path.startswith("/notifications/unsubscribe/")
        cron_secret = os.getenv("CRON_SECRET", "")
        user = request.session.get("user")
        authenticated = is_authenticated(user)
        private = ((request.url.path.startswith("/api/") and (request.method, request.url.path) not in public_api)
                   or request.url.path in ("/docs", "/redoc", "/openapi.json"))
        if cron and (not cron_secret or not secrets.compare_digest(
                request.headers.get("authorization", "").encode(), ("Bearer " + cron_secret).encode())):
            response = JSONResponse({"detail": "예약 점검 인증이 필요합니다."}, status_code=401)
        elif private and not cron and not signed_callback and not authenticated:
            response = (JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
                        if request.url.path.startswith("/api/") else RedirectResponse("/login", status_code=303))
        elif request.url.path.startswith("/api/") and not cron and not signed_callback and request.headers.get("x-portfolio-account") is not None and request.headers["x-portfolio-account"] != (user["sub"] if authenticated else None):
            response = JSONResponse({"detail": "로그인 계정이 변경되었습니다. 새로고침해주세요."}, status_code=409)
        elif request.method not in ("GET", "HEAD", "OPTIONS") and not signed_callback and not unsubscribe and request.headers.get("origin") != os.getenv("APP_BASE_URL", "").rstrip("/"):
            response = JSONResponse({"detail": "요청 출처를 확인할 수 없습니다."}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # Added last so session decoding runs before the access check.
    app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48),
                       session_cookie="portfolio_session", max_age=8 * 3600, same_site="lax",
                       https_only=not os.getenv("APP_BASE_URL", "").startswith("http://localhost:"))
    app.include_router(router)


@router.get("/session")
def session(request: Request):
    user = request.session.get("user")
    return {"configured": configured(), "user": user if is_authenticated(user) else None,
            "isOwner": is_owner(user)}


@router.get("/login")
async def login(request: Request):
    if not configured():
        raise HTTPException(503, "Google 로그인 설정이 필요합니다. README의 인증 설정을 확인해주세요.")
    base = os.environ["APP_BASE_URL"].rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "localhost"):
        raise HTTPException(503, "운영 환경은 HTTPS가 필요합니다.")
    request.session.clear()
    try:
        return await oauth.google.authorize_redirect(request, base + "/auth/callback", prompt="select_account")
    except httpx.HTTPError:
        raise HTTPException(502, "Google에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.") from None


@router.get("/callback")
async def callback(request: Request):
    if not configured():
        raise HTTPException(503, "Google 로그인 설정이 필요합니다.")
    try:
        token = await oauth.google.authorize_access_token(request)
        claims = token.get("userinfo", {})
        user = {"sub": claims.get("sub"), "email": claims.get("email", "")}
        if claims.get("email_verified") is not True or not is_authenticated(user):
            request.session.clear()
            raise HTTPException(403, "이메일이 인증된 Google 계정으로 로그인해주세요.")
    except (AuthlibBaseError, JoseError, ValueError):
        request.session.clear()
        raise HTTPException(400, "로그인 검증에 실패했습니다. 다시 로그인해주세요.") from None
    except httpx.HTTPError:
        request.session.clear()
        raise HTTPException(502, "Google에 연결하지 못했습니다. 다시 로그인해주세요.") from None
    request.session.clear()
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
