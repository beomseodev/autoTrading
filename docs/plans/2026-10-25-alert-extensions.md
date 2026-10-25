# 알림 확장 실행 기록

기준 HEAD: bfcdd5c. 기존 관심종목 확인·선택 가져오기는 별도 커밋으로 이미 보존되어 있다.
운영 배포·실제 발송은 비활성 상태로 유지한다. 각 기능은 실패 테스트 → 구현 → 네 역할 팀 리뷰 → 수정·재검증 후 다음 기능으로 진행한다.

| 순서 | 기능 | 상태 |
|---|---|---|
| 1 | 명세 보완: 읽을 수 있는 규칙·이력, 운영자 상태 | 완료, 네 역할 재검토 통과 |
| 2 | 같은 종목·거래일 AND 2~3조건 | 완료, 네 역할 통과 |
| 3 | 돌파일 가격 고정 재접근 | 완료, 네 역할 검토·수정 후 팀장 승인 |
| 4 | 동일 시장·통화 상대 수익률 | 완료, 네 역할 검토·수정 후 팀장 승인 |
| 5 | 저장 보유자산 기반 평단·비중 | 완료, 네 역할 재검토 통과 |
| 6 | 실적·배당 일정·변경 | 완료, 네 역할 검토·수정 후 팀장 승인 |
| 7 | 한국·미국 공시 | 완료, 네 역할 검토·수정 후 팀장 승인 |
| 8 | 채널별 일간·주간 | 완료, 네 역할 재검토 통과 (푸시 채널은 기능9에서 연결) |
| 9 | 웹 푸시 | 완료, 네 역할 재검토 통과 |
| 최종 | 전체 회귀·부하·실사용자 흐름 리뷰 | 완료, 네 역할 참여·필수 지적 수정 및 재확인 |

현재 명세 근거는 2026-10-23-stock-alerts.md와 최신 구현·복구 검토 문서다. 기존 10종 수식, API 생명주기, 거래소 달력, 계정 분리, 저장 복구는 기존 구현·테스트를 재사용한다. 기본 검사: Python 251 통과/21 건너뜀, Node 38 통과. 건너뛴 DB·부하 검사는 격리 PostgreSQL에서 수행한다.

## 기능 1 검증과 팀 판단

Orca run `run_b05fbfd8e6aa`의 비판·가능성·외부인·확장성 4명 참여, 누락·공급자 대체 없음. 초기 리뷰의 실제 조건·단위 누락, 내부 기준 0 표시, 재시도 시각과 대기 시작 혼용, 테스트 메일 혼입, 호출 계측 누락, 반복 SQL 및 초기화 시간 누락을 모두 채택·수정했다. 각 역할의 후속 검토에서 필수 수정 없음으로 확인했다. 로컬 원문은 `.team-review/extensions-f1-*-followup.md`이며 작업자는 모두 회수했다.

검증: 실패 테스트 선행, 격리 DB 포함 전체 Python 273 통과/부하 1개 제외 후 추가 운영 검사 6개 통과, 관련 작업 검사 19개 통과, Node 40개 통과, 실제 Chrome 3종 통과. 실 공급자·메일 호출은 하지 않았으며 강제 종료된 실행의 계측은 보장하지 않는다.

## 기능 2 검증과 팀 판단

동일 run에서 네 역할 모두 차단 결함 없이 통과했다. 분할 보호의 실제 작업 검증 보강을 채택해 AND 지정가격 포함 규칙에서 `needs_review`·정지·이벤트/메일 0건을 확인했다. 3조건 일치·불일치도 추가했다. 단위 54개, API/작업 29개 통과 후 확장 검사 9개 통과, Chrome의 AND 등록·모바일 확인 통과. 원문 `.team-review/extensions-f2-*.md`. 작업자 전원 회수, 누락·공급자 대체 없음. 메일 내부 매개변수 표현의 가독성은 최종 사용자 검토에서 보완한다.

## Feature 3 verification

Four Orca roles reviewed, no missing roles/provider substitutions. Initial findings: replay all missed state transitions separately from the five-session delivery window; invalidate sequences affected by intermediate bar corrections; re-arm on the first expired day; reflect stored state in dry-run; display current waiting state separately. All adopted. Critic, possibility and outsider follow-ups cleared their findings. Scale follow-up identified an initial warm-up mismatch in dry-run: coordinator reproduced a failing test, applied the same insufficient-data skip policy as live evaluation, and confirmed it passes. Coordinator accepts the final fix; no third review round was claimed. Reports: `.team-review/extensions-f3-*-followup.md`. All workers released.

Extension tests 18 passed before follow-up; added 70-session chunk/resume and initial warm-up regressions also passed. Related jobs/extensions 33 passed; Node format 3 and Chrome passed. Historical data corrections conservatively require confirmation; no historical signal is automatically re-sent.

## Feature 4 verification

