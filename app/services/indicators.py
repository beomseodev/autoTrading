from __future__ import annotations

import pandas as pd


def compute_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = average_gain / average_loss
    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.where(average_loss != 0, 100.0)
    rsi = rsi.where(~((average_gain == 0) & (average_loss == 0)), 50.0)
    return rsi

