"use strict";
function describeRule(kind,p) {
  const direction={up:'상향 돌파',down:'하향 돌파',both:'상향 또는 하향 돌파'}[p.direction]||'';
  const ma=`${p.period}거래일 ${p.average==='ema'?'지수':'단순'}이동평균`;
  const descriptions={
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
  const unit=['daily_change','drawdown_cross'].includes(kind)?'%':kind==='volume_spike'?'주':kind==='rsi_cross'?'':kind?' '+currency:'';
  const labels={previousValue:'전일 값',value:'당일 값',previousThreshold:'전일 기준',threshold:'기준',close:'종가 (현지 통화)',previousHigh:'직전 최고 종가',previousLow:'직전 최저 종가'};
  return Object.entries(values).filter(([key,value])=>labels[key]&&Number.isFinite(value)).map(([key,value])=>`${labels[key]}: ${value.toLocaleString('ko-KR',{maximumFractionDigits:6})}${['close','previousHigh','previousLow'].includes(key)?' '+currency:unit}`).join(' · ')||'비교 수치 없음';
}
if(typeof module!=='undefined')module.exports={describeRule,describeValues};
