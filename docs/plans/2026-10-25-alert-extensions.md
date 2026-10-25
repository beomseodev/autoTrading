# 알림 확장 실행 기록

기준 HEAD: bfcdd5c. 기존 관심종목 확인·선택 가져오기는 별도 커밋으로 이미 보존되어 있다.
운영 배포·실제 발송은 비활성 상태로 유지한다. 각 기능은 실패 테스트 → 구현 → 네 역할 팀 리뷰 → 수정·재검증 후 다음 기능으로 진행한다.

| 순서 | 기능 | 상태 |
|---|---|---|
| 1 | 명세 보완: 읽을 수 있는 규칙·이력, 운영자 상태 | 완료, 네 역할 재검토 통과 |
| 2 | 같은 종목·거래일 AND 2~3조건 | 완료, 네 역할 통과 |
| 3 | 돌파일 가격 고정 재접근 | 완료, 네 역할 검토·수정 후 팀장 승인 |
| 4 | 동일 시장·통화 상대 수익률 | 완료, 네 역할 검토·수정 후 팀장 승인 |
| 5 | 저장 보유자산 기반 평단·비중 | 진행 중 |
| 6 | 실적·배당 일정·변경 | 대기 |
| 7 | 한국·미국 공시 | 대기 |
| 8 | 채널별 일간·주간 | 대기 |
| 9 | 웹 푸시 | 대기 |
| 최종 | 전체 회귀·부하·실사용자 흐름 리뷰 | 대기 |

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
