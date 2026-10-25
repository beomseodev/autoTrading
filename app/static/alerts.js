"use strict";
const $ = id => document.getElementById(id);
const state = {account: null, settings: null, watches: [], rules: [], selected: new Set(), preview: null, edit: null, events: null, digests: null};
let requests = 0;
const names = {average:"평균 방식",period:"기간 (거래일)",direction:"방향",shortPeriod:"단기 (거래일)",longPeriod:"장기 (거래일)",price:"지정가격 (현지 통화)",thresholdPct:"기준 (%)",level:"RSI 기준",multiple:"거래량 배수",fastPeriod:"MACD fast",slowPeriod:"MACD slow",signalPeriod:"시그널 기간",crossMode:"MACD 비교선",stddev:"표준편차 배수",event:"발동 조건"};
const options = {average:[['sma','단순이동평균 SMA'],['ema','지수이동평균 EMA']],direction:[['both','양방향'],['up','상향'],['down','하향']],crossMode:[['signal','시그널선'],['zero','0선']],event:[['upper_exit','상단 위 돌파'],['lower_exit','하단 아래 이탈'],['upper_reentry','상단 아래 복귀'],['lower_reentry','하단 위 복귀']]};
names.windowSessions='대기 기간 (거래일)';names.tolerancePct='재접근 허용폭 (%)';
names.benchmark='비교 종목 코드 (같은 시장·통화)';
names.useSavedPortfolio='이 계정에 저장된 보유자산을 연결합니다 (직접 선택)';
names.daysBefore='일정 며칠 전 (달력일)';names.dateField='배당 알림 기준일';
options.dateField=[['exDate','배당락일'],['payDate','지급일']];
const reasons={calendar_not_configured:'일정 공급자 연결이 설정되지 않았습니다.',calendar_quota_not_configured:'일정 공급자 조회 한도가 설정되지 않았습니다.',calendar_unavailable:'일정 공급자 조회 실패',calendar_unsupported:'지원하지 않는 종목·일정입니다.',calendar_snapshot_pending:'일정 자료 준비 중',invalid_calendar_data:'일정 자료 형식 검증 실패',invalid_calendar_response:'일정 응답 검증 실패',invalid_calendar_removal:'일정 취소 자료 검증 실패'};
Object.assign(reasons,{sec_not_configured:'SEC 조회 연락처 설정·접근 권한 확인이 필요합니다.',dart_not_configured:'DART API 키·접근 권한 확인이 필요합니다.',filing_unsupported:'지원되는 공시 회사 식별자를 찾지 못했습니다.',filing_unavailable:'공시 조회 실패',filing_snapshot_pending:'공시 자료 준비 중',invalid_filing_data:'공시 자료 검증 실패',invalid_filing_response:'공시 응답 검증 실패',invalid_filing_mapping:'공시 회사 식별자 검증 실패'});
const statuses = {signal:'새 신호',no_signal:'정상 무신호',no_new_session:'휴장 또는 활성화 후 새 거래일 없음',insufficient_data:'계산 자료 부족',invalid_data:'자료 검증 실패',needs_review:'조건 재확인 필요',pending:'발송 대기',sending:'전송 중',accepted:'공급자 접수',delivered:'배달 확인',no_signals:'새 신호 없음',unknown:'결과 확인 필요',retryable_failed:'발송 재시도 대기',permanent_failed:'발송 실패',canceled:'설정 변경으로 취소',bounced:'반송',complained:'스팸신고',suppressed:'발송 중지'};
statuses.deferred='조회 실패·자료 보류, 신호 여부 미확인';
statuses.missed_window='발송 시간 내 미접수';
statuses.data_pending='자료 준비 중';
function busy(delta) {requests += delta; $('loading').hidden = !requests; $('content').setAttribute('aria-busy',String(Boolean(requests))); document.querySelectorAll('button').forEach(b=>b.disabled=Boolean(requests));}
async function api(path, method='GET', body) {
  busy(1);
  try {
    const response = await fetch(path,{method,headers:{'Content-Type':'application/json',...(state.account?{'X-Portfolio-Account':state.account}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
    return data;
  } finally {busy(-1);}
}
function fail(error){$('status').textContent=error.message||'요청에 실패했습니다. 입력을 보관하고 다시 시도해주세요.';}
function element(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function button(text,action){const b=element('button',text,'button quiet');b.type='button';b.addEventListener('click',()=>action().catch(fail));return b;}
function canonicalSymbol(symbol){return /\.K[QS]$/.test(symbol)?symbol:symbol.replaceAll('.','-');}
function typeInfo(){return state.settings.types.find(x=>x.type===$('kind').value);}
function fields(params){
  $('params').replaceChildren();
  state.readParams=parameterFields($('kind').value,params,$('params'));
  $('rule-meaning').textContent=describeRule($('kind').value,params);
}
function parameterFields(kind,params,container,prefix='param-') {
  if(kind==='retest') {
    const select=element('select'),body=element('div');select.setAttribute('aria-label','돌파 기준');
    state.settings.types.filter(t=>['price_cross','price_ma_cross','range_breakout'].includes(t.type)).forEach(t=>{const opt=element('option',t.label);opt.value=t.type;select.append(opt);});
    select.value=params.trigger.type;container.append(select,body);
    let read=parameterFields(params.trigger.type,params.trigger.params,body,prefix+'trigger-');
    select.addEventListener('change',()=>{body.replaceChildren();read=parameterFields(select.value,state.settings.types.find(t=>t.type===select.value).defaults,body,prefix+'trigger-');$('rule-meaning').textContent=describeRule(kind,state.readParams());});
    const rest=parameterFields('',{windowSessions:params.windowSessions,tolerancePct:params.tolerancePct},container,prefix);
    return ()=>({...rest(),trigger:{type:select.value,params:read()}});
  }
  if(kind==='all_of') {
    const children=[];
    const count=element('select');count.setAttribute('aria-label','AND 조건 수');
    [2,3].forEach(n=>{const opt=element('option',n+'개 조건');opt.value=n;count.append(opt);});count.value=params.conditions.length;container.append(count);
    const redraw=()=>{
      const values=children.map(c=>({type:c.select.value,params:c.read()}));
      while(values.length<Number(count.value))values.push({type:'daily_change',params:{thresholdPct:5,direction:'both'}});
      fields({conditions:values.slice(0,Number(count.value))});
    };
    count.addEventListener('change',redraw);
    params.conditions.forEach((child,i)=>{
      const fieldset=element('fieldset'),legend=element('legend','조건 '+(i+1)),select=element('select'),body=element('div');
      select.setAttribute('aria-label','조건 '+(i+1)+' 종류');
      state.settings.types.filter(t=>t.composable).forEach(t=>{const opt=element('option',t.label);opt.value=t.type;select.append(opt);});
      select.value=child.type;fieldset.append(legend,select,body);container.append(fieldset);
      const entry={select,read:parameterFields(child.type,child.params,body,prefix+i+'-')};children.push(entry);
      select.addEventListener('change',()=>{body.replaceChildren();entry.read=parameterFields(select.value,state.settings.types.find(t=>t.type===select.value).defaults,body,prefix+i+'-');$('rule-meaning').textContent=describeRule('all_of',state.readParams());});
    });
    return ()=>({conditions:children.map(c=>({type:c.select.value,params:c.read()}))});
  }
  const controls=[];
  Object.entries(params).forEach(([key,value])=>{
    const label=element('label',kind==='relative_performance'&&key==='thresholdPct'?'수익률 차이 기준 (%p)':names[key]||key),control=element(options[key]?'select':'input');
    control.dataset.param=key;control.id=prefix+key;
    if(options[key])options[key].forEach(([val,text])=>{const opt=element('option',text);opt.value=val;control.append(opt);});
    else {control.type=typeof value==='boolean'?'checkbox':typeof value==='string'?'text':'number';control.step=key.toLowerCase().includes('period')||key==='period'?'1':'any';control.required=true;}
    if(control.type==='checkbox')control.checked=value;
    control.value=value;label.append(control);container.append(label);controls.push([key,control]);
  });
  return ()=>Object.fromEntries(controls.map(([key,c])=>[key,c.type==='checkbox'?c.checked:c.tagName==='SELECT'||c.type==='text'?c.value:Number(c.value)]));
}
function resetEdit(){state.edit=null;$('kind').disabled=false;$('rule-name').value='';$('cancel-edit').hidden=true;$('resolve-edit').hidden=true;$('restore-archived').checked=false;$('save-rule').textContent='선택 종목에 규칙 등록';$('rule-heading').textContent='조건 등록';fields(typeInfo().defaults);}
function render(){
  $('watchlist').replaceChildren();
  if(!state.watches.length)$('watchlist').append(element('li','등록한 관심종목이 없습니다.'));
  state.watches.forEach(w=>{
    const li=element('li'),label=element('label'),check=element('input');
    check.type='checkbox';check.checked=state.selected.has(w.instrumentId);check.setAttribute('aria-label',w.symbol+' 일괄 등록 선택');
    check.addEventListener('change',()=>check.checked?state.selected.add(w.instrumentId):state.selected.delete(w.instrumentId));
    const detail=element('span',`${w.metadata.name} (${w.symbol}) · ${w.metadata.mic} · ${w.metadata.currency}`);
    detail.append(element('small',`규칙 ${w.ruleCount}개 · 최근 판정: ${w.dataStatuses.map(s=>statuses[s]||s).join(', ')||'규칙 등록 전'}`));
    label.append(check,detail);li.append(label,button('제거',async()=>{await api('/api/alerts/watchlist/'+w.id,'DELETE');state.selected.delete(w.instrumentId);await refresh();}));$('watchlist').append(li);
  });
  $('rules').replaceChildren();
  if(!state.rules.length)$('rules').append(element('li','등록한 규칙이 없습니다.'));
  state.rules.forEach(r=>{const li=element('li'),detail=element('div',`${r.symbol} · ${r.name||state.settings.types.find(x=>x.type===r.kind).label}`,'detail');detail.append(element('small',describeRule(r.kind,r.params)),element('small',describeState(r)),element('small',r.enabled?'활성':'정지'),element('small','마지막 정상 평가일: '+(r.last_session||'없음')),element('small',r.last_result?(statuses[r.last_result.status]||r.last_result.status)+' · '+(reasons[r.last_result.reason]||r.last_result.reason||''):'자료 준비 중 · 다음 점검부터 평가합니다.'));li.append(detail,button(r.enabled?'정지':'재개',async()=>{await api('/api/alerts/rules/'+r.id,'PATCH',{revision:r.revision,enabled:!r.enabled});await refresh();}),button('수정',async()=>{state.edit=r;$('kind').value=r.kind;$('kind').disabled=true;$('rule-name').value=r.name;fields(r.params);$('cancel-edit').hidden=false;$('save-rule').textContent='조건 수정 저장';$('rule-heading').textContent=r.symbol+' 조건 수정';$('rule-heading').scrollIntoView({block:'center'});$('rule-name').focus();}),button('삭제',async()=>{await api('/api/alerts/rules/'+r.id,'DELETE');await refresh();}));$('rules').append(li);});
}
async function history(kind,append=false) {
  const result=await api('/api/alerts/'+kind+(append&&state[kind]?'?cursor='+encodeURIComponent(state[kind]):''));
  if(!append) $(kind).replaceChildren();
  if(!append&&!result.items.length) $(kind).append(element('li',kind==='events'?'아직 신호 이력이 없습니다.':'아직 발송 이력이 없습니다.'));
  result.items.forEach(row=>{
    const li=element('li');
    if(kind==='events') {
      li.append(element('span',`${row.payload.symbol} · ${row.session_date} · ${row.payload.label}`));
      const kind=row.payload.kind||state.settings.types.find(t=>t.label===row.payload.label)?.type;
      if(kind)li.append(element('small',describeRule(kind,row.payload.params)));
      if(row.payload.information){
        if(row.canceled)li.append(element('small','설정 변경·정지로 취소된 신호'));
        const info=row.payload.information;li.append(element('small',info.title+' · '+info.details));
        const link=element('a','출처 확인');
        try{const url=new URL(info.sourceUrl);if(url.protocol==='https:'&&['www.benzinga.com','www.sec.gov','dart.fss.or.kr'].includes(url.hostname)){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';li.append(link);}}catch{}
      }else li.append(element('small',row.canceled?'취소된 신호':(row.payload.result.reason||'')+' · '+describeValues(row.payload.result.values,kind,row.payload.params,row.payload.currency)));
      (row.payload.result.conditions||[]).forEach(c=>li.append(element('small',describeRule(c.type,c.params)+' · '+describeValues(c.result.values,c.type,c.params,row.payload.currency))));
      if(row.correction) li.append(element('small','자료 정정: '+row.correction.reason));
    } else {
      li.append(element('span',`${row.digest_date} · 이메일 · ${row.kind==='test'?'테스트':row.kind==='weekly'?'주간':'일간'} · ${statuses[row.status]||row.status}`));
      if(row.deferredCount) li.append(element('small',row.deferredCount+'개 조건 보류'));
      if(row.error) li.append(element('small',row.error));
    }
    $(kind).append(li);
  });
  state[kind]=result.nextCursor;
  $('more-'+kind).hidden=!result.nextCursor;
}
async function refresh() {
  const [settings,watches,rules]=await Promise.all([api('/api/alerts/settings'),api('/api/alerts/watchlist'),api('/api/alerts/rules')]);
  const first=!state.settings;
  state.settings=settings; state.watches=watches.items; state.rules=rules.items;
  state.selected=new Set([...state.selected].filter(id=>state.watches.some(w=>w.instrumentId===id)));
  if(state.edit) {
    const latest=state.rules.find(r=>r.id===state.edit.id);
    $('resolve-edit').hidden=Boolean(latest&&latest.revision===state.edit.revision);
    if(!$('resolve-edit').hidden) $('status').textContent='편집 중인 조건이 변경되었거나 삭제되었습니다. 입력은 유지했습니다. 최신 설정을 확인해주세요.';
  }
  $('email').textContent='수신 주소: '+settings.email;
  $('consent').checked=settings.consent;
  $('email-frequency').value=settings.emailFrequency||'daily';
  $('frequency-note').textContent=settings.emailFrequency==='weekly'?'주간 요약 · 매주 월요일 한국시간 오전 8시대 · 최근 7일 새 신호가 있을 때 한 통':'일간 요약 · 한국시간 오전 8시대 · 새 신호가 있는 날 한 통';
  $('delivery-note').textContent=settings.suppressed?'발송 중지: '+settings.suppressed:settings.sendingEnabled?'운영 발송이 활성화되어 있습니다.':'실제 메일 발송은 아직 꺼져 있습니다. 조건을 저장할 수 있습니다.';
  if(!settings.calendarConfigured)$('delivery-note').textContent+=' 실적·배당 일정 공급자 연결은 설정되지 않았습니다.';
  if(!settings.filingsConfigured?.sec||!settings.filingsConfigured?.dart)$('delivery-note').textContent+=' 공시 연결: SEC '+(settings.filingsConfigured?.sec?'설정됨':'미설정')+', DART '+(settings.filingsConfigured?.dart?'설정됨':'미설정')+'.';
  if(first) {
    settings.types.forEach(info=>{const opt=element('option',info.label);opt.value=info.type;$('kind').append(opt);});
    fields(typeInfo().defaults);
  }
  $('content').hidden=false; render();
  await Promise.all([history('events'),history('digests'),operations()]);
}
async function operations() {
  if(!state.isOwner)return;
  $('operations-panel').hidden=false;
  try {
    const result=await api('/api/alerts/operations');
    $('operations').replaceChildren();$('operations-status').textContent='';
    if(!result.runs.length)$('operations-status').textContent='아직 점검 실행 기록이 없습니다.';
    result.runs.forEach(run=>{
      const li=element('li'),detail=element('div',run.digest_date,'detail');
      const taskNames={fetch:'자료 조회',evaluate:'규칙 평가'},taskStatuses={done:'완료',pending:'대기',deferred:'보류'};
      detail.append(element('small',run.tasks.map(t=>`${taskNames[t.kind]||t.kind} ${taskStatuses[t.status]||t.status}: ${t.count}건`).join(' · ')||'작업 없음'));
      detail.append(element('small',run.deliveries.map(d=>`${statuses[d.status]||d.status}: ${d.count}건`).join(' · ')||'발송 기록 없음'));
      detail.append(element('small',`누적 처리 ${Number(run.duration_seconds).toFixed(1)}초 · 시세 요청 ${run.data_calls||0}회 / HTTP 429 ${run.data_429||0}회 · 메일 요청 ${run.email_calls||0}회 / HTTP 429 ${run.email_429||0}회`));
      detail.append(element('small',`시세 제한(로컬 quota 포함) ${run.rate_limit_count}회 · 가장 오래된 미처리 시작: ${run.oldestPending?new Date(run.oldestPending).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' (한국시간)':'없음'}`));
      li.append(detail);$('operations').append(li);
    });
  } catch(error){$('operations-status').textContent='운영 상태 조회 실패: '+error.message;}
}
$('params').addEventListener('input',()=>{$('rule-meaning').textContent=describeRule($('kind').value,state.readParams());});
$('settings-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/alerts/settings','PATCH',{revision:state.settings.revision,consent:$('consent').checked,emailFrequency:$('email-frequency').value});await refresh();$('status').textContent='수신 설정을 저장했습니다.';}catch(error){fail(error);}});
$('watch-form').addEventListener('submit',async e=>{
  e.preventDefault();if(requests)return;
  const query=$('symbol').value.trim(),version=searchVersion;
  state.preview=null;$('watch-preview').hidden=true;
  try {
    const meta=await api('/api/alerts/instrument?symbol='+encodeURIComponent(query));
    if(version!==searchVersion||query!==$('symbol').value.trim())return;
    state.preview={...meta,query};
    $('watch-preview-detail').textContent=`${meta.name} (${meta.symbol}) · ${meta.mic} · ${meta.currency}`;
    $('watch-preview').hidden=false;$('confirm-watch').focus();
  } catch(error){fail(error);}
});
$('confirm-watch').addEventListener('click',async()=>{
  if(requests||!state.preview||state.preview.query!==$('symbol').value.trim())return;
  const preview=state.preview;
  try {
    const result=await api('/api/alerts/watchlist','POST',{symbol:preview.symbol});
    state.selected.add(result.instrumentId);
    if(state.preview===preview){state.preview=null;$('watch-preview').hidden=true;$('symbol').value='';}
    await refresh();$('status').textContent='관심종목을 추가했습니다.';
  } catch(error){fail(error);}
});
$('kind').addEventListener('change',()=>fields(typeInfo().defaults));
$('template').addEventListener('change',()=>{const templates={ma200:['price_ma_cross',{}],ma50:['ma_cross',{}],change:['daily_change',{}],rsi30:['rsi_cross',{level:30,direction:'down'}],rsi70:['rsi_cross',{level:70,direction:'up'}]};const t=templates[$('template').value];if(t){resetEdit();$('kind').value=t[0];fields({...typeInfo().defaults,...t[1]});}});
$('rule-form').addEventListener('submit',async e=>{
  e.preventDefault();
  try {
    const params=state.readParams();
    if(state.edit) await api('/api/alerts/rules/'+state.edit.id,'PATCH',{revision:state.edit.revision,params,name:$('rule-name').value});
    else {
      if(!state.selected.size) throw new Error('관심종목에서 적용할 종목을 선택해주세요.');
      await api('/api/alerts/rules','POST',{instrumentIds:[...state.selected],type:$('kind').value,params,name:$('rule-name').value,restoreArchived:$('restore-archived').checked});
    }
    resetEdit(); await refresh(); $('status').textContent='규칙을 저장했습니다.';
  } catch(error) {fail(error);}
});
$('cancel-edit').addEventListener('click',resetEdit);
$('resolve-edit').addEventListener('click',()=>{
  const latest=state.rules.find(r=>r.id===state.edit?.id);
  if(!latest) {fail(new Error('이 조건은 삭제되었습니다. 수정 취소 후 새 조건으로 등록해주세요.'));return;}
  if(confirm('최신 조건: '+describeRule(latest.kind,latest.params)+' / '+(latest.enabled?'활성':'정지')+'\n이를 확인한 뒤 현재 입력한 초안으로 저장을 계속할까요?')) {
    state.edit=latest; $('resolve-edit').hidden=true; $('status').textContent='입력을 유지했습니다. 저장 버튼으로 적용해주세요.';
  }
});
let searchTimer, searchVersion=0;
$('symbol').addEventListener('input',()=>{
  clearTimeout(searchTimer); const version=++searchVersion,query=$('symbol').value.trim();
  state.preview=null;$('watch-preview').hidden=true;
  $('symbol-results').replaceChildren(); if(!query)return;
  searchTimer=setTimeout(async()=>{try{const data=await api('/api/dashboard/symbols?q='+encodeURIComponent(query));if(version!==searchVersion)return;data.items.forEach(item=>{const option=element('option',item.name+' · '+item.market);option.value=item.ticker;$('symbol-results').append(option);});}catch(error){if(version===searchVersion)fail(error);}},300);
});
$('refresh').addEventListener('click',()=>refresh().catch(fail));
$('more-events').addEventListener('click',()=>history('events',true).catch(fail));$('more-digests').addEventListener('click',()=>history('digests',true).catch(fail));
$('email-test').addEventListener('click',async()=>{try{const r=await api('/api/alerts/email-test','POST',{});await history('digests');$('status').textContent='테스트 메일: '+(statuses[r.status]||r.status);}catch(error){fail(error);}});
$('import-holdings').addEventListener('click',async()=>{
  if(requests)return;
  try {
    const result=await api('/api/dashboard/state'),holdings=result.state?.portfolio?.positions||[];
    if(!holdings.length)throw new Error('계정에 저장된 보유 종목이 없습니다.');
    const remaining=holdings.filter(item=>!state.watches.some(w=>w.symbol===canonicalSymbol(item.ticker)));
    if(!remaining.length)throw new Error('모든 보유 종목이 이미 관심종목에 있습니다.');
    $('holdings-options').replaceChildren();
    remaining.forEach(item=>{
      const label=element('label'),check=element('input');check.type='checkbox';check.value=item.ticker;
      label.append(check,element('span',item.ticker));$('holdings-options').append(label);
    });
    $('holdings-form').hidden=false;$('holdings-options').querySelector('input').focus();
  } catch(error){fail(error);}
});
$('cancel-import').addEventListener('click',()=>{$('holdings-form').hidden=true;});
$('holdings-form').addEventListener('submit',async e=>{
  e.preventDefault();if(requests)return;
  const choices=[...document.querySelectorAll('#holdings-options input:checked')];
  if(!choices.length){fail(new Error('가져올 보유 종목을 선택해주세요.'));return;}
  let failure=null;
  try {
    for(const choice of choices){
      const added=state.watches.find(w=>w.symbol===canonicalSymbol(choice.value))
        ||await api('/api/alerts/watchlist','POST',{symbol:choice.value});
      state.selected.add(added.instrumentId);
      choice.closest('label').remove();
    }
  } catch(error){failure=error;}
  try {await refresh();}catch(error){failure=failure||error;}
  if(failure)fail(failure);
  else {$('holdings-form').hidden=true;$('status').textContent='선택한 보유 종목을 가져왔습니다.';}
});
$('delete-account').addEventListener('click',async()=>{if(!confirm('내 관심종목·규칙·신호·발송 이력을 삭제하고 수신을 중지할까요? 보유자산은 유지합니다.'))return;try{await api('/api/alerts/account','DELETE');state.selected.clear();resetEdit();await refresh();$('status').textContent='알림 데이터를 삭제했습니다.';}catch(error){fail(error);}});
(async()=>{try{const session=await api('/auth/session');if(!session.user){$('login-note').hidden=false;$('refresh').hidden=true;return;}state.account=session.user.sub;state.isOwner=session.isOwner;await refresh();}catch(error){fail(error);}})();