Four Orca roles participated, no missing roles or provider substitutions. Adopted findings: validate against exchange sessions even when both inputs omit the same day; track inserted/deleted historical rows; retain reference hash and correct same-instrument hash pairs; update visible deferred status immediately after retry exhaustion; explain percentage points and price-only returns in email. Added correction lookup indexes. Critic re-read the latest fixes before settlement and cleared required findings. Coordinator reproduced failures and verified fixes; all workers released. Reports: `.team-review/extensions-f4-*.md`.

Before review fixes, full Python suite: 305 passed / one load test skipped; Chrome passed. After fixes, related job/extension tests: 42 passed; latest relative tests: 7 passed including shared-reference account isolation and cancellation after benchmark edits. Format tests: 4 passed. Representative correction-query timing is included in the final load review; no live data or mail was sent.

## Feature 5 verification

All four Orca roles reviewed and rechecked the implementation; all follow-ups report zero required changes. No missing roles/provider substitutions, all workers released. Reports: `.team-review/extensions-f5-*-followup.md`. Adopted findings: preserve retry budget while waiting for shared inputs; checkpoint metadata lookup under a time budget; share only the latest confirmed valuation inputs per account; isolate cost-return dependencies; retain both sides of correction evidence; describe values as previous/current evaluations.

Conflicting Forex daily-bar timezone documentation led to replacing the daily-bar assumption with the documented historical `/exchange_rate` contract: explicit UTC date/time, symbol/rate/timestamp validation, prior completed UTC weekday end. References: https://twelvedata.com/docs and https://support.twelvedata.com/en/articles/5745849-timezones. Current and preceding FX points are rechecked; older FX corrections require a revision feed and are not guaranteed. Actual contracted API access and delivery activation remain outside this development scope.

Full Python before review fixes: 313 passed / load test skipped; Chrome passed. Related jobs/extensions after fixes: 51 passed. Latest holdings suite: 17 passed, including account paths, five financial edit reset cases, dependency recovery, data sharing, previous-date corrections, cash-inclusive weight crosses, frozen delivery cancellation and FX boundaries.

## Feature 6 verification

All four Orca roles independently reviewed and followed up, no missing roles/provider substitutions, all workers released. Reports: `.team-review/extensions-f6-*-followup.md`. Adopted findings: isolate calendar evidence from OHLC corrections; retain prior dividend and split evidence in comparison versions; recover missing comparison inputs; compare decimal thresholds exactly; display earnings time/period and settings-canceled information events; process multiple persisted pages within the existing time budget. Frozen unsent digests now revalidate event corrections before delivery.

Possibility and scale follow-ups cleared required changes. Critic and outsider follow-ups found a remaining stale-current-dividend recovery gap. Coordinator reproduced the failure, separated observed versions from completed comparisons, and verified first recovery emits once while previously evaluated current/prior stale round trips do not duplicate or invalidate the evidence. Coordinator accepts this final fix; no third team-review round is claimed.

Before review fixes: full Python 333 passed / one load test skipped, Node 42 passed, Chrome passed. Related calendar/jobs tests after primary fixes: 35 passed. Final calendar suite: 23 passed, including 9,000 removals plus terminal empty page through the actual cron, prior-only correction DB updates, relevant split corrections, threshold boundaries, dry-run parity, and stale recovery. Updated Chrome passed with canceled information history checks.

Benzinga contracts and supported US instruments are documented in `docs/stock-alerts-operations.md`. No paid account/API/email was activated. Direct provider docs do not specify the earnings time zone, so the UI preserves the supplied time and directs users to confirm the zone at source. Final load verification must include calendar/dividend comparisons, shared removal data and correction history; actual licensed coverage remains an operational integration check.

## Feature 7 verification

All four Orca roles independently reviewed. Scale cleared required code changes after confirming ZIP error isolation; critic/possibility/outsider follow-ups cleared the SEC source-date and DART XML error-classification fixes. No roles were missing and no provider was substituted. Orca could not recognize three settled terminals for reuse, so they were released and the same follow-up tasks ran in new Codex terminals. All workers and deliveries were settled/released/acknowledged. Reports: `.team-review/extensions-f7-*.md`.

SEC CIK and DART corporate-code mappings are shared, filings retain account-specific baseline/state/events, first successful collection and resume establish a baseline without historical delivery, and same-source-date filings discovered after that baseline remain eligible. DART maintenance and server errors retry within the same run. XML declarations, invalid paths/identifiers and malformed mapping data are rejected. JSON syntax failures intentionally remain retryable lookup failures, never normal no-filings results; the stricter malformed-response statement applies to XML. No existing deployed filing state needed migration because this feature is not deployed.

