from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import yfinance as yf


class DataProviderError(RuntimeError):
    pass


class YFinanceDataProvider:
    def fetch_adjusted_close(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        end_exclusive = end_date + timedelta(days=1)
        frame = yf.download(
            tickers=tickers,
            start=start_date.isoformat(),
            end=end_exclusive.isoformat(),
            auto_adjust=False,
            progress=False,
            actions=False,
            group_by="column",
            threads=True,
        )

        if frame.empty:
            raise DataProviderError("No price data returned from Yahoo Finance.")

        if isinstance(frame.columns, pd.MultiIndex):
            if "Adj Close" in frame.columns.get_level_values(0):
                prices = frame["Adj Close"].copy()
            elif "Close" in frame.columns.get_level_values(0):
                prices = frame["Close"].copy()
            else:
                raise DataProviderError("Adjusted close prices are missing in the downloaded data.")
        else:
            column_name = "Adj Close" if "Adj Close" in frame.columns else "Close"
            prices = frame[[column_name]].copy()
            prices.columns = tickers

        if isinstance(prices, pd.Series):
            prices = prices.to_frame(name=tickers[0])

        prices = prices.loc[:, tickers]
        prices.index = pd.to_datetime(prices.index).tz_localize(None)
        prices = prices.sort_index().dropna(how="any")

        if prices.empty:
            raise DataProviderError("Price data is empty after aligning tickers on common trading days.")

        return prices.astype(float)

