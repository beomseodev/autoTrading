const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');

test('compact portfolio keeps quote dates and never presents incomplete totals as valid', () => {
  const page = browser(null);
  page.context.compactData = {
    hasAssets:true,complete:true,totalValue:100000,unrealizedProfit:5000,cashValue:0,
    baseCurrency:'KRW',fetchedAt:'2026-10-22T00:00:00Z',dayChange:null,
    holdings:[{ticker:'TEST',quantity:1,averageCost:95000,available:true,currency:'KRW',price:100000,value:100000,profitPct:5,asOf:'2026-10-21',weightPct:100}],
  };
  page.run('renderPortfolio(compactData)');
  const summary = page.run('$("#holdings-compact").innerHTML');
  assert.match(summary, /100,000/);
  assert.match(summary, /5,000/);
  assert.match(summary, /2026-10-21/);
  assert.match(page.run('$("#holdings-body").innerHTML'), /data-label="평가금액"/);
  page.run('renderPortfolio({...compactData,complete:false})');
  const incomplete = page.run('$("#holdings-compact").innerHTML');
  assert.match(incomplete, /합계 계산 보류/);
  assert.match(incomplete, /2026-10-21/);
  assert.doesNotMatch(incomplete, /100,000|5,000/);
});

test('section sizes support keyboard, bounds, pointer cancellation and mobile height-only changes', async () => {
  const page = browser(null);
  await page.run('initializeAccount()');
  const keydown = key => { page.context.keyEvent = {key, preventDefault() {}}; page.run('$(\'[data-panel="goal"]\').children[0].events.keydown(keyEvent)'); };
  keydown('ArrowRight');
  assert.equal(page.run('panelLayout.goal.span'), 9);
  keydown('ArrowDown');
  assert.equal(page.run('panelLayout.goal.height'), 420);
  page.run('resizePanel("goal", 100, 99999)');
  assert.equal(page.run('panelLayout.goal.span'), 12);
  assert.equal(page.run('panelLayout.goal.height'), 1600);
  page.run('resizePanel("goal", -1, -1)');
  assert.equal(page.run('panelLayout.goal.span'), 3);
  assert.equal(page.run('panelLayout.goal.height'), 120);
  page.run('$("#reset-layout").events.click()');
  assert.equal(page.run('Object.keys(panelLayout).length'), 0);
  page.run(`$('[data-panel="goal"]').children[0].events.pointerdown({button:0, clientX:0, clientY:0, pointerId:1, preventDefault(){}});
    $('[data-panel="goal"]').children[0].events.pointermove({clientX:200, clientY:100});`);
  assert.equal(page.run('panelLayout.goal.height'), 500);
  page.run(`$('[data-panel="goal"]').children[0].events.pointercancel()`);
  assert.equal(page.run('Object.keys(panelLayout).length'), 0);
  page.context.matchMedia = () => ({matches: true});
  keydown('ArrowRight');
  assert.equal(page.run('panelLayout.goal.span'), 8);
  keydown('ArrowDown');
  assert.equal(page.run('panelLayout.goal.height'), 420);
  assert.equal(page.run('$("#save-layout").hidden'), true);
});

test('layout validation rejects unknown panels and saved account layout is restored', async () => {
  const store = new Map([['portfolio-dashboard-v1:account-A', JSON.stringify({...JSON.parse(saved(100)), layout: {goal: {span: 12, height: 600}}})]]);
  const page = browser({sub:'account-A', email:'a@example.com'}, store);
  await page.run('initializeAccount()');
  assert.equal(page.run('panelLayout.goal.span'), 12);
  assert.equal(page.run('$(\'[data-panel="goal"]\').style.values["--panel-height"]'), '600px');
  assert.throws(() => page.run('validatePanelLayout({unknown: {span: 4}})'), /화면 크기/);
  assert.throws(() => page.run('validatePanelLayout({goal: {span: 99}})'), /화면 크기/);
  assert.throws(() => page.run('validatePanelLayout({goal: {span: 4, height: 0}})'), /화면 크기/);
  page.run('cloudLoading = false; resizePanel("goal", 6, 500)');
  await page.run('layoutChanged(); savePanelLayout()');
  assert.equal(JSON.parse(store.get('portfolio-dashboard-v1:account-A')).layout.goal.span, 6);
  assert.equal(JSON.parse(store.get('portfolio-dashboard-v1:account-A')).portfolio.cashUSD, 100);
});

