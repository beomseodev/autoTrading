"use strict";
function describeRule(kind,p) {
  if(kind==='retest')return describeRule(p.trigger.type,p.trigger.params)+`의 기준 가격을 고정하고 다음 ${p.windowSessions}거래일 안에 종가가 ±${p.tolerancePct}% 이내로 처음 재접근. 대기 중 새 돌파는 기준을 바꾸지 않으며 완료·만료 후 새 돌파가 필요합니다. 정지·재개·계산 조건 변경은 대기를 초기화합니다(이름 변경 제외). 분할·관련 자료 정정 시 확인 후 재개합니다.`;
  if(kind==='all_of')return '같은 거래일에 모두 충족: '+p.conditions.map(c=>describeRule(c.type,c.params)).join(' 그리고 ');
  const direction={up:'상향 돌파',down:'하향 돌파',both:'상향 또는 하향 돌파'}[p.direction]||'';
  const ma=`${p.period}거래일 ${p.average==='ema'?'지수':'단순'}이동평균`;
  const descriptions={
    filing:'한국·미국 일반주의 새 공시·정정 제목, 접수일과 원문 링크를 확인합니다. 첫 정상 조회를 기준으로 저장하고 이후 처음 확인한 공시를 알립니다. 정지 후 재개도 기준을 다시 저장합니다. ETF는 지원하지 않으며 공시 내용의 투자 판단은 제공하지 않습니다.',
    earnings_event:`미국 일반주 실적 발표일 ${p.daysBefore}일 전 아침 알림과 추적 중인 일정 변경·취소. 공급자의 현지 날짜이며 잠정 일정은 따로 표시합니다. ETF·한국 종목은 지원하지 않습니다.`,
    dividend_event:`미국 종목 ${p.dateField==='payDate'?'배당 지급일':'배당락일'} ${p.daysBefore}일 전 아침 알림과 추적 중인 일정 변경·취소. 공급자의 현지 날짜이며 잠정 일정은 따로 표시합니다. 한국 종목은 지원하지 않습니다.`,
    dividend_change:`미국 종목의 확정 정기 현금배당을 직전 같은 통화·지급 주기와 비교해 ${p.thresholdPct}% 이상 ${p.direction==='up'?'인상':p.direction==='down'?'삭감':'인상·삭감'} 시 알립니다. 미래 배당락 일정 중 등록 이후 확인된 변경만 비교하며 분할이 끼면 보류합니다.`,
    holding_return:`이 계정의 저장 평단 대비 가격 수익률이 ${p.thresholdPct}%를 ${direction}한 점검일. 현금배당·수수료 제외. 첫 평가·보유 내역 변경·재개 시 비교 기준을 초기화합니다. 분할 시 수량·평단 확인 후 재개가 필요합니다.`,
    holding_weight:`이 계정의 현금을 포함한 저장 총자산 중 종목 비중이 ${p.thresholdPct}%를 ${direction}한 점검일. 각 시장의 최근 확정 종가와 직전 UTC 평일 말 시점에 조회한 환율을 사용합니다. 첫 평가·보유 내역 변경·재개 시 비교 기준을 초기화합니다.`,
    relative_performance:`최근 ${p.period}거래일 가격 수익률에서 ${p.benchmark} 수익률을 뺀 차이가 ${p.thresholdPct}%p를 ${direction}한 날 (같은 시장·통화, 분할 조정·현금배당 제외)`,
    price_ma_cross:`종가가 ${ma}을 ${direction}한 날`,
    ma_cross:`${p.shortPeriod}거래일선이 ${p.longPeriod}거래일선을 ${direction}한 날 (${p.average==='ema'?'EMA':'SMA'})`,
    price_cross:`종가가 현지 통화 ${p.price}을 ${direction}한 날`,
    daily_change:`전일 대비 ${p.direction==='down'?'하락':p.direction==='up'?'상승':'상승 또는 하락'}률이 ${p.thresholdPct}% 이상인 날`,
    rsi_cross:`${p.period}거래일 RSI가 ${p.level}을 ${direction}한 날`,
    range_breakout:`직전 ${p.period}거래일 종가의 ${p.direction==='up'?'최고가':p.direction==='down'?'최저가':'최고가 또는 최저가'}를 갱신한 날 (당일·동률 제외)`,
    volume_spike:`거래량이 직전 ${p.period}거래일 평균의 ${p.multiple}배 이상인 날 (당일 제외)`,
    macd_cross:`MACD(${p.fastPeriod}, ${p.slowPeriod})가 ${p.crossMode==='zero'?'0선':p.signalPeriod+'거래일 시그널선'}을 ${direction}한 날`,
    bollinger_cross:`볼린저(${p.period}거래일, ${p.stddev}배) ${ {upper_exit:'상단 위 돌파',lower_exit:'하단 아래 이탈',upper_reentry:'상단 아래 복귀',lower_reentry:'하단 위 복귀'}[p.event]}한 날`,
    drawdown_cross:`당일 포함 ${p.period}거래일 최고 종가 대비 낙폭이 ${p.thresholdPct}%에 진입한 날`
  };
  return descriptions[kind]||'지원하지 않는 조건';
}
function describeValues(values={},kind='',params={},currency='현지 통화') {
  values={...values};
  if(kind==='daily_change')values.threshold=params.thresholdPct;
  if(kind==='range_breakout')delete values.threshold;
  const unit=kind==='relative_performance'?'%p':['daily_change','drawdown_cross','holding_return','holding_weight'].includes(kind)?'%':kind==='volume_spike'?'주':kind==='rsi_cross'?'':kind?' '+currency:'';
  const labels={previousValue:'전일 값',value:'당일 값',previousThreshold:'전일 기준',threshold:'기준',close:'종가 (현지 통화)',previousHigh:'직전 최고 종가',previousLow:'직전 최저 종가'};
  if(['holding_return','holding_weight'].includes(kind))Object.assign(labels,{previousValue:'직전 평가 값',value:'현재 평가 값'});
  return Object.entries(values).filter(([key,value])=>labels[key]&&Number.isFinite(value)).map(([key,value])=>`${labels[key]}: ${value.toLocaleString('ko-KR',{maximumFractionDigits:6})}${['close','previousHigh','previousLow'].includes(key)?' '+currency:unit}`).join(' · ')||'비교 수치 없음';
}
function describeState(rule){
  if(rule.kind!=='retest')return '';
  if(!rule.enabled)return '현재 재접근 대기 없음 · 정지 시 기존 돌파 대기를 초기화합니다.';
  const s=rule.evaluation_state;
  return s?.date?`${s.date} 돌파 · 고정 가격 ${s.level} ${rule.metadata?.currency||'현지 통화'} · 재접근 대기 중`:'현재 새 돌파 대기 중';
}
if(typeof module!=='undefined')module.exports={describeRule,describeValues,describeState};
