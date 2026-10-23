# Portfolio Dashboard

운영 주소: https://autotrading-nu.vercel.app (Vercel 프로젝트 `beomseodev-9006s-projects/autotrading`). GitHub `beomseodev/autoTrading`의 `main` 변경을 자동 배포합니다.

Google Cloud 프로젝트는 `autotrading-512408`이며 OAuth 콜백은 `http://localhost:8000/auth/callback`과 `https://autotrading-nu.vercel.app/auth/callback`입니다. 운영 환경변수는 Vercel에서 관리하고, 로컬 `.env`와 인증용 CLI 파일은 Git/배포에서 제외합니다. Toss 키는 아직 연결되지 않았습니다.

FastAPI 기반 개인 투자 대시보드입니다. 메인은 실제로 입력한 보유 자산과 시장·거시경제·목표 계획을 보여주며, 과거 전략 비교는 별도 포트폴리오 실험실에서 제공합니다.

## 메인 대시보드

구현 예정 기능은 [종가 기준 관심종목 알림 확장 계획](docs/plans/2026-10-23-stock-alerts.md)에 정리했습니다.

향후 방향 결정 자료는 [추가 서버 없는 국내 해외주식 자동매매 검토](docs/reviews/2026-10-23-serverless-auto-trading.md)에 정리했습니다. 증권사와 실행 방식은 미확정이며 아직 자동주문 기능은 없습니다.
로그인 사용자별 기술 알림 10종과 오전 통합메일의 다음 세션 구현 명세이며, 현재 제공 기능과 구분합니다.

- 각 섹션 오른쪽 아래 `↘`로 크기를 조절하고 왼쪽 아래 `↕`로 순서를 바꿉니다. 두 핸들은 방향키도 지원합니다. 좁은 화면에서는 높이만 조절합니다. 로그인한 계정은 크기·순서·표시 상태를 자동 저장하고, 비로그인 배치는 현재 화면에만 적용합니다. ×로 숨긴 위젯은 **위젯 목록**에서 다시 추가하며 **기본 배치**로 전체 배치를 복원합니다.
- 섹션이 작으면 핵심 수치·점검 상태·비중 상위 항목을 요약하고, 커지면 상세 열·설명·비교 시나리오를 표시합니다. 그래프는 가로·세로 공간에 맞춰 다시 그리며, 크기 조절 때문에 데이터를 재조회하지 않습니다. 가격 기준일과 시세 누락 안내는 요약에서도 유지합니다.
- 보유 표·리밸런싱·자산 구성은 종목명을 우선 표시하고 코드는 보조 정보로 유지합니다. 이름 조회가 실패하면 코드로 표시합니다. 자산 구성 도넛의 각 구간에 마우스를 올리거나 키보드로 선택하면 종목명과 비중을 보여줍니다.
- 목표 진행 막대의 위치 표시와 전망 그래프의 세로선·점·화살표로 현재 자산과 달성률을 표시합니다. 전망 그래프의 현재 표식은 조회한 자산에서 시작하는 위치이며 과거 자산 이력을 나타내지는 않습니다.

- 보유 자산 편집창에서 종목명 또는 코드(예: 삼성전자, 애플, AAPL)를 검색하고 결과를 선택하면 종목 코드가 자동 입력됩니다. 검색은 네이버 증권을 사용하며 한국·미국 종목을 지원합니다. 코드 직접 입력도 가능합니다.

- `/`: 한국·미국 주식·ETF의 종목 코드(코스피: `005930.KS`, 코스닥: `247540.KQ`), 수량, 현지 통화 기준 평균 매입가(한국 KRW / 미국 USD)와 원화·달러 현금을 직접 입력해 보유 현황을 평가합니다. 표시 통화는 KRW/USD를 선택합니다.
- 보유 주식 평가손익은 현재 수량 × (최근 가격 − 평균 매입가)입니다. 거래 통화와 표시 통화가 다르면 현재 환율로 환산하며, 취득 시점 환차손익·실현손익·과거 배당 누계는 포함하지 않습니다.
- 시장 지표: Yahoo Finance의 S&P 500·나스닥·VIX·USD/KRW 최근 일봉과 관측일.
- 거시경제: FRED의 DFF(실효 연방금리), DGS10(10년 국채금리), CPIAUCSL(계절조정 CPI 전년 동월 대비), UNRATE(실업률). 관측일/월과 원자료 링크를 제공합니다. 지표 변화는 직전 유효 관측 대비 %p입니다.
- 목표 계획: 목표 통화·금액·월 추가금·사용자 가정 연 수익률을 입력하면 기준 가정과 ±3%p를 비교합니다. 현재의 완전한 보유 평가액에서 시작하여 월 복리·다음 달 말부터 추가 납입하며, 환율 고정·세금/비용/물가 미반영입니다. 이미 달성 또는 60년 내 미도달을 구분합니다.
- 보유와 목표는 Google 계정별 비공개 서버 저장소에 저장하고 브라우저에도 `portfolio-dashboard-v1:<Google sub>`로 백업합니다. 복구 원본도 계정별 키로 보관하며, 다른 계정의 데이터는 불러오지 않습니다. 서버 저장 설정 전에는 해당 계정의 브라우저 백업만 사용합니다.
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

