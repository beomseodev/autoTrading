# 종가 관심종목 알림 운영

`/alerts`에서 로그인한 사용자가 관심종목과 10종 조건을 저장한다. 200일 SMA 양방향 돌파, 50일·200일선 교차, 지정가격, 일간 급등락, RSI, 기간 신고저가, 거래량 급증, MACD, 볼린저밴드, 낙폭 진입을 지원한다. 자동 주문과는 별도 기능이다.

## 개발 완료와 운영 활성화 구분

평가 엔진·계정별 API·화면·PostgreSQL 원장·재개 실행·Resend 전송·해지·웹훅 경로가 구현되어 있다. 실공급자 계약·종목 커버리지, 발신 도메인, 운영 DB와 요금제 준비는 별도다. 현재 실제 발송은 기본 OFF이며 이 문서만으로 운영 검증이 완료된 것은 아니다.

기존 `vercel.json`의 일일 Cron은 그대로 둔다. 실제 운영 플랜이 확인되지 않은 상태에서 분 Cron을 기본 배포에 넣어 Hobby 배포를 실패시키지 않기 위해, 활성화용 설정을 `vercel.stock-alerts.json`에 분리했다. Pro 확인 후 기존 파일에 해당 stock-alerts Cron을 반영하거나 Vercel CLI의 `--local-config vercel.stock-alerts.json`으로 배포한다. 활성화 전까지 기본 배포에는 새 자동 점검 일정이 없다.

## 준비 순서

1. PostgreSQL DB와 `.env.example`의 `ALERT_DATABASE_URL`을 설정한다. 운영은 TLS pooled URL, 마이그레이션은 TLS direct URL을 사용한다.
2. 저장소 루트에서 `.venv/Scripts/python -m scripts.migrate_alerts`를 실행한다. `ALERT_MIGRATION_DATABASE_URL`이 필요하다. 번호별 SQL 체크섬을 검사하며 앱 시작 시 자동 적용하지 않는다.
3. Twelve Data의 한국·미국 주식·ETF 및 외부 이메일 알림 이용 계약과 개별 종목 커버리지를 확인한다. API 키와 실제 분당 credit 한도를 설정한다. 한 종목 일봉 및 분할 조회는 합계 21 credits이므로 최소 그 이상의 분당 quota가 필요하다. 검색도 credits를 사용한다.

   21 credits는 단일 조회의 최소 조건이며 100종목을 08시대에 두 번 확인할 처리량 보장이 아니다. 대상 수·5분 재개 간격·확인 대기를 포함해 계약 quota와 실제 응답 지연으로 별도 shadow 검증한다. 아래 부하 검사는 공급자 네트워크와 실제 credit 소모를 가짜 응답으로 대체한다.
4. Resend 발신 도메인과 SPF/DKIM, `RESEND_API_KEY`, `ALERT_EMAIL_FROM`을 준비한다. HTTPS `APP_BASE_URL`과 별도 32자 이상 `ALERT_UNSUBSCRIBE_SECRET`을 설정한다.
5. Resend 웹훅을 `/api/webhooks/alert-email`로 연결하고 `RESEND_WEBHOOK_SECRET`을 설정한다. delivered, bounced, complained, suppressed를 수신한다.
6. `STOCK_ALERTS_SEND_ENABLED=false`로 배포하고 계정 격리·관심종목·조건·해지를 확인한다. OFF Cron은 캐시 기반 평가 집계만 반환하며 운영 run/task/event/digest나 규칙 커서를 만들거나 전진시키지 않는다. 신규 캐시가 없으면 자료 준비 중으로 나온다.
7. 아래 opt-in 시세 검사로 AAPL, SCHD, QQQI, 005930.KS, 247540.KQ와 한국 ETF를 대조한다. 실제 봉 날짜·거래량·분할·서머타임·휴장 자료를 검증한다. 이 검사는 credits 원장과 공급자 읽기를 사용하지만 규칙 커서나 발송 원장을 변경하지 않는다.
8. Vercel Pro와 300초 실행 설정을 확인한 뒤 5분 Cron을 적용한다. `CRON_SECRET`은 기존 예약 인증과 공유하되 정확한 두 Cron 경로만 허용한다.
9. 동의한 소수 계정으로 발송을 켜고 테스트 메일·통합메일·수신 해지·반송 처리를 검증한다. 실제 메일은 화면의 본인 테스트 버튼으로 요청하며 하루 1회 제한이다. 기능이 준비됐다고 과거 로그인 사용자를 자동 구독하지 않는다.

```powershell
.venv/Scripts/python -m scripts.check_stock_alert_data --live --symbol SCHD
```

이 CLI는 운영 공급자에 읽기 요청을 보내므로 데이터 계약 및 비용 범위를 확인한 뒤 실행한다. 키·주소·원문 자료를 로그에 출력하지 않는다.

## 데이터와 규칙 의미

