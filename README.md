# US Portfolio Backtester

FastAPI-based backtesting app for US stock and ETF portfolios.

You can:

- define target portfolio weights
- choose a backtest window
- optionally add a monthly contribution
- rebalance on a calendar schedule or by RSI signals
- optionally auto-reinvest dividend cash
- review summary metrics including CAGR/XIRR and inflation-adjusted real value, the equity curve, holdings, and rebalance events

## Features

- Multi-ticker portfolio input with target weights summing to `100`
- Period input by:
  - explicit `startDate` / `endDate`
  - trailing `lookbackYears`
- Rebalance modes:
  - `calendar`: `monthly`, `quarterly`, `yearly`
  - `rsi`: configurable `rsiPeriod`, `lower`, `upper`
- RSI signal scope:
  - `all`: monitor every portfolio ticker
  - `single`: monitor one selected portfolio ticker only
- Execution options:
  - monthly contribution
  - fractional shares
  - auto dividend reinvestment
  - fee rate
  - slippage rate
- Outputs:
  - summary metrics
  - nominal / real equity curve
  - final holdings snapshot
  - rebalance event history

## Project Structure

```text
app/
  main.py                 # FastAPI app and routes
  schemas.py              # Request/response models
  services/
    backtest.py           # Backtest engine
    data_provider.py      # Yahoo Finance market data loader
    indicators.py         # RSI calculation
  static/
    index.html            # Single-page UI
    style.css
    app.js
tests/
  test_api.py
  test_backtest_service.py
```

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
```

### 2. Activate it

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

CMD:

```bat
.venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install -e .[dev]
```

## Run the Server

```bash
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Usage

1. Enter one or more valid Yahoo Finance tickers.
2. Set target weights so the total is `100`.
3. Enter the initial capital.
4. Optionally enter a monthly contribution amount.
5. Choose a period:
   - date range, or
   - trailing years
6. Choose a rebalance mode:
   - `calendar`
   - `rsi`
7. For `calendar`, choose `monthly`, `quarterly`, or `yearly`.
8. For `rsi`, set:
   - RSI period
   - lower threshold
   - upper threshold
   - RSI signal scope
9. If RSI signal scope is `single`, choose one of the portfolio tickers as the trigger ticker.
10. Optionally configure:
   - fractional shares
   - auto dividend reinvestment
   - fee rate
   - slippage rate
11. Run the backtest and review the result panels.

## API

Endpoint:

```text
POST /api/backtests
```

Example request:

```json
{
  "positions": [
    { "ticker": "SCHD", "targetWeight": 50 },
    { "ticker": "TQQQ", "targetWeight": 50 }
  ],
  "initialCapital": 10000,
  "monthlyContribution": 500,
  "period": {
    "startDate": "2020-01-01",
    "endDate": "2026-04-14"
  },
  "rebalance": {
    "mode": "rsi",
    "rsiPeriod": 14,
    "lower": 30,
    "upper": 70,
    "rsiSignalScope": "single",
    "rsiTriggerTicker": "TQQQ"
  },
  "execution": {
    "fractionalShares": true,
    "dividendReinvestment": true,
    "feeRate": 0.001,
    "slippageRate": 0.0005
  }
}
```

## Behavior Notes

- Position weights must add up to `100`.
- Tickers must be valid Yahoo Finance symbols.
  - Example: `SCHD` is valid.
  - Example: `SHCD` is not.
- When `monthlyContribution > 0`, that cash is added on the first trading day of each month after the starting month.
- Monthly contribution cash is invested immediately by target weights on the contribution day.
- RSI signals are evaluated on the signal-day close, and RSI-triggered rebalances execute on the next trading day.
- Price performance uses Yahoo Finance `Close` data plus explicit dividend cashflows.
- Dividends are always added to portfolio cash.
- When `execution.dividendReinvestment=true`, that new dividend cash is reinvested on the same trading day by target weights.
- When `execution.dividendReinvestment=false`, dividend cash stays in cash until a later rebalance or the end of the backtest.
- Stock splits are not manually re-applied to holdings on top of Yahoo `Close` prices. This avoids split-day value spikes from double-counting.
- When `rebalance.rsiSignalScope` is omitted, it defaults to `"all"`.
- When `rebalance.rsiSignalScope="single"`, `rebalance.rsiTriggerTicker` must match one of the selected portfolio tickers.
- Even in RSI `single` mode, a trigger rebalances the full portfolio back to target weights.
- When `execution.dividendReinvestment` is omitted, it defaults to `true`.
- `summary.totalReturnPct` is calculated against `summary.totalContributed`.
- `summary.cagrPct` is returned only for lump-sum backtests with `monthlyContribution=0`.
- `summary.xirrPct` is returned when the portfolio cash-flow schedule has a valid XIRR solution.
- A fixed 3% annual inflation rate is applied to produce start-date purchasing-power metrics.
- `summary.realFinalValue` and `summary.realTotalReturnPct` are inflation-adjusted values in start-date dollars.
- The chart can be toggled between nominal value and real value using the same date range.

## Tests

Run:

```bash
.\.venv\Scripts\python -m pytest
```

Included coverage:

- request validation
- monthly contribution validation and schedule rules
- calendar rebalance date rules
- RSI threshold-cross execution timing
- single-ticker RSI signal scope behavior
- dividend cash accounting and reinvestment behavior
- monthly contribution investment ordering and trading-cost behavior
- fractional vs integer share behavior
- trading cost impact
- split-day regression coverage for TQQQ-style split dates
- CAGR, XIRR, inflation-adjusted real value, and MDD calculations
- API response shape