### 테스트 실행

```powershell
.venv/Scripts/python -m pytest -q
node --test tests/*.cjs
node tests/browser/dashboard.cjs
node tests/browser/loading.cjs
```

Node 테스트는 계정 분리, 종목명, 그룹 리밸런싱, 위젯 배치, 목표 현재 위치,
수익률 순위, 로딩 상태별 파일로 나뉩니다. 브라우저 검사는 Windows의 Google Chrome을
사용하며, 모의 API 응답으로 동작하므로 실제 계정이나 시세 서비스가 필요하지 않습니다.
화면 확인용 이미지는 `output/`에 저장합니다.

- 대시보드 첫 진입·조회·검색과 실험실 분석 중에는 회전 아이콘과 진행 문구를 표시합니다.
  동시 요청이 모두 끝나거나 실패하면 로딩 상태를 해제하며, 동작 줄이기 설정을 따릅니다.

### 수동 포트폴리오 저장과 리밸런싱 이메일

- 대시보드 위젯의 ↕ 핸들을 드래그하거나 위아래 방향키로 순서를 바꾸고, ↘ 핸들로 크기를 조절합니다. ×는 위젯을 숨기며 **위젯 목록**에서 다시 표시할 수 있습니다. 숨겨도 보유 자산·목표 데이터는 삭제되지 않습니다. 로그인 상태에서는 배치 변경이 자동 저장되며, 저장 실패 시 **저장 재시도**가 표시됩니다. 비로그인은 현재 화면에서만 유지됩니다. **기본 배치**는 원래 순서·크기·표시 상태로 복원합니다.
- 추가 위젯 **종목 수익률 순위**는 위젯 목록에서 켤 수 있습니다. 평균 매입가 대비 평가수익률이 높은 순서로 보여주며, 시세나 매입가가 없어 계산할 수 없는 종목은 제외 개수를 표시합니다. 별도 시세 요청은 하지 않습니다.

- 보유 자산 편집의 **그룹 추가**에서 보유 종목을 2개 이상 선택하고 합산 목표를 설정할 수 있습니다. 예: QQQI + SCHD 60%. 그룹 구성 종목의 개별 목표는 비워두며, 한 종목은 한 그룹에만 포함됩니다. 그룹 내부 비율은 점검하지 않습니다. 개별 목표와 그룹 목표의 합계에서 남은 비중은 현금입니다.

- 보유 수량·매입가·현금·목표 비중은 로그인한 본인의 비공개 Vercel Blob에 저장합니다. 집·회사·모바일에서 같은 정보를 불러옵니다. 공개 GitHub에는 금융 데이터를 저장하지 않습니다.
- 기존 브라우저 자산은 자동 업로드하지 않습니다. 서버에 정보가 없을 때 **이 브라우저의 자산·목표를 계정에 저장**을 누르거나 편집 후 저장합니다. 기존 로컬 값은 `portfolio-dashboard-v1-before-cloud`에 한 번 보관합니다. 다른 기기의 수정과 충돌하면 저장을 거절하고 입력을 유지합니다.
- 각 종목의 목표 비중 합계는 100% 이하이며 남은 비중은 현금 목표입니다. 허용폭은 상대 퍼센트가 아닌 **퍼센트포인트(%p)**입니다. 목표 30%, 허용폭 5%p라면 25% 미만 또는 35% 초과가 점검 대상입니다. 조정금액은 현재 평가액과 목표액의 차이이며 세금·수수료·정수주 거래를 반영하지 않습니다.
- `vercel.json`의 `0 1 * * *`는 한국 시간 오전 10시대에 하루 한 번 점검합니다. Hobby에서는 분 단위 실행·성공·메일 도착을 보장하지 않습니다. UTC 자정 기준이 아닌 시세 기준일별로 발송 시도를 한 번만 허용하여 주말의 동일 시세 알림을 반복하지 않습니다.
- 같은 통화의 종목은 시세 날짜가 같아야 하고, 한국·미국 시장 간에는 최대 하루 차이를 허용합니다. 시세와 필요한 환율이 4일보다 오래되거나 미래 날짜·누락이면 보류하며, 시세 날짜 차이도 화면/메일에 표시합니다. **shortcut:** 거래소 휴장 달력 대신 보수적인 날짜 검사를 사용합니다. 장기 연휴의 점검 누락이 문제가 되면 정식 거래소 달력을 도입합니다.
- 이메일은 `GOOGLE_OWNER_EMAIL`로만 보내며 총 평가액·계좌번호를 넣지 않습니다. 기존 Google 로그인만으로 발송 권한이 생기지는 않습니다. 본인 Gmail의 2단계 인증 후 앱 비밀번호를 만들고 서버의 `GMAIL_APP_PASSWORD`에 등록해야 합니다. **본인에게 테스트 메일 보내기**로 수신 확인 후 보유 자산 편집에서 이메일 알림을 켭니다. 메일이 필요 없으면 끌 수 있습니다.
- 필수 서버 설정: `BLOB_READ_WRITE_TOKEN`(반드시 **private** 저장소), 32자 이상 무작위 `CRON_SECRET`. `GMAIL_APP_PASSWORD` 미설정 시 화면 점검·계정 저장은 가능하지만 메일은 비활성입니다. Cron Bearer 키는 `/api/cron/rebalance`에만 유효하고 계정 데이터 API 권한은 주지 않습니다.
- 최신 읽기는 Blob의 `cache=0`, 동시 수정 방지는 `x-if-match` 조건부 쓰기를 사용합니다. 포트폴리오와 발송 기록은 별도 파일입니다. **shortcut:** SMTP 전송과 저장소 기록을 하나로 묶을 수 없어 모호한 실패는 같은 시세일에 자동 재전송하지 않습니다. 메일 서버 접수와 실제 받은편지함 배달은 구분됩니다. 확실한 재시도가 필요하면 idempotency를 제공하는 발송 서비스로 전환합니다.
- Vercel Hobby 무료 한도를 넘으면 기능이 중단될 수 있으며 자동 유료 업그레이드는 하지 않습니다. Toss 조회는 제거하고 수동 입력을 사용합니다. 실제 거래 후 보유 수량을 갱신하지 않으면 알림도 이전 수량 기준입니다.

