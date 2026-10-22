# Portfolio Dashboard

FastAPI 기반 개인 투자 대시보드입니다. 메인은 실제로 입력한 보유 자산과 시장·거시경제·목표 계획을 보여주며, 과거 전략 비교는 별도 포트폴리오 실험실에서 제공합니다.

## 메인 대시보드

- `/`: 미국 주식·ETF의 종목 코드, 수량, USD 평균 매입가와 원화·달러 현금을 직접 입력해 보유 현황을 평가합니다. 표시 통화는 KRW/USD를 선택합니다.
- 보유 주식 평가손익은 현재 수량 × (최근 가격 − 평균 매입가)입니다. 원화 표시에서는 USD 손익을 현재 환율로 환산하며, 취득 시점 환차손익·실현손익·과거 배당 누계는 포함하지 않습니다.
- 시장 지표: Yahoo Finance의 S&P 500·나스닥·VIX·USD/KRW 최근 일봉과 관측일.
- 거시경제: FRED의 DFF(실효 연방금리), DGS10(10년 국채금리), CPIAUCSL(계절조정 CPI 전년 동월 대비), UNRATE(실업률). 관측일/월과 원자료 링크를 제공합니다. 지표 변화는 직전 유효 관측 대비 %p입니다.
- 목표 계획: 목표 통화·금액·월 추가금·사용자 가정 연 수익률을 입력하면 기준 가정과 ±3%p를 비교합니다. 현재의 완전한 보유 평가액에서 시작하여 월 복리·다음 달 말부터 추가 납입하며, 환율 고정·세금/비용/물가 미반영입니다. 이미 달성 또는 60년 내 미도달을 구분합니다.
- 보유와 목표는 이 브라우저의 `portfolio-dashboard-v1`에 저장합니다. 새로고침과 실험실 이용 시 보존됩니다. 서버 저장·계좌 연동·기기 동기화는 없으며, 사이트 데이터를 삭제하면 지워집니다. 손상된 저장값은 자동 삭제하지 않고, 사용자가 새 저장을 실행할 때 `portfolio-dashboard-v1-recovery`에 원본을 보관합니다.
- 시세가 일부라도 없으면 총자산·목표 날짜를 계산하지 않습니다. 시세 캐시는 5분, 거시 캐시는 30분이며 자동 고빈도 폴링은 하지 않습니다. 각 숫자의 관측일은 조회 시각과 다를 수 있습니다.

메인 API: `GET /api/dashboard/markets`, `POST /api/dashboard/portfolio`, `POST /api/dashboard/goal`. 기존 백테스트·비교·위험 분석 API는 유지합니다.

## 포트폴리오 실험실 사용 흐름

실험실은 `/lab`에서 엽니다. 실제 보유 저장정보와 독립된 가상 실험입니다.

데스크톱은 왼쪽 설정과 오른쪽 성과 대시보드를 함께 보여줍니다. 여섯 지표, 자산 추이, 종료일 구성, 보유 내역과 최근 거래 기록을 대조할 수 있습니다. 모바일에서는 같은 입력과 결과를 한 열로 표시합니다. 자세한 계산 기준과 시작 안내는 상단 링크에서 펼칠 수 있습니다.

1. 종목과 목표 비중을 입력하고 비중 막대에서 합계 100%를 확인합니다.
2. 시작 투자금·매월 추가 투자금·기간을 정합니다. 리밸런싱과 거래·배당 옵션은 펼쳐서 변경할 수 있습니다.
3. **분석하기** 또는 **S&P 500 ETF와 비교**를 누릅니다. 후자는 같은 기간·자금·거래 조건의 SPY 100% 포트폴리오와 비교하며, SPY는 연간 정기 리밸런싱·추가 TER 0을 사용합니다. 실제 지수 수익률과는 다릅니다.
4. 실제 거래일과 총 납입금, 평가금액·손익·연환산 수익률·최대 낙폭을 확인합니다. 종료일 구성에는 잔여 현금도 표시합니다.

결과는 USD 기준 가상 실험입니다. 입력을 변경하면 기존 결과임을 표시합니다. 두 종목 위험 분석은 별도 분석이며, 초기 비중 매수·보유와 배당 재투자를 가정합니다.

처음 사용하는 경우 화면 상단의 **처음이라면 · 3단계로 시작하기**를 펼치세요. 결과의 **이 숫자는 어떻게 계산하나요?**에서 납입금·손익·XIRR·CAGR·MDD·물가 조정의 계산식과 예시를 확인할 수 있습니다. 두 종목 분석에는 변동성·상관계수·총수익 지수 설명도 있습니다.

비교 대상의 실제 거래일이 다르거나 분석 기간 중 시세가 누락되면 오류를 표시합니다. 모든 종목에 데이터가 있는 기간으로 조정하세요. MDD는 추가 납입의 영향을 제거한 일별 수익 지수로 계산합니다. 밴드 이탈은 종가에 확인하고 다음 거래일 종가에 실행합니다. 정수주 매매와 추가 TER 차감의 동시 사용은 지원하지 않습니다.

