# US Portfolio Backtester

미국 주식/ETF 포트폴리오를 대상으로 목표 비중, 투자금, 백테스트 기간, 리밸런싱 기준을 입력해 성과를 확인하는 FastAPI 프로젝트입니다.

## 기능

- 티커와 목표 비중 입력
- 초기 투자금과 백테스트 기간 입력
- 정기 리밸런싱(`monthly`, `quarterly`, `yearly`) 지원
- RSI 기반 리밸런싱(`lower`, `upper`, `rsiPeriod`) 지원
- 거래 수수료, 슬리피지, 소수점 주식 옵션 지원
- 결과 요약, 자산곡선, 최종 보유 비중, 리밸런싱 이벤트 제공

## 프로젝트 구조

```text
app/
  main.py                 # FastAPI 앱과 라우팅
  schemas.py              # 요청/응답 스키마
  services/
    backtest.py           # 백테스트 엔진
    data_provider.py      # Yahoo Finance 데이터 로더
    indicators.py         # RSI 계산
  static/
    index.html            # 단일 페이지 UI
    style.css
    app.js
tests/
  test_api.py
  test_backtest_service.py
```

## 개발 환경 구성

### 1. 가상환경 생성

```bash
python -m venv .venv
```

### 2. 가상환경 활성화

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Windows CMD:

```bat
.venv\Scripts\activate.bat
```

### 3. 의존성 설치

```bash
pip install -e .[dev]
```

## 서버 실행

```bash
uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`으로 접속하면 됩니다.

## 사용 방법

1. 포지션 영역에 티커와 목표 비중을 입력합니다.
2. 목표 비중 합계가 100%인지 확인합니다.
3. 초기 투자금(USD)을 입력합니다.
4. 기간 입력 방식을 선택합니다.
5. `시작일/종료일` 또는 `최근 N년` 중 하나로 기간을 입력합니다.
6. 리밸런싱 모드를 선택합니다.
7. `정기 리밸런싱`이면 월간/분기/연간 중 하나를 고릅니다.
8. `RSI 리밸런싱`이면 RSI 기간, 하단 기준, 상단 기준을 입력합니다.
9. 필요하면 수수료율, 슬리피지율, 소수점 주식 허용 여부를 조정합니다.
10. `백테스트 실행` 버튼을 누릅니다.
11. 결과 영역에서 최종 금액, 총수익률, CAGR, MDD, 자산곡선, 최종 보유 현황, 리밸런싱 이벤트를 확인합니다.

## API 사용 예시

엔드포인트:

```text
POST /api/backtests
```

예시 요청:

```json
{
  "positions": [
    { "ticker": "AAPL", "targetWeight": 40 },
    { "ticker": "MSFT", "targetWeight": 35 },
    { "ticker": "QQQ", "targetWeight": 25 }
  ],
  "initialCapital": 10000,
  "period": {
    "startDate": "2020-01-01",
    "endDate": "2024-12-31"
  },
  "rebalance": {
    "mode": "rsi",
    "rsiPeriod": 14,
    "lower": 30,
    "upper": 70
  },
  "execution": {
    "fractionalShares": true,
    "feeRate": 0.001,
    "slippageRate": 0.0005
  }
}
```

주요 규칙:

- 비중 합계는 100이어야 합니다.
- RSI 신호는 당일 종가로 판정하고, 실제 리밸런싱은 다음 거래일 가격으로 체결합니다.
- 데이터는 Yahoo Finance 조정종가 기준입니다.
- 리밸런싱 모드는 한 번의 실행에 하나만 선택합니다.

## 테스트 실행

```bash
.\.venv\Scripts\python -m pytest
```

현재 포함된 테스트:

- 요청 검증 테스트
- 정기 리밸런싱 날짜 규칙 테스트
- RSI 임계값 돌파 후 다음 거래일 체결 테스트
- 소수점/정수 주식 차이 테스트
- 거래 비용 반영 테스트
- CAGR / MDD 계산 테스트
- API 응답 구조 테스트