참고: [Vercel Cron 무료 한도](https://vercel.com/docs/cron-jobs/usage-and-pricing), [Blob 무료 한도](https://vercel.com/docs/vercel-blob/usage-and-pricing), [Gmail 앱 비밀번호](https://support.google.com/accounts/answer/185833?hl=ko).

### Google 로그인

대시보드·종목 검색·시세 평가·목표 계산·실험실은 로그인 없이 이용할 수 있습니다. 비로그인 입력은 현재 페이지 메모리에만 유지하며 서버·브라우저 저장소에 저장하거나 기존 계정의 백업을 불러오지 않습니다. 새로고침·로그인·페이지 이동 시 초기화됩니다. 계정 저장·조회와 이메일 기능은 로그인이 필요하며, OAuth 토큰 검증·서명 쿠키·요청 Origin 검사는 유지합니다.

1. `.env.example`을 `.env`로 복사하고 `APP_BASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`을 설정합니다.
2. Google Cloud Console에서 외부용 OAuth 동의 화면과 웹 클라이언트를 설정합니다. 현재 요청 범위는 `openid email`뿐이며, Google의 기본 신원 범위 예외에 따라 테스트 모드에서도 테스트 사용자 명단 제한이 적용되지 않습니다. 내부 조직 전용 앱이나 조직 관리자의 접근 정책은 별도입니다. 운영 서비스의 게시 상태는 콘솔에서 관리합니다.
3. 승인된 리디렉션 URI에 `http://localhost:8000/auth/callback` 및 운영 주소의 `/auth/callback`을 등록합니다.
4. `SESSION_SECRET`은 `python -c "import secrets; print(secrets.token_urlsafe(48))"`로 생성하고 모든 서버 인스턴스에서 같은 32자 이상 값을 사용합니다.
5. `GOOGLE_OWNER_EMAIL` 및 선택적인 `GOOGLE_OWNER_SUB`는 운영자 전용 메일과 기존 자산 데이터 식별용입니다. 일반 로그인에는 필수가 아닙니다.

서버 포트폴리오는 검증된 Google `sub`의 SHA-256 경로로 분리하고, 기존 운영자 데이터와 메일 작업만 기존 전용 경로를 사용합니다. 다른 계정은 운영자 데이터·메일 상태를 조회하거나 알림을 발송할 수 없습니다. 브라우저 저장·복구 백업도 `sub`별로 분리하며, 기존 공통 브라우저 데이터는 운영자에게만 이전합니다. 계정 확인 전에는 저장된 자산을 읽지 않습니다.

Toss 계좌 조회 API와 화면은 제거했습니다. 매수·자동 주문 기능은 없습니다. 시세·분석에는 사용자별 일일 할당량을 두지 않고 Vercel 방화벽의 IP별 과다 호출 제한을 사용합니다. 방화벽 규칙은 배포 코드와 별도로 게시해야 하며 운영 설정은 `output/public-access-firewall.json`으로 확인합니다.

세션 쿠키는 서명되지만 암호화되지 않으므로 Google 비밀번호·액세스 토큰·자산 내역은 넣지 않습니다. 세션 최대 수명은 8시간입니다. 전체 로그아웃이 필요하면 `SESSION_SECRET`을 교체합니다.

공식 문서: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect), [Vercel WAF 요청 제한](https://vercel.com/docs/vercel-firewall/vercel-waf/rate-limiting).

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
