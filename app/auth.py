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
        "GOOGLE_OWNER_EMAIL", "SESSION_SECRET",
    )) and len(os.getenv("SESSION_SECRET", "")) >= 32


def is_owner(user: dict | None) -> bool:
    return bool(configured() and user and user.get("sub") and
                user.get("email", "").casefold() == os.getenv("GOOGLE_OWNER_EMAIL", "").casefold() and
                (not os.getenv("GOOGLE_OWNER_SUB") or user["sub"] == os.environ["GOOGLE_OWNER_SUB"]))


def install_auth(app):
    @app.middleware("http")
    async def protect(request: Request, call_next):
        private = request.url.path.startswith("/api/") or request.url.path in ("/", "/lab", "/docs", "/redoc", "/openapi.json")
        if private and not is_owner(request.session.get("user")):
            response = (JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
                        if request.url.path.startswith("/api/") else RedirectResponse("/login", status_code=303))
        elif request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") != os.getenv("APP_BASE_URL", "").rstrip("/"):
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
    return {"configured": configured(), "user": user if is_owner(user) else None}


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
        if claims.get("email_verified") is not True or not is_owner(user):
            request.session.clear()
            raise HTTPException(403, "허용된 본인 Google 계정으로 로그인해주세요.")
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
    return RedirectResponse("/login", status_code=303)
