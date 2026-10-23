const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');

const user = {sub: 'recovery-A', email: 'a@example.com'};
const key = 'portfolio-dashboard-v1:recovery-A';
const value = n => JSON.parse(saved(n));
const response = (data, status=200) => ({ok: status < 400, status, json: async () => structuredClone(data)});
function server(initial=value(100)) {
  const data = {state:initial, revision:initial ? 'r1' : null, posts:[], getError:false, postError:false, lostResponse:false};
  data.fetch = async (path, options) => {
    if (path === '/auth/session') return response({user});
    assert.equal(path, '/api/dashboard/state');
    assert.equal(options.headers['X-Portfolio-Account'], user.sub);
    if (!options.body) return data.getError ? response({detail:'offline'},503) : response({configured:true, emailReady:false, state:data.state, revision:data.revision});
    const body = JSON.parse(options.body); data.posts.push(body);
    if (data.postError) return response({detail:'offline'},503);
    if (body.revision !== data.revision) return response({detail:'다른 기기'},409);
    data.state = body.state; data.revision = 'r' + (Number((data.revision || 'r0').slice(1)) + 1);
    if (data.lostResponse) throw new Error('lost response');
    return response({revision:data.revision});
  };
  return data;
}
async function page(data, storage=new Map()) {
  const b = browser(user, storage, false, true);
  b.context.fetch = data.fetch;
  await b.run('initializeAccount()');
  return b;
}
const edit = (b, n) => b.run(`persistState({...state, portfolio:{...state.portfolio,cashUSD:${n}}})`);
const draftValues = storage => [...storage].filter(([k])=>k.startsWith(key+'-draft:')).map(([,v])=>JSON.parse(v).state.portfolio.cashUSD);

test('GET failure, edit, reload preserves draft and old backups; unknown base requires explicit choice', async () => {
  const data = server(), storage = new Map([[key,saved(100)],[key+'-before-cloud',saved(50)],[key+'-recovery','old damaged data']]);
  data.getError = true;
  const first = await page(data, storage);
  await assert.rejects(edit(first,200), /초안/);
  assert.equal(data.posts.length,0);
  data.getError = false;
  const next = await page(data,storage);
  assert.equal(next.run('state.portfolio.cashUSD'),200);
  assert.deepEqual(draftValues(storage),[200]);
  await next.run('retryCloudSave()');
  assert.equal(data.posts.length,0);
  assert.match(next.run('$("#draft-preview").textContent'),/200 USD/);
  assert.match(next.run('$("#server-preview").textContent'),/100 USD/);
  await next.run('$("#use-draft-btn").events.click()');
  assert.equal(data.state.portfolio.cashUSD,200);
  assert.deepEqual(draftValues(storage),[]);
  assert.equal(storage.get(key+'-before-cloud'),saved(50));
  assert.equal(storage.get(key+'-recovery'),'old damaged data');
});

test('POST failure reload never auto-writes; manual retry uses the original matching revision', async () => {
  const data = server(), b = await page(data);
  data.postError=true;
  await assert.rejects(edit(b,200));
  assert.deepEqual(draftValues(b.stored),[200]);
  data.postError=false;
  const next=await page(data,b.stored);
  assert.equal(data.posts.length,1);
  await next.run('retryCloudSave()');
  assert.equal(data.posts.length,2);
  assert.equal(data.posts[1].revision,'r1');
  assert.equal(data.state.portfolio.cashUSD,200);
});

test('response loss is verified by reading without duplicate writes, including server defaults', async () => {
  const data=server(), b=await page(data);
  data.lostResponse=true;
  await assert.rejects(edit(b,200));
  data.state.portfolio = {emailAlerts:false, rebalanceGroups:[], rebalanceTolerancePct:5, ...data.state.portfolio};
  await b.run('retryCloudSave()');
  assert.equal(data.posts.length,1);
  assert.deepEqual(draftValues(b.stored),[]);
  assert.match(b.run('$("#cloud-status").textContent'),/이미 계정에 저장/);
});

test('conflict requires comparison and another change after comparison is still blocked', async () => {
  const data=server(), b=await page(data);
  data.state=value(300); data.revision='r2';
  await assert.rejects(edit(b,200));
  await b.run('retryCloudSave()');
  assert.equal(data.posts.length,1);
  data.state=value(400); data.revision='r3';
  await b.run('$("#use-draft-btn").events.click()');
  assert.equal(data.posts[1].revision,'r2');
  assert.equal(data.state.portfolio.cashUSD,400);
  assert.deepEqual(draftValues(b.stored),[200]);
  assert.equal(b.run('comparedCloud'),null);
  await b.run('retryCloudSave();');
  b.run('$("#use-server-btn").events.click()');
  assert.equal(b.run('state.portfolio.cashUSD'),400);
  assert.deepEqual(draftValues(b.stored),[]);
  const backups=[...b.stored].filter(([k])=>k.startsWith(key+'-backup:')).map(([,v])=>JSON.parse(v));
  assert.equal(backups.at(-1).state.portfolio.cashUSD,200);
});