더리치의 [백테스팅](https://www.therich.io/backtesting)과 [공개 예시 포트폴리오](https://www.therich.io/my/portfolios)에서 입력·결과 분리와 자산 구성 시각화를 참고했습니다. 계좌 연동·실보유 관리·미래 배당 예측은 포함하지 않습니다.

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

### Google 본인 전용 로그인 · Toss 계좌 조회

페이지와 모든 `/api/*`는 로그인이 필요합니다. 설정 누락 시 접근을 허용하지 않습니다.

1. `.env.example`을 `.env`로 복사합니다. `.env`는 Git에서 제외됩니다.
2. Google Cloud Console에서 OAuth 동의 화면을 설정하고 **웹 애플리케이션** OAuth 클라이언트를 만듭니다. 테스트 모드라면 본인 계정을 테스트 사용자로 추가합니다.
3. 승인된 리디렉션 URI에 `http://localhost:8000/auth/callback`을 등록합니다. 로컬 접속도 `http://localhost:8000`을 사용합니다.
4. `.env`에 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OWNER_EMAIL`을 입력합니다. 허용된 **검증된 이메일**만 로그인할 수 있습니다. Gmail 계정 사용을 권장하며, 첫 로그인 후 `/auth/session`에서 본인의 `sub`를 확인해 `GOOGLE_OWNER_SUB`에도 지정하면 계정 ID까지 고정됩니다.
5. `python -c "import secrets; print(secrets.token_urlsafe(48))"`로 생성한 값을 `SESSION_SECRET`에 넣습니다. 32자 이상이며 재시작·Vercel 인스턴스마다 같은 값을 사용해야 합니다.
6. 기존 Toss 키를 `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`에 입력합니다. 키는 브라우저에 전달하지 않습니다. 참조 스크립트는 `C:/dev/etc/toss_holdings.py`이며 그 폴더나 자격증명 파일을 배포에 포함하지 않습니다.
7. 의존성을 설치하고 기존 실행 스크립트로 서버를 시작합니다. Google로 로그인한 뒤 **Toss 계좌 조회**를 누릅니다.

Google 로그인은 본인 확인용이며 Toss 계좌 인증과는 별개입니다. Toss 연동은 토큰 발급·계좌 목록·보유 주식 조회만 수행합니다. 주문은 구현하지 않았습니다. 가능하면 조회 전용 권한의 키를 사용합니다. 계좌 번호와 토큰은 결과에 포함하지 않습니다. 일부 계좌가 실패하면 전체 조회를 오류로 처리하여 불완전한 금액을 정상 자산으로 표시하지 않습니다.

Toss 내역은 별도 읽기 전용 구역에 표시하며 기존 수동 입력·현금·목표·복구 데이터는 변경하지 않습니다. Toss 현금, 미래에셋 등 타 증권사 자산은 포함하지 않고, 수동 자산과 중복 합산하지 않습니다. 수동 자산·목표는 여전히 localStorage에 저장되며 로그인만으로 기기 간 동기화되지 않습니다. 도메인이나 포트를 바꾸면 기존 브라우저 저장값은 자동 이동하지 않습니다.

Vercel에서는 동일한 변수들을 서버 환경변수로 등록하고 `APP_BASE_URL=https://실제도메인` 및 해당 도메인의 `/auth/callback`을 Google에 등록합니다. 비밀값에는 `NEXT_PUBLIC_` 같은 공개 접두사를 붙이지 않습니다. 설정 변경 후 재배포/재시작합니다. HTTPS에서는 Secure·HttpOnly·SameSite=Lax 쿠키를 사용합니다. 세션은 8시간 비활성 후 만료되며 로그아웃은 현재 브라우저 쿠키를 삭제합니다. **shortcut:** 서버 저장소 없는 서명 쿠키이므로 유출된 쿠키의 개별 강제 폐기는 지원하지 않습니다. 즉시 폐기가 필요하면 `SESSION_SECRET`을 교체해 모든 세션을 종료하고, 다중 사용자/개별 세션 폐기가 필요해질 때 서버 세션 저장소를 도입합니다.

공식 문서: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect), [Toss API](https://developers.tossinvest.com/docs), [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi).

### Windows (PowerShell) — 권장 스크립트

저장소 루트에서 한 번만 실행합니다. Python 3.13+ 와 (선택) `py` 런처가 PATH에 있어야 합니다.

```powershell
.\scripts\setup.ps1
```

실행 정책 오류가 나면:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

가상환경 활성화(수동으로 할 때):

```powershell
.\.venv\Scripts\Activate.ps1
```

### WSL (Ubuntu 등)

저장소를 `/mnt/c/...` 또는 WSL 홈에 둔 뒤, WSL 터미널에서:

```bash
chmod +x scripts/*.sh
./scripts/setup_wsl.sh
# /mnt/c 등에서 실행 비트가 안 붙으면: bash ./scripts/setup_wsl.sh
source .venv/bin/activate
```

`python3` 와 `python3-venv` 가 없으면:

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

### 수동 셋업 (공통)

```bash
python -m venv .venv
```

PowerShell에서 활성화:

```powershell
.\.venv\Scripts\Activate.ps1
```

CMD:

```bat
.venv\Scripts\activate.bat
```

의존성 설치:

```bash
pip install -e .[dev]
```

## Run the Server

PowerShell:

```powershell
.\scripts\run.ps1
```

WSL:

```bash
./scripts/run_wsl.sh
```

또는 가상환경을 켠 뒤:

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

PowerShell:

```powershell
.\scripts\test.ps1
```

WSL:

```bash
./scripts/test_wsl.sh
```

또는:

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
