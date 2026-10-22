from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()

from app.auth import install_auth
from app.services.toss import TossService, TossError

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.schemas import (
    BacktestRequest,
    BacktestResponse,
    CompareBacktestsRequest,
    CompareBacktestsResponse,
    LabeledBacktestResult,
    RiskProfileRequest,
    RiskProfileResponse,
)
from app.services.backtest import BacktestService
from app.services.data_provider import DataProviderError
from app.services.risk_profile import RiskProfileService
from app.dashboard_schemas import PortfolioSnapshotRequest, GoalProjectionRequest, GoalProjectionResponse
from app.services.dashboard import DashboardService
from app.services.goals import project_goal


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Portfolio Dashboard", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
install_auth(app)


@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/api/toss/holdings")
def toss_holdings() -> dict:
    try:
        return TossService().holdings()
    except TossError as exc:
        raise HTTPException(exc.status, str(exc)) from None


def get_backtest_service() -> BacktestService:
    return BacktestService()


def get_risk_profile_service() -> RiskProfileService:
    return RiskProfileService()


@app.get("/")
def read_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/lab")
def read_lab() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@lru_cache
def get_dashboard_service() -> DashboardService:
    return DashboardService()


@app.get("/api/dashboard/markets")
def dashboard_markets(service: DashboardService = Depends(get_dashboard_service)) -> dict:
    return service.market_overview()


@app.post("/api/dashboard/portfolio")
def dashboard_portfolio(payload: PortfolioSnapshotRequest, service: DashboardService = Depends(get_dashboard_service)) -> dict:
    return service.portfolio_snapshot(payload)


@app.post("/api/dashboard/goal", response_model=GoalProjectionResponse)
def dashboard_goal(payload: GoalProjectionRequest) -> GoalProjectionResponse:
    return project_goal(payload)


@app.post("/api/backtests", response_model=BacktestResponse)
def create_backtest(
    payload: BacktestRequest,
    service: BacktestService = Depends(get_backtest_service),
) -> BacktestResponse:
    try:
        return service.run(payload)
    except DataProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# 수정: 2026-04-23 — 다중 시나리오 순차 백테스트 비교 엔드포인트 추가
@app.post("/api/backtests/compare", response_model=CompareBacktestsResponse)
def compare_backtests(
    payload: CompareBacktestsRequest,
    service: BacktestService = Depends(get_backtest_service),
) -> CompareBacktestsResponse:
    results: list[LabeledBacktestResult] = []
    for run in payload.runs:
        try:
            result = service.run(run.request)
        except DataProviderError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"[{run.label}] {exc}",
            ) from exc
        if results and [point.date for point in result.equityCurve] != [
            point.date for point in results[0].result.equityCurve
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"[{run.label}] 실제 거래일이 다른 포트폴리오는 비교할 수 없습니다. 모든 종목에 데이터가 있는 공통 기간을 선택해주세요.",
            )
        results.append(LabeledBacktestResult(label=run.label, result=result))
    return CompareBacktestsResponse(runs=results)


@app.post("/api/risk-profile", response_model=RiskProfileResponse)
def create_risk_profile(
    payload: RiskProfileRequest,
    service: RiskProfileService = Depends(get_risk_profile_service),
) -> RiskProfileResponse:
    try:
        return service.run(payload)
    except DataProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
