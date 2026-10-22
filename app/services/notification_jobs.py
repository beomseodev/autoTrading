import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.dashboard_schemas import SavedDashboard
from app.services.dashboard import DashboardService
from app.services.notification_email import email_ready, send_email
from app.services.portfolio_store import PortfolioStore


def run_notification(*, test=False):
    store = PortfolioStore()
    now = datetime.now(timezone.utc)
    day = (now + timedelta(hours=9)).date().isoformat()
    status = {"checkedAt": now.isoformat(), "outcome": "disabled", "message": "이메일 알림이 꺼져 있습니다."}

    def finish(outcome, message):
        status.update(outcome=outcome, message=message)
        store.write("notifications/status.json", status)
        return status

    if not email_ready():
        if test:
            raise HTTPException(503, "Gmail 앱 비밀번호 설정이 필요합니다.")
        return finish("not_configured", "Gmail 발송 설정이 없어 이메일 점검을 보류했습니다.")
    if test:
        subject, body = "[포트폴리오] 알림 메일 연결 테스트", "본인 전용 포트폴리오의 알림 메일 연결 테스트입니다.\n" + os.environ["APP_BASE_URL"]
        claim_name = "notifications/test-" + day + ".json"
    else:
        raw, revision = store.read("dashboard.json")
        if raw is None:
            return finish("no_portfolio", "서버에 저장된 포트폴리오가 없습니다.")
        saved = SavedDashboard.model_validate(raw)
        if not saved.portfolio.emailAlerts:
            return finish("disabled", status["message"])
        snapshot = DashboardService().portfolio_snapshot(saved.portfolio)
        check = snapshot["rebalance"]
        if not check["ready"]:
            return finish("deferred", check["message"])
        if check["status"] == "balanced":
            return finish("balanced", check["message"])
        # Do not email a decision from holdings changed while quotes were being fetched.
        if store.read("dashboard.json")[1] != revision:
            return finish("deferred", "점검 중 입력 정보가 변경되어 발송을 보류했습니다.")
        status["asOf"] = check["asOf"]
        claim_name = "notifications/alert-" + check["asOf"] + ".json"
        subject = "[포트폴리오] 리밸런싱 점검 알림"
        lines = ["설정한 목표 비중의 허용폭을 벗어난 자산이 있습니다.",
                 f"시세 기준일: {' / '.join(check['quoteDates'])} · 허용폭: ±{check['thresholdPct']:g}%p",
                 f"보유 정보 수정일: {saved.portfolioSavedAt or '미기록'}", ""]
        for item in check["items"]:
            if item["needsRebalance"]:
                lines.append(f"{item['ticker']}: 현재 {item['weightPct']:.2f}% / 목표 {item['targetWeightPct']:.2f}% (차이 {item['driftPct']:+.2f}%p)")
        body = "\n".join([*lines, "", "수동 입력한 수량 기준입니다. 거래 후 보유 정보를 수정해주세요.",
                           "일봉 기준 점검이며 자동 주문은 실행하지 않습니다.", os.environ["APP_BASE_URL"]])
    try:
        store.write(claim_name, {"attemptedAt": now.isoformat()}, create=True)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return {**status, "outcome": "already_attempted", "message": "같은 시세 기준일의 발송을 이미 시도했습니다. 중복 발송하지 않습니다."}
    # shortcut: SMTP와 저장소를 묶은 트랜잭션은 없다. 모호한 실패는 재전송하지 않으며 발송 재시도 보장이 필요할 때 공급자 idempotency를 도입한다.
    try:
        send_email(subject, body)
    except HTTPException:
        return finish("send_failed", "메일 발송에 실패했거나 결과를 확인하지 못했습니다. 설정을 확인해주세요. 같은 기준일에는 자동 재시도하지 않습니다.")
    return finish("sent", "메일 서버가 알림을 접수했습니다. 실제 수신함과 스팸함을 확인해주세요.")
