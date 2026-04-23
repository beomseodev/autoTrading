from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PositionInput(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    targetWeight: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def normalize_ticker(self) -> "PositionInput":
        self.ticker = self.ticker.strip().upper()
        return self


class PeriodInput(BaseModel):
    startDate: date | None = None
    endDate: date | None = None
    lookbackYears: float | None = Field(default=None, gt=0, le=50)

    @model_validator(mode="after")
    def validate_period(self) -> "PeriodInput":
        has_date_range = self.startDate is not None or self.endDate is not None
        has_lookback = self.lookbackYears is not None

        if has_date_range and has_lookback:
            raise ValueError("lookbackYears and startDate/endDate cannot be used together.")

        if has_date_range:
            if self.startDate is None or self.endDate is None:
                raise ValueError("Both startDate and endDate are required when using a date range.")
            if self.startDate >= self.endDate:
                raise ValueError("startDate must be earlier than endDate.")
        elif not has_lookback:
            raise ValueError("Provide either startDate/endDate or lookbackYears.")

        return self


class RebalanceInput(BaseModel):
    mode: Literal["calendar", "rsi"]
    frequency: Literal["monthly", "quarterly", "yearly"] | None = None
    rsiPeriod: int = Field(default=14, ge=2, le=200)
    lower: float = Field(default=30, gt=0, lt=100)
    upper: float = Field(default=70, gt=0, lt=100)
    rsiSignalScope: Literal["all", "single"] = "all"
    rsiTriggerTicker: str | None = None

    @model_validator(mode="after")
    def normalize_rsi_trigger_ticker(self) -> "RebalanceInput":
        if self.rsiTriggerTicker is not None:
            self.rsiTriggerTicker = self.rsiTriggerTicker.strip().upper() or None
        return self

    @model_validator(mode="after")
    def validate_rebalance(self) -> "RebalanceInput":
        if self.mode == "calendar" and self.frequency is None:
            raise ValueError("frequency is required when mode is calendar.")
        if self.mode == "rsi" and self.lower >= self.upper:
            raise ValueError("lower must be smaller than upper.")
        if self.mode == "rsi" and self.rsiSignalScope == "single" and self.rsiTriggerTicker is None:
            raise ValueError("rsiTriggerTicker is required when rsiSignalScope is single.")
        return self


class ExecutionInput(BaseModel):
    fractionalShares: bool = True
    dividendReinvestment: bool = True
    feeRate: float = Field(default=0, ge=0, le=1)
    slippageRate: float = Field(default=0, ge=0, le=1)


class BacktestRequest(BaseModel):
    positions: list[PositionInput]
    initialCapital: float = Field(gt=0)
    monthlyContribution: float = Field(default=0, ge=0)
    period: PeriodInput
    rebalance: RebalanceInput
    execution: ExecutionInput = Field(default_factory=ExecutionInput)

    @model_validator(mode="after")
    def validate_weights(self) -> "BacktestRequest":
        if not self.positions:
            raise ValueError("At least one position is required.")

        total_weight = sum(position.targetWeight for position in self.positions)
        if abs(total_weight - 100) > 0.001:
            raise ValueError("Position weights must add up to 100.")

        unique_tickers = {position.ticker for position in self.positions}
        if len(unique_tickers) != len(self.positions):
            raise ValueError("Duplicate tickers are not allowed.")

        if self.rebalance.mode == "rsi" and self.rebalance.rsiSignalScope == "single":
            if self.rebalance.rsiTriggerTicker not in unique_tickers:
                raise ValueError("rsiTriggerTicker must match one of the selected position tickers.")

        return self


class SummaryOutput(BaseModel):
    initialCapital: float
    monthlyContribution: float
    totalContributed: float
    inflationRatePct: float
    deployedCapital: float
    finalValue: float
    realFinalValue: float
    totalReturnPct: float
    realTotalReturnPct: float
    cagrPct: float | None
    xirrPct: float | None
    mddPct: float
    rebalanceCount: int


class EquityPoint(BaseModel):
    date: date
    value: float


class HoldingSnapshot(BaseModel):
    ticker: str
    shares: float
    weight: float
    value: float


class RebalanceEvent(BaseModel):
    date: date
    reason: str
    turnoverPct: float


class BacktestResponse(BaseModel):
    summary: SummaryOutput
    equityCurve: list[EquityPoint]
    realEquityCurve: list[EquityPoint]
    holdingsSnapshot: list[HoldingSnapshot]
    rebalanceEvents: list[RebalanceEvent]


# 수정: 2026-04-23 — 다중 포트폴리오 비교 API용 요청/응답 모델 추가
class LabeledBacktestRun(BaseModel):
    """단일 백테스트 설정 + UI/응답 매핑용 라벨."""

    label: str = Field(min_length=1, max_length=64)
    request: BacktestRequest

    @model_validator(mode="after")
    def normalize_label(self) -> "LabeledBacktestRun":
        self.label = self.label.strip()
        if not self.label:
            raise ValueError("label cannot be empty.")
        return self


class CompareBacktestsRequest(BaseModel):
    """2개 이상의 시나리오를 한 번에 백테스트(순차 실행)."""

    runs: list[LabeledBacktestRun] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def validate_unique_labels(self) -> "CompareBacktestsRequest":
        labels = [run.label for run in self.runs]
        if len(labels) != len(set(labels)):
            raise ValueError("Duplicate scenario labels are not allowed.")
        return self


class LabeledBacktestResult(BaseModel):
    """비교 응답의 한 행: 라벨 + 단일 백테스트 결과."""

    label: str
    result: BacktestResponse


class CompareBacktestsResponse(BaseModel):
    """비교 API 응답: 시나리오별 결과 배열."""

    runs: list[LabeledBacktestResult]
