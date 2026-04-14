from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.schemas import BacktestRequest, BacktestResponse, EquityPoint, HoldingSnapshot, RebalanceEvent, SummaryOutput
from app.services.data_provider import DataProviderError, YFinanceDataProvider
from app.services.indicators import compute_rsi


@dataclass
class TradeResult:
    holdings: pd.Series
    cash: float
    turnover_pct: float


class BacktestService:
    def __init__(self, data_provider: YFinanceDataProvider | None = None) -> None:
        self.data_provider = data_provider or YFinanceDataProvider()

    def run(self, request: BacktestRequest) -> BacktestResponse:
        start_date, end_date = self._resolve_period(request)
        tickers = [position.ticker for position in request.positions]
        target_weights = pd.Series(
            {position.ticker: position.targetWeight / 100 for position in request.positions},
            dtype=float,
        )
        prices = self.data_provider.fetch_adjusted_close(tickers, start_date, end_date)
        if len(prices.index) < 2:
            raise DataProviderError("At least two trading days are required to run a backtest.")

        holdings = pd.Series(0.0, index=tickers, dtype=float)
        cash = float(request.initialCapital)
        events: list[RebalanceEvent] = []

        initial_trade = self._rebalance_to_target(
            current_holdings=holdings,
            cash=cash,
            prices=prices.iloc[0],
            target_weights=target_weights,
            fractional_shares=request.execution.fractionalShares,
            fee_rate=request.execution.feeRate,
            slippage_rate=request.execution.slippageRate,
        )
        holdings = initial_trade.holdings
        cash = initial_trade.cash
        deployed_capital = request.initialCapital - cash

        calendar_dates = self._calendar_rebalance_dates(prices.index, request.rebalance.frequency)
        rsi_schedule = self._rsi_rebalance_schedule(prices, request) if request.rebalance.mode == "rsi" else {}

        equity_curve: list[EquityPoint] = []
        for index_position, timestamp in enumerate(prices.index):
            if index_position > 0:
                reason = None
                if request.rebalance.mode == "calendar" and timestamp in calendar_dates:
                    reason = f"calendar:{request.rebalance.frequency}"
                elif request.rebalance.mode == "rsi" and timestamp in rsi_schedule:
                    reason = rsi_schedule[timestamp]

                if reason is not None:
                    trade = self._rebalance_to_target(
                        current_holdings=holdings,
                        cash=cash,
                        prices=prices.loc[timestamp],
                        target_weights=target_weights,
                        fractional_shares=request.execution.fractionalShares,
                        fee_rate=request.execution.feeRate,
                        slippage_rate=request.execution.slippageRate,
                    )
                    holdings = trade.holdings
                    cash = trade.cash
                    if trade.turnover_pct > 1e-8:
                        events.append(
                            RebalanceEvent(
                                date=timestamp.date(),
                                reason=reason,
                                turnoverPct=round(trade.turnover_pct, 4),
                            )
                        )

            portfolio_value = cash + float((holdings * prices.loc[timestamp]).sum())
            equity_curve.append(EquityPoint(date=timestamp.date(), value=round(portfolio_value, 4)))

        final_prices = prices.iloc[-1]
        final_value = equity_curve[-1].value
        holdings_value = holdings * final_prices
        total_holdings_value = float(holdings_value.sum())

        holdings_snapshot = [
            HoldingSnapshot(
                ticker=ticker,
                shares=round(float(holdings[ticker]), 8),
                value=round(float(holdings_value[ticker]), 4),
                weight=round(float((holdings_value[ticker] / final_value) * 100) if final_value else 0, 4),
            )
            for ticker in tickers
        ]

        total_return_pct = ((final_value / request.initialCapital) - 1) * 100
        elapsed_days = max((prices.index[-1] - prices.index[0]).days, 1)
        elapsed_years = elapsed_days / 365.25
        cagr_pct = ((final_value / request.initialCapital) ** (1 / elapsed_years) - 1) * 100

        equity_values = pd.Series([point.value for point in equity_curve], index=prices.index, dtype=float)
        running_max = equity_values.cummax()
        drawdown = (equity_values / running_max) - 1
        mdd_pct = float(drawdown.min() * 100)

        return BacktestResponse(
            summary=SummaryOutput(
                initialCapital=round(request.initialCapital, 4),
                deployedCapital=round(deployed_capital, 4),
                finalValue=round(final_value, 4),
                totalReturnPct=round(total_return_pct, 4),
                cagrPct=round(cagr_pct, 4),
                mddPct=round(mdd_pct, 4),
                rebalanceCount=len(events),
            ),
            equityCurve=equity_curve,
            holdingsSnapshot=holdings_snapshot,
            rebalanceEvents=events,
        )

    def _resolve_period(self, request: BacktestRequest) -> tuple[date, date]:
        if request.period.lookbackYears is not None:
            end_date = date.today()
            day_span = max(int(round(request.period.lookbackYears * 365.25)), 1)
            start_date = end_date - timedelta(days=day_span)
            return start_date, end_date
        return request.period.startDate, request.period.endDate

    def _calendar_rebalance_dates(
        self,
        trading_index: pd.DatetimeIndex,
        frequency: str | None,
    ) -> set[pd.Timestamp]:
        if frequency is None:
            return set()

        freq_map = {"monthly": "M", "quarterly": "Q", "yearly": "Y"}
        periods = trading_index.to_period(freq_map[frequency])
        first_days = pd.Series(trading_index, index=trading_index).groupby(periods).first()
        return set(first_days.iloc[1:].tolist())

    def _rsi_rebalance_schedule(self, prices: pd.DataFrame, request: BacktestRequest) -> dict[pd.Timestamp, str]:
        schedule: dict[pd.Timestamp, str] = {}

        for ticker in prices.columns:
            rsi = compute_rsi(prices[ticker], period=request.rebalance.rsiPeriod)
            for signal_position in range(1, len(rsi) - 1):
                previous_value = rsi.iloc[signal_position - 1]
                current_value = rsi.iloc[signal_position]
                if np.isnan(previous_value) or np.isnan(current_value):
                    continue

                crossed_lower = previous_value > request.rebalance.lower and current_value <= request.rebalance.lower
                crossed_upper = previous_value < request.rebalance.upper and current_value >= request.rebalance.upper
                if not (crossed_lower or crossed_upper):
                    continue

                execution_date = prices.index[signal_position + 1]
                threshold_label = "lower" if crossed_lower else "upper"
                reason = f"rsi:{ticker}:{threshold_label}"
                if execution_date in schedule:
                    schedule[execution_date] = f"{schedule[execution_date]},{reason}"
                else:
                    schedule[execution_date] = reason

        return schedule

    def _rebalance_to_target(
        self,
        current_holdings: pd.Series,
        cash: float,
        prices: pd.Series,
        target_weights: pd.Series,
        fractional_shares: bool,
        fee_rate: float,
        slippage_rate: float,
    ) -> TradeResult:
        total_value = cash + float((current_holdings * prices).sum())
        if total_value <= 0:
            return TradeResult(holdings=current_holdings.copy(), cash=cash, turnover_pct=0)

        desired_shares = (total_value * target_weights) / prices
        if not fractional_shares:
            desired_shares = np.floor(desired_shares + 1e-12)

        delta_shares = desired_shares - current_holdings
        sell_shares = delta_shares.clip(upper=0).abs()
        sell_value = sell_shares * prices * (1 - slippage_rate)
        sell_fee = float(sell_value.sum() * fee_rate)
        available_cash = cash + float(sell_value.sum()) - sell_fee

        buy_shares = delta_shares.clip(lower=0)
        buy_cost = buy_shares * prices * (1 + slippage_rate)
        gross_buy_cost = float(buy_cost.sum())
        buy_fee = gross_buy_cost * fee_rate
        total_buy_cost = gross_buy_cost + buy_fee

        if total_buy_cost > available_cash + 1e-8 and gross_buy_cost > 0:
            scale = max(min(available_cash / total_buy_cost, 1), 0)
            buy_shares = buy_shares * scale
            if not fractional_shares:
                buy_shares = np.floor(buy_shares + 1e-12)
            buy_cost = buy_shares * prices * (1 + slippage_rate)
            gross_buy_cost = float(buy_cost.sum())
            buy_fee = gross_buy_cost * fee_rate
            total_buy_cost = gross_buy_cost + buy_fee

        holdings_after_trade = current_holdings - sell_shares + buy_shares
        final_cash = available_cash - total_buy_cost

        trade_notional = float((sell_value + buy_cost).sum())
        turnover_pct = (trade_notional / total_value) * 100 if total_value else 0

        return TradeResult(
            holdings=holdings_after_trade.astype(float),
            cash=float(max(final_cash, 0)),
            turnover_pct=float(turnover_pct),
        )

