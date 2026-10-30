const fs = require('node:fs');
const vm = require('node:vm');

function browser(user, stored = new Map(), isOwner = false, cloud = false) {
  const nodes = new Map();
  const element = () => ({
    textContent: '', children: [], dataset: {}, attributes: {}, events: {}, clientWidth: 1000,
    style: {values: {}, setProperty(key, value) { this.values[key] = value; }, removeProperty(key) { delete this.values[key]; }},
    classList: {toggle() {}, add() {}, remove() {}},
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; },
    addEventListener(name, callback) { this.events[name] = callback; },
    appendChild(child) { this.children = this.children.filter(item => item !== child); this.children.push(child); },
    getBoundingClientRect() { return {width: 640, height: 400}; },
    focus() {}, scrollIntoView() {}, setPointerCapture() {},
  });
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  const requests = [];
  const context = vm.createContext({
    console, Intl, Date, Number, Map, Set, JSON, structuredClone, crypto: require('node:crypto').webcrypto,
    setTimeout: () => 1, setInterval: () => 1, clearTimeout() {},
    document: {querySelector: node, createElement: element, addEventListener() {}},
    window: {addEventListener() {}},
    getComputedStyle: () => ({columnGap: '16px'}), matchMedia: () => ({matches: false}),
    localStorage: {get length() { return stored.size; }, key: index => [...stored.keys()][index], getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key)},
    fetch: async (path, options) => {
      requests.push({path, options});
      return {ok: true, status: 200, json: async () => ({user, isOwner})};
    },
    location: {assign() {}, replace() {}, reload() {}},
    ResizeObserver: class {observe() {}},
  });
  const script = fs.readFileSync('app/static/dashboard.js', 'utf8').replace('\ninitializeAccount();', '\n');
  vm.runInContext(script, context);
  vm.runInContext('loadMarkets = () => {}; loadPortfolio = () => {};', context);
  if (!cloud) vm.runInContext('loadCloudState = async () => {};', context);
  return {context, stored, requests, run: code => vm.runInContext(code, context)};
}

const saved = cashUSD => JSON.stringify({version: 1, portfolio: {positions: [], cashKRW: 0, cashUSD, baseCurrency: 'USD'}, goal: null, portfolioSavedAt: null});

module.exports = {browser, saved};