Before review fixes: full Python 355 passed / one load test skipped, Node 42 passed, Chrome passed. Source-date and XML failures were reproduced before fixes. Final filings/jobs tests: 34 passed, including shared fetch/account isolation, two-page atomic cache completion, maintenance recovery, source-date boundary and malformed XML/encoding. Provider quota throughput remains part of final mixed-load testing. Actual SEC/DART credentials and delivery were not exercised.

## Feature 8 verification

All four Orca roles independently reviewed and followed up, clearing required changes; no roles were missing or providers substituted. All feature workers were released and deliveries acknowledged. Reports: `.team-review/extensions-f8-*-followup.md`. Adopted findings: weekly candidates must use discovery time without the daily session-age cap; frequency-only settings preserve received-email unsubscribe links while consent changes and legacy reconfirmations rotate them; document a stopped-Cron schema/code transition. Added failures before fixes, including same-day legacy duplication, ambiguous prior delivery, discovery-window boundaries and legacy API compatibility.

Latest full Python: 372 passed / one opt-in load skipped; related cadence/jobs/API: 28 passed. Node 42 and all three Chrome flows passed. Separately ran the existing PostgreSQL acceptance load: 50 users, 100 instruments, 5,000 rules, exactly 100 fetches and 50 digests, two resumed calls of 159.48 and 60.66 seconds (222.05 seconds including setup). This uses fixed market data and immediate fake mail responses; weekly accumulation, mature ledger, provider delay and mixed-source quota testing remain in final verification. Web Push connects its own channel to cadence in feature 9.

## Feature 9 verification

All four Orca roles independently reviewed and followed up, clearing required changes. No roles were missing or providers substituted; all feature workers were released and deliveries acknowledged. Reports: `.team-review/extensions-f9-*-followup.md`. Adopted findings: same-value settings preserve pending deliveries; reconnect reconciles lost POST responses and settings conflicts before replacing subscriptions; expired/canceled device states finalize with accepted evidence preserved; terminal push unknown digests leave the Cron candidate set while email idempotent retries remain.

Initial failures preceded implementation. Real pywebpush encryption was decrypted locally to verify its generic payload, timeout and redirect behavior. Before review fixes, full Python 387 passed / one opt-in load skipped, Node 43 passed. After fixes, push/jobs/cadence 41 passed, including 20 push tests. Chrome covers explicit permission/denial, independent weekly settings, key replacement, unsubscribe, lost subscription responses and failed settings followed by retry without duplicate subscriptions. The follow-up recommendation to model changing server revisions was added to the browser fixture. Browser push services and Notification/PushManager are mocked; actual browser-provider delivery and iOS installation remain operational checks. Final mixed-load and whole-product user-flow review follow this feature gate.

## 최종 전체 기능 검증과 팀 판단

전체 제품의 사용자 흐름을 실제 Orca 네 역할이 검토했다. 누락·공급자 대체 없음. 비판 역할의 불명 전송 이후 실패 응답 상태 보존, 무시도 주간 묶음에서 남은 신호 복구와 외부인 역할의 운영 OFF 범위·보유자산 오류 행동 안내를 채택했다. 실패 테스트를 먼저 확인한 뒤 수정했고 두 역할의 각 1회 후속 검토에서 필수 지적 해소를 확인했다. 가능성·확장성 역할은 새 필수 코드 수정 없이 부하 결과의 적용 범위와 운영 조건을 명시하도록 권고했다. 모든 최종 작업자는 회수하고 완료 메시지를 확인 처리했다. 상세는 [최종 사용자 흐름 검토](../reviews/2026-10-25-alert-extensions-final.md).

최신 전체 Python **400 통과 / opt-in 부하 3개 제외**, Node **43 통과**, 실제 headless Chrome의 알림·대시보드·로딩/실험실 **3종 통과**. 별도 부하는 기존 5,000개 가격 규칙 검사 1개와 최종 혼합·누적 검사 2개 모두 통과했다. 최종 혼합/누적 두 검사는 133.11초에 완료했다. 혼합은 50사용자·950규칙·100종목·양 채널·250푸시기기, 삭제 자료 종류별 9,000건, 모의 지연/429를 포함해 재개 7회·최대 호출 183.12초였다. 400일 발송 이력/18,000이벤트의 주간 후보 700개 중 기발송 1개를 제외했고 준비 2.47초·정정 1.13초·400개 배당자료 50회 비교 1.42초를 측정했다. 정정은 처음 약 18초 성능 실패를 재현한 뒤 계산 재사용과 일괄 DB 갱신으로 개선했다.

이 결과는 실제 외부 사용자 조사나 운영 공급자·전달 검증이 아니다. 5,000 가격 규칙과 950 혼합 규칙은 다른 구성이고 700개는 전체 사건 상한이 아니다. 장기 완료 task 정리·실제 계약 quota·냉간 캐시·실기기 전달 등 미측정 범위를 최종 검토에 기록했다. Git 원격 푸시·배포·실제 발송은 수행하지 않았다.