- 현지 정규장이 오전 8시 KST보다 90분 이상 전에 끝난 최신 거래일을 기대 세션으로 사용한다. 최신 봉 누락은 과거 봉으로 대체하지 않는다.
- 확정 플래그에 의존하지 않고 같은 스냅샷을 5분 이상 간격으로 두 번 확인한다. 사후 정정 가능성은 남으며 바뀐 기존 신호는 이력에 정정으로 표시하고 자동 재발송하지 않는다.
- raw 가격을 받아 분할만 보정하고 현금배당은 재투자하지 않는다. Twelve Data의 split `ratio`는 과거 가격에 곱하는 값으로 취급한다. 4-for-1 fixture의 .25는 가격 ×.25, 거래량 ÷.25다. 실제 공급 응답을 출시 전 반드시 대조한다.
- 등록·재개·조건 변경 이전에 종료된 세션과 정지 기간은 소급하지 않는다. 자동 소급은 최근 5개 시장 세션으로 제한한다.
- 삭제는 보관이다. 동일 조건 재등록은 화면에서 명시적으로 ‘보관된 동일 조건 재사용’을 선택해야 하며 새 의미 버전과 적용 시점을 갖는다.
- 지정가격은 분할 후 정지하고 가격 검토와 재개를 요구한다. 가격을 자동 변경하지 않는다.
- 거래소 개장일의 봉 누락은 자료 보류로 처리한다. 실제 거래정지로 확인한 날만 `config/alert_trading_halts.json`에 `{"005930.KS":[{"date":"YYYY-MM-DD","source":"https://공식근거"}]}` 형태로 운영자가 등록할 수 있다. 근거 없는 누락 제외나 forward-fill은 금지한다. 거래정지 정보를 자동 제공하는 별도 공급 연동은 없다.

## 발송과 복구

오전 8시대에 같은 일일 run을 재개한다. 활성 조정자는 DB lease로 하나만 허용하고 결과 저장 전 token을 검사한다. 규칙 평가와 이벤트·진행 기록은 같은 트랜잭션으로 저장한다. 08:45부터 미완료 자료를 보류로 마감하고 09:00 이후 신규 전송하지 않는다.

메일은 사용자·발송일당 한 통이며 최초 전송 후 수신자·본문·멱등 키가 고정된다. 설정·이메일·규칙이 바뀌면 전송 직전 재확인하여 취소한다. 공급자 접수는 수신함 도착과 다르다. HTTP timeout과 5xx는 결과불명으로 보고 같은 키를 유지한다. 429는 Retry-After를 존중한다. 24시간이 지난 불명 요청은 새 키로 자동 재전송하지 않는다.

이전 발송 창에서 명백히 미접수인 메일은 마감 상태로 전환한 뒤 5세션 범위에서 다음 날 통합할 수 있다. 결과불명·접수·배달된 이벤트는 새 키로 보내지 않는다. 이미 접수된 한 통은 해지 후 도착할 수 있다. GET 해지 링크는 확인 화면만 표시하고 POST로 동의를 취소한다.

운영자 수신 동의와 실제 신규 발송 활성화 후 legacy 전환 표식을 영구 보존한다. 기존 10시 메일은 별도 전송을 중지하며, 신규 수신 해지나 알림 데이터 삭제 후에도 다시 살아나지 않는다. 전환 조회 DB 장애 시 기존 메일은 보류한다. 롤백 시 전환 표식을 모르는 옛 Cron을 먼저 중지한다.

## 검사 명령

단위·API 회귀는 `.venv/Scripts/python -m pytest -q`로 실행한다. DB가 없으면 PostgreSQL 통합 검사는 skip으로 표시되며 통과로 간주하지 않는다. 실제 네트워크·메일 요청은 단위 테스트에 없다.

```powershell
# 다른 데이터가 없는 alerts_test 전용 DB만 사용한다. 테스트가 테이블을 비운다.
$env:ALERT_TEST_DATABASE_URL='postgresql://USER:PASSWORD@127.0.0.1:55432/alerts_test'
.venv/Scripts/python -m pytest tests/test_stock_alert_api.py tests/test_stock_alert_jobs.py tests/test_stock_alert_data.py -q
node tests/browser/stock-alerts.cjs

# 시세·메일은 고정 가짜 응답, 저장·경합은 실제 PostgreSQL
$env:ALERT_RUN_LOAD_TEST='1'
.venv/Scripts/python -m pytest tests/test_stock_alert_load.py -q
```

Chrome 검사는 Windows의 기본 Chrome 실행 경로를 사용한다. 화면 캡처와 부하 결과는 `output/`에만 남기며 커밋하지 않는다. 운영 환경의 네트워크 지연과 공급자 quota는 로컬 부하 결과로 대체하지 않는다.

## 운영 확인과 중단

사용자는 `/alerts`에서 최신 판정·신호 이력·일별 접수 결과와 보류 수를 확인한다. 운영자는 일일 run, task 상태/시도/안전한 오류 코드, digest 결과불명·반송을 DB 또는 Cron 집계 로그로 점검한다. 수신주소와 본문을 일반 로그에 출력하지 않는다.

사건/본문은 180일, 중복 방지 키는 400일 보존 후 정리한다. 사용자 알림 삭제는 관심종목·규칙·이벤트·메일 본문과 주소를 삭제하고 legacy 전환용 최소 사용자 표식을 보존한다. 공유 시세와 기존 Blob 자산은 삭제하지 않는다.

문제가 생기면 `STOCK_ALERTS_SEND_ENABLED=false`로 변경하고 신규 Cron을 중지한다. 원장과 기존 자산은 보존한다. unknown은 Resend 로그에서 기존 멱등 키·접수ID로 확인한 뒤 운영자가 판단한다. 새 키로 무조건 재전송하지 않는다.
