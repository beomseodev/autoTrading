# 관심종목 알림 운영

`/alerts`에서 로그인한 사용자가 관심종목과 19종 조건을 저장한다. 기존 10종 기술 조건(200일 SMA 양방향 돌파, 이동평균 교차, 지정가격, 일간 급등락, RSI, 기간 신고저가, 거래량 급증, MACD, 볼린저밴드, 낙폭 진입)에 AND, 고정가격 재접근, 상대 수익률, 저장 보유자산 수익률·비중, 실적·배당 일정·배당 변경, 공시를 지원한다. 이메일·웹 푸시의 동의와 일간·주간 빈도는 독립적이다. 자동 주문과는 별도 기능이다. 최신 기능별 검증은 [실행 기록](plans/2026-10-25-alert-extensions.md)을 참고한다.

## 개발 완료와 운영 활성화 구분

운영자 본인 전용 Gmail 모드에서는 `ALERT_EMAIL_TRANSPORT=gmail`과 기존 `GOOGLE_OWNER_EMAIL`·`GMAIL_APP_PASSWORD`를 사용한다. 기존 리밸런싱 메일은 별도로 유지한다. `.github/workflows/stock-alerts.yml`은 GitHub Actions의 `ALERT_CRON_SECRET`과 Vercel의 같은 이름의 비밀값으로 한국시간 오전 8시대에 5분마다 점검을 요청한다. GitHub 예약은 지연·누락될 수 있으며 SMTP 접수 여부가 불확실하면 중복 방지를 위해 재발송하지 않는다. Gmail에는 반송 웹훅이나 수신 확인이 없으므로 받은 편지함에서 확인한다.

평가 엔진·계정별 API·화면·PostgreSQL 원장·재개 실행·Resend 전송·해지·웹훅 경로가 구현되어 있다. 실공급자 계약·종목 커버리지, 발신 도메인, 운영 DB와 요금제 준비는 별도다. 현재 실제 발송은 기본 OFF이며 이 문서만으로 운영 검증이 완료된 것은 아니다.

기존 `vercel.json`의 일일 Cron은 그대로 둔다. 실제 운영 플랜이 확인되지 않은 상태에서 분 Cron을 기본 배포에 넣어 Hobby 배포를 실패시키지 않기 위해, 활성화용 설정을 `vercel.stock-alerts.json`에 분리했다. Pro 확인 후 기존 파일에 해당 stock-alerts Cron을 반영하거나 Vercel CLI의 `--local-config vercel.stock-alerts.json`으로 배포한다. 활성화 전까지 기본 배포에는 새 자동 점검 일정이 없다.

## 관심종목 등록과 확인

- 이름·코드 검색에서 코드를 선택한 뒤 **종목 확인**을 누른다. `GET /api/alerts/instrument?symbol=...`은 로그인 사용자의 종목을 기존 시세 공급자로 검증하고 이름·코드·거래소·통화를 반환한다. 관심종목은 저장하지 않으며 공급자 조회 credits는 사용한다.
- 확인한 종목을 추가할 때 서버가 다시 검증한다. 입력을 바꾸면 이전 확인은 무효이며, 저장 실패 시 입력과 확인 결과를 유지한다.
- **보유 종목 가져오기**는 계정에 저장된 보유자산에서 아직 등록하지 않은 종목을 보여준다. 선택한 항목만 순서대로 추가하며, 중간 실패 시 완료된 항목을 제외하고 남은 선택을 보존한다. 보유자산 자체는 수정하지 않는다.
- 목록의 규칙 수는 본인의 보관되지 않은 규칙 수(정지 포함)이며 `dataStatuses`는 그 규칙들의 최근 판정 상태다. 미평가 규칙은 `data_pending`(자료 준비 중), 규칙이 없으면 빈 배열(규칙 등록 전)이다. 과거 판정은 현재 시세의 정상 여부를 보장하지 않으며 규칙별 상세 결과는 아래 목록에서 확인한다.
- 관심종목은 최대 50개다. 제거하면 연결 규칙도 보관되며, 재추가해도 과거 규칙을 자동 활성화하지 않는다.

## 준비 순서

1. PostgreSQL DB와 `.env.example`의 `ALERT_DATABASE_URL`을 설정한다. 운영은 TLS pooled URL, 마이그레이션은 TLS direct URL을 사용한다.
2. 저장소 루트에서 `.venv/Scripts/python -m scripts.migrate_alerts`를 실행한다. `ALERT_MIGRATION_DATABASE_URL`이 필요하다. 번호별 SQL 체크섬을 검사하며 앱 시작 시 자동 적용하지 않는다.
   기존 운영 환경에 011 이후 채널 마이그레이션을 적용할 때는 Cron을 중지하고 진행 중 요청이 끝난 후 마이그레이션 → 새 코드 배포 → 확인 → Cron 재개 순서를 따른다. 구버전 발송 SQL은 새 고유 제약과 호환되지 않으므로 신·구 버전을 동시에 발송시키지 않는다. 이번 개발에서는 운영 발송을 활성화하지 않는다.
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
# 실적·배당 일정

