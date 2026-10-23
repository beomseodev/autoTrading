"use strict";
const $ = id => document.getElementById(id);
const state = {account: null, settings: null, watches: [], rules: [], selected: new Set(), edit: null, events: null, digests: null};
let requests = 0;
const names = {average:"평균 방식",period:"기간 (거래일)",direction:"방향",shortPeriod:"단기 (거래일)",longPeriod:"장기 (거래일)",price:"지정가격 (현지 통화)",thresholdPct:"기준 (%)",level:"RSI 기준",multiple:"거래량 배수",fastPeriod:"MACD fast",slowPeriod:"MACD slow",signalPeriod:"시그널 기간",crossMode:"MACD 비교선",stddev:"표준편차 배수",event:"발동 조건"};
const options = {average:[['sma','단순이동평균 SMA'],['ema','지수이동평균 EMA']],direction:[['both','양방향'],['up','상향'],['down','하향']],crossMode:[['signal','시그널선'],['zero','0선']],event:[['upper_exit','상단 위 돌파'],['lower_exit','하단 아래 이탈'],['upper_reentry','상단 아래 복귀'],['lower_reentry','하단 위 복귀']]};
const statuses = {signal:'새 신호',no_signal:'정상 무신호',no_new_session:'휴장 또는 활성화 후 새 거래일 없음',insufficient_data:'계산 자료 부족',invalid_data:'자료 검증 실패',needs_review:'분할 후 지정가격 확인 필요',pending:'발송 대기',sending:'전송 중',accepted:'공급자 접수',delivered:'배달 확인',no_signals:'새 신호 없음',unknown:'결과 확인 필요',retryable_failed:'발송 재시도 대기',permanent_failed:'발송 실패',canceled:'설정 변경으로 취소',bounced:'반송',complained:'스팸신고',suppressed:'발송 중지'};
statuses.deferred='조회 실패·자료 보류, 신호 여부 미확인';
statuses.missed_window='발송 시간 내 미접수';
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
function typeInfo(){return state.settings.types.find(x=>x.type===$('kind').value);}
function fields(params){$('params').replaceChildren();Object.entries(params).forEach(([key,value])=>{const label=element('label',names[key]||key);const control=element(options[key]?'select':'input');control.dataset.param=key;control.id='param-'+key;if(options[key])options[key].forEach(([val,text])=>{const opt=element('option',text);opt.value=val;control.append(opt);});else{control.type='number';control.step=key.toLowerCase().includes('period')||key==='period'?'1':'any';control.required=true;}control.value=value;label.append(control);$('params').append(label);});$('rule-meaning').textContent=typeInfo().label+' · 선택한 종목의 현지 정규장 종가 기준 · 기간은 거래일 수입니다.';}
function resetEdit(){state.edit=null;$('kind').disabled=false;$('rule-name').value='';$('cancel-edit').hidden=true;$('resolve-edit').hidden=true;$('restore-archived').checked=false;$('save-rule').textContent='선택 종목에 규칙 등록';$('rule-heading').textContent='조건 등록';fields(typeInfo().defaults);}
function render(){
  $('watchlist').replaceChildren();
  if(!state.watches.length)$('watchlist').append(element('li','등록한 관심종목이 없습니다.'));
  state.watches.forEach(w=>{const li=element('li');const label=element('label');const check=element('input');check.type='checkbox';check.checked=state.selected.has(w.instrumentId);check.setAttribute('aria-label',w.symbol+' 일괄 등록 선택');check.addEventListener('change',()=>check.checked?state.selected.add(w.instrumentId):state.selected.delete(w.instrumentId));label.append(check,element('span',`${w.metadata.name} (${w.symbol}) · ${w.metadata.mic} · ${w.metadata.currency}`));li.append(label,button('제거',async()=>{await api('/api/alerts/watchlist/'+w.id,'DELETE');state.selected.delete(w.instrumentId);await refresh();}));$('watchlist').append(li);});
  $('rules').replaceChildren();
  if(!state.rules.length)$('rules').append(element('li','등록한 규칙이 없습니다.'));
  state.rules.forEach(r=>{const li=element('li'),detail=element('div',`${r.symbol} · ${r.name||state.settings.types.find(x=>x.type===r.kind).label}`,'detail');detail.append(element('small',JSON.stringify(r.params)),element('small',r.enabled?'활성':'정지'),element('small',r.last_result?(statuses[r.last_result.status]||r.last_result.status)+' · '+(r.last_result.reason||''):'자료 준비 중 · 다음 점검부터 평가합니다.'));li.append(detail,button(r.enabled?'정지':'재개',async()=>{await api('/api/alerts/rules/'+r.id,'PATCH',{revision:r.revision,enabled:!r.enabled});await refresh();}),button('수정',async()=>{state.edit=r;$('kind').value=r.kind;$('kind').disabled=true;$('rule-name').value=r.name;fields(r.params);$('cancel-edit').hidden=false;$('save-rule').textContent='조건 수정 저장';$('rule-heading').textContent=r.symbol+' 조건 수정';$('rule-heading').scrollIntoView({block:'center'});$('rule-name').focus();}),button('삭제',async()=>{await api('/api/alerts/rules/'+r.id,'DELETE');await refresh();}));$('rules').append(li);});
}
async function history(kind,append=false) {
  const result=await api('/api/alerts/'+kind+(append&&state[kind]?'?cursor='+encodeURIComponent(state[kind]):''));
  if(!append) $(kind).replaceChildren();
  if(!append&&!result.items.length) $(kind).append(element('li',kind==='events'?'아직 신호 이력이 없습니다.':'아직 발송 이력이 없습니다.'));
  result.items.forEach(row=>{
    const li=element('li');
    if(kind==='events') {
      li.append(element('span',`${row.payload.symbol} · ${row.session_date} · ${row.payload.label}`));
      li.append(element('small',row.canceled?'취소된 신호':JSON.stringify(row.payload.result.values)));
      if(row.correction) li.append(element('small','자료 정정: '+row.correction.reason));
    } else {
      li.append(element('span',`${row.digest_date} · ${row.kind==='test'?'테스트 · ':''}${statuses[row.status]||row.status}`));
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
  if(state.edit) {
    const latest=state.rules.find(r=>r.id===state.edit.id);
    $('resolve-edit').hidden=Boolean(latest&&latest.revision===state.edit.revision);
    if(!$('resolve-edit').hidden) $('status').textContent='편집 중인 조건이 변경되었거나 삭제되었습니다. 입력은 유지했습니다. 최신 설정을 확인해주세요.';
  }
  $('email').textContent='수신 주소: '+settings.email;
  $('consent').checked=settings.consent;
  $('delivery-note').textContent=settings.suppressed?'발송 중지: '+settings.suppressed:settings.sendingEnabled?'운영 발송이 활성화되어 있습니다.':'실제 메일 발송은 아직 꺼져 있습니다. 조건을 저장할 수 있습니다.';
  if(first) {
    settings.types.forEach(info=>{const opt=element('option',info.label);opt.value=info.type;$('kind').append(opt);});
    fields(typeInfo().defaults);
  }
  $('content').hidden=false; render();
  await Promise.all([history('events'),history('digests')]);
}
$('settings-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/alerts/settings','PATCH',{revision:state.settings.revision,consent:$('consent').checked});await refresh();$('status').textContent='수신 설정을 저장했습니다.';}catch(error){fail(error);}});
$('watch-form').addEventListener('submit',async e=>{e.preventDefault();try{const result=await api('/api/alerts/watchlist','POST',{symbol:$('symbol').value.trim()});state.selected.add(result.instrumentId);$('symbol').value='';await refresh();$('status').textContent='관심종목을 추가했습니다.';}catch(error){fail(error);}});
$('kind').addEventListener('change',()=>fields(typeInfo().defaults));
$('template').addEventListener('change',()=>{const templates={ma200:['price_ma_cross',{}],ma50:['ma_cross',{}],change:['daily_change',{}],rsi30:['rsi_cross',{level:30,direction:'down'}],rsi70:['rsi_cross',{level:70,direction:'up'}]};const t=templates[$('template').value];if(t){resetEdit();$('kind').value=t[0];fields({...typeInfo().defaults,...t[1]});}});
$('rule-form').addEventListener('submit',async e=>{
  e.preventDefault();
  try {
    const params={};
    document.querySelectorAll('[data-param]').forEach(input=>params[input.dataset.param]=input.tagName==='SELECT'?input.value:Number(input.value));
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
  if(confirm('최신 조건: '+JSON.stringify(latest.params)+' / '+(latest.enabled?'활성':'정지')+'\n이를 확인한 뒤 현재 입력한 초안으로 저장을 계속할까요?')) {
    state.edit=latest; $('resolve-edit').hidden=true; $('status').textContent='입력을 유지했습니다. 저장 버튼으로 적용해주세요.';
  }
});
let searchTimer, searchVersion=0;
$('symbol').addEventListener('input',()=>{
  clearTimeout(searchTimer); const version=++searchVersion,query=$('symbol').value.trim();
  $('symbol-results').replaceChildren(); if(!query)return;
  searchTimer=setTimeout(async()=>{try{const data=await api('/api/dashboard/symbols?q='+encodeURIComponent(query));if(version!==searchVersion)return;data.items.forEach(item=>{const option=element('option',item.name+' · '+item.market);option.value=item.ticker;$('symbol-results').append(option);});}catch(error){if(version===searchVersion)fail(error);}},300);
});
$('refresh').addEventListener('click',()=>refresh().catch(fail));
$('more-events').addEventListener('click',()=>history('events',true).catch(fail));$('more-digests').addEventListener('click',()=>history('digests',true).catch(fail));
$('email-test').addEventListener('click',async()=>{try{const r=await api('/api/alerts/email-test','POST',{});await history('digests');$('status').textContent='테스트 메일: '+(statuses[r.status]||r.status);}catch(error){fail(error);}});
$('import-holdings').addEventListener('click',async()=>{try{const result=await api('/api/dashboard/state');const holdings=result.state?.portfolio?.positions||[];if(!holdings.length)throw new Error('계정에 저장된 보유 종목이 없습니다.');for(const item of holdings){if(state.watches.some(w=>w.symbol===item.ticker))continue;const added=await api('/api/alerts/watchlist','POST',{symbol:item.ticker});state.selected.add(added.instrumentId);}await refresh();$('status').textContent='보유 종목을 가져왔습니다.';}catch(error){fail(error);await refresh().catch(fail);}});
$('delete-account').addEventListener('click',async()=>{if(!confirm('내 관심종목·규칙·신호·발송 이력을 삭제하고 수신을 중지할까요? 보유자산은 유지합니다.'))return;try{await api('/api/alerts/account','DELETE');state.selected.clear();resetEdit();await refresh();$('status').textContent='알림 데이터를 삭제했습니다.';}catch(error){fail(error);}});
(async()=>{try{const session=await api('/auth/session');if(!session.user){$('login-note').hidden=false;$('refresh').hidden=true;return;}state.account=session.user.sub;await refresh();}catch(error){fail(error);}})();
