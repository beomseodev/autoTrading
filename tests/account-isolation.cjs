const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');
const vm = require('node:vm');

test('prices show each market date separately from the Korean screen query time', () => {
  const page = browser(null);
  page.context.viewData = {
    hasAssets: true, complete: false, baseCurrency: 'KRW', totalValue: null,
    dayChange: null, fetchedAt: '2026-10-22T23:30:00Z',
    fx: {value: 1400, asOf: '2026-10-22'},
    holdings: [
      {ticker: '005930.KS', available: true, currency: 'KRW', price: 70000, averageCost: 60000, quantity: 1, asOf: '2026-10-22', value: 70000, weightPct: null},
      {ticker: 'AAPL', available: true, currency: 'USD', price: 200, averageCost: 150, quantity: 1, asOf: '2026-10-21', value: 280000, weightPct: null},
      {ticker: 'FAIL', available: false, currency: 'USD', averageCost: 100, quantity: 1, asOf: 'not-a-quote-date', value: null, weightPct: null},
    ],
  };
  page.run('setMetric = () => {}; renderAllocation = () => {}; renderPortfolio(viewData)');
  const rows = page.run('$("#holdings-body").innerHTML').split('</tr>');
  assert.match(rows[0], /가격 기준일 2026-10-22/);
  assert.match(rows[1], /가격 기준일 2026-10-21/);
  assert.doesNotMatch(rows[2], /가격 기준일|not-a-quote-date/);
  const note = page.run('$("#valuation-note").textContent');
  assert.match(note, /거래소 현지 날짜/);
  assert.match(note, /환율.*가격 기준일 2026-10-22/);
  assert.match(note, /화면 조회 2026\. 10\. 23\./);
  assert.match(page.run('$("#updated-at").textContent'), /화면 조회.*08:30.*한국시간/);
});

test('guest uses memory only, never reads or writes account backups or cloud state', async () => {
  const store = new Map([['portfolio-dashboard-v1', saved(900)], ['portfolio-dashboard-v1:account-A', saved(100)]]);
  const before = [...store];
  const guest = browser(null, store);
  vm.runInContext(`globalThis.storageAccesses = 0;
    localStorage = {getItem() { storageAccesses++; throw new Error('Guest read storage'); }, setItem() { storageAccesses++; throw new Error('Guest wrote storage'); }};
    loadCloudState = async () => { throw new Error('Guest read cloud state'); };`, guest.context);
  await guest.run('initializeAccount()');
  guest.run('setWidgetVisible("goal",false); movePanel("holdings","market")');
  assert.equal(guest.run('cloudLoading'), false);
  assert.equal(guest.run('state.portfolio.cashUSD'), 0);
  assert.equal(guest.run('STORAGE_KEY'), null);
  await guest.run('persistState({...state, portfolio: {...state.portfolio, cashUSD: 200}, goal: {currency: "USD", targetAmount: 1000, monthlyContribution: 20, annualReturnPct: 5}})');
  assert.equal(guest.run('state.portfolio.cashUSD'), 200);
  assert.equal(guest.run('state.goal.targetAmount'), 1000);
  assert.equal(guest.run('storageAccesses'), 0);
  assert.deepEqual([...store], before);
  assert.deepEqual(guest.requests.map(item => item.path), ['/auth/session']);
  await guest.run('api("/api/dashboard/portfolio", state.portfolio)');
  assert.equal(guest.requests.at(-1).options.headers['X-Portfolio-Account'], undefined);
  const fresh = browser(null, store);
  await fresh.run('initializeAccount()');
  assert.equal(fresh.run('state.portfolio.cashUSD'), 0);
  assert.equal(fresh.run('state.goal'), null);
});

test('account identity resolves before storage is read; another account sees no owner backup', async () => {
  const store = new Map([['portfolio-dashboard-v1', saved(900)], ['portfolio-dashboard-v1:account-A', saved(100)]]);
  const b = browser({sub: 'account-B', email: 'b@example.com'}, store);
  assert.equal(b.run('STORAGE_KEY'), null);
  await b.run('initializeAccount()');
  assert.equal(b.run('STORAGE_KEY'), 'portfolio-dashboard-v1:account-B');
  assert.equal(b.run('state.portfolio.cashUSD'), 0);
  b.run('saveState({...state, portfolio: {...state.portfolio, cashUSD: 200}})');
  assert.equal(JSON.parse(store.get('portfolio-dashboard-v1:account-A')).portfolio.cashUSD, 100);
  assert.equal(JSON.parse(store.get('portfolio-dashboard-v1:account-B')).portfolio.cashUSD, 200);
  assert.equal(JSON.parse(store.get('portfolio-dashboard-v1')).portfolio.cashUSD, 900);
  await b.run('api("/api/dashboard/state", {})');
  assert.equal(b.requests.at(-1).options.headers['X-Portfolio-Account'], 'account-B');
});

test('only the operator imports legacy state; recovery backups stay scoped', async () => {
  const store = new Map([['portfolio-dashboard-v1', saved(900)]]);
  const owner = browser({sub: 'owner', email: 'owner@example.com'}, store, true);
  await owner.run('initializeAccount()');
  assert.equal(owner.run('state.portfolio.cashUSD'), 900);
  store.set('portfolio-dashboard-v1:new-user', 'invalid-json');
  const other = browser({sub: 'new-user', email: 'other@example.com'}, store);
  await other.run('initializeAccount()');
  assert.equal(other.run('state.portfolio.cashUSD'), 0);
  other.run('saveState(state)');
  assert.equal(store.get('portfolio-dashboard-v1:new-user-recovery'), 'invalid-json');
  assert.equal(store.get('portfolio-dashboard-v1-recovery'), undefined);
});