선택 기능: `BENZINGA_API_KEY`와 계약상 분당 요청 수 `BENZINGA_REQUESTS_PER_MINUTE`를 설정한다. 실적은 미국 USD 일반주, 배당은 미국 USD 일반주·ETF를 지원한다. 한국 일정과 ETF 실적은 등록 단계에서 지원 불가를 표시한다. 공급자 키·권한·조회 실패는 보류이며 정상 무신호와 구분한다. 운영 발송 활성화와 별개로 공급자 라이선스·종목 커버리지를 확인해야 한다.

실적 `/api/v2.1/calendar/earnings`, 배당 `/api/v2.2/calendar/dividends`, 삭제 `/api/v2.1/calendar-removed/` 계약을 사용한다. 종목·종류별 조회와 종류별 삭제 조회는 사용자 간 공유하고 1,000행 단위 페이지 진행을 저장한다. 공급자 이벤트 ID와 내용 버전으로 중복을 막으며 응답 누락만으로 취소를 추정하지 않는다. 누락된 일정·비교 구간 분할은 보류한다. 삭제 기록은 400일 보존한다.

알림은 공급자 현지 날짜를 기준으로 지정한 달력일 전 한국시간 아침에 점검한다. 날짜/내용 변경·삭제는 이미 추적하던 일정에서만 알린다. 인상·삭감은 등록 이후 갱신된 미래 배당락일의 확정 정기 현금배당을 직전 같은 통화·주기의 배당과 비교한다. 특별배당·분할 전후 금액은 비교하지 않는다. 일정은 과거 400일~미래 366일의 공급자 조회 범위이며 실제 계좌의 배당 수령 여부를 판정하지 않는다.

