const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync('app/static/app.js', 'utf8');
function extract(name, next) {
  return source.slice(source.indexOf(name), source.indexOf(next, source.indexOf(name)));
}
function node(textContent = '', disabled = false) {
  const classes = new Set();
  return {textContent, disabled, classes, classList: {add: key => classes.add(key), remove: key => classes.delete(key)}};
}

for (const mode of ['single', 'compare', 'risk']) {
  for (const failure of [false, true]) {
    test(`${mode} analysis restores controls and loading indicators after ${failure ? 'failure' : 'success'}`, async () => {
      const button = node('분석하기');
      const status = node();
      const disabledControl = node('', true);
      const attributes = new Map();
      const container = {
        querySelectorAll: () => [button, disabledControl],
        querySelector: selector => selector.startsWith('button') ? button : status,
        setAttribute: (key, value) => attributes.set(key, value),
        removeAttribute: key => attributes.delete(key),
        getAttribute: key => attributes.get(key),
      };
      let respond;
      const context = vm.createContext({
        form: container, riskProfileForm: container, statusText: status, riskStatusText: status,
        runModeButtons: [], addPositionButton: node(), addScenarioButton: node(),
        isCompareMode: () => false, buildPayload: () => ({}), buildRiskProfilePayload: () => ({}),
        buildComparePayload: () => ({runs: [{label: 'test'}]}),
        fetch: () => new Promise(resolve => { respond = resolve; }),
        formatApiDetail: value => value, JSON, Number,
        resultsHint: {}, compareSummaryWrap: node(), scenarioDetailLabel: node(), summaryGrid: node(),
        scenarioDetailSelect: {}, chartSeries: {},
        renderSummary() {}, renderResultContext() {}, renderHoldings() {}, renderEvents() {},
        renderActiveChart() {}, renderCompareSummaryTable() {}, populateScenarioDetailSelect() {},
        renderDetailForCompareIndex() {}, renderRiskProfile() {},
      });
      vm.runInContext(extract('function lockControls(', 'function renderHoldings('), context);
      vm.runInContext(extract('async function runRiskProfile(', 'function buildPeriodFromFormData('), context);
      vm.runInContext(extract('let backtestBusy = false;', 'function wirePositionRow('), context);
      const task = vm.runInContext(mode === 'risk' ? 'runRiskProfile({preventDefault(){}})' : mode === 'compare' ? 'runCompareBacktest()' : 'runBacktest({preventDefault(){}})', context);
      assert.equal(button.disabled, true);
      assert.equal(attributes.get('aria-busy'), 'true');
      assert(button.classes.has('loading-indicator'));
      assert(status.classes.has('loading-indicator'));
      assert.match(button.textContent, /분석 중/);
      const result = {summary: {rebalanceCount: 0, inflationRatePct: 3}};
      respond({ok: !failure, json: async () => failure ? {detail: '조회 실패'} : {...result, runs: [{result}]}});
      await task;
      assert.equal(button.disabled, false);
      assert.equal(disabledControl.disabled, true);
      assert.equal(button.textContent, '분석하기');
      assert(!button.classes.has('loading-indicator'));
      assert(!status.classes.has('loading-indicator'));
      assert(!attributes.has('aria-busy'));
      assert.match(status.textContent, failure ? /조회 실패/ : /완료/);
    });
  }
}
