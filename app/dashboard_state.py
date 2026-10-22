from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request

from app.auth import is_owner

from app.dashboard_schemas import SavedDashboard, SaveDashboardRequest
from app.services.notification_email import email_ready
from app.services.notification_jobs import run_notification
from app.services.portfolio_store import PortfolioStore, storage_ready


router = APIRouter()


def dashboard_path(request: Request):
    user = request.session["user"]
    # Keep the original owner's path compatible with existing daily email jobs.
    if is_owner(user):
        return "dashboard.json"
    return "users/" + sha256(user["sub"].encode()).hexdigest() + "/dashboard.json"


@router.get("/api/dashboard/state")
def read_state(request: Request):
    owner = is_owner(request.session["user"])
    if not storage_ready():
        return {"configured": False, "state": None, "revision": None, "emailReady": owner and email_ready(), "notificationStatus": None}
    store = PortfolioStore()
    state, revision = store.read(dashboard_path(request))
    status = store.read("notifications/status.json")[0] if owner else None
    if state is not None:
        state = SavedDashboard.model_validate(state).model_dump(mode="json")
    return {"configured": True, "state": state, "revision": revision,
            "emailReady": owner and email_ready(), "notificationStatus": status}


@router.post("/api/dashboard/state")
def save_state(payload: SaveDashboardRequest, request: Request):
    if payload.state.portfolio.emailAlerts and not is_owner(request.session["user"]):
        raise HTTPException(403, "이메일 알림은 현재 운영자 계정에서만 사용할 수 있습니다.")
    if payload.state.portfolio.emailAlerts and not email_ready():
        raise HTTPException(503, "Gmail 발송 설정 후 이메일 알림을 켤 수 있습니다. 설정 전에는 화면에서 점검할 수 있습니다.")
    revision = PortfolioStore().write(dashboard_path(request), payload.state.model_dump(mode="json"),
                                      payload.revision, create=payload.revision is None)
    return {"revision": revision}


@router.get("/api/cron/rebalance")
def daily_rebalance():
    return run_notification()


@router.post("/api/dashboard/notifications/test")
def test_email(request: Request):
    if not is_owner(request.session["user"]):
        raise HTTPException(403, "이메일 테스트는 운영자 계정에서만 사용할 수 있습니다.")
    return run_notification(test=True)