계약 근거: [실적](https://docs.benzinga.com/api-reference/calendar-api/get-earnings), [배당](https://docs.benzinga.com/api-reference/calendar-api/get-dividends), [삭제](https://docs.benzinga.com/api-reference/calendar-api/get-removed). 실제 유료 계정 호출은 개발 검증에 포함하지 않았다.

## 한국·미국 공시

한국 일반주는 DART `DART_API_KEY`, 미국 일반주는 회사명·연락 이메일을 포함한 `SEC_USER_AGENT`가 필요하다. ETF는 지원하지 않는다. SEC는 앱 전체에서 초당 5건, DART는 분당 60건으로 제한하며 공급자 제한 응답은 재시도 대기로 구분한다. 호출 자격과 기업 식별자 매핑 실패를 정상 무공시로 취급하지 않는다.

종목-회사 식별자 목록은 하루 한 번 공유 조회한다. SEC는 공식 회사별 submissions의 최소 최근 1년 목록에서 최근 14일을 선택하며, DART는 최근 14일을 100건씩 조회해 모든 페이지가 완료된 뒤만 평가한다. 정정보고서도 포함한다. 날짜는 각 기관의 접수일이며 정확한 접수 시각을 추정하지 않는다. 첫 정상 조회와 재개 후 첫 조회는 기준만 저장하고, 이후 처음 확인한 새 접수번호 또는 같은 번호의 표시 정보 변경을 알린다. 첫 기준 날짜 이전 공시는 과거 이력으로 제외하며 누락만으로 철회·취소를 추정하지 않는다. 제목·접수일·원문 링크만 전달하고 내용 해석·투자 판단은 하지 않는다.

공식 계약: [SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [SEC 접근 규칙](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [DART 공시검색](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001), [DART 고유번호](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018). 인증키를 이용한 실제 호출·운영 발송은 개발 검증에서 제외한다.

최초 기준 날짜는 SEC는 미국 동부시간, DART는 한국시간으로 계산한다. 한국의 다음 날짜 아침 이후 미국의 같은 접수일에 추가된 공시도 처음 확인할 때 알린다. DART 회사 목록의 XML 오류도 인증 오류·요청 제한·시스템 점검을 구분하며 점검/일시 오류는 같은 실행일에 재시도한다.
# 일간·주간 수신 주기

이메일은 기존 계정과 기존 API 요청 모두 일간이 기본이다. 주간을 선택해도 조건은 매일 평가하며, 한국시간 월요일 오전 8시대에 최근 7일 동안 확인한 유효한 신호를 모아 보낸다. 주간 메일의 보류 개수는 이번 점검 기준이다. 조건을 중지·수정하거나 데이터 정정이 확인되면 대기 중인 해당 신호는 제외된다.

같은 채널에서 이미 접수되었거나 접수 여부가 불확실한 신호는 수신 주기를 바꿔도 재발송하지 않는다. 하루에 한 묶음만 준비한다. 당일 묶음이 준비된 뒤 주기를 변경하면 미발송 묶음이 취소될 수 있으며, 새 주기는 다음 예정일부터 적용된다. 이메일 테스트는 별도로 하루 한 번이다. 주간 요약에는 기존 리밸런싱 점검도 동일 주기로 포함된다. 동의를 유지한 주기 설정은 기존 메일의 해지 링크를 보존한다. 동의 변경·재구독과 기존 동의 전용 API의 재동의 요청은 이전 링크를 무효화한다.

첫 전송을 아직 시도하지 않은 묶음이 규칙 정지·정정 등으로 취소되면, 같은 주기의 다음 당일 점검에서 남은 유효 신호로 다시 구성한다. 이미 전송을 시도한 본문과 키는 변경하지 않는다. 결과불명 뒤 429나 영구 실패가 돌아와도 앞선 요청의 접수 가능성은 지워지지 않으며 새 키 재발송 후보로 풀리지 않는다.

## 웹 푸시

이메일과 독립된 동의·일간/월요일 주간 주기를 지원한다. 설정 화면의 연결 버튼을 눌러 브라우저 권한을 허용해야 하며, 페이지 방문만으로 권한을 요청하지 않는다. 계정당 최대 5개 브라우저를 연결하고 각 연결을 해제할 수 있다. 이메일 해지는 이메일만 중지한다. 알림 계정 삭제는 모든 푸시 연결과 동의도 삭제한다. 기존 리밸런싱 본문은 이메일에만 포함된다.

HTTPS와 Push API를 지원하는 브라우저가 필요하다. iOS/iPadOS는 지원 버전에서 홈 화면에 추가한 웹 앱으로 이용한다. 최소 manifest와 알림 전용 서비스 워커를 제공하며 개인 API·페이지를 오프라인 캐시에 저장하지 않는다. 잠금 화면에는 일반 안내만 표시하고 클릭하면 `/alerts`로 이동해 현재 로그인 계정의 이력을 확인한다. 로그아웃 후에도 일반 안내가 표시될 수 있으므로 공용 기기에서는 연결을 해제한다. 다른 계정에서 직접 연결하면 브라우저의 이전 구독을 해제한 뒤 새 구독을 만들며 서버의 기존 계정 소유권을 이전하지 않는다.

서버 환경 변수 `WEB_PUSH_VAPID_PRIVATE_KEY`는 base64url DER 형식의 P-256 개인키, `WEB_PUSH_VAPID_SUBJECT`는 운영 연락처 `mailto:` URI다. 공개키는 서버에서 파생해 반환한다. 키는 배포마다 생성하지 않고 안전하게 보관한다. 키 교체 후에는 기존 브라우저 재연결이 필요하다. 생성 예시(키 출력은 비밀로 보관):

```powershell
.venv/Scripts/python -c "import base64; from cryptography.hazmat.primitives.asymmetric import ec; from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption; print(base64.urlsafe_b64encode(ec.generate_private_key(ec.SECP256R1()).private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption())).decode().rstrip('='))"
```

012 마이그레이션과 의존성 설치 후 사용한다. `STOCK_ALERTS_SEND_ENABLED=false`에서는 푸시도 전송하지 않는다. 이메일 키 없이 푸시만 운영할 수 있다. FCM·Mozilla·Apple·Windows의 알려진 HTTPS 푸시 호스트만 허용하고 리다이렉트를 따라가지 않는다. P-256 구독 키와 16바이트 인증 비밀을 검증하고 pywebpush의 표준 암호화·VAPID 구현을 사용한다. 구독 주소·키는 개인 API와 DB에만 보관하며 이력·운영 집계에는 노출하지 않는다.

브라우저별 접수 상태를 기록한다. 404/410은 만료 연결 제거, 429는 해당 브라우저만 재시도한다. 타임아웃·5xx·전송 중 프로세스 종료는 접수 여부 불명으로 남기며 자동 재전송하지 않는다(Web Push에는 이메일 공급자와 같은 멱등 키 보장이 없음). 동일 알림의 Topic·notification tag도 고정한다. 일부 접수와 전체 접수를 구분하며 공급자 접수는 기기 표시 보장이 아니다. 대기 TTL은 1시간이고 오프라인 기기는 알림을 놓칠 수 있으므로 전체 상세는 화면 이력을 따른다. 기기별 오류를 포함한 이력과 운영자의 푸시 요청·429 수치를 확인한다.

계약 근거: [pywebpush 공식 구현](https://github.com/web-push-libs/pywebpush), [브라우저 구독 API](https://developer.mozilla.org/en-US/docs/Web/API/PushManager/subscribe). 실제 기기·FCM/Apple/Mozilla/Windows 전달은 운영 키 없이 로컬 자동 검사로 대체할 수 없으며 활성화 전 대상 브라우저에서 확인한다.
