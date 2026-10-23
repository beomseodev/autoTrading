const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');

test('optional performance widget ranks valid returns and flags excluded positions', () => {
  const page = browser(null);
  page.context.rankData = {
    hasAssets:true,complete:false,totalValue:null,cashValue:0,baseCurrency:'KRW',dayChange:null,fetchedAt:'2026-10-22T00:00:00Z',
    holdings:[
      {ticker:'LOW',name:'하락 <x>',available:true,profitPct:-10,asOf:'2026-10-21'},
      {ticker:'HIGH',available:true,profitPct:20,asOf:'2026-10-22'},
      {ticker:'UNKNOWN',available:false,profitPct:null},
      {ticker:'NO-COST',available:true,profitPct:null},
    ],
  };
  const requestsBefore = page.requests.length;
  page.run('renderPortfolio(rankData)');
  const list = page.run('$("#performance-list").innerHTML');
  assert(list.indexOf('HIGH') < list.indexOf('LOW'));
  assert.match(list,/하락 &lt;x&gt;/);
  assert.match(list,/가격 기준일 2026-10-21/);
  assert(!list.includes('UNKNOWN'));
  assert.match(page.run('$("#performance-status").textContent'),/2개 제외/);
  page.run('setupDashboardLayout()');
  assert.equal(page.run('$(\'[data-panel="performance"]\').hidden'),true);
  assert.equal(page.requests.length, requestsBefore);
  page.run('renderPortfolio({...rankData, holdings: []})');
  assert.equal(page.run('$("#performance-list").innerHTML'), '');
  assert.match(page.run('$("#performance-status").textContent'), /평균 매입가를 확인/);
});
