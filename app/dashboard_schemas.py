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


class RebalanceGroup(BaseModel):
    model_config = {"allow_inf_nan": False}
    name: str = Field(min_length=1, max_length=40)
    tickers: list[str] = Field(min_length=2, max_length=30)
    targetWeight: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def normalize(self) -> RebalanceGroup:
        self.name = self.name.strip()
        self.tickers = [ticker.upper() for ticker in self.tickers]
        if not self.name or len(set(self.tickers)) != len(self.tickers):
            raise ValueError("그룹 이름과 중복 없는 구성 종목을 확인해주세요.")
        return self


class PortfolioSnapshotRequest(BaseModel):
    model_config = {"allow_inf_nan": False}
    positions: list[OwnedPosition] = Field(default_factory=list, max_length=30)
    cashKRW: float = Field(default=0, ge=0, le=1e15)
    cashUSD: float = Field(default=0, ge=0, le=1e12)
    baseCurrency: Literal["KRW", "USD"] = "KRW"
    rebalanceTolerancePct: float = Field(default=5, ge=0.01, le=50)
    emailAlerts: bool = False
    rebalanceGroups: list[RebalanceGroup] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def unique_tickers(self) -> PortfolioSnapshotRequest:
        if len({p.ticker for p in self.positions}) != len(self.positions):
            raise ValueError("같은 종목은 한 행에 합쳐 입력해주세요.")
        members = [ticker for group in self.rebalanceGroups for ticker in group.tickers]
        if len(set(members)) != len(members) or not set(members) <= {p.ticker for p in self.positions}:
            raise ValueError("그룹에는 보유 종목만 중복 없이 포함할 수 있습니다.")
        if len({group.name for group in self.rebalanceGroups}) != len(self.rebalanceGroups):
            raise ValueError("그룹 이름은 서로 달라야 합니다.")
        if any(p.targetWeight is not None for p in self.positions if p.ticker in members):
            raise ValueError("그룹에 포함된 종목은 개별 목표 대신 그룹 목표를 사용해주세요.")
        if sum(p.targetWeight or 0 for p in self.positions) + sum(g.targetWeight for g in self.rebalanceGroups) > 100 + 1e-6:
            raise ValueError("개별 목표와 그룹 목표의 합계는 100% 이하여야 합니다. 남은 비중은 현금입니다.")
        if self.emailAlerts and (not self.positions or any(p.targetWeight is None and p.ticker not in members for p in self.positions)):
            raise ValueError("이메일 알림을 켜려면 모든 종목의 개별 또는 그룹 목표 비중을 입력해주세요.")
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


class PanelSize(BaseModel):
    span: int = Field(ge=3, le=12)
    height: int | None = Field(default=None, ge=120, le=1600)
    order: int | None = Field(default=None, ge=0, le=7, strict=True)
    hidden: bool = Field(default=False, strict=True)


class SavedDashboard(BaseModel):
    version: Literal[1] = 1
    portfolio: PortfolioSnapshotRequest
    goal: SavedGoal | None = None
    portfolioSavedAt: str | None = Field(default=None, max_length=40)
    layout: dict[Literal["market", "summary", "goal", "macro", "rebalance", "holdings", "allocation", "performance"], PanelSize] = Field(default_factory=dict)


class SaveDashboardRequest(BaseModel):
    state: SavedDashboard
    revision: str | None = Field(default=None, min_length=1, max_length=200)
