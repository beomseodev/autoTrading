const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');

test('rebalance shows all quote dates and preserves the specific deferral reason', () => {
  const page = browser(null);
  page.run(`setMetric = () => {}; renderAllocation = () => {};
    const data = {holdings:[], complete:true, hasAssets:true, baseCurrency:'USD',
      totalValue:100, cashValue:100, dayChange:null, fetchedAt:'2026-10-26T03:00:00Z',
      rebalance:{message:'점검 완료', asOf:'2026-10-26', quoteDates:['2026-10-23','2026-10-26'], thresholdPct:5, items:[]}};
    renderPortfolio(data)`);
  assert.match(page.run('$("#rebalance-status").textContent'), /2026-10-23 \/ 2026-10-26/);
  page.run(`renderPortfolio({...data, rebalance:{message:'AAPL 시세 기준일 2026-10-22: 필요 기준일 2026-10-23. 5분 후 새로고침해주세요.', items:[]}})`);
  assert.match(page.run('$("#rebalance-status").textContent'), /AAPL.*2026-10-22.*2026-10-23/);
  assert.match(page.run('$("#rebalance-status").textContent'), /5분 후 새로고침/);
});

test('rebalance settings remain visible without quotes and distinguish incomplete targets', () => {
  const page = browser(null);
  page.run(`state.portfolio.positions=[{ticker:'005930.KS',targetWeight:60},{ticker:'AAPL',targetWeight:0}];
    state.portfolio.rebalanceTolerancePct=3;
    renderRebalanceSettings([{ticker:'005930.KS',name:'삼성전자'}])`);
  const text = page.run('$("#rebalance-settings").textContent');
  assert.match(text, /허용폭 ±3%p/);
  assert.match(text, /삼성전자 60%/);
  assert.match(text, /AAPL 0%/);
  assert.match(text, /현금 40%/);
  page.run('state.portfolio.positions[1].targetWeight=null; renderRebalanceSettings()');
  const partial = page.run('$("#rebalance-settings").textContent');
  assert.match(partial, /005930.KS 60%/);
  assert.match(partial, /미설정 종목 있음/);
  assert(!partial.includes('현금'));
});

test('group targets are validated and included once in widget settings', () => {
  const page = browser(null);
  page.run(`state.portfolio.positions=[{ticker:'QQQI',quantity:1,averageCost:0},{ticker:'SCHD',quantity:1,averageCost:0}];
    state.portfolio.rebalanceGroups=[{name:'배당 ETF <x>',tickers:['QQQI','SCHD'],targetWeight:60}];
    state.portfolio.emailAlerts=true;
    validatePortfolio(state.portfolio); renderRebalanceSettings()`);
  const text = page.run('$("#rebalance-settings").textContent');
  assert.match(text, /배당 ETF <x> \(QQQI \+ SCHD\) 60%/);
  assert.match(text, /현금 40%/);
  assert(!text.includes('미설정'));
  page.run('state.portfolio.positions[0].targetWeight=0');
  assert.throws(() => page.run('validatePortfolio(state.portfolio)'), /개별 목표/);
  page.run('state.portfolio.positions[0].targetWeight=null; state.portfolio.rebalanceGroups[0].tickers=["QQQI","QQQI"]');
  assert.throws(() => page.run('validatePortfolio(state.portfolio)'), /중복/);
  page.run('state.portfolio.rebalanceGroups[0].tickers=["QQQI","UNKNOWN"]');
  assert.throws(() => page.run('validatePortfolio(state.portfolio)'), /보유 종목/);
});
