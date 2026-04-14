from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.schemas import BacktestRequest, BacktestResponse
from app.services.backtest import BacktestService
from app.services.data_provider import DataProviderError


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="US Portfolio Backtester", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_backtest_service() -> BacktestService:
    return BacktestService()


@app.get("/")
def read_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/backtests", response_model=BacktestResponse)
def create_backtest(
    payload: BacktestRequest,
    service: BacktestService = Depends(get_backtest_service),
) -> BacktestResponse:
    try:
        return service.run(payload)
    except DataProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

