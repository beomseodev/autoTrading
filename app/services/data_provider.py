from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import numpy as np
import yfinance as yf


class DataProviderError(RuntimeError):
    pass


@dataclass
class MarketData:
    prices: pd.DataFrame
    dividends: pd.DataFrame


class YFinanceDataProvider:
    def fetch_market_data(self, tickers: list[str], start_date: date, end_date: date) -> MarketData:
        end_exclusive = end_date + timedelta(days=1)
        frame = yf.download(
            tickers=tickers,
            start=start_date.isoformat(),
            end=end_exclusive.isoformat(),
            auto_adjust=False,
            progress=False,
            actions=True,
            group_by="column",
            threads=True,
        )

        if frame.empty:
            raise DataProviderError("No price data returned from Yahoo Finance.")

        prices = self._extract_metric_frame(frame, tickers, "Close")
        dividends = self._extract_metric_frame(frame, tickers, "Dividends", default_value=0.0)

        prices.index = pd.to_datetime(prices.index).tz_localize(None)
        dividends.index = pd.to_datetime(dividends.index).tz_localize(None)

        prices = prices.sort_index()
        complete = prices.dropna(how="any")
        if complete.empty:
            raise DataProviderError("Price data is empty after aligning tickers on common trading days.")
        prices = prices.loc[complete.index[0]:complete.index[-1]]
        if prices.isna().any().any():
            raise DataProviderError("분석 기간 중 일부 종목의 시세가 누락되었습니다. 배당·수익 왜곡을 막기 위해 계산을 중단했습니다. 기간 또는 종목을 바꿔주세요.")
        if not np.isfinite(prices.to_numpy(dtype=float)).all() or (prices <= 0).any().any():
            raise DataProviderError("Price data must contain finite, positive prices.")

        dividends = dividends.sort_index().reindex(prices.index).fillna(0.0)

        return MarketData(
            prices=prices.astype(float),
            dividends=dividends.astype(float),
        )

    def _extract_metric_frame(
        self,
        frame: pd.DataFrame,
        tickers: list[str],
        metric: str,
        default_value: float | None = None,
    ) -> pd.DataFrame:
        if isinstance(frame.columns, pd.MultiIndex):
            if metric in frame.columns.get_level_values(0):
                values = frame[metric].copy()
            elif default_value is not None:
                values = pd.DataFrame(default_value, index=frame.index, columns=tickers)
            else:
                raise DataProviderError(f"{metric} data is missing in the downloaded data.")
        else:
            if metric in frame.columns:
                values = frame[[metric]].copy()
                values.columns = tickers
            elif default_value is not None:
                values = pd.DataFrame(default_value, index=frame.index, columns=tickers)
            else:
                raise DataProviderError(f"{metric} data is missing in the downloaded data.")

        if isinstance(values, pd.Series):
            values = values.to_frame(name=tickers[0])

        return values.reindex(columns=tickers, fill_value=default_value if default_value is not None else 0.0)
