from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OwnedPosition(BaseModel):
    model_config = {"allow_inf_nan": False}
    ticker: str = Field(min_length=1, max_length=10, pattern=r"^(?:[A-Za-z][A-Za-z0-9.\-]*|[0-9]{6}\.[Kk][SsQq])$")
    quantity: float = Field(gt=0, le=1e9)
    averageCost: float = Field(ge=0, le=1e9)
    targetWeight: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def normalize(self) -> OwnedPosition:
        self.ticker = self.ticker.upper()
        return self


class PortfolioSnapshotRequest(BaseModel):
    model_config = {"allow_inf_nan": False}
    positions: list[OwnedPosition] = Field(default_factory=list, max_length=30)
    cashKRW: float = Field(default=0, ge=0, le=1e15)
    cashUSD: float = Field(default=0, ge=0, le=1e12)
    baseCurrency: Literal["KRW", "USD"] = "KRW"
    rebalanceTolerancePct: float = Field(default=5, ge=0.01, le=50)
    emailAlerts: bool = False

    @model_validator(mode="after")
    def unique_tickers(self) -> PortfolioSnapshotRequest:
        if len({p.ticker for p in self.positions}) != len(self.positions):
            raise ValueError("같은 종목은 한 행에 합쳐 입력해주세요.")
        if sum(p.targetWeight or 0 for p in self.positions) > 100 + 1e-6:
            raise ValueError("종목 목표 비중의 합계는 100% 이하여야 합니다. 남은 비중은 현금입니다.")
        if self.emailAlerts and (not self.positions or any(p.targetWeight is None for p in self.positions)):
            raise ValueError("이메일 알림을 켜려면 모든 종목의 목표 비중을 입력해주세요.")
        return self


class GoalProjectionRequest(BaseModel):
    model_config = {"allow_inf_nan": False}
    initialAmount: float = Field(ge=0, le=1e15)
    targetAmount: float = Field(gt=0, le=1e15)
    monthlyContribution: float = Field(ge=0, le=1e12)
    annualReturnPct: float = Field(ge=-50, le=50)


class ProjectionPoint(BaseModel):
    date: date
    value: float


class GoalScenario(BaseModel):
    label: str
    annualReturnPct: float
    monthsToGoal: int | None
    reachedDate: date | None
    points: list[ProjectionPoint]


class GoalProjectionResponse(BaseModel):
    asOf: date
    horizonYears: int
    scenarios: list[GoalScenario]


class SavedGoal(BaseModel):
    model_config = {"allow_inf_nan": False}
    currency: Literal["KRW", "USD"]
    targetAmount: float = Field(gt=0, le=1e15)
    monthlyContribution: float = Field(ge=0, le=1e12)
    annualReturnPct: float = Field(ge=-50, le=50)


class SavedDashboard(BaseModel):
    version: Literal[1] = 1
    portfolio: PortfolioSnapshotRequest
    goal: SavedGoal | None = None
    portfolioSavedAt: str | None = Field(default=None, max_length=40)


class SaveDashboardRequest(BaseModel):
    state: SavedDashboard
    revision: str | None = Field(default=None, max_length=200)
