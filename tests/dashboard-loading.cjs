const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');

test('loading stays visible until overlapping responses finish, including parsing and errors', async () => {
  const page = browser(null);
  const pending = [];
  page.context.fetch = () => new Promise(resolve => pending.push(resolve));
  const first = page.run('api("/first")');
  const second = page.run('api("/second")');
  assert.equal(page.run('$("#data-loading").hidden'), false);
  pending[0]({ok: true, json: async () => ({})});
  await first;
  assert.equal(page.run('$("#data-loading").hidden'), false);
  pending[1]({ok: false, json: async () => ({detail: '조회 실패'})});
  await assert.rejects(second, /조회 실패/);
  assert.equal(page.run('$("#data-loading").hidden'), true);
  page.context.fetch = async () => ({ok: true, json: async () => { throw new Error('invalid JSON'); }});
  await assert.rejects(page.run('api("/invalid")'), /invalid JSON/);
  assert.equal(page.run('pendingRequests'), 0);
  assert.equal(page.run('$("#data-loading").hidden'), true);
  page.context.fetch = async () => { throw new Error('offline'); };
  await page.run('initializeAccount()');
  assert.equal(page.run('$("#data-loading").hidden'), true);
  assert.match(page.run('$("#app-status").textContent'), /로그인 상태/);
});

test('refresh label waits for both data loads and restores when they finish', () => {
  const page = browser(null);
  page.run('marketBusy = true; portfolioBusy = true; updateRefreshButton()');
  assert.equal(page.run('$("#refresh-btn").disabled'), true);
  assert.match(page.run('$("#refresh-btn").textContent'), /불러오는 중/);
  page.run('marketBusy = false; updateRefreshButton()');
  assert.equal(page.run('$("#refresh-btn").disabled'), true);
  page.run('portfolioBusy = false; updateRefreshButton()');
  assert.equal(page.run('$("#refresh-btn").disabled'), false);
  assert.match(page.run('$("#refresh-btn").textContent'), /새로고침/);
});

test('an old search cannot clear the current search spinner', async () => {
  const page = browser(null);
  const pending = [];
  const classes = new Set();
  page.run('$("#symbol-results").replaceChildren = () => {}');
  page.context.searchClasses = classes;
  page.run('$("#symbol-search-status").classList = {add: name => searchClasses.add(name), remove: name => searchClasses.delete(name)}');
  page.context.fetch = () => new Promise(resolve => pending.push(resolve));
  page.run('$("#symbol-query").value = "apple"');
  const first = page.run('searchSymbols()');
  page.run('$("#symbol-query").value = "samsung"');
  const second = page.run('searchSymbols()');
  pending[0]({ok: true, json: async () => ({items: []})});
  await first;
  assert(classes.has('loading-indicator'));
  pending[1]({ok: false, json: async () => ({detail: '검색 실패'})});
  await second;
  assert(!classes.has('loading-indicator'));
  assert.match(page.run('$("#symbol-search-status").textContent'), /검색 실패/);
  assert.equal(page.run('$("#data-loading").hidden'), true);
});

test('loading includes response parsing and allows a new request after a network failure', async () => {
  const page = browser(null);
  let parse;
  page.context.fetch = async () => ({ok: true, json: () => new Promise(resolve => { parse = resolve; })});
  const request = page.run('api("/delayed-json")');
  await new Promise(setImmediate);
  assert.equal(page.run('$("#data-loading").hidden'), false);
  parse({});
  await request;
  assert.equal(page.run('$("#data-loading").hidden'), true);
  page.context.fetch = async () => { throw new Error('offline'); };
  await assert.rejects(page.run('api("/offline")'), /offline/);
  page.context.fetch = async () => ({ok: true, json: async () => ({recovered: true})});
  assert.equal((await page.run('api("/retry")')).recovered, true);
  assert.equal(page.run('pendingRequests'), 0);
});
