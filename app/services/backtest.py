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
    INFLATION_RATE = 0.03
    # 수정: 2026-04-24 — 연 TER를 거래일당 선형 분할(업계 관행 252 거래일/년)
    TRADING_DAYS_PER_YEAR = 252

    def __init__(self, data_provider: YFinanceDataProvider | None = None) -> None:
        self.data_provider = data_provider or YFinanceDataProvider()

    def run(self, request: BacktestRequest) -> BacktestResponse:
        start_date, end_date = self._resolve_period(request)
        tickers = [position.ticker for position in request.positions]
        target_weights = pd.Series(
            {position.ticker: position.targetWeight / 100 for position in request.positions},
            dtype=float,
        )
        # 수정: 2026-04-24 — 티커별 연 운영보수율(TER), 일일 차감에 사용
        expense_ratios = pd.Series(
            {position.ticker: float(position.annualExpenseRatio) for position in request.positions},
            dtype=float,
        ).reindex(tickers, fill_value=0.0)
        market_data = self.data_provider.fetch_market_data(tickers, start_date, end_date)
        prices = market_data.prices
        dividends = market_data.dividends
        if len(prices.index) < 2:
            raise DataProviderError("At least two trading days are required to run a backtest.")

        holdings = pd.Series(0.0, index=tickers, dtype=float)
        cash = float(request.initialCapital)
        total_contributed = float(request.initialCapital)
        cash_flows: list[tuple[date, float]] = [(prices.index[0].date(), -float(request.initialCapital))]
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

        # 수정: 2026-04-23 — 캘린더 리밸런스일만 계산(band/rsi 모드에서는 불필요)
        calendar_dates = (
            self._calendar_rebalance_dates(prices.index, request.rebalance.frequency)
            if request.rebalance.mode == "calendar"
            else set()
        )
        contribution_dates = self._monthly_contribution_dates(prices.index, request.monthlyContribution)
        rsi_schedule = self._rsi_rebalance_schedule(prices, request) if request.rebalance.mode == "rsi" else {}

        base_date = prices.index[0].date()
        equity_curve: list[EquityPoint] = []
        real_equity_curve: list[EquityPoint] = []
        total_expense_paid = 0.0
        for index_position, timestamp in enumerate(prices.index):
            current_prices = prices.loc[timestamp]

            if index_position > 0:
                # 수정: 2026-04-23 — 야후 배당은 총액 기준; dividendTaxRate 만큼 차감한 순액만 현금·재투자에 반영
                gross_dividend_cash = self._collect_dividend_cash(holdings, dividends.loc[timestamp])
                tax_rate = float(request.execution.dividendTaxRate)
                dividend_cash = gross_dividend_cash * (1.0 - tax_rate)
                cash += dividend_cash

                if dividend_cash > 1e-8 and request.execution.dividendReinvestment:
                    portfolio_value_before_reinvest = cash + float((holdings * current_prices).sum())
                    dividend_trade = self._invest_cash_by_weights(
                        current_holdings=holdings,
                        cash=cash,
                        cash_to_invest=dividend_cash,
                        prices=current_prices,
                        target_weights=target_weights,
                        fractional_shares=request.execution.fractionalShares,
                        fee_rate=request.execution.feeRate,
                        slippage_rate=request.execution.slippageRate,
                        portfolio_value=portfolio_value_before_reinvest,
                    )
                    holdings = dividend_trade.holdings
                    cash = dividend_trade.cash
                    if dividend_trade.turnover_pct > 1e-8:
                        events.append(
                            RebalanceEvent(
                                date=timestamp.date(),
                                reason="dividend-reinvest",
                                turnoverPct=round(dividend_trade.turnover_pct, 4),
                            )
                        )

                if timestamp in contribution_dates:
                    cash += request.monthlyContribution
                    total_contributed += request.monthlyContribution
                    cash_flows.append((timestamp.date(), -float(request.monthlyContribution)))
                    portfolio_value_before_contribution_invest = cash + float((holdings * current_prices).sum())
                    contribution_trade = self._invest_cash_by_weights(
                        current_holdings=holdings,
                        cash=cash,
                        cash_to_invest=request.monthlyContribution,
                        prices=current_prices,
                        target_weights=target_weights,
                        fractional_shares=request.execution.fractionalShares,
                        fee_rate=request.execution.feeRate,
                        slippage_rate=request.execution.slippageRate,
                        portfolio_value=portfolio_value_before_contribution_invest,
                    )
                    holdings = contribution_trade.holdings
                    cash = contribution_trade.cash
                    if contribution_trade.turnover_pct > 1e-8:
                        events.append(
                            RebalanceEvent(
                                date=timestamp.date(),
                                reason="contribution:monthly",
                                turnoverPct=round(contribution_trade.turnover_pct, 4),
                            )
                        )

                reason = None
                if request.rebalance.mode == "calendar" and timestamp in calendar_dates:
                    reason = f"calendar:{request.rebalance.frequency}"
                elif request.rebalance.mode == "rsi" and timestamp in rsi_schedule:
                    reason = rsi_schedule[timestamp]
                elif request.rebalance.mode == "band":
                    # 수정: 2026-04-23 — 매일 종가 기준 비중 드리프트가 밴드 폭(%p)을 넘으면 목표로 리밸런스
                    reason = self._band_rebalance_reason(
                        holdings=holdings,
                        cash=cash,
                        prices=current_prices,
                        target_weights=target_weights,
                        band_width_pct=float(request.rebalance.bandWidthPct),
                    )

                if reason is not None:
                    trade = self._rebalance_to_target(
                        current_holdings=holdings,
                        cash=cash,
                        prices=current_prices,
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

            # 수정: 2026-04-24 — 일일 TER: 현금에서 차감, 부족 시 종가 기준 보유 시가총액 비례 매도(매매 수수료 미부과)
            daily_ter = self._compute_daily_ter_amount(holdings, current_prices, expense_ratios)
            total_expense_paid += daily_ter
            holdings, cash = self._apply_daily_ter_to_portfolio(holdings, cash, current_prices, daily_ter)

            portfolio_value = cash + float((holdings * current_prices).sum())
            equity_curve.append(EquityPoint(date=timestamp.date(), value=round(portfolio_value, 4)))
            real_equity_curve.append(
                EquityPoint(
                    date=timestamp.date(),
                    value=round(self._to_real_value(portfolio_value, base_date, timestamp.date()), 4),
                )
            )

        final_prices = prices.iloc[-1]
        final_value = cash + float((holdings * final_prices).sum())
        real_final_value = self._to_real_value(final_value, base_date, prices.index[-1].date())
        deployed_capital = total_contributed - cash
        real_total_contributed = sum(
            self._to_real_value(-amount, base_date, flow_date)
            for flow_date, amount in cash_flows
            if amount < 0
        )
        holdings_value = holdings * final_prices

        holdings_snapshot = [
            HoldingSnapshot(
                ticker=ticker,
                shares=round(float(holdings[ticker]), 8),
                value=round(float(holdings_value[ticker]), 4),
                weight=round(float((holdings_value[ticker] / final_value) * 100) if final_value else 0, 4),
            )
            for ticker in tickers
        ]

        total_return_pct = ((final_value / total_contributed) - 1) * 100
        real_total_return_pct = ((real_final_value / real_total_contributed) - 1) * 100
        cagr_pct = None
        if request.monthlyContribution == 0:
            elapsed_days = max((prices.index[-1] - prices.index[0]).days, 1)
            elapsed_years = elapsed_days / 365.25
            cagr_pct = ((final_value / request.initialCapital) ** (1 / elapsed_years) - 1) * 100
        xirr_pct = self._compute_xirr(cash_flows + [(prices.index[-1].date(), final_value)])

        equity_values = pd.Series([point.value for point in equity_curve], index=prices.index, dtype=float)
        running_max = equity_values.cummax()
        drawdown = (equity_values / running_max) - 1
        mdd_pct = float(drawdown.min() * 100)

        return BacktestResponse(
            summary=SummaryOutput(
                initialCapital=round(request.initialCapital, 4),
                monthlyContribution=round(request.monthlyContribution, 4),
                totalContributed=round(total_contributed, 4),
                inflationRatePct=round(self.INFLATION_RATE * 100, 4),
                deployedCapital=round(deployed_capital, 4),
                finalValue=round(final_value, 4),
                realFinalValue=round(real_final_value, 4),
                totalReturnPct=round(total_return_pct, 4),
                realTotalReturnPct=round(real_total_return_pct, 4),
                cagrPct=round(cagr_pct, 4) if cagr_pct is not None else None,
                xirrPct=round(xirr_pct, 4) if xirr_pct is not None else None,
                mddPct=round(mdd_pct, 4),
                rebalanceCount=len(events),
                totalExpensePaid=round(total_expense_paid, 4),
            ),
            equityCurve=equity_curve,
            realEquityCurve=real_equity_curve,
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

    def _monthly_contribution_dates(
        self,
        trading_index: pd.DatetimeIndex,
        monthly_contribution: float,
    ) -> set[pd.Timestamp]:
        if monthly_contribution <= 0:
            return set()

        first_days = pd.Series(trading_index, index=trading_index).groupby(trading_index.to_period("M")).first()
        return set(first_days.iloc[1:].tolist())

    def _band_rebalance_reason(
        self,
        holdings: pd.Series,
        cash: float,
        prices: pd.Series,
        target_weights: pd.Series,
        band_width_pct: float,
    ) -> str | None:
        """드리프트 밴드: 실제 비중과 목표 비중의 최대 절대 편차가 허용폭(%p)을 넘으면 리밸런스 사유 문자열 반환.

        수정: 2026-04-23 — band 모드용. band_width_pct는 비중 절대편차 허용폭(예: 5 → ±5%p).
        """
        portfolio_value = cash + float((holdings * prices).sum())
        if portfolio_value <= 1e-12:
            return None
        market_values = holdings * prices
        actual_weights = market_values / portfolio_value
        max_abs_drift = float((actual_weights - target_weights).abs().max())
        threshold = band_width_pct / 100.0
        if max_abs_drift > threshold + 1e-12:
            return f"band:{band_width_pct}"
        return None

    def _compute_xirr(self, cash_flows: list[tuple[date, float]]) -> float | None:
        if len(cash_flows) < 2:
            return None

        if not any(amount < 0 for _, amount in cash_flows) or not any(amount > 0 for _, amount in cash_flows):
            return None

        base_date = cash_flows[0][0]
        dated_amounts = [
            ((flow_date - base_date).days / 365.25, amount)
            for flow_date, amount in cash_flows
        ]
        if max(year_fraction for year_fraction, _ in dated_amounts) <= 0:
            return None

        def xnpv(rate: float) -> float:
            return sum(amount / ((1 + rate) ** year_fraction) for year_fraction, amount in dated_amounts)

        candidate_rates = [
            -0.9999,
            -0.99,
            -0.95,
            -0.9,
            -0.75,
            -0.5,
            -0.25,
            -0.1,
            0.0,
            0.05,
            0.1,
            0.2,
            0.5,
            1.0,
            2.0,
            5.0,
            10.0,
            20.0,
            50.0,
            100.0,
        ]
        npv_values = [xnpv(rate) for rate in candidate_rates]

        bracket: tuple[float, float] | None = None
        for left_rate, right_rate, left_npv, right_npv in zip(
            candidate_rates,
            candidate_rates[1:],
            npv_values,
            npv_values[1:],
        ):
            if left_npv == 0:
                return left_rate * 100
            if left_npv * right_npv < 0:
                bracket = (left_rate, right_rate)
                break

        if bracket is None:
            if npv_values[-1] == 0:
                return candidate_rates[-1] * 100
            return None

        low_rate, high_rate = bracket
        low_npv = xnpv(low_rate)

        for _ in range(100):
            mid_rate = (low_rate + high_rate) / 2
            mid_npv = xnpv(mid_rate)
            if abs(mid_npv) < 1e-10:
                return mid_rate * 100

            if low_npv * mid_npv <= 0:
                high_rate = mid_rate
            else:
                low_rate = mid_rate
                low_npv = mid_npv

        return ((low_rate + high_rate) / 2) * 100

    def _to_real_value(self, nominal_value: float, base_date: date, value_date: date) -> float:
        elapsed_years = max((value_date - base_date).days, 0) / 365.25
        return nominal_value / ((1 + self.INFLATION_RATE) ** elapsed_years)

    def _rsi_rebalance_schedule(self, prices: pd.DataFrame, request: BacktestRequest) -> dict[pd.Timestamp, str]:
        schedule: dict[pd.Timestamp, str] = {}
        signal_tickers = (
            [request.rebalance.rsiTriggerTicker]
            if request.rebalance.rsiSignalScope == "single" and request.rebalance.rsiTriggerTicker is not None
            else list(prices.columns)
        )

        for ticker in signal_tickers:
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

    def _compute_daily_ter_amount(self, holdings: pd.Series, prices: pd.Series, expense_ratios: pd.Series) -> float:
        """티커별 시가총액에 연 TER를 곱한 뒤 252로 나눈 일일 보수액."""
        position_mv = holdings.astype(float) * prices.astype(float)
        return float((position_mv * expense_ratios.astype(float)).sum() / float(self.TRADING_DAYS_PER_YEAR))

    def _apply_daily_ter_to_portfolio(
        self,
        holdings: pd.Series,
        cash: float,
        prices: pd.Series,
        expense: float,
    ) -> tuple[pd.Series, float]:
        """일일 보수를 현금에서 징수. 현금이 부족하면 보유 전체를 종가 비율로 축소 매도해 충당(수수료 없음)."""
        if expense <= 1e-12:
            return holdings, cash
        cash = float(cash - expense)
        if cash >= -1e-9:
            return holdings.astype(float), float(max(cash, 0.0))
        deficit = float(-cash)
        total_mv = float((holdings.astype(float) * prices.astype(float)).sum())
        if total_mv <= 1e-12:
            return holdings.astype(float) * 0.0, 0.0
        fraction = min(1.0, deficit / total_mv + 1e-15)
        new_holdings = holdings.astype(float) * (1.0 - fraction)
        cash = cash + fraction * total_mv
        return new_holdings, float(max(cash, 0.0))

    def _collect_dividend_cash(self, holdings: pd.Series, dividend_row: pd.Series) -> float:
        return float((holdings * dividend_row).sum())

    def _invest_cash_by_weights(
        self,
        current_holdings: pd.Series,
        cash: float,
        cash_to_invest: float,
        prices: pd.Series,
        target_weights: pd.Series,
        fractional_shares: bool,
        fee_rate: float,
        slippage_rate: float,
        portfolio_value: float,
    ) -> TradeResult:
        if cash_to_invest <= 0 or portfolio_value <= 0:
            return TradeResult(holdings=current_holdings.copy(), cash=cash, turnover_pct=0)

        buy_budget = cash_to_invest * target_weights
        buy_shares = buy_budget / (prices * (1 + slippage_rate))
        if not fractional_shares:
            buy_shares = np.floor(buy_shares + 1e-12)

        buy_cost = buy_shares * prices * (1 + slippage_rate)
        gross_buy_cost = float(buy_cost.sum())
        buy_fee = gross_buy_cost * fee_rate
        total_buy_cost = gross_buy_cost + buy_fee

        if total_buy_cost > cash_to_invest + 1e-8 and gross_buy_cost > 0:
            scale = max(min(cash_to_invest / total_buy_cost, 1), 0)
            buy_shares = buy_shares * scale
            if not fractional_shares:
                buy_shares = np.floor(buy_shares + 1e-12)
            buy_cost = buy_shares * prices * (1 + slippage_rate)
            gross_buy_cost = float(buy_cost.sum())
            buy_fee = gross_buy_cost * fee_rate
            total_buy_cost = gross_buy_cost + buy_fee

        holdings_after_trade = current_holdings + buy_shares
        final_cash = cash - total_buy_cost
        turnover_pct = (gross_buy_cost / portfolio_value) * 100 if portfolio_value else 0

        return TradeResult(
            holdings=holdings_after_trade.astype(float),
            cash=float(max(final_cash, 0)),
            turnover_pct=float(turnover_pct),
        )

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