test('widget order and visibility auto-save without changing financial inputs', async () => {
  const page = browser({sub:'account-A', email:'a@example.com'});
  await page.run('initializeAccount()');
  page.run(`state.portfolio.cashUSD=123; movePanel('holdings','market'); setWidgetVisible('goal',false)`);
  assert.equal(page.run('panelKeys()[0]'), 'holdings');
  assert.equal(page.run('$(\'[data-panel="goal"]\').hidden'), true);
  await page.run('savePanelLayout()');
  const restored = JSON.parse(page.stored.get('portfolio-dashboard-v1:account-A'));
  assert.equal(restored.layout.holdings.order, 0);
  assert.equal(restored.layout.goal.hidden, true);
  assert.equal(restored.portfolio.cashUSD, 123);
  page.run('setWidgetVisible("goal",true)');
  assert.equal(page.run('$(\'[data-panel="goal"]\').hidden'), false);
  assert.equal(page.run('panelLayout.goal.order'), restored.layout.goal.order);
  assert.throws(() => page.run('validatePanelLayout({goal:{span:8,order:-1}})'), /화면 크기/);
  assert.throws(() => page.run('validatePanelLayout({goal:{span:8,hidden:"false"}})'), /화면 크기/);
});

test('layout changes during a cloud save are queued and failed saves can be retried', async () => {
  const page = browser({sub:'account-A', email:'a@example.com'});
  await page.run('initializeAccount()');
  page.run('cloudLoading=false; cloudReady=true; cloudRevision="r0"; setWidgetVisible("goal",false)');
  let finish;
  page.context.fetch = async () => new Promise(resolve => { finish = resolve; });
  const firstSave = page.run('savePanelLayout()');
  page.run('movePanel("holdings","market"); setWidgetVisible("allocation",false)');
  assert.equal(page.run('layoutPending'),true);
  finish({ok:true,status:200,json:async()=>({revision:'r1'})});
  await firstSave;
  page.context.fetch = async (_path, options) => {
    const body = JSON.parse(options.body);
    assert.equal(body.revision,'r1');
    assert.equal(body.state.layout.holdings.order,0);
    assert.equal(body.state.layout.allocation.hidden,true);
    return {ok:true,status:200,json:async()=>({revision:'r2'})};
  };
  await page.run('savePanelLayout()');
  assert.equal(page.run('layoutPending'),false);
  page.run('setWidgetVisible("goal",true)');
  page.context.fetch = async () => ({ok:false,status:409,json:async()=>({detail:'다른 창에서 변경됨'})});
  await page.run('savePanelLayout()');
  assert.equal(page.run('layoutPending'),true);
  assert.equal(page.run('$("#save-layout").hidden'),false);
  assert.match(page.run('$("#layout-status").textContent'),/저장 실패/);
});

test('unfinished layout auto-save is recovered only against the same cloud revision', async () => {
  for (const revision of ['r0','newer-device-revision']) {
    const store = new Map([['portfolio-dashboard-v1:account-A-layout-draft', JSON.stringify({
      revision:'r0',layout:{goal:{span:6,height:500,hidden:true,order:0}},
    })]]);
    const page = browser({sub:'account-A',email:'a@example.com'},store);
    page.context.fixtureRevision = revision;
    page.run('loadCloudState=async()=>{cloudReady=true;cloudLoading=false;cloudRevision=fixtureRevision}');
    await page.run('initializeAccount()');
    assert.equal(page.run('$(\'[data-panel="goal"]\').hidden'), revision==='r0');
    assert.equal(page.run('layoutPending'), revision==='r0');
  }
});
