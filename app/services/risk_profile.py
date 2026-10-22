from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.schemas import (
    RiskProfileAllocationPoint,
    RiskProfileCurvePoint,
    RiskProfileMetric,
    RiskProfileRequest,
    RiskProfileResponse,
)
from app.services.data_provider import DataProviderError, YFinanceDataProvider


class RiskProfileService:
    TRADING_DAYS_PER_YEAR = 252

    def __init__(self, data_provider: YFinanceDataProvider | None = None) -> None:
        self.data_provider = data_provider or YFinanceDataProvider()

    def run(self, request: RiskProfileRequest) -> RiskProfileResponse:
        start_date, end_date = self._resolve_period(request)
        market_data = self.data_provider.fetch_market_data(request.tickers, start_date, end_date)
        prices = market_data.prices.reindex(columns=request.tickers).sort_index().dropna(how="any")
        dividends = market_data.dividends.reindex(index=prices.index, columns=request.tickers, fill_value=0.0).fillna(0.0)

        if len(prices.index) < 3:
            raise DataProviderError("At least three common trading days are required to compute a risk profile.")

        normalized = self._total_return_index(prices, dividends)
        returns = normalized.pct_change().dropna(how="any")
        if len(returns.index) < 2:
            raise DataProviderError("At least two daily return observations are required to compute correlation.")

        portfolio_normalized = self._portfolio_curve(normalized, request.tickers, request.weightsPct)
        portfolio_returns = portfolio_normalized.pct_change().dropna()
        elapsed_days = max((prices.index[-1] - prices.index[0]).days, 1)
        elapsed_years = elapsed_days / 365.25

        metrics = [
            self._metric_for_ticker(
                ticker=ticker,
                prices=normalized[ticker],
                returns=returns[ticker],
                normalized=normalized[ticker],
                elapsed_years=elapsed_years,
            )
            for ticker in request.tickers
        ]
        allocation_label = "/".join(f"{weight:g}" for weight in request.weightsPct)
        portfolio_metric = self._metric_for_ticker(
            ticker=f"Portfolio {allocation_label}",
            prices=portfolio_normalized,
            returns=portfolio_returns,
            normalized=portfolio_normalized,
            elapsed_years=elapsed_years,
        )
        allocation_profiles = [
            self._allocation_profile_point(
                normalized=normalized,
                tickers=request.tickers,
                first_weight_pct=float(first_weight_pct),
                elapsed_years=elapsed_years,
            )
            for first_weight_pct in range(0, 101, 10)
        ]

        left_returns = returns[request.tickers[0]]
        right_returns = returns[request.tickers[1]]
        if left_returns.std() <= 1e-12 or right_returns.std() <= 1e-12:
            correlation = None
        else:
            correlation_value = float(left_returns.corr(right_returns))
            correlation = round(correlation_value, 6) if np.isfinite(correlation_value) else None

        curve = [
            RiskProfileCurvePoint(
                date=timestamp.date(),
                values={
                    **{ticker: round(float(normalized.loc[timestamp, ticker]), 4) for ticker in request.tickers},
                    "Portfolio": round(float(portfolio_normalized.loc[timestamp]), 4),
                },
            )
            for timestamp in normalized.index
        ]

        return RiskProfileResponse(
            startDate=prices.index[0].date(),
            endDate=prices.index[-1].date(),
            tickers=request.tickers,
            weightsPct=[round(float(weight), 4) for weight in request.weightsPct],
            portfolioMetric=portfolio_metric,
            allocationProfiles=allocation_profiles,
            metrics=metrics,
            correlation=correlation,
            normalizedPriceCurve=curve,
        )

    def _resolve_period(self, request: RiskProfileRequest) -> tuple[date, date]:
        if request.period.lookbackYears is not None:
            end_date = date.today()
            day_span = max(int(round(request.period.lookbackYears * 365.25)), 1)
            start_date = end_date - timedelta(days=day_span)
            return start_date, end_date
        return request.period.startDate, request.period.endDate

    def _total_return_index(self, prices: pd.DataFrame, dividends: pd.DataFrame) -> pd.DataFrame:
        previous_prices = prices.shift(1)
        daily_returns = ((prices + dividends) / previous_prices) - 1.0
        daily_returns = daily_returns.iloc[1:].fillna(0.0)
        total_return = (1.0 + daily_returns).cumprod().multiply(100.0)
        first_row = pd.DataFrame([100.0] * len(prices.columns), index=prices.columns).T
        first_row.index = pd.DatetimeIndex([prices.index[0]])
        return pd.concat([first_row, total_return]).reindex(columns=prices.columns)

    def _portfolio_curve(self, normalized: pd.DataFrame, tickers: list[str], weights_pct: list[float]) -> pd.Series:
        weights = pd.Series(
            {ticker: weight / 100.0 for ticker, weight in zip(tickers, weights_pct)},
            dtype=float,
        )
        return normalized.dot(weights)

    def _allocation_profile_point(
        self,
        normalized: pd.DataFrame,
        tickers: list[str],
        first_weight_pct: float,
        elapsed_years: float,
    ) -> RiskProfileAllocationPoint:
        weights_pct = [first_weight_pct, 100.0 - first_weight_pct]
        portfolio_normalized = self._portfolio_curve(normalized, tickers, weights_pct)
        metric = self._metric_for_ticker(
            ticker=f"{tickers[0]} {first_weight_pct:g}% / {tickers[1]} {100.0 - first_weight_pct:g}%",
            prices=portfolio_normalized,
            returns=portfolio_normalized.pct_change().dropna(),
            normalized=portfolio_normalized,
            elapsed_years=elapsed_years,
        )
        return RiskProfileAllocationPoint(
            weightsPct=[round(float(weight), 4) for weight in weights_pct],
            metric=metric,
        )

    def _metric_for_ticker(
        self,
        ticker: str,
        prices: pd.Series,
        returns: pd.Series,
        normalized: pd.Series,
        elapsed_years: float,
    ) -> RiskProfileMetric:
        start_price = float(prices.iloc[0])
        end_price = float(prices.iloc[-1])
        total_return_pct = ((end_price / start_price) - 1.0) * 100.0
        annualized_return_pct = ((end_price / start_price) ** (1.0 / elapsed_years) - 1.0) * 100.0
        annualized_volatility_pct = float(returns.std() * np.sqrt(self.TRADING_DAYS_PER_YEAR) * 100.0)
        drawdown = (normalized / normalized.cummax()) - 1.0
        mdd_pct = float(drawdown.min() * 100.0)

        return RiskProfileMetric(
            ticker=ticker,
            totalReturnPct=round(total_return_pct, 4),
            annualizedReturnPct=round(annualized_return_pct, 4),
            annualizedVolatilityPct=round(annualized_volatility_pct, 4),
            mddPct=round(mdd_pct, 4),
        )