test('layout changes never upload a legacy portfolio before an explicit first save', async () => {
  const data=server(null), storage=new Map([[key,saved(100)]]), b=await page(data,storage);
  await b.run('setWidgetVisible("goal",false); movePanel("holdings","market"); resizePanel("macro",6,500); layoutChanged(); savePanelLayout()');
  assert.equal(data.posts.length,0);
  const next=await page(data,storage);
  await next.run('savePanelLayout()');
  assert.equal(data.posts.length,0);
  assert.equal(next.run('panelLayout.goal.hidden'),true);
  await edit(next,100);
  await next.run('setWidgetVisible("goal",true); savePanelLayout()');
  assert.equal(data.posts.length,2);
  assert.equal(data.posts[1].revision,'r1');
});

test('layout while a financial draft is pending preserves its financial content', async () => {
  const data=server(), b=await page(data);
  data.postError=true;
  await assert.rejects(edit(b,200));
  await b.run('setWidgetVisible("goal",false); savePanelLayout()');
  assert.equal(data.posts.length,1);
  assert.equal(b.run('pendingDraft.state.portfolio.cashUSD'),200);
  assert.equal(b.run('pendingDraft.state.layout.goal.hidden'),true);
  data.postError=false;
  await b.run('retryCloudSave()');
  assert.equal(data.state.portfolio.cashUSD,200);
  assert.equal(data.state.layout.goal.hidden,true);
});

test('goal editing after a failed portfolio save keeps both edits in the draft', async () => {
  const data=server(), b=await page(data); data.postError=true;
  await assert.rejects(edit(b,200));
  await assert.rejects(b.run('persistState({...state,goal:{currency:"USD",targetAmount:1000,monthlyContribution:10,annualReturnPct:3}})'));
  assert.equal(b.run('pendingDraft.state.portfolio.cashUSD'),200);
  assert.equal(b.run('pendingDraft.state.goal.targetAmount'),1000);
  assert.equal(data.posts.length,1);
});

test('storage denial prevents sending and keeps input state for the open form', async () => {
  const data=server(), b=await page(data);
  b.context.localStorage.setItem=()=>{throw new Error('quota');};
  await assert.rejects(edit(b,200),/현재 창의 입력만/);
  assert.equal(data.posts.length,0);
  assert.equal(data.state.portfolio.cashUSD,100);
});

test('choosing server data stops if the draft cannot be archived', async () => {
  const data=server(), b=await page(data); data.postError=true;
  await assert.rejects(edit(b,200));
  data.state=value(300); data.revision='r2';
  await b.run('loadCloudState()');
  b.context.localStorage.setItem=()=>{throw new Error('quota');};
  b.run('$("#use-server-btn").events.click()');
  assert.equal(b.run('state.portfolio.cashUSD'),200);
  assert.equal(b.run('pendingDraft.state.portfolio.cashUSD'),200);
  assert.deepEqual(draftValues(b.stored),[200]);
});

test('unconfigured storage remains distinct from failed reads and never claims an account save', async () => {
  const b=browser(user,new Map(),false,true);
  b.context.fetch=async path=>response(path==='/auth/session'?{user}:{configured:false,state:null,revision:null});
  await b.run('initializeAccount()');
  assert.equal(b.run('cloudStatus'),'unconfigured');
  await assert.rejects(edit(b,200),/아직 저장되지/);
  await b.run('retryCloudSave()');
  assert.equal(b.run('cloudStatus'),'unconfigured');
  assert.deepEqual(draftValues(b.stored),[200]);
});

test('another tab draft survives a late successful response and both offline drafts survive reload', async () => {
  const data=server(), storage=new Map(), a=await page(data,storage), b=await page(data,storage);
  let finish;
  a.context.fetch=async(path,options)=>new Promise(resolve=>{finish=()=>data.fetch(path,options).then(resolve);});
  const first=edit(a,200);
  data.postError=true;
  await assert.rejects(edit(b,300));
  data.postError=false;
  await finish(); await first;
  a.context.fetch=data.fetch;
  assert.deepEqual(draftValues(storage),[300]);
  data.postError=true;
  await assert.rejects(edit(a,400));
  assert.deepEqual(draftValues(storage).sort(),[300,400]);
  const restored=await page(data,storage);
  assert.equal(restored.run('storedDrafts().length'),2);
  assert.equal(data.posts.length,3);
});

test('another account cannot see or upload this account draft', async () => {
  const data=server(), a=await page(data); data.postError=true;
  await assert.rejects(edit(a,200));
  const b=browser({sub:'B'},a.stored,false,true);
  b.context.fetch=async path=>response(path==='/auth/session'?{user:{sub:'B'}}:{configured:true,state:null,revision:null});
  await b.run('initializeAccount()');
  assert.equal(b.run('pendingDraft'),null);
  assert.equal(b.run('state.portfolio.cashUSD'),0);
  assert.deepEqual(draftValues(a.stored),[200]);
});

test('a delayed retry blocks concurrent form saves and retains queued layout edits', async () => {
  const data=server(), b=await page(data); data.postError=true;
  await assert.rejects(edit(b,200));
  let finish;
  b.context.fetch=async(path,options)=>new Promise(resolve=>{finish=()=>data.fetch(path,options).then(resolve);});
  const retry=b.run('loadCloudState()');
  await assert.rejects(edit(b,300),/처리하고/);
  b.run('setWidgetVisible("goal",false)');
  await finish(); await retry;
  await b.run('savePanelLayout()');
  assert.equal(b.run('pendingDraft.state.portfolio.cashUSD'),200);
  assert.equal(b.run('pendingDraft.state.layout.goal.hidden'),true);
  assert.equal(data.posts.length,1);
});
