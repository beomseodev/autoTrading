const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

function browser(user, stored = new Map(), isOwner = false) {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {textContent: '', addEventListener() {}});
    return nodes.get(selector);
  };
  const requests = [];
  const context = vm.createContext({
    console, Intl, Date, Number, Map, Set, JSON,
    document: {querySelector: node, addEventListener() {}},
    localStorage: {getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value)},
    fetch: async (path, options) => {
      requests.push({path, options});
      return {ok: true, status: 200, json: async () => ({user, isOwner})};
    },
    location: {replace() {}, reload() {}},
    ResizeObserver: class {observe() {}},
  });
  const script = fs.readFileSync('app/static/dashboard.js', 'utf8').replace('\ninitializeAccount();', '\n');
  vm.runInContext(script, context);
  vm.runInContext('loadMarkets = () => {}; loadCloudState = async () => {}; loadPortfolio = () => {};', context);
  return {context, stored, requests, run: code => vm.runInContext(code, context)};
}

const saved = cashUSD => JSON.stringify({version: 1, portfolio: {positions: [], cashKRW: 0, cashUSD, baseCurrency: 'USD'}, goal: null, portfolioSavedAt: null});

test('guest uses memory only, never reads or writes account backups or cloud state', async () => {
  const store = new Map([['portfolio-dashboard-v1', saved(900)], ['portfolio-dashboard-v1:account-A', saved(100)]]);
  const before = [...store];
  const guest = browser(null, store);
  vm.runInContext(`globalThis.storageAccesses = 0;
    localStorage = {getItem() { storageAccesses++; throw new Error('Guest read storage'); }, setItem() { storageAccesses++; throw new Error('Guest wrote storage'); }};
    loadCloudState = async () => { throw new Error('Guest read cloud state'); };`, guest.context);
  await guest.run('initializeAccount()');
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
